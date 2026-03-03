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
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from crawl_bridge import CRAWL_ROOT, URL_READER_VENV_PY, resolve_python_cmd, run_url_reader
from quote_ingest_core import DEFAULT_NEAR_DUP_THRESHOLD, CandidateQuote, NearDupItem, resolve_path
try:
    from douyin_asr_extractor import DouyinAsrError, extract_douyin_asr_dict
except Exception:  # pragma: no cover - optional runtime dependency
    DouyinAsrError = RuntimeError  # type: ignore[assignment]
    extract_douyin_asr_dict = None  # type: ignore[assignment]

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
DOUYIN_RECOMMEND_NOISE_TERMS = (
    "推荐视频",
    "全部评论",
    "大家都在搜",
    "你可能感兴趣",
    "相关搜索",
    "举报",
)
DOUYIN_SHARE_PREFIX_RE = re.compile(
    r"^\s*\d+(?:\.\d+)?\s+[A-Za-z0-9@._-]{2,20}\s+\d{2}/\d{2}\s+[A-Za-z]{2,8}:/\s*",
    re.IGNORECASE,
)
DOUYIN_SHARE_OPENING_RE = re.compile(
    r"^\s*(?:复制打开抖音，看看|复制打开抖音看看|打开抖音，看看)\s*(?:【[^】]+】)?\s*",
    re.IGNORECASE,
)
DOUYIN_SHARE_TRAILING_PATTERNS = (
    re.compile(r"复制此链接[，,。.!！]*\s*打开(?:Dou音|抖音)搜索[，,。.!！]*\s*直接观看视频[！!。.\s]*$", re.IGNORECASE),
    re.compile(r"复制此链接[，,。.!！]*\s*打开(?:Dou音|抖音)搜索[！!。.\s]*$", re.IGNORECASE),
    re.compile(r"打开(?:Dou音|抖音)搜索[，,。.!！]*\s*直接观看视频[！!。.\s]*$", re.IGNORECASE),
)

def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


DEFAULT_MIN_CONTENT_CHARS = 120
DEFAULT_DOUYIN_CAPTION_MIN_SCORE = 2
DEFAULT_DOUYIN_CAPTION_MIN_LEN = 20
DEFAULT_DOUYIN_TITLE_MAX_CHARS = 120
DEFAULT_DOUYIN_STRICT_FULL_TEXT = True
DEFAULT_DOUYIN_SUMMARY_BLOCK = True
DEFAULT_DOUYIN_MIN_SENTENCES = 3
DEFAULT_DOUYIN_PREFER_SOURCE_TEXT = True
DEFAULT_DOUYIN_BITABLE_ENABLED = True
DEFAULT_DOUYIN_BITABLE_READ_FIRST = True
DEFAULT_DOUYIN_BITABLE_WRITE_BACK = True
DEFAULT_DOUYIN_SOURCE_MODE = "bitable_only"
DEFAULT_DOUYIN_PIPELINE_MODE = "asr_primary"
DEFAULT_DOUYIN_BITABLE_FALLBACK_FULL_SCAN = True
DEFAULT_DOUYIN_ASR_ENABLED = True
DEFAULT_DOUYIN_ASR_TIMEOUT_SEC = 600
DEFAULT_DOUYIN_WRITE_SUMMARY = True
DEFAULT_DOUYIN_WRITE_KEYPOINTS = True
DEFAULT_DOUYIN_DEDUP_KEY_MODE = "video_or_canonical_url"
FEISHU_OPEN_BASE_URL = "https://open.feishu.cn"
FEISHU_HTTP_TIMEOUT_SEC = 20
FEISHU_HTTP_VERIFY_SSL = True
BITABLE_APP_TOKEN = ""
BITABLE_TABLE_ID = ""
BITABLE_VIEW_ID = ""
BITABLE_VIDEO_ID_FIELD = "视频ID"
BITABLE_LINK_FIELD = "视频链接"
BITABLE_DESC_FIELD = "视频描述"
BITABLE_TEXT_FIELD = "文案整理"
BITABLE_TEXT_FALLBACK_FIELD = "文案出参"
BITABLE_STATUS_FIELD = "入库状态"
BITABLE_REASON_FIELD = "失败原因"
_BITABLE_TOKEN_CACHE: dict[str, Any] = {"token": "", "expire_at": 0.0}
_BITABLE_FIELD_NAME_CACHE: dict[str, Any] = {"names": set(), "expire_at": 0.0}
TEST_DOMAIN_HINTS = {
    "example.com",
    "raw.githubusercontent.com",
    "localhost",
    "127.0.0.1",
}

ENV_RUNTIME_FALLBACKS = (
    Path("/etc/openclaw/feishu.env"),
    Path(__file__).resolve().parent / ".env.ingest-writer.local",
    Path(__file__).resolve().parent / ".env.ingest-writer",
    Path(__file__).resolve().parent / ".env.feishu",
)
ENV_RUNTIME_REQUIRED_KEYS = {
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "BITABLE_APP_TOKEN",
    "BITABLE_TABLE_ID",
    "BITABLE_VIEW_ID",
    "BITABLE_TEXT_FIELD",
    "BITABLE_TEXT_FALLBACK_FIELD",
    "INGEST_DOUYIN_SOURCE_MODE",
    "INGEST_DOUYIN_PIPELINE_MODE",
    "INGEST_DOUYIN_BITABLE_ENABLED",
}


def _load_runtime_env_fallbacks() -> None:
    missing = [k for k in ENV_RUNTIME_REQUIRED_KEYS if not (os.getenv(k) or "").strip()]
    if not missing:
        return
    for env_path in ENV_RUNTIME_FALLBACKS:
        if not env_path.exists():
            continue
        text = env_path.read_text(encoding="utf-8", errors="ignore")
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.lower().startswith("export "):
                key = key[7:].strip()
            value = value.strip().strip('"').strip("'")
            if key and value and not (os.getenv(key) or "").strip():
                os.environ[key] = value
        missing = [k for k in ENV_RUNTIME_REQUIRED_KEYS if not (os.getenv(k) or "").strip()]
        if not missing:
            return


def _refresh_runtime_config() -> None:
    global DEFAULT_MIN_CONTENT_CHARS
    global DEFAULT_DOUYIN_CAPTION_MIN_SCORE
    global DEFAULT_DOUYIN_CAPTION_MIN_LEN
    global DEFAULT_DOUYIN_TITLE_MAX_CHARS
    global DEFAULT_DOUYIN_STRICT_FULL_TEXT
    global DEFAULT_DOUYIN_SUMMARY_BLOCK
    global DEFAULT_DOUYIN_MIN_SENTENCES
    global DEFAULT_DOUYIN_PREFER_SOURCE_TEXT
    global DEFAULT_DOUYIN_BITABLE_ENABLED
    global DEFAULT_DOUYIN_BITABLE_READ_FIRST
    global DEFAULT_DOUYIN_BITABLE_WRITE_BACK
    global DEFAULT_DOUYIN_SOURCE_MODE
    global DEFAULT_DOUYIN_PIPELINE_MODE
    global DEFAULT_DOUYIN_BITABLE_FALLBACK_FULL_SCAN
    global DEFAULT_DOUYIN_ASR_ENABLED
    global DEFAULT_DOUYIN_ASR_TIMEOUT_SEC
    global DEFAULT_DOUYIN_WRITE_SUMMARY
    global DEFAULT_DOUYIN_WRITE_KEYPOINTS
    global DEFAULT_DOUYIN_DEDUP_KEY_MODE
    global FEISHU_OPEN_BASE_URL
    global FEISHU_HTTP_TIMEOUT_SEC
    global FEISHU_HTTP_VERIFY_SSL
    global BITABLE_APP_TOKEN
    global BITABLE_TABLE_ID
    global BITABLE_VIEW_ID
    global BITABLE_VIDEO_ID_FIELD
    global BITABLE_LINK_FIELD
    global BITABLE_DESC_FIELD
    global BITABLE_TEXT_FIELD
    global BITABLE_TEXT_FALLBACK_FIELD
    global BITABLE_STATUS_FIELD
    global BITABLE_REASON_FIELD

    _load_runtime_env_fallbacks()

    DEFAULT_MIN_CONTENT_CHARS = max(1, int(os.getenv("INGEST_LINK_MIN_CONTENT_CHARS", "120")))
    DEFAULT_DOUYIN_CAPTION_MIN_SCORE = int(os.getenv("INGEST_DOUYIN_CAPTION_MIN_SCORE", "2"))
    DEFAULT_DOUYIN_CAPTION_MIN_LEN = max(8, int(os.getenv("INGEST_DOUYIN_CAPTION_MIN_LEN", "20")))
    DEFAULT_DOUYIN_TITLE_MAX_CHARS = max(20, int(os.getenv("INGEST_DOUYIN_TITLE_MAX_CHARS", "120")))
    DEFAULT_DOUYIN_STRICT_FULL_TEXT = _env_bool("INGEST_DOUYIN_STRICT_FULL_TEXT", True)
    DEFAULT_DOUYIN_SUMMARY_BLOCK = _env_bool("INGEST_DOUYIN_SUMMARY_BLOCK", True)
    DEFAULT_DOUYIN_MIN_SENTENCES = max(1, int(os.getenv("INGEST_DOUYIN_MIN_SENTENCES", "3")))
    DEFAULT_DOUYIN_PREFER_SOURCE_TEXT = _env_bool("INGEST_DOUYIN_PREFER_SOURCE_TEXT", True)
    DEFAULT_DOUYIN_BITABLE_ENABLED = _env_bool("INGEST_DOUYIN_BITABLE_ENABLED", True)
    DEFAULT_DOUYIN_BITABLE_READ_FIRST = _env_bool("INGEST_DOUYIN_BITABLE_READ_FIRST", True)
    DEFAULT_DOUYIN_BITABLE_WRITE_BACK = _env_bool("INGEST_DOUYIN_BITABLE_WRITE_BACK", True)
    source_mode_raw = str(os.getenv("INGEST_DOUYIN_SOURCE_MODE") or "bitable_only").strip().lower()
    DEFAULT_DOUYIN_SOURCE_MODE = source_mode_raw if source_mode_raw in {"bitable_only", "hybrid"} else "hybrid"
    pipeline_mode_raw = str(os.getenv("INGEST_DOUYIN_PIPELINE_MODE") or "").strip().lower()
    if not pipeline_mode_raw:
        pipeline_mode_raw = "bitable_only" if DEFAULT_DOUYIN_SOURCE_MODE == "bitable_only" else "bitable_primary"
    DEFAULT_DOUYIN_PIPELINE_MODE = (
        pipeline_mode_raw
        if pipeline_mode_raw in {"asr_primary", "bitable_primary", "bitable_only"}
        else "asr_primary"
    )
    DEFAULT_DOUYIN_BITABLE_FALLBACK_FULL_SCAN = _env_bool("INGEST_DOUYIN_BITABLE_FALLBACK_FULL_SCAN", True)
    DEFAULT_DOUYIN_ASR_ENABLED = _env_bool("INGEST_DOUYIN_ASR_ENABLED", True)
    DEFAULT_DOUYIN_ASR_TIMEOUT_SEC = max(60, int(os.getenv("INGEST_DOUYIN_ASR_TIMEOUT_SEC", "600")))
    DEFAULT_DOUYIN_WRITE_SUMMARY = _env_bool("INGEST_DOUYIN_WRITE_SUMMARY", True)
    DEFAULT_DOUYIN_WRITE_KEYPOINTS = _env_bool("INGEST_DOUYIN_WRITE_KEYPOINTS", True)
    DEFAULT_DOUYIN_DEDUP_KEY_MODE = str(
        os.getenv("INGEST_DOUYIN_DEDUP_KEY_MODE") or "video_or_canonical_url"
    ).strip().lower()
    FEISHU_OPEN_BASE_URL = str(os.getenv("FEISHU_OPEN_BASE_URL") or "https://open.feishu.cn").strip().rstrip("/")
    FEISHU_HTTP_TIMEOUT_SEC = max(5, int(os.getenv("FEISHU_HTTP_TIMEOUT_SEC", "20")))
    FEISHU_HTTP_VERIFY_SSL = _env_bool("FEISHU_HTTP_VERIFY_SSL", True)
    BITABLE_APP_TOKEN = str(os.getenv("BITABLE_APP_TOKEN") or os.getenv("FEISHU_BITABLE_APP_TOKEN") or "").strip()
    BITABLE_TABLE_ID = str(os.getenv("BITABLE_TABLE_ID") or os.getenv("FEISHU_BITABLE_TABLE_ID") or "").strip()
    BITABLE_VIEW_ID = str(os.getenv("BITABLE_VIEW_ID") or os.getenv("FEISHU_BITABLE_VIEW_ID") or "").strip()
    BITABLE_VIDEO_ID_FIELD = str(os.getenv("BITABLE_VIDEO_ID_FIELD") or "视频ID").strip()
    BITABLE_LINK_FIELD = str(os.getenv("BITABLE_LINK_FIELD") or "视频链接").strip()
    BITABLE_DESC_FIELD = str(os.getenv("BITABLE_DESC_FIELD") or "视频描述").strip()
    BITABLE_TEXT_FIELD = str(os.getenv("BITABLE_TEXT_FIELD") or "文案整理").strip()
    BITABLE_TEXT_FALLBACK_FIELD = str(os.getenv("BITABLE_TEXT_FALLBACK_FIELD") or "文案出参").strip()
    BITABLE_STATUS_FIELD = str(os.getenv("BITABLE_STATUS_FIELD") or "入库状态").strip()
    BITABLE_REASON_FIELD = str(os.getenv("BITABLE_REASON_FIELD") or "失败原因").strip()


_refresh_runtime_config()


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
    douyin_main_section_used: bool = False
    douyin_caption_score: int = 0
    douyin_caption_selected: str = ""
    summary_detected: bool = False
    text_source: str = ""
    reject_reason: str = ""
    douyin_pipeline_mode: str = ""
    douyin_source_used: str = ""
    douyin_dedup_key: str = ""


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


@dataclass(frozen=True)
class DouyinCaptionPick:
    text: str
    score: int
    used_main_section: bool


@dataclass(frozen=True)
class DouyinCandidateEval:
    source: str
    text: str
    score: int
    chars: int
    sentence_count: int
    summary_detected: bool
    reject_reason: str


def _repair_mojibake(text: str) -> str:
    """Fix UTF-8/GBK mojibake conservatively without damaging normal Chinese text."""
    value = str(text or "")
    if not value:
        return value
    # Only attempt conversion when obvious mojibake hints exist; avoid corrupting valid text.
    hint_count = len(re.findall(r"(?:锟斤拷|�|鍙|鍚|鍒|鍏|鍥|鎴|鏂|鏄|浠|鐨|璇|璁|瀵|绗|閾|闂|甯|骞|銆|锛)", value))
    if hint_count <= 0:
        return value
    try:
        repaired = value.encode("gbk").decode("utf-8")
    except Exception:
        return value
    if not repaired:
        return value

    def _score_readability(s: str) -> int:
        common_hits = sum(s.count(token) for token in ("的", "了", "是", "在", "我", "你", "他", "有", "和", "不", "这", "那", "我们", "可以"))
        punct_hits = sum(s.count(ch) for ch in ("，", "。", "！", "？", "：", "；", "、", "\n"))
        bad_hits = len(re.findall(r"(?:锟斤拷|�|鍙|鍚|鍒|鍏|鍥|鎴|鏂|鏄|浠|鐨|璇|璁|瀵|绗|閾|闂|甯|骞|銆|锛)", s))
        return common_hits * 3 + punct_hits - bad_hits * 4

    # Accept repaired text only if readability is clearly better.
    return repaired if _score_readability(repaired) > (_score_readability(value) + 2) else value


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


def _effective_min_chars(*, url: str, min_chars: int) -> int:
    return max(1, int(min_chars))


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


def _extract_douyin_video_id(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    match = re.search(r"/video/(\d{8,32})", raw, flags=re.IGNORECASE)
    if match:
        return str(match.group(1) or "").strip()
    return ""


def _bitable_enabled() -> bool:
    return bool(
        DEFAULT_DOUYIN_BITABLE_ENABLED
        and BITABLE_APP_TOKEN
        and BITABLE_TABLE_ID
        and os.getenv("FEISHU_APP_ID", "").strip()
        and os.getenv("FEISHU_APP_SECRET", "").strip()
    )


def _bitable_get_token() -> str:
    now = time.time()
    token = str(_BITABLE_TOKEN_CACHE.get("token") or "").strip()
    expire_at = float(_BITABLE_TOKEN_CACHE.get("expire_at") or 0.0)
    if token and expire_at - 60 > now:
        return token

    payload = {
        "app_id": str(os.getenv("FEISHU_APP_ID") or "").strip(),
        "app_secret": str(os.getenv("FEISHU_APP_SECRET") or "").strip(),
    }
    response = requests.post(
        f"{FEISHU_OPEN_BASE_URL}/open-apis/auth/v3/tenant_access_token/internal",
        json=payload,
        timeout=FEISHU_HTTP_TIMEOUT_SEC,
        verify=FEISHU_HTTP_VERIFY_SSL,
    )
    response.raise_for_status()
    data = json.loads(response.content.decode("utf-8"))
    if not isinstance(data, dict):
        data = response.json()
    if int(data.get("code") or 0) != 0:
        raise RuntimeError(f"bitable auth failed: {data.get('msg') or data}")
    token = str(data.get("tenant_access_token") or "").strip()
    if not token:
        raise RuntimeError("bitable auth token empty")
    expire = int(data.get("expire") or 7200)
    _BITABLE_TOKEN_CACHE["token"] = token
    _BITABLE_TOKEN_CACHE["expire_at"] = now + max(300, expire)
    return token


def _bitable_request_json(*, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    token = _bitable_get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    url = f"{FEISHU_OPEN_BASE_URL}{path}"
    response = requests.request(
        method,
        url,
        headers=headers,
        json=payload,
        timeout=FEISHU_HTTP_TIMEOUT_SEC,
        verify=FEISHU_HTTP_VERIFY_SSL,
    )
    response.raise_for_status()
    data = json.loads(response.content.decode("utf-8"))
    if not isinstance(data, dict):
        data = response.json()
    if int(data.get("code") or 0) != 0:
        raise RuntimeError(f"bitable request failed: {data.get('msg') or data}")
    return data


def _bitable_get_field_names() -> set[str]:
    if not _bitable_enabled():
        return set()
    now = time.time()
    cached = _BITABLE_FIELD_NAME_CACHE.get("names")
    if isinstance(cached, set) and cached and float(_BITABLE_FIELD_NAME_CACHE.get("expire_at") or 0.0) - 60 > now:
        return cached

    path = f"/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/fields?page_size=500"
    if BITABLE_VIEW_ID:
        path += f"&view_id={BITABLE_VIEW_ID}"
    data = _bitable_request_json(method="GET", path=path)
    items = ((data.get("data") or {}).get("items") or []) if isinstance(data, dict) else []
    names: set[str] = set()
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("field_name") or "").strip()
            if name:
                names.add(name)
    _BITABLE_FIELD_NAME_CACHE["names"] = names
    _BITABLE_FIELD_NAME_CACHE["expire_at"] = now + 900
    return names


def _bitable_cell_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value).strip()
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            text = _bitable_cell_to_text(item)
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip()
    if isinstance(value, dict):
        if "text" in value and isinstance(value.get("text"), str):
            return str(value.get("text") or "").strip()
        # rich-text array item e.g. {"type":"text","text":"..."}
        for key in ("link", "url", "href", "name", "value"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        chunks: list[str] = []
        for raw in value.values():
            text = _bitable_cell_to_text(raw)
            if text:
                chunks.append(text)
        if chunks:
            return "\n".join(chunks).strip()
    return ""


def _douyin_source_rank(source: str) -> int:
    mode = str(DEFAULT_DOUYIN_PIPELINE_MODE or "").strip().lower()
    source_name = str(source or "").strip().lower()

    base = {
        "asr": 1,
        "bitable": 2,
        "bitable_text": 2,
        "share_text": 3,
        "url_reader_main": 4,
        "f2": 5,
        "yt_dlp": 6,
        "http_fallback": 7,
        "url_reader_caption": 8,
    }
    if mode == "bitable_primary":
        base["bitable"] = 0
        base["bitable_text"] = 0
        base["asr"] = 1
    elif mode == "bitable_only":
        base["bitable"] = 0
        base["bitable_text"] = 0
        base["asr"] = 50
    else:
        # default asr_primary
        base["asr"] = 0
        base["bitable"] = 1
        base["bitable_text"] = 1

    return int(base.get(source_name, 99))


def _normalize_match_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw.lower()
    scheme = str(parsed.scheme or "https").lower()
    host = str(parsed.netloc or "").lower()
    path = str(parsed.path or "").strip()
    if not host:
        return raw.lower()
    if not path:
        path = "/"
    path = re.sub(r"/+", "/", path)
    return f"{scheme}://{host}{path}".rstrip("/")


_BITABLE_FULLTEXT_MARKER_RE = re.compile(
    r"(?:^|\n)\s*(?:完整文案|文案出参|正文|全文)\s*[：:]\s*",
    flags=re.IGNORECASE,
)


def _extract_bitable_fulltext_segment(text: str) -> tuple[str, bool]:
    """Extract or normalize Bitable text while preserving useful summary/keypoint blocks."""
    value = str(text or "").replace("\r\n", "\n").strip()
    if not value:
        return "", False
    # If the field already contains structured blocks (摘要/关键点), preserve them.
    if re.search(r"(?:^|\n)\s*##\s*摘要", value) or re.search(r"(?:^|\n)\s*##\s*关键点", value):
        return value, True
    match = _BITABLE_FULLTEXT_MARKER_RE.search(value)
    if not match:
        return value, False
    candidate = value[match.end() :].strip()
    if not candidate:
        return value, False
    return candidate, True


def _bitable_pick_text(fields: dict[str, Any]) -> str:
    # Prefer explicitly configured fields first, then known legacy aliases.
    ordered_fields = [
        BITABLE_TEXT_FIELD,
        BITABLE_TEXT_FALLBACK_FIELD,
        "文案整理",
        "文案出参",
        "内容提取.文案",
        "文案摘要",
        "内容提取.摘要",
    ]
    prioritized_candidates: list[tuple[int, int, int, str]] = []
    seen_fields: set[str] = set()
    for idx, field in enumerate(ordered_fields):
        if not field:
            continue
        field = str(field).strip()
        if not field or field in seen_fields:
            continue
        seen_fields.add(field)
        raw_text = _bitable_cell_to_text(fields.get(field))
        if not raw_text:
            continue
        extracted_text, used_full_marker = _extract_bitable_fulltext_segment(raw_text)
        cleaned = _clean_douyin_text_for_output(extracted_text)
        if not cleaned:
            continue
        is_summary = 1 if _is_summary_candidate(cleaned) else 0
        marker_penalty = 0 if used_full_marker else 1
        prioritized_candidates.append((marker_penalty, is_summary, idx, cleaned))

    if prioritized_candidates:
        prioritized_candidates.sort(key=lambda item: (item[0], item[1], item[2], -len(item[3])))
        return prioritized_candidates[0][3]

    candidates: list[tuple[int, int, str]] = []
    for key, raw in fields.items():
        text = _bitable_cell_to_text(raw)
        if not text:
            continue
        if re.fullmatch(r"\d{8,22}", text):
            continue
        if not re.search(r"[\u4e00-\u9fffA-Za-z]", text):
            continue
        extracted_text, _ = _extract_bitable_fulltext_segment(text)
        cleaned = _clean_douyin_text_for_output(extracted_text)
        if not cleaned:
            continue
        lower = cleaned.lower()
        if ("douyin.com" in lower or "iesdouyin.com" in lower) and len(cleaned) < 200:
            continue
        key_text = str(key or "")
        pri = 3
        if ("文案" in key_text) or ("copy" in key_text.lower()):
            pri = 0
        elif ("摘要" in key_text) or ("summary" in key_text.lower()):
            pri = 1
        elif ("内容" in key_text) or ("提取" in key_text) or ("整理" in key_text):
            pri = 2
        candidates.append((pri, -len(cleaned), cleaned))
    if candidates:
        candidates.sort()
        return candidates[0][2]
    return ""


def _bitable_search_douyin_text(url: str, source_text: str = "") -> tuple[str, str, str]:
    if not _bitable_enabled():
        return "", "", "bitable_disabled_or_not_configured"

    query_url = str(url or "").strip()
    expanded_url = _expand_short_url(query_url)
    match_urls = {x for x in {_normalize_match_url(query_url), _normalize_match_url(expanded_url)} if x}
    video_ids = {x for x in {_extract_douyin_video_id(query_url), _extract_douyin_video_id(expanded_url)} if x}
    if not video_ids and not match_urls:
        return "", "", "bitable_no_query_key"

    # In strict bitable mode we only trust exact link/video-id matching.
    # Do not use text-overlap fallback to avoid cross-video false matches.

    def fetch_records(*, with_view: bool) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {}
        if with_view and BITABLE_VIEW_ID:
            payload["view_id"] = BITABLE_VIEW_ID

        items: list[dict[str, Any]] = []
        page_token = ""
        # Avoid strict server-side filter operators (they vary by field type and can raise InvalidFilter).
        # Pull recent records and match video_id/url locally for stability.
        for _ in range(5):  # up to 1000 records with page_size=200
            path = f"/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/records/search?page_size=200"
            if page_token:
                path = f"{path}&page_token={page_token}"
            data = _bitable_request_json(method="POST", path=path, payload=payload)
            batch = ((data.get("data") or {}).get("items") or []) if isinstance(data, dict) else []
            if isinstance(batch, list):
                items.extend(item for item in batch if isinstance(item, dict))
            has_more = bool((data.get("data") or {}).get("has_more")) if isinstance(data, dict) else False
            page_token = str((data.get("data") or {}).get("page_token") or "").strip() if isinstance(data, dict) else ""
            if not has_more or not page_token:
                break
        return items

    search_scopes: list[bool] = [True]
    if DEFAULT_DOUYIN_BITABLE_FALLBACK_FULL_SCAN and BITABLE_VIEW_ID:
        search_scopes.append(False)

    saw_any_record = False
    saw_matched_record = False
    for with_view in search_scopes:
        items = fetch_records(with_view=with_view)
        if not isinstance(items, list) or not items:
            continue
        saw_any_record = True
        for item in items:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields") or {}
            if not isinstance(fields, dict):
                continue
            id_text = _bitable_cell_to_text(fields.get(BITABLE_VIDEO_ID_FIELD))
            link_text = _bitable_cell_to_text(fields.get(BITABLE_LINK_FIELD))
            if not id_text or not link_text:
                for _, raw in fields.items():
                    t = _bitable_cell_to_text(raw)
                    if not t:
                        continue
                    if not link_text and ("douyin.com" in t or "iesdouyin.com" in t):
                        link_text = t
                    if not id_text:
                        m = re.search(r"/video/(\d{8,32})", t, flags=re.IGNORECASE)
                        if m:
                            id_text = str(m.group(1) or "").strip()
                        elif re.fullmatch(r"\d{15,22}", t):
                            id_text = t
            link_text_expanded = _expand_short_url(link_text) if link_text else ""
            link_text_norm_candidates = {
                x for x in {_normalize_match_url(link_text), _normalize_match_url(link_text_expanded)} if x
            }
            matched_by_video_id = any(
                video_id
                and (
                    video_id in id_text
                    or video_id in link_text
                    or video_id in link_text_expanded
                )
                for video_id in video_ids
            )
            matched_by_url = False
            if match_urls:
                matched_by_url = any(
                    match_url
                    and (
                        any(match_url in candidate for candidate in link_text_norm_candidates)
                        or match_url in _normalize_match_url(id_text)
                    )
                    for match_url in match_urls
                )

            # Link-first strategy:
            # 1) if request has URL key, prefer URL match;
            # 2) only when record has no usable link text, allow video-id fallback.
            if match_urls:
                matched = matched_by_url or (not link_text_norm_candidates and matched_by_video_id)
            else:
                matched = matched_by_video_id
            text = _bitable_pick_text(fields)
            if not matched:
                continue
            saw_matched_record = True
            if text:
                return text, str(item.get("record_id") or "").strip(), ""

    if not saw_any_record:
        return "", "", "bitable_no_record"
    if not saw_matched_record:
        return "", "", "bitable_no_record"
    return "", "", "bitable_record_without_text"


def _bitable_write_back(
    *,
    url: str,
    title: str,
    body_text: str,
    content_status: str,
    quality_reason: str,
    source: str,
    record_id_hint: str = "",
) -> str:
    if not (_bitable_enabled() and DEFAULT_DOUYIN_BITABLE_WRITE_BACK):
        return ""

    field_names = _bitable_get_field_names()
    if not field_names:
        return "bitable_field_meta_empty"

    video_id = _extract_douyin_video_id(url)
    fields: dict[str, Any] = {}
    if BITABLE_VIDEO_ID_FIELD and BITABLE_VIDEO_ID_FIELD in field_names and video_id:
        fields[BITABLE_VIDEO_ID_FIELD] = video_id
    if BITABLE_LINK_FIELD and BITABLE_LINK_FIELD in field_names and url:
        fields[BITABLE_LINK_FIELD] = str(url).strip()
    if BITABLE_DESC_FIELD and BITABLE_DESC_FIELD in field_names and title:
        fields[BITABLE_DESC_FIELD] = str(title).strip()

    if str(content_status) == "success":
        if BITABLE_TEXT_FIELD and BITABLE_TEXT_FIELD in field_names and body_text:
            fields[BITABLE_TEXT_FIELD] = body_text
        if BITABLE_TEXT_FALLBACK_FIELD and BITABLE_TEXT_FALLBACK_FIELD in field_names and body_text:
            fields[BITABLE_TEXT_FALLBACK_FIELD] = body_text

    if BITABLE_STATUS_FIELD and BITABLE_STATUS_FIELD in field_names:
        fields[BITABLE_STATUS_FIELD] = str(content_status or "").strip()
    if BITABLE_REASON_FIELD and BITABLE_REASON_FIELD in field_names:
        fields[BITABLE_REASON_FIELD] = str(quality_reason or "").strip()

    # Attach trace source if such a field exists.
    if "文案来源" in field_names:
        fields["文案来源"] = str(source or "").strip()
    if not fields:
        return ""

    record_id = str(record_id_hint or "").strip()
    if not record_id:
        _, record_id, _ = _bitable_search_douyin_text(url)

    try:
        if record_id:
            _bitable_request_json(
                method="PUT",
                path=f"/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/records/{record_id}",
                payload={"fields": fields},
            )
        else:
            _bitable_request_json(
                method="POST",
                path=f"/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/records",
                payload={"fields": fields},
            )
    except Exception as exc:
        return f"bitable_write_failed:{exc}"
    return ""


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
    if value.startswith(("00:", "0:", "播放", "截图", "进入全屏", "开启读屏标签", "读屏标签已关闭", "章节要点", "因浏览器限制")):
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
    if len(value) <= 12 and re.search(r"[\u4e00-\u9fff]", value) and not re.search(r"[，。！？：；,.!?]", value):
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9 ._-]{1,20}", value):
        return True
    return False


def _normalize_douyin_candidate_line(raw: str) -> str:
    line = re.sub(r"^[#>*\-\s]+", "", str(raw or "")).strip()
    line = re.sub(r"https?://\S+", " ", line, flags=re.IGNORECASE)
    line = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", line)
    line = re.sub(r"!\[[^\]]*\]\([^\)]*\)", " ", line)
    line = re.sub(r"[\[\]\(\)]", " ", line)
    line = line.replace("\\#", "#")
    line = re.sub(r"\s+", " ", line).strip("`*_ ").strip()
    return line


def _extract_douyin_share_text_body(source_text: str) -> str:
    raw = _repair_mojibake(str(source_text or "")).replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return ""

    line = re.sub(r"\s+", " ", raw).strip()
    line = DOUYIN_SHARE_PREFIX_RE.sub("", line)
    line = DOUYIN_SHARE_OPENING_RE.sub("", line)
    line = re.sub(r"https?://\S+", " ", line, flags=re.IGNORECASE)
    for pattern in DOUYIN_SHARE_TRAILING_PATTERNS:
        line = pattern.sub("", line)
    line = re.sub(r"\s+", " ", line).strip(" \t\r\n:：;；，,")

    if not line:
        return ""
    if not re.search(r"[\u4e00-\u9fff]", line):
        return ""
    return line


def _score_douyin_body_quality(text: str) -> int:
    value = str(text or "").strip()
    if not value:
        return -999
    score = 0
    length = len(value)
    score += min(12, length // 20)
    score += min(6, len(re.findall(r"[。！？!?；;]", value)))
    score += min(4, value.count("#"))
    if "..." in value or "…" in value:
        score -= 2
    if "复制此链接" in value or "打开抖音搜索" in value or "看看【" in value:
        score -= 4
    return score


def _should_prefer_source_text_for_douyin(candidate: str, current: str) -> bool:
    cand = str(candidate or "").strip()
    curr = str(current or "").strip()
    if not cand:
        return False
    if not curr:
        return True
    cand_score = _score_douyin_body_quality(cand)
    curr_score = _score_douyin_body_quality(curr)
    if cand_score >= curr_score + 2:
        return True
    if len(cand) >= len(curr) + 40:
        return True
    return False


def _is_douyin_stop_line(raw: str) -> bool:
    line = str(raw or "").strip()
    if not line:
        return False
    if line.startswith(("## 推荐视频", "## 全部评论")):
        return True
    if line in {"大家都在搜", "举报", "Log in to Douyin", "Please login before leaving comments", "Web-Cross-Storage"}:
        return True
    return False


SUMMARY_HINT_REGEXES = (
    re.compile(r"(?:作者|博主|视频|这段文案).{0,8}(?:强调|指出|认为|提到|总结|说明|分享)", re.IGNORECASE),
    re.compile(r"^\s*(?:通过分享|这段内容主要在说|核心观点是|本质是|主要讲了)", re.IGNORECASE),
    re.compile(r"(?:告诉我们|启发我们|提醒我们|建议我们)", re.IGNORECASE),
)


def _count_sentences(text: str) -> int:
    value = str(text or "").strip()
    if not value:
        return 0
    count = len(re.findall(r"[。！？!?；;]", value))
    if count > 0:
        return count
    # Fallback for single-line text without punctuation.
    lines = [ln.strip() for ln in value.splitlines() if ln.strip()]
    return len(lines)


def _clean_douyin_text_for_output(text: str) -> str:
    value = _repair_mojibake(str(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    if not value.strip():
        return ""

    # Remove share wrappers/noise, keep正文分段换行.
    lines = value.split("\n")
    cleaned_lines: list[str] = []
    for idx, raw in enumerate(lines):
        line = str(raw or "").strip()
        if not line:
            cleaned_lines.append("")
            continue
        if idx == 0:
            line = DOUYIN_SHARE_PREFIX_RE.sub("", line)
            line = DOUYIN_SHARE_OPENING_RE.sub("", line)
        line = re.sub(r"https?://\S+", " ", line, flags=re.IGNORECASE)
        line = re.sub(r"(?:^|\s)@[A-Za-z0-9_.\-\u4e00-\u9fff]+[:：]?", " ", line)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            cleaned_lines.append("")
            continue
        cleaned_lines.append(line)

    value = "\n".join(cleaned_lines)
    for pattern in DOUYIN_SHARE_TRAILING_PATTERNS:
        value = pattern.sub("", value)
    value = re.sub(r"(?:复制此链接|打开Dou音搜索|打开抖音搜索|直接观看视频)[^\n]*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()

    # Keep hashtags in one line at the end for readability.
    if "#" in value:
        hashtag_tokens = re.findall(r"(#[^\s#]+)", value)
        if hashtag_tokens:
            body_wo_tags = re.sub(r"(?:\s*#[^\s#]+)+\s*$", "", value).strip()
            value = body_wo_tags
            if hashtag_tokens:
                value = f"{value}\n{' '.join(hashtag_tokens)}".strip()

    # If source is a single long line, split by sentence punctuation to match
    # the common Douyin detail-panel layout (full-text mode readability).
    lines = [ln.strip() for ln in value.splitlines() if ln.strip()]
    if len(lines) <= 2:
        tag_line = ""
        body_line = value.strip()
        if lines:
            if len(lines) == 1:
                body_line = lines[0]
            elif lines[-1].startswith("#"):
                tag_line = lines[-1]
                body_line = lines[0]
            else:
                body_line = "\n".join(lines)

        if body_line:
            sentence_parts = [
                part.strip()
                for part in re.split(r"(?<=[。！？；])\s*", body_line)
                if part and part.strip()
            ]
            if len(sentence_parts) >= 2:
                body_line = "\n".join(sentence_parts)

        value = body_line.strip()
        if tag_line:
            value = f"{value}\n{tag_line}".strip()

    return value


def _is_summary_candidate(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    lower = value.lower()
    if "作者强调" in value or "通过分享" in value:
        return True
    for rule in SUMMARY_HINT_REGEXES:
        if rule.search(value):
            return True
    # Abstractive summary style: highly abstract words + too little detail.
    abstract_hits = sum(
        1
        for token in ("本质", "核心", "总结", "观点", "启发", "提醒", "强调", "说明")
        if token in value
    )
    sentence_count = _count_sentences(value)
    if abstract_hits >= 2 and sentence_count <= 3 and len(value) < 220:
        return True
    # Avoid accepting heavily truncated headline-like snippets as full正文.
    if ("..." in value or "…" in value) and sentence_count <= 2 and len(value) < 180:
        return True
    if "page=app_code_link" in lower:
        return True
    return False


def _score_fulltext_quality(text: str, *, title_keywords: list[str]) -> int:
    value = str(text or "").strip()
    if not value:
        return -999
    chars = len(value)
    sentence_count = _count_sentences(value)
    line_count = len([ln for ln in value.splitlines() if ln.strip()])
    keyword_hits = sum(1 for kw in title_keywords if kw and kw in value)

    score = 0
    score += min(12, chars // 40)
    score += min(10, sentence_count * 2)
    score += min(4, max(0, line_count - 1))
    score += min(8, keyword_hits * 2)
    if "#" in value:
        score += min(3, value.count("#"))
    if _is_summary_candidate(value):
        score -= 12
    if "复制此链接" in value or "打开抖音搜索" in value:
        score -= 8
    return score


def _evaluate_douyin_candidate(
    *,
    source: str,
    raw_text: str,
    title_keywords: list[str],
    min_chars_for_url: int,
) -> DouyinCandidateEval:
    cleaned = _clean_douyin_text_for_output(raw_text)
    chars = len(cleaned)
    sentence_count = _count_sentences(cleaned)
    summary_detected = _is_summary_candidate(cleaned) if DEFAULT_DOUYIN_SUMMARY_BLOCK else False
    score = _score_fulltext_quality(cleaned, title_keywords=title_keywords)

    reject_reason = ""
    if not cleaned:
        reject_reason = "no_full_text"
    elif summary_detected:
        reject_reason = "summary_detected"
    elif chars < min_chars_for_url:
        reject_reason = f"content_too_short:{chars}<{min_chars_for_url}"
    elif sentence_count < DEFAULT_DOUYIN_MIN_SENTENCES:
        reject_reason = f"sentences_too_few:{sentence_count}<{DEFAULT_DOUYIN_MIN_SENTENCES}"

    return DouyinCandidateEval(
        source=source,
        text=cleaned,
        score=score,
        chars=chars,
        sentence_count=sentence_count,
        summary_detected=summary_detected,
        reject_reason=reject_reason,
    )


def _extract_douyin_title_keywords(title: str) -> list[str]:
    title_norm = _normalize_title(title)
    if not title_norm:
        return []
    parts = re.split(r"[，。！？；：、,.!?;:\s\-\_/|【】\[\]（）()]+", title_norm)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        token = part.strip()
        if len(token) < 2:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token[:20])
    return out


def _is_douyin_rich_caption_candidate(line: str, *, title_keywords: list[str]) -> bool:
    text = _normalize_douyin_candidate_line(line)
    if not text:
        return False
    if len(text) < 40:
        return False
    if not re.search(r"[\u4e00-\u9fff]", text):
        return False
    if re.search(r"https?://", text, flags=re.IGNORECASE):
        return False
    if any(term in text for term in DOUYIN_RECOMMEND_NOISE_TERMS):
        return False
    if any(kw in text for kw in title_keywords):
        return True
    if "#" in text:
        return True
    return False


def _is_valid_douyin_content_line(line: str, *, title_norm: str = "") -> bool:
    text = _normalize_douyin_candidate_line(line)
    if not text:
        return False
    if title_norm and text == title_norm and len(text) < 60:
        return False
    if len(text) < 10:
        return False
    if not re.search(r"[\u4e00-\u9fff]", text):
        return False
    rich_candidate = (
        len(text) >= 40
        and "#" in text
        and not re.search(r"https?://", text, flags=re.IGNORECASE)
        and not any(term in text for term in DOUYIN_RECOMMEND_NOISE_TERMS)
    )
    if (_looks_like_noise_line(text) and not rich_candidate) or _looks_like_douyin_noise_line(text):
        return False
    return True


def _count_douyin_content_candidates(text: str, *, title_norm: str = "") -> int:
    return sum(1 for raw in str(text or "").split("\n") if _is_valid_douyin_content_line(raw, title_norm=title_norm))


def _normalize_douyin_title(title: str) -> str:
    text = _normalize_title(title)
    if not text:
        return ""
    if len(text) <= DEFAULT_DOUYIN_TITLE_MAX_CHARS:
        return text
    first_sentence = re.split(r"[。！？!?；;]", text, maxsplit=1)[0].strip()
    if first_sentence and len(first_sentence) >= 8:
        return first_sentence[:DEFAULT_DOUYIN_TITLE_MAX_CHARS].strip()
    return text[:DEFAULT_DOUYIN_TITLE_MAX_CHARS].strip()


def _slice_douyin_main_section(markdown: str, *, request_url: str = "", title: str = "") -> tuple[str, bool]:
    _ = request_url
    text = _repair_mojibake(markdown).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if not lines:
        return text, False

    start_idx: int | None = None
    anchor_idx: int | None = None
    for i, raw in enumerate(lines):
        if "章节要点" in raw:
            anchor_idx = i
            break

    title_keywords = _extract_douyin_title_keywords(title)
    heading_candidates: list[tuple[int, int]] = []
    for i, raw in enumerate(lines):
        if not re.match(r"^\s*#\s+\S", raw):
            continue
        heading = _normalize_douyin_candidate_line(raw)
        if not heading:
            continue
        score = 0
        if re.search(r"[\u4e00-\u9fff]", heading):
            score += 3
        score += min(200, len(heading))
        if len(heading) >= 30:
            score += 40
        if "#" in heading:
            score += 10
        if any(kw in heading for kw in title_keywords):
            score += 80
        if heading in {"引言", "结语", "总结", "章节要点"}:
            score -= 120
        heading_candidates.append((score, i))

    if heading_candidates:
        heading_candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        start_idx = heading_candidates[0][1]

    if start_idx is None:
        seek_from = (anchor_idx + 1) if anchor_idx is not None else 0
        for i in range(seek_from, len(lines)):
            line = _normalize_douyin_candidate_line(lines[i])
            if not line:
                continue
            if _looks_like_noise_line(line) or _looks_like_douyin_noise_line(line):
                continue
            if re.search(r"[\u4e00-\u9fff]", line) and len(line) >= 10:
                start_idx = i
                break

    if start_idx is None:
        start_idx = 0

    end_idx = len(lines)
    for i in range(start_idx, len(lines)):
        raw = str(lines[i] or "")
        if _is_douyin_stop_line(raw):
            end_idx = i
            break

    section = "\n".join(lines[start_idx:end_idx]).strip()
    title_norm = _normalize_title(_normalize_douyin_candidate_line(lines[start_idx])) if start_idx < len(lines) else ""
    heading_candidate_count = _count_douyin_content_candidates(section, title_norm=title_norm)

    # Some Douyin pages contain the useful摘要 in "章节要点" before the first "# 标题".
    if heading_candidate_count == 0 and anchor_idx is not None and anchor_idx < start_idx:
        summary_start = anchor_idx + 1
        summary_end = start_idx
        summary_section = "\n".join(lines[summary_start:summary_end]).strip()
        if _count_douyin_content_candidates(summary_section, title_norm=title_norm) > 0:
            return summary_section, True

    if not section:
        return text.strip(), False
    return section, True


def _score_douyin_caption_candidate(line: str, *, title_keywords: list[str]) -> int:
    score = 0
    text = str(line or "").strip()
    if not text:
        return -999
    length = len(text)

    if length < 20:
        score -= 3
    elif length <= 520:
        score += min(8, length // 60)
    elif length <= 900:
        score += 3
    else:
        score -= 2

    if re.search(r"[，。！？；,.!?]", text):
        score += 1
    score += min(5, len(re.findall(r"[。！？!?；;]", text)))
    if "#" in text:
        score += 1
    if re.search(r"https?://|www\.", text, flags=re.IGNORECASE):
        score -= 5
    if not re.search(r"[\u4e00-\u9fff]", text):
        score -= 2

    keyword_hits = 0
    for kw in title_keywords:
        if kw and kw in text:
            keyword_hits += 1
    if keyword_hits:
        score += min(6, keyword_hits * 2)

    if any(term in text for term in DOUYIN_RECOMMEND_NOISE_TERMS):
        score -= 4
    if _looks_like_douyin_noise_line(text):
        score -= 3
    if _is_douyin_rich_caption_candidate(text, title_keywords=title_keywords):
        score += 4
    return score


def _extract_douyin_caption(
    markdown: str,
    *,
    title: str = "",
    request_url: str = "",
    already_sliced: bool = False,
) -> DouyinCaptionPick:
    if already_sliced:
        section = _repair_mojibake(markdown).replace("\r\n", "\n").replace("\r", "\n").strip()
        used_main_section = True
    else:
        section, used_main_section = _slice_douyin_main_section(
            markdown,
            request_url=request_url,
            title=title,
        )

    text = _strip_code_blocks(section)
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)

    title_norm = _normalize_title(title)
    title_keywords = _extract_douyin_title_keywords(title_norm)

    candidates: list[tuple[str, int]] = []
    for raw in text.split("\n"):
        line = _normalize_douyin_candidate_line(raw)
        if not line:
            continue
        if _is_douyin_stop_line(raw):
            break
        rich_candidate = _is_douyin_rich_caption_candidate(line, title_keywords=title_keywords)
        if (_looks_like_noise_line(line) and not rich_candidate) or _looks_like_douyin_noise_line(line):
            continue
        if title_norm and line == title_norm and len(line) < 60:
            continue
        if len(line) < 8:
            continue
        if not re.search(r"[\u4e00-\u9fff]", line):
            continue
        score = _score_douyin_caption_candidate(line, title_keywords=title_keywords)
        candidates.append((line, score))

    if not candidates:
        return DouyinCaptionPick(text="", score=0, used_main_section=used_main_section)

    best_line, best_score = max(candidates, key=lambda item: (item[1], len(item[0])))
    return DouyinCaptionPick(text=best_line.strip(), score=int(best_score), used_main_section=used_main_section)


def _extract_douyin_publish_time(markdown: str) -> str:
    text = _repair_mojibake(markdown)
    m = re.search(r"发布时间[:：]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}(?:\s+[0-9]{2}:[0-9]{2})?)", text)
    return str(m.group(1) if m else "").strip()


def _provider_source_text_extract(source_text: str, *, url: str, title: str = "") -> tuple[str, str]:
    raw = _repair_mojibake(str(source_text or "")).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return "", "source_text_empty"

    is_douyin = _is_douyin_url(url)
    share_body = _clean_douyin_text_for_output(raw) if is_douyin else ""

    cleaned = raw
    cleaned = re.sub(r"https?://\S+", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?:^|\s)@[A-Za-z0-9_.\-\u4e00-\u9fff]+[:：]?", " ", cleaned)
    if is_douyin:
        cleaned = _clean_douyin_text_for_output(cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not cleaned:
        return "", "source_text_no_body"

    body = _markdown_to_plain_text(cleaned, title=title, source_url=url if is_douyin else "")
    if is_douyin and share_body:
        body = share_body if _should_prefer_source_text_for_douyin(share_body, body) else body

    body = re.sub(r"\bpage=app_code_link\b", " ", body, flags=re.IGNORECASE)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if not body:
        return "", "source_text_no_body"
    return body, ""


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
    title_keywords = _extract_douyin_title_keywords(title_norm) if is_douyin else []
    for raw in text.split("\n"):
        if is_douyin and _is_douyin_stop_line(raw):
            break
        line = raw.strip()
        if not line:
            continue

        if line.startswith("#"):
            line = line.lstrip("#").strip()
        line = re.sub(r"^[>*\-\s]+", "", line)
        line = re.sub(r"^\d+[\.、]\s*", "", line)
        line = line.replace("\\#", "#")
        line = re.sub(r"\s+", " ", line).strip()
        line = line.strip("`*_ ").strip()

        rich_candidate = _is_douyin_rich_caption_candidate(line, title_keywords=title_keywords) if is_douyin else False
        if _looks_like_noise_line(line) and not rich_candidate:
            continue
        if is_douyin and _looks_like_douyin_noise_line(line):
            continue
        if title_norm and line == title_norm and len(line) < 60:
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
    # Prefer first-hop redirect location if it already reveals a stable target (e.g. /video/<id>).
    try:
        head_resp = requests.get(raw, headers=headers, timeout=12, allow_redirects=False)
        location = str(head_resp.headers.get("location") or "").strip()
        if location:
            try:
                from urllib.parse import urljoin

                first_hop = str(urljoin(raw, location) or "").strip()
            except Exception:
                first_hop = location
            if first_hop.startswith("http") and re.search(r"/video/\d{8,32}", first_hop, flags=re.IGNORECASE):
                return first_hop
    except Exception:
        pass
    try:
        resp = requests.get(raw, headers=headers, timeout=12, allow_redirects=True)
        final_url = str(resp.url or "").strip()
        if final_url.startswith("http"):
            # Anti-bot/landing fallback often collapses to douyin homepage.
            # In that case keep original short URL to avoid losing match key.
            try:
                p = urlparse(final_url)
                final_host = str(p.netloc or "").lower()
                final_path = str(p.path or "").strip("/")
                if "douyin.com" in final_host and final_path in {"", "home"}:
                    return raw
            except Exception:
                pass
            return final_url
    except Exception:
        pass
    return raw


def _build_douyin_dedup_key(
    *,
    url: str,
    source_text: str = "",
    video_id_hint: str = "",
    canonical_url_hint: str = "",
) -> str:
    mode = str(DEFAULT_DOUYIN_DEDUP_KEY_MODE or "").strip().lower()
    video_id = (
        str(video_id_hint or "").strip()
        or _extract_douyin_video_id(url)
        or _extract_douyin_video_id(source_text)
        or _extract_douyin_video_id(canonical_url_hint)
    )
    if video_id:
        return f"dy-{video_id}"

    if mode == "video_only":
        return ""

    canonical = _normalize_match_url(canonical_url_hint) or _normalize_match_url(_expand_short_url(url)) or _normalize_match_url(url)
    if not canonical:
        canonical = str(url or "").strip()
    if not canonical:
        return ""
    return f"url-{hashlib.sha1(canonical.encode('utf-8')).hexdigest()[:12]}"


def _extract_markdown_section(text: str, heading: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not value.strip():
        return ""
    pattern = re.compile(
        rf"(?ms)^\s*##\s*{re.escape(heading)}\s*$\n?(.*?)(?=^\s*##\s*\S+\s*$|\Z)"
    )
    match = pattern.search(value)
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def _split_douyin_doc_sections(
    *,
    body_text: str,
    summary_text: str = "",
    keypoints_text: str = "",
) -> tuple[str, str, str]:
    value = str(body_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    summary = str(summary_text or "").strip()
    keypoints = str(keypoints_text or "").strip()
    main_body = value

    heading_summary = _extract_markdown_section(value, "摘要")
    heading_keypoints = _extract_markdown_section(value, "关键点")
    heading_body = _extract_markdown_section(value, "正文")

    if heading_summary:
        summary = heading_summary
    if heading_keypoints:
        keypoints = heading_keypoints
    if heading_body:
        main_body = heading_body
    elif heading_summary or heading_keypoints:
        main_body = re.sub(r"(?ms)^\s*##\s*(摘要|关键点)\s*$\n?.*?(?=^\s*##\s*\S+\s*$|\Z)", "", value).strip()

    if not summary:
        # Fallback: first meaningful sentence as summary.
        first_line = ""
        for raw_line in main_body.splitlines():
            line = str(raw_line or "").strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            if line.startswith("-"):
                line = line.lstrip("- ").strip()
            if not line:
                continue
            first_line = re.split(r"[。！？!?]", line, maxsplit=1)[0].strip()
            break
        summary = first_line[:100] if first_line else ""

    if keypoints:
        normalized_lines: list[str] = []
        for raw_line in keypoints.splitlines():
            line = str(raw_line or "").strip()
            if not line:
                continue
            if line.startswith(("-", "•", "·")):
                normalized_lines.append(line)
            else:
                normalized_lines.append(f"- {line}")
        keypoints = "\n".join(normalized_lines).strip()

    return summary.strip(), keypoints.strip(), main_body.strip()


def _find_existing_doc_by_dedup_key(output_dir: Path, dedup_key: str) -> Path | None:
    key = str(dedup_key or "").strip()
    if not key or not output_dir.exists():
        return None
    marker = f"- 去重键：{key}"
    for candidate in sorted(output_dir.glob("*.md")):
        try:
            content = candidate.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if marker in content:
            return candidate
    return None


def _write_body_doc(
    *,
    benchmark_root: Path,
    source_time: str,
    url: str,
    title: str,
    body_text: str,
    summary_text: str = "",
    keypoints_text: str = "",
    dedup_key: str = "",
) -> Path:
    date_str = _normalize_date(source_time) or _today()
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]

    def _derive_local_title_base(raw_title: str, raw_body: str) -> str:
        base = str(raw_title or "").strip()
        if base and base != "-":
            return base
        for raw_line in str(raw_body or "").splitlines():
            line = str(raw_line or "").strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            if line.startswith(("摘要", "关键点", "完整文案")):
                continue
            if line.startswith("-"):
                line = line.lstrip("- ").strip()
            if not line:
                continue
            line = re.split(r"[。！？!?]", line, maxsplit=1)[0].strip()
            if line:
                return line[:40]
        return "未命名文案"

    title_base = _derive_local_title_base(title, body_text)
    display_title = f"{title_base}-{date_str}"
    slug = _safe_slug(title_base or url, 60)

    output_dir = benchmark_root / "提取正文" / date_str
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{slug}-{date_str}.md"
    current_url_norm = _normalize_match_url(url)
    current_video_id = _extract_douyin_video_id(url)

    existing_by_key = _find_existing_doc_by_dedup_key(output_dir, dedup_key)
    if existing_by_key is not None:
        output_path = existing_by_key

    # Keep required naming as 标题+提取日期; add stable suffix only when collision is for another link.
    if output_path.exists() and existing_by_key is None:
        try:
            existing_text = output_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            existing_text = ""
        same_doc = f"- 原文链接：{url}" in existing_text
        if not same_doc:
            matched = re.search(r"^- 原文链接：(.+)$", existing_text, flags=re.MULTILINE)
            existing_url = str(matched.group(1) if matched else "").strip()
            existing_url_norm = _normalize_match_url(existing_url)
            existing_video_id = _extract_douyin_video_id(existing_url)
            same_doc = bool(
                (current_url_norm and existing_url_norm and current_url_norm == existing_url_norm)
                or (current_video_id and existing_video_id and current_video_id == existing_video_id)
            )
        if not same_doc:
            stable = str(dedup_key or "").strip() or digest
            output_path = output_dir / f"{slug}-{date_str}-{stable[-8:]}.md"

    summary, keypoints, 正文 = _split_douyin_doc_sections(
        body_text=body_text,
        summary_text=summary_text,
        keypoints_text=keypoints_text,
    )

    lines = [
        "# 对标文案提取",
        "",
        f"- 标题：{display_title}",
        f"- 原文链接：{url}",
    ]
    if dedup_key:
        lines.append(f"- 去重键：{dedup_key}")

    if DEFAULT_DOUYIN_WRITE_SUMMARY and summary:
        lines.extend(["", "## 摘要", "", summary])
    if DEFAULT_DOUYIN_WRITE_KEYPOINTS and keypoints:
        lines.extend(["", "## 关键点", "", keypoints])

    lines.extend(
        [
            "",
            "## 正文",
            "",
            正文 or "（正文提取为空，建议打开抓取原文件人工复核）",
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
        lines.append(f"  - summary_detected：{'true' if item.summary_detected else 'false'}\n")
        if item.text_source:
            lines.append(f"  - text_source：{item.text_source}\n")
        if item.douyin_pipeline_mode:
            lines.append(f"  - douyin_pipeline_mode：{item.douyin_pipeline_mode}\n")
        if item.douyin_source_used:
            lines.append(f"  - douyin_source_used：{item.douyin_source_used}\n")
        if item.douyin_dedup_key:
            lines.append(f"  - douyin_dedup_key：{item.douyin_dedup_key}\n")
        if item.reject_reason:
            lines.append(f"  - reject_reason：{item.reject_reason}\n")
        if item.douyin_main_section_used:
            lines.append(f"  - douyin_main_section_used：{str(item.douyin_main_section_used).lower()}\n")
        if item.douyin_caption_score:
            lines.append(f"  - douyin_caption_score：{item.douyin_caption_score}\n")
        if item.douyin_caption_selected:
            lines.append(f"  - douyin_caption_selected：{item.douyin_caption_selected}\n")
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
    _refresh_runtime_config()

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
        is_douyin = _is_douyin_url(url)
        douyin_pipeline_mode = str(DEFAULT_DOUYIN_PIPELINE_MODE or "").strip().lower() if is_douyin else ""
        if is_douyin and douyin_pipeline_mode not in {"asr_primary", "bitable_primary", "bitable_only"}:
            douyin_pipeline_mode = "asr_primary"
        douyin_bitable_only = bool(is_douyin and douyin_pipeline_mode == "bitable_only")
        min_chars_for_url = _effective_min_chars(url=url, min_chars=min_chars)
        route_status = "failed"
        provider = "none"
        quality_reason = ""
        if douyin_bitable_only:
            ok, result, combined = False, {}, ""
        else:
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
        douyin_main_section_used = False
        douyin_caption_score = 0
        douyin_caption_selected = ""
        summary_detected = False
        text_source = ""
        reject_reason = ""
        bitable_record_id = ""
        bitable_error = ""
        bitable_lookup_reason = ""
        asr_error = ""
        asr_payload: dict[str, Any] = {}
        asr_summary = ""
        asr_keypoints = ""
        douyin_source_used = ""
        douyin_dedup_key = ""
        bitable_read_first = bool(DEFAULT_DOUYIN_BITABLE_READ_FIRST or douyin_bitable_only)
        title_keywords = _extract_douyin_title_keywords(title) if is_douyin else []
        douyin_candidates: list[tuple[str, str]] = []

        if is_douyin and _bitable_enabled() and bitable_read_first:
            try:
                bitable_text, bitable_record_id, bitable_lookup_err = _bitable_search_douyin_text(url, source_text=source_text)
                if bitable_text:
                    douyin_candidates.append(("bitable", bitable_text))
                if bitable_lookup_err:
                    bitable_lookup_reason = bitable_lookup_err
                if bitable_lookup_err and bitable_lookup_err not in {"bitable_no_record", "bitable_record_without_text"}:
                    bitable_error = bitable_lookup_err
            except Exception as exc:
                bitable_error = f"bitable_read_failed:{exc}"

        if is_douyin and not douyin_bitable_only and DEFAULT_DOUYIN_ASR_ENABLED and extract_douyin_asr_dict is not None:
            try:
                asr_payload = extract_douyin_asr_dict(
                    source_text or url,
                    timeout_sec=max(30, int(DEFAULT_DOUYIN_ASR_TIMEOUT_SEC)),
                )
                asr_transcript = _clean_douyin_text_for_output(str(asr_payload.get("transcript") or ""))
                asr_summary = _clean_douyin_text_for_output(str(asr_payload.get("desc") or asr_payload.get("title") or ""))
                if asr_transcript:
                    douyin_candidates.append(("asr", asr_transcript))
                if not title and asr_summary:
                    title = _normalize_douyin_title(asr_summary)
                    title_keywords = _extract_douyin_title_keywords(title)
            except Exception as exc:
                asr_error = f"asr_extract_failed:{exc}"

        if ok and md_file and Path(md_file).exists():
            markdown = _repair_mojibake(Path(md_file).read_text(encoding="utf-8", errors="ignore"))
            meta, body_markdown = _extract_front_matter(markdown)
            title = _normalize_title(title or str(meta.get("title") or ""))
            if is_douyin:
                title = _normalize_douyin_title(title)
            published_date = _pick_published_date(meta, source_time)
            normalized_body_markdown = body_markdown
            if is_douyin:
                normalized_body_markdown, douyin_main_section_used = _slice_douyin_main_section(
                    body_markdown,
                    request_url=url,
                    title=title,
                )
            body_plain = _markdown_to_plain_text(normalized_body_markdown, title=title, source_url=url)
            if is_douyin:
                title_keywords = _extract_douyin_title_keywords(title)
                if body_plain.strip() and not douyin_bitable_only:
                    douyin_candidates.append(("url_reader_main", body_plain))
            if is_douyin:
                caption_pick = _extract_douyin_caption(
                    normalized_body_markdown,
                    title=title,
                    request_url=url,
                    already_sliced=True,
                )
                douyin_main_section_used = bool(douyin_main_section_used or caption_pick.used_main_section)
                douyin_caption_score = int(caption_pick.score)
                douyin_caption_selected = str(caption_pick.text or "")[:160]
                caption = str(caption_pick.text or "").strip()
                if caption and not douyin_bitable_only:
                    douyin_candidates.append(("url_reader_caption", caption))
                should_override_with_caption = bool(
                    caption
                    and len(caption) >= DEFAULT_DOUYIN_CAPTION_MIN_LEN
                    and (
                        caption_pick.score >= DEFAULT_DOUYIN_CAPTION_MIN_SCORE
                        or not body_plain
                    )
                    and (
                        not body_plain
                        or len(body_plain) < min_chars_for_url
                        or any(term in body_plain for term in DOUYIN_RECOMMEND_NOISE_TERMS)
                    )
                )
                if should_override_with_caption and not DEFAULT_DOUYIN_STRICT_FULL_TEXT:
                    publish_time = _extract_douyin_publish_time(body_markdown)
                    body_plain = caption if not publish_time else f"{caption}\n发布时间：{publish_time}"
            body_chars = len(body_plain)
            provider = "url_reader"
        elif ok:
            ok = False
            error = "抓取成功但未返回正文文件"

        if (not ok or (not is_test_url and body_chars < min_chars_for_url)) and is_douyin and not douyin_bitable_only:
            f2_title, f2_body, f2_published, f2_err = _provider_f2_extract(url)
            if f2_body:
                ok = True
                provider = "f2"
                title = _normalize_title(f2_title or title)
                if is_douyin:
                    title = _normalize_douyin_title(title)
                    title_keywords = _extract_douyin_title_keywords(title)
                body_plain = _markdown_to_plain_text(f2_body, title=title, source_url=url)
                if is_douyin and body_plain.strip() and not douyin_bitable_only:
                    douyin_candidates.append(("f2", body_plain))
                body_chars = len(body_plain)
                if f2_published:
                    published_date = _normalize_date(f2_published) or published_date
                error = ""
            elif f2_err and not error:
                error = f2_err

        if (not ok or (not is_test_url and body_chars < min_chars_for_url)) and not douyin_bitable_only:
            yt_title, yt_body, yt_published, yt_err = _provider_ytdlp_extract(url)
            if yt_body:
                ok = True
                provider = "yt_dlp"
                title = _normalize_title(yt_title or title)
                if is_douyin:
                    title = _normalize_douyin_title(title)
                    title_keywords = _extract_douyin_title_keywords(title)
                body_plain = _markdown_to_plain_text(yt_body, title=title, source_url=url)
                if is_douyin and body_plain.strip() and not douyin_bitable_only:
                    douyin_candidates.append(("yt_dlp", body_plain))
                body_chars = len(body_plain)
                if yt_published:
                    published_date = _normalize_date(yt_published) or published_date
                error = ""
            elif yt_err and not error:
                error = yt_err

        if not ok and not douyin_bitable_only:
            fallback_text, fallback_err = _fallback_fetch_plain_text(url)
            if fallback_text:
                ok = True
                provider = "http_fallback"
                error = ""
                if not title:
                    path_name = Path(urlparse(url).path).name
                    title = _normalize_title(path_name.rsplit(".", 1)[0] if "." in path_name else path_name) or "链接正文"
                if is_douyin:
                    title = _normalize_douyin_title(title)
                    title_keywords = _extract_douyin_title_keywords(title)
                published_date = _normalize_date(source_time) or _today()
                body_plain = _markdown_to_plain_text(fallback_text, title=title, source_url=url)
                if is_douyin and body_plain.strip() and not douyin_bitable_only:
                    douyin_candidates.append(("http_fallback", body_plain))
                body_chars = len(body_plain)
            elif fallback_err and not error:
                error = fallback_err

        if source_text.strip() and not douyin_bitable_only:
            src_body, src_err = _provider_source_text_extract(source_text, url=url, title=title)
            if src_body:
                if is_douyin and not douyin_bitable_only:
                    douyin_candidates.append(("share_text", src_body))
                prefer_source_text = (
                    not ok
                    or (not is_test_url and body_chars < min_chars_for_url)
                    or (
                        is_douyin
                        and DEFAULT_DOUYIN_PREFER_SOURCE_TEXT
                        and _should_prefer_source_text_for_douyin(src_body, body_plain)
                    )
                )
                if prefer_source_text:
                    ok = True
                    provider = "source_text" if not provider or provider == "none" else f"{provider}+source_text"
                    error = ""
                    body_plain = src_body
                    body_chars = len(body_plain)
            elif src_err and not error and (not ok or (not is_test_url and body_chars < min_chars_for_url)):
                error = src_err

        if is_douyin and _bitable_enabled() and not bitable_read_first:
            try:
                bitable_text, bitable_record_id, bitable_lookup_err = _bitable_search_douyin_text(url, source_text=source_text)
                if bitable_text:
                    douyin_candidates.append(("bitable", bitable_text))
                if bitable_lookup_err and not bitable_lookup_reason:
                    bitable_lookup_reason = bitable_lookup_err
                if bitable_lookup_err and bitable_lookup_err not in {"bitable_no_record", "bitable_record_without_text"} and not bitable_error:
                    bitable_error = bitable_lookup_err
            except Exception as exc:
                if not bitable_error:
                    bitable_error = f"bitable_read_failed:{exc}"

        if is_douyin and douyin_candidates:
            seen_text: set[str] = set()
            evaluated: list[DouyinCandidateEval] = []
            for src, raw_text in douyin_candidates:
                key = str(raw_text or "").strip()
                if not key or key in seen_text:
                    continue
                seen_text.add(key)
                evaluated.append(
                    _evaluate_douyin_candidate(
                        source=src,
                        raw_text=raw_text,
                        title_keywords=title_keywords,
                        min_chars_for_url=min_chars_for_url,
                    )
                )
            if douyin_bitable_only:
                evaluated = [item for item in evaluated if item.source in {"bitable", "bitable_text"}]
            if evaluated:
                ok = True
                passed = [item for item in evaluated if not item.reject_reason]
                ranked = sorted(
                    evaluated,
                    key=lambda item: (_douyin_source_rank(item.source), -item.score, -item.chars, -item.sentence_count),
                )
                best = ranked[0]
                if passed:
                    best = sorted(
                        passed,
                        key=lambda item: (_douyin_source_rank(item.source), -item.score, -item.chars, -item.sentence_count),
                    )[0]
                    ok = True
                    body_plain = best.text
                    body_chars = best.chars
                    provider = best.source
                    text_source = best.source
                    douyin_source_used = best.source
                    summary_detected = bool(best.summary_detected)
                    reject_reason = ""
                elif DEFAULT_DOUYIN_STRICT_FULL_TEXT:
                    # Strict mode: reject summary/short candidates as failed正文.
                    body_plain = ""
                    body_chars = 0
                    text_source = best.source
                    douyin_source_used = best.source
                    provider = best.source or provider
                    summary_detected = bool(best.summary_detected)
                    reject_reason = best.reject_reason or "no_full_text"
                    if not error:
                        error = reject_reason
                else:
                    body_plain = best.text
                    body_chars = best.chars
                    provider = best.source
                    text_source = best.source
                    douyin_source_used = best.source
                    summary_detected = bool(best.summary_detected)
                    reject_reason = best.reject_reason
            elif DEFAULT_DOUYIN_STRICT_FULL_TEXT:
                body_plain = ""
                body_chars = 0
                reject_reason = bitable_lookup_reason or ("not_from_bitable" if douyin_bitable_only else "no_full_text")
                if not error:
                    error = reject_reason
        elif is_douyin and douyin_bitable_only:
            body_plain = ""
            body_chars = 0
            reject_reason = bitable_lookup_reason or "bitable_no_record"
            if not error:
                error = reject_reason

        route_status = "success" if ok else "failed"

        content_status = "failed"
        if is_test_url:
            content_status = "skipped_test"
            quality_reason = "test_url_skip"
        elif is_douyin and DEFAULT_DOUYIN_STRICT_FULL_TEXT and reject_reason:
            content_status = "failed"
            quality_reason = reject_reason
        elif not ok:
            content_status = "failed"
            quality_reason = "extract_failed"
        elif body_chars < min_chars_for_url:
            content_status = "failed"
            quality_reason = f"content_too_short:{body_chars}<{min_chars_for_url}"
        else:
            content_status = "success"
            quality_reason = ""

        if is_douyin and content_status == "success":
            allowed_sources = {"bitable", "bitable_text"}
            if douyin_pipeline_mode in {"asr_primary", "bitable_primary"}:
                allowed_sources = {"asr", "bitable", "bitable_text"}
            if str(text_source or "").strip().lower() not in allowed_sources:
                content_status = "failed"
                quality_reason = "not_from_pipeline_source"
                reject_reason = "not_from_pipeline_source"

        if is_douyin and not quality_reason and summary_detected and DEFAULT_DOUYIN_SUMMARY_BLOCK:
            content_status = "failed"
            quality_reason = "summary_detected"

        if not reject_reason and quality_reason in {"summary_detected", "no_full_text"}:
            reject_reason = quality_reason

        if bitable_error and not error:
            error = bitable_error
        if asr_error and not error and douyin_pipeline_mode == "asr_primary":
            error = asr_error

        if is_douyin and apply_mode:
            sync_error = _bitable_write_back(
                url=url,
                title=title,
                body_text=body_plain,
                content_status=content_status,
                quality_reason=quality_reason,
                source=text_source or provider or "",
                record_id_hint=bitable_record_id,
            )
            if sync_error:
                error = f"{error} | {sync_error}".strip(" |") if error else sync_error

        write_summary_text = ""
        write_keypoints_text = ""
        if is_douyin:
            if not douyin_source_used:
                douyin_source_used = str(text_source or provider or "").strip().lower()
            douyin_dedup_key = _build_douyin_dedup_key(
                url=url,
                source_text=source_text,
                video_id_hint=str(asr_payload.get("video_id") or "").strip(),
                canonical_url_hint=str(asr_payload.get("canonical_url") or "").strip(),
            )
            if douyin_source_used == "asr":
                write_summary_text = asr_summary
                write_keypoints_text = asr_keypoints

        if apply_mode and body_plain.strip() and content_status in {"success", "skipped_test"}:
            body_doc = _write_body_doc(
                benchmark_root=benchmark_root,
                source_time=source_time,
                url=url,
                title=title,
                body_text=body_plain,
                summary_text=write_summary_text,
                keypoints_text=write_keypoints_text,
                dedup_key=douyin_dedup_key,
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
                douyin_main_section_used=douyin_main_section_used,
                douyin_caption_score=douyin_caption_score,
                douyin_caption_selected=douyin_caption_selected,
                summary_detected=bool(summary_detected),
                text_source=text_source or provider or "none",
                reject_reason=reject_reason,
                douyin_pipeline_mode=douyin_pipeline_mode,
                douyin_source_used=douyin_source_used or text_source or provider or "",
                douyin_dedup_key=douyin_dedup_key,
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
        lines.append(f"  summary_detected={str(item.summary_detected).lower()}")
        if item.text_source:
            lines.append(f"  text_source={item.text_source}")
        if item.douyin_pipeline_mode:
            lines.append(f"  douyin_pipeline_mode={item.douyin_pipeline_mode}")
        if item.douyin_source_used:
            lines.append(f"  douyin_source_used={item.douyin_source_used}")
        if item.douyin_dedup_key:
            lines.append(f"  douyin_dedup_key={item.douyin_dedup_key}")
        if item.reject_reason:
            lines.append(f"  reject_reason={item.reject_reason}")
        if item.douyin_main_section_used:
            lines.append(f"  douyin_main_section_used={item.douyin_main_section_used}")
        if item.douyin_caption_score:
            lines.append(f"  douyin_caption_score={item.douyin_caption_score}")
        if item.douyin_caption_selected:
            lines.append(f"  douyin_caption_selected={item.douyin_caption_selected}")
        if item.error:
            lines.append(f"  error={item.error}")
    if result.link_log_path:
        lines.append(f"link_log={result.link_log_path}")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    _refresh_runtime_config()
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
    parser.add_argument(
        "--source-text",
        default="",
        help="Optional raw source text (e.g. copied Douyin share text) for正文补全.",
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
        source_text=str(args.source_text or ""),
    )
    sys.stdout.write(_render_cli_report(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
