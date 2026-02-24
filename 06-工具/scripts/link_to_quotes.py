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
import re
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


@dataclass(frozen=True)
class LinkProcessItem:
    url: str
    status: str
    title: str
    published_date: str
    saved_md: str
    body_file: str
    body_chars: int
    added_count: int
    near_dup_count: int
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


def _markdown_to_plain_text(markdown: str, *, title: str = "") -> str:
    text = _repair_mojibake(markdown).replace("\r\n", "\n").replace("\r", "\n")
    text = _strip_code_blocks(text)
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)

    lines: list[str] = []
    seen: set[str] = set()
    title_norm = _normalize_title(title)
    for raw in text.split("\n"):
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
        lines.append(f"  - 标题：{item.title or '-'}\n")
        lines.append(f"  - 日期：{item.published_date or '-'}\n")
        if item.saved_md:
            lines.append(f"  - 抓取文件：`{item.saved_md}`\n")
        if item.body_file:
            lines.append(f"  - 正文文件：`{item.body_file}`\n")
        lines.append(f"  - 正文字符：{item.body_chars}\n")
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
                status="failed",
                title="",
                published_date=_normalize_date(source_time) or _today(),
                saved_md="",
                body_file="",
                body_chars=0,
                added_count=0,
                near_dup_count=0,
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

    for url in normalized_urls:
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

        if ok and md_file and Path(md_file).exists():
            markdown = _repair_mojibake(Path(md_file).read_text(encoding="utf-8", errors="ignore"))
            meta, body_markdown = _extract_front_matter(markdown)
            title = _normalize_title(title or str(meta.get("title") or ""))
            published_date = _pick_published_date(meta, source_time)
            body_plain = _markdown_to_plain_text(body_markdown, title=title)
            body_chars = len(body_plain)

            if apply_mode:
                body_doc = _write_body_doc(
                    benchmark_root=benchmark_root,
                    source_time=source_time,
                    url=url,
                    title=title,
                    body_text=body_plain,
                )
                body_file = body_doc.as_posix()
                touched_files.append(body_doc)
        elif ok:
            ok = False
            error = "抓取成功但未返回正文文件"

        if not ok:
            fallback_text, fallback_err = _fallback_fetch_plain_text(url)
            if fallback_text:
                ok = True
                error = ""
                if not title:
                    path_name = Path(urlparse(url).path).name
                    title = _normalize_title(path_name.rsplit(".", 1)[0] if "." in path_name else path_name) or "链接正文"
                published_date = _normalize_date(source_time) or _today()
                body_plain = _markdown_to_plain_text(fallback_text, title=title)
                body_chars = len(body_plain)
                if apply_mode:
                    body_doc = _write_body_doc(
                        benchmark_root=benchmark_root,
                        source_time=source_time,
                        url=url,
                        title=title,
                        body_text=body_plain,
                    )
                    body_file = body_doc.as_posix()
                    touched_files.append(body_doc)
            elif fallback_err and not error:
                error = fallback_err

        if not ok:
            errors: list[str] = []
            if isinstance(result.get("errors"), list):
                errors.extend(str(item) for item in result.get("errors") if str(item).strip())
            if result.get("error"):
                errors.append(str(result.get("error")))
            if not error:
                error = " | ".join(errors) or combined or "抓取失败"

        items.append(
            LinkProcessItem(
                url=url,
                status="success" if ok else "failed",
                title=title,
                published_date=published_date,
                saved_md=md_file,
                body_file=body_file,
                body_chars=body_chars,
                added_count=0,
                near_dup_count=0,
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
    lines = [
        f"run_id={result.run_id}",
        f"apply_mode={result.apply_mode}",
        f"url_total={len(result.items)}",
        f"doc_saved={sum(1 for item in result.items if item.body_file)}",
    ]
    for item in result.items:
        lines.append(f"[{item.status}] {item.url} body_chars={item.body_chars}")
        if item.title:
            lines.append(f"  title={item.title}")
        if item.published_date:
            lines.append(f"  date={item.published_date}")
        if item.body_file:
            lines.append(f"  body_file={item.body_file}")
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
    )
    sys.stdout.write(_render_cli_report(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
