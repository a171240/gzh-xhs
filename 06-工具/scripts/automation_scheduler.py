#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Automation scheduler for nightly metrics/retro/maintenance pipeline."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from automation_maintenance_runner import run_maintenance
from automation_state import AUTOMATION_ROOT
from metrics_runner import run_metrics
from retro_runner import run_retro


SCHEDULER_DIR = AUTOMATION_ROOT / "scheduler"
STATE_FILE = SCHEDULER_DIR / "state.json"
HEARTBEAT_FILE = SCHEDULER_DIR / "heartbeat.json"
RUN_LOG = SCHEDULER_DIR / "runs.jsonl"


def _ensure_dirs() -> None:
    SCHEDULER_DIR.mkdir(parents=True, exist_ok=True)


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fw:
        fw.write(_json_dumps(payload) + "\n")


def _now_shanghai() -> dt.datetime:
    try:
        return dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def _state_default() -> dict[str, Any]:
    return {
        "version": 1,
        "last_run_date": "",
        "last_status": "",
        "updated_at": "",
    }


def _save_heartbeat(payload: dict[str, Any]) -> None:
    _write_json(HEARTBEAT_FILE, payload)


def run_once(*, date_str: str, dry_run: bool) -> dict[str, Any]:
    metrics_result = run_metrics({"date": date_str, "source_user": "scheduler"}, dry_run=dry_run)
    retro_result = run_retro({"date": date_str, "source_user": "scheduler"}, dry_run=dry_run)
    maintenance_result = run_maintenance({"source_user": "scheduler"}, dry_run=dry_run)

    status = "success"
    for item in (metrics_result, retro_result, maintenance_result):
        if str(item.get("status") or "") != "success":
            status = "partial"
            break

    return {
        "status": status,
        "date": date_str,
        "metrics": metrics_result,
        "retro": retro_result,
        "maintenance": maintenance_result,
    }


def run_daemon(*, poll_sec: int, dry_run: bool, force: bool = False) -> None:
    _ensure_dirs()
    state = _read_json(STATE_FILE, _state_default())
    if not isinstance(state, dict):
        state = _state_default()

    while True:
        now = _now_shanghai()
        today = now.date().isoformat()
        should_run = force or (
            now.hour == 22 and now.minute >= 30 and str(state.get("last_run_date") or "") != today
        )

        if should_run:
            result = run_once(date_str=today, dry_run=dry_run)
            state["last_run_date"] = today
            state["last_status"] = result.get("status")
            state["updated_at"] = now.isoformat(timespec="seconds")
            _write_json(STATE_FILE, state)
            _append_jsonl(
                RUN_LOG,
                {
                    "ts": now.isoformat(timespec="seconds"),
                    "status": result.get("status"),
                    "date": today,
                    "dry_run": dry_run,
                    "result": result,
                },
            )
            force = False

        _save_heartbeat(
            {
                "ts": now.isoformat(timespec="seconds"),
                "mode": "automation-scheduler",
                "last_run_date": state.get("last_run_date"),
                "last_status": state.get("last_status"),
                "poll_sec": poll_sec,
                "dry_run": dry_run,
            }
        )
        time.sleep(max(10, poll_sec))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automation scheduler")
    parser.add_argument("--once", action="store_true", help="Run one round immediately")
    parser.add_argument("--date", default="", help="Target date for --once (YYYY-MM-DD); default today")
    parser.add_argument("--poll-sec", type=int, default=60, help="Daemon poll interval seconds")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Force one run in daemon mode on first loop")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if args.once:
        date_str = str(args.date or _now_shanghai().date().isoformat())
        result = run_once(date_str=date_str, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if str(result.get("status") or "") in {"success", "partial"} else 1

    print(
        json.dumps(
            {
                "status": "running",
                "mode": "automation-scheduler-daemon",
                "poll_sec": max(10, args.poll_sec),
                "dry_run": bool(args.dry_run),
                "force": bool(args.force),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    run_daemon(poll_sec=max(10, args.poll_sec), dry_run=args.dry_run, force=args.force)
    return 0


if __name__ == "__main__":
    import os
    import sys

    raise SystemExit(main(sys.argv[1:]))
