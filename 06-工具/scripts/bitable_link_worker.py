#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Async worker: poll Bitable-backed Douyin jobs and write repo files."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any

from feishu_http_client import reply_message, send_text_message
from feishu_kb_orchestrator import (
    _load_settings,
    _now_iso,
    _run_git_sync_after_write,
    _run_ingest,
)
from link_async_jobs import (
    STATE_TIMEOUT,
    claim_due_jobs,
    complete_job_failed,
    complete_job_success,
    ensure_schema,
    get_job,
    is_expired,
    mark_job_pending,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_ROOT = REPO_ROOT / "06-工具" / "data" / "feishu-orchestrator"
RUN_LOG_DIR = LOG_ROOT / "runs"
DEAD_LETTER_DIR = LOG_ROOT / "dead-letter"
RETRYABLE_REASONS = {
    "not_from_bitable",
    "bitable_no_record",
    "bitable_record_without_text",
    "bitable_no_query_key",
    "bitable_disabled_or_not_configured",
    "content_too_short",
    "content_not_success",
}
BITABLE_SOURCES = {"bitable", "bitable_text"}


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _pick_ingest_meta(ingest: dict[str, Any]) -> dict[str, Any]:
    result = ingest.get("result") or {}
    details = result.get("details") or {}
    summary = details.get("summary") or {}

    route_status = str(result.get("link_route_status") or "").strip()
    if not route_status and _int(summary.get("link_total")) > 0:
        route_ok = _int(summary.get("link_route_success_count") or summary.get("link_success"))
        route_status = "success" if route_ok > 0 else "failed"

    content_status = str(result.get("link_content_status") or "").strip()
    if not content_status:
        if _int(summary.get("link_doc_saved_count") or summary.get("link_content_success_count")) > 0:
            content_status = "success"
        elif _int(summary.get("link_total")) > 0:
            content_status = "failed"

    content_chars = _int(result.get("link_content_chars"))
    if content_chars <= 0:
        content_chars = _int(summary.get("link_content_chars_total"))

    provider = str(
        result.get("link_provider")
        or result.get("provider")
        or summary.get("link_provider")
        or ""
    ).strip()
    quality_reason = str(result.get("link_quality_reason") or summary.get("link_quality_reason") or "").strip()
    text_source = str(result.get("link_text_source") or summary.get("link_text_source") or "").strip().lower()
    reject_reason = str(result.get("link_reject_reason") or summary.get("link_reject_reason") or "").strip()
    summary_detected = bool(result.get("link_summary_detected") or summary.get("link_summary_detected"))
    is_test = bool(result.get("link_is_test") or summary.get("link_is_test"))

    if not quality_reason and reject_reason:
        quality_reason = reject_reason
    if not reject_reason and quality_reason:
        reject_reason = quality_reason

    return {
        "route_status": route_status,
        "content_status": content_status,
        "content_chars": content_chars,
        "provider": provider,
        "quality_reason": quality_reason,
        "text_source": text_source,
        "reject_reason": reject_reason,
        "summary_detected": summary_detected,
        "is_test": is_test,
        "result": result,
    }


def _is_success(meta: dict[str, Any]) -> bool:
    return (
        str(meta.get("content_status") or "") == "success"
        and str(meta.get("text_source") or "").lower() in BITABLE_SOURCES
        and not bool(meta.get("summary_detected"))
    )


def _is_retryable(meta: dict[str, Any], ingest_status: str) -> bool:
    if _is_success(meta):
        return False
    reason = str(meta.get("reject_reason") or meta.get("quality_reason") or "").lower()
    if reason:
        return any(token in reason for token in RETRYABLE_REASONS)
    if ingest_status in {"error", "partial", "failed"}:
        return True
    return str(meta.get("content_status") or "") in {"", "pending", "failed"}


def _compose_success_reply(meta: dict[str, Any], git_sync: dict[str, Any] | None) -> str:
    chars = _int(meta.get("content_chars"))
    provider = str(meta.get("provider") or "bitable")
    source = str(meta.get("text_source") or "bitable")
    line = f"链接入库完成：正文已保存（来源 {source}/{provider}，字数 {chars}）。"
    if git_sync and str(git_sync.get("status") or "") == "success":
        commit = str(git_sync.get("commit") or "").strip()
        if commit:
            line += f"\nGit 同步成功：{commit[:12]}"
    return line


def _compose_fail_reply(reason: str, *, timeout: bool = False) -> str:
    detail = str(reason or "unknown").strip() or "unknown"
    if timeout:
        return f"链接入库失败：等待多维表文案超时（{detail}）。"
    return f"链接入库失败：{detail}。"


def _safe_reply(job: dict[str, Any], text: str) -> None:
    body = str(text or "").strip()
    if not body:
        return

    message_id = str(job.get("message_id") or "").strip()
    chat_id = str(job.get("chat_id") or "").strip()
    errors: list[str] = []

    if message_id:
        try:
            reply_message(message_id=message_id, text=body)
            return
        except Exception as exc:
            errors.append(f"reply_message:{exc}")

    if chat_id:
        try:
            send_text_message(receive_id=chat_id, receive_id_type="chat_id", text=body)
            return
        except Exception as exc:
            errors.append(f"send_text_message:{exc}")

    if errors:
        _append_jsonl(
            DEAD_LETTER_DIR / f"{dt.date.today().isoformat()}.jsonl",
            {
                "ts": _now_iso(),
                "event_ref": str(job.get("event_ref") or ""),
                "job_id": str(job.get("job_id") or ""),
                "reason": "reply_failed",
                "errors": errors,
            },
        )


def _append_run_log(job: dict[str, Any], *, status: str, ingest: dict[str, Any], git_sync: dict[str, Any] | None, errors: list[str]) -> None:
    meta = _pick_ingest_meta(ingest)
    run_log = RUN_LOG_DIR / f"{dt.date.today().isoformat()}.jsonl"
    _append_jsonl(
        run_log,
        {
            "ts": _now_iso(),
            "event_ref": str(job.get("event_ref") or ""),
            "status": status,
            "intent": {
                "automation": False,
                "automation_kind": "",
                "ingest": True,
                "skill": False,
                "urls": [str(job.get("url") or "")],
                "skill_id": "",
                "skill_platform": "",
                "skill_trigger": "",
                "ingest_trigger": "url",
            },
            "ingest_trigger": "url",
            "plain_chat_fallback_used": False,
            "git_sync_status": str((git_sync or {}).get("status") or ""),
            "git_sync_commit": str((git_sync or {}).get("commit") or ""),
            "link_route_status": meta.get("route_status"),
            "link_content_status": meta.get("content_status"),
            "link_content_chars": meta.get("content_chars"),
            "link_provider": meta.get("provider"),
            "link_is_test": bool(meta.get("is_test")),
            "link_quality_reason": meta.get("quality_reason"),
            "link_summary_detected": bool(meta.get("summary_detected")),
            "link_text_source": meta.get("text_source"),
            "link_reject_reason": meta.get("reject_reason"),
            "async_worker": True,
            "async_job_id": str(job.get("job_id") or ""),
            "errors": errors,
        },
    )


def _process_job(job: dict[str, Any], *, dry_run: bool) -> None:
    settings = _load_settings()
    job_id = str(job.get("job_id") or "").strip()
    event_ref = str(job.get("event_ref") or "").strip()
    source_ref = str(job.get("source_ref") or "").strip() or "feishu-async-worker"
    source_time = str(job.get("source_time") or "").strip() or _now_iso()
    source_user = str(job.get("source_user") or "").strip()
    url = str(job.get("url") or "").strip()
    meta = job.get("meta_json") if isinstance(job.get("meta_json"), dict) else {}
    text = str((meta or {}).get("text") or "").strip()

    if not event_ref or not url:
        complete_job_failed(job_id, error="invalid_job_payload", state="failed")
        return

    if is_expired(job):
        reason = "timeout_waiting_bitable_text"
        complete_job_failed(job_id, error=reason, state=STATE_TIMEOUT)
        _safe_reply(job, _compose_fail_reply(reason, timeout=True))
        return

    ingest = _run_ingest(
        settings=settings,
        text=text,
        urls=[url],
        event_ref=event_ref,
        source_ref=source_ref,
        source_time=source_time,
        dry_run=dry_run,
    )
    ingest_status = str(ingest.get("status") or "").strip().lower()
    meta_info = _pick_ingest_meta(ingest)

    if _is_success(meta_info):
        touched = ((ingest.get("result") or {}).get("touched_files") or [])
        git_sync: dict[str, Any] | None = None
        if not dry_run:
            git_sync = _run_git_sync_after_write(
                settings=settings,
                event_ref=event_ref,
                kind="ingest",
                paths=[str(item) for item in touched if str(item).strip()],
                dry_run=False,
            )

        errors: list[str] = []
        if git_sync and str(git_sync.get("status") or "") == "error":
            errors.append(str(git_sync.get("message") or git_sync.get("stderr") or "git_sync_failed"))

        final_status = "success" if not errors else "partial"
        complete_job_success(
            job_id,
            result={"ingest": ingest, "git_sync": git_sync or {}, "status": final_status},
        )
        _append_run_log(job, status=final_status, ingest=ingest, git_sync=git_sync, errors=errors)
        _safe_reply(job, _compose_success_reply(meta_info, git_sync))
        return

    if _is_retryable(meta_info, ingest_status):
        if is_expired(job):
            reason = str(meta_info.get("reject_reason") or meta_info.get("quality_reason") or "timeout_waiting_bitable_text")
            complete_job_failed(
                job_id,
                error=reason,
                result={"ingest": ingest, "status": "timeout"},
                state=STATE_TIMEOUT,
            )
            _append_run_log(job, status="error", ingest=ingest, git_sync=None, errors=[reason])
            _safe_reply(job, _compose_fail_reply(reason, timeout=True))
            return

        mark_job_pending(
            job_id,
            next_poll_seconds=max(10, int(settings.link_async_poll_interval_sec)),
            error=str(meta_info.get("reject_reason") or meta_info.get("quality_reason") or "waiting_bitable_text"),
            result={"ingest": ingest, "status": "pending"},
        )
        return

    reason = str(meta_info.get("reject_reason") or meta_info.get("quality_reason") or "content_not_success").strip()
    complete_job_failed(
        job_id,
        error=reason,
        result={"ingest": ingest, "status": "failed"},
        state="failed",
    )
    _append_run_log(job, status="error", ingest=ingest, git_sync=None, errors=[reason])
    _safe_reply(job, _compose_fail_reply(reason))


def run_once(*, limit: int, dry_run: bool) -> dict[str, Any]:
    ensure_schema()
    jobs = claim_due_jobs(
        limit=max(1, int(limit)),
        stale_processing_seconds=max(60, int(os.getenv("FEISHU_LINK_ASYNC_STALE_SEC", "300"))),
    )
    stats = {"picked": len(jobs), "success": 0, "failed": 0, "pending": 0}

    for job in jobs:
        job_id = str(job.get("job_id") or "").strip()
        try:
            _process_job(job, dry_run=dry_run)
            latest_state = str((get_job(job_id) or {}).get("state") or "")
            if latest_state == "success":
                stats["success"] += 1
            elif latest_state in {"failed", "timeout"}:
                stats["failed"] += 1
            elif latest_state in {"pending", "processing"}:
                stats["pending"] += 1
        except Exception as exc:
            msg = str(exc)
            if is_expired(job):
                complete_job_failed(job_id, error=msg, state=STATE_TIMEOUT, result={"error": msg})
                _safe_reply(job, _compose_fail_reply(msg, timeout=True))
                stats["failed"] += 1
            else:
                mark_job_pending(
                    job_id,
                    next_poll_seconds=max(10, int(os.getenv("FEISHU_LINK_ASYNC_POLL_INTERVAL_SEC", "60"))),
                    error=msg,
                    result={"error": msg},
                )
                stats["pending"] += 1
            _append_jsonl(
                DEAD_LETTER_DIR / f"{dt.date.today().isoformat()}.jsonl",
                {
                    "ts": _now_iso(),
                    "event_ref": str(job.get("event_ref") or ""),
                    "job_id": job_id,
                    "reason": "worker_exception",
                    "error": msg,
                },
            )
    return stats


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll Bitable async jobs and ingest completed text.")
    parser.add_argument("--once", action="store_true", help="Run one batch and exit.")
    parser.add_argument("--loop", action="store_true", help="Run forever with sleep interval.")
    parser.add_argument("--sleep-sec", type=int, default=int(os.getenv("FEISHU_LINK_ASYNC_POLL_INTERVAL_SEC", "60")))
    parser.add_argument("--limit", type=int, default=int(os.getenv("FEISHU_LINK_ASYNC_BATCH", "5")))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    loop_mode = bool(args.loop) or not bool(args.once)
    sleep_sec = max(10, int(args.sleep_sec))

    if bool(args.once):
        stats = run_once(limit=max(1, int(args.limit)), dry_run=bool(args.dry_run))
        print(json.dumps({"status": "ok", "mode": "once", "stats": stats}, ensure_ascii=False))
        return 0

    while True:
        stats = run_once(limit=max(1, int(args.limit)), dry_run=bool(args.dry_run))
        print(json.dumps({"status": "ok", "mode": "loop", "stats": stats, "ts": _now_iso()}, ensure_ascii=False))
        if not loop_mode:
            break
        time.sleep(sleep_sec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))
