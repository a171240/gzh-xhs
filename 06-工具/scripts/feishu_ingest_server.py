#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feishu event callback server for quote/link auto-ingest."""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import hashlib
import hmac
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from feishu_ingest_router import build_short_reply, parse_feishu_text_content, process_message


@dataclass(frozen=True)
class ServerSettings:
    app_id: str
    app_secret: str
    verification_token: str
    encrypt_key: str
    allowed_chat_ids: set[str]
    allowed_open_ids: set[str]
    reply_enabled: bool
    apply_mode: bool
    near_dup_threshold: float
    report_tz: str
    signature_strict: bool
    link_min_content_chars: int
    link_allow_test_url_skip: bool


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on", "是"}


def _split_csv(value: str | None) -> set[str]:
    if not value:
        return set()
    out = set()
    for part in re_split_csv(value):
        item = part.strip()
        if item:
            out.add(item)
    return out


def re_split_csv(value: str) -> list[str]:
    return [part for part in value.replace("\n", ",").replace(";", ",").split(",")]


def _load_settings() -> ServerSettings:
    return ServerSettings(
        app_id=os.getenv("FEISHU_APP_ID", "").strip(),
        app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
        verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", "").strip(),
        encrypt_key=os.getenv("FEISHU_ENCRYPT_KEY", "").strip(),
        allowed_chat_ids=_split_csv(os.getenv("FEISHU_ALLOWED_CHAT_IDS")),
        allowed_open_ids=_split_csv(os.getenv("FEISHU_ALLOWED_OPEN_IDS")),
        reply_enabled=_as_bool(os.getenv("FEISHU_REPLY_ENABLED"), default=True),
        apply_mode=_as_bool(os.getenv("FEISHU_APPLY_MODE"), default=True),
        near_dup_threshold=float(os.getenv("FEISHU_NEAR_DUP_THRESHOLD", "0.88")),
        report_tz=os.getenv("FEISHU_REPORT_DATE_TZ", "Asia/Shanghai").strip() or "Asia/Shanghai",
        signature_strict=_as_bool(os.getenv("FEISHU_SIGNATURE_STRICT"), default=False),
        link_min_content_chars=max(1, int(os.getenv("INGEST_LINK_MIN_CONTENT_CHARS", "120"))),
        link_allow_test_url_skip=_as_bool(os.getenv("INGEST_LINK_ALLOW_TEST_SKIP"), default=True),
    )


SETTINGS = _load_settings()
REPO_ROOT = _repo_root()
QUOTE_DIR = REPO_ROOT / "03-素材库" / "金句库"
TOPIC_POOL_PATH = REPO_ROOT / "01-选题管理" / "选题规划" / "金句选题池.md"
IMPORT_RECORD_DIR = QUOTE_DIR / "导入记录"
LINK_LOG_DIR = REPO_ROOT / "03-素材库" / "对标链接库"
STATE_DB = REPO_ROOT / "06-工具" / "data" / "feishu-ingest" / "state.db"

DB_LOCK = threading.Lock()
TOKEN_CACHE: dict[str, Any] = {"token": "", "expire_at": 0.0}

app = FastAPI(title="Feishu Ingest Server", version="1.0.0")


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat(timespec="seconds")


def _today_str() -> str:
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(SETTINGS.report_tz)
        return dt.datetime.now(tz).date().isoformat()
    except Exception:
        return dt.date.today().isoformat()


def _init_db() -> None:
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(STATE_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dead_letter (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _db_get_event(event_id: str) -> dict[str, Any] | None:
    with DB_LOCK:
        with sqlite3.connect(STATE_DB) as conn:
            row = conn.execute(
                "SELECT event_id, status, request_json, result_json, created_at, updated_at FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
    if not row:
        return None
    return {
        "event_id": row[0],
        "status": row[1],
        "request_json": row[2],
        "result_json": row[3],
        "created_at": row[4],
        "updated_at": row[5],
    }


def _db_upsert_event(event_id: str, status: str, request_json: str, result_json: str = "") -> None:
    now = _now_iso()
    with DB_LOCK:
        with sqlite3.connect(STATE_DB) as conn:
            conn.execute(
                """
                INSERT INTO events(event_id, status, request_json, result_json, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                  status=excluded.status,
                  request_json=excluded.request_json,
                  result_json=excluded.result_json,
                  updated_at=excluded.updated_at
                """,
                (event_id, status, request_json, result_json, now, now),
            )
            conn.commit()


def _db_dead_letter(event_id: str, error_message: str) -> None:
    with DB_LOCK:
        with sqlite3.connect(STATE_DB) as conn:
            conn.execute(
                "INSERT INTO dead_letter(event_id, error_message, created_at) VALUES (?, ?, ?)",
                (event_id, error_message, _now_iso()),
            )
            conn.commit()


def _verify_token(payload: dict[str, Any]) -> bool:
    if not SETTINGS.verification_token:
        return True
    token = payload.get("token") or payload.get("header", {}).get("token")
    return token == SETTINGS.verification_token


def _verify_timestamp(timestamp: str | None, *, tolerance_seconds: int = 600) -> bool:
    if not timestamp:
        return False
    try:
        ts = int(timestamp)
    except Exception:
        return False
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    return abs(now - ts) <= tolerance_seconds


def _verify_signature(*, body_bytes: bytes, timestamp: str | None, nonce: str | None, signature: str | None) -> bool:
    # If no app secret configured, skip signature validation.
    if not SETTINGS.app_secret:
        return True
    if not signature or not timestamp or not nonce:
        return not SETTINGS.signature_strict

    base = f"{timestamp}{nonce}".encode("utf-8") + body_bytes
    digest = hmac.new(SETTINGS.app_secret.encode("utf-8"), base, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    if hmac.compare_digest(expected, signature):
        return True
    return not SETTINGS.signature_strict


def _extract_event_id(payload: dict[str, Any]) -> str:
    header = payload.get("header") or {}
    event = payload.get("event") or {}
    message = event.get("message") or {}

    event_id = (
        str(header.get("event_id") or "").strip()
        or str(payload.get("event_id") or "").strip()
        or str(message.get("message_id") or "").strip()
    )
    if event_id:
        return event_id
    return f"fallback_{int(dt.datetime.now().timestamp())}_{os.getpid()}"


def _extract_source_time(event_message: dict[str, Any]) -> str:
    create_time = str(event_message.get("create_time") or "").strip()
    if not create_time:
        return _now_iso()
    try:
        raw = int(create_time)
    except Exception:
        return _now_iso()

    # Feishu create_time usually in milliseconds.
    if raw > 10_000_000_000:
        raw = raw / 1000
    return dt.datetime.fromtimestamp(raw, tz=dt.timezone.utc).isoformat(timespec="seconds")


def _allowed(event: dict[str, Any]) -> bool:
    message = event.get("message") or {}
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}

    chat_id = str(message.get("chat_id") or "").strip()
    open_id = str(sender_id.get("open_id") or "").strip()

    if SETTINGS.allowed_chat_ids and chat_id not in SETTINGS.allowed_chat_ids:
        return False
    if SETTINGS.allowed_open_ids and open_id not in SETTINGS.allowed_open_ids:
        return False
    return True


def _get_tenant_access_token() -> str:
    now = dt.datetime.now().timestamp()
    cached = TOKEN_CACHE.get("token") or ""
    expire_at = float(TOKEN_CACHE.get("expire_at") or 0)
    if cached and now < expire_at - 60:
        return cached

    if not SETTINGS.app_id or not SETTINGS.app_secret:
        raise RuntimeError("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")

    response = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": SETTINGS.app_id, "app_secret": SETTINGS.app_secret},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"tenant_access_token 获取失败: {data}")

    token = str(data.get("tenant_access_token") or "")
    expire = int(data.get("expire") or 0)
    TOKEN_CACHE["token"] = token
    TOKEN_CACHE["expire_at"] = now + expire
    return token


def _reply_to_message(*, message_id: str, text: str) -> None:
    if not SETTINGS.reply_enabled:
        return
    token = _get_tenant_access_token()
    response = requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
        headers={"Authorization": f"Bearer {token}"},
        json={"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书回执发送失败: {data}")


def _process_event_payload(payload: dict[str, Any], *, event_id: str) -> dict[str, Any]:
    event = payload.get("event") or {}
    message = event.get("message") or {}

    message_type = str(message.get("message_type") or "")
    if message_type != "text":
        return {"status": "ignored", "reason": f"unsupported message_type={message_type}"}

    if not _allowed(event):
        return {"status": "ignored", "reason": "sender/chat not allowed"}

    source_time = _extract_source_time(message)
    text = parse_feishu_text_content(str(message.get("content") or ""))

    import_record_path = IMPORT_RECORD_DIR / f"{_today_str()}-feishu-import.md"
    link_log_path = LINK_LOG_DIR / f"{_today_str()}-feishu-links.md"

    summary = process_message(
        text=text,
        quote_dir=QUOTE_DIR,
        topic_pool_path=TOPIC_POOL_PATH,
        import_record_path=import_record_path,
        link_log_path=link_log_path,
        apply_mode=SETTINGS.apply_mode,
        source_time=source_time,
        source_ref=event_id,
        near_dup_threshold=max(0.1, min(0.99, SETTINGS.near_dup_threshold)),
        min_content_chars=SETTINGS.link_min_content_chars,
        allow_test_url_skip=SETTINGS.link_allow_test_url_skip,
    )

    reply_text = build_short_reply(summary)
    reply_error = ""
    message_id = str(message.get("message_id") or "").strip()
    if message_id and SETTINGS.reply_enabled:
        try:
            _reply_to_message(message_id=message_id, text=reply_text)
        except Exception as exc:  # keep ingest success even if Feishu reply fails
            reply_error = str(exc)

    result: dict[str, Any] = {
        "status": "success",
        "summary": dataclasses.asdict(summary),
        "reply": reply_text,
    }
    if reply_error:
        result["reply_error"] = reply_error
    return result


@app.on_event("startup")
def _startup() -> None:
    _init_db()


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "db": STATE_DB.as_posix(),
        "apply_mode": SETTINGS.apply_mode,
        "reply_enabled": SETTINGS.reply_enabled,
        "link_min_content_chars": SETTINGS.link_min_content_chars,
        "link_allow_test_url_skip": SETTINGS.link_allow_test_url_skip,
    }


@app.post("/api/feishu/replay/{event_id}")
def replay(event_id: str) -> dict[str, Any]:
    record = _db_get_event(event_id)
    if not record:
        raise HTTPException(status_code=404, detail="event_id not found")

    payload = json.loads(record["request_json"])
    result = _process_event_payload(payload, event_id=event_id)
    _db_upsert_event(event_id, result.get("status", "unknown"), record["request_json"], json.dumps(result, ensure_ascii=False))
    return {"code": 0, "msg": "ok", "event_id": event_id, "result": result}


@app.post("/api/feishu/events")
async def feishu_events(request: Request) -> JSONResponse:
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    event_id = _extract_event_id(payload)
    request_json = json.dumps(payload, ensure_ascii=False)

    if payload.get("encrypt"):
        _db_upsert_event(event_id, "rejected", request_json, json.dumps({"error": "encrypted payload is not supported yet"}, ensure_ascii=False))
        raise HTTPException(status_code=400, detail="encrypted payload is not supported yet")

    if not _verify_token(payload):
        _db_upsert_event(event_id, "rejected", request_json, json.dumps({"error": "invalid token"}, ensure_ascii=False))
        raise HTTPException(status_code=403, detail="invalid token")

    msg_type = str(payload.get("type") or "")
    if msg_type == "url_verification":
        challenge = payload.get("challenge")
        return JSONResponse(content={"challenge": challenge})

    timestamp = request.headers.get("X-Lark-Request-Timestamp")
    nonce = request.headers.get("X-Lark-Request-Nonce")
    signature = request.headers.get("X-Lark-Signature")

    if not _verify_timestamp(timestamp):
        _db_upsert_event(event_id, "rejected", request_json, json.dumps({"error": "invalid timestamp"}, ensure_ascii=False))
        raise HTTPException(status_code=403, detail="invalid timestamp")

    if not _verify_signature(body_bytes=body_bytes, timestamp=timestamp, nonce=nonce, signature=signature):
        _db_upsert_event(event_id, "rejected", request_json, json.dumps({"error": "invalid signature"}, ensure_ascii=False))
        raise HTTPException(status_code=403, detail="invalid signature")

    existing = _db_get_event(event_id)
    if existing and existing["status"] in {"success", "ignored"}:
        return JSONResponse(content={"code": 0, "msg": "ok", "event_id": event_id, "duplicate": True})

    _db_upsert_event(event_id, "pending", request_json)

    try:
        result = _process_event_payload(payload, event_id=event_id)
        status = str(result.get("status") or "unknown")
        _db_upsert_event(event_id, status, request_json, json.dumps(result, ensure_ascii=False))
        return JSONResponse(content={"code": 0, "msg": "ok", "event_id": event_id, "result": result})
    except Exception as exc:
        error_text = str(exc)
        _db_upsert_event(event_id, "error", request_json, json.dumps({"error": error_text}, ensure_ascii=False))
        _db_dead_letter(event_id, error_text)
        raise HTTPException(status_code=500, detail=error_text)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8787)
