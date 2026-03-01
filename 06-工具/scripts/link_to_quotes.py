#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Process benchmark links and store extracted copy in benchmark link library.

Design choice:
- Links are ingested into `03-素材库/对标链接库` only.
- Links are NOT auto-ingested into quote library.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from crawl_bridge import CRAWL_ROOT, URL_READER_VENV_PY, resolve_python_cmd, run_url_reader
from quote_ingest_core import DEFAULT_NEAR_DUP_THRESHOLD, CandidateQuote, NearDupItem, resolve_path

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
NOISE_LINE_SUBSTRINGS = (
    "小红书",
    "登录",
    "通知",
    "发布",
    "沪icp",
    "icp",
    "备案",
    "举报",
    "我知道了",
    "创作服务",
    "直播管理",
    "电脑直播助手",
    "复制笔记链接",
    "复制图片",
    "下载图片",
    "去首页",
    "加载中",
    "营业执照",
    "违法不良信息",
    "来源",
    "读取策略",
    "更多",
    "地址：",
    "电话：",
    "复制后打开",
    "查看笔记",
    "关注",
    "我要申诉",
    "温馨提示",
    "推广合作",
    "商家入驻",
    "mcn入驻",
    "pro",
    "xhs",
    "行吟信息科技",
    "专业号",
    "蒲公英",
)
NOISE_LINE_REGEXES = (
    re.compile(r"^\d{2}-\d{2}\s*[\u4e00-\u9fffA-Za-z]*$"),
    re.compile(r"^\d{6,}$"),
    re.compile(r"^[\u2000-\u206F\u2E00-\u2E7F\s|]+$"),
)
EXACT_NOISE_LINES = {
    "专业号",
    "蒲公英",
    "创作服务",
    "直播管理",
}

DOUYIN_UI_NOISE_SUBSTRINGS = (
    "Topick",
    "For You",
    "Following",
    "Friends",
    "Profile",
    "下载抖音精选",
    "Full screen",
    "Picture in picture",
    "Watch later",
    "Quality",
    "Autoplay",
    "Listen Video",
    "Open autoplay",
    "ClearJ",
    "设备无网络请试试刷新",
    "不支持的音频/视频格式",
    "点击按住可拖动视频",
    "Please login before leaving comments",
    "Log in to Douyin",
    "Log in / Sign up",
    "Use Phone",
    "Use Password",
    "Send code",
    "Web-Cross-Storage",
)
DOUYIN_STOP_MARKERS = (
    "## 推荐视频",
    "## 全部评论",
    "Log in to Douyin",
    "Please login before leaving comments",
    "Web-Cross-Storage",
)

DEFAULT_MIN_CONTENT_CHARS = max(1, int(os.getenv("INGEST_LINK_MIN_CONTENT_CHARS", "120")))
TEST_DOMAIN_HINTS = {
    "example.com",
    "raw.githubusercontent.com",
    "localhost",
    "127.0.0.1",
}
URL_INLINE_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
SHORT_URL_INLINE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:v\.douyin\.com|xhslink\.com|b23\.tv)/[A-Za-z0-9_-]+/?(?:\?[^\s<>\"'`]+)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LinkProcessItem:
    url: str
    route_status: str
    content_status: str
    status: str
    provider: str
    is_test_url: bool
    title: str
    published_date: str
    saved_md: str
    body_file: str
    body_chars: int
    added_count: int
    near_dup_count: int
    quality_reason: str
    error: str


@dataclass(frozen=True)
class LinkToQuotesResult:
    run_id: str
    apply_mode: bool
    quote_added: list[CandidateQuote]
    near_dups: list[NearDupItem]
    exact_dup_count: int
    touched_files: list[Path]
    topic_pool_updated: bool
    topic_total: int
    items: list[LinkProcessItem]
    link_log_path: Path | None


def _repair_mojibake(text: str) -> str:
    """Fix common UTF-8/GBK mojibake like '鍙戠幇' -> '发现'."""
    value = str(text or "")
    if not value:
        return value
    try:
        repaired = value.encode("gbk").decode("utf-8")
    except Exception:
        return value
    return repaired or value


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return dt.date.today().isoformat()


def _normalize_date(value: str) -> str:
    match = DATE_RE.search(str(value or ""))
    return match.group(0) if match else ""


def _pick_published_date(meta: dict[str, str], source_time: str) -> str:
    for key in ("published_at", "publish_time", "date", "saved_at", "saved_date", "saved"):
        value = _normalize_date(meta.get(key, ""))
        if value:
            return value
    fallback = _normalize_date(source_time)
    return fallback or _today()


def _safe_slug(value: str, max_len: int = 48) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", str(value or "").strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:max_len] or "untitled"


def _fallback_fetch_plain_text(url: str, timeout_sec: int = 15) -> tuple[str, str]:
    """Fallback plain-text fetch for URLs that fail browser/script extraction."""
    try:
        resp = requests.get(
            str(url or ""),
            timeout=max(5, timeout_sec),
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; OpenClaw-LinkFetcher/1.0)",
                "Accept": "text/markdown,text/plain,text/html,*/*",
            },
        )
    except Exception as exc:
        return "", f"HTTP fallback failed: {exc}"

    if resp.status_code >= 400:
        return "", f"HTTP fallback status={resp.status_code}"

    text = str(resp.text or "").strip()
    if not text:
        return "", "HTTP fallback empty body"

    content_type = str(resp.headers.get("content-type") or "").lower()
    host = str(urlparse(url).netloc or "").lower()
    if not any(token in content_type for token in ("text/", "markdown", "json")):
        # Keep strict for most hosts, but allow well-known raw text hosts.
        if "raw.githubusercontent.com" not in host:
            return "", f"HTTP fallback unsupported content-type: {content_type or 'unknown'}"

    return text, ""


def _normalize_title(value: str) -> str:
    text = _repair_mojibake(value).strip()
    if not text:
        return ""
    md_link = re.match(r"^\[([^\]]+)\]\(", text)
    if md_link:
        text = md_link.group(1).strip()
    return re.sub(r"\s+", " ", text)


def _host_from_url(url: str) -> str:
    try:
        return str(urlparse(str(url or "")).netloc or "").lower()
    except Exception:
        return ""


def _is_douyin_url(url: str) -> bool:
    host = _host_from_url(url)
    return ("douyin.com" in host) or ("iesdouyin.com" in host)


def _is_test_link(url: str, *, source_ref: str) -> bool:
    ref = str(source_ref or "").strip().lower()
    if ref.startswith("cloud-smoke") or ref.startswith("smoke-") or "smoke" in ref:
        return True
    host = _host_from_url(url)
    if not host:
        return False
    if host in TEST_DOMAIN_HINTS:
        return True
    return any(host.endswith(f".{item}") for item in TEST_DOMAIN_HINTS)


def _extract_json_from_blob(blob: str) -> dict[str, Any] | None:
    raw = str(blob or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    first = raw.find("{")
    last = raw.rfind("}")
    if first >= 0 and last > first:
        snippet = raw[first : last + 1]
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def _provider_f2_extract(url: str, *, timeout_sec: int = 30) -> tuple[str, str, str, str]:
    exe = shutil.which("f2")
    has_module = importlib.util.find_spec("f2") is not None
    if not exe and not has_module:
        return "", "", "", "f2_not_available"

    command_candidates: list[list[str]] = []
    if exe:
        command_candidates.extend(
            [
                [exe, "--url", url, "--json"],
                [exe, "douyin", "--url", url, "--json"],
                [exe, "dy", "--url", url, "--json"],
            ]
        )
    if has_module:
        command_candidates.append([sys.executable, "-m", "f2", "--url", url, "--json"])

    for cmd in command_candidates:
        try:
            cp = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(10, timeout_sec),
            )
        except Exception:
            continue
        if cp.returncode != 0:
            continue

        parsed = _extract_json_from_blob(cp.stdout or "")
        if not parsed:
            continue
        title = _normalize_title(str(parsed.get("title") or ""))
        body = str(
            parsed.get("description")
            or parsed.get("desc")
            or parsed.get("text")
            or parsed.get("content")
            or ""
        ).strip()
        published = _normalize_date(str(parsed.get("published_at") or parsed.get("create_time") or ""))
        if body:
            return title, body, published, ""

        aweme_detail = parsed.get("aweme_detail") if isinstance(parsed.get("aweme_detail"), dict) else {}
        if aweme_detail:
            title = title or _normalize_title(str(aweme_detail.get("desc") or ""))
            body = str(aweme_detail.get("desc") or "").strip()
            published = published or _normalize_date(str(aweme_detail.get("create_time") or ""))
            if body:
                return title, body, published, ""
    return "", "", "", "f2_extract_failed"


def _provider_ytdlp_extract(url: str, *, timeout_sec: int = 30) -> tuple[str, str, str, str]:
    exe = shutil.which("yt-dlp")
    if not exe:
        return "", "", "", "yt_dlp_not_available"

    try:
        cp = subprocess.run(
            [exe, "--skip-download", "--dump-single-json", "--no-warnings", "--", url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(10, timeout_sec),
        )
    except Exception as exc:
        return "", "", "", f"yt_dlp_error:{exc}"

    if cp.returncode != 0:
        message = (cp.stderr or cp.stdout or "").strip()
        return "", "", "", f"yt_dlp_failed:{message or cp.returncode}"

    parsed = _extract_json_from_blob(cp.stdout or "")
    if not parsed:
        return "", "", "", "yt_dlp_invalid_json"

    title = _normalize_title(str(parsed.get("title") or parsed.get("fulltitle") or ""))
    body = str(parsed.get("description") or "").strip()
    upload_date = str(parsed.get("upload_date") or "").strip()
    published = ""
    if len(upload_date) == 8 and upload_date.isdigit():
        published = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    if not body and title:
        body = title
    if not body:
        return title, "", published, "yt_dlp_empty_description"
    return title, body, published, ""


def _extract_front_matter(markdown: str) -> tuple[dict[str, str], str]:
    text = _repair_mojibake(markdown).replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        return {}, text

    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text

    raw_head = text[4:end]
    body = text[end + 5 :]
    meta: dict[str, str] = {}
    for line in raw_head.split("\n"):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip().lower()] = value.strip()
    return meta, body


def _strip_code_blocks(text: str) -> str:
    return re.sub(r"```[\s\S]*?```", "\n", text)


def _looks_like_noise_line(line: str) -> bool:
    value = line.strip()
    if not value:
        return True

    lower = value.lower()
    if lower.startswith(("http://", "https://")):
        return True
    if re.fullmatch(r"img_\d+\.(?:png|jpg|jpeg|webp)", lower):
        return True
    if re.fullmatch(r"\d+/\d+", value):
        return True
    if value.startswith(("©", "漏 ")):
        return True
    if value.count("#") >= 2:
        return True
    if "http" in lower and " " not in lower:
        return True

    if any(token in lower for token in NOISE_LINE_SUBSTRINGS):
        return True
    if any(token in lower for token in ("xhscdn", "beian", "xiaohongshu.com/search_result")):
        return True
    for rule in NOISE_LINE_REGEXES:
        if rule.fullmatch(value):
            return True

    return False


def _looks_like_douyin_noise_line(line: str) -> bool:
    value = str(line or "").strip()
    if not value:
        return True
    if re.search(r"https?://", value, flags=re.IGNORECASE):
        return True
    if value.startswith(("原文链接", "来源", "字幕", "不开启", "720p", "540p", "360p", "推荐视频", "全部评论")):
        return True
    if value.startswith(("00:", "0:", "播放", "截图", "进入全屏", "开启读屏标签", "读屏标签已关闭")):
        return True
    if value.startswith(("重播", "3s 后", "粉丝", "获赞", "举报", "关注")):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?[KWM]?", value, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"\d{2}:\d{2}(?:/\d{2}:\d{2})?", value):
        return True
    lower = value.lower()
    if any(token.lower() in lower for token in DOUYIN_UI_NOISE_SUBSTRINGS):
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9 ._-]{1,20}", value):
        return True
    return False


def _extract_douyin_caption(markdown: str, *, title: str = "") -> str:
    text = _repair_mojibake(markdown).replace("\r\n", "\n").replace("\r", "\n")
    text = _strip_code_blocks(text)
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)

    title_norm = _normalize_title(title)
    candidates: list[str] = []
    hashtag_best = ""

    for raw in text.split("\n"):
        line = re.sub(r"^[#>*\-\s]+", "", raw).strip()
        line = re.sub(r"\s+", " ", line).strip("`*_ ").strip()
        if not line:
            continue
        if any(marker in line for marker in DOUYIN_STOP_MARKERS):
            break
        has_hashtag = "#" in line
        if ((not has_hashtag) and _looks_like_noise_line(line)) or _looks_like_douyin_noise_line(line):
            continue
        if title_norm and line == title_norm:
            continue
        if len(line) < 8:
            continue
        if not re.search(r"[\u4e00-\u9fff]", line):
            continue
        if "#" in line:
            hash_count = len(re.findall(r"#[^\s#]+", line))
            if hash_count >= 2:
                return line
            if not hashtag_best:
                hashtag_best = line
        candidates.append(line)

    if hashtag_best:
        return hashtag_best
    if not candidates:
        return ""
    with_hashtag = [line for line in candidates if "#" in line and len(line) >= 12]
    return (with_hashtag[0] if with_hashtag else candidates[0]).strip()


def _extract_douyin_publish_time(markdown: str) -> str:
    text = _repair_mojibake(markdown)
    m = re.search(r"发布时间[:：]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}(?:\s+[0-9]{2}:[0-9]{2})?)", text)
    return str(m.group(1) if m else "").strip()


def _markdown_to_plain_text(markdown: str, *, title: str = "", source_url: str = "") -> str:
    text = _repair_mojibake(markdown).replace("\r\n", "\n").replace("\r", "\n")
    text = _strip_code_blocks(text)
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)

    is_douyin = _is_douyin_url(source_url)
    lines: list[str] = []
    seen: set[str] = set()
    title_norm = _normalize_title(title)
    for raw in text.split("\n"):
        if is_douyin and any(marker in raw for marker in DOUYIN_STOP_MARKERS):
            break
        line = raw.strip()
        if not line:
            continue

        if line.startswith("#"):
            line = line.lstrip("#").strip()
        line = re.sub(r"^[>*\-\s]+", "", line)
        line = re.sub(r"^\d+[\.、]\s*", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        line = line.strip("`*_ ").strip()

        if _looks_like_noise_line(line):
            continue
        if is_douyin and _looks_like_douyin_noise_line(line):
            continue
        if title_norm and line == title_norm:
            continue
        if len(line) <= 1:
            continue
        if line in seen:
            continue

        seen.add(line)
        lines.append(line)

    # Trim platform-specific wrappers around正文内容.
    while lines and (
        lines[0] in EXACT_NOISE_LINES
        or (
            len(lines[0]) <= 14
            and not re.search(r"[，。！？：；,.!?]", lines[0])
            and not re.match(r"^\d+[\.、]", lines[0])
        )
    ):
        lines.pop(0)

    while lines and (
        lines[-1] in EXACT_NOISE_LINES
        or (
            len(lines[-1]) <= 10
            and not re.search(r"[，。！？：；,.!?]", lines[-1])
            and not re.match(r"^\d+[\.、]", lines[-1])
        )
    ):
        lines.pop()

    merged = "\n".join(lines)
    merged = re.sub(r"\n{3,}", "\n\n", merged)
    return merged.strip()


def _run_single_url(py_cmd: list[str], url: str, output_dir: Path) -> tuple[bool, dict[str, Any], str]:
    ok, result, combined = run_url_reader(py_cmd, url, output_dir)
    return ok, result if isinstance(result, dict) else {}, combined


def _parse_urls(raw_urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_urls:
        url = str(raw or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(_expand_short_url(url))
    return out


def _expand_short_url(url: str) -> str:
    """Best-effort expansion for common short links (e.g. v.douyin.com)."""
    raw = str(url or "").strip()
    if not raw:
        return raw

    try:
        domain = (urlparse(raw).netloc or "").lower()
    except Exception:
        return raw

    short_domains = {"v.douyin.com", "xhslink.com", "b23.tv"}
    if not any(domain == d or domain.endswith(f".{d}") for d in short_domains):
        return raw

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    }
    try:
        resp = requests.get(raw, headers=headers, timeout=12, allow_redirects=True)
        final_url = str(resp.url or "").strip()
        if final_url.startswith("http"):
            return final_url
    except Exception:
        pass
    return raw


def _extract_body_from_source_text(source_text: str, *, url: str, title: str) -> str:
    raw = str(source_text or "").strip()
    if not raw:
        return ""

    text = _repair_mojibake(raw)
    # Remove explicit URLs and short-link forms first.
    text = text.replace(str(url or "").strip(), " ")
    text = URL_INLINE_RE.sub(" ", text)
    text = SHORT_URL_INLINE_RE.sub(" ", text)

    # Remove common share wrappers/noise around copied Douyin text.
    text = text.replace("复制打开抖音，看看", " ")
    text = re.sub(r"【[^】]{1,80}的作品】", " ", text)
    text = re.sub(r"\b[A-Za-z]@\S+\s+\S+:/\s*\d{1,2}/\d{1,2}\b", " ", text)
    text = re.sub(r"\bpage=[A-Za-z0-9_/-]+\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*\d+(?:\.\d+)?\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Reuse existing cleaner to drop platform UI noise while preserving正文.
    body = _markdown_to_plain_text(text, title=title, source_url=url)
    if len(body.strip()) >= 8:
        return body.strip()
    return ""


def _write_body_doc(
    *,
    benchmark_root: Path,
    source_time: str,
    url: str,
    title: str,
    body_text: str,
) -> Path:
    date_str = _normalize_date(source_time) or _today()
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    slug = _safe_slug(title or url, 60)

    output_dir = benchmark_root / "提取正文" / date_str
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{slug}-{date_str}.md"

    # Keep required naming as 标题+提取日期; add hash suffix only when name collision is for another link.
    if output_path.exists():
        try:
            existing_text = output_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            existing_text = ""
        if f"- 原文链接：{url}" not in existing_text:
            output_path = output_dir / f"{slug}-{date_str}-{digest}.md"

    lines = [
        "# 对标文案提取",
        "",
        f"- 标题：{title or '-'}",
        f"- 原文链接：{url}",
    ]

    lines.extend(
        [
            "",
            "## 正文",
            "",
            body_text.strip() or "（正文提取为空，建议打开抓取原文件人工复核）",
            "",
        ]
    )

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def _append_link_log(path: Path, *, run_id: str, source_time: str, items: list[LinkProcessItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if not path.exists():
        lines.append("# 飞书对标链接处理记录\n\n")

    lines.append(f"## {source_time} run={run_id}\n")
    for item in items:
        lines.append(f"- [{item.status}] {item.url}\n")
        lines.append(f"  - 路由状态：{item.route_status}\n")
        lines.append(f"  - 正文状态：{item.content_status}\n")
        lines.append(f"  - 提取来源：{item.provider or '-'}\n")
        lines.append(f"  - 测试链接：{'yes' if item.is_test_url else 'no'}\n")
        lines.append(f"  - 标题：{item.title or '-'}\n")
        lines.append(f"  - 日期：{item.published_date or '-'}\n")
        if item.saved_md:
            lines.append(f"  - 抓取文件：`{item.saved_md}`\n")
        if item.body_file:
            lines.append(f"  - 正文文件：`{item.body_file}`\n")
        lines.append(f"  - 正文字符：{item.body_chars}\n")
        if item.quality_reason:
            lines.append(f"  - 质量原因：{item.quality_reason}\n")
        lines.append("  - 金句入库：已禁用（链接仅入对标链接库）\n")
        if item.error:
            lines.append(f"  - 错误：{item.error}\n")
    lines.append("\n")

    original = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(original + "".join(lines), encoding="utf-8")


def process_urls_to_quotes(
    *,
    urls: list[str],
    quote_dir: Path,
    topic_pool_path: Path,
    apply_mode: bool,
    source_time: str,
    source_ref: str,
    near_dup_threshold: float = DEFAULT_NEAR_DUP_THRESHOLD,
    link_log_path: Path | None = None,
    min_content_chars: int = DEFAULT_MIN_CONTENT_CHARS,
    allow_test_url_skip: bool = True,
    source_text: str = "",
) -> LinkToQuotesResult:
    # Keep signature for compatibility with existing callers.
    _ = (quote_dir, topic_pool_path, near_dup_threshold)

    run_id = f"link_{dt.datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    normalized_urls = _parse_urls(urls)

    py_cmd = resolve_python_cmd([str(URL_READER_VENV_PY)] if URL_READER_VENV_PY.exists() else None)
    if not py_cmd:
        items = [
            LinkProcessItem(
                url=url,
                route_status="failed",
                content_status="failed",
                status="failed",
                provider="none",
                is_test_url=False,
                title="",
                published_date=_normalize_date(source_time) or _today(),
                saved_md="",
                body_file="",
                body_chars=0,
                added_count=0,
                near_dup_count=0,
                quality_reason="python_unavailable",
                error="未找到可用 Python，无法执行 URL 抓取",
            )
            for url in normalized_urls
        ]
        touched: list[Path] = []
        if link_log_path and apply_mode:
            _append_link_log(link_log_path, run_id=run_id, source_time=source_time, items=items)
            touched.append(link_log_path)
        return LinkToQuotesResult(
            run_id=run_id,
            apply_mode=apply_mode,
            quote_added=[],
            near_dups=[],
            exact_dup_count=0,
            touched_files=touched,
            topic_pool_updated=False,
            topic_total=0,
            items=items,
            link_log_path=link_log_path,
        )

    output_dir = CRAWL_ROOT / "url-reader-output" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    benchmark_root = _repo_root() / "03-素材库" / "对标链接库"
    items: list[LinkProcessItem] = []
    touched_files: list[Path] = []
    min_chars = max(1, int(min_content_chars))

    for url in normalized_urls:
        is_test_url = bool(allow_test_url_skip and _is_test_link(url, source_ref=source_ref))
        route_status = "success"
        provider = "none"
        quality_reason = ""
        ok, result, combined = _run_single_url(py_cmd, url, output_dir)
        save_info = result.get("save") if isinstance(result, dict) else {}
        if not isinstance(save_info, dict):
            save_info = {}

        title = _normalize_title(str(save_info.get("title") or result.get("title") or ""))
        md_file = str(save_info.get("md_file") or "").strip()
        published_date = _normalize_date(source_time) or _today()
        body_file = ""
        body_chars = 0
        error = ""
        body_plain = ""

        if ok and md_file and Path(md_file).exists():
            markdown = _repair_mojibake(Path(md_file).read_text(encoding="utf-8", errors="ignore"))
            meta, body_markdown = _extract_front_matter(markdown)
            title = _normalize_title(title or str(meta.get("title") or ""))
            published_date = _pick_published_date(meta, source_time)
            body_plain = _markdown_to_plain_text(body_markdown, title=title, source_url=url)
            if _is_douyin_url(url):
                caption = _extract_douyin_caption(body_markdown, title=title)
                if caption:
                    publish_time = _extract_douyin_publish_time(body_markdown)
                    body_plain = caption if not publish_time else f"{caption}\n发布时间：{publish_time}"
            body_chars = len(body_plain)
            provider = "url_reader"
        elif ok:
            ok = False
            error = "抓取成功但未返回正文文件"

        if (not ok or (not is_test_url and body_chars < min_chars)) and _is_douyin_url(url):
            f2_title, f2_body, f2_published, f2_err = _provider_f2_extract(url)
            if f2_body:
                ok = True
                provider = "f2"
                title = _normalize_title(f2_title or title)
                body_plain = _markdown_to_plain_text(f2_body, title=title, source_url=url)
                body_chars = len(body_plain)
                if f2_published:
                    published_date = _normalize_date(f2_published) or published_date
                error = ""
            elif f2_err and not error:
                error = f2_err

        if (not ok or (not is_test_url and body_chars < min_chars)):
            yt_title, yt_body, yt_published, yt_err = _provider_ytdlp_extract(url)
            if yt_body:
                ok = True
                provider = "yt_dlp"
                title = _normalize_title(yt_title or title)
                body_plain = _markdown_to_plain_text(yt_body, title=title, source_url=url)
                body_chars = len(body_plain)
                if yt_published:
                    published_date = _normalize_date(yt_published) or published_date
                error = ""
            elif yt_err and not error:
                error = yt_err

        if not ok:
            fallback_text, fallback_err = _fallback_fetch_plain_text(url)
            if fallback_text:
                ok = True
                provider = "http_fallback"
                error = ""
                if not title:
                    path_name = Path(urlparse(url).path).name
                    title = _normalize_title(path_name.rsplit(".", 1)[0] if "." in path_name else path_name) or "链接正文"
                published_date = _normalize_date(source_time) or _today()
                body_plain = _markdown_to_plain_text(fallback_text, title=title, source_url=url)
                body_chars = len(body_plain)
            elif fallback_err and not error:
                error = fallback_err

        # Final fallback: use user-provided source text (often contains preview caption in Feishu card copy).
        if (not is_test_url) and body_chars < min_chars and source_text.strip():
            source_body = _extract_body_from_source_text(source_text, url=url, title=title)
            if source_body:
                ok = True
                provider = "source_text"
                body_plain = source_body
                body_chars = len(source_body)
                if not title:
                    title = _normalize_title(source_body[:30]) or title
                error = ""

        content_status = "failed"
        if is_test_url:
            content_status = "skipped_test"
            quality_reason = "test_url_skip"
        elif not ok:
            content_status = "failed"
            quality_reason = "extract_failed"
        elif body_chars < min_chars:
            content_status = "failed"
            quality_reason = f"content_too_short:{body_chars}<{min_chars}"
        else:
            content_status = "success"
            quality_reason = ""

        if apply_mode and body_plain.strip():
            body_doc = _write_body_doc(
                benchmark_root=benchmark_root,
                source_time=source_time,
                url=url,
                title=title,
                body_text=body_plain,
            )
            body_file = body_doc.as_posix()
            touched_files.append(body_doc)

        if not ok:
            errors: list[str] = []
            if isinstance(result.get("errors"), list):
                errors.extend(str(item) for item in result.get("errors") if str(item).strip())
            if result.get("error"):
                errors.append(str(result.get("error")))
            if not error:
                error = " | ".join(errors) or combined or "抓取失败"
        elif content_status == "failed" and not error:
            error = quality_reason

        items.append(
            LinkProcessItem(
                url=url,
                route_status=route_status,
                content_status=content_status,
                status="success" if content_status in {"success", "skipped_test"} else "failed",
                provider=provider or "none",
                is_test_url=is_test_url,
                title=title,
                published_date=published_date,
                saved_md=md_file,
                body_file=body_file,
                body_chars=body_chars,
                added_count=0,
                near_dup_count=0,
                quality_reason=quality_reason,
                error=error,
            )
        )

    if link_log_path and apply_mode:
        _append_link_log(link_log_path, run_id=run_id, source_time=source_time, items=items)
        touched_files.append(link_log_path)

    return LinkToQuotesResult(
        run_id=run_id,
        apply_mode=apply_mode,
        quote_added=[],
        near_dups=[],
        exact_dup_count=0,
        touched_files=touched_files,
        topic_pool_updated=False,
        topic_total=0,
        items=items,
        link_log_path=link_log_path,
    )


def _render_cli_report(result: LinkToQuotesResult) -> str:
    route_success = sum(1 for item in result.items if item.route_status == "success")
    content_success = sum(1 for item in result.items if item.content_status == "success")
    content_failed = sum(1 for item in result.items if item.content_status == "failed")
    content_skipped_test = sum(1 for item in result.items if item.content_status == "skipped_test")
    lines = [
        f"run_id={result.run_id}",
        f"apply_mode={result.apply_mode}",
        f"url_total={len(result.items)}",
        f"route_success={route_success}",
        f"content_success={content_success}",
        f"content_failed={content_failed}",
        f"content_skipped_test={content_skipped_test}",
        f"doc_saved={sum(1 for item in result.items if item.body_file)}",
    ]
    for item in result.items:
        lines.append(
            f"[{item.status}] {item.url} route={item.route_status} content={item.content_status} "
            f"provider={item.provider} body_chars={item.body_chars}"
        )
        if item.title:
            lines.append(f"  title={item.title}")
        if item.published_date:
            lines.append(f"  date={item.published_date}")
        if item.body_file:
            lines.append(f"  body_file={item.body_file}")
        if item.quality_reason:
            lines.append(f"  quality_reason={item.quality_reason}")
        if item.error:
            lines.append(f"  error={item.error}")
    if result.link_log_path:
        lines.append(f"link_log={result.link_log_path}")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest benchmark links into benchmark library")
    parser.add_argument("--urls", nargs="+", required=True, help="One or multiple URLs")
    parser.add_argument("--quote-dir", default=r"03-素材库/金句库", help="Deprecated compatibility argument")
    parser.add_argument("--topic-pool", default=r"01-选题管理/选题规划/金句选题池.md", help="Deprecated compatibility argument")
    parser.add_argument("--source-time", default=_now_iso(), help="Source timestamp for this run")
    parser.add_argument("--source-ref", default="manual-cli", help="Source ref for traceability")
    parser.add_argument(
        "--near-dup-threshold",
        type=float,
        default=DEFAULT_NEAR_DUP_THRESHOLD,
        help="Deprecated compatibility argument",
    )
    parser.add_argument("--apply", action="store_true", help="Write into markdown files")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument(
        "--link-log",
        default="",
        help="Optional link log path. Defaults to 03-素材库/对标链接库/YYYY-MM-DD-feishu-links.md",
    )
    parser.add_argument(
        "--min-content-chars",
        type=int,
        default=DEFAULT_MIN_CONTENT_CHARS,
        help="Minimum content chars for non-test URL to be marked content_success.",
    )
    parser.add_argument(
        "--allow-test-url-skip",
        action="store_true",
        default=True,
        help="Treat known smoke/test links as content_status=skipped_test.",
    )
    parser.add_argument(
        "--disallow-test-url-skip",
        action="store_true",
        help="Disable skipped_test behavior and enforce quality for all links.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    apply_mode = bool(args.apply and not args.dry_run)

    repo_root = _repo_root()
    quote_dir = resolve_path(repo_root, args.quote_dir)
    topic_pool_path = resolve_path(repo_root, args.topic_pool)

    if args.link_log:
        link_log_path = resolve_path(repo_root, args.link_log)
    else:
        link_log_path = repo_root / "03-素材库" / "对标链接库" / f"{_today()}-feishu-links.md"

    result = process_urls_to_quotes(
        urls=args.urls,
        quote_dir=quote_dir,
        topic_pool_path=topic_pool_path,
        apply_mode=apply_mode,
        source_time=args.source_time,
        source_ref=args.source_ref,
        near_dup_threshold=args.near_dup_threshold,
        link_log_path=link_log_path,
        min_content_chars=max(1, int(args.min_content_chars)),
        allow_test_url_skip=bool(args.allow_test_url_skip and not args.disallow_test_url_skip),
    )
    sys.stdout.write(_render_cli_report(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
