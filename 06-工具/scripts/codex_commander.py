#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex commander: run skill generation tasks with fixed worker concurrency."""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import json
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from feishu_skill_runner import DEFAULT_MODEL, run_skill_task

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_ROOT = REPO_ROOT / "06-工具" / "data" / "codex-commander"
RUN_LOG_DIR = LOG_ROOT / "runs"
DEAD_LETTER_DIR = LOG_ROOT / "dead-letter"


@dataclasses.dataclass(frozen=True)
class CommanderTask:
    index: int
    task_id: str
    skill_id: str
    brief: str
    platform: str
    date: str
    model: str
    event_ref: str
    source_ref: str
    context_files: tuple[str, ...]


LOCKS_GUARD = threading.Lock()
LOCKS_BY_KEY: dict[str, threading.Lock] = {}


def _today() -> str:
    return dt.date.today().isoformat()


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _ensure_lock(key: str) -> threading.Lock:
    with LOCKS_GUARD:
        if key not in LOCKS_BY_KEY:
            LOCKS_BY_KEY[key] = threading.Lock()
        return LOCKS_BY_KEY[key]


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _load_tasks(payload: Any) -> list[CommanderTask]:
    if isinstance(payload, dict):
        if isinstance(payload.get("tasks"), list):
            raw_tasks = payload["tasks"]
        else:
            raw_tasks = [payload]
    elif isinstance(payload, list):
        raw_tasks = payload
    else:
        raise ValueError("task payload must be dict or list")

    tasks: list[CommanderTask] = []
    for idx, item in enumerate(raw_tasks):
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id") or item.get("skill") or "").strip()
        brief = str(item.get("brief") or item.get("input") or "").strip()
        if not skill_id:
            raise ValueError(f"task[{idx}] missing skill_id")
        if not brief:
            raise ValueError(f"task[{idx}] missing brief")
        task_id = str(item.get("task_id") or item.get("id") or f"task-{idx+1}").strip() or f"task-{idx+1}"
        tasks.append(
            CommanderTask(
                index=idx,
                task_id=task_id,
                skill_id=skill_id,
                brief=brief,
                platform=str(item.get("platform") or "").strip(),
                date=str(item.get("date") or "").strip(),
                model=str(item.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
                event_ref=str(item.get("event_ref") or "").strip(),
                source_ref=str(item.get("source_ref") or "").strip(),
                context_files=tuple(
                    str(path or "").strip()
                    for path in (item.get("context_files") if isinstance(item.get("context_files"), list) else [])
                    if str(path or "").strip()
                ),
            )
        )
    if not tasks:
        raise ValueError("no runnable tasks")
    return tasks


def _task_conflict_key(task: CommanderTask) -> str:
    date = task.date or _today()
    platform = task.platform or "通用"
    return f"{platform}|{date}"


def _run_one_task(
    task: CommanderTask,
    *,
    codex_cli: str,
    timeout_sec: int,
    max_retries: int,
    dry_run: bool,
) -> dict[str, Any]:
    started = time.time()
    lock_key = _task_conflict_key(task)
    task_lock = _ensure_lock(lock_key)
    last_error = ""

    for attempt in range(1, max_retries + 2):
        try:
            with task_lock:
                result = run_skill_task(
                    skill_id=task.skill_id,
                    brief=task.brief,
                    platform=task.platform,
                    model=task.model,
                    event_ref=task.event_ref,
                    source_ref=task.source_ref or task.task_id,
                    date_str=task.date,
                    timeout_sec=timeout_sec,
                    codex_cli=codex_cli,
                    context_files=list(task.context_files),
                    dry_run=dry_run,
                )
            result["task_id"] = task.task_id
            result["attempt"] = attempt
            result["conflict_key"] = lock_key
            result["started_at"] = _now_iso()
            result["elapsed_total_ms"] = int((time.time() - started) * 1000)
            return result
        except Exception as exc:  # keep retry behavior deterministic
            last_error = str(exc)
            if attempt > max_retries:
                break

    return {
        "status": "error",
        "task_id": task.task_id,
        "skill_id": task.skill_id,
        "platform": task.platform or "通用",
        "date": task.date or _today(),
        "attempt": max_retries + 1,
        "conflict_key": lock_key,
        "saved_files": [],
        "full_text": "",
        "errors": [last_error or "unknown commander error"],
        "started_at": _now_iso(),
        "elapsed_total_ms": int((time.time() - started) * 1000),
    }


def execute_tasks(
    *,
    payload: Any,
    workers: int,
    codex_cli: str = "",
    timeout_sec: int = 1800,
    max_retries: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    tasks = _load_tasks(payload)
    worker_count = max(1, min(8, int(workers)))

    run_id = f"cmd-{int(time.time())}"
    run_log = RUN_LOG_DIR / f"{_today()}.jsonl"
    dead_log = DEAD_LETTER_DIR / f"{_today()}.jsonl"

    results: list[dict[str, Any] | None] = [None for _ in tasks]
    errors: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
        future_map = {
            pool.submit(
                _run_one_task,
                task,
                codex_cli=codex_cli,
                timeout_sec=timeout_sec,
                max_retries=max_retries,
                dry_run=dry_run,
            ): task
            for task in tasks
        }
        for future in concurrent.futures.as_completed(future_map):
            task = future_map[future]
            try:
                result = future.result()
            except Exception as exc:  # executor-level unexpected failure
                result = {
                    "status": "error",
                    "task_id": task.task_id,
                    "skill_id": task.skill_id,
                    "platform": task.platform or "通用",
                    "date": task.date or _today(),
                    "saved_files": [],
                    "full_text": "",
                    "errors": [str(exc)],
                    "traceback": traceback.format_exc(),
                    "started_at": _now_iso(),
                    "elapsed_total_ms": 0,
                }
            results[task.index] = result

            _append_jsonl(
                run_log,
                {
                    "run_id": run_id,
                    "ts": _now_iso(),
                    "task_id": task.task_id,
                    "skill_id": task.skill_id,
                    "status": result.get("status"),
                    "platform": result.get("platform"),
                    "date": result.get("date"),
                    "saved_files": result.get("saved_files") or [],
                    "errors": result.get("errors") or [],
                },
            )
            if result.get("status") != "success":
                _append_jsonl(
                    dead_log,
                    {
                        "run_id": run_id,
                        "ts": _now_iso(),
                        "task_id": task.task_id,
                        "payload": dataclasses.asdict(task),
                        "result": result,
                    },
                )
                msg = f"{task.task_id}: {(result.get('errors') or ['unknown error'])[0]}"
                errors.append(msg)

    final_results = [item for item in results if isinstance(item, dict)]
    success_count = sum(1 for item in final_results if item.get("status") == "success")
    fail_count = len(final_results) - success_count
    status = "success" if fail_count == 0 else ("partial" if success_count > 0 else "error")

    return {
        "run_id": run_id,
        "status": status,
        "workers": worker_count,
        "summary": {
            "total": len(final_results),
            "success": success_count,
            "failed": fail_count,
            "max_retries": max_retries,
            "dry_run": dry_run,
        },
        "results": final_results,
        "errors": errors,
        "run_log": run_log.relative_to(REPO_ROOT).as_posix(),
        "dead_letter_log": dead_log.relative_to(REPO_ROOT).as_posix(),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run multiple skill tasks with 2-worker orchestration.")
    parser.add_argument("--task-json", default="", help="Task payload JSON string or JSON file path.")
    parser.add_argument("--workers", type=int, default=2, help="Worker count. Default: 2")
    parser.add_argument("--codex-cli", default="", help="Override codex cli path.")
    parser.add_argument("--timeout-sec", type=int, default=1800, help="Task timeout seconds.")
    parser.add_argument("--max-retries", type=int, default=1, help="Retry once by default.")
    parser.add_argument("--dry-run", action="store_true", help="Do not invoke codex or write generated files.")
    return parser.parse_args(argv)


def _load_payload(task_json: str) -> Any:
    raw = str(task_json or "").strip()
    if not raw:
        raise ValueError("task-json is required")
    candidate = Path(raw)
    if candidate.exists() and candidate.is_file():
        # PowerShell's UTF-8 output often includes BOM; accept it explicitly.
        return json.loads(candidate.read_text(encoding="utf-8-sig", errors="ignore"))
    if raw.startswith("\ufeff"):
        raw = raw.lstrip("\ufeff")
    return json.loads(raw)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        payload = _load_payload(args.task_json)
        result = execute_tasks(
            payload=payload,
            workers=args.workers,
            codex_cli=args.codex_cli,
            timeout_sec=max(30, args.timeout_sec),
            max_retries=max(0, args.max_retries),
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in {"success", "partial"} else 1
    except Exception as exc:
        out = {"status": "error", "errors": [str(exc)]}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
