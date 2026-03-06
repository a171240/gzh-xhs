#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Topic production pipeline: 待深化 -> 待生产 -> 多平台生成."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from feishu_skill_runner import DEFAULT_MODEL, build_skill_registry, resolve_skill, run_skill_task
from skill_context_resolver import resolve_context_files
from topic_brief_builder import build_brief_from_payload
from topic_doc_utils import (
    dump_frontmatter,
    ensure_required_topic_meta,
    normalize_platforms,
    parse_frontmatter,
    parse_sections,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

DEEPEN_DIR = REPO_ROOT / "01-选题管理" / "01-待深化"
PRODUCE_DIR = REPO_ROOT / "01-选题管理" / "02-待生产"
WHITELIST_FILE = PRODUCE_DIR / "00-首轮白名单.txt"

PIPELINE_ROOT = REPO_ROOT / "06-工具" / "data" / "feishu-orchestrator" / "topic-pipeline"
RUN_LOG_DIR = PIPELINE_ROOT / "runs"
STATE_FILE = PIPELINE_ROOT / "state.json"
HEARTBEAT_FILE = PIPELINE_ROOT / "heartbeat.json"

TOPIC_KEY_ORDER = ["date", "topic", "target", "platforms", "related", "status", "source"]
WAITING_STATUS = {"待生产", "生产失败"}
MAX_RETRY_PER_PLATFORM = 2
DEFAULT_BATCH_LIMIT = 3

PLATFORM_DISPATCH: dict[str, tuple[str, str]] = {
    "公众号": ("wechat", "公众号"),
    "小红书": ("xhs", "小红书"),
    "抖音": ("短视频脚本生产", "抖音"),
    "视频号": ("短视频脚本生产", "视频号"),
}


@dataclasses.dataclass
class TopicDoc:
    path: Path
    rel_path: str
    text: str
    body: str
    meta: dict[str, Any]
    sections: dict[str, str]
    content_hash: str
    mtime: float


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso_now() -> str:
    return _now_utc().isoformat(timespec="seconds")


def _shanghai_tz() -> dt.tzinfo:
    try:
        return ZoneInfo("Asia/Shanghai")
    except Exception:
        return dt.timezone(dt.timedelta(hours=8))


def _now_shanghai() -> dt.datetime:
    return dt.datetime.now(_shanghai_tz())


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


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


def _normalize_rel(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT).as_posix()


def _file_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="ignore")).hexdigest()


def _load_state() -> dict[str, Any]:
    now_ts = time.time()
    default = {
        "version": 1,
        "created_at": _iso_now(),
        "updated_at": _iso_now(),
        "baseline_ts": now_ts,
        "first_round_completed": False,
        "last_batch_date": "",
        "seen_hashes": [],
        "retry_counts": {},
    }
    state = _read_json(STATE_FILE, default)
    if not isinstance(state, dict):
        state = dict(default)
    state.setdefault("version", 1)
    state.setdefault("created_at", _iso_now())
    state.setdefault("updated_at", _iso_now())
    state.setdefault("baseline_ts", now_ts)
    state.setdefault("first_round_completed", False)
    state.setdefault("last_batch_date", "")
    state.setdefault("seen_hashes", [])
    state.setdefault("retry_counts", {})
    if not isinstance(state.get("seen_hashes"), list):
        state["seen_hashes"] = []
    if not isinstance(state.get("retry_counts"), dict):
        state["retry_counts"] = {}
    return state


def _save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = _iso_now()
    seen = [str(item).strip() for item in state.get("seen_hashes", []) if str(item).strip()]
    state["seen_hashes"] = list(dict.fromkeys(seen))[-10000:]
    retry_counts = state.get("retry_counts") or {}
    if isinstance(retry_counts, dict):
        clean_retry: dict[str, int] = {}
        for key, value in retry_counts.items():
            try:
                count = int(value)
            except Exception:
                continue
            if count > 0:
                clean_retry[str(key)] = count
        state["retry_counts"] = clean_retry
    _write_json(STATE_FILE, state)


def _read_topic_doc(path: Path) -> TopicDoc | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    meta, body = parse_frontmatter(text)
    if not isinstance(meta, dict):
        meta = {}
    sections = parse_sections(body)
    return TopicDoc(
        path=path,
        rel_path=_normalize_rel(path),
        text=text,
        body=body,
        meta=meta,
        sections=sections,
        content_hash=_file_hash(text),
        mtime=path.stat().st_mtime if path.exists() else 0,
    )


def _collect_deepen_candidates() -> list[TopicDoc]:
    out: list[TopicDoc] = []
    if not DEEPEN_DIR.exists():
        return out
    for path in sorted(DEEPEN_DIR.rglob("*.md")):
        if not path.is_file():
            continue
        doc = _read_topic_doc(path)
        if not doc:
            continue
        if str(doc.meta.get("status") or "").strip() != "待深化":
            continue
        out.append(doc)
    return out


def _collect_production_docs() -> list[TopicDoc]:
    out: list[TopicDoc] = []
    if not PRODUCE_DIR.exists():
        return out
    for path in sorted(PRODUCE_DIR.rglob("*.md")):
        if not path.is_file():
            continue
        doc = _read_topic_doc(path)
        if not doc:
            continue
        status = str(doc.meta.get("status") or "").strip()
        if status in WAITING_STATUS:
            out.append(doc)
    return out


def _read_whitelist() -> list[str]:
    if not WHITELIST_FILE.exists():
        return []
    items: list[str] = []
    for raw in WHITELIST_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = str(raw or "").strip()
        if not line or line.startswith("#"):
            continue
        rel = line.replace("\\", "/").lstrip("./")
        if rel:
            items.append(rel)
    return list(dict.fromkeys(items))


def _is_known_platform(value: str) -> bool:
    return value in PLATFORM_DISPATCH


def _normalize_dispatch_platforms(value: Any) -> list[str]:
    platforms = normalize_platforms(value)
    out: list[str] = []
    seen: set[str] = set()
    for item in platforms:
        text = str(item).strip()
        if text in {"抖音口播", "抖音脚本"}:
            text = "抖音"
        elif text in {"视频号口播", "视频号脚本"}:
            text = "视频号"
        if not _is_known_platform(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _allocate_target(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    idx = 2
    while True:
        candidate = path.with_name(f"{stem}-{idx}{suffix}")
        if not candidate.exists():
            return candidate
        idx += 1


def _write_topic_doc(path: Path, *, meta: dict[str, Any], body: str) -> None:
    normalized = ensure_required_topic_meta(meta)
    text = dump_frontmatter(normalized, body, key_order=TOPIC_KEY_ORDER)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _mark_whitelist_completed(state: dict[str, Any], *, now_ts: float) -> None:
    state["first_round_completed"] = True
    state["baseline_ts"] = now_ts


def _plan_migrations(state: dict[str, Any], deepen_docs: list[TopicDoc]) -> tuple[list[TopicDoc], dict[str, Any]]:
    first_round_completed = bool(state.get("first_round_completed"))
    seen_hashes = {str(item) for item in state.get("seen_hashes", [])}
    mode = "incremental"
    whitelist = _read_whitelist()
    whitelist_set = set(whitelist)

    if not first_round_completed:
        mode = "whitelist"
        if whitelist_set:
            selected = [doc for doc in deepen_docs if doc.rel_path in whitelist_set]
        else:
            selected = []
    else:
        baseline_ts = float(state.get("baseline_ts") or 0.0)
        selected = [doc for doc in deepen_docs if float(doc.mtime) >= baseline_ts]

    unique_selected: list[TopicDoc] = []
    duplicate_skipped = 0
    for doc in selected:
        if doc.content_hash in seen_hashes:
            duplicate_skipped += 1
            continue
        unique_selected.append(doc)

    whitelist_pending = 0
    if not first_round_completed and whitelist:
        for rel in whitelist:
            path = (REPO_ROOT / rel).resolve()
            if path.exists() and path.is_file():
                whitelist_pending += 1

    summary = {
        "mode": mode,
        "detected": len(deepen_docs),
        "selected": len(selected),
        "selected_unique": len(unique_selected),
        "duplicate_skipped": duplicate_skipped,
        "whitelist_total": len(whitelist),
        "whitelist_pending": whitelist_pending,
    }
    return unique_selected, summary


def _migrate_topics(
    *,
    state: dict[str, Any],
    docs: list[TopicDoc],
    dry_run: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen_hashes = set(str(item) for item in state.get("seen_hashes", []))

    migrated = 0
    for doc in docs:
        source_rel = doc.rel_path
        target_path = _allocate_target(PRODUCE_DIR / doc.path.name)
        target_rel = _normalize_rel(target_path)

        new_meta = ensure_required_topic_meta(doc.meta, source=source_rel)
        new_meta["status"] = "待生产"

        action = {
            "source": source_rel,
            "target": target_rel,
            "status_from": str(doc.meta.get("status") or ""),
            "status_to": "待生产",
            "platforms": _normalize_dispatch_platforms(new_meta.get("platforms")),
            "dry_run": dry_run,
        }
        actions.append(action)

        if dry_run:
            continue

        _write_topic_doc(target_path, meta=new_meta, body=doc.body)
        try:
            doc.path.unlink()
        except Exception:
            pass
        seen_hashes.add(doc.content_hash)
        migrated += 1

    if not dry_run:
        state["seen_hashes"] = sorted(seen_hashes)

    return actions, {"planned": len(actions), "migrated": migrated}


def _should_run_batch(state: dict[str, Any], *, force_batch: bool) -> tuple[bool, str]:
    if force_batch:
        return True, "forced"
    now_local = _now_shanghai()
    today = now_local.date().isoformat()
    due_now = now_local.hour >= 10
    already_ran = str(state.get("last_batch_date") or "") == today
    if due_now and not already_ran:
        return True, "daily_window"
    return False, "not_due"


def _sort_task_docs(docs: list[TopicDoc]) -> list[TopicDoc]:
    def sort_key(doc: TopicDoc) -> tuple[str, str]:
        date_text = str(doc.meta.get("date") or "")
        return (date_text, doc.path.name)

    return sorted(docs, key=sort_key)


def _retry_key(rel_path: str, platform: str) -> str:
    return f"{rel_path}|{platform}"


def _resolve_dispatch_skill(platform: str) -> tuple[str, str]:
    mapping = PLATFORM_DISPATCH.get(platform)
    if not mapping:
        raise KeyError(platform)
    skill_ref, target_platform = mapping
    skill = resolve_skill(build_skill_registry(), skill_ref)
    return skill.skill_id, target_platform


def _execute_one_task(
    *,
    state: dict[str, Any],
    doc: TopicDoc,
    model: str,
    dry_run: bool,
) -> dict[str, Any]:
    payload = {"meta": ensure_required_topic_meta(doc.meta), "sections": doc.sections, "body": doc.body}
    rel_path = doc.rel_path
    source_meta = payload["meta"]
    dispatch_platforms = _normalize_dispatch_platforms(source_meta.get("platforms"))

    if not dispatch_platforms:
        return {
            "task": rel_path,
            "status": "skipped",
            "reason": "no_supported_platforms",
            "platforms": [],
            "results": [],
        }

    if not dry_run:
        source_meta["status"] = "生产中"
        _write_topic_doc(doc.path, meta=source_meta, body=doc.body)

    retry_counts: dict[str, int] = state.get("retry_counts", {})
    platform_results: list[dict[str, Any]] = []
    has_error = False

    for platform in dispatch_platforms:
        try:
            skill_id, target_platform = _resolve_dispatch_skill(platform)
        except KeyError:
            continue
        retry_key = _retry_key(rel_path, target_platform)
        previous_retry = int(retry_counts.get(retry_key) or 0)
        if previous_retry >= MAX_RETRY_PER_PLATFORM:
            has_error = True
            platform_results.append(
                {
                    "platform": target_platform,
                    "skill_id": skill_id,
                    "status": "skipped_max_retry",
                    "retry": previous_retry,
                }
            )
            continue

        brief = build_brief_from_payload(payload, platform=target_platform)
        context_files = resolve_context_files(doc.path, platform=target_platform, skill_id=skill_id)
        try:
            result = run_skill_task(
                skill_id=skill_id,
                brief=brief,
                platform=target_platform,
                model=model or DEFAULT_MODEL,
                source_ref=f"topic-pipeline:{rel_path}",
                context_files=context_files,
                dry_run=dry_run,
            )
            ok = str(result.get("status") or "") == "success"
            if ok:
                retry_counts.pop(retry_key, None)
            else:
                retry_counts[retry_key] = previous_retry + 1
                has_error = True
            platform_results.append(
                {
                    "platform": target_platform,
                    "skill_id": skill_id,
                    "status": result.get("status"),
                    "saved_files": result.get("saved_files") or [],
                    "context_files_used": result.get("context_files_used") or [],
                    "context_warnings": result.get("context_warnings") or [],
                    "errors": result.get("errors") or [],
                    "retry": retry_counts.get(retry_key, 0),
                }
            )
        except Exception as exc:
            retry_counts[retry_key] = previous_retry + 1
            has_error = True
            platform_results.append(
                {
                    "platform": target_platform,
                    "skill_id": skill_id,
                    "status": "error",
                    "saved_files": [],
                    "context_files_used": context_files,
                    "context_warnings": [],
                    "errors": [str(exc)],
                    "retry": retry_counts.get(retry_key, 0),
                }
            )

    state["retry_counts"] = retry_counts

    final_status = "已生产" if not has_error else "生产失败"
    if not dry_run:
        source_meta["status"] = final_status
        _write_topic_doc(doc.path, meta=source_meta, body=doc.body)

    return {
        "task": rel_path,
        "status": final_status if not dry_run else ("dry_run_failed" if has_error else "dry_run_ok"),
        "platforms": dispatch_platforms,
        "results": platform_results,
    }


def _write_heartbeat(payload: dict[str, Any]) -> None:
    _write_json(HEARTBEAT_FILE, payload)


def run_pipeline_once(
    *,
    dry_run: bool = False,
    force_batch: bool = False,
    batch_limit: int = DEFAULT_BATCH_LIMIT,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    start = time.time()
    run_id = f"topic-{_now_shanghai().strftime('%Y%m%d%H%M%S')}"
    run_log = RUN_LOG_DIR / f"{_now_shanghai().date().isoformat()}.jsonl"
    PIPELINE_ROOT.mkdir(parents=True, exist_ok=True)
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)

    state = _load_state()
    now_ts = time.time()
    deepen_docs = _collect_deepen_candidates()
    planned_docs, plan_summary = _plan_migrations(state, deepen_docs)
    migration_actions, migration_stats = _migrate_topics(state=state, docs=planned_docs, dry_run=dry_run)

    if not dry_run and not bool(state.get("first_round_completed")):
        whitelist_entries = _read_whitelist()
        if not whitelist_entries:
            _mark_whitelist_completed(state, now_ts=now_ts)
        else:
            pending_count = 0
            for rel in whitelist_entries:
                path = (REPO_ROOT / rel).resolve()
                if path.exists() and path.is_file():
                    pending_count += 1
            if pending_count == 0:
                _mark_whitelist_completed(state, now_ts=now_ts)

    should_batch, batch_reason = _should_run_batch(state, force_batch=force_batch)
    batch_tasks_all = _sort_task_docs(_collect_production_docs())
    selected_tasks = batch_tasks_all[: max(1, int(batch_limit))]

    batch_results: list[dict[str, Any]] = []
    batch_failures = 0
    if should_batch:
        for doc in selected_tasks:
            result = _execute_one_task(state=state, doc=doc, model=model, dry_run=dry_run)
            batch_results.append(result)
            if str(result.get("status")).startswith("生产失败") or "failed" in str(result.get("status")):
                batch_failures += 1

        if not dry_run:
            state["last_batch_date"] = _now_shanghai().date().isoformat()

    if not dry_run:
        _save_state(state)

    elapsed_ms = int((time.time() - start) * 1000)
    status = "success" if batch_failures == 0 else "partial"
    summary = {
        "status": status,
        "run_id": run_id,
        "dry_run": dry_run,
        "force_batch": force_batch,
        "migration": {
            **plan_summary,
            **migration_stats,
            "actions": migration_actions,
        },
        "batch": {
            "triggered": should_batch,
            "reason": batch_reason,
            "max_batch": int(batch_limit),
            "selected": len(selected_tasks),
            "selected_tasks": [doc.rel_path for doc in selected_tasks],
            "results": batch_results,
        },
        "state_file": _normalize_rel(STATE_FILE),
        "run_log": _normalize_rel(run_log),
        "heartbeat_file": _normalize_rel(HEARTBEAT_FILE),
        "elapsed_ms": elapsed_ms,
    }

    _append_jsonl(
        run_log,
        {
            "ts": _iso_now(),
            "run_id": run_id,
            "status": status,
            "dry_run": dry_run,
            "force_batch": force_batch,
            "migration_selected": plan_summary.get("selected_unique"),
            "migration_migrated": migration_stats.get("migrated"),
            "batch_triggered": should_batch,
            "batch_reason": batch_reason,
            "batch_selected": len(selected_tasks),
            "batch_failures": batch_failures,
            "elapsed_ms": elapsed_ms,
        },
    )

    heartbeat = {
        "ts": _iso_now(),
        "run_id": run_id,
        "status": status,
        "dry_run": dry_run,
        "migration_selected": plan_summary.get("selected_unique"),
        "migration_migrated": migration_stats.get("migrated"),
        "batch_triggered": should_batch,
        "batch_selected": len(selected_tasks),
        "pending_deepen": len(_collect_deepen_candidates()),
        "pending_production": len(_collect_production_docs()),
    }
    _write_heartbeat(heartbeat)

    return summary


def run_pipeline_daemon(
    *,
    poll_seconds: int = 60,
    dry_run: bool = False,
    force_batch: bool = False,
    batch_limit: int = DEFAULT_BATCH_LIMIT,
    model: str = DEFAULT_MODEL,
) -> None:
    sleep_sec = max(5, int(poll_seconds))
    while True:
        try:
            result = run_pipeline_once(
                dry_run=dry_run,
                force_batch=force_batch,
                batch_limit=batch_limit,
                model=model,
            )
            _write_heartbeat(
                {
                    "ts": _iso_now(),
                    "mode": "daemon",
                    "last_run_id": result.get("run_id"),
                    "last_status": result.get("status"),
                    "poll_seconds": sleep_sec,
                    "dry_run": dry_run,
                }
            )
        except Exception as exc:
            _write_heartbeat(
                {
                    "ts": _iso_now(),
                    "mode": "daemon",
                    "last_status": "error",
                    "error": str(exc),
                    "poll_seconds": sleep_sec,
                    "dry_run": dry_run,
                }
            )
        time.sleep(sleep_sec)

