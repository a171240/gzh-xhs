#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feishu message orchestrator for knowledge-base ingest + skill generation.

Contract:
- CLI: python feishu_kb_orchestrator.py --text ... --event-ref ... --source-time ...
- Intent routing:
  - Message with URL => ingest link_mode (do not write into quote library directly)
  - Message with @前缀（含可选“回复/序号”）=> ingest quote_mode（仅入库 @ 后正文）
  - Non-link, non-skill, non-@ message => plain chat fallback
- Skill commands support:
    - Strong trigger: /skill {skill_id} 平台={平台} 需求={文本}
    - Weak trigger: 用{skill名}生成{需求}
- If ingest and skill are both triggered, execute them concurrently and aggregate reply.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from codex_commander import execute_tasks
from feishu_skill_runner import DEFAULT_MODEL, build_skill_registry, resolve_codex_cli, resolve_skill


REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_ROOT = REPO_ROOT / "06-工具" / "data" / "feishu-orchestrator"
RUN_LOG_DIR = LOG_ROOT / "runs"
DEAD_LETTER_DIR = LOG_ROOT / "dead-letter"

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
SKILL_COMMAND_RE = re.compile(r"^\s*/skill\s+([^\s]+)(.*)$", re.IGNORECASE)
PLATFORM_KV_RE = re.compile(r"(?:平台|platform)\s*[=:：]\s*([^\s,，;；]+)", re.IGNORECASE)
BRIEF_KV_RE = re.compile(r"(?:需求|brief|prompt)\s*[=:：]\s*(.+)$", re.IGNORECASE)
GEN_VERB_RE = re.compile(r"(生成|写|产出|输出|起草|改写|扩写|润色)")
REPLY_PREFIX_RE = re.compile(
    r"^\s*(?:(?:\d+\s*[\.、]\s*)?)?(?:回复\s+[^:：\n]{1,80}\s*[:：]\s*)"
)
QUOTE_AT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:\d+\s*[\.、]\s*)?(?:回复\s*)?)?[\"'“”‘’]?[@＠]\s*(?P<mention>[^:：，,\n]+?)\s*(?:[:：]\s*|\n+)(?P<body>[\s\S]+?)\s*$"
)
QUOTE_TEXT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:\d+\s*[\.、]\s*)?)?金句\s*(?:[:：]\s*|\n+)(?P<body>[\s\S]+?)\s*$"
)

ENV_FALLBACKS = (
    REPO_ROOT / "06-工具" / "scripts" / ".env.ingest-writer.local",
    REPO_ROOT / "06-工具" / "scripts" / ".env.ingest-writer",
    REPO_ROOT / "06-工具" / "scripts" / ".env.feishu",
)


@dataclasses.dataclass(frozen=True)
class OrchestratorSettings:
    writer_base_url: str
    ingest_shared_token: str
    ingest_hmac_secret: str
    ingest_timeout_sec: int
    ingest_verify_ssl: bool
    commander_workers: int
    commander_timeout_sec: int
    commander_max_retries: int
    codex_model: str
    max_reply_chars: int
    plain_text_mode: str
    plain_chat_model: str
    plain_chat_timeout_sec: int
    git_sync_enabled: bool
    git_sync_repo_root: str
    git_sync_remote: str
    git_sync_branch: str
    git_sync_include_paths: tuple[str, ...]
    git_sync_author_name: str
    git_sync_author_email: str
    git_sync_max_retries: int


@dataclasses.dataclass(frozen=True)
class SkillIntent:
    skill_id: str
    brief: str
    platform: str
    trigger: str  # strong | weak


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _split_csv(value: str | None, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if not value:
        return default
    out: list[str] = []
    for raw in str(value).replace(";", ",").split(","):
        item = raw.strip().replace("\\", "/").strip("/")
        if not item:
            continue
        out.append(item)
    if not out:
        return default
    return tuple(dict.fromkeys(out))


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return dt.date.today().isoformat()


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _load_env_fallbacks() -> None:
    keys = {"INGEST_WRITER_BASE_URL", "INGEST_SHARED_TOKEN", "INGEST_HMAC_SECRET"}
    missing = [k for k in keys if not (os.getenv(k) or "").strip()]
    if not missing:
        return
    for env_path in ENV_FALLBACKS:
        if not env_path.exists():
            continue
        text = env_path.read_text(encoding="utf-8", errors="ignore")
        for raw in text.splitlines():
            line = raw.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and not (os.getenv(key) or "").strip():
                os.environ[key] = value
        missing = [k for k in keys if not (os.getenv(k) or "").strip()]
        if not missing:
            return


def _load_settings() -> OrchestratorSettings:
    _load_env_fallbacks()
    shared_token = str(os.getenv("INGEST_SHARED_TOKEN") or "").strip()
    hmac_secret = str(os.getenv("INGEST_HMAC_SECRET") or "").strip() or shared_token
    return OrchestratorSettings(
        writer_base_url=str(os.getenv("INGEST_WRITER_BASE_URL") or "http://127.0.0.1:8790").strip().rstrip("/"),
        ingest_shared_token=shared_token,
        ingest_hmac_secret=hmac_secret,
        ingest_timeout_sec=max(3, int(os.getenv("INGEST_TIMEOUT_SEC", "20"))),
        ingest_verify_ssl=_as_bool(os.getenv("INGEST_VERIFY_SSL"), default=True),
        commander_workers=max(1, int(os.getenv("FEISHU_COMMANDER_WORKERS", "2"))),
        commander_timeout_sec=max(30, int(os.getenv("FEISHU_COMMANDER_TIMEOUT_SEC", "1800"))),
        commander_max_retries=max(0, int(os.getenv("FEISHU_COMMANDER_MAX_RETRIES", "1"))),
        codex_model=str(os.getenv("FEISHU_SKILL_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        max_reply_chars=max(500, int(os.getenv("FEISHU_REPLY_MAX_CHARS", "1500"))),
        plain_text_mode=str(os.getenv("FEISHU_PLAIN_TEXT_MODE") or "chat").strip().lower(),
        plain_chat_model=str(os.getenv("FEISHU_CHAT_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        plain_chat_timeout_sec=max(30, int(os.getenv("FEISHU_CHAT_TIMEOUT_SEC", "120"))),
        git_sync_enabled=_as_bool(os.getenv("GIT_SYNC_ENABLED"), default=False),
        git_sync_repo_root=str(os.getenv("GIT_SYNC_REPO_ROOT") or REPO_ROOT).strip() or str(REPO_ROOT),
        git_sync_remote=str(os.getenv("GIT_SYNC_REMOTE") or "origin").strip() or "origin",
        git_sync_branch=str(os.getenv("GIT_SYNC_BRANCH") or "main").strip() or "main",
        git_sync_include_paths=_split_csv(
            os.getenv("GIT_SYNC_INCLUDE_PATHS"),
            default=("02-", "03-", "01-"),
        ),
        git_sync_author_name=str(os.getenv("GIT_SYNC_AUTHOR_NAME") or "feishu-bot").strip() or "feishu-bot",
        git_sync_author_email=str(os.getenv("GIT_SYNC_AUTHOR_EMAIL") or "feishu-bot@local").strip()
        or "feishu-bot@local",
        git_sync_max_retries=max(0, int(os.getenv("GIT_SYNC_MAX_RETRIES", "2"))),
    )


def _normalize_key(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def _dedupe_urls(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in urls:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _extract_urls(text: str) -> list[str]:
    return _dedupe_urls(URL_RE.findall(str(text or "")))


def _strip_urls(text: str) -> str:
    cleaned = URL_RE.sub(" ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_quote_trigger(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "none", ""

    # Feishu thread replies may prefix body with `回复 某某：`.
    normalized = raw
    for _ in range(2):
        prefix = REPLY_PREFIX_RE.match(normalized)
        if not prefix:
            break
        normalized = normalized[prefix.end() :].lstrip()

    matched = QUOTE_AT_PREFIX_RE.match(normalized)
    trigger = "at_prefix"
    if not matched:
        matched = QUOTE_TEXT_PREFIX_RE.match(normalized)
        trigger = "text_prefix"
    if not matched:
        return "none", ""
    body = re.sub(r"\s+", " ", str(matched.group("body") or "")).strip(" \t\r\n:：")
    if not body:
        return "none", ""
    return trigger, body


def _extract_quote_after_mention(text: str) -> tuple[bool, str]:
    trigger, body = _parse_quote_trigger(text)
    return trigger != "none", body


def _is_quote_trigger_text(text: str) -> bool:
    trigger, _ = _parse_quote_trigger(text)
    return trigger != "none"


def _extract_platform(text: str) -> str:
    matched = PLATFORM_KV_RE.search(str(text or ""))
    if not matched:
        return ""
    return str(matched.group(1) or "").strip()


def _remove_kv_chunks(text: str) -> str:
    cleaned = PLATFORM_KV_RE.sub(" ", str(text or ""))
    cleaned = BRIEF_KV_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _find_best_skill_alias(text: str, registry: Any) -> str:
    normalized_text = _normalize_key(text)
    if not normalized_text:
        return ""

    best_skill = ""
    best_len = 0
    for skill in registry.by_id.values():
        aliases = {skill.skill_id, skill.name, *skill.aliases}
        for alias in aliases:
            key = _normalize_key(alias)
            if len(key) < 2:
                continue
            if key in normalized_text and len(key) > best_len:
                best_skill = skill.skill_id
                best_len = len(key)
    if best_skill:
        return best_skill

    fallback_aliases = (
        ("公众号批量生产", "wechat"),
        ("公众号爆文写作", "wechat"),
        ("小红书内容生产", "xhs"),
        ("短视频脚本生产", "短视频脚本生产"),
    )
    for alias, mapped in fallback_aliases:
        if _normalize_key(alias) in normalized_text:
            try:
                return resolve_skill(registry, mapped).skill_id
            except Exception:
                continue
    return ""


def _detect_skill_intent(text: str, registry: Any, forced_skill_id: str, forced_platform: str) -> SkillIntent | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    if forced_skill_id:
        skill = resolve_skill(registry, forced_skill_id)
        platform = forced_platform or _extract_platform(raw) or skill.default_platform
        brief = _remove_kv_chunks(raw)
        if not brief:
            raise ValueError("skill brief is empty")
        return SkillIntent(skill_id=skill.skill_id, brief=brief, platform=platform, trigger="strong")

    # Strong trigger: /skill {skill_id} 平台=... 需求=...
    matched = SKILL_COMMAND_RE.match(raw)
    if matched:
        skill_ref = str(matched.group(1) or "").strip()
        tail = str(matched.group(2) or "").strip()
        skill = resolve_skill(registry, skill_ref)
        platform = forced_platform or _extract_platform(tail) or skill.default_platform

        brief_match = BRIEF_KV_RE.search(tail)
        if brief_match:
            brief = str(brief_match.group(1) or "").strip()
        else:
            brief = _remove_kv_chunks(tail)
        brief = _strip_urls(brief)
        if not brief:
            raise ValueError("skill brief is empty")
        return SkillIntent(skill_id=skill.skill_id, brief=brief, platform=platform, trigger="strong")

    # Weak trigger: 用{skill名}生成{需求}
    if not GEN_VERB_RE.search(raw):
        return None
    if "用" not in raw and "使用" not in raw:
        return None

    skill_id = _find_best_skill_alias(raw, registry)
    if not skill_id:
        return None
    skill = resolve_skill(registry, skill_id)
    platform = forced_platform or _extract_platform(raw) or skill.default_platform

    verb_match = GEN_VERB_RE.search(raw)
    brief = raw[verb_match.end() :].strip(" ：:，,。") if verb_match else ""
    if not brief:
        brief = _remove_kv_chunks(raw)
    brief = _strip_urls(brief)
    if not brief:
        raise ValueError("skill brief is empty")
    return SkillIntent(skill_id=skill.skill_id, brief=brief, platform=platform, trigger="weak")


def _stable_event_ref(*, text: str, source_ref: str) -> str:
    payload = f"{source_ref}|{text}".encode("utf-8", errors="ignore")
    digest = hashlib.sha1(payload).hexdigest()[:20]
    return f"evt-{digest}"


def _writer_signature(secret: str, *, timestamp: str, nonce: str, body: bytes) -> str:
    payload = f"{timestamp}\n{nonce}\n".encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _writer_headers(settings: OrchestratorSettings, body: bytes) -> dict[str, str]:
    timestamp = str(int(dt.datetime.now(dt.timezone.utc).timestamp()))
    nonce = uuid.uuid4().hex
    signature = _writer_signature(settings.ingest_hmac_secret, timestamp=timestamp, nonce=nonce, body=body)
    return {
        "Authorization": f"Bearer {settings.ingest_shared_token}",
        "Content-Type": "application/json",
        "X-Ingest-Timestamp": timestamp,
        "X-Ingest-Nonce": nonce,
        "X-Ingest-Signature": signature,
    }


def _call_writer(settings: OrchestratorSettings, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.ingest_shared_token:
        raise RuntimeError("INGEST_SHARED_TOKEN is empty")
    if not settings.ingest_hmac_secret:
        raise RuntimeError("INGEST_HMAC_SECRET is empty")

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response = requests.post(
        f"{settings.writer_base_url}{endpoint}",
        headers=_writer_headers(settings, body),
        data=body,
        timeout=settings.ingest_timeout_sec,
        verify=settings.ingest_verify_ssl,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"writer api {response.status_code}: {response.text}")
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"writer api business error: {data}")
    return data


def _run_ingest(
    *,
    settings: OrchestratorSettings,
    text: str,
    urls: list[str],
    event_ref: str,
    source_ref: str,
    source_time: str,
    dry_run: bool,
) -> dict[str, Any]:
    if not text.strip() and not urls:
        return {"status": "ignored", "mode": "ignore", "errors": ["empty text"], "result": {}}

    mode = "link" if urls else "quote"
    endpoint = "/internal/ingest/v1/link" if urls else "/internal/ingest/v1/quote"
    ingest_event_ref = f"{event_ref}#{mode}"
    payload: dict[str, Any] = {
        "event_ref": ingest_event_ref,
        "source_kind": "feishu-orchestrator",
        "source_ref": source_ref,
        "source_time": source_time,
    }
    if urls:
        payload["urls"] = urls
        payload["text"] = text
    else:
        quote_body = _strip_urls(text)
        if quote_body and not re.match(r"^\s*金句\s*[:：]\s*", quote_body):
            quote_body = f"金句：{quote_body}"
        payload["text"] = quote_body

    if dry_run:
        return {
            "status": "success",
            "mode": mode,
            "dry_run": True,
            "event_ref": ingest_event_ref,
            "payload": payload,
            "result": {"mode": mode, "status": "success", "added": 0, "near_dup": 0, "skipped": 0},
        }

    data = _call_writer(settings, endpoint, payload)
    result = data.get("result") or {}
    return {
        "status": str(result.get("status") or "unknown"),
        "mode": str(result.get("mode") or mode),
        "event_ref": str(data.get("event_ref") or ingest_event_ref),
        "duplicate": bool(data.get("duplicate")),
        "result": result,
        "raw": data,
    }


def _run_skill(
    *,
    settings: OrchestratorSettings,
    skill_intent: SkillIntent,
    event_ref: str,
    source_ref: str,
    source_time: str,
    dry_run: bool,
) -> dict[str, Any]:
    task = {
        "task_id": f"{event_ref}#skill",
        "event_ref": f"{event_ref}#skill",
        "source_ref": source_ref,
        "source_time": source_time,
        "skill_id": skill_intent.skill_id,
        "brief": skill_intent.brief,
        "platform": skill_intent.platform,
        "model": settings.codex_model,
    }

    if dry_run:
        return {
            "status": "success",
            "dry_run": True,
            "trigger": skill_intent.trigger,
            "skill_id": skill_intent.skill_id,
            "platform": skill_intent.platform,
            "brief": skill_intent.brief,
            "result": {
                "status": "success",
                "skill_id": skill_intent.skill_id,
                "platform": skill_intent.platform,
                "saved_files": [],
                "full_text": "",
                "errors": [],
            },
            "commander": {"task": task},
        }

    commander_result = execute_tasks(
        payload={"tasks": [task]},
        workers=settings.commander_workers,
        timeout_sec=settings.commander_timeout_sec,
        max_retries=settings.commander_max_retries,
    )
    rows = commander_result.get("results") or []
    first = rows[0] if rows else {}
    return {
        "status": str(first.get("status") or commander_result.get("status") or "unknown"),
        "trigger": skill_intent.trigger,
        "skill_id": skill_intent.skill_id,
        "platform": skill_intent.platform,
        "brief": skill_intent.brief,
        "result": first,
        "commander": commander_result,
    }


def _parse_codex_json_lines(stdout_text: str) -> tuple[str, str]:
    latest_text = ""
    parse_errors = ""
    fallback_lines: list[str] = []
    for raw in str(stdout_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            fallback_lines.append(raw)
            continue

        if isinstance(payload, dict):
            if payload.get("type") == "error":
                parse_errors = str(payload.get("message") or payload.get("error") or parse_errors)
                continue
            item = payload.get("item")
            if isinstance(item, dict):
                item_type = str(item.get("type") or "")
                if item_type in {"assistant_message", "agent_message"} and isinstance(item.get("text"), str):
                    latest_text = str(item["text"])

    if not latest_text and fallback_lines:
        latest_text = "\n".join(fallback_lines).strip()
    return latest_text, parse_errors


def _run_plain_chat(
    *,
    settings: OrchestratorSettings,
    text: str,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return {
            "status": "success",
            "mode": "chat",
            "result": {"full_text": f"[dry-run] {text.strip()}"},
        }

    if settings.plain_text_mode != "chat":
        return {
            "status": "error",
            "mode": "chat",
            "errors": ["plain text mode disabled"],
            "result": {},
        }

    prompt = (
        "你是飞书私聊助手。\n"
        "请直接用中文回复用户，简洁自然，不要输出代码块、JSON、系统解释。\n\n"
        f"用户消息：{text.strip()}\n"
    )
    codex_cli = resolve_codex_cli()
    args = [codex_cli, "exec", "--json", "--skip-git-repo-check", "-m", settings.plain_chat_model, "-"]
    completed = subprocess.run(
        args,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        timeout=max(30, settings.plain_chat_timeout_sec),
    )
    chat_text, parse_error = _parse_codex_json_lines(completed.stdout or "")
    if completed.returncode != 0:
        err = parse_error or (completed.stderr or "").strip() or f"codex exited with {completed.returncode}"
        return {"status": "error", "mode": "chat", "errors": [err], "result": {}}

    chat_text = str(chat_text or "").strip()
    if not chat_text:
        err = parse_error or "chat model returned empty output"
        return {"status": "error", "mode": "chat", "errors": [err], "result": {}}

    return {
        "status": "success",
        "mode": "chat",
        "result": {"full_text": chat_text},
    }


def _normalize_git_paths(paths: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        value = str(raw or "").strip().replace("\\", "/")
        if not value:
            continue
        value = value.lstrip("./")
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _run_git_sync_after_write(
    *,
    settings: OrchestratorSettings,
    event_ref: str,
    kind: str,
    paths: list[str],
    dry_run: bool,
) -> dict[str, Any]:
    if not settings.git_sync_enabled:
        return {"status": "disabled", "message": "GIT_SYNC_ENABLED=false"}

    script_path = Path(__file__).resolve().with_name("git_sync_after_write.py")
    if not script_path.exists():
        return {"status": "error", "message": f"missing {script_path.name}"}

    args = [
        sys.executable,
        str(script_path),
        "--event-ref",
        event_ref,
        "--kind",
        kind,
    ]
    for item in _normalize_git_paths(paths):
        args.extend(["--path", item])
    if dry_run:
        args.append("--dry-run")

    env = os.environ.copy()
    env["GIT_SYNC_ENABLED"] = "true" if settings.git_sync_enabled else "false"
    env["GIT_SYNC_REPO_ROOT"] = settings.git_sync_repo_root
    env["GIT_SYNC_REMOTE"] = settings.git_sync_remote
    env["GIT_SYNC_BRANCH"] = settings.git_sync_branch
    env["GIT_SYNC_INCLUDE_PATHS"] = ",".join(settings.git_sync_include_paths)
    env["GIT_SYNC_AUTHOR_NAME"] = settings.git_sync_author_name
    env["GIT_SYNC_AUTHOR_EMAIL"] = settings.git_sync_author_email
    env["GIT_SYNC_MAX_RETRIES"] = str(settings.git_sync_max_retries)

    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        env=env,
    )
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    payload: dict[str, Any] = {}
    if stdout:
        try:
            payload = json.loads(stdout)
        except Exception:
            payload = {"status": "error", "message": stdout}
    if not payload:
        payload = {"status": "error", "message": stderr or f"git sync exited with {completed.returncode}"}
    if completed.returncode != 0 and payload.get("status") in {"success", "skipped", "dry_run"}:
        payload["status"] = "error"
    if stderr:
        payload.setdefault("stderr", stderr)
    return payload


def _ingest_reply_line(ingest: dict[str, Any]) -> str:
    status = str(ingest.get("status") or "")
    result = ingest.get("result") or {}
    mode = str(result.get("mode") or ingest.get("mode") or "")
    detail_summary = (result.get("details") or {}).get("summary") or {}

    if status in {"error"}:
        errors = result.get("errors") or ingest.get("errors") or ["unknown error"]
        return f"入库失败：{errors[0]}"

    if mode == "quote":
        added = int(result.get("added") or 0)
        near_dup = int(result.get("near_dup") or 0)
        skipped = int(result.get("skipped") or 0)
        return f"金句已入库：新增{added}，近似{near_dup}，重复{skipped}"

    if mode == "link":
        added = int(result.get("added") or 0)
        skipped = int(result.get("skipped") or 0)
        total = int(detail_summary.get("link_total") or (added + skipped))
        success = int(detail_summary.get("link_success") or max(0, total - skipped))
        if total > 0 and success <= 0:
            errors = result.get("errors") or ingest.get("errors") or []
            if errors:
                return f"链接入库失败：{errors[0]}"
            return f"链接入库失败：成功{success}/{total}，正文{added}"
        return f"链接已入库：成功{success}/{total}，正文{added}"

    added = int(result.get("added") or 0)
    near_dup = int(result.get("near_dup") or 0)
    skipped = int(result.get("skipped") or 0)
    return f"入库已处理：新增{added}，近似{near_dup}，跳过{skipped}"


def _segment_reply(text: str, max_chars: int) -> list[str]:
    source = str(text or "").strip()
    if not source:
        return []
    if len(source) <= max_chars:
        return [source]

    out: list[str] = []
    remaining = source
    while remaining:
        if len(remaining) <= max_chars:
            out.append(remaining.strip())
            break
        cut = remaining.rfind("\n", 0, max_chars)
        if cut < int(max_chars * 0.6):
            cut = max_chars
        part = remaining[:cut].strip()
        if part:
            out.append(part)
        remaining = remaining[cut:].lstrip()
    return [item for item in out if item]


def _compose_reply(
    *,
    ingest: dict[str, Any] | None,
    skill: dict[str, Any] | None,
    plain_chat: dict[str, Any] | None,
    max_chars: int,
) -> tuple[str, list[str]]:
    if plain_chat is not None:
        plain_status = str(plain_chat.get("status") or "")
        if plain_status == "success":
            full_text = str((plain_chat.get("result") or {}).get("full_text") or "").strip()
            if full_text:
                segments = _segment_reply(full_text, max_chars=max_chars)
                return (segments[0] if segments else full_text, segments)
        fallback = "处理失败：聊天回复不可用，请稍后重试。"
        return fallback, [fallback]

    ingest_line = _ingest_reply_line(ingest) if ingest else ""

    if skill:
        skill_result = skill.get("result") or {}
        skill_status = str(skill.get("status") or skill_result.get("status") or "")
        if skill_status == "success":
            full_text = str(skill_result.get("full_text") or "").strip()
            if ingest_line and full_text:
                joined = f"{ingest_line}\n\n{full_text}"
            else:
                joined = full_text or ingest_line or "已处理。"
            segments = _segment_reply(joined, max_chars=max_chars)
            return (segments[0] if segments else joined, segments)

        # Skill failed: keep concise and do not flood.
        errors = skill_result.get("errors") or skill.get("errors") or ["skill执行失败"]
        if ingest_line:
            return (f"{ingest_line}；文案生成失败：{errors[0]}", [f"{ingest_line}；文案生成失败：{errors[0]}"])
        return (f"文案生成失败：{errors[0]}", [f"文案生成失败：{errors[0]}"])

    if ingest_line:
        return ingest_line, [ingest_line]
    return "未识别可处理内容。", ["未识别可处理内容。"]


def orchestrate_message(
    *,
    text: str,
    event_ref: str = "",
    source_ref: str = "",
    source_time: str = "",
    forced_skill_id: str = "",
    forced_platform: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    started = time.time()
    settings = _load_settings()
    message_text = str(text or "").strip()
    source_time_value = str(source_time or "").strip() or _now_iso()
    source_ref_value = str(source_ref or "").strip() or "feishu-message"
    event_ref_value = str(event_ref or "").strip() or _stable_event_ref(text=message_text, source_ref=source_ref_value)

    registry = build_skill_registry()
    urls = _extract_urls(message_text)
    quote_trigger, quote_text = _parse_quote_trigger(message_text)
    quote_trigger_hit = quote_trigger != "none"
    skill_intent = _detect_skill_intent(
        message_text,
        registry=registry,
        forced_skill_id=str(forced_skill_id or "").strip(),
        forced_platform=str(forced_platform or "").strip(),
    )

    ingest_trigger = "url" if urls else quote_trigger
    will_ingest = bool(urls) or quote_trigger_hit
    will_skill = skill_intent is not None

    ingest_result: dict[str, Any] | None = None
    skill_result: dict[str, Any] | None = None
    plain_chat_result: dict[str, Any] | None = None
    plain_chat_fallback_used = False
    git_sync_result: dict[str, Any] | None = None
    errors: list[str] = []

    if not will_ingest and not will_skill:
        plain_chat_fallback_used = True
        plain_chat_result = _run_plain_chat(settings=settings, text=message_text, dry_run=dry_run)
    else:
        ingest_input_text = quote_text if (quote_trigger_hit and not urls) else message_text
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures: dict[concurrent.futures.Future[Any], str] = {}
            if will_ingest:
                futures[
                    pool.submit(
                        _run_ingest,
                        settings=settings,
                        text=ingest_input_text,
                        urls=urls,
                        event_ref=event_ref_value,
                        source_ref=source_ref_value,
                        source_time=source_time_value,
                        dry_run=dry_run,
                    )
                ] = "ingest"
            if will_skill and skill_intent is not None:
                futures[
                    pool.submit(
                        _run_skill,
                        settings=settings,
                        skill_intent=skill_intent,
                        event_ref=event_ref_value,
                        source_ref=source_ref_value,
                        source_time=source_time_value,
                        dry_run=dry_run,
                    )
                ] = "skill"

            for future in concurrent.futures.as_completed(futures):
                kind = futures[future]
                try:
                    payload = future.result()
                except Exception as exc:
                    payload = {"status": "error", "errors": [str(exc)]}
                if kind == "ingest":
                    ingest_result = payload
                else:
                    skill_result = payload

    if plain_chat_result is not None:
        status = "success" if str(plain_chat_result.get("status") or "") == "success" else "error"
    else:
        ingest_status = str((ingest_result or {}).get("status") or "")
        skill_status = str((skill_result or {}).get("status") or "")
        statuses = [item for item in [ingest_status, skill_status] if item]
        if not statuses:
            status = "ignored"
        elif all(item == "success" for item in statuses):
            status = "success"
        elif any(item == "success" for item in statuses):
            status = "partial"
        else:
            status = "error"

    git_paths: list[str] = []
    ingest_success = bool(ingest_result) and str((ingest_result or {}).get("status") or "") in {"success", "partial"}
    skill_success = bool(skill_result) and str((skill_result or {}).get("status") or "") == "success"
    if ingest_success:
        ingest_touched = ((ingest_result or {}).get("result") or {}).get("touched_files") or []
        git_paths.extend([str(p) for p in ingest_touched if str(p).strip()])
    if skill_success:
        saved_files = ((skill_result or {}).get("result") or {}).get("saved_files") or []
        git_paths.extend([str(p) for p in saved_files if str(p).strip()])

    if settings.git_sync_enabled and (ingest_success or skill_success):
        sync_kind = "mixed" if ingest_success and skill_success else ("ingest" if ingest_success else "skill")
        git_sync_result = _run_git_sync_after_write(
            settings=settings,
            event_ref=event_ref_value,
            kind=sync_kind,
            paths=git_paths,
            dry_run=dry_run,
        )
    git_sync_error = bool(git_sync_result) and str((git_sync_result or {}).get("status") or "") == "error"
    if git_sync_error:
        sync_msg = str((git_sync_result or {}).get("message") or (git_sync_result or {}).get("stderr") or "").strip()
        errors.append(f"git sync failed: {sync_msg or 'unknown error'}")

    if ingest_result and ingest_result.get("status") in {"error", "partial"}:
        ingest_err = (ingest_result.get("result") or {}).get("errors") or ingest_result.get("errors") or []
        errors.extend(ingest_err)
        if not ingest_err:
            result_summary = ((ingest_result.get("result") or {}).get("details") or {}).get("summary") or {}
            link_total = int(result_summary.get("link_total") or 0)
            link_success = int(result_summary.get("link_success") or 0)
            if link_total > 0 and link_success <= 0:
                errors.append(f"链接抓取失败：成功{link_success}/{link_total}")
    if skill_result and skill_result.get("status") == "error":
        skill_err = (skill_result.get("result") or {}).get("errors") or skill_result.get("errors") or []
        errors.extend(skill_err)
    if plain_chat_result and plain_chat_result.get("status") == "error":
        errors.extend(plain_chat_result.get("errors") or [])

    reply, reply_segments = _compose_reply(
        ingest=ingest_result,
        skill=skill_result,
        plain_chat=plain_chat_result,
        max_chars=settings.max_reply_chars,
    )
    elapsed_ms = int((time.time() - started) * 1000)

    output = {
        "status": status,
        "event_ref": event_ref_value,
        "source_ref": source_ref_value,
        "source_time": source_time_value,
        "intent": {
            "ingest": will_ingest,
            "skill": will_skill,
            "urls": urls,
            "skill_id": (skill_intent.skill_id if skill_intent else ""),
            "skill_platform": (skill_intent.platform if skill_intent else ""),
            "skill_trigger": (skill_intent.trigger if skill_intent else ""),
            "ingest_trigger": ingest_trigger,
        },
        "ingest_trigger": ingest_trigger,
        "plain_chat_fallback_used": plain_chat_fallback_used,
        "ingest": ingest_result,
        "skill": skill_result,
        "plain_chat": plain_chat_result,
        "git_sync": git_sync_result,
        "reply": reply,
        "reply_segments": reply_segments,
        "errors": errors,
        "elapsed_ms": elapsed_ms,
    }

    run_log = RUN_LOG_DIR / f"{_today()}.jsonl"
    _append_jsonl(
        run_log,
        {
            "ts": _now_iso(),
            "event_ref": event_ref_value,
            "status": status,
            "intent": output["intent"],
            "ingest_trigger": ingest_trigger,
            "plain_chat_fallback_used": plain_chat_fallback_used,
            "git_sync_status": (git_sync_result or {}).get("status"),
            "git_sync_commit": (git_sync_result or {}).get("commit", ""),
            "elapsed_ms": elapsed_ms,
            "errors": errors,
        },
    )
    output["run_log"] = run_log.relative_to(REPO_ROOT).as_posix()

    needs_dead_letter = (status in {"error", "partial"} and errors) or git_sync_error
    if needs_dead_letter:
        dead_log = DEAD_LETTER_DIR / f"{_today()}.jsonl"
        _append_jsonl(
            dead_log,
            {
                "ts": _now_iso(),
                "event_ref": event_ref_value,
                "text": message_text,
                "output": output,
                "reason": "git_sync_error" if git_sync_error else "orchestrator_error",
            },
        )
        output["dead_letter_log"] = dead_log.relative_to(REPO_ROOT).as_posix()

    return output


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Feishu KB orchestrator")
    parser.add_argument("--text", default="", help="Raw Feishu text content.")
    parser.add_argument("--event-ref", default="", help="External idempotency key.")
    parser.add_argument("--source-ref", default="", help="Source trace id.")
    parser.add_argument("--source-time", default="", help="Source ISO timestamp.")
    parser.add_argument("--skill-id", default="", help="Force skill id.")
    parser.add_argument("--platform", default="", help="Force platform for skill generation.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call writer/codex, only plan output.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        result = orchestrate_message(
            text=args.text,
            event_ref=args.event_ref,
            source_ref=args.source_ref,
            source_time=args.source_time,
            forced_skill_id=args.skill_id,
            forced_platform=args.platform,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in {"success", "partial", "ignored"} else 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "reply": f"处理失败：{exc}",
                    "reply_segments": [f"处理失败：{exc}"],
                    "errors": [str(exc)],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
