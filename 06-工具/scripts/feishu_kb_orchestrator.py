#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feishu message orchestrator for knowledge-base ingest + skill generation.

Contract:
- CLI: python feishu_kb_orchestrator.py --text ... --event-ref ... --source-time ...
- Intent routing:
  - Message with URL => ingest link_mode
  - Message with URL + explicit quote trigger => ingest mixed_mode (quote + link)
  - Message with @鍓嶇紑锛堝惈鍙€夆€滃洖澶?搴忓彿鈥濓級=> ingest quote_mode锛堜粎鍏ュ簱 @ 鍚庢鏂囷級
  - Non-link, non-skill, non-@ message => plain chat fallback
- Skill commands support:
    - Strong trigger: /skill {skill_id} 骞冲彴={骞冲彴} 闇€姹?{鏂囨湰}
    - Weak trigger: 鐢▄skill鍚峿鐢熸垚{闇€姹倉
- If ingest and skill are both triggered, execute them concurrently and aggregate reply.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from codex_commander import execute_tasks
from feishu_http_client import ensure_bitable_link_record
from feishu_skill_runner import (
    DEFAULT_MODEL,
    build_skill_registry,
    resolve_codex_cli,
    resolve_skill,
)
try:
    from feishu_skill_runner import build_skill_context_plan
except ImportError:
    # Backward compatibility for clouds that still run older feishu_skill_runner.py
    # without build_skill_context_plan. Keep skill flow usable with a no-op plan.
    def build_skill_context_plan(
        *,
        skill_id: str,
        brief: str,
        platform: str = "",
        context_files: list[str] | None = None,
    ) -> dict[str, Any]:
        merged = list(context_files or [])
        return {
            "skill_id": skill_id,
            "platform": platform,
            "context_files_merged": merged,
            "context_files_auto": [],
            "context_warnings": [],
        }
from link_async_jobs import enqueue_job as enqueue_link_async_job
from link_async_jobs import ensure_schema as ensure_link_async_schema
from topic_pipeline import run_pipeline_daemon, run_pipeline_once


REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_ROOT = REPO_ROOT / "06-工具" / "data" / "feishu-orchestrator"
RUN_LOG_DIR = LOG_ROOT / "runs"
DEAD_LETTER_DIR = LOG_ROOT / "dead-letter"

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
SHORT_LINK_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:v\.douyin\.com|xhslink\.com|b23\.tv)/[A-Za-z0-9_-]+/?(?:\?[^\s<>\"'`]+)?",
    re.IGNORECASE,
)
SKILL_COMMAND_RE = re.compile(r"^\s*/skill\s+([^\s]+)(.*)$", re.IGNORECASE)
WECHAT_IMAGE_DIRECT_RE = re.compile(
    r"^\s*(?:生成公众号图片|公众号图片生成)\s*[:：]\s*(?P<target>.+?)\s*$",
    re.IGNORECASE,
)
WECHAT_CONTENT_DIRECT_RE = re.compile(
    r"^\s*(?:生成公众号内容|公众号内容生成)\s*[:：]\s*(?P<target>.+?)\s*$",
    re.IGNORECASE,
)
WECHAT_TOPIC_REFINE_DIRECT_RE = re.compile(
    r"^\s*(?:深化公众号选题|公众号选题深化)\s*[:：]\s*(?P<target>.+?)\s*$",
    re.IGNORECASE,
)
WECHAT_BENCHMARK_DIRECT_RE = re.compile(
    r"^\s*(?:分析公众号对标文案|公众号对标文案分析)\s*[:：]\s*(?P<target>.+?)\s*$",
    re.IGNORECASE,
)
WECHAT_PROMPT_NORMALIZE_DIRECT_RE = re.compile(
    r"^\s*(?:标准化公众号配图提示词|公众号配图提示词标准化)\s*[:：]\s*(?P<target>.+?)\s*$",
    re.IGNORECASE,
)
WECHAT_LAYOUT_DIRECT_RE = re.compile(r"^\s*排版公众号\s*[:：]\s*(?P<target>.+?)\s*$", re.IGNORECASE)
WECHAT_PUBLISH_DIRECT_RE = re.compile(r"^\s*发布公众号\s*[:：]\s*(?P<target>.+?)\s*$", re.IGNORECASE)
WECHAT_APPROVE_DIRECT_RE = re.compile(r"^\s*确认发布\s*[:：]\s*(?P<task>[A-Za-z0-9._:-]+)\s*$", re.IGNORECASE)
MEDIA_COMMAND_RE = re.compile(r"^\s*/media\s+generate\b", re.IGNORECASE)
PUBLISH_PREPARE_RE = re.compile(r"^\s*/publish\s+prepare\b", re.IGNORECASE)
PUBLISH_RETRY_RE = re.compile(r"^\s*/publish\s+retry\b", re.IGNORECASE)
METRICS_COMMAND_RE = re.compile(r"^\s*/metrics\s+run\b", re.IGNORECASE)
METRICS_BACKFILL_RE = re.compile(r"^\s*/metrics\s+backfill\b", re.IGNORECASE)
RETRO_COMMAND_RE = re.compile(r"^\s*/retro\s+run\b", re.IGNORECASE)
RETRO_BACKFILL_RE = re.compile(r"^\s*/retro\s+backfill\b", re.IGNORECASE)
OPS_TASK_RE = re.compile(r"^\s*/ops\s+task\b", re.IGNORECASE)
APPROVE_COMMAND_RE = re.compile(r"(?:任务|task)\s*[=:：]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE)
PLATFORM_KV_RE = re.compile(r"(?:平台|platform)\s*[=:：]\s*([^\s,，]+)", re.IGNORECASE)
BRIEF_KV_RE = re.compile(r"(?:需求|brief|prompt)\s*[=:：]\s*(.+)$", re.IGNORECASE)
GEN_VERB_RE = re.compile(r"(生成|创作|产出|输出|起草|改写|扩写|润色)", re.IGNORECASE)
REPLY_PREFIX_RE = re.compile(
    r"^\s*(?:(?:\d+\s*[\.、]\s*)?)?(?:(?:回复|reply)\s+[^:：\n]{1,80}\s*[:：]\s*)",
    re.IGNORECASE,
)
BLOCKQUOTE_PREFIX_RE = re.compile(r"^\s*(?:[|｜>＞]+\s*)+")
LEADING_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[·•●▪▫◦○・\-*]+\s*)+")
QUOTE_AT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:\d+\s*[\.、]\s*)?)?(?:(?:回复|reply)\s*[^:：\n]{0,80}\s*[:：]\s*)?(?:[·•●▪▫◦○・\-*]+\s*)?[@＠]\s*(?P<mention>[^:：，,\n]+?)\s*(?:[:：]\s*|\s+)(?P<body>[\s\S]+?)\s*$",
    re.IGNORECASE,
)
QUOTE_TEXT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:\d+\s*[\.、]\s*)?)?(?:(?:回复|reply)\s*[^:：\n]{0,80}\s*[:：]\s*)?(?:[·•●▪▫◦○・\-*]+\s*)?(?:金句|quote)\s*(?:[:：]\s*|\s+)(?P<body>[\s\S]+?)\s*$",
    re.IGNORECASE,
)

ENV_FALLBACKS = (
    Path("/etc/openclaw/feishu.env"),
    REPO_ROOT / "06-工具" / "scripts" / ".env.ingest-writer.local",
    REPO_ROOT / "06-工具" / "scripts" / ".env.ingest-writer",
    REPO_ROOT / "06-工具" / "scripts" / ".env.feishu",
)

ENV_REQUIRED_KEYS = (
    "INGEST_WRITER_BASE_URL",
    "INGEST_SHARED_TOKEN",
    "INGEST_HMAC_SECRET",
    "GIT_SYNC_ENABLED",
    "GIT_SYNC_REPO_ROOT",
    "GIT_SYNC_REMOTE",
    "GIT_SYNC_BRANCH",
    "GIT_SYNC_INCLUDE_PATHS",
    "GIT_SYNC_AUTHOR_NAME",
    "GIT_SYNC_AUTHOR_EMAIL",
    "GIT_SYNC_MAX_RETRIES",
)


@dataclasses.dataclass(frozen=True)
class OrchestratorSettings:
    writer_base_url: str
    ingest_shared_token: str
    ingest_hmac_secret: str
    ingest_timeout_sec: int
    ingest_verify_ssl: bool
    commander_workers: int
    commander_timeout_sec: int
    commander_max_retries: int
    codex_model: str
    max_reply_chars: int
    plain_text_mode: str
    plain_chat_model: str
    plain_chat_timeout_sec: int
    git_sync_enabled: bool
    git_sync_repo_root: str
    git_sync_remote: str
    git_sync_branch: str
    git_sync_include_paths: tuple[str, ...]
    git_sync_author_name: str
    git_sync_author_email: str
    git_sync_max_retries: int
    approval_open_ids: tuple[str, ...]
    link_async_enabled: bool
    link_async_poll_interval_sec: int
    link_async_timeout_min: int


@dataclasses.dataclass(frozen=True)
class SkillIntent:
    skill_id: str
    brief: str
    platform: str
    trigger: str  # strong | weak


@dataclasses.dataclass(frozen=True)
class AutomationIntent:
    kind: str
    payload: dict[str, Any]


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _split_csv(value: str | None, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if not value:
        return default
    out: list[str] = []
    for raw in str(value).replace(";", ",").split(","):
        item = raw.strip().replace("\\", "/").strip("/")
        if not item:
            continue
        out.append(item)
    if not out:
        return default
    return tuple(dict.fromkeys(out))


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return dt.date.today().isoformat()


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _load_env_fallbacks() -> None:
    keys = ENV_REQUIRED_KEYS
    missing = [k for k in keys if not (os.getenv(k) or "").strip()]
    if not missing:
        return
    for env_path in ENV_FALLBACKS:
        if not env_path.exists():
            continue
        text = env_path.read_text(encoding="utf-8", errors="ignore")
        for raw in text.splitlines():
            line = raw.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.lower().startswith("export "):
                key = key[7:].strip()
            value = value.strip().strip('"').strip("'")
            if key and value and not (os.getenv(key) or "").strip():
                os.environ[key] = value
        missing = [k for k in keys if not (os.getenv(k) or "").strip()]
        if not missing:
            return


def _load_settings() -> OrchestratorSettings:
    _load_env_fallbacks()
    shared_token = str(os.getenv("INGEST_SHARED_TOKEN") or "").strip()
    hmac_secret = str(os.getenv("INGEST_HMAC_SECRET") or "").strip() or shared_token
    return OrchestratorSettings(
        writer_base_url=str(os.getenv("INGEST_WRITER_BASE_URL") or "http://127.0.0.1:8790").strip().rstrip("/"),
        ingest_shared_token=shared_token,
        ingest_hmac_secret=hmac_secret,
        ingest_timeout_sec=max(3, int(os.getenv("INGEST_TIMEOUT_SEC", "20"))),
        ingest_verify_ssl=_as_bool(os.getenv("INGEST_VERIFY_SSL"), default=True),
        commander_workers=max(1, int(os.getenv("FEISHU_COMMANDER_WORKERS", "2"))),
        commander_timeout_sec=max(30, int(os.getenv("FEISHU_COMMANDER_TIMEOUT_SEC", "1800"))),
        commander_max_retries=max(0, int(os.getenv("FEISHU_COMMANDER_MAX_RETRIES", "1"))),
        codex_model=str(os.getenv("FEISHU_SKILL_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        max_reply_chars=max(500, int(os.getenv("FEISHU_REPLY_MAX_CHARS", "1500"))),
        plain_text_mode=str(os.getenv("FEISHU_PLAIN_TEXT_MODE") or "chat").strip().lower(),
        plain_chat_model=str(os.getenv("FEISHU_CHAT_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        plain_chat_timeout_sec=max(30, int(os.getenv("FEISHU_CHAT_TIMEOUT_SEC", "120"))),
        git_sync_enabled=_as_bool(os.getenv("GIT_SYNC_ENABLED"), default=False),
        git_sync_repo_root=str(os.getenv("GIT_SYNC_REPO_ROOT") or REPO_ROOT).strip() or str(REPO_ROOT),
        git_sync_remote=str(os.getenv("GIT_SYNC_REMOTE") or "origin").strip() or "origin",
        git_sync_branch=str(os.getenv("GIT_SYNC_BRANCH") or "main").strip() or "main",
        git_sync_include_paths=_split_csv(
            os.getenv("GIT_SYNC_INCLUDE_PATHS"),
            default=("02-", "03-", "01-"),
        ),
        git_sync_author_name=str(os.getenv("GIT_SYNC_AUTHOR_NAME") or "feishu-bot").strip() or "feishu-bot",
        git_sync_author_email=str(os.getenv("GIT_SYNC_AUTHOR_EMAIL") or "feishu-bot@local").strip()
        or "feishu-bot@local",
        git_sync_max_retries=max(0, int(os.getenv("GIT_SYNC_MAX_RETRIES", "2"))),
        approval_open_ids=_split_csv(
            os.getenv("FEISHU_APPROVAL_OPEN_IDS"),
            default=(),
        ),
        link_async_enabled=_as_bool(os.getenv("FEISHU_LINK_ASYNC_ENABLED"), default=False),
        link_async_poll_interval_sec=max(10, int(os.getenv("FEISHU_LINK_ASYNC_POLL_INTERVAL_SEC", "60"))),
        link_async_timeout_min=max(1, int(os.getenv("FEISHU_LINK_ASYNC_TIMEOUT_MIN", "20"))),
    )


def _normalize_key(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def _dedupe_urls(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in urls:
        value = str(item or "").strip().rstrip(".,;:!?，。；：！？）)]》」』")
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _extract_urls(text: str) -> list[str]:
    raw = str(text or "")
    urls: list[str] = list(URL_RE.findall(raw))
    for match in SHORT_LINK_RE.finditer(raw):
        value = str(match.group(0) or "").strip()
        if not value:
            continue
        if not value.lower().startswith("http"):
            value = f"https://{value}"
        urls.append(value)
    return _dedupe_urls(urls)


def _extract_urls_from_node(node: Any) -> list[str]:
    urls: list[str] = []

    def _walk(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            urls.extend(_extract_urls(value))
            return
        if isinstance(value, list):
            for item in value:
                _walk(item)
            return
        if isinstance(value, dict):
            for key in ("href", "url", "link", "share_url"):
                raw = value.get(key)
                if isinstance(raw, str):
                    urls.extend(_extract_urls(raw))
            for item in value.values():
                _walk(item)

    _walk(node)
    return _dedupe_urls(urls)


def _extract_urls_from_meta(meta: dict[str, Any], source_ref: str = "") -> list[str]:
    urls = _extract_urls_from_node(meta)
    raw_ref = str(source_ref or "").strip()
    if raw_ref.startswith("{") and raw_ref.endswith("}"):
        try:
            payload = json.loads(raw_ref)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            urls.extend(_extract_urls_from_node(payload))
    return _dedupe_urls(urls)


def _normalize_async_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        p = urlparse(raw)
    except Exception:
        return raw.lower()
    scheme = (p.scheme or "https").lower()
    host = (p.netloc or "").lower()
    if not host:
        return raw.lower()
    path = re.sub(r"/+", "/", p.path or "/")
    return f"{scheme}://{host}{path}".rstrip("/")


def _extract_douyin_video_id(url: str) -> str:
    raw = str(url or "")
    match = re.search(r"/(?:video|share/video)/(\d{8,32})(?:\D|$)", raw, re.IGNORECASE)
    if match:
        return str(match.group(1) or "").strip()
    return ""


def _build_async_dedup_key(url: str) -> str:
    video_id = _extract_douyin_video_id(url)
    if video_id:
        return f"dy-{video_id}"
    normalized = _normalize_async_url(url)
    if not normalized:
        normalized = str(url or "").strip()
    if not normalized:
        return ""
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"url-{digest}"


def _is_douyin_url(url: str) -> bool:
    try:
        host = (urlparse(str(url or "")).netloc or "").lower()
    except Exception:
        host = ""
    return bool(host) and (
        host.endswith("douyin.com")
        or host.endswith("iesdouyin.com")
        or host.endswith("v.douyin.com")
    )


def _extract_message_meta_from_source_ref(source_ref: str) -> dict[str, str]:
    raw = str(source_ref or "").strip()
    if not raw:
        return {"message_id": "", "chat_id": ""}

    message_id = ""
    chat_id = ""

    # Optional JSON payload embedded in source_ref.
    if raw.startswith("{") and raw.endswith("}"):
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            message_id = str(payload.get("message_id") or payload.get("msg_id") or "").strip()
            chat_id = str(payload.get("chat_id") or payload.get("open_chat_id") or "").strip()
            if message_id or chat_id:
                return {"message_id": message_id, "chat_id": chat_id}

    # K/V style fallback: message_id=..., chat_id=...
    mid = re.search(r"(?:^|[\s,;|])(?:message_id|msg_id)\s*[:=]\s*([A-Za-z0-9_-]+)", raw, re.IGNORECASE)
    cid = re.search(r"(?:^|[\s,;|])(?:chat_id|open_chat_id)\s*[:=]\s*([A-Za-z0-9_-]+)", raw, re.IGNORECASE)
    if mid:
        message_id = str(mid.group(1) or "").strip()
    if cid:
        chat_id = str(cid.group(1) or "").strip()

    return {"message_id": message_id, "chat_id": chat_id}


def _enqueue_async_jobs(
    *,
    settings: OrchestratorSettings,
    event_ref: str,
    urls: list[str],
    text: str,
    source_ref: str,
    source_time: str,
    source_user: str,
    meta: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    ensure_link_async_schema()
    bitable_app_token = str(os.getenv("BITABLE_APP_TOKEN") or "").strip()
    bitable_table_id = str(os.getenv("BITABLE_TABLE_ID") or "").strip()
    bitable_view_id = str(os.getenv("BITABLE_VIEW_ID") or "").strip()
    bitable_link_field = str(os.getenv("BITABLE_LINK_FIELD") or "视频链接").strip()
    bitable_video_id_field = str(os.getenv("BITABLE_VIDEO_ID_FIELD") or "视频ID").strip()
    bitable_full_scan = _as_bool(os.getenv("INGEST_DOUYIN_BITABLE_FALLBACK_FULL_SCAN"), default=True)

    source_meta = _extract_message_meta_from_source_ref(source_ref)
    message_id = str((meta or {}).get("message_id") or (meta or {}).get("msg_id") or source_meta.get("message_id") or "").strip()
    chat_id = str((meta or {}).get("chat_id") or source_meta.get("chat_id") or "").strip()
    out: list[dict[str, Any]] = []
    for idx, url in enumerate(urls):
        normalized_url = _normalize_async_url(url)
        dedup_key = _build_async_dedup_key(url)
        bitable_seed: dict[str, Any] = {}
        if bitable_app_token and bitable_table_id:
            try:
                bitable_seed = ensure_bitable_link_record(
                    app_token=bitable_app_token,
                    table_id=bitable_table_id,
                    url=url,
                    link_field=bitable_link_field or "视频链接",
                    video_id_field=bitable_video_id_field or "视频ID",
                    view_id=bitable_view_id,
                    fallback_full_scan=bitable_full_scan,
                )
            except Exception as exc:
                bitable_seed = {"ok": False, "error": str(exc)}

        job = enqueue_link_async_job(
            job_id=f"{event_ref}#async#{idx + 1}",
            event_ref=event_ref,
            url=url,
            normalized_url=normalized_url,
            message_id=message_id,
            chat_id=chat_id,
            source_ref=source_ref,
            source_time=source_time,
            source_user=source_user,
            dedup_key=dedup_key,
            timeout_minutes=settings.link_async_timeout_min,
            meta={
                "text": text,
                "urls": urls,
                "pipeline_mode": str(os.getenv("INGEST_DOUYIN_PIPELINE_MODE") or "asr_primary").strip().lower(),
                "message_id": message_id,
                "chat_id": chat_id,
                "source_ref": source_ref,
                "source_time": source_time,
                "source_user": source_user,
                "bitable_seed": bitable_seed,
                "dedup_key": dedup_key,
            },
        )
        out.append(job)
    return out


def _strip_urls(text: str) -> str:
    cleaned = URL_RE.sub(" ", str(text or ""))
    cleaned = SHORT_LINK_RE.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_quote_trigger(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "none", ""

    # Feishu thread replies may prefix body with `鍥炲 鏌愭煇锛歚.
    normalized = raw
    for _ in range(3):
        normalized = BLOCKQUOTE_PREFIX_RE.sub("", normalized).lstrip()
        normalized = LEADING_BULLET_PREFIX_RE.sub("", normalized).lstrip()
        prefix = REPLY_PREFIX_RE.match(normalized)
        if not prefix:
            break
        normalized = normalized[prefix.end() :].lstrip()

    # Clients may still keep one leading quote marker after prefix stripping.
    normalized = BLOCKQUOTE_PREFIX_RE.sub("", normalized).lstrip()
    normalized = LEADING_BULLET_PREFIX_RE.sub("", normalized).lstrip()

    matched = QUOTE_AT_PREFIX_RE.match(normalized)
    trigger = "at_prefix"
    if not matched:
        matched = QUOTE_TEXT_PREFIX_RE.match(normalized)
        trigger = "text_prefix"
    if not matched:
        return "none", ""
    body = re.sub(r"\s+", " ", str(matched.group("body") or "")).strip(" \t\r\n:：")
    if not body:
        return "none", ""
    return trigger, body


def _extract_quote_after_mention(text: str) -> tuple[bool, str]:
    trigger, body = _parse_quote_trigger(text)
    return trigger != "none", body


def _is_quote_trigger_text(text: str) -> bool:
    trigger, _ = _parse_quote_trigger(text)
    return trigger != "none"


def _extract_platform(text: str) -> str:
    matched = PLATFORM_KV_RE.search(str(text or ""))
    if not matched:
        return ""
    return str(matched.group(1) or "").strip()


def _remove_kv_chunks(text: str) -> str:
    cleaned = PLATFORM_KV_RE.sub(" ", str(text or ""))
    cleaned = BRIEF_KV_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _extract_token(text: str, names: list[str]) -> str:
    if not names:
        return ""
    pattern = r"(?:%s)\s*[:=：]\s*([^\s]+)" % "|".join(re.escape(name) for name in names)
    matched = re.search(pattern, str(text or ""), re.IGNORECASE)
    if not matched:
        return ""
    return str(matched.group(1) or "").strip()


def _extract_text_value(text: str, names: list[str], stop_names: list[str]) -> str:
    if not names:
        return ""
    stop_pattern = "|".join(re.escape(name) for name in stop_names)
    pattern = r"(?:%s)\s*[:=：]\s*(.+?)(?=\s+(?:%s)\s*[:=：]|$)" % (
        "|".join(re.escape(name) for name in names),
        stop_pattern if stop_pattern else "$^",
    )
    matched = re.search(pattern, str(text or ""), re.IGNORECASE | re.DOTALL)
    if not matched:
        return ""
    value = re.sub(r"\s+", " ", str(matched.group(1) or "")).strip()
    return value


def _source_user_from_ref(source_ref: str) -> str:
    text = str(source_ref or "").strip()
    if not text:
        return ""
    matched = re.search(r"(?:open_id|openid|user_id|userid)[:=]([A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if matched:
        return str(matched.group(1) or "").strip()
    return ""


def _looks_like_media_command(text: str) -> bool:
    return bool(MEDIA_COMMAND_RE.search(str(text or "")))


def _detect_automation_intent(text: str, *, meta: dict[str, Any] | None = None) -> AutomationIntent | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    meta_payload = dict(meta or {})

    if _looks_like_media_command(raw):
        platform = _extract_token(raw, ["平台", "platform"])
        model = _extract_token(raw, ["模型", "model"])
        mode = _extract_token(raw, ["模式", "mode"])
        brief = _extract_text_value(
            raw,
            ["文案", "copy", "prompt"],
            ["平台", "platform", "模型", "model", "模式", "mode"],
        )
        payload = {
            **meta_payload,
            "action": "generate",
            "platform": platform,
            "model": model,
            "mode": mode or "text",
            "copy": brief,
        }
        return AutomationIntent(kind="media_generate", payload=payload)

    layout_match = WECHAT_LAYOUT_DIRECT_RE.match(raw)
    if layout_match:
        payload = {
            **meta_payload,
            "platform": "wechat",
            "content": str(layout_match.group("target") or "").strip(),
        }
        return AutomationIntent(kind="publish_preview", payload=payload)

    publish_direct_match = WECHAT_PUBLISH_DIRECT_RE.match(raw)
    if publish_direct_match:
        payload = {
            **meta_payload,
            "platform": "wechat",
            "content": str(publish_direct_match.group("target") or "").strip(),
            "mode": "publish",
        }
        return AutomationIntent(kind="publish_prepare", payload=payload)

    approve_direct_match = WECHAT_APPROVE_DIRECT_RE.match(raw)
    if approve_direct_match:
        payload = {
            **meta_payload,
            "action": "approve",
            "task_id": str(approve_direct_match.group("task") or "").strip(),
        }
        return AutomationIntent(kind="publish_approve", payload=payload)

    if PUBLISH_PREPARE_RE.search(raw):
        payload = {
            **meta_payload,
            "platform": _extract_token(raw, ["平台", "platform"]),
            "account": _extract_token(raw, ["账号", "account"]),
            "content": _extract_token(raw, ["内容", "content"]),
            "mode": _extract_token(raw, ["模式", "mode"]) or "publish",
            "schedule_time": _extract_token(raw, ["时间", "publish_time", "schedule_time"]),
        }
        return AutomationIntent(kind="publish_prepare", payload=payload)

    if PUBLISH_RETRY_RE.search(raw):
        payload = {
            **meta_payload,
            "action": "retry",
            "task_id": _extract_token(raw, ["任务", "task"]),
        }
        return AutomationIntent(kind="publish_approve", payload=payload)

    approve_match = APPROVE_COMMAND_RE.search(raw)
    if approve_match and re.search(r"(确认发布|approve)", raw, re.IGNORECASE):
        payload = {
            **meta_payload,
            "action": "approve",
            "task_id": str(approve_match.group(1) or "").strip(),
        }
        return AutomationIntent(kind="publish_approve", payload=payload)

    if METRICS_COMMAND_RE.search(raw):
        payload = {**meta_payload, "date": _extract_token(raw, ["日期", "date"]) or "today"}
        return AutomationIntent(kind="metrics_run", payload=payload)

    if METRICS_BACKFILL_RE.search(raw):
        payload = {**meta_payload, "date": _extract_token(raw, ["日期", "date"]) or "today"}
        return AutomationIntent(kind="metrics_backfill", payload=payload)

    if RETRO_COMMAND_RE.search(raw):
        payload = {**meta_payload, "date": _extract_token(raw, ["日期", "date"]) or "today"}
        return AutomationIntent(kind="retro_run", payload=payload)

    if RETRO_BACKFILL_RE.search(raw):
        payload = {**meta_payload, "date": _extract_token(raw, ["日期", "date"]) or "today"}
        return AutomationIntent(kind="retro_backfill", payload=payload)

    if OPS_TASK_RE.search(raw):
        payload: dict[str, Any] = {**meta_payload}
        query_value = _extract_token(raw, ["查询", "query"])
        if query_value:
            if re.match(r"^\d{4}-\d{2}-\d{2}$", query_value):
                payload["date"] = query_value
            elif re.match(r"^[a-z]+-\d{8}\d{6}-[0-9a-f]{8}$", query_value, re.IGNORECASE):
                payload["task_id"] = query_value
            elif query_value in {"running", "pending_approval", "waiting_manual_publish", "success", "error", "retry_pending"}:
                payload["status"] = query_value
            else:
                payload["task_id"] = query_value
        payload["task_id"] = _extract_token(raw, ["任务", "task"]) or str(payload.get("task_id") or "")
        payload["date"] = _extract_token(raw, ["日期", "date"]) or str(payload.get("date") or "")
        payload["status"] = _extract_token(raw, ["状态", "status"]) or str(payload.get("status") or "")
        payload["task_type"] = _extract_token(raw, ["类型", "type"])
        payload["platform"] = _extract_token(raw, ["平台", "platform"])
        payload["limit"] = _extract_token(raw, ["数量", "limit"]) or "20"
        return AutomationIntent(kind="ops_task_query", payload=payload)

    return None


def _find_best_skill_alias(text: str, registry: Any) -> str:
    normalized_text = _normalize_key(text)
    if not normalized_text:
        return ""

    best_skill = ""
    best_len = 0
    for skill in registry.by_id.values():
        aliases = {skill.skill_id, skill.name, *skill.aliases}
        for alias in aliases:
            key = _normalize_key(alias)
            if len(key) < 2:
                continue
            if key in normalized_text and len(key) > best_len:
                best_skill = skill.skill_id
                best_len = len(key)
    if best_skill:
        return best_skill

    fallback_aliases = (
        ("生成公众号内容", "wechat"),
        ("公众号内容生成", "wechat"),
        ("深化公众号选题", "wechat_topic_refine"),
        ("公众号选题深化", "wechat_topic_refine"),
        ("分析公众号对标文案", "wechat_benchmark_analyze"),
        ("公众号对标文案分析", "wechat_benchmark_analyze"),
        ("标准化公众号配图提示词", "wechat_prompt_normalize"),
        ("公众号配图提示词标准化", "wechat_prompt_normalize"),
        ("公众号图片生成", "wechat_image"),
        ("生成公众号图片", "wechat_image"),
        ("wechat", "wechat"),
        ("公众号", "wechat"),
        ("xhs", "xhs"),
        ("小红书", "xhs"),
        ("douyin", "douyin"),
        ("短视频", "短视频脚本生成"),
    )
    for alias, mapped in fallback_aliases:
        if _normalize_key(alias) in normalized_text:
            try:
                return resolve_skill(registry, mapped).skill_id
            except Exception:
                continue
    return ""


def _detect_skill_intent(text: str, registry: Any, forced_skill_id: str, forced_platform: str) -> SkillIntent | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    if forced_skill_id:
        skill = resolve_skill(registry, forced_skill_id)
        platform = forced_platform or _extract_platform(raw) or skill.default_platform
        brief = _remove_kv_chunks(raw)
        if not brief:
            raise ValueError("skill brief is empty")
        return SkillIntent(skill_id=skill.skill_id, brief=brief, platform=platform, trigger="strong")

    direct_skill_patterns = (
        (WECHAT_CONTENT_DIRECT_RE, "wechat"),
        (WECHAT_TOPIC_REFINE_DIRECT_RE, "wechat_topic_refine"),
        (WECHAT_BENCHMARK_DIRECT_RE, "wechat_benchmark_analyze"),
        (WECHAT_PROMPT_NORMALIZE_DIRECT_RE, "wechat_prompt_normalize"),
        (WECHAT_IMAGE_DIRECT_RE, "wechat_image"),
    )
    for pattern, target_skill_id in direct_skill_patterns:
        matched = pattern.match(raw)
        if not matched:
            continue
        skill = resolve_skill(registry, target_skill_id)
        brief = str(matched.group("target") or "").strip()
        if not brief:
            raise ValueError("skill brief is empty")
        platform = forced_platform or skill.default_platform or "公众号"
        return SkillIntent(skill_id=skill.skill_id, brief=brief, platform=platform, trigger="strong")

    # Strong trigger: /skill {skill_id} 骞冲彴=... 闇€姹?...
    matched = SKILL_COMMAND_RE.match(raw)
    if matched:
        skill_ref = str(matched.group(1) or "").strip()
        tail = str(matched.group(2) or "").strip()
        skill = resolve_skill(registry, skill_ref)
        platform = forced_platform or _extract_platform(tail) or skill.default_platform

        brief_match = BRIEF_KV_RE.search(tail)
        if brief_match:
            brief = str(brief_match.group(1) or "").strip()
        else:
            brief = _remove_kv_chunks(tail)
        brief = _strip_urls(brief)
        if not brief:
            raise ValueError("skill brief is empty")
        return SkillIntent(skill_id=skill.skill_id, brief=brief, platform=platform, trigger="strong")

    # Weak trigger: use-skill + generate intent.
    if not GEN_VERB_RE.search(raw):
        return None
    lowered = raw.lower()
    if "用" not in raw and "使用" not in raw and "skill" not in lowered:
        return None

    skill_id = _find_best_skill_alias(raw, registry)
    if not skill_id:
        return None
    skill = resolve_skill(registry, skill_id)
    platform = forced_platform or _extract_platform(raw) or skill.default_platform

    verb_match = GEN_VERB_RE.search(raw)
    brief = raw[verb_match.end() :].strip(" ：:，,。") if verb_match else ""
    if not brief:
        brief = _remove_kv_chunks(raw)
    brief = _strip_urls(brief)
    if not brief:
        raise ValueError("skill brief is empty")
    return SkillIntent(skill_id=skill.skill_id, brief=brief, platform=platform, trigger="weak")


def _stable_event_ref(*, text: str, source_ref: str) -> str:
    payload = f"{source_ref}|{text}".encode("utf-8", errors="ignore")
    digest = hashlib.sha1(payload).hexdigest()[:20]
    return f"evt-{digest}"


def _writer_signature(secret: str, *, timestamp: str, nonce: str, body: bytes) -> str:
    payload = f"{timestamp}\n{nonce}\n".encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _writer_headers(settings: OrchestratorSettings, body: bytes) -> dict[str, str]:
    timestamp = str(int(dt.datetime.now(dt.timezone.utc).timestamp()))
    nonce = uuid.uuid4().hex
    signature = _writer_signature(settings.ingest_hmac_secret, timestamp=timestamp, nonce=nonce, body=body)
    return {
        "Authorization": f"Bearer {settings.ingest_shared_token}",
        "Content-Type": "application/json",
        "X-Ingest-Timestamp": timestamp,
        "X-Ingest-Nonce": nonce,
        "X-Ingest-Signature": signature,
    }


def _call_writer(
    settings: OrchestratorSettings,
    endpoint: str,
    payload: dict[str, Any],
    *,
    method: str = "POST",
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    if not settings.ingest_shared_token:
        raise RuntimeError("INGEST_SHARED_TOKEN is empty")
    if not settings.ingest_hmac_secret:
        raise RuntimeError("INGEST_HMAC_SECRET is empty")

    method_upper = str(method or "POST").strip().upper()
    request_timeout = max(3, int(timeout_sec if timeout_sec is not None else settings.ingest_timeout_sec))
    if method_upper == "GET":
        body = b""
        response = requests.get(
            f"{settings.writer_base_url}{endpoint}",
            headers=_writer_headers(settings, body),
            params=payload,
            timeout=request_timeout,
            verify=settings.ingest_verify_ssl,
        )
    else:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        response = requests.post(
            f"{settings.writer_base_url}{endpoint}",
            headers=_writer_headers(settings, body),
            data=body,
            timeout=request_timeout,
            verify=settings.ingest_verify_ssl,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"writer api {response.status_code}: {response.text}")
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"writer api business error: {data}")
    return data


def _run_ingest(
    *,
    settings: OrchestratorSettings,
    text: str,
    urls: list[str],
    event_ref: str,
    source_ref: str,
    source_time: str,
    dry_run: bool,
    force_replay: bool = False,
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    if not text.strip() and not urls:
        return {"status": "ignored", "mode": "ignore", "errors": ["empty text"], "result": {}}

    mode = "link" if urls else "quote"
    endpoint = "/internal/ingest/v1/replay" if force_replay else ("/internal/ingest/v1/link" if urls else "/internal/ingest/v1/quote")
    ingest_event_ref = f"{event_ref}#{mode}"
    source_kind = str(os.getenv("INGEST_SOURCE_KIND") or "openclaw-feishu").strip() or "openclaw-feishu"
    payload: dict[str, Any] = {
        "event_ref": ingest_event_ref,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "source_time": source_time,
    }
    if force_replay:
        payload["mode"] = mode
    if urls:
        payload["urls"] = urls
        payload["text"] = text
    else:
        quote_body = _strip_urls(text)
        if quote_body and not re.match(r"^\s*(金句|quote)\s*[:：]\s*", quote_body, re.IGNORECASE):
            quote_body = f"金句：{quote_body}"
        payload["text"] = quote_body

    if dry_run:
        return {
            "status": "success",
            "mode": mode,
            "dry_run": True,
            "event_ref": ingest_event_ref,
            "payload": payload,
            "result": {"mode": mode, "status": "success", "added": 0, "near_dup": 0, "skipped": 0},
        }

    data = _call_writer(settings, endpoint, payload, timeout_sec=timeout_sec)
    result = data.get("result") or {}
    return {
        "status": str(result.get("status") or "unknown"),
        "mode": str(result.get("mode") or mode),
        "event_ref": str(data.get("event_ref") or ingest_event_ref),
        "duplicate": bool(data.get("duplicate")),
        "result": result,
        "raw": data,
    }


def _latest_nextday_brief() -> str:
    enabled = str(os.getenv("FEISHU_SKILL_INCLUDE_NEXTDAY_BRIEF", "true")).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    if not enabled:
        return ""
    brief_dir = REPO_ROOT / "01-选题管理" / "次日创作输入"
    if not brief_dir.exists() or not brief_dir.is_dir():
        return ""
    files = sorted((path for path in brief_dir.glob("*.md") if path.is_file()), key=lambda p: p.name, reverse=True)
    if not files:
        return ""
    try:
        return files[0].resolve().relative_to(REPO_ROOT).as_posix()
    except Exception:
        return ""


def _run_skill(
    *,
    settings: OrchestratorSettings,
    skill_intent: SkillIntent,
    event_ref: str,
    source_ref: str,
    source_time: str,
    dry_run: bool,
) -> dict[str, Any]:
    explicit_context_files: list[str] = []
    nextday_brief = _latest_nextday_brief()
    if nextday_brief:
        explicit_context_files.append(nextday_brief)

    context_plan = build_skill_context_plan(
        skill_id=skill_intent.skill_id,
        brief=skill_intent.brief,
        platform=skill_intent.platform,
        context_files=explicit_context_files,
    )
    merged_context_files = list(context_plan.get("context_files_merged") or [])

    task = {
        "task_id": f"{event_ref}#skill",
        "event_ref": f"{event_ref}#skill",
        "source_ref": source_ref,
        "source_time": source_time,
        "skill_id": context_plan.get("skill_id") or skill_intent.skill_id,
        "brief": skill_intent.brief,
        "platform": context_plan.get("platform") or skill_intent.platform,
        "model": settings.codex_model,
        "context_files": explicit_context_files,
        "context_files_planned": merged_context_files,
        "context_files_auto": context_plan.get("context_files_auto") or [],
        "context_warnings": context_plan.get("context_warnings") or [],
        "context_errors": context_plan.get("context_errors") or [],
    }

    if dry_run:
        context_errors = list(context_plan.get("context_errors") or [])
        status = "error" if context_errors else "success"
        return {
            "status": status,
            "dry_run": True,
            "trigger": skill_intent.trigger,
            "skill_id": skill_intent.skill_id,
            "platform": skill_intent.platform,
            "brief": skill_intent.brief,
            "result": {
                "status": status,
                "skill_id": task["skill_id"],
                "platform": task["platform"],
                "saved_files": [],
                "full_text": "",
                "context_files_used": merged_context_files,
                "context_files_auto": context_plan.get("context_files_auto") or [],
                "context_warnings": context_plan.get("context_warnings") or [],
                "context_errors": context_errors,
                "errors": context_errors,
            },
            "commander": {"task": task},
        }

    if context_plan.get("context_errors"):
        return {
            "status": "error",
            "dry_run": False,
            "trigger": skill_intent.trigger,
            "skill_id": skill_intent.skill_id,
            "platform": skill_intent.platform,
            "brief": skill_intent.brief,
            "result": {
                "status": "error",
                "skill_id": task["skill_id"],
                "platform": task["platform"],
                "saved_files": [],
                "full_text": "",
                "context_files_used": merged_context_files,
                "context_files_auto": context_plan.get("context_files_auto") or [],
                "context_warnings": context_plan.get("context_warnings") or [],
                "context_errors": context_plan.get("context_errors") or [],
                "errors": list(context_plan.get("context_errors") or []),
            },
            "commander": {"task": task},
        }

    commander_result = execute_tasks(
        payload={"tasks": [task]},
        workers=settings.commander_workers,
        timeout_sec=settings.commander_timeout_sec,
        max_retries=settings.commander_max_retries,
    )
    rows = commander_result.get("results") or []
    first = rows[0] if rows else {}
    return {
        "status": str(first.get("status") or commander_result.get("status") or "unknown"),
        "trigger": skill_intent.trigger,
        "skill_id": skill_intent.skill_id,
        "platform": skill_intent.platform,
        "brief": skill_intent.brief,
        "result": first,
        "commander": commander_result,
    }


def _run_automation_intent(
    *,
    settings: OrchestratorSettings,
    intent: AutomationIntent,
    event_ref: str,
    source_ref: str,
    source_time: str,
    source_user: str,
    dry_run: bool,
) -> dict[str, Any]:
    endpoint_map: dict[str, tuple[str, str]] = {
        "media_generate": ("POST", "/internal/media/generate"),
        "publish_preview": ("POST", "/internal/publish/preview"),
        "publish_prepare": ("POST", "/internal/publish/prepare"),
        "publish_approve": ("POST", "/internal/publish/approve"),
        "metrics_run": ("POST", "/internal/metrics/run"),
        "metrics_backfill": ("POST", "/internal/metrics/backfill"),
        "retro_run": ("POST", "/internal/retro/run"),
        "retro_backfill": ("POST", "/internal/retro/backfill"),
    }
    if intent.kind == "ops_task_query":
        payload = dict(intent.payload or {})
        task_id_for_detail = str(payload.get("task_id") or "").strip()
        if task_id_for_detail:
            route = ("GET", f"/internal/tasks/{task_id_for_detail}")
        else:
            route = ("GET", "/internal/tasks")
    else:
        route = endpoint_map.get(intent.kind)
    if not route:
        raise RuntimeError(f"unsupported automation intent: {intent.kind}")
    method, endpoint = route

    payload = dict(intent.payload or {})
    if intent.kind == "ops_task_query":
        task_id_for_detail = str(payload.get("task_id") or "").strip()
        if task_id_for_detail:
            payload = {
                "include_logs": "true",
                "log_limit": str(payload.get("limit") or "50"),
            }
    if method != "GET":
        payload.setdefault("event_ref", f"{event_ref}#{intent.kind}")
    payload.setdefault("source_ref", source_ref)
    payload.setdefault("source_time", source_time)
    if source_user:
        payload.setdefault("source_user", source_user)
    if dry_run:
        payload["dry_run"] = True
        skip_writer = str(os.getenv("ORCHESTRATOR_DRYRUN_SKIP_WRITER", "true")).strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }
        if skip_writer:
            return {
                "status": "success",
                "kind": intent.kind,
                "event_ref": str(payload.get("event_ref") or ""),
                "result": {"status": "success", "dry_run": True, "payload": payload},
                "raw": {"code": 0, "msg": "dry_run_skip_writer"},
            }

    data = _call_writer(settings, endpoint, payload, method=method)
    result = data.get("result") or {}
    if method == "GET":
        result = {"status": "success", **data}
    status = str(result.get("status") or ("success" if int(data.get("code") or 0) == 0 else "error"))
    return {
        "status": status,
        "kind": intent.kind,
        "event_ref": str(payload.get("event_ref") or ""),
        "result": result,
        "raw": data,
    }


def _compose_automation_reply(auto_result: dict[str, Any], *, max_chars: int) -> tuple[str, list[str]]:
    kind = str(auto_result.get("kind") or "")
    status = str(auto_result.get("status") or "")
    result = auto_result.get("result") or {}
    payload = result.get("payload") if isinstance(result, dict) else {}
    nested = result.get("result") if isinstance(result, dict) else {}
    task_id = str(
        (result.get("task_id") if isinstance(result, dict) else "")
        or (nested.get("task_id") if isinstance(nested, dict) else "")
        or (payload.get("task_id") if isinstance(payload, dict) else "")
        or ""
    )
    lines: list[str] = []

    if kind == "media_generate":
        if status == "success":
            video_url = str(result.get("video_url") or ((result.get("result") or {}).get("video_url")) or "").strip()
            line = f"媒体生成完成 task={task_id or '-'}"
            if video_url:
                line += f" video={video_url}"
            lines.append(line)
        elif status == "duplicate":
            lines.append(f"媒体任务已存在 task={task_id or '-'}")
        else:
            err = (result.get("errors") or auto_result.get("errors") or ["unknown error"])[0]
            lines.append(f"媒体生成失败：{err}")
    elif kind == "publish_preview":
        if status == "success":
            preview_html = str(result.get("preview_html") or "")
            lines.append(f"排版预览已生成 preview={preview_html or '-'}")
        else:
            err = (result.get("errors") or auto_result.get("errors") or ["unknown error"])[0]
            lines.append(f"排版预览失败：{err}")
    elif kind == "publish_prepare":
        if status in {"success", "duplicate"}:
            lines.append(f"发布准备完成 task={task_id or '-'}，等待审批确认。")
        else:
            err = (result.get("errors") or auto_result.get("errors") or ["unknown error"])[0]
            lines.append(f"发布准备失败：{err}")
    elif kind == "publish_approve":
        if status in {"success", "partial"}:
            task_status = str(result.get("task_status") or status)
            lines.append(f"发布审批已执行 task={task_id or '-'}，状态={task_status}。")
        else:
            err = (result.get("errors") or auto_result.get("errors") or ["unknown error"])[0]
            lines.append(f"发布审批失败：{err}")
    elif kind in {"metrics_run", "metrics_backfill"}:
        if status == "success":
            date = str(
                (result.get("date") if isinstance(result, dict) else "")
                or (nested.get("date") if isinstance(nested, dict) else "")
                or (payload.get("date") if isinstance(payload, dict) else "")
                or ""
            )
            prefix = "抓数回填完成" if kind == "metrics_backfill" else "抓数完成"
            lines.append(f"{prefix} date={date or '-'} task={task_id or '-'}")
        else:
            err = (result.get("errors") or auto_result.get("errors") or ["unknown error"])[0]
            lines.append(f"抓数失败：{err}")
    elif kind in {"retro_run", "retro_backfill"}:
        if status == "success":
            date = str(
                (result.get("date") if isinstance(result, dict) else "")
                or (nested.get("date") if isinstance(nested, dict) else "")
                or (payload.get("date") if isinstance(payload, dict) else "")
                or ""
            )
            prefix = "复盘回填完成" if kind == "retro_backfill" else "复盘完成"
            lines.append(f"{prefix} date={date or '-'} task={task_id or '-'}")
        else:
            err = (result.get("errors") or auto_result.get("errors") or ["unknown error"])[0]
            lines.append(f"复盘失败：{err}")
    elif kind == "ops_task_query":
        if status == "success":
            count = int(result.get("count") or 0)
            lines.append(f"任务查询完成，共 {count} 条。")
            tasks = result.get("tasks") if isinstance(result, dict) else []
            if isinstance(tasks, list) and tasks:
                top = tasks[0]
                lines.append(
                    "最近任务: {task_id} {task_type} {status}".format(
                        task_id=str(top.get("task_id") or "-"),
                        task_type=str(top.get("task_type") or "-"),
                        status=str(top.get("status") or "-"),
                    )
                )
        else:
            err = (result.get("errors") or auto_result.get("errors") or ["unknown error"])[0]
            lines.append(f"任务查询失败：{err}")
    else:
        lines.append(f"已处理命令 kind={kind} status={status}")

    joined = "\n".join(lines).strip() or f"已处理命令 kind={kind}"
    segments = _segment_reply(joined, max_chars=max_chars)
    return (segments[0] if segments else joined, segments)


def _parse_codex_json_lines(stdout_text: str) -> tuple[str, str]:
    latest_text = ""
    parse_errors = ""
    fallback_lines: list[str] = []
    for raw in str(stdout_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            fallback_lines.append(raw)
            continue

        if isinstance(payload, dict):
            if payload.get("type") == "error":
                parse_errors = str(payload.get("message") or payload.get("error") or parse_errors)
                continue
            item = payload.get("item")
            if isinstance(item, dict):
                item_type = str(item.get("type") or "")
                if item_type in {"assistant_message", "agent_message"} and isinstance(item.get("text"), str):
                    latest_text = str(item["text"])

    if not latest_text and fallback_lines:
        latest_text = "\n".join(fallback_lines).strip()
    return latest_text, parse_errors


def _run_plain_chat(
    *,
    settings: OrchestratorSettings,
    text: str,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return {
            "status": "success",
            "mode": "chat",
            "result": {"full_text": f"[dry-run] {text.strip()}"},
        }

    if settings.plain_text_mode != "chat":
        return {
            "status": "error",
            "mode": "chat",
            "errors": ["plain text mode disabled"],
            "result": {},
        }

    prompt = (
        "你是飞书聊天助手。请直接使用中文回复，简洁清晰，不要输出 JSON 或代码块。\n\n"
        f"用户消息：{text.strip()}\n"
    )
    codex_cli = resolve_codex_cli()
    args = [codex_cli, "exec", "--json", "--skip-git-repo-check", "-m", settings.plain_chat_model, "-"]
    completed = subprocess.run(
        args,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        timeout=max(30, settings.plain_chat_timeout_sec),
    )
    chat_text, parse_error = _parse_codex_json_lines(completed.stdout or "")
    if completed.returncode != 0:
        err = parse_error or (completed.stderr or "").strip() or f"codex exited with {completed.returncode}"
        return {"status": "error", "mode": "chat", "errors": [err], "result": {}}

    chat_text = str(chat_text or "").strip()
    if not chat_text:
        err = parse_error or "chat model returned empty output"
        return {"status": "error", "mode": "chat", "errors": [err], "result": {}}

    return {
        "status": "success",
        "mode": "chat",
        "result": {"full_text": chat_text},
    }


def _normalize_git_paths(paths: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        value = str(raw or "").strip().replace("\\", "/")
        if not value:
            continue
        value = value.lstrip("./")
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _run_git_sync_after_write(
    *,
    settings: OrchestratorSettings,
    event_ref: str,
    kind: str,
    paths: list[str],
    dry_run: bool,
) -> dict[str, Any]:
    if not settings.git_sync_enabled:
        return {"status": "disabled", "message": "GIT_SYNC_ENABLED=false"}

    script_path = Path(__file__).resolve().with_name("git_sync_after_write.py")
    if not script_path.exists():
        return {"status": "error", "message": f"missing {script_path.name}"}

    args = [
        sys.executable,
        str(script_path),
        "--event-ref",
        event_ref,
        "--kind",
        kind,
    ]
    for item in _normalize_git_paths(paths):
        args.extend(["--path", item])
    if dry_run:
        args.append("--dry-run")

    env = os.environ.copy()
    env["GIT_SYNC_ENABLED"] = "true" if settings.git_sync_enabled else "false"
    env["GIT_SYNC_REPO_ROOT"] = settings.git_sync_repo_root
    env["GIT_SYNC_REMOTE"] = settings.git_sync_remote
    env["GIT_SYNC_BRANCH"] = settings.git_sync_branch
    env["GIT_SYNC_INCLUDE_PATHS"] = ",".join(settings.git_sync_include_paths)
    env["GIT_SYNC_AUTHOR_NAME"] = settings.git_sync_author_name
    env["GIT_SYNC_AUTHOR_EMAIL"] = settings.git_sync_author_email
    env["GIT_SYNC_MAX_RETRIES"] = str(settings.git_sync_max_retries)

    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        env=env,
    )
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    payload: dict[str, Any] = {}
    if stdout:
        try:
            payload = json.loads(stdout)
        except Exception:
            payload = {"status": "error", "message": stdout}
    if not payload:
        payload = {"status": "error", "message": stderr or f"git sync exited with {completed.returncode}"}
    if completed.returncode != 0 and payload.get("status") in {"success", "skipped", "dry_run"}:
        payload["status"] = "error"
    if stderr:
        payload.setdefault("stderr", stderr)
    return payload


def _ingest_reply_line(ingest: dict[str, Any]) -> str:
    status = str(ingest.get("status") or "")
    result = ingest.get("result") or {}
    mode = str(result.get("mode") or ingest.get("mode") or "")
    detail_summary = (result.get("details") or {}).get("summary") or {}

    if status == "error":
        errors = result.get("errors") or ingest.get("errors") or ["unknown error"]
        return f"入库失败：{errors[0]}"

    if mode == "quote":
        added = int(result.get("added") or 0)
        near_dup = int(result.get("near_dup") or 0)
        skipped = int(result.get("skipped") or 0)
        return f"金句入库完成：新增{added}，近似{near_dup}，重复{skipped}"

    if mode == "link":
        added = int(result.get("added") or 0)
        skipped = int(result.get("skipped") or 0)
        total = int(detail_summary.get("link_total") or (added + skipped))
        route_success = int(detail_summary.get("link_route_success_count") or 0)
        content_success = int(detail_summary.get("link_content_success_count") or 0)
        content_failed = int(detail_summary.get("link_content_failed_count") or 0)
        content_skipped_test = int(detail_summary.get("link_content_skipped_test_count") or 0)
        if total > 0 and route_success <= 0:
            errors = result.get("errors") or ingest.get("errors") or []
            if errors:
                return f"链接入库失败：{errors[0]}"
            return f"链接入库失败：路由{route_success}/{total}"
        return (
            "链接处理完成："
            f"路由{route_success}/{total}，"
            f"正文达标{content_success}，"
            f"失败{content_failed}，"
            f"测试跳过{content_skipped_test}"
        )

    added = int(result.get("added") or 0)
    near_dup = int(result.get("near_dup") or 0)
    skipped = int(result.get("skipped") or 0)
    return f"入库完成：新增{added}，近似{near_dup}，跳过{skipped}"


def _segment_reply(text: str, max_chars: int) -> list[str]:
    source = str(text or "").strip()
    if not source:
        return []
    if len(source) <= max_chars:
        return [source]

    out: list[str] = []
    remaining = source
    while remaining:
        if len(remaining) <= max_chars:
            out.append(remaining.strip())
            break
        cut = remaining.rfind("\n", 0, max_chars)
        if cut < int(max_chars * 0.6):
            cut = max_chars
        part = remaining[:cut].strip()
        if part:
            out.append(part)
        remaining = remaining[cut:].lstrip()
    return [item for item in out if item]


def _compose_reply(
    *,
    ingest: dict[str, Any] | None,
    skill: dict[str, Any] | None,
    plain_chat: dict[str, Any] | None,
    max_chars: int,
) -> tuple[str, list[str]]:
    if plain_chat is not None:
        plain_status = str(plain_chat.get("status") or "")
        if plain_status == "success":
            full_text = str((plain_chat.get("result") or {}).get("full_text") or "").strip()
            if full_text:
                segments = _segment_reply(full_text, max_chars=max_chars)
                return (segments[0] if segments else full_text, segments)
        fallback = "处理失败：聊天回复不可用，请稍后重试。"
        return fallback, [fallback]

    ingest_line = _ingest_reply_line(ingest) if ingest else ""

    if skill:
        skill_result = skill.get("result") or {}
        skill_status = str(skill.get("status") or skill_result.get("status") or "")
        if skill_status == "success":
            full_text = str(skill_result.get("full_text") or "").strip()
            if ingest_line and full_text:
                joined = f"{ingest_line}\n\n{full_text}"
            else:
                joined = full_text or ingest_line or "已处理。"
            segments = _segment_reply(joined, max_chars=max_chars)
            return (segments[0] if segments else joined, segments)

        # Skill failed: keep concise and do not flood.
        errors = skill_result.get("errors") or skill.get("errors") or ["skill 执行失败"]
        if ingest_line:
            text = f"{ingest_line}；文案生成失败：{errors[0]}"
            return (text, [text])
        text = f"文案生成失败：{errors[0]}"
        return (text, [text])

    if ingest_line:
        return ingest_line, [ingest_line]
    fallback = "未识别到可处理内容。"
    return fallback, [fallback]


def orchestrate_message(
    *,
    text: str,
    event_ref: str = "",
    source_ref: str = "",
    source_time: str = "",
    source_user: str = "",
    meta: dict[str, Any] | None = None,
    forced_skill_id: str = "",
    forced_platform: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    started = time.time()
    settings = _load_settings()
    message_text = str(text or "").strip()
    source_time_value = str(source_time or "").strip() or _now_iso()
    source_ref_value = str(source_ref or "").strip() or "feishu-message"
    event_ref_value = str(event_ref or "").strip() or _stable_event_ref(text=message_text, source_ref=source_ref_value)
    meta_payload = dict(meta or {})
    source_user_value = (
        str(source_user or "").strip()
        or str(meta_payload.get("source_user") or "").strip()
        or _source_user_from_ref(source_ref_value)
    )

    automation_intent = _detect_automation_intent(message_text, meta=meta_payload)
    if automation_intent is not None:
        auto_errors: list[str] = []
        auto_result: dict[str, Any]
        approval_guard = automation_intent.kind == "publish_approve"
        if approval_guard:
            whitelist = {str(item or "").strip() for item in settings.approval_open_ids if str(item or "").strip()}
            if not source_user_value:
                auto_result = {
                    "status": "error",
                    "kind": automation_intent.kind,
                    "result": {"errors": ["approval source_user missing"]},
                }
            elif not whitelist or source_user_value not in whitelist:
                auto_result = {
                    "status": "error",
                    "kind": automation_intent.kind,
                    "result": {"errors": [f"approver not allowed: {source_user_value}"]},
                }
                auto_errors.append(f"approval rejected: {source_user_value}")
            else:
                try:
                    auto_result = _run_automation_intent(
                        settings=settings,
                        intent=automation_intent,
                        event_ref=event_ref_value,
                        source_ref=source_ref_value,
                        source_time=source_time_value,
                        source_user=source_user_value,
                        dry_run=dry_run,
                    )
                except Exception as exc:
                    auto_result = {"status": "error", "kind": automation_intent.kind, "result": {"errors": [str(exc)]}}
                    auto_errors.append(str(exc))
        else:
            try:
                auto_result = _run_automation_intent(
                    settings=settings,
                    intent=automation_intent,
                    event_ref=event_ref_value,
                    source_ref=source_ref_value,
                    source_time=source_time_value,
                    source_user=source_user_value,
                    dry_run=dry_run,
                )
            except Exception as exc:
                auto_result = {"status": "error", "kind": automation_intent.kind, "result": {"errors": [str(exc)]}}
                auto_errors.append(str(exc))

        auto_status = str(auto_result.get("status") or "")
        status = "success" if auto_status in {"success", "partial", "duplicate"} else "error"
        reply, reply_segments = _compose_automation_reply(auto_result, max_chars=settings.max_reply_chars)
        if status == "error":
            auto_errors.extend((auto_result.get("result") or {}).get("errors") or auto_result.get("errors") or [])
        elapsed_ms = int((time.time() - started) * 1000)
        output = {
            "status": status,
            "event_ref": event_ref_value,
            "source_ref": source_ref_value,
            "source_time": source_time_value,
            "source_user": source_user_value,
            "intent": {
                "automation": True,
                "automation_kind": automation_intent.kind,
                "ingest": False,
                "skill": False,
                "urls": [],
                "skill_id": "",
                "skill_platform": "",
                "skill_trigger": "",
                "ingest_trigger": "none",
            },
            "automation": auto_result,
            "reply": reply,
            "reply_segments": reply_segments,
            "errors": auto_errors,
            "elapsed_ms": elapsed_ms,
        }
        run_log = RUN_LOG_DIR / f"{_today()}.jsonl"
        _append_jsonl(
            run_log,
            {
                "ts": _now_iso(),
                "event_ref": event_ref_value,
                "status": status,
                "intent": output["intent"],
                "source_user": source_user_value,
                "automation_kind": automation_intent.kind,
                "elapsed_ms": elapsed_ms,
                "errors": auto_errors,
            },
        )
        output["run_log"] = run_log.relative_to(REPO_ROOT).as_posix()
        if status == "error":
            dead_log = DEAD_LETTER_DIR / f"{_today()}.jsonl"
            _append_jsonl(
                dead_log,
                {
                    "ts": _now_iso(),
                    "event_ref": event_ref_value,
                    "source_user": source_user_value,
                    "text": message_text,
                    "output": output,
                    "reason": "automation_error",
                },
            )
            output["dead_letter_log"] = dead_log.relative_to(REPO_ROOT).as_posix()
        return output

    registry = build_skill_registry()
    text_urls = _extract_urls(message_text)
    meta_urls = _extract_urls_from_meta(meta_payload, source_ref_value)
    urls = _dedupe_urls(text_urls + meta_urls)
    douyin_urls = [url for url in urls if _is_douyin_url(url)]
    quote_trigger, quote_text = _parse_quote_trigger(message_text)
    quote_trigger_hit = quote_trigger != "none"
    skill_intent = _detect_skill_intent(
        message_text,
        registry=registry,
        forced_skill_id=str(forced_skill_id or "").strip(),
        forced_platform=str(forced_platform or "").strip(),
    )

    ingest_trigger = "url" if urls else quote_trigger
    will_ingest = bool(urls) or quote_trigger_hit
    will_skill = skill_intent is not None
    async_douyin_ingest = (
        settings.link_async_enabled
        and bool(douyin_urls)
        and not will_skill
        and not dry_run
    )

    ingest_result: dict[str, Any] | None = None
    skill_result: dict[str, Any] | None = None
    plain_chat_result: dict[str, Any] | None = None
    plain_chat_fallback_used = False
    git_sync_result: dict[str, Any] | None = None
    errors: list[str] = []

    if async_douyin_ingest:
        pipeline_mode = str(os.getenv("INGEST_DOUYIN_PIPELINE_MODE") or "asr_primary").strip().lower()
        if pipeline_mode not in {"asr_primary", "bitable_primary", "bitable_only"}:
            pipeline_mode = "asr_primary"
        queued_jobs = _enqueue_async_jobs(
            settings=settings,
            event_ref=event_ref_value,
            urls=douyin_urls,
            text=message_text,
            source_ref=source_ref_value,
            source_time=source_time_value,
            source_user=source_user_value,
            meta=meta_payload,
        )
        elapsed_ms = int((time.time() - started) * 1000)
        timeout_min = settings.link_async_timeout_min
        poll_sec = settings.link_async_poll_interval_sec
        if pipeline_mode == "bitable_only":
            waiting_text = "正在等待多维表文案"
            provider = "bitable_async"
        elif pipeline_mode == "bitable_primary":
            waiting_text = "正在提取文案（多维表优先，ASR 兜底）"
            provider = "bitable_primary_async"
        else:
            waiting_text = "正在提取文案（ASR 优先，多维表补充）"
            provider = "asr_primary_async"
        reply = f"链接已接收：{len(queued_jobs)}/{len(douyin_urls)}，{waiting_text}（轮询{poll_sec}s，超时{timeout_min}分钟），完成后自动回帖。"
        output = {
            "status": "queued",
            "event_ref": event_ref_value,
            "source_ref": source_ref_value,
            "source_time": source_time_value,
            "source_user": source_user_value,
            "intent": {
                "automation": False,
                "automation_kind": "",
                "ingest": True,
                "skill": False,
                "urls": urls,
                "skill_id": "",
                "skill_platform": "",
                "skill_trigger": "",
                "ingest_trigger": "url",
            },
            "ingest_trigger": "url",
            "plain_chat_fallback_used": False,
            "ingest": {
                "status": "queued",
                "mode": "link_async",
                "event_ref": event_ref_value,
                "result": {"queued_jobs": queued_jobs, "queued_count": len(queued_jobs)},
            },
            "skill": None,
            "plain_chat": None,
            "git_sync": None,
            "reply": reply,
            "reply_segments": [reply],
            "errors": [],
            "elapsed_ms": elapsed_ms,
            "async": {"enabled": True, "queued_jobs": queued_jobs},
            "douyin_pipeline_mode": pipeline_mode,
        }
        run_log = RUN_LOG_DIR / f"{_today()}.jsonl"
        _append_jsonl(
            run_log,
            {
                "ts": _now_iso(),
                "event_ref": event_ref_value,
                "status": "queued",
                "intent": output["intent"],
                "ingest_trigger": "url",
                "plain_chat_fallback_used": False,
                "git_sync_status": "",
                "git_sync_commit": "",
                "link_route_status": "pending_async",
                "link_content_status": "pending_async",
                "link_content_chars": 0,
                "link_provider": provider,
                "link_is_test": False,
                "link_quality_reason": "pending_async",
                "link_summary_detected": False,
                "link_text_source": "",
                "link_reject_reason": "",
                "elapsed_ms": elapsed_ms,
                "errors": [],
                "async_job_count": len(queued_jobs),
            },
        )
        output["run_log"] = run_log.relative_to(REPO_ROOT).as_posix()
        return output

    if not will_ingest and not will_skill:
        plain_chat_fallback_used = True
        plain_chat_result = _run_plain_chat(settings=settings, text=message_text, dry_run=dry_run)
    else:
        ingest_input_text = quote_text if (quote_trigger_hit and not urls) else message_text
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures: dict[concurrent.futures.Future[Any], str] = {}
            if will_ingest:
                futures[
                    pool.submit(
                        _run_ingest,
                        settings=settings,
                        text=ingest_input_text,
                        urls=urls,
                        event_ref=event_ref_value,
                        source_ref=source_ref_value,
                        source_time=source_time_value,
                        dry_run=dry_run,
                    )
                ] = "ingest"
            if will_skill and skill_intent is not None:
                futures[
                    pool.submit(
                        _run_skill,
                        settings=settings,
                        skill_intent=skill_intent,
                        event_ref=event_ref_value,
                        source_ref=source_ref_value,
                        source_time=source_time_value,
                        dry_run=dry_run,
                    )
                ] = "skill"

            for future in concurrent.futures.as_completed(futures):
                kind = futures[future]
                try:
                    payload = future.result()
                except Exception as exc:
                    payload = {"status": "error", "errors": [str(exc)]}
                if kind == "ingest":
                    ingest_result = payload
                else:
                    skill_result = payload

    if plain_chat_result is not None:
        status = "success" if str(plain_chat_result.get("status") or "") == "success" else "error"
    else:
        ingest_status = str((ingest_result or {}).get("status") or "")
        skill_status = str((skill_result or {}).get("status") or "")
        statuses = [item for item in [ingest_status, skill_status] if item]
        if not statuses:
            status = "ignored"
        elif all(item == "success" for item in statuses):
            status = "success"
        elif any(item == "success" for item in statuses):
            status = "partial"
        else:
            status = "error"

    git_paths: list[str] = []
    ingest_success = bool(ingest_result) and str((ingest_result or {}).get("status") or "") in {"success", "partial"}
    skill_success = bool(skill_result) and str((skill_result or {}).get("status") or "") == "success"
    if ingest_success:
        ingest_touched = ((ingest_result or {}).get("result") or {}).get("touched_files") or []
        git_paths.extend([str(p) for p in ingest_touched if str(p).strip()])
    if skill_success:
        saved_files = ((skill_result or {}).get("result") or {}).get("saved_files") or []
        git_paths.extend([str(p) for p in saved_files if str(p).strip()])

    if settings.git_sync_enabled and (ingest_success or skill_success):
        sync_kind = "mixed" if ingest_success and skill_success else ("ingest" if ingest_success else "skill")
        git_sync_result = _run_git_sync_after_write(
            settings=settings,
            event_ref=event_ref_value,
            kind=sync_kind,
            paths=git_paths,
            dry_run=dry_run,
        )
    git_sync_error = bool(git_sync_result) and str((git_sync_result or {}).get("status") or "") == "error"
    if git_sync_error:
        sync_msg = str((git_sync_result or {}).get("message") or (git_sync_result or {}).get("stderr") or "").strip()
        errors.append(f"git sync failed: {sync_msg or 'unknown error'}")

    if ingest_result and ingest_result.get("status") in {"error", "partial"}:
        ingest_err = (ingest_result.get("result") or {}).get("errors") or ingest_result.get("errors") or []
        errors.extend(ingest_err)
        if not ingest_err:
            result_summary = ((ingest_result.get("result") or {}).get("details") or {}).get("summary") or {}
            link_total = int(result_summary.get("link_total") or 0)
            route_success = int(result_summary.get("link_route_success_count") or 0)
            content_success = int(result_summary.get("link_content_success_count") or 0)
            if link_total > 0 and route_success <= 0:
                errors.append(f"链接抓取失败：路由{route_success}/{link_total}")
            elif link_total > 0 and content_success <= 0:
                quality = str((ingest_result.get("result") or {}).get("link_quality_reason") or "").strip()
                if quality:
                    errors.append(f"链接正文不达标：{quality}")
    if skill_result and skill_result.get("status") == "error":
        skill_err = (skill_result.get("result") or {}).get("errors") or skill_result.get("errors") or []
        errors.extend(skill_err)
    if plain_chat_result and plain_chat_result.get("status") == "error":
        errors.extend(plain_chat_result.get("errors") or [])

    reply, reply_segments = _compose_reply(
        ingest=ingest_result,
        skill=skill_result,
        plain_chat=plain_chat_result,
        max_chars=settings.max_reply_chars,
    )
    elapsed_ms = int((time.time() - started) * 1000)

    output = {
        "status": status,
        "event_ref": event_ref_value,
        "source_ref": source_ref_value,
        "source_time": source_time_value,
        "source_user": source_user_value,
        "intent": {
            "automation": False,
            "automation_kind": "",
            "ingest": will_ingest,
            "skill": will_skill,
            "urls": urls,
            "skill_id": (skill_intent.skill_id if skill_intent else ""),
            "skill_platform": (skill_intent.platform if skill_intent else ""),
            "skill_trigger": (skill_intent.trigger if skill_intent else ""),
            "ingest_trigger": ingest_trigger,
        },
        "ingest_trigger": ingest_trigger,
        "plain_chat_fallback_used": plain_chat_fallback_used,
        "ingest": ingest_result,
        "skill": skill_result,
        "plain_chat": plain_chat_result,
        "git_sync": git_sync_result,
        "reply": reply,
        "reply_segments": reply_segments,
        "errors": errors,
        "elapsed_ms": elapsed_ms,
    }

    run_log = RUN_LOG_DIR / f"{_today()}.jsonl"
    ingest_payload = (ingest_result or {}).get("result") or {}
    _append_jsonl(
        run_log,
        {
            "ts": _now_iso(),
            "event_ref": event_ref_value,
            "status": status,
            "intent": output["intent"],
            "ingest_trigger": ingest_trigger,
            "plain_chat_fallback_used": plain_chat_fallback_used,
            "git_sync_status": (git_sync_result or {}).get("status"),
            "git_sync_commit": (git_sync_result or {}).get("commit", ""),
            "link_route_status": ingest_payload.get("link_route_status"),
            "link_content_status": ingest_payload.get("link_content_status"),
            "link_content_chars": ingest_payload.get("link_content_chars"),
            "link_provider": ingest_payload.get("link_provider"),
            "link_is_test": ingest_payload.get("link_is_test"),
            "link_quality_reason": ingest_payload.get("link_quality_reason"),
            "link_summary_detected": ingest_payload.get("link_summary_detected"),
            "link_text_source": ingest_payload.get("link_text_source"),
            "link_reject_reason": ingest_payload.get("link_reject_reason"),
            "elapsed_ms": elapsed_ms,
            "errors": errors,
        },
    )
    output["run_log"] = run_log.relative_to(REPO_ROOT).as_posix()

    needs_dead_letter = (status in {"error", "partial"} and errors) or git_sync_error
    if needs_dead_letter:
        dead_log = DEAD_LETTER_DIR / f"{_today()}.jsonl"
        _append_jsonl(
            dead_log,
            {
                "ts": _now_iso(),
                "event_ref": event_ref_value,
                "text": message_text,
                "output": output,
                "reason": "git_sync_error" if git_sync_error else "orchestrator_error",
            },
        )
        output["dead_letter_log"] = dead_log.relative_to(REPO_ROOT).as_posix()

    return output


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Feishu KB orchestrator")
    parser.add_argument("--text", default="", help="Raw Feishu text content.")
    parser.add_argument("--event-ref", default="", help="External idempotency key.")
    parser.add_argument("--source-ref", default="", help="Source trace id.")
    parser.add_argument("--source-time", default="", help="Source ISO timestamp.")
    parser.add_argument("--source-user", default="", help="Source open_id/user id for approval whitelist.")
    parser.add_argument("--meta-json", default="", help="Optional extra metadata JSON object.")
    parser.add_argument("--skill-id", default="", help="Force skill id.")
    parser.add_argument("--platform", default="", help="Force platform for skill generation.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call writer/codex, only plan output.")
    parser.add_argument(
        "--pipeline-mode",
        choices=("off", "once", "daemon"),
        default="off",
        help="Run topic pipeline mode instead of message routing.",
    )
    parser.add_argument(
        "--pipeline-dry-run",
        action="store_true",
        help="Topic pipeline dry-run: no file mutation and no generation call.",
    )
    parser.add_argument(
        "--pipeline-force-batch",
        action="store_true",
        help="Topic pipeline: force batch execution regardless of daily window.",
    )
    parser.add_argument(
        "--pipeline-poll-sec",
        type=int,
        default=60,
        help="Topic pipeline daemon poll interval in seconds. Default: 60",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.pipeline_mode != "off":
            settings = _load_settings()
            if args.pipeline_mode == "once":
                result = run_pipeline_once(
                    dry_run=args.pipeline_dry_run,
                    force_batch=args.pipeline_force_batch,
                    batch_limit=3,
                    model=settings.codex_model,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result.get("status") in {"success", "partial"} else 1

            print(
                json.dumps(
                    {
                        "status": "running",
                        "mode": "pipeline-daemon",
                        "poll_seconds": max(5, int(args.pipeline_poll_sec)),
                        "dry_run": bool(args.pipeline_dry_run),
                        "force_batch": bool(args.pipeline_force_batch),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            run_pipeline_daemon(
                poll_seconds=max(5, int(args.pipeline_poll_sec)),
                dry_run=args.pipeline_dry_run,
                force_batch=args.pipeline_force_batch,
                batch_limit=3,
                model=settings.codex_model,
            )
            return 0

        result = orchestrate_message(
            text=args.text,
            event_ref=args.event_ref,
            source_ref=args.source_ref,
            source_time=args.source_time,
            source_user=args.source_user,
            meta=json.loads(args.meta_json) if str(args.meta_json or "").strip() else None,
            forced_skill_id=args.skill_id,
            forced_platform=args.platform,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in {"success", "partial", "ignored", "queued"} else 1
    except KeyboardInterrupt:
        print(json.dumps({"status": "stopped", "reason": "keyboard_interrupt"}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        if str(os.getenv("ORCHESTRATOR_RAISE_ON_ERROR") or "").strip().lower() in {"1", "true", "yes", "y", "on"}:
            raise
        err_text = f"处理失败：{exc}"
        print(
            json.dumps(
                {
                    "status": "error",
                    "reply": err_text,
                    "reply_segments": [err_text],
                    "errors": [str(exc)],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

