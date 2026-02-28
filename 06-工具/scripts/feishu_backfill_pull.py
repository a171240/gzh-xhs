#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pull recent Feishu chat messages and backfill them into Writer API.

Usage example:
  python 06-工具/scripts/feishu_backfill_pull.py --hours 24
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import requests

OPEN_API_BASE = "https://open.feishu.cn/open-apis"
URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _script_dir() -> Path:
    return Path(__file__).resolve().parent


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and not (os.getenv(key) or "").strip():
            os.environ[key] = value


def _load_env_fallbacks() -> None:
    script_dir = _script_dir()
    _load_env_file(script_dir / ".env.feishu.local")
    _load_env_file(script_dir / ".env.feishu")
    _load_env_file(script_dir / ".env.ingest-writer.local")
    _load_env_file(script_dir / ".env.ingest-writer")


def _as_iso(ts_sec: int) -> str:
    # Feishu often uses milliseconds epoch; normalize to seconds.
    value = int(ts_sec)
    if value > 10_000_000_000:
        value = value // 1000
    return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc).isoformat(timespec="seconds")


def _now_ts() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp())


def _today() -> str:
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(os.getenv("INGEST_REPORT_DATE_TZ", "Asia/Shanghai"))
        return dt.datetime.now(zone).date().isoformat()
    except Exception:
        return dt.date.today().isoformat()


def _require(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is empty")
    return value


def _tenant_access_token(app_id: str, app_secret: str, timeout_sec: int) -> str:
    url = f"{OPEN_API_BASE}/auth/v3/tenant_access_token/internal"
    payload = {"app_id": app_id, "app_secret": app_secret}
    resp = requests.post(url, json=payload, timeout=timeout_sec)
    if resp.status_code >= 400:
        raise RuntimeError(f"token http {resp.status_code}: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"token biz error: {data}")
    token = (data.get("tenant_access_token") or "").strip()
    if not token:
        raise RuntimeError("tenant_access_token missing")
    return token


def _api_get(
    *,
    path: str,
    token: str,
    params: dict[str, Any],
    timeout_sec: int,
) -> dict[str, Any]:
    url = f"{OPEN_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, params=params, timeout=timeout_sec)
    if resp.status_code >= 400:
        raise RuntimeError(f"GET {path} http {resp.status_code}: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"GET {path} biz error: {data}")
    return data


def _list_chats(token: str, timeout_sec: int, max_chats: int) -> list[str]:
    out: list[str] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {"page_size": min(100, max(1, max_chats)), "sort_type": "ByActiveTimeDesc"}
        if page_token:
            params["page_token"] = page_token
        data = _api_get(path="/im/v1/chats", token=token, params=params, timeout_sec=timeout_sec)
        items = (data.get("data") or {}).get("items") or []
        for item in items:
            chat_id = str(item.get("chat_id") or "").strip()
            if chat_id:
                out.append(chat_id)
                if len(out) >= max_chats:
                    return out
        page_token = str((data.get("data") or {}).get("page_token") or "").strip()
        has_more = bool((data.get("data") or {}).get("has_more"))
        if not has_more or not page_token:
            break
    return out


def _fallback_chat_ids_from_state(max_chats: int) -> list[str]:
    db_path = _repo_root() / "06-工具" / "data" / "feishu-ingest" / "state.db"
    if not db_path.exists():
        return []
    try:
        import sqlite3

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT request_json FROM events ORDER BY updated_at DESC LIMIT 300")
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        raw = str(row[0] or "")
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        event = data.get("event") or (data.get("data") or {}).get("event") or {}
        if not isinstance(event, dict):
            continue
        message = event.get("message") or {}
        if not isinstance(message, dict):
            continue
        chat_id = str(message.get("chat_id") or "").strip()
        chat_type = str(message.get("chat_type") or "").strip().lower()
        if not chat_id:
            continue
        # Prefer p2p chats for personal backfill.
        if chat_type != "p2p":
            continue
        if chat_id in seen:
            continue
        seen.add(chat_id)
        out.append(chat_id)
        if len(out) >= max_chats:
            break
    return out


def _list_messages(
    *,
    token: str,
    chat_id: str,
    start_ts: int,
    end_ts: int,
    timeout_sec: int,
    per_chat_limit: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {
            "container_id_type": "chat",
            "container_id": chat_id,
            "sort_type": "ByCreateTimeAsc",
            "start_time": str(start_ts),
            "end_time": str(end_ts),
            "page_size": 50,
        }
        if page_token:
            params["page_token"] = page_token
        data = _api_get(path="/im/v1/messages", token=token, params=params, timeout_sec=timeout_sec)
        items = (data.get("data") or {}).get("items") or []
        out.extend(items)
        if len(out) >= per_chat_limit:
            return out[:per_chat_limit]
        page_token = str((data.get("data") or {}).get("page_token") or "").strip()
        has_more = bool((data.get("data") or {}).get("has_more"))
        if not has_more or not page_token:
            break
    return out


def _extract_text(msg: dict[str, Any]) -> str:
    msg_type = str(msg.get("msg_type") or "").strip().lower()
    body = msg.get("body") or {}
    raw = body.get("content")
    if not raw:
        return ""
    try:
        content = json.loads(raw)
    except Exception:
        content = {"raw": str(raw)}
    if msg_type == "text":
        return str(content.get("text") or "").strip()
    if msg_type == "post":
        # post format: {"zh_cn":{"title":"", "content":[[{...}]]}}
        text_parts: list[str] = []
        for lang_data in content.values():
            if not isinstance(lang_data, dict):
                continue
            title = str(lang_data.get("title") or "").strip()
            if title:
                text_parts.append(title)
            rows = lang_data.get("content") or []
            for row in rows:
                if not isinstance(row, list):
                    continue
                for seg in row:
                    if not isinstance(seg, dict):
                        continue
                    if str(seg.get("tag") or "").lower() == "text":
                        val = str(seg.get("text") or "").strip()
                        if val:
                            text_parts.append(val)
        return "\n".join(text_parts).strip()
    return ""


def _route_text(text: str) -> tuple[str, dict[str, Any]]:
    urls = []
    seen: set[str] = set()
    for item in URL_RE.findall(text or ""):
        value = item.strip()
        if value and value not in seen:
            seen.add(value)
            urls.append(value)
    quote_text = URL_RE.sub(" ", text or "")
    quote_text = re.sub(r"\s+", " ", quote_text).strip()

    if urls and quote_text:
        return "mixed", {"text": quote_text, "urls": urls}
    if urls:
        return "link", {"text": text, "urls": urls}
    return "quote", {"text": quote_text}


def _sign(secret: str, *, timestamp: str, nonce: str, body: bytes) -> str:
    payload = f"{timestamp}\n{nonce}\n".encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _writer_post(
    *,
    base_url: str,
    token: str,
    secret: str,
    endpoint: str,
    payload: dict[str, Any],
    timeout_sec: int,
    verify_ssl: bool,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    timestamp = str(_now_ts())
    nonce = uuid.uuid4().hex
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Ingest-Timestamp": timestamp,
        "X-Ingest-Nonce": nonce,
        "X-Ingest-Signature": _sign(secret, timestamp=timestamp, nonce=nonce, body=body),
    }
    url = f"{base_url.rstrip('/')}{endpoint}"
    resp = requests.post(url, headers=headers, data=body, timeout=timeout_sec, verify=verify_ssl)
    if resp.status_code >= 400:
        raise RuntimeError(f"writer http {resp.status_code}: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"writer biz error: {data}")
    return data


def _append_report(path: Path, *, title: str, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if not path.exists():
        lines.append("# 飞书离线补偿导入记录\n\n")
    lines.append(f"## {title}\n")
    lines.append(f"- 时间窗口(UTC): `{summary['window_start']}` -> `{summary['window_end']}`\n")
    lines.append(f"- chat_count: `{summary['chat_count']}`\n")
    lines.append(f"- pulled_messages: `{summary['pulled_messages']}`\n")
    lines.append(f"- processed: `{summary['processed']}`\n")
    lines.append(f"- success: `{summary['success']}`\n")
    lines.append(f"- duplicate: `{summary['duplicate']}`\n")
    lines.append(f"- ignored: `{summary['ignored']}`\n")
    lines.append(f"- failed: `{summary['failed']}`\n\n")
    for row in rows:
        lines.append(f"- event_ref: `{row['event_ref']}`\n")
        lines.append(f"  - status: {row['status']}\n")
        lines.append(f"  - chat_id: `{row['chat_id']}`\n")
        lines.append(f"  - message_id: `{row['message_id']}`\n")
        lines.append(f"  - mode: {row['mode']}\n")
        lines.append(f"  - detail: {row['detail']}\n")
    lines.append("\n")
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(old + "".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pull Feishu recent messages and backfill into writer")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours (default: 24)")
    parser.add_argument("--chat-id", action="append", default=[], help="Specific chat_id, repeatable")
    parser.add_argument("--max-chats", type=int, default=20, help="Max chats when auto listing")
    parser.add_argument("--per-chat-limit", type=int, default=200, help="Max messages per chat")
    parser.add_argument("--timeout-sec", type=int, default=20, help="HTTP timeout seconds")
    parser.add_argument("--dry-run", action="store_true", help="Do not write, only print")
    parser.add_argument("--report-file", default="", help="Override report path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        _load_env_fallbacks()

        app_id = _require("FEISHU_APP_ID")
        app_secret = _require("FEISHU_APP_SECRET")
        ingest_token = _require("INGEST_SHARED_TOKEN")
        ingest_secret = (os.getenv("INGEST_HMAC_SECRET") or "").strip() or ingest_token
        writer_base_url = (os.getenv("INGEST_WRITER_BASE_URL") or "http://127.0.0.1:8790").strip()
        verify_ssl = (os.getenv("INGEST_VERIFY_SSL") or "true").strip().lower() in {"1", "true", "yes", "y", "on"}

        now_ts = _now_ts()
        start_ts = now_ts - max(1, args.hours) * 3600
        title = f"{dt.datetime.now().isoformat(timespec='seconds')} (last {args.hours}h)"

        token = _tenant_access_token(app_id, app_secret, args.timeout_sec)
        chat_ids = [x.strip() for x in args.chat_id if x and x.strip()]
        if not chat_ids:
            try:
                chat_ids = _list_chats(token, args.timeout_sec, max_chats=max(1, args.max_chats))
            except Exception:
                # Missing chat scope (99991672) is common; fallback to known p2p chats from local state.
                chat_ids = []
        if not chat_ids:
            chat_ids = _fallback_chat_ids_from_state(max(1, args.max_chats))

        rows: list[dict[str, Any]] = []
        summary = {
            "window_start": _as_iso(start_ts),
            "window_end": _as_iso(now_ts),
            "chat_count": len(chat_ids),
            "pulled_messages": 0,
            "processed": 0,
            "success": 0,
            "duplicate": 0,
            "ignored": 0,
            "failed": 0,
        }

        for chat_id in chat_ids:
            try:
                messages = _list_messages(
                    token=token,
                    chat_id=chat_id,
                    start_ts=start_ts,
                    end_ts=now_ts,
                    timeout_sec=args.timeout_sec,
                    per_chat_limit=max(1, args.per_chat_limit),
                )
            except Exception as exc:
                summary["failed"] += 1
                rows.append(
                    {
                        "event_ref": f"feishu-backfill-chat-{chat_id}",
                        "status": "failed",
                        "chat_id": chat_id,
                        "message_id": "-",
                        "mode": "chat-scan",
                        "detail": f"list_messages failed: {exc}",
                    }
                )
                continue
            summary["pulled_messages"] += len(messages)
            for msg in messages:
                sender = msg.get("sender") or {}
                if str(sender.get("sender_type") or "").lower() != "user":
                    continue

                message_id = str(msg.get("message_id") or "").strip()
                if not message_id:
                    continue
                raw_text = _extract_text(msg)
                if not raw_text.strip():
                    continue
                event_ref = f"feishu-backfill-{message_id}"
                mode, payload_core = _route_text(raw_text)
                if mode == "quote" and not payload_core.get("text", "").strip():
                    summary["ignored"] += 1
                    rows.append(
                        {
                            "event_ref": event_ref,
                            "status": "ignored",
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "mode": mode,
                            "detail": "empty text after cleanup",
                        }
                    )
                    continue

                endpoint = {
                    "quote": "/internal/ingest/v1/quote",
                    "link": "/internal/ingest/v1/link",
                    "mixed": "/internal/ingest/v1/mixed",
                }[mode]

                payload = {
                    "event_ref": event_ref,
                    "source_kind": "openclaw-backfill",
                    "source_ref": message_id,
                    "source_time": _as_iso(int(msg.get("create_time", now_ts))),
                    **payload_core,
                }
                summary["processed"] += 1

                if args.dry_run:
                    rows.append(
                        {
                            "event_ref": event_ref,
                            "status": "dry-run",
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "mode": mode,
                            "detail": endpoint,
                        }
                    )
                    continue

                try:
                    data = _writer_post(
                        base_url=writer_base_url,
                        token=ingest_token,
                        secret=ingest_secret,
                        endpoint=endpoint,
                        payload=payload,
                        timeout_sec=max(1, args.timeout_sec),
                        verify_ssl=verify_ssl,
                    )
                    duplicate = bool(data.get("duplicate"))
                    result = data.get("result") or {}
                    if duplicate:
                        summary["duplicate"] += 1
                        status = "duplicate"
                    elif str(result.get("status") or "").lower() == "ignored":
                        summary["ignored"] += 1
                        status = "ignored"
                    else:
                        summary["success"] += 1
                        status = "success"
                    rows.append(
                        {
                            "event_ref": event_ref,
                            "status": status,
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "mode": mode,
                            "detail": f"added={result.get('added', 0)} skipped={result.get('skipped', 0)}",
                        }
                    )
                except Exception as exc:
                    summary["failed"] += 1
                    rows.append(
                        {
                            "event_ref": event_ref,
                            "status": "failed",
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "mode": mode,
                            "detail": str(exc),
                        }
                    )

        report_path = (
            Path(args.report_file)
            if args.report_file
            else _repo_root() / "03-素材库" / "金句库" / "导入记录" / f"{_today()}-feishu-backfill.md"
        )
        _append_report(report_path, title=title, rows=rows, summary=summary)

        print(json.dumps({"summary": summary, "report": report_path.as_posix()}, ensure_ascii=False, indent=2))
        return 0 if summary["failed"] == 0 else 1
    except Exception as exc:
        msg = str(exc)
        print(f"[backfill] error: {msg}")
        if (os.getenv("BACKFILL_DEBUG") or "").strip().lower() in {"1", "true", "yes", "on"}:
            import traceback

            traceback.print_exc()
        if "99991672" in msg:
            print(
                "[backfill] missing scope: please enable one of "
                "`im:chat:readonly` / `im:chat` / `im:chat.group_info:readonly` / `im:chat:read`."
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
