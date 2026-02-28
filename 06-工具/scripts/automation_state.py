#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared automation task state for media/publish/metrics/retro workflows."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
AUTOMATION_ROOT = REPO_ROOT / "06-工具" / "data" / "automation"
RUN_LOG_DIR = AUTOMATION_ROOT / "runs"
DEAD_LETTER_DIR = AUTOMATION_ROOT / "dead-letter"
SCREENSHOT_DIR = AUTOMATION_ROOT / "screenshots"
METRICS_DIR = AUTOMATION_ROOT / "metrics"
MEDIA_INBOX_DIR = AUTOMATION_ROOT / "media" / "inbox"
STATE_DB = AUTOMATION_ROOT / "automation_state.db"

DB_LOCK = threading.Lock()
DB_READY = False


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return dt.date.today().isoformat()


def ensure_dirs() -> None:
    for path in (RUN_LOG_DIR, DEAD_LETTER_DIR, SCREENSHOT_DIR, METRICS_DIR, MEDIA_INBOX_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _db_connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(STATE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    global DB_READY
    if DB_READY:
        return
    with DB_LOCK:
        if DB_READY:
            return
        with _db_connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  task_id TEXT PRIMARY KEY,
                  event_ref TEXT,
                  task_type TEXT NOT NULL,
                  status TEXT NOT NULL,
                  phase TEXT,
                  platform TEXT,
                  account TEXT,
                  mode TEXT,
                  source_user TEXT,
                  approver TEXT,
                  payload_json TEXT NOT NULL,
                  result_json TEXT,
                  error_text TEXT,
                  retry_count INTEGER NOT NULL DEFAULT 0,
                  next_retry_at TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  approved_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_event_ref
                ON tasks(event_ref, task_type)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_logs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  task_id TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        DB_READY = True


def _ensure_db() -> None:
    if not DB_READY:
        init_db()


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def make_task_id(prefix: str) -> str:
    base = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{base}-{uuid.uuid4().hex[:8]}"


def append_run_log(kind: str, payload: dict[str, Any]) -> str:
    ensure_dirs()
    path = RUN_LOG_DIR / f"{today()}.jsonl"
    entry = {"ts": now_iso(), "kind": kind, **payload}
    with path.open("a", encoding="utf-8") as f:
        f.write(_json_dumps(entry) + "\n")
    return path.relative_to(REPO_ROOT).as_posix()


def append_dead_letter(reason: str, payload: dict[str, Any]) -> str:
    ensure_dirs()
    path = DEAD_LETTER_DIR / f"{today()}.jsonl"
    entry = {"ts": now_iso(), "reason": reason, **payload}
    with path.open("a", encoding="utf-8") as f:
        f.write(_json_dumps(entry) + "\n")
    return path.relative_to(REPO_ROOT).as_posix()


def create_task(
    *,
    task_id: str,
    task_type: str,
    status: str,
    phase: str = "",
    platform: str = "",
    account: str = "",
    mode: str = "",
    source_user: str = "",
    event_ref: str = "",
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error_text: str = "",
) -> dict[str, Any]:
    _ensure_db()
    created_at = now_iso()
    payload_json = _json_dumps(payload or {})
    result_json = _json_dumps(result or {}) if result else ""
    with DB_LOCK:
        with _db_connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks(
                  task_id, event_ref, task_type, status, phase, platform, account, mode,
                  source_user, approver, payload_json, result_json, error_text,
                  retry_count, next_retry_at, created_at, updated_at, approved_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    event_ref,
                    task_type,
                    status,
                    phase,
                    platform,
                    account,
                    mode,
                    source_user,
                    "",
                    payload_json,
                    result_json,
                    error_text,
                    0,
                    "",
                    created_at,
                    created_at,
                    "",
                ),
            )
            conn.commit()
    row = get_task(task_id)
    return row or {}


def update_task(task_id: str, **fields: Any) -> dict[str, Any] | None:
    _ensure_db()
    if not fields:
        return get_task(task_id)
    allowed = {
        "status",
        "phase",
        "platform",
        "account",
        "mode",
        "source_user",
        "approver",
        "payload_json",
        "result_json",
        "error_text",
        "retry_count",
        "next_retry_at",
        "approved_at",
    }
    parts: list[str] = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        parts.append(f"{key} = ?")
        values.append(value)
    if not parts:
        return get_task(task_id)
    parts.append("updated_at = ?")
    values.append(now_iso())
    values.append(task_id)
    with DB_LOCK:
        with _db_connect() as conn:
            conn.execute(
                f"UPDATE tasks SET {', '.join(parts)} WHERE task_id = ?",
                values,
            )
            conn.commit()
    return get_task(task_id)


def get_task(task_id: str) -> dict[str, Any] | None:
    _ensure_db()
    with DB_LOCK:
        with _db_connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    for key in ("payload_json", "result_json"):
        raw = str(data.get(key) or "").strip()
        if not raw:
            data[key] = {}
            continue
        try:
            data[key] = json.loads(raw)
        except Exception:
            data[key] = {}
    return data


def find_task_by_event_ref(event_ref: str, task_type: str) -> dict[str, Any] | None:
    _ensure_db()
    ref = str(event_ref or "").strip()
    if not ref:
        return None
    with DB_LOCK:
        with _db_connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE event_ref = ? AND task_type = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (ref, task_type),
            ).fetchone()
    if not row:
        return None
    return get_task(str(row["task_id"]))


def add_task_log(task_id: str, event_type: str, payload: dict[str, Any]) -> None:
    _ensure_db()
    with DB_LOCK:
        with _db_connect() as conn:
            conn.execute(
                """
                INSERT INTO task_logs(task_id, event_type, payload_json, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (task_id, event_type, _json_dumps(payload), now_iso()),
            )
            conn.commit()


def list_retryable_tasks(*, before_iso: str | None = None) -> list[dict[str, Any]]:
    _ensure_db()
    deadline = str(before_iso or now_iso()).strip()
    with DB_LOCK:
        with _db_connect() as conn:
            rows = conn.execute(
                """
                SELECT task_id
                FROM tasks
                WHERE status = 'retry_pending'
                  AND (next_retry_at IS NULL OR next_retry_at = '' OR next_retry_at <= ?)
                ORDER BY updated_at ASC
                LIMIT 200
                """,
                (deadline,),
            ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        task = get_task(str(row["task_id"]))
        if task:
            out.append(task)
    return out


def list_tasks(
    *,
    task_type: str = "",
    statuses: list[str] | None = None,
    date_prefix: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    _ensure_db()
    where: list[str] = []
    values: list[Any] = []
    if task_type:
        where.append("task_type = ?")
        values.append(str(task_type))
    if statuses:
        clean = [str(item).strip() for item in statuses if str(item).strip()]
        if clean:
            marks = ", ".join("?" for _ in clean)
            where.append(f"status IN ({marks})")
            values.extend(clean)
    if date_prefix:
        where.append("created_at LIKE ?")
        values.append(f"{date_prefix}%")

    sql = "SELECT task_id FROM tasks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    values.append(max(1, int(limit)))

    with DB_LOCK:
        with _db_connect() as conn:
            rows = conn.execute(sql, values).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        task = get_task(str(row["task_id"]))
        if task:
            out.append(task)
    return out
