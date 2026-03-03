#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Async job store for Douyin->Bitable ingest workflow."""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_tool_dir() -> Path:
    env_dir = str(os.getenv("FEISHU_TOOL_DIR") or "").strip()
    if env_dir:
        candidate = Path(env_dir).expanduser()
        if candidate.exists():
            return candidate

    for candidate in (REPO_ROOT / "06-宸ュ叿", REPO_ROOT / "06-\u5de5\u5177"):
        if candidate.exists() and (candidate / "scripts").exists():
            return candidate

    for child in sorted(REPO_ROOT.glob("06-*")):
        if child.is_dir() and (child / "scripts").exists():
            return child

    return REPO_ROOT


TOOL_DIR = _resolve_tool_dir()
DEFAULT_DB = TOOL_DIR / "data" / "feishu-orchestrator" / "link_async_jobs.db"
STATE_PENDING = "pending"
STATE_PROCESSING = "processing"
STATE_SUCCESS = "success"
STATE_FAILED = "failed"
STATE_TIMEOUT = "timeout"


def _db_path() -> Path:
    raw = str(os.getenv("FEISHU_LINK_ASYNC_DB") or "").strip()
    if raw:
        return Path(raw).expanduser()
    if DEFAULT_DB.exists():
        return DEFAULT_DB
    matches = [p for p in REPO_ROOT.glob("06-*/data/feishu-orchestrator/link_async_jobs.db") if p.exists()]
    if matches:
        return sorted(matches, key=lambda p: len(str(p)))[0]
    return DEFAULT_DB


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(ts: dt.datetime | None = None) -> str:
    value = ts or _now()
    return value.isoformat(timespec="seconds")


def _parse_iso(value: str) -> dt.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=float(os.getenv("FEISHU_LINK_ASYNC_DB_TIMEOUT_SEC", "10")))
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode=WAL")
    conn.execute("pragma synchronous=NORMAL")
    conn.execute(f"pragma busy_timeout={int(float(os.getenv('FEISHU_LINK_ASYNC_DB_BUSY_TIMEOUT_MS', '5000')))}")
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("meta_json", "result_json"):
        raw = data.get(key)
        if isinstance(raw, str) and raw.strip():
            try:
                data[key] = json.loads(raw)
            except Exception:
                pass
    return data


def ensure_schema() -> None:
    with _connect() as conn:
        conn.execute(
            """
            create table if not exists link_async_jobs (
              job_id text primary key,
              event_ref text not null,
              message_id text not null default '',
              chat_id text not null default '',
              source_ref text not null default '',
              source_time text not null default '',
              source_user text not null default '',
              url text not null default '',
              normalized_url text not null default '',
              state text not null default 'pending',
              created_at text not null default '',
              updated_at text not null default '',
              last_poll_at text not null default '',
              next_poll_at text not null default '',
              deadline_at text not null default '',
              completed_at text not null default '',
              try_count integer not null default 0,
              meta_json text not null default '{}',
              result_json text not null default '{}',
              error text not null default ''
            )
            """
        )
        conn.execute("create index if not exists idx_link_async_jobs_state_next on link_async_jobs(state, next_poll_at)")
        conn.execute("create index if not exists idx_link_async_jobs_event_ref on link_async_jobs(event_ref)")
        conn.execute("create unique index if not exists uq_link_async_jobs_event_ref_url on link_async_jobs(event_ref, normalized_url)")


def enqueue_job(
    *,
    job_id: str,
    event_ref: str,
    url: str,
    normalized_url: str,
    message_id: str = "",
    chat_id: str = "",
    source_ref: str = "",
    source_time: str = "",
    source_user: str = "",
    timeout_minutes: int = 20,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_schema()
    now = _now()
    created_at = _iso(now)
    deadline_at = _iso(now + dt.timedelta(minutes=max(1, int(timeout_minutes))))
    payload = (
        str(job_id or "").strip(),
        str(event_ref or "").strip(),
        str(message_id or "").strip(),
        str(chat_id or "").strip(),
        str(source_ref or "").strip(),
        str(source_time or "").strip() or created_at,
        str(source_user or "").strip(),
        str(url or "").strip(),
        str(normalized_url or "").strip(),
        STATE_PENDING,
        created_at,
        created_at,
        "",
        created_at,
        deadline_at,
        "",
        0,
        json.dumps(meta or {}, ensure_ascii=False),
        "{}",
        "",
    )
    with _connect() as conn:
        conn.execute(
            """
            insert into link_async_jobs(
              job_id,event_ref,message_id,chat_id,source_ref,source_time,source_user,
              url,normalized_url,state,created_at,updated_at,last_poll_at,next_poll_at,
              deadline_at,completed_at,try_count,meta_json,result_json,error
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            on conflict(event_ref, normalized_url) do update set
              message_id=excluded.message_id,
              chat_id=excluded.chat_id,
              source_ref=excluded.source_ref,
              source_time=excluded.source_time,
              source_user=excluded.source_user,
              state=excluded.state,
              next_poll_at=excluded.next_poll_at,
              deadline_at=excluded.deadline_at,
              completed_at='',
              try_count=0,
              error='',
              result_json='{}',
              meta_json=excluded.meta_json,
              updated_at=excluded.updated_at
            """,
            payload,
        )
    job = find_job_by_event_ref(event_ref)
    return job or {"job_id": job_id, "event_ref": event_ref, "state": STATE_PENDING}


def claim_due_jobs(*, limit: int = 5, stale_processing_seconds: int = 300) -> list[dict[str, Any]]:
    ensure_schema()
    now = _now()
    now_iso = _iso(now)
    stale_cutoff = _iso(now - dt.timedelta(seconds=max(30, int(stale_processing_seconds))))
    picked: list[dict[str, Any]] = []
    with _connect() as conn:
        conn.execute("begin immediate")
        rows = conn.execute(
            """
            select * from link_async_jobs
            where
              (
                state = ?
                or (state = ? and (last_poll_at = '' or last_poll_at <= ?))
              )
              and (next_poll_at = '' or next_poll_at <= ?)
            order by created_at asc
            limit ?
            """,
            (STATE_PENDING, STATE_PROCESSING, stale_cutoff, now_iso, max(1, int(limit))),
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                update link_async_jobs
                set state=?, updated_at=?, last_poll_at=?, try_count=try_count+1
                where job_id=?
                """,
                (STATE_PROCESSING, now_iso, now_iso, str(row["job_id"])),
            )
            updated = dict(row)
            updated["state"] = STATE_PROCESSING
            updated["updated_at"] = now_iso
            updated["last_poll_at"] = now_iso
            updated["try_count"] = int(updated.get("try_count") or 0) + 1
            picked.append(updated)
        conn.commit()
    return [_normalize_row(item) for item in picked]


def _normalize_row(item: dict[str, Any]) -> dict[str, Any]:
    data = dict(item)
    for key in ("meta_json", "result_json"):
        raw = data.get(key)
        if isinstance(raw, str) and raw.strip():
            try:
                data[key] = json.loads(raw)
            except Exception:
                pass
    return data


def mark_job_pending(job_id: str, *, next_poll_seconds: int = 60, error: str = "", result: dict[str, Any] | None = None) -> None:
    now = _now()
    now_iso = _iso(now)
    next_poll_at = _iso(now + dt.timedelta(seconds=max(5, int(next_poll_seconds))))
    with _connect() as conn:
        conn.execute(
            """
            update link_async_jobs
            set state=?, updated_at=?, last_poll_at=?, next_poll_at=?, error=?, result_json=?
            where job_id=?
            """,
            (
                STATE_PENDING,
                now_iso,
                now_iso,
                next_poll_at,
                str(error or "").strip(),
                json.dumps(result or {}, ensure_ascii=False),
                str(job_id or "").strip(),
            ),
        )


def complete_job_success(job_id: str, result: dict[str, Any] | None = None) -> None:
    now_iso = _iso()
    with _connect() as conn:
        conn.execute(
            """
            update link_async_jobs
            set state=?, updated_at=?, completed_at=?, error='', result_json=?
            where job_id=?
            """,
            (STATE_SUCCESS, now_iso, now_iso, json.dumps(result or {}, ensure_ascii=False), str(job_id or "").strip()),
        )


def complete_job_failed(job_id: str, *, error: str, result: dict[str, Any] | None = None, state: str = STATE_FAILED) -> None:
    fail_state = state if state in {STATE_FAILED, STATE_TIMEOUT} else STATE_FAILED
    now_iso = _iso()
    with _connect() as conn:
        conn.execute(
            """
            update link_async_jobs
            set state=?, updated_at=?, completed_at=?, error=?, result_json=?
            where job_id=?
            """,
            (
                fail_state,
                now_iso,
                now_iso,
                str(error or "").strip(),
                json.dumps(result or {}, ensure_ascii=False),
                str(job_id or "").strip(),
            ),
        )


def find_job_by_event_ref(event_ref: str) -> dict[str, Any] | None:
    if not str(event_ref or "").strip():
        return None
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "select * from link_async_jobs where event_ref=? order by created_at desc limit 1",
            (str(event_ref).strip(),),
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_job(job_id: str) -> dict[str, Any] | None:
    if not str(job_id or "").strip():
        return None
    ensure_schema()
    with _connect() as conn:
        row = conn.execute("select * from link_async_jobs where job_id=?", (str(job_id).strip(),)).fetchone()
    return _row_to_dict(row) if row else None


def is_expired(job: dict[str, Any]) -> bool:
    deadline = _parse_iso(str(job.get("deadline_at") or ""))
    if deadline is None:
        return False
    return _now() >= deadline

