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
from automation_state import get_task
from feishu_ingest_router import process_message
from media_task_runner import run_media_task
from metrics_runner import run_metrics
from publish_action_runner import approve_publish, prepare_publish, retry_publish_task
from quote_ingest_core import DEFAULT_NEAR_DUP_THRESHOLD
from retro_runner import run_retro

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
QUOTE_TRIGGER_RE = re.compile(
    r"^\s*(?:(?:\d+\s*[\.、]\s*)?(?:回复\s*)?)?(?:@[^:\s：，,]+\s*[:：]\s*.+|金句\s*[:：]\s*.+)\s*$"
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
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _extract_urls(text: str) -> list[str]:
    return _dedupe_urls(URL_RE.findall(str(text or "")))


def _strip_urls(text: str) -> str:
    cleaned = URL_RE.sub(" ", str(text or ""))
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
                "SELECT event_ref, mode, status, request_json, result_json, error_text, created_at, updated_at "
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
    }


def _db_upsert(
    *,
    event_ref: str,
    mode: str,
    status: str,
    request_json: str,
    result_json: str = "",
    error_text: str = "",
) -> None:
    _ensure_db_ready()
    now = _now_iso()
    with DB_LOCK:
        with sqlite3.connect(STATE_DB) as conn:
            conn.execute(
                """
                INSERT INTO requests(event_ref, mode, status, request_json, result_json, error_text, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_ref) DO UPDATE SET
                  mode=excluded.mode,
                  status=excluded.status,
                  request_json=excluded.request_json,
                  result_json=excluded.result_json,
                  error_text=excluded.error_text,
                  updated_at=excluded.updated_at
                """,
                (event_ref, mode, status, request_json, result_json, error_text, now, now),
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
        )
    return _make_result(
        event_ref=event_ref,
        mode=mode,
        status="success" if not summary.errors else "partial",
        added=summary.link_doc_saved_count,
        near_dup=0,
        skipped=summary.link_failed,
        touched_files=summary.touched_files,
        errors=summary.errors,
        details={"summary": summary_dict},
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

    summary = process_message(
        text="\n".join(urls),
        quote_dir=QUOTE_DIR,
        topic_pool_path=TOPIC_POOL_PATH,
        import_record_path=import_record_path,
        link_log_path=link_log_path,
        apply_mode=SETTINGS.apply_mode,
        source_time=source_time,
        source_ref=source_ref,
        near_dup_threshold=max(0.1, min(0.99, SETTINGS.near_dup_threshold)),
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
        )
        details["quote"] = dataclasses.asdict(quote_summary)
        touched_files.update(quote_summary.touched_files)
        errors.extend(quote_summary.errors)
        added += quote_summary.quote_added_count
        near_dup += quote_summary.quote_near_dup_count
        skipped += quote_summary.quote_exact_dup_count

    if urls:
        link_summary = process_message(
            text="\n".join(urls),
            quote_dir=QUOTE_DIR,
            topic_pool_path=TOPIC_POOL_PATH,
            import_record_path=import_record_path,
            link_log_path=link_log_path,
            apply_mode=SETTINGS.apply_mode,
            source_time=source_time,
            source_ref=f"{source_ref}#link",
            near_dup_threshold=max(0.1, min(0.99, SETTINGS.near_dup_threshold)),
        )
        details["link"] = dataclasses.asdict(link_summary)
        touched_files.update(link_summary.touched_files)
        errors.extend(link_summary.errors)
        added += link_summary.link_doc_saved_count
        skipped += link_summary.link_failed

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


@app.post("/internal/maintenance/run")
async def maintenance_run(request: Request) -> JSONResponse:
    body = await request.body()
    _verify_auth(request, body)
    payload = _json_body_or_400(body)
    try:
        return _automation_response(_run_maintenance_runner(payload))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/internal/tasks/{task_id}")
async def get_task_detail(task_id: str, request: Request) -> JSONResponse:
    _verify_auth(request, b"")
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return JSONResponse(content={"code": 0, "msg": "ok", "task": task})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8790)
