#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Two-phase publishing adapter for WeChat/XHS/Douyin."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from automation_state import (
    AUTOMATION_ROOT,
    REPO_ROOT,
    add_task_log,
    append_dead_letter,
    append_run_log,
    create_task,
    find_task_by_event_ref,
    get_task,
    make_task_id,
    update_task,
)


REQUEST_DIR = AUTOMATION_ROOT / "requests"
REQUEST_DIR.mkdir(parents=True, exist_ok=True)

PLATFORM_ALIASES = {
    "wechat": "wechat",
    "公众号": "wechat",
    "xhs": "xhs",
    "小红书": "xhs",
    "douyin": "douyin",
    "抖音": "douyin",
}


def _normalize_platform(value: str) -> str:
    return PLATFORM_ALIASES.get(str(value or "").strip().lower(), "")


def _normalize_mode(value: str, *, default: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    if raw in {"draft", "publish", "schedule"}:
        return raw
    if raw in {"prepare", "approve"}:
        return raw
    return default


def _codex_home() -> Path:
    env_home = str(os.getenv("CODEX_HOME") or "").strip()
    if env_home:
        return Path(env_home)
    if os.name == "nt":
        base = str(os.getenv("USERPROFILE") or "").strip()
        if base:
            return Path(base) / ".codex"
    return Path.home() / ".codex"


def _skill_script_path(skill_name: str, script_name: str) -> Path:
    return _codex_home() / "skills" / skill_name / "scripts" / script_name


def _wechat_script() -> Path:
    return _skill_script_path("wechat-publish-playwright", "publish_wechat.py")


def _xhs_script() -> Path:
    return _skill_script_path("xhs-publish-playwright", "publish_xhs.py")


def _douyin_script() -> Path:
    return _skill_script_path("douyin-publish-playwright", "publish_douyin.py")


def _parse_frontmatter(markdown_text: str) -> tuple[dict[str, str], str]:
    raw = str(markdown_text or "").lstrip("\ufeff")
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    meta_text = parts[1]
    body = parts[2]
    meta: dict[str, str] = {}
    for line in meta_text.splitlines():
        item = line.strip()
        if not item or ":" not in item:
            continue
        key, value = item.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body.strip()


def _extract_section(text: str, heading: str) -> str:
    pattern = re.compile(rf"(?ms)^##\s*{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)")
    matched = pattern.search(str(text or ""))
    if not matched:
        return ""
    return str(matched.group(1) or "").strip()


def _extract_title(text: str, meta: dict[str, str]) -> str:
    for key in ("title", "标题", "主标题"):
        value = str(meta.get(key) or "").strip()
        if value:
            return value
    body = str(text or "")
    for line in body.splitlines():
        item = line.strip()
        if item.startswith("#"):
            title = re.sub(r"^#+\s*", "", item).strip()
            if title:
                return title
    for line in body.splitlines():
        item = line.strip()
        if item:
            return item[:32]
    return "未命名内容"


def _extract_tags(text: str) -> list[str]:
    tags = re.findall(r"#([A-Za-z0-9\u4e00-\u9fff_-]{1,20})", str(text or ""))
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        item = str(tag or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out[:10]


def _resolve_content_path(content_ref: str) -> Path:
    ref = str(content_ref or "").strip()
    if not ref:
        raise ValueError("content is required")
    candidate = Path(ref)
    if candidate.is_file():
        return candidate.resolve()
    repo_candidate = (REPO_ROOT / ref).resolve()
    if repo_candidate.is_file():
        return repo_candidate

    # Support using previous task id as content source.
    task = get_task(ref)
    if task:
        payload = task.get("payload_json") or {}
        result = task.get("result_json") or {}
        for key in ("content_path", "output_path"):
            path_text = str(payload.get(key) or result.get(key) or "").strip()
            if not path_text:
                continue
            rel_candidate = (REPO_ROOT / path_text).resolve()
            if rel_candidate.is_file():
                return rel_candidate
            abs_candidate = Path(path_text)
            if abs_candidate.is_file():
                return abs_candidate.resolve()
    raise ValueError(f"content path not found: {content_ref}")


def _load_content_profile(content_path: Path) -> dict[str, Any]:
    text = content_path.read_text(encoding="utf-8", errors="ignore")
    meta, body = _parse_frontmatter(text)
    title = _extract_title(body, meta)
    body_section = _extract_section(body, "正文") or body
    tags = _extract_tags(body)
    return {
        "path": content_path,
        "meta": meta,
        "title": title,
        "body": body_section.strip(),
        "tags": tags,
        "raw": body,
    }


def _default_profile_dir(platform: str) -> str:
    base = REPO_ROOT / "06-工具" / "data" / "automation" / "profiles"
    if platform == "wechat":
        return str(base / "wechat-main")
    if platform == "xhs":
        return str(base / "xhs-main")
    return str(base / "douyin-main")


def _request_file(task_id: str) -> Path:
    return REQUEST_DIR / f"{task_id}.json"


def _write_request(task_id: str, payload: dict[str, Any]) -> Path:
    path = _request_file(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _parse_json_like(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return {"value": data}
    except Exception:
        pass
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def _run_script(
    *,
    script_path: Path,
    input_payload: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    if not script_path.exists():
        raise RuntimeError(f"script not found: {script_path}")
    task_id = str(input_payload.get("_task_id") or "").strip()
    if not task_id:
        raise RuntimeError("missing _task_id in input payload")

    request_path = _write_request(task_id, input_payload)
    args = [
        sys.executable,
        str(script_path),
        "--input",
        str(request_path),
        "--workspace",
        str(REPO_ROOT),
    ]
    if dry_run:
        args.append("--dry-run")

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    completed = subprocess.run(
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=False,
        env=env,
    )

    def _decode_output(raw: bytes) -> str:
        if not raw:
            return ""
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "cp936"):
            try:
                return raw.decode(encoding).strip()
            except Exception:
                continue
        return raw.decode("utf-8", errors="backslashreplace").strip()

    stdout = _decode_output(completed.stdout or b"")
    stderr = _decode_output(completed.stderr or b"")
    parsed = _parse_json_like(stdout)
    if not parsed:
        parsed = {"status": "failed", "stdout": stdout}
    parsed.setdefault("stdout", stdout)
    if stderr:
        parsed.setdefault("stderr", stderr)
    parsed["exit_code"] = completed.returncode
    if completed.returncode != 0 and str(parsed.get("status") or "").lower() in {"", "success", "dry_run"}:
        parsed["status"] = "failed"
    return parsed


def _build_platform_payload(
    *,
    platform: str,
    mode: str,
    content_profile: dict[str, Any],
    account: str,
    schedule_time: str,
    images: list[str],
    videos: list[str],
) -> tuple[Path, dict[str, Any]]:
    title = str(content_profile.get("title") or "未命名内容").strip()
    body = str(content_profile.get("body") or "").strip()
    tags = list(content_profile.get("tags") or [])
    if platform == "wechat":
        payload = {
            "title": title,
            "author": account or "OpenClaw",
            "content_md": body or str(content_profile.get("raw") or ""),
            "cover_path": images[0] if images else "",
            "mode": mode,
            "publish_time": schedule_time or "",
            "account_profile": {
                "user_data_dir": str(os.getenv("WECHAT_PROFILE_DIR") or _default_profile_dir("wechat")),
                "headless": False,
                "login_timeout_sec": int(os.getenv("WECHAT_LOGIN_TIMEOUT_SEC", "180")),
                "slow_mo": int(os.getenv("WECHAT_SLOW_MO_MS", "0")),
            },
            "selectors_path": str(os.getenv("WECHAT_SELECTORS_PATH") or "").strip(),
        }
        return _wechat_script(), payload

    if platform == "xhs":
        payload = {
            "title": title[:20],
            "body": body[:1000],
            "tags": tags,
            "images": images,
            "mode": mode,
            "schedule_time": schedule_time or "",
            "account_profile": {
                "user_data_dir": str(os.getenv("XHS_PROFILE_DIR") or _default_profile_dir("xhs")),
                "headless": False,
                "login_timeout_sec": int(os.getenv("XHS_LOGIN_TIMEOUT_SEC", "180")),
                "slow_mo": int(os.getenv("XHS_SLOW_MO_MS", "0")),
            },
            "selectors_path": str(os.getenv("XHS_SELECTORS_PATH") or "").strip(),
        }
        return _xhs_script(), payload

    if platform == "douyin":
        payload = {
            "title": title[:55],
            "body": body[:2000],
            "tags": tags,
            "videos": videos,
            "mode": mode,
            "schedule_time": schedule_time or "",
            "account_profile": {
                "user_data_dir": str(os.getenv("DOUYIN_PROFILE_DIR") or _default_profile_dir("douyin")),
                "headless": False,
                "login_timeout_sec": int(os.getenv("DOUYIN_LOGIN_TIMEOUT_SEC", "180")),
                "slow_mo": int(os.getenv("DOUYIN_SLOW_MO_MS", "0")),
            },
            "selectors_path": str(os.getenv("DOUYIN_SELECTORS_PATH") or "").strip(),
        }
        return _douyin_script(), payload

    raise RuntimeError(f"unsupported platform: {platform}")


def prepare_publish(payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    event_ref = str(payload.get("event_ref") or "").strip()
    source_user = str(payload.get("source_user") or "").strip()
    platform = _normalize_platform(str(payload.get("platform") or ""))
    account = str(payload.get("account") or "").strip()
    final_mode = _normalize_mode(str(payload.get("mode") or "publish"), default="publish")
    content_ref = str(payload.get("content") or "").strip()
    schedule_time = str(payload.get("schedule_time") or payload.get("时间") or "").strip()
    images = [str(item).strip() for item in (payload.get("images") or []) if str(item).strip()]
    videos = [str(item).strip() for item in (payload.get("videos") or []) if str(item).strip()]
    if not platform:
        raise ValueError("platform is required, supported: wechat/xhs/douyin")

    duplicate = find_task_by_event_ref(event_ref, "publish_prepare") if event_ref else None
    if duplicate:
        return {
            "status": "duplicate",
            "task_id": duplicate["task_id"],
            "phase": duplicate.get("phase") or "",
            "platform": duplicate.get("platform") or platform,
            "event_ref": event_ref,
            "duplicate": True,
            "task": duplicate,
        }

    content_path = _resolve_content_path(content_ref)
    content_profile = _load_content_profile(content_path)
    task_id = make_task_id("pub")

    if platform == "wechat":
        prepare_mode = "draft"
    elif platform == "xhs":
        # XHS skill only supports publish/schedule. Use final mode for validation.
        prepare_mode = "schedule" if final_mode == "schedule" else "publish"
    else:
        prepare_mode = "prepare"
    script_path, script_payload = _build_platform_payload(
        platform=platform,
        mode=prepare_mode,
        content_profile=content_profile,
        account=account,
        schedule_time=schedule_time,
        images=images,
        videos=videos,
    )
    script_payload["_task_id"] = task_id

    if platform == "wechat" and not dry_run:
        run_draft = str(os.getenv("PUBLISH_PREPARE_RUN_WECHAT_DRAFT", "true")).strip().lower() in {"1", "true", "yes", "y", "on"}
        precheck_dry_run = not run_draft
    elif platform == "douyin" and not dry_run:
        run_prepare = str(os.getenv("PUBLISH_PREPARE_RUN_DOUYIN", "true")).strip().lower() in {"1", "true", "yes", "y", "on"}
        precheck_dry_run = not run_prepare
    else:
        precheck_dry_run = True

    precheck = _run_script(script_path=script_path, input_payload=script_payload, dry_run=precheck_dry_run)
    precheck_status = str(precheck.get("status") or "").lower()
    ok = precheck_status in {"success", "dry_run"}
    task_status = "pending_approval" if ok else "error"
    phase = "prepare"

    task_payload = {
        "content_ref": content_ref,
        "content_path": content_path.relative_to(REPO_ROOT).as_posix(),
        "title": content_profile.get("title"),
        "final_mode": final_mode,
        "schedule_time": schedule_time,
        "platform_payload": script_payload,
        "script_path": script_path.as_posix(),
        "precheck_dry_run": precheck_dry_run,
    }

    task_result = {
        "precheck": precheck,
        "precheck_status": precheck_status,
    }

    create_task(
        task_id=task_id,
        event_ref=event_ref,
        task_type="publish_prepare",
        status=task_status,
        phase=phase,
        platform=platform,
        account=account,
        mode=final_mode,
        source_user=source_user,
        payload=task_payload,
        result=task_result,
        error_text="" if ok else str(precheck.get("stderr") or precheck.get("stdout") or "prepare failed"),
    )
    add_task_log(task_id, "prepare", {"precheck_status": precheck_status, "precheck": precheck})

    if not ok:
        dead_log = append_dead_letter(
            "publish_prepare_failed",
            {
                "task_id": task_id,
                "platform": platform,
                "account": account,
                "event_ref": event_ref,
                "result": task_result,
            },
        )
        return {
            "status": "error",
            "task_id": task_id,
            "phase": phase,
            "platform": platform,
            "account": account,
            "dead_letter_log": dead_log,
            "result": task_result,
        }

    run_log = append_run_log(
        "publish_prepare",
        {
            "task_id": task_id,
            "platform": platform,
            "account": account,
            "mode": final_mode,
            "event_ref": event_ref,
            "status": task_status,
            "precheck_status": precheck_status,
        },
    )
    return {
        "status": "success",
        "task_id": task_id,
        "phase": phase,
        "platform": platform,
        "account": account,
        "mode": final_mode,
        "pending_approval": True,
        "run_log": run_log,
        "result": task_result,
    }


def approve_publish(payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    task_id = str(payload.get("task_id") or "").strip()
    approver = str(payload.get("approver") or payload.get("source_user") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    task = get_task(task_id)
    if not task:
        raise ValueError(f"task not found: {task_id}")
    task_status = str(task.get("status") or "").strip()
    if task_status not in {"pending_approval", "retry_pending"}:
        raise ValueError(f"task status does not allow approval: {task_status}")

    platform = str(task.get("platform") or "")
    final_mode = _normalize_mode(str(task.get("mode") or "publish"), default="publish")
    task_payload = task.get("payload_json") or {}
    platform_payload = dict(task_payload.get("platform_payload") or {})
    if not platform_payload:
        raise RuntimeError("platform payload missing in task")

    platform_payload["_task_id"] = task_id
    platform_payload["mode"] = final_mode if platform != "douyin" else "approve"

    script_path = Path(str(task_payload.get("script_path") or "")).resolve()
    run_result = _run_script(script_path=script_path, input_payload=platform_payload, dry_run=dry_run)
    status_text = str(run_result.get("status") or "").lower()

    if status_text in {"success", "dry_run"}:
        next_status = "success"
        error_text = ""
    elif status_text in {"pending_manual_publish", "waiting_manual_publish", "manual_pending"}:
        next_status = "waiting_manual_publish"
        error_text = ""
    else:
        next_status = "error"
        error_text = str(run_result.get("stderr") or run_result.get("stdout") or "approve failed")

    result = {
        "approve": run_result,
        "approved_by": approver,
    }
    update_task(
        task_id,
        status=next_status,
        phase="approve",
        approver=approver,
        approved_at=dt.datetime.now().isoformat(timespec="seconds"),
        result_json=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        error_text=error_text,
    )
    add_task_log(task_id, "approve", result)

    if next_status == "error":
        dead_log = append_dead_letter(
            "publish_approve_failed",
            {
                "task_id": task_id,
                "platform": platform,
                "approver": approver,
                "result": result,
            },
        )
        return {
            "status": "error",
            "task_id": task_id,
            "phase": "approve",
            "platform": platform,
            "dead_letter_log": dead_log,
            "result": result,
        }

    run_log = append_run_log(
        "publish_approve",
        {
            "task_id": task_id,
            "platform": platform,
            "approver": approver,
            "status": next_status,
        },
    )
    return {
        "status": "success" if next_status == "success" else "partial",
        "task_id": task_id,
        "phase": "approve",
        "platform": platform,
        "task_status": next_status,
        "run_log": run_log,
        "result": result,
    }


def retry_publish_task(payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    task = get_task(task_id)
    if not task:
        raise ValueError(f"task not found: {task_id}")
    status = str(task.get("status") or "").strip()
    if status not in {"error", "retry_pending"}:
        raise ValueError(f"task is not retryable, current status={status}")
    retry_count = int(task.get("retry_count") or 0) + 1
    update_task(task_id, status="retry_pending", retry_count=retry_count, error_text="")
    add_task_log(task_id, "retry_marked", {"retry_count": retry_count})
    return approve_publish({"task_id": task_id, "approver": payload.get("approver") or ""}, dry_run=dry_run)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish action runner")
    parser.add_argument("--action", choices=("prepare", "approve", "retry"), required=True)
    parser.add_argument("--payload-file", default="", help="JSON payload file")
    parser.add_argument("--payload-json", default="", help="Inline JSON payload")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload_json:
        data = json.loads(args.payload_json)
        if not isinstance(data, dict):
            raise ValueError("payload-json must be JSON object")
        return data
    if args.payload_file:
        path = Path(args.payload_file).resolve()
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("payload-file content must be JSON object")
        return data
    raise ValueError("payload-file or payload-json is required")


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    payload = _load_payload(args)
    try:
        if args.action == "prepare":
            result = prepare_publish(payload, dry_run=args.dry_run)
        elif args.action == "approve":
            result = approve_publish(payload, dry_run=args.dry_run)
        else:
            result = retry_publish_task(payload, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if str(result.get("status") or "") in {"success", "partial", "duplicate"} else 1
    except Exception as exc:
        out = {"status": "error", "action": args.action, "errors": [str(exc)]}
        print(json.dumps(out, ensure_ascii=True, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
