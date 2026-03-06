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
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
from wechat_publish_renderer import build_render_payload


REQUEST_DIR = AUTOMATION_ROOT / "requests"
REQUEST_DIR.mkdir(parents=True, exist_ok=True)
SCRIPT_DIR = Path(__file__).resolve().parent

ENV_FALLBACK_FILES = (
    ".env.ingest-writer.local",
    ".env.ingest-writer",
    ".env.feishu",
)

WECHAT_ACCOUNT_ALIASES: dict[str, tuple[str, ...]] = {
    "IP内容工厂": ("IP内容工厂", "gongchang", "main", "wechat_main"),
    "IP工厂": ("IP工厂", "ipgc", "factory", "wechat_factory"),
    "IP增长引擎": ("IP增长引擎", "zengzhang", "growth", "wechat_growth"),
    "商业IP实战笔记": ("商业IP实战笔记", "shizhan", "notes", "wechat_notes"),
}

WECHAT_ACCOUNT_SLUGS = {
    "IP内容工厂": "gongchang",
    "IP工厂": "ipgc",
    "IP增长引擎": "zengzhang",
    "商业IP实战笔记": "shizhan",
}

WECHAT_ACCOUNT_CANONICAL_BY_KEY = {
    re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", alias.strip().lower()): canonical
    for canonical, aliases in WECHAT_ACCOUNT_ALIASES.items()
    for alias in aliases
}

PLATFORM_ALIASES = {
    "wechat": "wechat",
    "公众号": "wechat",
    "xhs": "xhs",
    "小红书": "xhs",
    "douyin": "douyin",
    "抖音": "douyin",
}


def _load_env_fallbacks() -> None:
    for name in ENV_FALLBACK_FILES:
        env_path = SCRIPT_DIR / name
        if not env_path.exists():
            continue
        text = env_path.read_text(encoding="utf-8", errors="ignore")
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and not (os.getenv(key) or "").strip():
                os.environ[key] = value


_load_env_fallbacks()

XHS_ADAPTER_ACTIONS = {"publish", "search", "detail", "comment", "content_data"}


def _normalize_platform(value: str) -> str:
    return PLATFORM_ALIASES.get(str(value or "").strip().lower(), "")


def _normalize_label(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _canonical_wechat_account(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = _normalize_label(raw)
    return WECHAT_ACCOUNT_CANONICAL_BY_KEY.get(normalized, raw)


def _wechat_account_slug(value: str) -> str:
    canonical = _canonical_wechat_account(value)
    slug = WECHAT_ACCOUNT_SLUGS.get(canonical)
    if slug:
        return slug
    fallback = re.sub(r"[^0-9a-z]+", "-", _normalize_label(canonical or value)).strip("-")
    return fallback or "default"


def _candidate_env_names(*parts: str) -> list[str]:
    return ["_".join(part for part in parts if part).upper()]


def _first_env_text(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _parse_bool_text(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_mode(value: str, *, default: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    if raw in {"draft", "publish", "schedule"}:
        return raw
    if raw in {"prepare", "approve"}:
        return raw
    return default


def _env_enabled(name: str, *, default: bool = False) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _xhs_adapter_enabled() -> bool:
    return _env_enabled("XHS_ADAPTER_ENABLED", default=False)


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
    repo_local = SCRIPT_DIR / "publish_wechat_playwright.py"
    if repo_local.exists():
        return repo_local
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
    cta_section = _extract_section(body, "CTA")
    tags = _extract_tags(body)
    return {
        "path": content_path,
        "meta": meta,
        "title": title,
        "body": body_section.strip(),
        "cta": cta_section.strip(),
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


def _preferred_profile_dir(platform: str, *, account: str = "") -> str:
    metrics_base = REPO_ROOT / "06-工具" / "data" / "profiles"
    automation_base = REPO_ROOT / "06-工具" / "data" / "automation" / "profiles"
    if platform == "wechat":
        candidates: list[Path] = []
        slug = _wechat_account_slug(account) if account else ""
        if slug:
            specific_env = _first_env_text(
                *_candidate_env_names("WECHAT", slug, "PROFILE", "DIR"),
                *_candidate_env_names("WECHAT", slug, "USER", "DATA", "DIR"),
            )
            if specific_env:
                candidates.append(Path(specific_env))
            candidates.append(metrics_base / f"wechat-{slug}")
            candidates.append(automation_base / f"wechat-{slug}")
        generic_env = _first_env_text("WECHAT_PROFILE_DIR")
        if generic_env:
            candidates.append(Path(generic_env))
        candidates.extend((metrics_base / "wechat-metrics", automation_base / "wechat-main"))
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return str(candidates[0])
    if platform == "xhs":
        preferred = metrics_base / "xhs-metrics"
        fallback = automation_base / "xhs-main"
        return str(preferred if preferred.exists() else fallback)
    preferred = metrics_base / "douyin-metrics"
    fallback = automation_base / "douyin-main"
    return str(preferred if preferred.exists() else fallback)


def _has_dedicated_wechat_profile_dir(account: str) -> bool:
    slug = _wechat_account_slug(account)
    if not slug:
        return False
    if _first_env_text(
        *_candidate_env_names("WECHAT", slug, "PROFILE", "DIR"),
        *_candidate_env_names("WECHAT", slug, "USER", "DATA", "DIR"),
    ):
        return True
    metrics_base = REPO_ROOT / "06-묏야" / "data" / "profiles"
    automation_base = REPO_ROOT / "06-묏야" / "data" / "automation" / "profiles"
    candidates = (
        metrics_base / f"wechat-{slug}",
        automation_base / f"wechat-{slug}",
    )
    return any(path.exists() for path in candidates)


def _bitbrowser_headers(cfg: dict[str, Any]) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Language": str(cfg.get("language") or os.getenv("BITBROWSER_LANGUAGE") or "zh"),
    }
    api_key = str(cfg.get("api_key") or os.getenv("BITBROWSER_API_KEY") or os.getenv("BITBROWSER_LOCAL_API_TOKEN") or "").strip()
    if api_key:
        headers["X-API-KEY"] = api_key
    return headers


def _bitbrowser_post(cfg: dict[str, Any], path: str, payload: dict[str, Any]) -> dict[str, Any]:
    base = str(cfg.get("api_base") or os.getenv("BITBROWSER_API_BASE") or os.getenv("BITBROWSER_LOCAL_API_BASE") or "http://127.0.0.1:54345").rstrip("/")
    timeout_sec = int(cfg.get("timeout_sec") or os.getenv("BITBROWSER_TIMEOUT_SEC") or 20)
    request = Request(
        url=f"{base}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_bitbrowser_headers(cfg),
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(3, timeout_sec)) as resp:
            raw = resp.read()
    except HTTPError as exc:
        detail = ""
        try:
            detail = (exc.read() or b"").decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise RuntimeError(f"BitBrowser API HTTP {exc.code}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"BitBrowser API request failed: {exc}") from exc

    try:
        parsed = json.loads((raw or b"{}").decode("utf-8", errors="replace"))
    except Exception as exc:
        raise RuntimeError("BitBrowser API returned non-JSON response") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("BitBrowser API returned invalid response")
    if not parsed.get("success", False):
        raise RuntimeError(str(parsed.get("msg") or f"BitBrowser API call failed: {path}"))
    data = parsed.get("data")
    return data if isinstance(data, dict) else {}


def _bitbrowser_list_profiles(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    page = 0
    page_size = 100
    profiles: list[dict[str, Any]] = []
    while True:
        data = _bitbrowser_post(cfg, "/browser/list", {"page": page, "pageSize": page_size})
        chunk = data.get("list") or []
        if not isinstance(chunk, list) or not chunk:
            break
        profiles.extend(item for item in chunk if isinstance(item, dict))
        total_num = int(data.get("totalNum") or 0)
        if len(chunk) < page_size or (total_num and len(profiles) >= total_num):
            break
        page += 1
        if page > 20:
            break
    return profiles


def _is_wechat_bitbrowser_profile(item: dict[str, Any]) -> bool:
    platform = str(item.get("platform") or "").strip().lower()
    if "mp.weixin.qq.com" in platform:
        return True
    tags = item.get("browserTags") or []
    if isinstance(tags, list):
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            if _normalize_label(tag.get("tagName")) in {"订阅号", "公众号"}:
                return True
    return False


def _lookup_wechat_bitbrowser_profile(account: str, cfg: dict[str, Any]) -> dict[str, Any]:
    canonical = _canonical_wechat_account(account)
    if not canonical:
        return {}
    normalized_targets = {_normalize_label(canonical), _normalize_label(_wechat_account_slug(canonical))}
    for item in _bitbrowser_list_profiles(cfg):
        if not _is_wechat_bitbrowser_profile(item):
            continue
        profile_name = str(item.get("name") or "").strip()
        if _normalize_label(profile_name) not in normalized_targets:
            continue
        profile_id = str(item.get("id") or "").strip()
        if not profile_id:
            continue
        out = dict(cfg)
        out["profile_id"] = profile_id
        out["profile_name"] = profile_name or canonical
        out["resolved_by"] = "bitbrowser_profile_name"
        return out
    return {}


def _platform_bitbrowser_cfg(platform: str, *, account: str = "") -> dict[str, Any]:
    prefix = {
        "wechat": "WECHAT",
        "xhs": "XHS",
        "douyin": "DOUYIN",
    }.get(platform, "")
    if not prefix:
        return {}

    cfg: dict[str, Any] = {}
    account_slug = _wechat_account_slug(account) if platform == "wechat" and account else ""

    profile_id = ""
    if account_slug:
        profile_id = _first_env_text(
            *_candidate_env_names(prefix, account_slug, "BITBROWSER", "PROFILE", "ID"),
            *_candidate_env_names(prefix, account_slug, "METRICS", "BITBROWSER", "PROFILE", "ID"),
        )

    api_base = _first_env_text(
        *_candidate_env_names(prefix, account_slug, "BITBROWSER", "API", "BASE"),
        "BITBROWSER_API_BASE",
        "BITBROWSER_LOCAL_API_BASE",
    )
    api_key = _first_env_text(
        *_candidate_env_names(prefix, account_slug, "BITBROWSER", "API", "KEY"),
        "BITBROWSER_API_KEY",
        "BITBROWSER_LOCAL_API_TOKEN",
    )
    timeout_sec = _first_env_text(
        *_candidate_env_names(prefix, account_slug, "BITBROWSER", "TIMEOUT", "SEC"),
        "BITBROWSER_TIMEOUT_SEC",
    )
    cdp_timeout_ms = _first_env_text(
        *_candidate_env_names(prefix, account_slug, "BITBROWSER", "CDP", "TIMEOUT", "MS"),
        "BITBROWSER_CDP_TIMEOUT_MS",
    )
    close_after = _first_env_text(
        *_candidate_env_names(prefix, account_slug, "BITBROWSER", "CLOSE", "AFTER", "PUBLISH"),
        "BITBROWSER_CLOSE_AFTER_COLLECT",
    )

    if api_base:
        cfg["api_base"] = api_base
    if api_key:
        cfg["api_key"] = api_key
    if timeout_sec.isdigit():
        cfg["timeout_sec"] = int(timeout_sec)
    if cdp_timeout_ms.isdigit():
        cfg["cdp_timeout_ms"] = int(cdp_timeout_ms)
    if close_after:
        cfg["close_after_publish"] = _parse_bool_text(close_after)

    if profile_id:
        cfg["profile_id"] = profile_id
        if account_slug:
            cfg["resolved_by"] = "env_account_profile"
        return cfg

    if platform == "wechat" and account:
        looked_up = _lookup_wechat_bitbrowser_profile(account, cfg)
        if looked_up:
            return looked_up

    profile_id = _first_env_text(f"{prefix}_METRICS_BITBROWSER_PROFILE_ID", f"{prefix}_BITBROWSER_PROFILE_ID")
    if profile_id:
        cfg["profile_id"] = profile_id
        cfg["resolved_by"] = "env_shared_profile"
        return cfg
    return {}


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


def _build_platform_payload__legacy(
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


def _build_xhs_adapter_payload(payload: dict[str, Any]) -> dict[str, Any]:
    adapter_payload = payload.get("adapter_payload")
    if isinstance(adapter_payload, dict):
        return dict(adapter_payload)
    content_ref = str(payload.get("content") or "").strip()
    if content_ref:
        content_path = _resolve_content_path(content_ref)
        content_profile = _load_content_profile(content_path)
        return {
            "title": str(content_profile.get("title") or ""),
            "body": str(content_profile.get("body") or ""),
            "tags": list(content_profile.get("tags") or []),
            "images": [str(item).strip() for item in (payload.get("images") or []) if str(item).strip()],
            "videos": [str(item).strip() for item in (payload.get("videos") or []) if str(item).strip()],
            "schedule_time": str(payload.get("schedule_time") or payload.get("时间") or "").strip(),
        }
    return {
        "title": str(payload.get("title") or "").strip(),
        "body": str(payload.get("body") or "").strip(),
        "tags": list(payload.get("tags") or []),
        "images": [str(item).strip() for item in (payload.get("images") or []) if str(item).strip()],
        "videos": [str(item).strip() for item in (payload.get("videos") or []) if str(item).strip()],
        "schedule_time": str(payload.get("schedule_time") or payload.get("时间") or "").strip(),
    }


def _default_wechat_selectors_path() -> str:
    config_path = SCRIPT_DIR / "config" / "selectors.wechat.json"
    return str(config_path) if config_path.exists() else ""


def _render_wechat_content(content_path: Path, *, task_id: str) -> dict[str, Any]:
    preview_root = REPO_ROOT / "reports" / dt.date.today().isoformat() / f"wechat-layout-{task_id}"
    rendered = build_render_payload(
        content_path,
        layout_profile="raphael_wechat_v1",
        preview_root=preview_root,
        task_id=task_id,
    )
    if int(rendered.get("body_image_count") or 0) < 1:
        raise ValueError("wechat article requires at least 1 body image before publish")
    return rendered


def _build_platform_payload(
    *,
    platform: str,
    mode: str,
    content_profile: dict[str, Any],
    rendered_profile: dict[str, Any] | None,
    account: str,
    schedule_time: str,
    images: list[str],
    videos: list[str],
) -> tuple[Path, dict[str, Any]]:
    title = str(content_profile.get("title") or "未命名内容").strip()
    body = str(content_profile.get("body") or "").strip()
    tags = list(content_profile.get("tags") or [])
    if platform == "wechat":
        article_meta = dict(content_profile.get("meta") or {})
        render = dict(rendered_profile or {})
        canonical_account = _canonical_wechat_account(
            account
            or str(article_meta.get("账号") or "").strip()
            or str(article_meta.get("account") or "").strip()
        )
        bitbrowser_cfg = _platform_bitbrowser_cfg("wechat", account=canonical_account)
        user_data_dir = str(os.getenv("WECHAT_PROFILE_DIR") or _preferred_profile_dir("wechat", account=canonical_account))
        allow_shared_fallback = _env_enabled("WECHAT_ALLOW_SHARED_PROFILE_FALLBACK", default=False)
        uses_shared_bitbrowser = str(bitbrowser_cfg.get("resolved_by") or "").strip() == "env_shared_profile"
        if canonical_account and uses_shared_bitbrowser and not allow_shared_fallback:
            raise ValueError(
                f"shared wechat publish profile is not allowed for account: {canonical_account}; "
                "bind this account to a dedicated BitBrowser profile or set WECHAT_ALLOW_SHARED_PROFILE_FALLBACK=true"
            )
        if canonical_account and not bitbrowser_cfg and not _has_dedicated_wechat_profile_dir(canonical_account) and not allow_shared_fallback:
            raise ValueError(
                f"missing dedicated publish profile for wechat account: {canonical_account}; "
                "configure BitBrowser profile by account name or WECHAT_<ACCOUNT>_BITBROWSER_PROFILE_ID"
            )
        author = (
            str(render.get("author") or "").strip()
            or str(article_meta.get("作者") or "").strip()
            or str(article_meta.get("author") or "").strip()
            or canonical_account
            or str(article_meta.get("账号") or "").strip()
            or "OpenClaw"
        )
        payload = {
            "title": str(render.get("title") or title),
            "author": author,
            "content_blocks": list(render.get("content_blocks") or []),
            "content_html": str(render.get("content_html") or ""),
            "cover_path": str(render.get("cover_path") or (images[0] if images else "")),
            "layout_profile": str(render.get("layout_profile") or "raphael_wechat_v1"),
            "theme_id": str(render.get("theme_id") or ""),
            "clipboard_html": str(render.get("clipboard_html_path") or ""),
            "preview_html": str(render.get("preview_html_path") or ""),
            "render_manifest": str(render.get("manifest_path") or ""),
            "mode": mode,
            "publish_time": schedule_time or "",
            "account_profile": {
                "user_data_dir": user_data_dir,
                "headless": False,
                "login_timeout_sec": int(os.getenv("WECHAT_LOGIN_TIMEOUT_SEC", "180")),
                "slow_mo": int(os.getenv("WECHAT_SLOW_MO_MS", "0")),
            },
            "account_name": canonical_account or account,
            "selectors_path": str(os.getenv("WECHAT_SELECTORS_PATH") or _default_wechat_selectors_path()).strip(),
        }
        if bitbrowser_cfg:
            payload["bitbrowser"] = bitbrowser_cfg
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
                "user_data_dir": str(os.getenv("XHS_PROFILE_DIR") or _preferred_profile_dir("xhs")),
                "headless": False,
                "login_timeout_sec": int(os.getenv("XHS_LOGIN_TIMEOUT_SEC", "180")),
                "slow_mo": int(os.getenv("XHS_SLOW_MO_MS", "0")),
            },
            "selectors_path": str(os.getenv("XHS_SELECTORS_PATH") or "").strip(),
        }
        bitbrowser_cfg = _platform_bitbrowser_cfg("xhs")
        if bitbrowser_cfg:
            payload["bitbrowser"] = bitbrowser_cfg
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
                "user_data_dir": str(os.getenv("DOUYIN_PROFILE_DIR") or _preferred_profile_dir("douyin")),
                "headless": False,
                "login_timeout_sec": int(os.getenv("DOUYIN_LOGIN_TIMEOUT_SEC", "180")),
                "slow_mo": int(os.getenv("DOUYIN_SLOW_MO_MS", "0")),
            },
            "selectors_path": str(os.getenv("DOUYIN_SELECTORS_PATH") or "").strip(),
        }
        bitbrowser_cfg = _platform_bitbrowser_cfg("douyin")
        if bitbrowser_cfg:
            payload["bitbrowser"] = bitbrowser_cfg
        return _douyin_script(), payload

    raise ValueError(f"unsupported platform: {platform}")


def _maybe_forward_xhs_adapter(payload: dict[str, Any], *, dry_run: bool) -> dict[str, Any] | None:
    if not _xhs_adapter_enabled():
        return None
    platform = _normalize_platform(str(payload.get("platform") or ""))
    if platform != "xhs":
        return None
    adapter_action = str(payload.get("adapter_action") or payload.get("xhs_action") or "publish").strip().lower()
    if adapter_action not in XHS_ADAPTER_ACTIONS:
        return None
    account_id = str(payload.get("account_id") or payload.get("account") or "").strip()
    if not account_id:
        return {"status": "error", "phase": "adapter", "platform": "xhs", "errors": ["account/account_id is required"]}
    timeout_sec = int(payload.get("timeout_sec") or 180)
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    trace_id = str(payload.get("trace_id") or make_task_id("xhsa")).strip()
    adapter_payload = _build_xhs_adapter_payload(payload)
    try:
        from adapters.xhs.client import XHSAdapterClient

        client = XHSAdapterClient.from_env(repo_root=REPO_ROOT)
        adapter_result = client.execute(
            action=adapter_action,
            account_id=account_id,
            payload=adapter_payload,
            idempotency_key=idempotency_key,
            dry_run=dry_run,
            timeout_sec=timeout_sec,
            trace_id=trace_id,
        )
        run_log = append_run_log(
            "xhs_adapter",
            {
                "action": adapter_action,
                "account": account_id,
                "trace_id": adapter_result.trace_id,
                "status": adapter_result.status,
                "ok": adapter_result.ok,
            },
        )
        return {
            "status": "success" if adapter_result.ok else "error",
            "phase": "adapter",
            "platform": "xhs",
            "account": account_id,
            "action": adapter_action,
            "trace_id": adapter_result.trace_id,
            "run_log": run_log,
            "result": adapter_result.to_dict(),
        }
    except Exception as exc:
        return {
            "status": "error",
            "phase": "adapter",
            "platform": "xhs",
            "account": account_id,
            "action": adapter_action,
            "errors": [str(exc)],
        }


def preview_publish(payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    platform = _normalize_platform(str(payload.get("platform") or "wechat"))
    if platform and platform != "wechat":
        raise ValueError("publish preview currently supports only wechat")
    content_ref = str(payload.get("content") or "").strip()
    if not content_ref:
        raise ValueError("content is required")
    content_path = _resolve_content_path(content_ref)
    preview_id = make_task_id("layout")
    rendered = _render_wechat_content(content_path, task_id=preview_id)
    try:
        content_ref_out = content_path.relative_to(REPO_ROOT).as_posix()
    except Exception:
        content_ref_out = content_path.as_posix()
    return {
        "status": "success",
        "phase": "preview",
        "platform": "wechat",
        "content_path": content_ref_out,
        "layout_profile": str(rendered.get("layout_profile") or ""),
        "theme_id": str(rendered.get("theme_id") or ""),
        "preview_dir": str(rendered.get("preview_dir") or ""),
        "clipboard_html": str(rendered.get("clipboard_html_path") or ""),
        "preview_html": str(rendered.get("preview_html_path") or ""),
        "render_manifest": str(rendered.get("manifest_path") or ""),
        "body_image_count": int(rendered.get("body_image_count") or 0),
        "dry_run": dry_run,
    }


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
    task_id = make_task_id("pub")
    content_profile = _load_content_profile(content_path)
    if not account:
        meta = dict(content_profile.get("meta") or {})
        account = (
            str(meta.get("账号") or "").strip()
            or str(meta.get("璐﹀彿") or "").strip()
            or str(meta.get("account") or "").strip()
        )
    rendered_profile = _render_wechat_content(content_path, task_id=task_id) if platform == "wechat" else None

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
        rendered_profile=rendered_profile,
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
    if rendered_profile:
        task_payload.update(
            {
                "layout_profile": str(rendered_profile.get("layout_profile") or ""),
                "theme_id": str(rendered_profile.get("theme_id") or ""),
                "clipboard_html": str(rendered_profile.get("clipboard_html_path") or ""),
                "preview_dir": str(rendered_profile.get("preview_dir") or ""),
                "preview_html": str(rendered_profile.get("preview_html_path") or ""),
                "render_manifest": str(rendered_profile.get("manifest_path") or ""),
                "body_image_count": int(rendered_profile.get("body_image_count") or 0),
            }
        )

    task_result = {
        "precheck": precheck,
        "precheck_status": precheck_status,
    }
    if rendered_profile:
        task_result["theme_id"] = str(rendered_profile.get("theme_id") or "")
        task_result["clipboard_html"] = str(rendered_profile.get("clipboard_html_path") or "")
        task_result["preview_html"] = str(rendered_profile.get("preview_html_path") or "")
        task_result["render_manifest"] = str(rendered_profile.get("manifest_path") or "")

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
    parser.add_argument("--action", choices=("preview", "prepare", "approve", "retry"), required=True)
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
        if args.action == "preview":
            result = preview_publish(payload, dry_run=args.dry_run)
        elif args.action == "prepare":
            adapter_result = _maybe_forward_xhs_adapter(payload, dry_run=args.dry_run)
            if adapter_result is not None:
                result = adapter_result
            else:
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
