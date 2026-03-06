#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Internal Writer API for OpenClaw -> repo ingestion.

This service is intentionally decoupled from Feishu callbacks. It only accepts
signed internal requests and writes into the repository using existing ingest
rules from feishu_ingest_router.py.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from automation_maintenance_runner import run_maintenance
import automation_state as _automation_state
from feishu_ingest_router import process_message
from media_task_runner import run_media_task
from metrics_runner import run_metrics
from publish_action_runner import approve_publish, prepare_publish, retry_publish_task
from quote_ingest_core import DEFAULT_NEAR_DUP_THRESHOLD
from retro_runner import run_retro

get_task = _automation_state.get_task


def _list_tasks_safe(**kwargs: Any) -> list[dict[str, Any]]:
    fn = getattr(_automation_state, "list_tasks", None)
    if callable(fn):
        return fn(**kwargs)
    return []


def _list_task_logs_safe(task_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    fn = getattr(_automation_state, "list_task_logs", None)
    if callable(fn):
        return fn(task_id, limit=limit)
    return []

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
SHORT_LINK_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:v\.douyin\.com|xhslink\.com|b23\.tv)/[A-Za-z0-9_-]+/?(?:\?[^\s<>\"'`]+)?",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
QUOTE_TRIGGER_RE = re.compile(
    r"^\s*(?:(?:\d+\s*[\.、]\s*)?(?:回复\s*)?)?(?:[@＠][^:\s：，,]+\s*(?:[:：]\s*|\s+).+|(?:金句|quote)\s*(?:[:：]\s*|\s+).+)\s*$"
)
SOURCE_KIND_ALIAS_MAP = {
    # Keep backward compatibility with older orchestrator sender.
    "feishu-orchestrator": "openclaw-feishu",
}


@dataclass(frozen=True)
class WriterSettings:
    shared_token: str
    hmac_secret: str
    apply_mode: bool
    near_dup_threshold: float
    report_tz: str
    auth_max_skew_seconds: int
    signature_required: bool
    allowed_source_kinds: set[str]
    link_min_content_chars: int
    link_allow_test_url_skip: bool


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _split_csv(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.replace(";", ",").replace("\n", ",").split(",") if item.strip()}


def _normalize_source_kind_value(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return SOURCE_KIND_ALIAS_MAP.get(raw, raw)


def _load_env_fallbacks() -> None:
    required = {"INGEST_SHARED_TOKEN", "INGEST_HMAC_SECRET"}
    missing = [k for k in required if not (os.getenv(k) or "").strip()]
    if not missing:
        return

    script_dir = Path(__file__).resolve().parent
    candidates = (
        script_dir / ".env.ingest-writer.local",
        script_dir / ".env.ingest-writer",
        script_dir / ".env.feishu",
    )
    for env_path in candidates:
        if not env_path.exists():
            continue
        text = env_path.read_text(encoding="utf-8", errors="ignore")
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and not (os.getenv(key) or "").strip():
                os.environ[key] = value
        missing = [k for k in required if not (os.getenv(k) or "").strip()]
        if not missing:
            return


def _load_settings() -> WriterSettings:
    _load_env_fallbacks()
    shared_token = os.getenv("INGEST_SHARED_TOKEN", "").strip()
    hmac_secret = os.getenv("INGEST_HMAC_SECRET", "").strip() or shared_token
    return WriterSettings(
        shared_token=shared_token,
        hmac_secret=hmac_secret,
        apply_mode=_as_bool(os.getenv("INGEST_APPLY_MODE"), default=True),
        near_dup_threshold=float(os.getenv("INGEST_NEAR_DUP_THRESHOLD", str(DEFAULT_NEAR_DUP_THRESHOLD))),
        report_tz=os.getenv("INGEST_REPORT_DATE_TZ", "Asia/Shanghai").strip() or "Asia/Shanghai",
        auth_max_skew_seconds=max(30, int(os.getenv("INGEST_AUTH_MAX_SKEW_SECONDS", "600"))),
        signature_required=_as_bool(os.getenv("INGEST_SIGNATURE_REQUIRED"), default=True),
        allowed_source_kinds=_split_csv(os.getenv("INGEST_ALLOWED_SOURCE_KINDS")),
        link_min_content_chars=max(1, int(os.getenv("INGEST_LINK_MIN_CONTENT_CHARS", "120"))),
        link_allow_test_url_skip=_as_bool(os.getenv("INGEST_LINK_ALLOW_TEST_SKIP"), default=True),
    )


SETTINGS = _load_settings()
REPO_ROOT = _repo_root()
QUOTE_DIR = REPO_ROOT / "03-素材库" / "金句库"
TOPIC_POOL_PATH = REPO_ROOT / "01-选题管理" / "选题规划" / "金句选题池.md"
IMPORT_RECORD_DIR = QUOTE_DIR / "导入记录"
LINK_LOG_DIR = REPO_ROOT / "03-素材库" / "对标链接库"
STATE_DB = REPO_ROOT / "06-工具" / "data" / "ingest-writer" / "writer_state.db"

DB_LOCK = threading.Lock()
DB_READY = False
app = FastAPI(title="OpenClaw Ingest Writer API", version="1.0.0")

REQUEST_EXTRA_COLUMNS: dict[str, str] = {
    "route_status": "TEXT",
    "content_status": "TEXT",
    "content_chars": "INTEGER",
    "provider": "TEXT",
    "is_test_url": "INTEGER",
    "quality_reason": "TEXT",
    "summary_detected": "INTEGER",
    "text_source": "TEXT",
    "reject_reason": "TEXT",
}


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat(timespec="seconds")


def _today_str() -> str:
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(SETTINGS.report_tz)
        return dt.datetime.now(zone).date().isoformat()
    except Exception:
        return dt.date.today().isoformat()


def _extract_date(value: str) -> str:
    matched = DATE_RE.search(str(value or ""))
    return matched.group(0) if matched else _today_str()


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


def _build_link_input_text(text: str, urls: list[str]) -> str:
    """
    Keep original message text, but always include at least one raw URL line.
    This prevents link-mode from being downgraded to ignore when rich messages
    provide URLs in metadata/payload only.
    """
    base = str(text or "").strip()
    if not base:
        return "\n".join(urls)
    if _extract_urls(base):
        return base
    return f"{base}\n" + "\n".join(urls)


def _strip_urls(text: str) -> str:
    cleaned = URL_RE.sub(" ", str(text or ""))
    cleaned = SHORT_LINK_RE.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _ensure_quote_trigger_text(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    if QUOTE_TRIGGER_RE.match(value):
        return value
    return f"金句：{value}"


def _build_signature(*, timestamp: str, nonce: str, body: bytes) -> str:
    payload = f"{timestamp}\n{nonce}\n".encode("utf-8") + body
    digest = hmac.new(SETTINGS.hmac_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return digest


def _verify_timestamp(timestamp: str) -> bool:
    if not timestamp:
        return False
    try:
        ts = int(timestamp)
    except Exception:
        return False
    now = int(_now_utc().timestamp())
    return abs(now - ts) <= SETTINGS.auth_max_skew_seconds


def _verify_auth(request: Request, body: bytes) -> None:
    if not SETTINGS.shared_token:
        raise HTTPException(status_code=500, detail="INGEST_SHARED_TOKEN not configured")
    if not SETTINGS.hmac_secret and SETTINGS.signature_required:
        raise HTTPException(status_code=500, detail="INGEST_HMAC_SECRET not configured")

    auth = request.headers.get("Authorization", "")
    expected_auth = f"Bearer {SETTINGS.shared_token}"
    if auth != expected_auth:
        raise HTTPException(status_code=403, detail="invalid bearer token")

    timestamp = request.headers.get("X-Ingest-Timestamp", "").strip()
    nonce = request.headers.get("X-Ingest-Nonce", "").strip()
    signature = request.headers.get("X-Ingest-Signature", "").strip()

    if not _verify_timestamp(timestamp):
        raise HTTPException(status_code=403, detail="invalid timestamp")
    if not nonce:
        raise HTTPException(status_code=403, detail="missing nonce")

    if SETTINGS.signature_required:
        expected = _build_signature(timestamp=timestamp, nonce=nonce, body=body)
        if not signature or not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=403, detail="invalid signature")


def _ensure_request_columns(conn: sqlite3.Connection) -> None:
    current = {
        str(row[1]).strip()
        for row in conn.execute("PRAGMA table_info(requests)").fetchall()
        if len(row) >= 2 and str(row[1]).strip()
    }
    for name, dtype in REQUEST_EXTRA_COLUMNS.items():
        if name in current:
            continue
        conn.execute(f"ALTER TABLE requests ADD COLUMN {name} {dtype}")


def _link_fields_from_result(result: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(result or {})
    return {
        "route_status": str(payload.get("link_route_status") or "").strip(),
        "content_status": str(payload.get("link_content_status") or "").strip(),
        "content_chars": int(payload.get("link_content_chars") or 0),
        "provider": str(payload.get("link_provider") or "").strip(),
        "is_test_url": 1 if bool(payload.get("link_is_test")) else 0,
        "quality_reason": str(payload.get("link_quality_reason") or "").strip(),
        "summary_detected": 1 if bool(payload.get("link_summary_detected")) else 0,
        "text_source": str(payload.get("link_text_source") or "").strip(),
        "reject_reason": str(payload.get("link_reject_reason") or "").strip(),
    }


def _init_db() -> None:
    global DB_READY
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(STATE_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                event_ref TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT,
                error_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dead_letter (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_ref TEXT,
                mode TEXT,
                error_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        _ensure_request_columns(conn)
        conn.commit()
    DB_READY = True


def _ensure_db_ready() -> None:
    if DB_READY:
        return
    _init_db()


def _db_get(event_ref: str) -> dict[str, Any] | None:
    _ensure_db_ready()
    with DB_LOCK:
        with sqlite3.connect(STATE_DB) as conn:
            row = conn.execute(
                "SELECT event_ref, mode, status, request_json, result_json, error_text, created_at, updated_at, "
                "route_status, content_status, content_chars, provider, is_test_url, quality_reason, "
                "summary_detected, text_source, reject_reason "
                "FROM requests WHERE event_ref = ?",
                (event_ref,),
            ).fetchone()
    if not row:
        return None
    return {
        "event_ref": row[0],
        "mode": row[1],
        "status": row[2],
        "request_json": row[3],
        "result_json": row[4],
        "error_text": row[5],
        "created_at": row[6],
        "updated_at": row[7],
        "route_status": row[8],
        "content_status": row[9],
        "content_chars": row[10],
        "provider": row[11],
        "is_test_url": row[12],
        "quality_reason": row[13],
        "summary_detected": row[14],
        "text_source": row[15],
        "reject_reason": row[16],
    }


def _db_upsert(
    *,
    event_ref: str,
    mode: str,
    status: str,
    request_json: str,
    result_json: str = "",
    error_text: str = "",
    route_status: str = "",
    content_status: str = "",
    content_chars: int = 0,
    provider: str = "",
    is_test_url: int = 0,
    quality_reason: str = "",
    summary_detected: int = 0,
    text_source: str = "",
    reject_reason: str = "",
) -> None:
    _ensure_db_ready()
    now = _now_iso()
    with DB_LOCK:
        with sqlite3.connect(STATE_DB) as conn:
            conn.execute(
                """
                INSERT INTO requests(
                  event_ref, mode, status, request_json, result_json, error_text, created_at, updated_at,
                  route_status, content_status, content_chars, provider, is_test_url, quality_reason,
                  summary_detected, text_source, reject_reason
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_ref) DO UPDATE SET
                  mode=excluded.mode,
                  status=excluded.status,
                  request_json=excluded.request_json,
                  result_json=excluded.result_json,
                  error_text=excluded.error_text,
                  route_status=excluded.route_status,
                  content_status=excluded.content_status,
                  content_chars=excluded.content_chars,
                  provider=excluded.provider,
                  is_test_url=excluded.is_test_url,
                  quality_reason=excluded.quality_reason,
                  summary_detected=excluded.summary_detected,
                  text_source=excluded.text_source,
                  reject_reason=excluded.reject_reason,
                  updated_at=excluded.updated_at
                """,
                (
                    event_ref,
                    mode,
                    status,
                    request_json,
                    result_json,
                    error_text,
                    now,
                    now,
                    route_status,
                    content_status,
                    int(content_chars or 0),
                    provider,
                    int(is_test_url or 0),
                    quality_reason,
                    int(summary_detected or 0),
                    text_source,
                    reject_reason,
                ),
            )
            conn.commit()


def _db_dead_letter(event_ref: str, mode: str, error_text: str) -> None:
    _ensure_db_ready()
    with DB_LOCK:
        with sqlite3.connect(STATE_DB) as conn:
            conn.execute(
                "INSERT INTO dead_letter(event_ref, mode, error_text, created_at) VALUES (?, ?, ?, ?)",
                (event_ref, mode, error_text, _now_iso()),
            )
            conn.commit()


def _make_result(
    *,
    event_ref: str,
    mode: str,
    status: str,
    added: int = 0,
    near_dup: int = 0,
    skipped: int = 0,
    touched_files: list[str] | None = None,
    errors: list[str] | None = None,
    details: dict[str, Any] | None = None,
    link_route_status: str = "",
    link_content_status: str = "",
    link_content_chars: int = 0,
    link_provider: str = "",
    link_is_test: bool = False,
    link_quality_reason: str = "",
    link_summary_detected: bool = False,
    link_text_source: str = "",
    link_reject_reason: str = "",
) -> dict[str, Any]:
    return {
        "event_ref": event_ref,
        "mode": mode,
        "status": status,
        "added": max(0, int(added)),
        "near_dup": max(0, int(near_dup)),
        "skipped": max(0, int(skipped)),
        "touched_files": sorted(set(touched_files or [])),
        "errors": errors or [],
        "details": details or {},
        "link_route_status": link_route_status,
        "link_content_status": link_content_status,
        "link_content_chars": max(0, int(link_content_chars or 0)),
        "link_provider": link_provider,
        "link_is_test": bool(link_is_test),
        "link_quality_reason": link_quality_reason,
        "link_summary_detected": bool(link_summary_detected),
        "link_text_source": link_text_source,
        "link_reject_reason": link_reject_reason,
    }


def _normalize_source(payload: dict[str, Any], *, default_ref: str) -> tuple[str, str, str]:
    source_time = str(payload.get("source_time") or "").strip() or _now_iso()
    source_kind = _normalize_source_kind_value(payload.get("source_kind")) or "openclaw"
    source_ref = str(payload.get("source_ref") or "").strip() or default_ref
    return source_time, source_kind, source_ref


def _check_source_kind(source_kind: str) -> None:
    if not SETTINGS.allowed_source_kinds:
        return
    allowed = {_normalize_source_kind_value(item) for item in SETTINGS.allowed_source_kinds}
    if _normalize_source_kind_value(source_kind) not in allowed:
        raise HTTPException(status_code=403, detail=f"source_kind not allowed: {source_kind}")


def _message_summary_to_result(event_ref: str, mode: str, summary: Any) -> dict[str, Any]:
    summary_dict = dataclasses.asdict(summary)
    # notify_status is resolved by async worker; keep the field in summary for schema stability.
    if "notify_status" not in summary_dict:
        summary_dict["notify_status"] = ""
    if mode == "quote":
        return _make_result(
            event_ref=event_ref,
            mode=mode,
            status="success" if not summary.errors else "partial",
            added=summary.quote_added_count,
            near_dup=summary.quote_near_dup_count,
            skipped=summary.quote_exact_dup_count,
            touched_files=summary.touched_files,
            errors=summary.errors,
            details={"summary": summary_dict},
            link_route_status=summary.link_route_status,
            link_content_status=summary.link_content_status,
            link_content_chars=summary.link_content_chars_total,
            link_provider=summary.link_provider,
            link_is_test=summary.link_is_test,
            link_quality_reason=summary.link_quality_reason,
            link_summary_detected=summary.link_summary_detected,
            link_text_source=summary.link_text_source,
            link_reject_reason=summary.link_reject_reason,
        )
    return _make_result(
        event_ref=event_ref,
        mode=mode,
        status="success" if not summary.errors else "partial",
        added=summary.link_content_success_count,
        near_dup=0,
        skipped=summary.link_content_failed_count + summary.link_content_skipped_test_count,
        touched_files=summary.touched_files,
        errors=summary.errors,
        details={"summary": summary_dict},
        link_route_status=summary.link_route_status,
        link_content_status=summary.link_content_status,
        link_content_chars=summary.link_content_chars_total,
        link_provider=summary.link_provider,
        link_is_test=summary.link_is_test,
        link_quality_reason=summary.link_quality_reason,
        link_summary_detected=summary.link_summary_detected,
        link_text_source=summary.link_text_source,
        link_reject_reason=summary.link_reject_reason,
    )


def _process_quote(*, event_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
    source_time, source_kind, source_ref = _normalize_source(payload, default_ref=event_ref)
    _check_source_kind(source_kind)

    text = _strip_urls(str(payload.get("text") or ""))
    quote_input_text = _ensure_quote_trigger_text(text)
    if not quote_input_text:
        return _make_result(
            event_ref=event_ref,
            mode="quote",
            status="ignored",
            skipped=1,
            errors=["empty quote text"],
        )

    import_record_path = IMPORT_RECORD_DIR / f"{_extract_date(source_time)}-feishu-import.md"
    link_log_path = LINK_LOG_DIR / f"{_extract_date(source_time)}-feishu-links.md"

    summary = process_message(
        text=quote_input_text,
        quote_dir=QUOTE_DIR,
        topic_pool_path=TOPIC_POOL_PATH,
        import_record_path=import_record_path,
        link_log_path=link_log_path,
        apply_mode=SETTINGS.apply_mode,
        source_time=source_time,
        source_ref=source_ref,
        near_dup_threshold=max(0.1, min(0.99, SETTINGS.near_dup_threshold)),
        min_content_chars=SETTINGS.link_min_content_chars,
        allow_test_url_skip=SETTINGS.link_allow_test_url_skip,
    )
    return _message_summary_to_result(event_ref, "quote", summary)


def _process_link(*, event_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
    source_time, source_kind, source_ref = _normalize_source(payload, default_ref=event_ref)
    _check_source_kind(source_kind)

    text = str(payload.get("text") or "")
    urls_from_text = _extract_urls(text)
    urls_from_payload = [str(item) for item in (payload.get("urls") or [])]
    urls = _dedupe_urls(urls_from_payload + urls_from_text)
    if not urls:
        return _make_result(
            event_ref=event_ref,
            mode="link",
            status="ignored",
            skipped=1,
            errors=["no valid url found"],
        )

    import_record_path = IMPORT_RECORD_DIR / f"{_extract_date(source_time)}-feishu-import.md"
    link_log_path = LINK_LOG_DIR / f"{_extract_date(source_time)}-feishu-links.md"

    link_input_text = _build_link_input_text(text, urls)
    summary = process_message(
        text=link_input_text,
        quote_dir=QUOTE_DIR,
        topic_pool_path=TOPIC_POOL_PATH,
        import_record_path=import_record_path,
        link_log_path=link_log_path,
        apply_mode=SETTINGS.apply_mode,
        source_time=source_time,
        source_ref=source_ref,
        near_dup_threshold=max(0.1, min(0.99, SETTINGS.near_dup_threshold)),
        min_content_chars=SETTINGS.link_min_content_chars,
        allow_test_url_skip=SETTINGS.link_allow_test_url_skip,
    )
    return _message_summary_to_result(event_ref, "link", summary)


def _process_mixed(*, event_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
    source_time, source_kind, source_ref = _normalize_source(payload, default_ref=event_ref)
    _check_source_kind(source_kind)

    text = str(payload.get("text") or "")
    urls_from_text = _extract_urls(text)
    urls_from_payload = [str(item) for item in (payload.get("urls") or [])]
    urls = _dedupe_urls(urls_from_payload + urls_from_text)
    quote_text = _strip_urls(text)
    quote_input_text = _ensure_quote_trigger_text(quote_text) if quote_text else ""

    if not urls and not quote_input_text:
        return _make_result(
            event_ref=event_ref,
            mode="mixed",
            status="ignored",
            skipped=1,
            errors=["both quote and urls are empty"],
        )

    import_record_path = IMPORT_RECORD_DIR / f"{_extract_date(source_time)}-feishu-import.md"
    link_log_path = LINK_LOG_DIR / f"{_extract_date(source_time)}-feishu-links.md"

    touched_files: set[str] = set()
    errors: list[str] = []
    details: dict[str, Any] = {}

    added = 0
    near_dup = 0
    skipped = 0

    if quote_input_text:
        quote_summary = process_message(
            text=quote_input_text,
            quote_dir=QUOTE_DIR,
            topic_pool_path=TOPIC_POOL_PATH,
            import_record_path=import_record_path,
            link_log_path=link_log_path,
            apply_mode=SETTINGS.apply_mode,
            source_time=source_time,
            source_ref=f"{source_ref}#quote",
            near_dup_threshold=max(0.1, min(0.99, SETTINGS.near_dup_threshold)),
            min_content_chars=SETTINGS.link_min_content_chars,
            allow_test_url_skip=SETTINGS.link_allow_test_url_skip,
        )
        details["quote"] = dataclasses.asdict(quote_summary)
        touched_files.update(quote_summary.touched_files)
        errors.extend(quote_summary.errors)
        added += quote_summary.quote_added_count
        near_dup += quote_summary.quote_near_dup_count
        skipped += quote_summary.quote_exact_dup_count

    if urls:
        link_input_text = _build_link_input_text(text, urls)
        link_summary = process_message(
            text=link_input_text,
            quote_dir=QUOTE_DIR,
            topic_pool_path=TOPIC_POOL_PATH,
            import_record_path=import_record_path,
            link_log_path=link_log_path,
            apply_mode=SETTINGS.apply_mode,
            source_time=source_time,
            source_ref=f"{source_ref}#link",
            near_dup_threshold=max(0.1, min(0.99, SETTINGS.near_dup_threshold)),
            min_content_chars=SETTINGS.link_min_content_chars,
            allow_test_url_skip=SETTINGS.link_allow_test_url_skip,
        )
        details["link"] = dataclasses.asdict(link_summary)
        touched_files.update(link_summary.touched_files)
        errors.extend(link_summary.errors)
        added += link_summary.link_content_success_count
        skipped += link_summary.link_content_failed_count + link_summary.link_content_skipped_test_count

    return _make_result(
        event_ref=event_ref,
        mode="mixed",
        status="success" if not errors else "partial",
        added=added,
        near_dup=near_dup,
        skipped=skipped,
        touched_files=sorted(touched_files),
        errors=errors,
        details=details,
        link_route_status=(details.get("link") or {}).get("link_route_status", ""),
        link_content_status=(details.get("link") or {}).get("link_content_status", ""),
        link_content_chars=int((details.get("link") or {}).get("link_content_chars_total", 0) or 0),
        link_provider=str((details.get("link") or {}).get("link_provider") or ""),
        link_is_test=bool((details.get("link") or {}).get("link_is_test")),
        link_quality_reason=str((details.get("link") or {}).get("link_quality_reason") or ""),
        link_summary_detected=bool((details.get("link") or {}).get("link_summary_detected")),
        link_text_source=str((details.get("link") or {}).get("link_text_source") or ""),
        link_reject_reason=str((details.get("link") or {}).get("link_reject_reason") or ""),
    )


def _json_body_or_400(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="json body must be an object")
    return payload


def _event_ref_or_400(payload: dict[str, Any]) -> str:
    event_ref = str(payload.get("event_ref") or "").strip()
    if not event_ref:
        raise HTTPException(status_code=400, detail="event_ref is required")
    return event_ref


def _maybe_duplicate_response(event_ref: str) -> JSONResponse | None:
    existing = _db_get(event_ref)
    if not existing:
        return None
    if existing["status"] not in {"success", "partial", "ignored"}:
        return None

    result = {}
    if existing["result_json"]:
        try:
            result = json.loads(existing["result_json"])
        except Exception:
            result = {"status": existing["status"], "errors": ["result_json decode failed"]}
    return JSONResponse(content={"code": 0, "msg": "ok", "event_ref": event_ref, "duplicate": True, "result": result})


def _handle_ingest(mode: str, payload: dict[str, Any], *, allow_duplicate_skip: bool) -> dict[str, Any]:
    event_ref = _event_ref_or_400(payload)
    request_json = json.dumps(payload, ensure_ascii=False)

    if allow_duplicate_skip:
        duplicate = _maybe_duplicate_response(event_ref)
        if duplicate is not None:
            # Caller endpoint converts this into direct response.
            return {"__duplicate_response__": duplicate}

    _db_upsert(event_ref=event_ref, mode=mode, status="pending", request_json=request_json)

    try:
        if mode == "quote":
            result = _process_quote(event_ref=event_ref, payload=payload)
        elif mode == "link":
            result = _process_link(event_ref=event_ref, payload=payload)
        elif mode == "mixed":
            result = _process_mixed(event_ref=event_ref, payload=payload)
        else:
            raise RuntimeError(f"unsupported mode: {mode}")

        _db_upsert(
            event_ref=event_ref,
            mode=mode,
            status=str(result.get("status") or "unknown"),
            request_json=request_json,
            result_json=json.dumps(result, ensure_ascii=False),
            **_link_fields_from_result(result),
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        error_text = str(exc)
        _db_upsert(
            event_ref=event_ref,
            mode=mode,
            status="error",
            request_json=request_json,
            result_json="",
            error_text=error_text,
            **_link_fields_from_result(None),
        )
        _db_dead_letter(event_ref, mode, error_text)
        raise HTTPException(status_code=500, detail=error_text) from exc


def _payload_bool(payload: dict[str, Any], key: str, *, default: bool = False) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _run_media(payload: dict[str, Any]) -> dict[str, Any]:
    return run_media_task(payload, dry_run=_payload_bool(payload, "dry_run", default=False))


def _run_publish_prepare(payload: dict[str, Any]) -> dict[str, Any]:
    return prepare_publish(payload, dry_run=_payload_bool(payload, "dry_run", default=False))


def _run_publish_approve(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "approve").strip().lower()
    dry_run = _payload_bool(payload, "dry_run", default=False)
    if action == "retry":
        return retry_publish_task(payload, dry_run=dry_run)
    return approve_publish(payload, dry_run=dry_run)


def _run_metrics_runner(payload: dict[str, Any]) -> dict[str, Any]:
    return run_metrics(payload, dry_run=_payload_bool(payload, "dry_run", default=False))


def _run_retro_runner(payload: dict[str, Any]) -> dict[str, Any]:
    return run_retro(payload, dry_run=_payload_bool(payload, "dry_run", default=False))


def _run_metrics_backfill(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    data.setdefault("action", "backfill")
    return run_metrics(data, dry_run=_payload_bool(payload, "dry_run", default=False))


def _run_retro_backfill(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    data.setdefault("action", "backfill")
    return run_retro(data, dry_run=_payload_bool(payload, "dry_run", default=False))


def _run_maintenance_runner(payload: dict[str, Any]) -> dict[str, Any]:
    return run_maintenance(payload, dry_run=_payload_bool(payload, "dry_run", default=False))


@app.on_event("startup")
def _startup() -> None:
    _init_db()


@app.get("/internal/healthz")
def internal_healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "db": STATE_DB.as_posix(),
        "repo_root": REPO_ROOT.as_posix(),
        "apply_mode": SETTINGS.apply_mode,
        "signature_required": SETTINGS.signature_required,
        "auth_max_skew_seconds": SETTINGS.auth_max_skew_seconds,
        "allowed_source_kinds": sorted(SETTINGS.allowed_source_kinds),
        "link_min_content_chars": SETTINGS.link_min_content_chars,
        "link_allow_test_url_skip": SETTINGS.link_allow_test_url_skip,
    }


@app.post("/internal/ingest/v1/quote")
async def ingest_quote(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)
    result = _handle_ingest("quote", payload, allow_duplicate_skip=True)
    duplicate_response = result.get("__duplicate_response__")
    if duplicate_response is not None:
        return duplicate_response
    return JSONResponse(content={"code": 0, "msg": "ok", "event_ref": result["event_ref"], "result": result})


@app.post("/internal/ingest/v1/link")
async def ingest_link(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)
    result = _handle_ingest("link", payload, allow_duplicate_skip=True)
    duplicate_response = result.get("__duplicate_response__")
    if duplicate_response is not None:
        return duplicate_response
    return JSONResponse(content={"code": 0, "msg": "ok", "event_ref": result["event_ref"], "result": result})


@app.post("/internal/ingest/v1/mixed")
async def ingest_mixed(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)
    result = _handle_ingest("mixed", payload, allow_duplicate_skip=True)
    duplicate_response = result.get("__duplicate_response__")
    if duplicate_response is not None:
        return duplicate_response
    return JSONResponse(content={"code": 0, "msg": "ok", "event_ref": result["event_ref"], "result": result})


@app.post("/internal/ingest/v1/replay")
async def replay_ingest(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)

    # Replay from stored payload by event_ref, or process supplied payload directly.
    event_ref = _event_ref_or_400(payload)
    replay_mode = str(payload.get("mode") or "").strip().lower()

    if replay_mode in {"quote", "link", "mixed"}:
        result = _handle_ingest(replay_mode, payload, allow_duplicate_skip=False)
        return JSONResponse(content={"code": 0, "msg": "ok", "event_ref": event_ref, "result": result, "replay": True})

    row = _db_get(event_ref)
    if not row:
        raise HTTPException(status_code=404, detail="event_ref not found")
    if not row.get("request_json"):
        raise HTTPException(status_code=404, detail="request payload not found")

    try:
        replay_payload = json.loads(row["request_json"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"stored request_json decode failed: {exc}") from exc

    if not isinstance(replay_payload, dict):
        raise HTTPException(status_code=500, detail="stored payload is invalid")
    replay_payload["event_ref"] = event_ref
    replay_mode = str(row.get("mode") or "").strip().lower()
    if replay_mode not in {"quote", "link", "mixed"}:
        raise HTTPException(status_code=500, detail=f"stored mode invalid: {replay_mode}")

    result = _handle_ingest(replay_mode, replay_payload, allow_duplicate_skip=False)
    return JSONResponse(content={"code": 0, "msg": "ok", "event_ref": event_ref, "result": result, "replay": True})


def _automation_response(result: dict[str, Any]) -> JSONResponse:
    code = 0 if str(result.get("status") or "") in {"success", "partial", "duplicate"} else 1
    msg = "ok" if code == 0 else "failed"
    return JSONResponse(content={"code": code, "msg": msg, "result": result})


@app.post("/internal/media/generate")
async def media_generate(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)
    try:
        return _automation_response(_run_media(payload))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/internal/publish/prepare")
async def publish_prepare(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)
    try:
        return _automation_response(_run_publish_prepare(payload))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/internal/publish/approve")
async def publish_approve(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)
    try:
        return _automation_response(_run_publish_approve(payload))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/internal/metrics/run")
async def metrics_run(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)
    try:
        return _automation_response(_run_metrics_runner(payload))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/internal/retro/run")
async def retro_run(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)
    try:
        return _automation_response(_run_retro_runner(payload))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/internal/metrics/backfill")
async def metrics_backfill(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)
    try:
        return _automation_response(_run_metrics_backfill(payload))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/internal/retro/backfill")
async def retro_backfill(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)
    try:
        return _automation_response(_run_retro_backfill(payload))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/internal/maintenance/run")
async def maintenance_run(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)
    try:
        return _automation_response(_run_maintenance_runner(payload))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _split_csv(text: str) -> list[str]:
    return [item.strip() for item in str(text or "").replace(";", ",").split(",") if item.strip()]


@app.get("/internal/tasks")
async def list_task_items(
    request: Request,
    task_type: str = "",
    status: str = "",
    date: str = "",
    biz_date: str = "",
    platform: str = "",
    account: str = "",
    event_ref: str = "",
    task_id: str = "",
    dedupe_key: str = "",
    limit: int = 100,
) -> JSONResponse:
    _verify_auth(request, b"")
    tasks = _list_tasks_safe(
        task_type=task_type.strip(),
        statuses=_split_csv(status),
        date_prefix=date.strip(),
        biz_date=biz_date.strip(),
        platform=platform.strip(),
        account=account.strip(),
        event_ref=event_ref.strip(),
        task_id_like=task_id.strip(),
        dedupe_key=dedupe_key.strip(),
        limit=max(1, min(int(limit), 1000)),
    )
    return JSONResponse(content={"code": 0, "msg": "ok", "count": len(tasks), "tasks": tasks})


@app.get("/internal/tasks/{task_id}")
async def get_task_detail(task_id: str, request: Request, include_logs: bool = True, log_limit: int = 50) -> JSONResponse:
    _verify_auth(request, b"")
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    payload: dict[str, Any] = {"code": 0, "msg": "ok", "task": task}
    if include_logs:
        payload["task_logs"] = _list_task_logs_safe(task_id, limit=max(1, min(int(log_limit), 500)))
    return JSONResponse(content=payload)


@app.get("/internal/tasks/{task_id}/logs")
async def get_task_detail_logs(task_id: str, request: Request, limit: int = 200) -> JSONResponse:
    _verify_auth(request, b"")
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    logs = _list_task_logs_safe(task_id, limit=max(1, min(int(limit), 2000)))
    return JSONResponse(content={"code": 0, "msg": "ok", "task_id": task_id, "count": len(logs), "task_logs": logs})


@app.post("/internal/tasks/retry")
async def retry_task(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)
    if not str(payload.get("task_id") or "").strip():
        raise HTTPException(status_code=400, detail="task_id is required")
    try:
        return _automation_response(_run_publish_approve({"action": "retry", **payload}))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8790)
