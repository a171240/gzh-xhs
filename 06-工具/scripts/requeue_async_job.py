#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Requeue one async link job with a clean replay state.

This tool standardizes "true replay" for a specific async event:
1) reset async job state/deadline/notify fields
2) clear writer cached request row for `<event_ref>#link`
3) write an audit record for traceability
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_tool_dir(repo_root: Path) -> Path:
    env_dir = str(os.getenv("FEISHU_TOOL_DIR") or "").strip()
    if env_dir:
        candidate = Path(env_dir).expanduser()
        if candidate.exists():
            return candidate
    for candidate in (repo_root / "06-工具", repo_root / "06-宸ュ叿"):
        if candidate.exists() and (candidate / "scripts").exists():
            return candidate
    for child in sorted(repo_root.glob("06-*")):
        if child.is_dir() and (child / "scripts").exists():
            return child
    return repo_root / "06-工具"


def _default_job_db(tool_dir: Path) -> Path:
    env_value = str(os.getenv("FEISHU_LINK_ASYNC_DB") or "").strip()
    if env_value:
        return Path(env_value).expanduser()
    return tool_dir / "data" / "feishu-orchestrator" / "link_async_jobs.db"


def _default_writer_db(tool_dir: Path) -> Path:
    env_value = str(os.getenv("INGEST_STATE_DB") or "").strip()
    if env_value:
        return Path(env_value).expanduser()
    return tool_dir / "data" / "ingest-writer" / "writer_state.db"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Requeue a link async job with clean replay state.")
    parser.add_argument("--event-ref", default="", help="Base event_ref without #link suffix")
    parser.add_argument("--job-id", default="", help="Explicit async job_id (takes priority over event_ref)")
    parser.add_argument("--timeout-min", type=int, default=int(os.getenv("FEISHU_LINK_ASYNC_TIMEOUT_MIN", "20")))
    parser.add_argument("--keep-try-count", default="false", help="Keep try_count/notify_try_count (default false)")
    parser.add_argument("--job-db", default="", help="Override link_async_jobs.db path")
    parser.add_argument("--writer-db", default="", help="Override writer_state.db path")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _fetch_target_job(conn: sqlite3.Connection, *, event_ref: str, job_id: str) -> tuple[str, str] | None:
    cur = conn.cursor()
    if str(job_id or "").strip():
        row = cur.execute(
            "select job_id,event_ref from link_async_jobs where job_id=? order by updated_at desc limit 1",
            (str(job_id).strip(),),
        ).fetchone()
        if row:
            return str(row[0] or ""), str(row[1] or "")
    if str(event_ref or "").strip():
        row = cur.execute(
            "select job_id,event_ref from link_async_jobs where event_ref=? order by updated_at desc limit 1",
            (str(event_ref).strip(),),
        ).fetchone()
        if row:
            return str(row[0] or ""), str(row[1] or "")
    return None


def _append_audit(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main() -> int:
    args = parse_args()
    event_ref = str(args.event_ref or "").strip()
    job_id = str(args.job_id or "").strip()
    if not event_ref and not job_id:
        print("missing --event-ref or --job-id")
        return 1

    repo_root = _repo_root()
    tool_dir = _resolve_tool_dir(repo_root)
    job_db = Path(args.job_db).expanduser() if str(args.job_db).strip() else _default_job_db(tool_dir)
    writer_db = Path(args.writer_db).expanduser() if str(args.writer_db).strip() else _default_writer_db(tool_dir)
    if not job_db.exists():
        print(f"job db not found: {job_db.as_posix()}")
        return 1
    if not writer_db.exists():
        print(f"writer db not found: {writer_db.as_posix()}")
        return 1

    now = dt.datetime.now(dt.timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    deadline_iso = (now + dt.timedelta(minutes=max(1, int(args.timeout_min)))).isoformat(timespec="seconds")
    keep_try_count = _bool(args.keep_try_count)

    with sqlite3.connect(job_db) as conn:
        target = _fetch_target_job(conn, event_ref=event_ref, job_id=job_id)
        if not target:
            print(f"async job not found (event_ref={event_ref or '-'} job_id={job_id or '-'})")
            return 1
        target_job_id, target_event_ref = target
        cur = conn.cursor()
        if args.dry_run:
            print(f"[dry-run] target_job_id={target_job_id}")
            print(f"[dry-run] target_event_ref={target_event_ref}")
        else:
            if keep_try_count:
                cur.execute(
                    """
                    update link_async_jobs
                    set state='pending',
                        completed_at='',
                        error='',
                        last_poll_at='',
                        next_poll_at=?,
                        deadline_at=?,
                        updated_at=?,
                        notify_state='',
                        notify_error='',
                        result_json='{}'
                    where job_id=?
                    """,
                    (now_iso, deadline_iso, now_iso, target_job_id),
                )
            else:
                cur.execute(
                    """
                    update link_async_jobs
                    set state='pending',
                        completed_at='',
                        error='',
                        last_poll_at='',
                        next_poll_at=?,
                        deadline_at=?,
                        updated_at=?,
                        try_count=0,
                        notify_state='',
                        notify_try_count=0,
                        notify_error='',
                        result_json='{}'
                    where job_id=?
                    """,
                    (now_iso, deadline_iso, now_iso, target_job_id),
                )
            updated = int(cur.rowcount or 0)
            conn.commit()
            print(f"job_requeued={updated}")

    writer_event_ref = f"{target_event_ref}#link"
    writer_deleted = 0
    if not args.dry_run:
        with sqlite3.connect(writer_db) as wconn:
            wcur = wconn.cursor()
            wcur.execute(
                "delete from requests where event_ref=? and mode='link'",
                (writer_event_ref,),
            )
            writer_deleted = int(wcur.rowcount or 0)
            wconn.commit()
    print(f"writer_deleted={writer_deleted}")

    audit = {
        "ts": now_iso,
        "event_ref": target_event_ref,
        "job_id": target_job_id,
        "writer_event_ref": writer_event_ref,
        "timeout_min": int(args.timeout_min),
        "keep_try_count": keep_try_count,
        "dry_run": bool(args.dry_run),
        "job_db": job_db.as_posix(),
        "writer_db": writer_db.as_posix(),
        "writer_deleted": writer_deleted,
    }
    audit_path = tool_dir / "data" / "feishu-orchestrator" / "requeue" / f"{dt.date.today().isoformat()}.jsonl"
    if not args.dry_run:
        _append_audit(audit_path, audit)
    print(f"audit_path={audit_path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
