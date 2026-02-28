#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Automation maintenance runner: retry publish tasks and cleanup artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
from pathlib import Path
from typing import Any

from automation_state import (
    AUTOMATION_ROOT,
    SCREENSHOT_DIR,
    add_task_log,
    append_dead_letter,
    append_run_log,
    create_task,
    list_retryable_tasks,
    make_task_id,
    update_task,
)
from publish_action_runner import retry_publish_task


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _cleanup_old_dirs(root: Path, *, keep_days: int) -> dict[str, Any]:
    if not root.exists():
        return {"removed": 0, "paths": []}
    deadline = dt.datetime.now() - dt.timedelta(days=max(1, keep_days))
    removed = 0
    paths: list[str] = []
    for path in root.iterdir():
        try:
            mtime = dt.datetime.fromtimestamp(path.stat().st_mtime)
        except Exception:
            continue
        if mtime > deadline:
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            removed += 1
            paths.append(str(path))
        except Exception:
            continue
    return {"removed": removed, "paths": paths}


def run_maintenance(payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    task_id = make_task_id("maint")
    source_user = str(payload.get("source_user") or "").strip()
    create_task(
        task_id=task_id,
        task_type="maintenance_run",
        status="running",
        phase="retry",
        platform="all",
        source_user=source_user,
        payload={"request": payload},
        result={"status": "running"},
    )

    try:
        retry_candidates = list_retryable_tasks()
        retry_results: list[dict[str, Any]] = []

        for task in retry_candidates:
            one_id = str(task.get("task_id") or "").strip()
            if not one_id:
                continue
            if dry_run:
                retry_results.append({"task_id": one_id, "status": "dry_run"})
                continue
            result = retry_publish_task({"task_id": one_id, "approver": source_user}, dry_run=False)
            retry_results.append(result)

        keep_days = max(3, int(payload.get("keep_days") or os.getenv("AUTOMATION_ARTIFACT_KEEP_DAYS", "14")))
        cleanup = _cleanup_old_dirs(SCREENSHOT_DIR, keep_days=keep_days)
        cleanup_runs = _cleanup_old_dirs(AUTOMATION_ROOT / "runs", keep_days=keep_days)

        result = {
            "status": "success",
            "retry_total": len(retry_candidates),
            "retry_results": retry_results,
            "cleanup_screenshots": cleanup,
            "cleanup_runs": cleanup_runs,
            "dry_run": dry_run,
        }
        update_task(task_id, status="success", phase="completed", result_json=_json_dumps(result), error_text="")
        add_task_log(task_id, "maintenance_completed", result)
        run_log = append_run_log("maintenance_run", {"task_id": task_id, "retry_total": len(retry_candidates), "cleanup": cleanup})
        return {"status": "success", "task_id": task_id, "run_log": run_log, "result": result}

    except Exception as exc:
        error_text = str(exc)
        update_task(task_id, status="error", phase="retry", error_text=error_text)
        add_task_log(task_id, "maintenance_failed", {"error": error_text})
        dead_log = append_dead_letter("maintenance_failed", {"task_id": task_id, "error": error_text})
        return {"status": "error", "task_id": task_id, "errors": [error_text], "dead_letter_log": dead_log}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automation maintenance runner")
    parser.add_argument("--payload-file", default="")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    payload: dict[str, Any] = {}
    if args.payload_file:
        raw = json.loads(Path(args.payload_file).read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            payload.update(raw)
    result = run_maintenance(payload, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if str(result.get("status") or "") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))
