#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Async worker: poll Bitable-backed Douyin jobs and write repo files."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from feishu_http_client import ensure_bitable_link_record, reply_message, send_text_message
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
    update_notify_status,
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
ASR_SOURCES = {"asr"}


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


def _current_pipeline_mode() -> str:
    mode = str(os.getenv("INGEST_DOUYIN_PIPELINE_MODE") or "asr_primary").strip().lower()
    if mode not in {"asr_primary", "bitable_primary", "bitable_only"}:
        return "asr_primary"
    return mode


def _allowed_sources_for_mode(pipeline_mode: str) -> set[str]:
    if pipeline_mode == "bitable_only":
        return set(BITABLE_SOURCES)
    if pipeline_mode in {"bitable_primary", "asr_primary"}:
        return set(BITABLE_SOURCES | ASR_SOURCES)
    return set(BITABLE_SOURCES | ASR_SOURCES)


def _is_success(meta: dict[str, Any], *, pipeline_mode: str) -> bool:
    allowed_sources = _allowed_sources_for_mode(pipeline_mode)
    return (
        str(meta.get("content_status") or "") == "success"
        and str(meta.get("text_source") or "").lower() in allowed_sources
        and not bool(meta.get("summary_detected"))
    )


def _is_retryable(meta: dict[str, Any], ingest_status: str, *, pipeline_mode: str) -> bool:
    if _is_success(meta, pipeline_mode=pipeline_mode):
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


_MID_RE = re.compile(r"(?:^|[\s,;|])(?:message_id|msg_id)\s*[:=]\s*([A-Za-z0-9_-]+)", re.IGNORECASE)
_CID_RE = re.compile(r"(?:^|[\s,;|])(?:chat_id|open_chat_id)\s*[:=]\s*([A-Za-z0-9_-]+)", re.IGNORECASE)


def _extract_ids_from_source_ref(source_ref: str) -> tuple[str, str]:
    raw = str(source_ref or "").strip()
    if not raw:
        return "", ""
    mid = _MID_RE.search(raw)
    cid = _CID_RE.search(raw)
    return (
        str(mid.group(1) if mid else "").strip(),
        str(cid.group(1) if cid else "").strip(),
    )


def _safe_reply(job: dict[str, Any], text: str) -> tuple[bool, list[str]]:
    body = str(text or "").strip()
    if not body:
        return True, []

    message_id = str(job.get("message_id") or "").strip()
    chat_id = str(job.get("chat_id") or "").strip()
    meta = job.get("meta_json") if isinstance(job.get("meta_json"), dict) else {}
    if not message_id:
        message_id = str((meta or {}).get("message_id") or (meta or {}).get("msg_id") or "").strip()
    if not chat_id:
        chat_id = str((meta or {}).get("chat_id") or (meta or {}).get("open_chat_id") or "").strip()
    if not message_id or not chat_id:
        s_mid, s_cid = _extract_ids_from_source_ref(str(job.get("source_ref") or ""))
        if not message_id and s_mid:
            message_id = s_mid
        if not chat_id and s_cid:
            chat_id = s_cid
    errors: list[str] = []

    if message_id:
        try:
            reply_message(message_id=message_id, text=body)
            return True, []
        except Exception as exc:
            errors.append(f"reply_message:{exc}")

    if chat_id:
        try:
            send_text_message(receive_id=chat_id, receive_id_type="chat_id", text=body)
            return True, []
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
        return False, errors
    return False, ["reply_target_missing"]


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
    pipeline_mode = _current_pipeline_mode()
    job_id = str(job.get("job_id") or "").strip()
    event_ref = str(job.get("event_ref") or "").strip()
    source_ref = str(job.get("source_ref") or "").strip() or "feishu-async-worker"
    source_time = str(job.get("source_time") or "").strip() or _now_iso()
    source_user = str(job.get("source_user") or "").strip()
    url = str(job.get("url") or "").strip()
    meta = job.get("meta_json") if isinstance(job.get("meta_json"), dict) else {}
    previous_result = job.get("result_json") if isinstance(job.get("result_json"), dict) else {}
    text = str((meta or {}).get("text") or "").strip()

    if not event_ref or not url:
        complete_job_failed(job_id, error="invalid_job_payload", state="failed")
        return

    # Notification retry mode: avoid re-ingest after content already succeeded.
    prev_status = str((previous_result or {}).get("status") or "").strip().lower()
    prev_ingest = (previous_result or {}).get("ingest") if isinstance((previous_result or {}).get("ingest"), dict) else None
    prev_git_sync = (previous_result or {}).get("git_sync") if isinstance((previous_result or {}).get("git_sync"), dict) else {}
    if prev_status in {"notify_pending", "success", "partial"} and prev_ingest:
        prev_meta = _pick_ingest_meta(prev_ingest)
        if _is_success(prev_meta, pipeline_mode=pipeline_mode):
            ok, _ = _safe_reply(job, _compose_success_reply(prev_meta, prev_git_sync))
            if ok:
                complete_job_success(
                    job_id,
                    result={"ingest": prev_ingest, "git_sync": prev_git_sync, "status": "success"},
                )
                update_notify_status(job_id, notify_state="sent", notify_error="", increment_try=False)
            else:
                mark_job_pending(
                    job_id,
                    next_poll_seconds=max(10, int(settings.link_async_poll_interval_sec)),
                    error="notify_retry",
                    result={"ingest": prev_ingest, "git_sync": prev_git_sync, "status": "notify_pending"},
                )
                update_notify_status(job_id, notify_state="pending", notify_error="notify_retry", increment_try=True)
            return

    # Best-effort safeguard: if async entry was queued but Bitable row is missing,
    # create the row here so upstream automation has a concrete target.
    bitable_app_token = str(os.getenv("BITABLE_APP_TOKEN") or "").strip()
    bitable_table_id = str(os.getenv("BITABLE_TABLE_ID") or "").strip()
    if bitable_app_token and bitable_table_id:
        try:
            ensure_bitable_link_record(
                app_token=bitable_app_token,
                table_id=bitable_table_id,
                url=url,
                link_field=str(os.getenv("BITABLE_LINK_FIELD") or "视频链接").strip() or "视频链接",
                video_id_field=str(os.getenv("BITABLE_VIDEO_ID_FIELD") or "视频ID").strip() or "视频ID",
                view_id=str(os.getenv("BITABLE_VIEW_ID") or "").strip(),
                fallback_full_scan=True,
            )
        except Exception:
            # keep ingest flow running; final failure reason comes from ingest result
            pass

    if is_expired(job):
        reason = "timeout_waiting_bitable_text"
        complete_job_failed(job_id, error=reason, state=STATE_TIMEOUT)
        notify_ok, notify_errors = _safe_reply(job, _compose_fail_reply(reason, timeout=True))
        if notify_ok:
            update_notify_status(job_id, notify_state="sent", notify_error="", increment_try=False)
        else:
            update_notify_status(job_id, notify_state="pending", notify_error="; ".join(notify_errors), increment_try=True)
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

    if _is_success(meta_info, pipeline_mode=pipeline_mode):
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
        result_payload = {"ingest": ingest, "git_sync": git_sync or {}, "status": final_status}
        complete_job_success(job_id, result=result_payload)
        _append_run_log(job, status=final_status, ingest=ingest, git_sync=git_sync, errors=errors)
        notify_ok, notify_errors = _safe_reply(job, _compose_success_reply(meta_info, git_sync))
        if not notify_ok:
            mark_job_pending(
                job_id,
                next_poll_seconds=max(10, int(settings.link_async_poll_interval_sec)),
                error="; ".join(notify_errors),
                result={**result_payload, "status": "notify_pending", "notify_errors": notify_errors},
            )
            update_notify_status(job_id, notify_state="pending", notify_error="; ".join(notify_errors), increment_try=True)
        else:
            update_notify_status(job_id, notify_state="sent", notify_error="", increment_try=False)
        return

    if _is_retryable(meta_info, ingest_status, pipeline_mode=pipeline_mode):
        if is_expired(job):
            reason = str(meta_info.get("reject_reason") or meta_info.get("quality_reason") or "timeout_waiting_bitable_text")
            complete_job_failed(
                job_id,
                error=reason,
                result={"ingest": ingest, "status": "timeout"},
                state=STATE_TIMEOUT,
            )
            _append_run_log(job, status="error", ingest=ingest, git_sync=None, errors=[reason])
            notify_ok, notify_errors = _safe_reply(job, _compose_fail_reply(reason, timeout=True))
            if notify_ok:
                update_notify_status(job_id, notify_state="sent", notify_error="", increment_try=False)
            else:
                update_notify_status(job_id, notify_state="pending", notify_error="; ".join(notify_errors), increment_try=True)
            return

        mark_job_pending(
            job_id,
            next_poll_seconds=max(10, int(settings.link_async_poll_interval_sec)),
            error=str(meta_info.get("reject_reason") or meta_info.get("quality_reason") or "waiting_bitable_text"),
            result={"ingest": ingest, "status": "pending"},
        )
        update_notify_status(job_id, notify_state="pending", notify_error="", increment_try=False)
        return

    reason = str(meta_info.get("reject_reason") or meta_info.get("quality_reason") or "content_not_success").strip()
    complete_job_failed(
        job_id,
        error=reason,
        result={"ingest": ingest, "status": "failed"},
        state="failed",
    )
    _append_run_log(job, status="error", ingest=ingest, git_sync=None, errors=[reason])
    notify_ok, notify_errors = _safe_reply(job, _compose_fail_reply(reason))
    if notify_ok:
        update_notify_status(job_id, notify_state="sent", notify_error="", increment_try=False)
    else:
        update_notify_status(job_id, notify_state="pending", notify_error="; ".join(notify_errors), increment_try=True)


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
