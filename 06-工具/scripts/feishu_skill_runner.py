#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run skill-based content generation for Feishu messages.

Responsibilities:
- Discover skills from repository `skills/` plus repo-local `skill-manifest.json`.
- Resolve skill aliases (id/name/stem/relative path) to one canonical skill.
- Invoke Codex CLI with model `gpt-5.4` (configurable).
- Parse `FILES_JSON + FILE` contract output when present.
- Persist generated markdown under:
  `02-内容生产/{平台}/生成内容/YYYY-MM-DD/`
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import dataclasses
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from skill_manifest import (
    get_repo_skill_entry,
    load_repo_skill_entries,
    resolve_benchmark_report_contexts,
    resolve_quote_theme_contexts,
    suggest_quote_theme,
)
from benchmark_analysis_runner import run_benchmark_analysis
from persona_story_library import load_persona_story_cards, resolve_persona_story_contexts, select_persona_story_cards
from chunshe_engine import (
    dedupe_and_pick_chunshe_topics,
    enrich_chunshe_video_topic,
    infer_chunshe_entry_class,
    is_high_boundary_keyword,
    load_chunshe_topic_seed_pool,
    match_chunshe_topic_seed_examples,
    normalize_chunshe_entry_class,
    normalize_chunshe_output_type,
    normalize_chunshe_role,
    render_chunshe_title_pool,
    select_chunshe_theme_phrase_pack,
    select_chunshe_review_phrase_pack,
    select_chunshe_quote_candidates,
    summarize_recent_history,
    collect_recent_chunshe_history,
)
from chunshe_video_runtime import (
    build_chunshe_video_draft_package_prompt,
    build_chunshe_video_pinned_comment,
    build_chunshe_video_polish_package_prompt,
    build_chunshe_video_reply_template,
    ensure_chunshe_video_core_lines,
    extract_chunshe_video_draft_body,
    normalize_chunshe_video_markdown,
    normalize_chunshe_video_polish_issues,
    render_chunshe_video_markdown,
    validate_chunshe_video_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"
DESKTOP_SKILLS_JSON = REPO_ROOT / "06-工具" / "desktop-app" / "data" / "skills.json"
OUTPUT_ROOT = REPO_ROOT / "02-内容生产"

DEFAULT_MODEL = "gpt-5.4"
FILE_JSON_START = "<!--FILES_JSON_START-->"
FILE_JSON_END = "<!--FILES_JSON_END-->"
FILE_BLOCK_START = "<!--FILE_START-->"
FILE_BLOCK_END = "<!--FILE_END-->"
WECHAT_ACCOUNT_ORDER = ("gongchang", "ipgc", "zengzhang", "shizhan")
DEFAULT_WECHAT_CONTENT_CONCURRENCY = 4
WECHAT_SHARED_STAGES = (
    "brief_normalize",
    "context_validate",
    "benchmark_resolve",
    "quote_theme_select",
    "source_pack",
    "strategy_matrix",
)
XHS_SHARED_STAGES = (
    "brief_normalize",
    "context_validate",
    "benchmark_resolve",
    "quote_theme_select",
    "source_pack",
    "strategy_matrix",
)
CHUNSHE_SHARED_STAGES = (
    "source_pack",
    "topic_planner",
    "topic_dedupe",
    "topic_pick",
    "draft_package",
    "polish_package",
)
CHUNSHE_BODY_CTA_BLACKLIST = (
    "评论",
    "私信",
    "关注",
    "加V",
    "加v",
    "VX",
    "vx",
    "大众点评",
    "团购",
    "价格",
    "预约",
    "到店",
)
CHUNSHE_INTRO_HARD_ISSUES = {
    "仍有问答模板味",
    "仍有值不值模板味",
    "仍有身份开头",
}

DEFAULT_PLATFORM_BY_SKILL = {
    "wechat": "公众号",
    "公众号批量生产": "公众号",
    "生成公众号内容": "公众号",
    "公众号内容生成": "公众号",
    "wechat_prompt_normalize": "公众号",
    "公众号配图提示词标准化": "公众号",
    "标准化公众号配图提示词": "公众号",
    "wechat_image": "公众号",
    "公众号图片生成": "公众号",
    "生成公众号图片": "公众号",
    "wechat_topic_refine": "公众号",
    "公众号选题深化": "公众号",
    "深化公众号选题": "公众号",
    "wechat_benchmark_analyze": "公众号",
    "公众号对标文案分析": "公众号",
    "分析公众号对标文案": "公众号",
    "xhs": "小红书",
    "小红书内容生产": "小红书",
    "短视频脚本生产": "短视频",
}

CANONICAL_PLATFORM_BY_ALIAS = {
    "公众号": "公众号",
    "wechat": "公众号",
    "小红书": "小红书",
    "xhs": "小红书",
    "短视频": "短视频",
    "douyin": "短视频",
    "shortvideo": "短视频",
}


@dataclasses.dataclass(frozen=True)
class SkillDefinition:
    skill_id: str
    name: str
    path: Path
    aliases: tuple[str, ...]
    default_platform: str
    kind: str
    default_contexts: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class SkillRegistry:
    by_id: dict[str, SkillDefinition]
    alias_to_id: dict[str, str]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _today() -> str:
    return dt.datetime.now().date().isoformat()


def _today_compact() -> str:
    return dt.datetime.now().strftime("%Y%m%d")


def _normalize_key(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def _sanitize_segment(value: str, fallback: str = "通用") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    text = re.sub(r"[\\/:*?\"<>|]+", "-", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or fallback


def _sanitize_filename(value: str, fallback: str = "content.md") -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    text = text.replace("\\", "/").split("/")[-1]
    text = re.sub(r"[\\/:*?\"<>|]+", "-", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text:
        text = fallback
    if not text.lower().endswith(".md"):
        text += ".md"
    return text


def _canonical_platform_label(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    canonical = CANONICAL_PLATFORM_BY_ALIAS.get(_normalize_key(raw))
    if canonical:
        return canonical
    return _sanitize_segment(raw, fallback="")


def _resolve_platform_segment(skill: "SkillDefinition", platform: str) -> str:
    for candidate in (
        platform,
        getattr(skill, "default_platform", ""),
        DEFAULT_PLATFORM_BY_SKILL.get(str(getattr(skill, "skill_id", "") or "").strip(), ""),
    ):
        resolved = _canonical_platform_label(candidate)
        if resolved:
            return resolved
    return "通用"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _safe_repo_relative(rel_path: str) -> tuple[str, Path] | None:
    text = str(rel_path or "").strip().replace("\\", "/")
    if not text:
        return None
    candidate = (REPO_ROOT / text).resolve()
    try:
        candidate.relative_to(REPO_ROOT)
    except Exception:
        return None
    return candidate.relative_to(REPO_ROOT).as_posix(), candidate


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    candidates = ("utf-8", "utf-8-sig", "gb18030", "gbk")
    for encoding in candidates:
        try:
            return json.loads(path.read_text(encoding=encoding))
        except Exception:
            continue
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default


def _resolve_context_path(base: Path, relative_path: str) -> Path:
    raw = str(relative_path or "").strip().replace("\\", "/")
    candidate = (base / raw).resolve()
    if candidate.exists():
        return candidate

    # Compatibility fallback: if desktop skill context paths are anchored to an
    # older base directory, strip leading ../ segments and resolve from repo root.
    normalized = raw
    while normalized.startswith("../"):
        normalized = normalized[3:]
    if normalized:
        repo_candidate = (REPO_ROOT / normalized).resolve()
        if repo_candidate.exists():
            return repo_candidate

    return candidate


def _codex_home() -> Path:
    env_home = str(os.getenv("CODEX_HOME") or "").strip()
    if env_home:
        return Path(env_home)
    if os.name == "nt":
        userprofile = str(os.getenv("USERPROFILE") or "").strip()
        if userprofile:
            return Path(userprofile) / ".codex"
    return Path.home() / ".codex"


def _skill_roots() -> list[Path]:
    roots: list[Path] = [SKILLS_ROOT]
    global_root = (_codex_home() / "skills").resolve()
    roots.append(global_root)

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _platform_for_skill(skill_id: str, aliases: set[str]) -> str:
    candidates = [skill_id, *aliases]
    for item in candidates:
        if item in DEFAULT_PLATFORM_BY_SKILL:
            return DEFAULT_PLATFORM_BY_SKILL[item]
    for item in candidates:
        normalized = _normalize_key(item)
        for key, platform in DEFAULT_PLATFORM_BY_SKILL.items():
            if normalized == _normalize_key(key):
                return platform
    return "通用"


def _is_execution_skill(skill: SkillDefinition | str) -> bool:
    if isinstance(skill, SkillDefinition):
        return str(skill.kind or "").strip().lower() == "execution"
    entry = get_repo_skill_entry(str(skill or "").strip())
    if entry:
        return str(entry.kind or "").strip().lower() == "execution"
    return False


def _auto_context_files_for_skill(skill_id: str) -> list[str]:
    entry = get_repo_skill_entry(str(skill_id or "").strip())
    if entry:
        return list(entry.default_contexts)
    normalized = str(skill_id or "").strip()
    if _is_execution_skill(normalized):
        return ["06-工具/scripts/README_WECHAT_IMAGE_GENERATOR.md"]
    return []


def build_skill_registry() -> SkillRegistry:
    by_id: dict[str, SkillDefinition] = {}
    repo_manifest_alias_keys: set[str] = set()
    repo_manifest_paths: set[Path] = set()

    def upsert(
        skill_id: str,
        name: str,
        path: Path,
        aliases: set[str],
        *,
        kind: str = "content",
        default_contexts: tuple[str, ...] = (),
    ) -> None:
        if not path.exists() or path.suffix.lower() != ".md":
            return
        current = by_id.get(skill_id)
        if current:
            merged_aliases = set(current.aliases) | aliases | {skill_id, name}
            platform = current.default_platform or _platform_for_skill(skill_id, merged_aliases)
            by_id[skill_id] = SkillDefinition(
                skill_id=skill_id,
                name=current.name or name,
                path=current.path,
                aliases=tuple(sorted(merged_aliases)),
                default_platform=platform,
                kind=current.kind or kind,
                default_contexts=tuple(dict.fromkeys([*current.default_contexts, *default_contexts])),
            )
            return

        merged_aliases = aliases | {skill_id, name, path.stem}
        platform = _platform_for_skill(skill_id, merged_aliases)
        by_id[skill_id] = SkillDefinition(
            skill_id=skill_id,
            name=name,
            path=path,
            aliases=tuple(sorted(merged_aliases)),
            default_platform=platform,
            kind=kind,
            default_contexts=tuple(dict.fromkeys(default_contexts)),
        )

    # 0) Repo-local skill manifest is the primary source of truth for markdown skills.
    for entry in load_repo_skill_entries():
        repo_manifest_paths.add(entry.abs_path.resolve())
        for alias in (*entry.aliases, entry.skill_id, entry.name):
            key = _normalize_key(alias)
            if key:
                repo_manifest_alias_keys.add(key)
        upsert(
            entry.skill_id,
            entry.name,
            entry.abs_path,
            set(entry.aliases),
            kind=entry.kind,
            default_contexts=entry.default_contexts,
        )

    # 1) Desktop app mapping remains only as a compatibility fallback for markdown-backed desktop skills.
    desktop_payload = _load_json(DESKTOP_SKILLS_JSON, {"skills": []})
    skills = desktop_payload.get("skills") if isinstance(desktop_payload, dict) else []
    if isinstance(skills, list):
        for item in skills:
            if not isinstance(item, dict):
                continue
            skill_id = str(item.get("id") or "").strip()
            if not skill_id:
                continue
            name = str(item.get("name") or skill_id).strip() or skill_id
            desktop_keys = {
                key
                for key in (
                    _normalize_key(skill_id),
                    _normalize_key(name),
                )
                if key
            }
            # Repo-local manifest owns those skills; desktop metadata must not
            # re-register or extend them.
            if desktop_keys & repo_manifest_alias_keys:
                continue
            aliases: set[str] = {skill_id, name}
            default_contexts = item.get("defaultContexts")
            if isinstance(default_contexts, list):
                for rel in default_contexts:
                    rel_path = str(rel or "").strip()
                    if not rel_path:
                        continue
                    candidate = _resolve_context_path(DESKTOP_SKILLS_JSON.parent, rel_path)
                    if candidate.exists() and candidate.suffix.lower() == ".md":
                        aliases.add(candidate.stem)
                        aliases.add(candidate.relative_to(SKILLS_ROOT).as_posix() if SKILLS_ROOT in candidate.parents else candidate.as_posix())
                        upsert(skill_id, name, candidate, aliases)

    # 2) Skill markdown files from repository and global CODEX_HOME.
    for root in _skill_roots():
        if not root.exists():
            continue
        for path in root.rglob("SKILL.md"):
            if not path.is_file():
                continue
            if path.resolve() in repo_manifest_paths:
                continue
            stem = path.parent.name or path.stem
            skill_id = stem
            aliases = {stem, path.as_posix(), path.parent.as_posix()}
            try:
                aliases.add(path.relative_to(root).as_posix())
                aliases.add(path.parent.relative_to(root).as_posix())
            except Exception:
                pass
            # If it looks like known skill names, map to stable ids.
            normalized_stem = _normalize_key(stem)
            for known_id in ("wechat", "xhs", "douyin"):
                normalized_known = _normalize_key(known_id)
                if normalized_stem == normalized_known:
                    skill_id = known_id
            upsert(skill_id, stem, path, aliases)

    alias_to_id: dict[str, str] = {}
    for skill in by_id.values():
        for alias in skill.aliases:
            key = _normalize_key(alias)
            if key and key not in alias_to_id:
                alias_to_id[key] = skill.skill_id
        key = _normalize_key(skill.skill_id)
        if key:
            alias_to_id[key] = skill.skill_id
        key = _normalize_key(skill.name)
        if key and key not in alias_to_id:
            alias_to_id[key] = skill.skill_id

    return SkillRegistry(by_id=by_id, alias_to_id=alias_to_id)


def list_skills_payload(registry: SkillRegistry) -> list[dict[str, Any]]:
    out = []
    for skill_id in sorted(registry.by_id):
        skill = registry.by_id[skill_id]
        out.append(
            {
                "skill_id": skill.skill_id,
                "name": skill.name,
                "path": skill.path.as_posix(),
                "default_platform": skill.default_platform,
                "kind": skill.kind,
                "aliases": list(skill.aliases),
            }
        )
    return out


def resolve_skill(registry: SkillRegistry, skill_ref: str) -> SkillDefinition:
    ref = str(skill_ref or "").strip()
    if not ref:
        raise ValueError("skill_id is required")

    if ref in registry.by_id:
        return registry.by_id[ref]

    key = _normalize_key(ref)
    resolved_id = registry.alias_to_id.get(key)
    if resolved_id and resolved_id in registry.by_id:
        return registry.by_id[resolved_id]

    # Stable fallback aliases for Feishu `/skill` commands.
    fallback_alias = {
        _normalize_key("wechat"): "wechat",
        _normalize_key("公众号"): "wechat",
        _normalize_key("生成公众号内容"): "wechat",
        _normalize_key("wechat_image"): "wechat_image",
        _normalize_key("公众号图片生成"): "wechat_image",
        _normalize_key("生成公众号图片"): "wechat_image",
        _normalize_key("wechat_prompt_normalize"): "wechat_prompt_normalize",
        _normalize_key("标准化公众号配图提示词"): "wechat_prompt_normalize",
        _normalize_key("wechat_topic_refine"): "wechat_topic_refine",
        _normalize_key("深化公众号选题"): "wechat_topic_refine",
        _normalize_key("wechat_benchmark_analyze"): "wechat_benchmark_analyze",
        _normalize_key("分析公众号对标文案"): "wechat_benchmark_analyze",
        _normalize_key("xhs"): "小红书内容生产",
        _normalize_key("小红书"): "小红书内容生产",
        _normalize_key("shortvideo"): "短视频脚本生产",
        _normalize_key("短视频"): "短视频脚本生产",
        _normalize_key("xhs"): "xhs",
        _normalize_key("小红书"): "xhs",
        _normalize_key("小红书内容生成"): "xhs",
        _normalize_key("生成小红书内容"): "xhs",
        _normalize_key("xhs_prompt_normalize"): "xhs_prompt_normalize",
        _normalize_key("标准化小红书配图提示词"): "xhs_prompt_normalize",
        _normalize_key("小红书配图提示词标准化"): "xhs_prompt_normalize",
        _normalize_key("xhs_image"): "xhs_image",
        _normalize_key("生成小红书图片"): "xhs_image",
        _normalize_key("小红书图片生成"): "xhs_image",
    }.get(key, "")
    if fallback_alias:
        alias_key = _normalize_key(fallback_alias)
        resolved_id = registry.alias_to_id.get(alias_key)
        if resolved_id and resolved_id in registry.by_id:
            return registry.by_id[resolved_id]

    available = ", ".join(sorted(registry.by_id.keys())[:15])
    raise ValueError(f"skill not found: {skill_ref}. available={available}")


def resolve_codex_cli() -> str:
    candidates: list[str] = []

    env_path = str(os.getenv("CODEX_CLI_PATH") or "").strip()
    if env_path:
        candidates.append(env_path)

    bundled = REPO_ROOT / "bin" / "windows-x86_64" / "codex.exe"
    if bundled.exists():
        candidates.append(str(bundled))

    candidates.append("codex")

    for item in candidates:
        if item == "codex":
            resolved = shutil.which(item)
            if resolved:
                return resolved
            continue
        candidate = Path(item)
        if candidate.exists():
            return str(candidate)

    raise RuntimeError("Codex CLI not found. Set CODEX_CLI_PATH or install `codex`.")


def _extract_between(text: str, start: str, end: str) -> str | None:
    idx = text.find(start)
    if idx < 0:
        return None
    jdx = text.find(end, idx + len(start))
    if jdx < 0:
        return None
    return text[idx + len(start) : jdx]


def _try_parse_files_json(text: str) -> dict[str, Any] | None:
    # Preferred protocol: HTML marker wrapped JSON.
    raw = _extract_between(text, FILE_JSON_START, FILE_JSON_END)
    if raw:
        try:
            data = json.loads(raw.strip())
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    # Fallback protocol used by some skills:
    # FILES_JSON
    # ```json
    # [...]
    # ```
    matched = re.search(r"(?is)FILES_JSON\s*```(?:json)?\s*(.*?)\s*```", str(text or ""))
    if matched:
        raw_json = str(matched.group(1) or "").strip()
        if raw_json:
            try:
                parsed = json.loads(raw_json)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list):
                return {"files": parsed}

    # Fallback protocol seen in cloud runtime:
    # { "FILES_JSON": [...] }
    # <blank line>
    # <markdown body>
    stripped = str(text or "").lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            decoder = json.JSONDecoder()
            obj, _end = decoder.raw_decode(stripped)
        except Exception:
            obj = None
        if isinstance(obj, dict):
            files_value = obj.get("FILES_JSON")
            if files_value is None:
                files_value = obj.get("files")
            if isinstance(files_value, list):
                return {"files": files_value}
            if isinstance(files_value, dict):
                return files_value
    return None


def _extract_file_blocks(text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    cursor = 0
    while True:
        start = text.find(FILE_BLOCK_START, cursor)
        if start < 0:
            break
        end = text.find(FILE_BLOCK_END, start + len(FILE_BLOCK_START))
        if end < 0:
            break
        chunk = text[start + len(FILE_BLOCK_START) : end].lstrip("\r\n")
        match = re.match(r"^([^\r\n]+)\r?\n([\s\S]*)$", chunk)
        if match:
            blocks.append({"path": match.group(1).strip(), "content": match.group(2).rstrip()})
        cursor = end + len(FILE_BLOCK_END)

    if blocks:
        return blocks

    # Fallback protocol:
    # FILE: `path/to/file.md`
    # ```markdown
    # ...
    # ```
    plain_pattern = re.compile(
        r"(?ms)^FILE:\s*`?([^\r\n`]+?)`?\s*\r?\n```(?:markdown|md)?\r?\n(.*?)\r?\n```"
    )
    for matched in plain_pattern.finditer(str(text or "")):
        path = str(matched.group(1) or "").strip()
        content = str(matched.group(2) or "").rstrip()
        if path and content:
            blocks.append({"path": path, "content": content})
    return blocks


def _coerce_markdown_files(text: str) -> list[dict[str, str]]:
    ordered = _try_parse_files_json(text)
    blocks = _extract_file_blocks(text)
    if not ordered and not blocks:
        return []

    block_map = {item["path"]: item["content"] for item in blocks}
    files: list[dict[str, str]] = []
    if ordered and isinstance(ordered.get("files"), list):
        for item in ordered["files"]:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            content = block_map.get(path)
            if content is None:
                content = str(item.get("content") or "")
            if not content:
                continue
            files.append({"path": path, "content": content})
    else:
        files.extend(blocks)
    return files


def _extract_short_title(markdown_text: str, fallback: str = "生成文案") -> str:
    for line in str(markdown_text or "").splitlines():
        item = line.strip()
        if not item:
            continue
        if item.startswith("#"):
            title = re.sub(r"^#+\s*", "", item).strip()
            if title:
                return title[:24]
    return fallback


def _select_primary_file(files: list[dict[str, str]]) -> dict[str, str] | None:
    if not files:
        return None

    def score(item: dict[str, str]) -> tuple[int, int]:
        path = str(item.get("path") or "")
        content = str(item.get("content") or "")
        name = Path(path.replace("\\", "/")).name
        bonus = 0
        if re.search(r"(正文|成稿|全文|main|article|body)", name, re.IGNORECASE):
            bonus += 10_000
        if re.search(r"(正文|成稿|全文|main|article|body)", path, re.IGNORECASE):
            bonus += 5_000
        if re.search(r"(标题|候选|大纲|提纲|top2|outline|title)", name, re.IGNORECASE):
            bonus -= 10_000
        if re.search(r"(标题|候选|大纲|提纲|top2|outline|title)", path, re.IGNORECASE):
            bonus -= 5_000
        return (bonus, len(content))

    return max(files, key=score)


def _render_full_text_for_reply(text: str) -> str:
    files = _coerce_markdown_files(text)
    primary = _select_primary_file(files)
    if primary:
        return str(primary.get("content") or "").strip()

    cleaned = str(text or "")

    # Remove leading JSON object that carries FILES_JSON metadata.
    stripped = cleaned.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            decoder = json.JSONDecoder()
            obj, end_idx = decoder.raw_decode(stripped)
        except Exception:
            obj = None
            end_idx = 0
        if isinstance(obj, dict) and ("FILES_JSON" in obj or "files" in obj):
            cleaned = stripped[end_idx:].lstrip()

    cleaned = re.sub(r"(?is)FILES_JSON\s*```(?:json)?\s*.*?\s*```", "", cleaned)
    cleaned = re.sub(r"(?im)^\s*No callable skill[^\r\n]*$", "", cleaned)
    cleaned = re.sub(
        r"(?ms)^FILE:\s*`?[^\r\n`]+`?\s*\r?\n```(?:markdown|md)?\r?\n.*?\r?\n```",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?im)^\s*FILE:\s*`?[^\r\n`]+`?\s*$", "", cleaned)
    cleaned = re.sub(r"(?im)^\s*```(?:markdown|md)?\s*$", "", cleaned)
    cleaned = re.sub(r"(?im)^\s*```\s*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _prepare_context_blocks(context_files: list[str] | None) -> tuple[list[str], list[str], str]:
    if not context_files:
        return [], [], ""

    used: list[str] = []
    warnings: list[str] = []
    blocks: list[str] = []
    seen: set[str] = set()

    for raw in context_files:
        resolved = _safe_repo_relative(str(raw or ""))
        if not resolved:
            warnings.append(f"path escapes repo or invalid: {raw}")
            continue
        rel_text, abs_path = resolved
        if rel_text in seen:
            continue
        seen.add(rel_text)
        if not abs_path.exists() or not abs_path.is_file():
            warnings.append(f"missing context file: {rel_text}")
            continue
        try:
            content = _read_text(abs_path).strip()
        except Exception as exc:
            warnings.append(f"read failed {rel_text}: {exc}")
            continue
        if not content:
            warnings.append(f"empty context file: {rel_text}")
            continue
        used.append(rel_text)
        blocks.append(f"File: {rel_text}\n{content}")

    prompt_text = ""
    if blocks:
        prompt_text = "【参考资料】\n" + "\n\n".join(blocks) + "\n\n"
    return used, warnings, prompt_text


def _brief_field(brief: str, *labels: str) -> str:
    text = str(brief or "")
    separators = (":", "\uFF1A", "=", "\uFF1D")
    for raw_line in text.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        for label in labels:
            label_text = str(label or "").strip()
            if not label_text:
                continue
            patterns = (
                rf"^{re.escape(label_text)}\s*[:：=＝]\s*(.+?)\s*$",
                rf"(?:^|[：:])\s*{re.escape(label_text)}\s*[:：=＝]\s*(.+?)\s*$",
            )
            for pattern in patterns:
                matched = re.search(pattern, line)
                if matched:
                    return str(matched.group(1) or "").strip()
            if line.startswith(label_text):
                remainder = line[len(label_text):].lstrip()
                if remainder and remainder[0] in separators:
                    return remainder[1:].strip()
    return ""


def _brief_int(brief: str, default: int, *labels: str) -> int:
    raw = _brief_field(brief, *labels)
    if not raw:
        return default
    matched = re.search(r"-?\d+", str(raw))
    if not matched:
        return default
    try:
        return int(matched.group(0))
    except Exception:
        return default
def _brief_flag_enabled(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "是", "开启", "开"}


def _brief_flag_or_default(brief: str, default: bool, *labels: str) -> bool:
    raw = _brief_field(brief, *labels)
    if not raw:
        return default
    return _brief_flag_enabled(raw)


def _brief_story_enabled(brief: str, *, default: bool = True) -> bool:
    raw = _brief_field(brief, "是否需要人设故事", "人设故事", "need_story")
    if not raw:
        return default
    lowered = str(raw).strip().lower()
    if lowered in {"否", "不要", "关闭", "off", "false", "0", "no", "n"}:
        return False
    return True


def _auto_quote_theme_from_brief(brief: str) -> str:
    return suggest_quote_theme(
        _brief_field(brief, "主题矿区/选题", "主关键词", "topic"),
        _brief_field(brief, "目标人群", "target"),
        _brief_field(brief, "核心矛盾"),
        _brief_field(brief, "场景证据/案例素材", "场景证据"),
    )


def _wechat_story_plan(usage: str) -> tuple[int, list[str]]:
    text = str(usage or "").strip()
    if not text or text == "自动":
        return 1, ["关键案例"]

    placements: list[str] = []
    if "开头" in text or "立人设" in text:
        placements.append("开头立人设")
    if "中段" in text or "举例" in text or "案例" in text:
        placements.append("关键案例")
    if "结尾" in text or "回扣" in text or "收束" in text:
        placements.append("结尾回扣")

    deduped = list(dict.fromkeys(placements)) or ["关键案例"]
    max_story_count = 2 if len(deduped) >= 2 else 1
    return max_story_count, deduped


def _conditional_context_files_for_skill(skill_id: str, brief: str) -> tuple[list[str], list[str], list[str]]:
    normalized = str(skill_id or "").strip()
    contexts: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []

    if normalized not in {"wechat", "xhs", "chunshe_wj"}:
        return contexts, warnings, errors

    quote_enabled = _brief_flag_or_default(brief, normalized == "wechat", "是否调用金句库", "调用金句库")
    quote_theme = _brief_field(brief, "金句主题", "quote_theme")
    benchmark_ref = _brief_field(brief, "参考对标文案", "对标文案", "benchmark_ref")
    fugui_enabled = _brief_flag_enabled(_brief_field(brief, "富贵模块开关", "是否启用富贵模块", "fugui"))
    story_enabled = _brief_story_enabled(brief, default=normalized == "wechat")

    mode = _brief_field(brief, "模式", "mode")

    if quote_enabled:
        selected_quote_theme = quote_theme or _auto_quote_theme_from_brief(brief)
        if normalized == "chunshe_wj" and not selected_quote_theme:
            selected_quote_theme = "人性与沟通"
        quote_contexts = resolve_quote_theme_contexts(selected_quote_theme)
        if quote_contexts:
            contexts.extend(quote_contexts)
            if not quote_theme:
                warnings.append(f"quote theme auto-selected: {selected_quote_theme}")
        else:
            errors.append(f"quote theme not found: {selected_quote_theme}")
    if benchmark_ref and normalized == "wechat":
        matches = resolve_benchmark_report_contexts(benchmark_ref)
        if matches:
            contexts.extend(matches)
        else:
            warnings.append(f"benchmark analysis report pending auto-generation: {benchmark_ref}")
    if fugui_enabled:
        contexts.append("03-素材库/增强模块/富贵-打动人模块.md")
    if story_enabled:
        contexts.extend(resolve_persona_story_contexts())

    if normalized == "xhs" and "情绪冲突" in mode:
        contexts.append("02-内容生产/小红书/resources/情绪冲突内容引擎.md")

    return contexts, warnings, errors


def build_skill_context_plan(
    *,
    skill_id: str,
    brief: str,
    platform: str = "",
    context_files: list[str] | None = None,
) -> dict[str, Any]:
    merged: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()

    def add(path_text: str) -> None:
        resolved = _safe_repo_relative(path_text)
        if not resolved:
            warnings.append(f"path escapes repo or invalid: {path_text}")
            return
        rel_text, abs_path = resolved
        if not abs_path.exists() or not abs_path.is_file():
            warnings.append(f"missing context file: {rel_text}")
            return
        if rel_text in seen:
            return
        seen.add(rel_text)
        merged.append(rel_text)

    for raw in context_files or []:
        add(str(raw or ""))

    auto_files = _auto_context_files_for_skill(skill_id)
    conditional_files, conditional_warnings, conditional_errors = _conditional_context_files_for_skill(skill_id, brief)

    for raw in auto_files:
        add(raw)

    for raw in conditional_files:
        add(raw)

    for warning in conditional_warnings:
        if warning not in warnings:
            warnings.append(warning)
    for error in conditional_errors:
        if error not in errors:
            errors.append(error)

    return {
        "skill_id": skill_id,
        "platform": platform,
        "context_files_merged": merged,
        "context_files_auto": [item for item in [*auto_files, *conditional_files] if item in merged],
        "context_warnings": warnings,
        "context_errors": errors,
    }


def _resolve_wechat_content_concurrency(requested: int | None = None) -> int:
    value = requested if isinstance(requested, int) and requested > 0 else 0
    if value <= 0:
        raw = str(os.getenv("WECHAT_CONTENT_CONCURRENCY") or "").strip()
        if raw:
            try:
                value = int(raw)
            except Exception:
                value = 0
    if value <= 0:
        value = DEFAULT_WECHAT_CONTENT_CONCURRENCY
    return max(1, min(len(WECHAT_ACCOUNT_ORDER), value))


def _extract_json_payload(text: str) -> Any | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    try:
        return json.loads(raw)
    except Exception:
        pass

    fenced = re.search(r"(?is)```(?:json)?\s*(.*?)\s*```", raw)
    if fenced:
        try:
            return json.loads(str(fenced.group(1) or "").strip())
        except Exception:
            pass

    for idx, char in enumerate(raw):
        if char not in "{[":
            continue
        try:
            decoder = json.JSONDecoder()
            parsed, _end = decoder.raw_decode(raw[idx:])
            return parsed
        except Exception:
            continue
    return None


def _run_codex_json_stage(
    *,
    stage_name: str,
    prompt: str,
    model: str,
    codex_cli: str,
    timeout_sec: int,
) -> tuple[Any, str, str]:
    text, stderr = _run_codex(
        prompt,
        model=model,
        codex_cli=codex_cli,
        timeout_sec=timeout_sec,
    )
    payload = _extract_json_payload(text)
    if payload is None:
        raise RuntimeError(f"invalid JSON output for stage: {stage_name}")
    return payload, text, stderr


def _wechat_generation_task_id(date_str: str) -> str:
    return f"wechat-gen-{str(date_str or _today()).replace('-', '')}-{dt.datetime.now().strftime('%H%M%S')}"


def _wechat_generation_report_dir(date_str: str, task_id: str) -> Path:
    root = REPO_ROOT / "reports" / "wechat-generation" / str(date_str or _today()) / task_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(content or ""), encoding="utf-8")


def _merge_runtime_context_files(base_files: list[str], extra_files: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw in [*base_files, *extra_files]:
        resolved = _safe_repo_relative(raw)
        if not resolved:
            continue
        rel_text, _abs = resolved
        if rel_text in seen:
            continue
        seen.add(rel_text)
        merged.append(rel_text)
    return merged


def _extract_benchmark_scan_date(reference: str, date_str: str) -> str:
    matched = re.search(r"(\d{4}-\d{2}-\d{2})", str(reference or ""))
    if matched:
        return matched.group(1)
    return str(date_str or _today()).strip() or _today()


def _ensure_benchmark_contexts(
    *,
    brief: str,
    date_str: str,
    event_ref: str,
    dry_run: bool,
) -> tuple[list[str], list[str]]:
    benchmark_ref = _brief_field(brief, "参考对标文案", "对标文案", "benchmark_ref")
    if not benchmark_ref:
        return [], []
    matches = resolve_benchmark_report_contexts(benchmark_ref)
    if matches:
        return matches, []
    if dry_run:
        return [], [f"benchmark analysis will auto-run for: {benchmark_ref}"]

    scan_date = _extract_benchmark_scan_date(benchmark_ref, date_str)
    analysis_result = run_benchmark_analysis(
        {
            "date": date_str,
            "scan_date": scan_date,
            "event_ref": event_ref,
        },
        dry_run=False,
    )
    matches = resolve_benchmark_report_contexts(benchmark_ref)
    if matches:
        warnings = [f"benchmark analysis auto-generated report: {', '.join(matches)}"]
        if str(analysis_result.get("status") or "").strip() == "partial":
            warnings.append("benchmark analysis completed with partial errors")
        return matches, warnings
    raise RuntimeError(f"benchmark analysis report not found after auto-generation: {benchmark_ref}")


def _ensure_wechat_benchmark_contexts(
    *,
    brief: str,
    date_str: str,
    event_ref: str,
    dry_run: bool,
) -> tuple[list[str], list[str]]:
    return _ensure_benchmark_contexts(
        brief=brief,
        date_str=date_str,
        event_ref=event_ref,
        dry_run=dry_run,
    )


def _extract_wechat_source_materials(
    *,
    brief: str,
    date_str: str,
    event_ref: str,
    dry_run: bool,
    base_context_files: list[str],
    base_context_warnings: list[str],
) -> tuple[dict[str, Any], list[str], list[str], str]:
    selected_quote_theme = ""
    quote_enabled = _brief_flag_or_default(brief, True, "是否调用金句库", "调用金句库")
    explicit_quote_theme = _brief_field(brief, "金句主题", "quote_theme")
    quote_contexts: list[str] = []
    if quote_enabled:
        selected_quote_theme = explicit_quote_theme or _auto_quote_theme_from_brief(brief)
        if selected_quote_theme:
            quote_contexts = resolve_quote_theme_contexts(selected_quote_theme)

    story_enabled = _brief_story_enabled(brief, default=True)
    story_usage = _brief_field(brief, "故事用途", "story_usage") or "自动"
    max_story_count, story_placements = _wechat_story_plan(story_usage)
    selected_story_cards: list[dict[str, Any]] = []
    story_contexts = resolve_persona_story_contexts()
    story_selection_plan: dict[str, Any] = {
        "enabled": story_enabled,
        "usage": story_usage,
        "max_story_count": max_story_count,
        "placement": story_placements,
        "selected_story_ids": [],
        "selection_reason": "story disabled" if not story_enabled else "awaiting selection",
    }
    if story_enabled:
        selected_story_cards = select_persona_story_cards(
            topic=_brief_field(brief, "主题矿区/选题", "topic"),
            conflict=_brief_field(brief, "核心矛盾"),
            usage=story_usage,
            limit=max_story_count,
        )
        story_selection_plan = {
            "enabled": True,
            "usage": story_usage,
            "max_story_count": max_story_count,
            "placement": story_placements,
            "selected_story_ids": [str(item.get("story_id") or "").strip() for item in selected_story_cards if str(item.get("story_id") or "").strip()],
            "selection_reason": f"matched canonical persona story cards for {', '.join(story_placements)}",
        }

    benchmark_contexts, benchmark_warnings = _ensure_wechat_benchmark_contexts(
        brief=brief,
        date_str=date_str,
        event_ref=event_ref,
        dry_run=dry_run,
    )
    runtime_context_warnings = list(base_context_warnings) + benchmark_warnings
    runtime_context_files = _merge_runtime_context_files(base_context_files, benchmark_contexts)
    runtime_context_files_used, prepared_warnings, runtime_context_prompt = _prepare_context_blocks(runtime_context_files)
    for warning in prepared_warnings:
        if warning not in runtime_context_warnings:
            runtime_context_warnings.append(warning)

    source_materials = {
        "quote_theme_selected": selected_quote_theme,
        "quote_candidates_source": quote_contexts,
        "benchmark_candidates_source": list(benchmark_contexts),
        "story_candidates_source": list(story_contexts),
        "story_library_path": story_contexts[0] if story_contexts else "",
        "persona_story_cards": selected_story_cards,
        "story_selection_plan": story_selection_plan,
        "benchmark_contexts": benchmark_contexts,
    }
    return source_materials, runtime_context_files_used, runtime_context_warnings, runtime_context_prompt


def _normalize_xhs_account(value: str) -> str:
    normalized = _normalize_key(value)
    alias_map = {
        "a": "A",
        "a号": "A",
        "转化": "A",
        "转化号": "A",
        "xhsa": "A",
        "b": "B",
        "b号": "B",
        "交付": "B",
        "交付号": "B",
        "xhsb": "B",
        "c": "C",
        "c号": "C",
        "观点": "C",
        "观点号": "C",
        "xhsc": "C",
    }
    return alias_map.get(normalized, "A")


def _normalize_xhs_mode(value: str) -> str:
    normalized = _normalize_key(value)
    if normalized in {"信息图", "信息图6页", "信息图六页"}:
        return "信息图6页"
    if normalized in {"情绪冲突", "情绪冲突文字帖", "文字帖"}:
        return "情绪冲突文字帖"
    return "信息图6页"


def _extract_xhs_source_materials(
    *,
    brief: str,
    date_str: str,
    event_ref: str,
    dry_run: bool,
    base_context_files: list[str],
    base_context_warnings: list[str],
) -> tuple[dict[str, Any], list[str], list[str], str]:
    mode = _normalize_xhs_mode(_brief_field(brief, "模式", "mode"))
    account = _normalize_xhs_account(_brief_field(brief, "账号", "account", "账号角色", "account_role"))
    quote_enabled = _brief_flag_or_default(brief, False, "是否调用金句库", "调用金句库")
    explicit_quote_theme = _brief_field(brief, "金句主题", "quote_theme")
    selected_quote_theme = explicit_quote_theme or (_auto_quote_theme_from_brief(brief) if quote_enabled else "")

    story_enabled = _brief_story_enabled(brief, default=True)
    story_usage = _brief_field(brief, "故事用途", "story_usage")
    if not story_usage:
        story_usage = "中段举例" if mode == "信息图6页" else "开头立人设"
    selected_story_cards: list[dict[str, Any]] = []
    story_selection_plan: dict[str, Any] = {
        "enabled": story_enabled,
        "usage": story_usage,
        "selected_story_ids": [],
        "selection_reason": "story disabled" if not story_enabled else "awaiting selection",
    }
    if story_enabled:
        selected_story_cards = select_persona_story_cards(
            topic=_brief_field(brief, "主题矿区/选题", "主关键词", "topic"),
            conflict=_brief_field(brief, "核心矛盾"),
            usage=story_usage,
            limit=2,
        )
        story_selection_plan = {
            "enabled": True,
            "usage": story_usage,
            "selected_story_ids": [
                str(item.get("story_id") or "").strip()
                for item in selected_story_cards
                if str(item.get("story_id") or "").strip()
            ],
            "selection_reason": "matched canonical persona story cards",
        }

    benchmark_contexts, benchmark_warnings = _ensure_benchmark_contexts(
        brief=brief,
        date_str=date_str,
        event_ref=event_ref,
        dry_run=dry_run,
    )
    runtime_context_warnings = list(base_context_warnings) + benchmark_warnings
    runtime_context_files = _merge_runtime_context_files(base_context_files, benchmark_contexts)
    runtime_context_files_used, prepared_warnings, runtime_context_prompt = _prepare_context_blocks(runtime_context_files)
    for warning in prepared_warnings:
        if warning not in runtime_context_warnings:
            runtime_context_warnings.append(warning)

    source_materials = {
        "account": account,
        "mode": mode,
        "quote_enabled": quote_enabled,
        "quote_theme_selected": selected_quote_theme,
        "story_library_path": resolve_persona_story_contexts()[0] if resolve_persona_story_contexts() else "",
        "persona_story_cards": selected_story_cards,
        "story_selection_plan": story_selection_plan,
        "benchmark_contexts": benchmark_contexts,
    }
    return source_materials, runtime_context_files_used, runtime_context_warnings, runtime_context_prompt


def _build_wechat_source_pack_prompt(
    *,
    skill: SkillDefinition,
    skill_content: str,
    platform: str,
    date_str: str,
    brief: str,
    context_prompt: str,
    source_materials: dict[str, Any],
) -> str:
    return (
        "你是公众号四账号内容生产流水线的共享分析阶段。\n"
        "【WECHAT_STAGE】source_pack\n"
        "目标：先把 Brief、对标分析、金句库、框架库、自有案例、人设故事整理成结构化 Source Pack。\n"
        "只输出一个 JSON 对象，不要输出 markdown，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "source_pack": {\n'
        '    "topic": "string",\n'
        '    "target_audience": "string",\n'
        '    "core_conflict": "string",\n'
        '    "cta_keyword": "string",\n'
        '    "benchmark_structure_template": ["string"],\n'
        '    "benchmark_viewpoints": ["string"],\n'
        '    "benchmark_candidates_source": ["string"],\n'
        '    "benchmark_quote_candidates": [{"text": "string", "usage": "hook|view|warning", "rewrite_hint": "string"}],\n'
        '    "benchmark_case_slots": ["string"],\n'
        '    "quote_theme_selected": "string",\n'
        '    "quote_candidates_source": ["string"],\n'
        '    "quote_candidates": [{"text": "string", "usage": "hook|view|warning", "rewrite_hint": "string"}],\n'
        '    "framework_choice": {"name": "string", "reason": "string", "skeleton": ["string"]},\n'
        '    "story_candidates_source": ["string"],\n'
        '    "persona_story_cards": [{"story_id": "string", "故事标题": "string", "事件": "string", "可支撑观点": ["string"], "适用内容类型": ["string"], "可改写金句": ["string"]}],\n'
        '    "story_selection_plan": {"enabled": true, "usage": "string", "max_story_count": 1, "placement": ["string"], "selected_story_ids": ["string"], "selection_reason": "string"},\n'
        '    "self_case_pool": ["string"],\n'
        '    "data_anchors": ["string"],\n'
        '    "must_cover_points": ["string"],\n'
        '    "anti_repetition_rules": ["string"]\n'
        "  }\n"
        "}\n\n"
        f"目标平台：{platform}\n"
        f"目标日期：{date_str}\n"
        f"【系统预提取素材】\n{json.dumps(source_materials, ensure_ascii=False, indent=2)}\n\n"
        f"{context_prompt}"
        f"【技能文档】\n{skill.path.as_posix()}\n\n"
        f"【技能规则】\n{skill_content}\n\n"
        f"【用户Brief】\n{brief.strip()}\n"
    )


def _build_wechat_strategy_matrix_prompt(
    *,
    skill_content: str,
    brief: str,
    source_pack: dict[str, Any],
) -> str:
    source_text = json.dumps(source_pack, ensure_ascii=False, indent=2)
    return (
        "你是公众号四账号内容生产流水线的策略分发阶段。\n"
        "【WECHAT_STAGE】strategy_matrix\n"
        "目标：基于同一份 Source Pack，为 gongchang/ipgc/zengzhang/shizhan 分配清晰且差异化的生成策略。\n"
        "只输出一个 JSON 对象，不要输出 markdown，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "accounts": {\n'
        '    "gongchang": {"positioning": "string", "title_formula": "string", "core_angle": "string", "tone": "string", "must_use_points": ["string"], "case_plan": ["string"], "story_usage": "string", "story_guardrails": ["string"], "structure_notes": ["string"], "avoid_overlap": ["string"], "image_style_hint": "string"},\n'
        '    "ipgc": {"positioning": "string", "title_formula": "string", "core_angle": "string", "tone": "string", "must_use_points": ["string"], "case_plan": ["string"], "story_usage": "string", "story_guardrails": ["string"], "structure_notes": ["string"], "avoid_overlap": ["string"], "image_style_hint": "string"},\n'
        '    "zengzhang": {"positioning": "string", "title_formula": "string", "core_angle": "string", "tone": "string", "must_use_points": ["string"], "case_plan": ["string"], "story_usage": "string", "story_guardrails": ["string"], "structure_notes": ["string"], "avoid_overlap": ["string"], "image_style_hint": "string"},\n'
        '    "shizhan": {"positioning": "string", "title_formula": "string", "core_angle": "string", "tone": "string", "must_use_points": ["string"], "case_plan": ["string"], "story_usage": "string", "story_guardrails": ["string"], "structure_notes": ["string"], "avoid_overlap": ["string"], "image_style_hint": "string"}\n'
        "  }\n"
        "}\n\n"
        f"【技能规则】\n{skill_content}\n\n"
        f"【用户Brief】\n{brief.strip()}\n\n"
        f"【Source Pack】\n{source_text}\n"
    )


def _build_wechat_titles_prompt(
    *,
    account: str,
    brief: str,
    source_pack: dict[str, Any],
    strategy: dict[str, Any],
) -> str:
    return (
        "你是公众号四账号内容生产流水线的账号标题阶段。\n"
        "【WECHAT_STAGE】account_titles\n"
        f"【ACCOUNT】{account}\n"
        "只输出一个 JSON 对象，不要输出 markdown，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "account": "string",\n'
        '  "primary_title": "string",\n'
        '  "alt_titles": ["string", "string"],\n'
        '  "title_formula": "string",\n'
        '  "angle_summary": "string"\n'
        "}\n\n"
        f"【用户Brief】\n{brief.strip()}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack, ensure_ascii=False, indent=2)}\n\n"
        f"【账号策略】\n{json.dumps(strategy, ensure_ascii=False, indent=2)}\n"
    )


def _build_wechat_outline_prompt(
    *,
    account: str,
    brief: str,
    source_pack: dict[str, Any],
    strategy: dict[str, Any],
    titles: dict[str, Any],
) -> str:
    return (
        "你是公众号四账号内容生产流水线的账号大纲阶段。\n"
        "【WECHAT_STAGE】account_outline\n"
        f"【ACCOUNT】{account}\n"
        "只输出一个 JSON 对象，不要输出 markdown，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "account": "string",\n'
        '  "headline": "string",\n'
        '  "short_slug": "string",\n'
        '  "summary": "string",\n'
        '  "sections": [{"heading": "string", "goal": "string", "must_include": ["string"], "case_anchor": "string", "rebuttal_safety_valve": "string", "screenshot_quote": "string"}],\n'
        '  "cta": "string"\n'
        "}\n\n"
        f"【用户Brief】\n{brief.strip()}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack, ensure_ascii=False, indent=2)}\n\n"
        f"【账号策略】\n{json.dumps(strategy, ensure_ascii=False, indent=2)}\n\n"
        f"【标题方案】\n{json.dumps(titles, ensure_ascii=False, indent=2)}\n"
    )


def _build_wechat_draft_prompt(
    *,
    account: str,
    date_str: str,
    brief: str,
    source_pack: dict[str, Any],
    strategy: dict[str, Any],
    titles: dict[str, Any],
    outline: dict[str, Any],
    qc_feedback: list[str] | None = None,
) -> str:
    rewrite_text = ""
    if qc_feedback:
        rewrite_text = "【上一次质检反馈】\n" + "\n".join(f"- {item}" for item in qc_feedback if str(item or "").strip()) + "\n\n"
    return (
        "你是公众号四账号内容生产流水线的账号正文阶段。\n"
        "【WECHAT_STAGE】account_draft\n"
        f"【ACCOUNT】{account}\n"
        "只输出一篇完整 markdown，不要输出 FILES_JSON，不要加代码块，不要解释。\n"
        "硬性要求：\n"
        "1) 必须包含 YAML frontmatter；\n"
        "2) frontmatter 至少包含 账号、日期、选题、标题公式、配图风格、summary；\n"
        "3) 正文必须包含 ## 标题、## 正文、## CTA、## 配图提示词；\n"
        "4) 配图提示词至少包含 1 张封面图和与章节数相同的正文图；\n"
        "5) 语气、人设、案例角度必须服从该账号策略，不能和其他账号混同；\n"
        "6) 如 Source Pack/账号策略分配了人设故事，只能用 1 个主故事（必要时 1 个辅故事），且只能用于开头立人设/关键案例/结尾回扣；\n"
        "7) 如果提供了质检反馈，必须只修正被指出的问题，不要丢结构。\n\n"
        f"【目标日期】\n{date_str}\n\n"
        f"【用户Brief】\n{brief.strip()}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack, ensure_ascii=False, indent=2)}\n\n"
        f"【账号策略】\n{json.dumps(strategy, ensure_ascii=False, indent=2)}\n\n"
        f"【标题方案】\n{json.dumps(titles, ensure_ascii=False, indent=2)}\n\n"
        f"【账号大纲】\n{json.dumps(outline, ensure_ascii=False, indent=2)}\n\n"
        f"{rewrite_text}"
    )


def _build_wechat_qc_prompt(
    *,
    account: str,
    brief: str,
    source_pack: dict[str, Any],
    strategy: dict[str, Any],
    markdown_text: str,
) -> str:
    return (
        "你是公众号四账号内容生产流水线的质检阶段。\n"
        "【WECHAT_STAGE】account_qc\n"
        f"【ACCOUNT】{account}\n"
        "只输出一个 JSON 对象，不要输出 markdown，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "account": "string",\n'
        '  "passed": true,\n'
        '  "issues": ["string"],\n'
        '  "rewrite_brief": ["string"],\n'
        '  "checks": {\n'
        '    "quote_rewritten": true,\n'
        '    "case_anchor_count": 0,\n'
        '    "story_anchor_used": true,\n'
        '    "story_traceable": true,\n'
        '    "screenshot_quote_count": 0,\n'
        '    "rebuttal_safety_valve_count": 0,\n'
        '    "summary_ready": true,\n'
        '    "cta_ready": true,\n'
        '    "quote_sources_used": ["string"],\n'
        '    "benchmark_elements_used": ["string"],\n'
        '    "persona_story_ids_used": ["string"],\n'
        '    "case_anchors_used": ["string"],\n'
        '    "source_usage_notes": ["string"]\n'
        "  }\n"
        "}\n\n"
        "放行标准：至少 1 处高价值表达来自金句库或对标分析并完成改写、至少 2 个案例/数据/场景锚点、至少 1 处可追溯到人设故事库的证据或故事变体、至少 1 句可截图金句、至少 2 处反驳安全阀、summary 可直接发布、CTA 明确。必须在 quote_sources_used / benchmark_elements_used / persona_story_ids_used / case_anchors_used 里写出真实消费证据；若开启了相关来源却没有真实消费，视为不通过。\n\n"
        f"【用户Brief】\n{brief.strip()}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack, ensure_ascii=False, indent=2)}\n\n"
        f"【账号策略】\n{json.dumps(strategy, ensure_ascii=False, indent=2)}\n\n"
        f"【待质检正文】\n{markdown_text.strip()}\n"
    )


def _build_xhs_source_pack_prompt(
    *,
    skill: SkillDefinition,
    skill_content: str,
    platform: str,
    date_str: str,
    brief: str,
    context_prompt: str,
    source_materials: dict[str, Any],
) -> str:
    return (
        "你是小红书内容生成流水线的共享分析阶段。\n"
        "【XHS_STAGE】source_pack\n"
        "目标：把 Brief、对标分析、金句候选、模板库、自有案例、人设故事整理成结构化 Source Pack。\n"
        "只输出一个 JSON 对象，不要输出 markdown，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "source_pack": {\n'
        '    "topic": "string",\n'
        '    "target_audience": "string",\n'
        '    "core_conflict": "string",\n'
        '    "goal": "string",\n'
        '    "mode": "信息图6页|情绪冲突文字帖",\n'
        '    "account": "A|B|C",\n'
        '    "benchmark_template": ["string"],\n'
        '    "benchmark_viewpoints": ["string"],\n'
        '    "benchmark_case_slots": ["string"],\n'
        '    "quote_theme_selected": "string",\n'
        '    "quote_candidates": [{"text": "string", "usage": "hook|view|warning", "rewrite_hint": "string"}],\n'
        '    "framework_choice": {"name": "string", "reason": "string", "skeleton": ["string"]},\n'
        '    "persona_story_cards": [{"story_id": "string", "故事标题": "string", "事件": "string", "可支撑观点": ["string"], "适用内容类型": ["string"], "可改写金句": ["string"]}],\n'
        '    "story_selection_plan": {"enabled": true, "usage": "string", "selected_story_ids": ["string"], "selection_reason": "string"},\n'
        '    "self_case_pool": ["string"],\n'
        '    "data_anchors": ["string"],\n'
        '    "audience_tensions": ["string"],\n'
        '    "must_cover_points": ["string"]\n'
        "  }\n"
        "}\n\n"
        f"目标平台：{platform}\n"
        f"目标日期：{date_str}\n"
        f"【系统预提取素材】\n{json.dumps(source_materials, ensure_ascii=False, indent=2)}\n\n"
        f"{context_prompt}"
        f"【技能文档路径】\n{skill.path.as_posix()}\n\n"
        f"【技能规则】\n{skill_content}\n\n"
        f"【用户Brief】\n{brief.strip()}\n"
    )


def _build_xhs_strategy_matrix_prompt(
    *,
    skill_content: str,
    brief: str,
    source_pack: dict[str, Any],
) -> str:
    return (
        "你是小红书内容生成流水线的策略分发阶段。\n"
        "【XHS_STAGE】strategy_matrix\n"
        "目标：基于同一份 Source Pack，为当前账号/模式输出唯一的 worker 策略。\n"
        "只输出一个 JSON 对象，不要输出 markdown，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "worker": {\n'
        '    "account": "A|B|C",\n'
        '    "mode": "信息图6页|情绪冲突文字帖",\n'
        '    "content_goal": "string",\n'
        '    "title_angle": "string",\n'
        '    "must_use_points": ["string"],\n'
        '    "story_usage": "string",\n'
        '    "story_guardrails": ["string"],\n'
        '    "structure_plan": ["string"],\n'
        '    "comment_goal": "string",\n'
        '    "visual_goal": "string",\n'
        '    "avoid_overlap": ["string"]\n'
        "  }\n"
        "}\n\n"
        f"【技能规则】\n{skill_content}\n\n"
        f"【用户Brief】\n{brief.strip()}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack, ensure_ascii=False, indent=2)}\n"
    )


def _build_xhs_delivery_prompt(
    *,
    skill: SkillDefinition,
    skill_content: str,
    platform: str,
    date_str: str,
    brief: str,
    source_pack: dict[str, Any],
    worker_strategy: dict[str, Any],
    qc_feedback: list[str] | None = None,
) -> str:
    rewrite_text = ""
    if qc_feedback:
        rewrite_text = "【上一轮质检反馈】\n" + "\n".join(f"- {item}" for item in qc_feedback if str(item or "").strip()) + "\n\n"
    return (
        "你是小红书内容生成流水线的 worker 阶段。\n"
        "【XHS_STAGE】delivery\n"
        "你必须严格遵守技能文档，输出最终交付包。\n"
        "必须输出 FILES_JSON + FILE_BLOCK，多文件交付，不要输出解释。\n"
        "要求：\n"
        "1) 最终交付必须保持当前技能的原有契约；\n"
        "2) 只能消费当前 Source Pack 与 worker 策略，不要自由扩写素材来源；\n"
        "3) 如分配了人设故事，只能在开头立人设 / 中段案例 / 结尾回扣中使用，禁止整篇传记化；\n"
        "4) 配图提示词、置顶评论、发布包都必须保留。\n\n"
        f"目标平台：{platform}\n"
        f"目标日期：{date_str}\n\n"
        f"【技能文档路径】\n{skill.path.as_posix()}\n\n"
        f"【技能规则】\n{skill_content}\n\n"
        f"【用户Brief】\n{brief.strip()}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack, ensure_ascii=False, indent=2)}\n\n"
        f"【Worker Strategy】\n{json.dumps(worker_strategy, ensure_ascii=False, indent=2)}\n\n"
        f"{rewrite_text}"
    )


def _build_xhs_qc_prompt(
    *,
    brief: str,
    source_pack: dict[str, Any],
    worker_strategy: dict[str, Any],
    output_text: str,
) -> str:
    return (
        "你是小红书内容生成流水线的质检阶段。\n"
        "【XHS_STAGE】qc\n"
        "只输出一个 JSON 对象，不要输出 markdown，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "passed": true,\n'
        '  "issues": ["string"],\n'
        '  "rewrite_brief": ["string"],\n'
        '  "checks": {\n'
        '    "story_anchor_used": true,\n'
        '    "story_traceable": true,\n'
        '    "quote_rewritten": true,\n'
        '    "case_anchor_count": 0,\n'
        '    "hook_ready": true,\n'
        '    "pinned_comment_ready": true,\n'
        '    "prompt_contract_ready": true,\n'
        '    "source_usage_notes": ["string"]\n'
        "  }\n"
        "}\n\n"
        "放行标准：至少 1 处故事或人设锚点真实落地、至少 1 处改写金句或高价值表达、至少 2 个场景/案例锚点、结构与当前模式匹配、置顶评论/配图提示词/发布包齐全。\n\n"
        f"【用户Brief】\n{brief.strip()}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack, ensure_ascii=False, indent=2)}\n\n"
        f"【Worker Strategy】\n{json.dumps(worker_strategy, ensure_ascii=False, indent=2)}\n\n"
        f"【待质检输出】\n{output_text.strip()}\n"
    )

def _normalize_wechat_stage_dict(stage_name: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"wechat stage {stage_name} returned non-object payload")
    return payload


def _build_wechat_output_path(account: str, date_str: str, markdown_text: str) -> str:
    short_title = _sanitize_segment(_extract_short_title(markdown_text, fallback=account), fallback=account)[:24]
    compact_date = str(date_str or _today()).replace("-", "")
    return f"生成内容/{date_str}/{account}-{compact_date}-{short_title}.md"


def _render_wechat_file_contract(
    *,
    date_str: str,
    account_docs: list[dict[str, Any]],
) -> str:
    files_meta = [{"path": str(item.get("path") or "").strip()} for item in account_docs if str(item.get("path") or "").strip()]
    parts = [
        FILE_JSON_START,
        json.dumps({"date": date_str, "files": files_meta}, ensure_ascii=False),
        FILE_JSON_END,
    ]
    for item in account_docs:
        path = str(item.get("path") or "").strip()
        content = str(item.get("content") or "").rstrip()
        if not path or not content:
            continue
        parts.extend([FILE_BLOCK_START, path, content, FILE_BLOCK_END])
    return "\n".join(parts).rstrip() + "\n"


def _cross_account_wechat_report(account_docs: list[dict[str, Any]]) -> dict[str, Any]:
    title_prefixes: dict[str, list[str]] = {}
    section_signatures: dict[str, list[str]] = {}
    warnings: list[str] = []
    for item in account_docs:
        account = str(item.get("account") or "").strip()
        title = _extract_short_title(str(item.get("content") or ""), fallback=account)
        prefix = re.sub(r"\s+", "", title)[:4]
        if prefix:
            title_prefixes.setdefault(prefix, []).append(account)
        headings = re.findall(r"(?m)^###\s+(.+?)\s*$", str(item.get("content") or ""))
        signature = "|".join(str(h or "").strip() for h in headings[:3] if str(h or "").strip())
        if signature:
            section_signatures.setdefault(signature, []).append(account)

    for prefix, accounts in title_prefixes.items():
        if len(accounts) > 1:
            warnings.append(f"title prefix duplicated for {prefix}: {', '.join(accounts)}")
    for signature, accounts in section_signatures.items():
        if len(accounts) > 1:
            warnings.append(f"section structure duplicated: {', '.join(accounts)}")

    return {
        "title_prefixes": title_prefixes,
        "section_signatures": section_signatures,
        "warnings": warnings,
    }


def _run_wechat_account_worker(
    *,
    account: str,
    brief: str,
    date_str: str,
    source_pack: dict[str, Any],
    strategy: dict[str, Any],
    model: str,
    codex_cli: str,
    timeout_sec: int,
) -> dict[str, Any]:
    titles_payload, _titles_raw, _titles_stderr = _run_codex_json_stage(
        stage_name=f"{account}:titles",
        prompt=_build_wechat_titles_prompt(
            account=account,
            brief=brief,
            source_pack=source_pack,
            strategy=strategy,
        ),
        model=model,
        codex_cli=codex_cli,
        timeout_sec=timeout_sec,
    )
    titles = _normalize_wechat_stage_dict("account_titles", titles_payload)

    outline_payload, _outline_raw, _outline_stderr = _run_codex_json_stage(
        stage_name=f"{account}:outline",
        prompt=_build_wechat_outline_prompt(
            account=account,
            brief=brief,
            source_pack=source_pack,
            strategy=strategy,
            titles=titles,
        ),
        model=model,
        codex_cli=codex_cli,
        timeout_sec=timeout_sec,
    )
    outline = _normalize_wechat_stage_dict("account_outline", outline_payload)

    draft_text, _draft_stderr = _run_codex(
        _build_wechat_draft_prompt(
            account=account,
            date_str=date_str,
            brief=brief,
            source_pack=source_pack,
            strategy=strategy,
            titles=titles,
            outline=outline,
        ),
        model=model,
        codex_cli=codex_cli,
        timeout_sec=timeout_sec,
    )
    draft_text = str(draft_text or "").strip()
    if not draft_text:
        raise RuntimeError(f"{account} draft stage returned empty markdown")

    qc_payload, _qc_raw, _qc_stderr = _run_codex_json_stage(
        stage_name=f"{account}:qc",
        prompt=_build_wechat_qc_prompt(
            account=account,
            brief=brief,
            source_pack=source_pack,
            strategy=strategy,
            markdown_text=draft_text,
        ),
        model=model,
        codex_cli=codex_cli,
        timeout_sec=timeout_sec,
    )
    qc = _normalize_wechat_stage_dict("account_qc", qc_payload)
    passed = bool(qc.get("passed"))
    retry_used = False
    if not passed:
        retry_feedback = [str(item or "").strip() for item in qc.get("rewrite_brief") or [] if str(item or "").strip()]
        draft_text, _draft_retry_stderr = _run_codex(
            _build_wechat_draft_prompt(
                account=account,
                date_str=date_str,
                brief=brief,
                source_pack=source_pack,
                strategy=strategy,
                titles=titles,
                outline=outline,
                qc_feedback=retry_feedback,
            ),
            model=model,
            codex_cli=codex_cli,
            timeout_sec=timeout_sec,
        )
        draft_text = str(draft_text or "").strip()
        retry_used = True
        qc_retry_payload, _qc_retry_raw, _qc_retry_stderr = _run_codex_json_stage(
            stage_name=f"{account}:qc_retry",
            prompt=_build_wechat_qc_prompt(
                account=account,
                brief=brief,
                source_pack=source_pack,
                strategy=strategy,
                markdown_text=draft_text,
            ),
            model=model,
            codex_cli=codex_cli,
            timeout_sec=timeout_sec,
        )
        qc = _normalize_wechat_stage_dict("account_qc", qc_retry_payload)
        passed = bool(qc.get("passed"))
    if not passed:
        issues = [str(item or "").strip() for item in qc.get("issues") or [] if str(item or "").strip()]
        raise RuntimeError("; ".join(issues) or f"{account} QC failed")

    return {
        "status": "success",
        "account": account,
        "path": _build_wechat_output_path(account, date_str, draft_text),
        "content": draft_text,
        "titles": titles,
        "outline": outline,
        "qc": qc,
        "retry_used": retry_used,
    }


def _run_wechat_staged_task(
    *,
    skill: SkillDefinition,
    brief: str,
    platform: str,
    date_str: str,
    model: str,
    event_ref: str,
    source_ref: str,
    timeout_sec: int,
    codex_cli: str,
    context_files_used: list[str],
    context_plan: dict[str, Any],
    context_warnings: list[str],
    context_errors: list[str],
    context_prompt: str,
    started: float,
    dry_run: bool,
    concurrency: int,
) -> dict[str, Any]:
    resolved_concurrency = _resolve_wechat_content_concurrency(concurrency)
    account_workers = [{"account": account, "stages": ["titles", "outline", "draft", "qc", "retry_once"]} for account in WECHAT_ACCOUNT_ORDER]
    source_materials, runtime_context_files_used, runtime_context_warnings, runtime_context_prompt = _extract_wechat_source_materials(
        brief=brief,
        date_str=date_str,
        event_ref=event_ref,
        dry_run=dry_run,
        base_context_files=context_files_used,
        base_context_warnings=context_warnings,
    )
    merged_runtime_contexts = list(
        dict.fromkeys([*(context_plan.get("context_files_merged") or []), *runtime_context_files_used])
    )
    if dry_run:
        elapsed = int((time.time() - started) * 1000)
        status = "error" if context_errors else "success"
        return {
            "status": status,
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "platform": platform,
            "date": date_str,
            "model": model or DEFAULT_MODEL,
            "event_ref": event_ref,
            "source_ref": source_ref,
            "context_files_used": runtime_context_files_used,
            "context_files_auto": list(context_plan.get("context_files_auto") or []),
            "context_files_merged": merged_runtime_contexts,
            "context_warnings": runtime_context_warnings,
            "context_errors": context_errors,
            "saved_files": [],
            "full_text": "",
            "stderr": "",
            "elapsed_ms": elapsed,
            "errors": list(context_errors),
            "dry_run": True,
            "pipeline_mode": "staged_parallel",
            "concurrency": resolved_concurrency,
            "shared_stages": list(WECHAT_SHARED_STAGES),
            "account_workers": account_workers,
            "source_materials": source_materials,
        }

    if context_errors:
        elapsed = int((time.time() - started) * 1000)
        return {
            "status": "error",
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "platform": platform,
            "date": date_str,
            "model": model or DEFAULT_MODEL,
            "event_ref": event_ref,
            "source_ref": source_ref,
            "context_files_used": runtime_context_files_used,
            "context_files_auto": list(context_plan.get("context_files_auto") or []),
            "context_files_merged": merged_runtime_contexts,
            "context_warnings": runtime_context_warnings,
            "context_errors": context_errors,
            "saved_files": [],
            "full_text": "",
            "stderr": "",
            "elapsed_ms": elapsed,
            "errors": list(context_errors),
            "pipeline_mode": "staged_parallel",
            "concurrency": resolved_concurrency,
            "shared_stages": list(WECHAT_SHARED_STAGES),
            "account_workers": account_workers,
            "source_materials": source_materials,
        }

    cli_path = codex_cli.strip() or resolve_codex_cli()
    task_id = _wechat_generation_task_id(date_str)
    report_dir = _wechat_generation_report_dir(date_str, task_id)
    skill_content = _read_text(skill.path)

    source_pack_payload, source_pack_raw, _source_stderr = _run_codex_json_stage(
        stage_name="source_pack",
        prompt=_build_wechat_source_pack_prompt(
            skill=skill,
            skill_content=skill_content,
            platform=platform,
            date_str=date_str,
            brief=brief,
            context_prompt=runtime_context_prompt,
            source_materials=source_materials,
        ),
        model=model or DEFAULT_MODEL,
        codex_cli=cli_path,
        timeout_sec=timeout_sec,
    )
    source_pack_root = _normalize_wechat_stage_dict("source_pack", source_pack_payload)
    source_pack = source_pack_root.get("source_pack")
    if not isinstance(source_pack, dict):
        raise RuntimeError("wechat source_pack stage returned missing source_pack object")

    strategy_payload, strategy_raw, _strategy_stderr = _run_codex_json_stage(
        stage_name="strategy_matrix",
        prompt=_build_wechat_strategy_matrix_prompt(
            skill_content=skill_content,
            brief=brief,
            source_pack=source_pack,
        ),
        model=model or DEFAULT_MODEL,
        codex_cli=cli_path,
        timeout_sec=timeout_sec,
    )
    strategy_root = _normalize_wechat_stage_dict("strategy_matrix", strategy_payload)
    strategy_accounts = strategy_root.get("accounts")
    if not isinstance(strategy_accounts, dict):
        raise RuntimeError("wechat strategy_matrix stage returned missing accounts object")

    _write_json_file(report_dir / "source-materials.json", source_materials)
    _write_json_file(report_dir / "source-pack.json", source_pack_root)
    _write_text_file(report_dir / "source-pack.raw.txt", source_pack_raw)
    _write_json_file(report_dir / "strategy-matrix.json", strategy_root)
    _write_text_file(report_dir / "strategy-matrix.raw.txt", strategy_raw)

    account_results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=resolved_concurrency) as executor:
        future_map = {
            executor.submit(
                _run_wechat_account_worker,
                account=account,
                brief=brief,
                date_str=date_str,
                source_pack=source_pack,
                strategy=dict(strategy_accounts.get(account) or {}),
                model=model or DEFAULT_MODEL,
                codex_cli=cli_path,
                timeout_sec=timeout_sec,
            ): account
            for account in WECHAT_ACCOUNT_ORDER
        }
        for future in as_completed(future_map):
            account = future_map[future]
            try:
                account_results[account] = future.result()
            except Exception as exc:
                account_results[account] = {
                    "status": "error",
                    "account": account,
                    "error": str(exc),
                }

    ordered_successes: list[dict[str, Any]] = []
    ordered_results: list[dict[str, Any]] = []
    errors: list[str] = []
    qc_report: dict[str, Any] = {}
    for account in WECHAT_ACCOUNT_ORDER:
        result = account_results.get(account) or {"status": "error", "account": account, "error": "missing account result"}
        ordered_results.append(result)
        if str(result.get("status") or "") == "success":
            ordered_successes.append(result)
            qc_report[account] = result.get("qc") or {}
        else:
            errors.append(f"{account}: {str(result.get('error') or 'worker failed').strip()}")

    shared_stage_report = {
        "task_id": task_id,
        "pipeline_mode": "staged_parallel",
        "concurrency": resolved_concurrency,
        "shared_stages": list(WECHAT_SHARED_STAGES),
        "source_materials": source_materials,
        "cross_account": _cross_account_wechat_report(ordered_successes),
    }
    _write_json_file(report_dir / "qc-report.json", qc_report)
    _write_json_file(report_dir / "shared-stage-report.json", shared_stage_report)

    saved_files: list[str] = []
    full_text = ""
    if ordered_successes:
        contract_text = _render_wechat_file_contract(
            date_str=date_str,
            account_docs=ordered_successes,
        )
        _write_text_file(report_dir / "wechat-files-contract.txt", contract_text)
        saved_files = _save_generated_files(
            text=contract_text,
            skill=skill,
            platform=platform,
            date_str=date_str,
        )
        full_text = _read_primary_saved_file(saved_files) or _render_full_text_for_reply(contract_text)

    status = "success"
    if errors and ordered_successes:
        status = "partial_error"
    elif errors:
        status = "error"

    elapsed = int((time.time() - started) * 1000)
    try:
        report_dir_text = report_dir.relative_to(REPO_ROOT).as_posix()
    except Exception:
        report_dir_text = str(report_dir)
    return {
        "status": status,
        "skill_id": skill.skill_id,
        "skill_name": skill.name,
        "skill_path": skill.path.as_posix(),
        "platform": platform,
        "date": date_str,
        "model": model or DEFAULT_MODEL,
        "event_ref": event_ref,
        "source_ref": source_ref,
        "context_files_used": runtime_context_files_used,
        "context_files_auto": list(context_plan.get("context_files_auto") or []),
        "context_files_merged": merged_runtime_contexts,
        "context_warnings": runtime_context_warnings,
        "context_errors": context_errors,
        "saved_files": saved_files,
        "full_text": full_text,
        "stderr": "",
        "elapsed_ms": elapsed,
        "errors": errors,
        "pipeline_mode": "staged_parallel",
        "concurrency": resolved_concurrency,
        "shared_stages": list(WECHAT_SHARED_STAGES),
        "account_workers": account_workers,
        "account_results": ordered_results,
        "shared_stage_report": shared_stage_report,
        "report_dir": report_dir_text,
        "source_materials": source_materials,
    }


def _xhs_generation_task_id(date_str: str) -> str:
    return f"xhs-gen-{str(date_str or _today()).replace('-', '')}-{dt.datetime.now().strftime('%H%M%S')}"


def _xhs_generation_report_dir(date_str: str, task_id: str) -> Path:
    root = REPO_ROOT / "reports" / "xhs-generation" / str(date_str or _today()) / task_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run_xhs_worker(
    *,
    skill: SkillDefinition,
    skill_content: str,
    platform: str,
    date_str: str,
    brief: str,
    source_pack: dict[str, Any],
    worker_strategy: dict[str, Any],
    model: str,
    codex_cli: str,
    timeout_sec: int,
) -> dict[str, Any]:
    output_text, _stderr = _run_codex(
        _build_xhs_delivery_prompt(
            skill=skill,
            skill_content=skill_content,
            platform=platform,
            date_str=date_str,
            brief=brief,
            source_pack=source_pack,
            worker_strategy=worker_strategy,
        ),
        model=model,
        codex_cli=codex_cli,
        timeout_sec=timeout_sec,
    )
    qc_payload, _qc_raw, _qc_stderr = _run_codex_json_stage(
        stage_name="xhs:qc",
        prompt=_build_xhs_qc_prompt(
            brief=brief,
            source_pack=source_pack,
            worker_strategy=worker_strategy,
            output_text=output_text,
        ),
        model=model,
        codex_cli=codex_cli,
        timeout_sec=timeout_sec,
    )
    qc = _normalize_wechat_stage_dict("xhs_qc", qc_payload)
    passed = bool(qc.get("passed"))
    retry_used = False
    if not passed:
        retry_feedback = [str(item or "").strip() for item in qc.get("rewrite_brief") or [] if str(item or "").strip()]
        output_text, _retry_stderr = _run_codex(
            _build_xhs_delivery_prompt(
                skill=skill,
                skill_content=skill_content,
                platform=platform,
                date_str=date_str,
                brief=brief,
                source_pack=source_pack,
                worker_strategy=worker_strategy,
                qc_feedback=retry_feedback,
            ),
            model=model,
            codex_cli=codex_cli,
            timeout_sec=timeout_sec,
        )
        retry_used = True
        qc_payload, _qc_raw, _qc_stderr = _run_codex_json_stage(
            stage_name="xhs:qc_retry",
            prompt=_build_xhs_qc_prompt(
                brief=brief,
                source_pack=source_pack,
                worker_strategy=worker_strategy,
                output_text=output_text,
            ),
            model=model,
            codex_cli=codex_cli,
            timeout_sec=timeout_sec,
        )
        qc = _normalize_wechat_stage_dict("xhs_qc", qc_payload)
        passed = bool(qc.get("passed"))
    if not passed:
        issues = [str(item or "").strip() for item in qc.get("issues") or [] if str(item or "").strip()]
        raise RuntimeError("; ".join(issues) or "xhs QC failed")
    return {
        "status": "success",
        "output_text": str(output_text or "").strip(),
        "qc": qc,
        "retry_used": retry_used,
    }


def _run_xhs_staged_task(
    *,
    skill: SkillDefinition,
    brief: str,
    platform: str,
    date_str: str,
    model: str,
    event_ref: str,
    source_ref: str,
    timeout_sec: int,
    codex_cli: str,
    context_files_used: list[str],
    context_plan: dict[str, Any],
    context_warnings: list[str],
    context_errors: list[str],
    context_prompt: str,
    started: float,
    dry_run: bool,
) -> dict[str, Any]:
    source_materials, runtime_context_files_used, runtime_context_warnings, runtime_context_prompt = _extract_xhs_source_materials(
        brief=brief,
        date_str=date_str,
        event_ref=event_ref,
        dry_run=dry_run,
        base_context_files=context_files_used,
        base_context_warnings=context_warnings,
    )
    merged_runtime_contexts = list(
        dict.fromkeys([*(context_plan.get("context_files_merged") or []), *runtime_context_files_used])
    )
    worker_plan = {
        "account": source_materials.get("account") or "A",
        "mode": source_materials.get("mode") or "信息图6页",
        "stages": ["delivery", "qc", "retry_once"],
    }
    if dry_run:
        elapsed = int((time.time() - started) * 1000)
        status = "error" if context_errors else "success"
        return {
            "status": status,
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "platform": platform,
            "date": date_str,
            "model": model or DEFAULT_MODEL,
            "event_ref": event_ref,
            "source_ref": source_ref,
            "context_files_used": runtime_context_files_used,
            "context_files_auto": list(context_plan.get("context_files_auto") or []),
            "context_files_merged": merged_runtime_contexts,
            "context_warnings": runtime_context_warnings,
            "context_errors": context_errors,
            "saved_files": [],
            "full_text": "",
            "stderr": "",
            "elapsed_ms": elapsed,
            "errors": list(context_errors),
            "dry_run": True,
            "pipeline_mode": "staged_single_worker",
            "shared_stages": list(XHS_SHARED_STAGES),
            "worker_plan": worker_plan,
            "source_materials": source_materials,
        }
    if context_errors:
        elapsed = int((time.time() - started) * 1000)
        return {
            "status": "error",
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "platform": platform,
            "date": date_str,
            "model": model or DEFAULT_MODEL,
            "event_ref": event_ref,
            "source_ref": source_ref,
            "context_files_used": runtime_context_files_used,
            "context_files_auto": list(context_plan.get("context_files_auto") or []),
            "context_files_merged": merged_runtime_contexts,
            "context_warnings": runtime_context_warnings,
            "context_errors": context_errors,
            "saved_files": [],
            "full_text": "",
            "stderr": "",
            "elapsed_ms": elapsed,
            "errors": list(context_errors),
            "pipeline_mode": "staged_single_worker",
            "shared_stages": list(XHS_SHARED_STAGES),
            "worker_plan": worker_plan,
            "source_materials": source_materials,
        }

    cli_path = codex_cli.strip() or resolve_codex_cli()
    task_id = _xhs_generation_task_id(date_str)
    report_dir = _xhs_generation_report_dir(date_str, task_id)
    skill_content = _read_text(skill.path)

    source_pack_payload, source_pack_raw, _source_stderr = _run_codex_json_stage(
        stage_name="xhs:source_pack",
        prompt=_build_xhs_source_pack_prompt(
            skill=skill,
            skill_content=skill_content,
            platform=platform,
            date_str=date_str,
            brief=brief,
            context_prompt=runtime_context_prompt,
            source_materials=source_materials,
        ),
        model=model or DEFAULT_MODEL,
        codex_cli=cli_path,
        timeout_sec=timeout_sec,
    )
    source_pack_root = _normalize_wechat_stage_dict("xhs_source_pack", source_pack_payload)
    source_pack = source_pack_root.get("source_pack")
    if not isinstance(source_pack, dict):
        raise RuntimeError("xhs source_pack stage returned missing source_pack object")

    strategy_payload, strategy_raw, _strategy_stderr = _run_codex_json_stage(
        stage_name="xhs:strategy_matrix",
        prompt=_build_xhs_strategy_matrix_prompt(
            skill_content=skill_content,
            brief=brief,
            source_pack=source_pack,
        ),
        model=model or DEFAULT_MODEL,
        codex_cli=cli_path,
        timeout_sec=timeout_sec,
    )
    strategy_root = _normalize_wechat_stage_dict("xhs_strategy_matrix", strategy_payload)
    worker_strategy = strategy_root.get("worker")
    if not isinstance(worker_strategy, dict):
        raise RuntimeError("xhs strategy_matrix stage returned missing worker object")

    _write_json_file(report_dir / "source-materials.json", source_materials)
    _write_json_file(report_dir / "source-pack.json", source_pack_root)
    _write_text_file(report_dir / "source-pack.raw.txt", source_pack_raw)
    _write_json_file(report_dir / "strategy-matrix.json", strategy_root)
    _write_text_file(report_dir / "strategy-matrix.raw.txt", strategy_raw)

    worker_result = _run_xhs_worker(
        skill=skill,
        skill_content=skill_content,
        platform=platform,
        date_str=date_str,
        brief=brief,
        source_pack=source_pack,
        worker_strategy=worker_strategy,
        model=model or DEFAULT_MODEL,
        codex_cli=cli_path,
        timeout_sec=timeout_sec,
    )
    _write_json_file(report_dir / "qc-report.json", worker_result.get("qc") or {})

    saved_files = _save_generated_files(
        text=worker_result["output_text"],
        skill=skill,
        platform=platform,
        date_str=date_str,
    )
    full_text = _read_primary_saved_file(saved_files) or _render_full_text_for_reply(worker_result["output_text"])
    elapsed = int((time.time() - started) * 1000)
    try:
        report_dir_text = report_dir.relative_to(REPO_ROOT).as_posix()
    except Exception:
        report_dir_text = str(report_dir)
    return {
        "status": "success",
        "skill_id": skill.skill_id,
        "skill_name": skill.name,
        "skill_path": skill.path.as_posix(),
        "platform": platform,
        "date": date_str,
        "model": model or DEFAULT_MODEL,
        "event_ref": event_ref,
        "source_ref": source_ref,
        "context_files_used": runtime_context_files_used,
        "context_files_auto": list(context_plan.get("context_files_auto") or []),
        "context_files_merged": merged_runtime_contexts,
        "context_warnings": runtime_context_warnings,
        "context_errors": context_errors,
        "saved_files": saved_files,
        "full_text": full_text,
        "stderr": "",
        "elapsed_ms": elapsed,
        "errors": [],
        "pipeline_mode": "staged_single_worker",
        "shared_stages": list(XHS_SHARED_STAGES),
        "worker_plan": worker_plan,
        "source_materials": source_materials,
        "qc_report": worker_result.get("qc") or {},
        "report_dir": report_dir_text,
    }


def _chunshe_generation_task_id(date_str: str) -> str:
    return f"chunshe-gen-{str(date_str or _today()).replace('-', '')}-{dt.datetime.now().strftime('%H%M%S%f')}"


def _chunshe_generation_report_dir(date_str: str, task_id: str) -> Path:
    root = REPO_ROOT / "reports" / "chunshe-generation" / str(date_str or _today()) / task_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def _normalize_chunshe_generation_mode(value: str) -> str:
    normalized = _normalize_key(value)
    alias_map = {
        "快速": "快速",
        "fast": "快速",
        "平衡": "平衡",
        "balanced": "平衡",
        "严格": "严格",
        "strict": "严格",
    }
    return alias_map.get(normalized, "平衡")


def _is_chunshe_broad_seed_keyword(seed_keyword: str) -> bool:
    normalized = _normalize_key(seed_keyword)
    if normalized in {_normalize_key("默认7题"), _normalize_key("默认七题")}:
        return True
    broad_terms = {
        _normalize_key("美容院"),
        _normalize_key("做脸"),
        _normalize_key("面部清洁"),
        _normalize_key("皮肤管理"),
        _normalize_key("毛孔"),
        _normalize_key("清洁"),
    }
    return normalized in broad_terms or len(normalized) <= 4


def _chunshe_retry_budget(mode: str) -> int:
    return {
        "快速": 0,
        "平衡": 1,
        "严格": 2,
    }.get(str(mode or "").strip(), 1)


def _extract_chunshe_runtime_config(brief: str) -> dict[str, Any]:
    explicit_topic = _brief_field(brief, "选题", "topic")
    seed_keyword = (
        _brief_field(brief, "关键词", "主关键词", "主题矿区/选题", "topic")
        or explicit_topic
        or "默认7题"
    )
    output_type = normalize_chunshe_output_type(_brief_field(brief, "输出", "output"))
    batch_count = max(1, min(7, _brief_int(brief, 1, "批量", "batch")))
    if output_type == "标题池" and batch_count < 5:
        batch_count = 7
    explicit_entry_class = normalize_chunshe_entry_class(_brief_field(brief, "意图", "entry_class"))
    entry_class = infer_chunshe_entry_class(seed_keyword, explicit_entry_class)
    return {
        "seed_keyword": seed_keyword,
        "explicit_topic": explicit_topic,
        "entry_class": entry_class,
        "explicit_entry_class": explicit_entry_class,
        "role": normalize_chunshe_role(_brief_field(brief, "角色", "role")),
        "output_type": output_type,
        "batch_count": batch_count,
        "stage_hint": _brief_field(brief, "阶段", "life_stage", "stage") or "自动",
        "target_audience": _brief_field(brief, "目标人群", "target"),
        "scene_trigger_hint": _brief_field(brief, "冲突瞬间", "scene_trigger"),
        "primary_emotion": _brief_field(brief, "主情绪", "emotion"),
        "buy_point": _brief_field(brief, "统一买点", "真实买点", "buy_point"),
        "banned_words": _brief_field(brief, "禁写", "禁用词", "ban"),
        "quote_enabled": _brief_flag_or_default(brief, False, "是否调用金句库", "调用金句库"),
        "explicit_quote_theme": _brief_field(brief, "金句主题", "quote_theme"),
        "high_boundary": is_high_boundary_keyword(seed_keyword),
        "mode": _normalize_chunshe_generation_mode(_brief_field(brief, "模式", "mode")),
    }


def _build_chunshe_manual_topic(config: dict[str, Any]) -> dict[str, Any]:
    angle_map = {
        "问题修复": "痛点确认",
        "信任怀疑": "防御拆解",
        "放松养护": "向往画面",
        "本地找店": "本地决策",
    }
    default_rules = {
        "问题修复": "不拿力度装认真",
        "信任怀疑": "说不要，话题就停",
        "放松养护": "先看状态，再决定今天做到哪一步",
        "本地找店": "流程、时长、合理预期说清楚",
    }
    seed_keyword = str(config.get("seed_keyword") or "").strip()
    explicit_topic = str(config.get("explicit_topic") or "").strip()
    entry_class = str(config.get("entry_class") or "信任怀疑").strip()
    return {
        "topic_id": "MANUAL-01",
        "seed_keyword": seed_keyword,
        "entry_class": entry_class,
        "angle_type": angle_map.get(entry_class, "防御拆解"),
        "topic_title": explicit_topic or (seed_keyword if seed_keyword and seed_keyword != "默认7题" else "先别急着决定做不做脸"),
        "scene_trigger": str(config.get("scene_trigger_hint") or "").strip() or "她站在预约页前，又想关掉了",
        "fear": str(config.get("target_audience") or "").strip() or "怕刚躺下，对面就开始把问题越说越重",
        "real_desire": str(config.get("buy_point") or "").strip() or "想找一次不被消耗的护理",
        "real_buy_point": str(config.get("buy_point") or "").strip() or "这次做脸不会让我更累",
        "store_rule_hint": default_rules.get(entry_class, "说不要，话题就停"),
        "life_stage_hint": str(config.get("stage_hint") or "").strip() or "自动",
        "ending_function": "安心感",
        "priority_score": 99,
        "source_type": "manual_topic",
        "status": "manual",
    }


def _normalize_chunshe_topic_pool(payload: Any, *, config: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[Any] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("topic_pool"), list):
            items = list(payload.get("topic_pool") or [])
        elif isinstance(payload.get("topics"), list):
            items = list(payload.get("topics") or [])
    elif isinstance(payload, list):
        items = list(payload)

    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        topic = dict(item)
        title = str(topic.get("topic_title") or "").strip()
        if not title:
            continue
        topic["topic_id"] = str(topic.get("topic_id") or "").strip() or f"TP-{idx:02d}"
        topic["seed_keyword"] = str(topic.get("seed_keyword") or config.get("seed_keyword") or "").strip()
        topic["entry_class"] = infer_chunshe_entry_class(
            str(topic.get("seed_keyword") or config.get("seed_keyword") or ""),
            str(topic.get("entry_class") or config.get("entry_class") or ""),
        )
        topic["angle_type"] = str(topic.get("angle_type") or "防御拆解").strip()
        topic["scene_trigger"] = str(topic.get("scene_trigger") or config.get("scene_trigger_hint") or "").strip()
        topic["fear"] = str(topic.get("fear") or "").strip()
        topic["real_desire"] = str(topic.get("real_desire") or topic.get("buy_point") or config.get("buy_point") or "").strip()
        topic["real_buy_point"] = str(topic.get("real_buy_point") or topic.get("real_desire") or config.get("buy_point") or "").strip()
        topic["store_rule_hint"] = str(topic.get("store_rule_hint") or "").strip() or "说不要，话题就停"
        topic["life_stage_hint"] = str(topic.get("life_stage_hint") or config.get("stage_hint") or "自动").strip()
        topic["ending_function"] = str(topic.get("ending_function") or "安心感").strip()
        try:
            topic["priority_score"] = float(topic.get("priority_score") or 0)
        except Exception:
            topic["priority_score"] = 0.0
        topic["source_type"] = str(topic.get("source_type") or "topic_planner").strip()
        normalized.append(topic)
    return normalized


def _prefer_chunshe_seed_consistent_topics(
    *,
    selected_topics: list[dict[str, Any]],
    topic_candidates: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    seed_keyword = str(config.get("seed_keyword") or "").strip()
    if not seed_keyword or seed_keyword in {"默认7题", "默认七题"}:
        return selected_topics
    target_count = max(1, int(config.get("batch_count") or 1))
    normalized_seed = _normalize_key(seed_keyword)

    def _seed_match_score(item: dict[str, Any]) -> tuple[int, float]:
        seed = _normalize_key(str(item.get("seed_keyword") or ""))
        title = _normalize_key(str(item.get("topic_title") or ""))
        exact = 0
        if seed == normalized_seed:
            exact = 3
        elif normalized_seed and (normalized_seed in seed or seed in normalized_seed):
            exact = 2
        elif normalized_seed and normalized_seed in title:
            exact = 1
        return (exact, float(item.get("priority_score") or 0))

    if len(selected_topics) >= target_count and all(_seed_match_score(item)[0] > 0 for item in selected_topics[:target_count]):
        return selected_topics

    same_seed = [dict(item) for item in topic_candidates if _seed_match_score(item)[0] > 0]
    if not same_seed:
        return selected_topics
    same_seed.sort(key=lambda item: _seed_match_score(item), reverse=True)
    replacements = same_seed[:target_count]
    if len(replacements) < target_count:
        used_ids = {_normalize_key(str(item.get("topic_id") or item.get("topic_title") or "")) for item in replacements}
        for item in selected_topics:
            key = _normalize_key(str(item.get("topic_id") or item.get("topic_title") or ""))
            if key in used_ids:
                continue
            replacements.append(item)
            used_ids.add(key)
            if len(replacements) >= target_count:
                break
    return replacements[:target_count]


def __deprecated_build_chunshe_topic_planner_prompt_v1(
    *,
    brief: str,
    config: dict[str, Any],
    context_prompt: str,
    seed_examples: list[dict[str, Any]],
    recent_history: list[dict[str, Any]],
) -> str:
    min_topics = max(5, int(config.get("batch_count") or 1))
    return (
        "你是椿舍门店专用内容系统的 topic_planner 阶段。\n"
        "任务：只做选题规划，不写正文。\n"
        "请基于关键词意图、差评痛点、门店规矩、最近已发历史，扩出一组可发选题。\n"
        "只输出一个 JSON 对象，不要输出 markdown，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "seed_keyword": "string",\n'
        '  "entry_class": "问题修复|信任怀疑|放松养护|本地找店",\n'
        '  "topic_pool": [\n'
        '    {"topic_id": "string", "topic_title": "string", "angle_type": "痛点确认|防御拆解|规矩托底|向往画面|本地决策|关系/身份翻译", "scene_trigger": "string", "fear": "string", "real_desire": "string", "real_buy_point": "string", "store_rule_hint": "string", "life_stage_hint": "string", "ending_function": "string", "priority_score": 0}\n'
        "  ],\n"
        '  "rejected_topics": [{"topic_title": "string", "reason": "string"}]\n'
        "}\n\n"
        f"要求：至少给出 {min_topics} 个候选，最多 12 个；题目要像能发的小红书题，不要写成问卷或方案。\n"
        "不要把“默认7题”原样照抄成同义句；优先让角度分散。\n\n"
        f"【运行配置】\n{json.dumps(config, ensure_ascii=False, indent=2)}\n\n"
        f"【历史高频题】\n{json.dumps(recent_history, ensure_ascii=False, indent=2)}\n\n"
        f"【首批种子题】\n{json.dumps(seed_examples, ensure_ascii=False, indent=2)}\n\n"
        f"{context_prompt}"
        f"【用户Brief】\n{brief.strip()}\n"
    )


def __deprecated_build_chunshe_draft_package_prompt_v1(
    *,
    role: str,
    output_type: str,
    brief: str,
    mode: str,
    topic: dict[str, Any],
) -> str:
    length_hint = "220-320字" if output_type == "精简发布版" else "280-420字"
    return (
        "你是椿舍门店专用内容系统的 draft_package 阶段。\n"
        "任务：一次完成策略定稿、段落安排、3个标题候选和首稿正文。\n"
        "只输出一个 JSON 对象，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "entry_class": "问题修复|信任怀疑|放松养护|本地找店",\n'
        '  "person": "string",\n'
        '  "recent_trigger": "string",\n'
        '  "fear": "string",\n'
        '  "real_buy_point": "string",\n'
        '  "store_rule_primary": "string",\n'
        '  "narrative_ratio": "2-5-3|4-3-3|1-5-4",\n'
        '  "title_candidates": ["string", "string", "string"],\n'
        '  "draft_markdown": "string"\n'
        "}\n\n"
        "draft_markdown 结构固定为：\n"
        "# 标题\n主标题\n## 备选标题\n- 备选1\n- 备选2\n# 正文\n正文\n# 置顶评论\n置顶评论\n# 回复模板\n回复模板\n\n"
        f"正文长度：{length_hint}。运行模式：{mode}。\n"
        "要求：\n"
        "- 先定人、冲突、真实买点，再落首稿。\n"
        "- 开头先写她为什么会在这个时候搜这个词，不要先讲“有用吗？有用，但……”。\n"
        "- 不要把阶段翻译直接写进正文，不出现“这个年龄段/到了这个年纪”这类台词。\n"
        "- 主规矩只出现一次，辅助细节最多一句，不把后半段写成门店制度说明。\n"
        "- 不要硬塞金句；金句阶段不在这里。\n"
        "- 标题候选要彼此有区分，不是同一句换词。\n\n"
        f"【角色】{role}\n"
        f"【输出类型】{output_type}\n\n"
        f"【已选题目】\n{json.dumps(topic, ensure_ascii=False, indent=2)}\n\n"
        f"【用户Brief】\n{brief.strip()}\n"
    )


def __deprecated_build_chunshe_polish_package_prompt_v1(
    *,
    markdown_text: str,
    topic: dict[str, Any],
    role: str,
    output_type: str,
    mode: str,
    quote_candidate: dict[str, Any] | None,
    retry_issues: list[str] | None = None,
) -> str:
    retry_block = ""
    if retry_issues:
        retry_block = f"【上轮问题】\n{json.dumps(retry_issues, ensure_ascii=False, indent=2)}\n\n"
    quote_block = "【候选结尾金句】\n无\n\n"
    if quote_candidate:
        quote_block = f"【候选结尾金句】\n{json.dumps(quote_candidate, ensure_ascii=False, indent=2)}\n\n"
    return (
        "你是椿舍门店专用内容系统的 polish_package 阶段。\n"
        "任务：在现有首稿上完成连贯修补、口语化二审和最终放行。\n"
        "只输出一个 JSON 对象，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "passed": true,\n'
        '  "issues": ["string"],\n'
        '  "removed_or_softened_claims": ["string"],\n'
        '  "final_markdown": "string"\n'
        "}\n\n"
        "要求：\n"
        "- 只修稿，不换题，不重写主买点。\n"
        "- 先修跳句、缺桥、一段塞太多任务，再做口语化。\n"
        "- 去掉解释味和提炼腔，优先用动作、时间、场景、后果接上。\n"
        "- 默认只允许在结尾或结尾前一句轻放 0-1 条金句；如果生硬，直接不用。\n"
        "- 必须保留标题结构：1个主标题 + 2个备选标题。\n"
        "- passed=true 的前提是：读起来顺、像人话、金句不抢戏。\n\n"
        f"【角色】{role}\n"
        f"【输出类型】{output_type}\n"
        f"【运行模式】{mode}\n\n"
        f"【题目】\n{json.dumps(topic, ensure_ascii=False, indent=2)}\n\n"
        f"{quote_block}"
        f"{retry_block}"
        f"【当前首稿】\n{markdown_text.strip()}\n"
    )


def _normalize_chunshe_title_candidates(topic: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in payload.get("title_candidates") or []:
        title = str(raw or "").strip()
        key = _normalize_key(title)
        if not title or not key or key in seen:
            continue
        seen.add(key)
        out.append(title)
        if len(out) >= 3:
            break
    topic_title = str(topic.get("topic_title") or "").strip()
    scene = str(topic.get("scene_trigger") or "").strip()
    rule = str(topic.get("store_rule_hint") or "").strip()
    fallbacks = [
        topic_title,
        f"{topic_title}｜{scene}" if topic_title and scene else "",
        f"{topic_title}：{rule}" if topic_title and rule else "",
    ]
    for raw in fallbacks:
        title = str(raw or "").strip(" ｜：")
        key = _normalize_key(title)
        if not title or not key or key in seen:
            continue
        seen.add(key)
        out.append(title)
        if len(out) >= 3:
            break
    while len(out) < 3 and out:
        out.append(out[-1])
    return out[:3]


def _normalize_chunshe_markdown(markdown_text: str, title_candidates: list[str]) -> str:
    sections = {
        "title": [],
        "alt_titles": [],
        "body": [],
        "comment": [],
        "reply": [],
    }
    current: str | None = None
    for raw_line in str(markdown_text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped in {"# 标题", "## 标题"}:
            current = "title"
            continue
        if stripped in {"# 标题候选", "## 标题候选", "# 备选标题", "## 备选标题"}:
            current = "alt_titles"
            continue
        if stripped in {"# 正文", "## 正文", "# 口播正文", "## 口播正文"}:
            current = "body"
            continue
        if stripped in {"# 置顶评论", "## 置顶评论"}:
            current = "comment"
            continue
        if stripped in {"# 回复模板", "## 回复模板"}:
            current = "reply"
            continue
        if current:
            sections[current].append(line)

    def _first_non_empty(lines: list[str]) -> str:
        for item in lines:
            text = str(item or "").strip()
            if text:
                return text.lstrip("- ").strip()
        return ""

    title = _first_non_empty(sections["title"]) or (title_candidates[0] if title_candidates else "")
    alt_titles: list[str] = []
    seen_alt: set[str] = {_normalize_key(title)}
    for item in sections["alt_titles"]:
        text = str(item or "").strip().lstrip("- ").strip()
        key = _normalize_key(text)
        if not text or not key or key in seen_alt:
            continue
        seen_alt.add(key)
        alt_titles.append(text)
    for item in title_candidates[1:]:
        text = str(item or "").strip()
        key = _normalize_key(text)
        if not text or not key or key in seen_alt:
            continue
        seen_alt.add(key)
        alt_titles.append(text)
        if len(alt_titles) >= 2:
            break

    body = "\n".join(sections["body"]).strip()
    comment = "\n".join(sections["comment"]).strip()
    reply = "\n".join(sections["reply"]).strip()
    parts = [
        "# 标题",
        title or "先别急着决定做不做脸",
        "",
        "## 备选标题",
        f"- {alt_titles[0] if len(alt_titles) > 0 else (title_candidates[1] if len(title_candidates) > 1 else title or '先别急着决定做不做脸')}",
        f"- {alt_titles[1] if len(alt_titles) > 1 else (title_candidates[2] if len(title_candidates) > 2 else title or '先别急着决定做不做脸')}",
        "",
        "# 正文",
        body,
        "",
        "# 置顶评论",
        comment,
        "",
        "# 回复模板",
        reply,
    ]
    return "\n".join(parts).strip() + "\n"


def __deprecated_validate_chunshe_markdown_v1(markdown_text: str) -> list[str]:
    text = str(markdown_text or "").strip()
    issues: list[str] = []
    required_sections = ("# 标题", "## 备选标题", "# 正文", "# 置顶评论", "# 回复模板")
    for section in required_sections:
        if section not in text:
            issues.append(f"缺少区块：{section}")
    if "有用吗？有用，但" in text or "值不值，先看" in text:
        issues.append("仍有问答模板味")
    if "不是……是…" in text or "更扎心的是" in text or "总的来说" in text:
        issues.append("仍有解释味或AI味")
    for marker in ("# 正文", "# 置顶评论", "# 回复模板"):
        matched = re.search(rf"(?ms){re.escape(marker)}\s*(.*?)\s*(?=^# |\Z)", text)
        if matched and not str(matched.group(1) or "").strip():
            issues.append(f"{marker} 为空")
    return issues


def _build_chunshe_output_path(
    *,
    date_str: str,
    role: str,
    output_type: str,
    topic: dict[str, Any],
    index: int = 1,
) -> str:
    title = str(topic.get("topic_title") or role).strip()
    short = _sanitize_segment(title, fallback=role)[:28]
    compact_date = str(date_str or _today()).replace("-", "")
    prefix = f"{role}-{compact_date}-{index:02d}"
    if output_type == "标题池":
        prefix = f"chunshe-{compact_date}"
    return f"生成内容/{date_str}/{prefix}-{short}.md"


def _render_chunshe_file_contract(*, date_str: str, topic_docs: list[dict[str, Any]]) -> str:
    files_meta = [{"path": str(item.get("path") or "").strip()} for item in topic_docs if str(item.get("path") or "").strip()]
    parts = [
        FILE_JSON_START,
        json.dumps({"date": date_str, "files": files_meta}, ensure_ascii=False),
        FILE_JSON_END,
    ]
    for item in topic_docs:
        path = str(item.get("path") or "").strip()
        content = str(item.get("content") or "").rstrip()
        if not path or not content:
            continue
        parts.extend([FILE_BLOCK_START, path, content, FILE_BLOCK_END])
    return "\n".join(parts).rstrip() + "\n"


def __deprecated_run_chunshe_single_topic_v1(
    *,
    role: str,
    output_type: str,
    brief: str,
    topic: dict[str, Any],
    mode: str,
    quote_enabled: bool,
    explicit_quote_theme: str,
    skill_content: str,
    context_prompt: str,
    model: str,
    codex_cli: str,
    timeout_sec: int,
    topic_report_dir: Path,
) -> dict[str, Any]:
    shared_reference = f"{context_prompt}【技能规则】\n{skill_content}\n\n"
    draft_payload, draft_raw, _draft_stderr = _run_codex_json_stage(
        stage_name="chunshe:draft_package",
        prompt=shared_reference
        + _build_chunshe_draft_package_prompt(
            role=role,
            output_type=output_type,
            brief=brief,
            mode=mode,
            topic=topic,
        ),
        model=model,
        codex_cli=codex_cli,
        timeout_sec=timeout_sec,
    )
    draft_package = _normalize_wechat_stage_dict("chunshe_draft_package", draft_payload)
    title_candidates = _normalize_chunshe_title_candidates(topic, draft_package)
    draft_text = _normalize_chunshe_markdown(str(draft_package.get("draft_markdown") or "").strip(), title_candidates)
    if not draft_text.strip():
        raise RuntimeError("chunshe draft package returned empty markdown")

    quote_candidate: dict[str, Any] | None = None
    quote_candidates: list[dict[str, Any]] = []
    if quote_enabled:
        quote_candidates = select_chunshe_quote_candidates(
            entry_class=str(topic.get("entry_class") or ""),
            topic=topic,
            explicit_theme=explicit_quote_theme,
            limit=3,
        )
        quote_candidate = quote_candidates[0] if quote_candidates else None

    retry_budget = _chunshe_retry_budget(mode)
    retry_count = 0
    retry_issues: list[str] = []
    removed_or_softened_claims: list[str] = []
    final_text = draft_text
    polish_package: dict[str, Any] = {}
    polish_raw = ""
    quote_used = False

    while True:
        polish_payload, polish_raw, _polish_stderr = _run_codex_json_stage(
            stage_name="chunshe:polish_package" if retry_count == 0 else f"chunshe:polish_package_retry_{retry_count}",
            prompt=shared_reference
            + _build_chunshe_polish_package_prompt(
                markdown_text=final_text,
                topic=topic,
                role=role,
                output_type=output_type,
                mode=mode,
                quote_candidate=quote_candidate,
                retry_issues=retry_issues or None,
            ),
            model=model,
            codex_cli=codex_cli,
            timeout_sec=timeout_sec,
        )
        polish_package = _normalize_wechat_stage_dict("chunshe_polish_package", polish_payload)
        candidate_text = _normalize_chunshe_markdown(str(polish_package.get("final_markdown") or "").strip(), title_candidates)
        issues = [str(item or "").strip() for item in polish_package.get("issues") or [] if str(item or "").strip()]
        local_issues = _validate_chunshe_markdown(candidate_text, topic)
        merged_issues = list(dict.fromkeys([*issues, *local_issues]))
        removed_or_softened_claims = [
            str(item or "").strip()
            for item in polish_package.get("removed_or_softened_claims") or []
            if str(item or "").strip()
        ]
        passed = bool(polish_package.get("passed")) and not merged_issues
        final_text = candidate_text or final_text
        quote_used = bool(quote_candidate) and final_text != draft_text
        if passed:
            polish_package["issues"] = merged_issues
            polish_package["passed"] = True
            break
        if retry_count >= retry_budget:
            raise RuntimeError("; ".join(merged_issues) or "chunshe polish package failed")
        retry_count += 1
        retry_issues = merged_issues[:6]
        if mode == "严格" and retry_count >= 2:
            quote_candidate = None
        elif any("金句" in item or "结尾" in item or "生硬" in item for item in merged_issues):
            quote_candidate = None

    _write_json_file(
        topic_report_dir / "draft-package.json",
        {
            "entry_class": draft_package.get("entry_class"),
            "person": draft_package.get("person"),
            "recent_trigger": draft_package.get("recent_trigger"),
            "fear": draft_package.get("fear"),
            "real_buy_point": draft_package.get("real_buy_point"),
            "store_rule_primary": draft_package.get("store_rule_primary"),
            "narrative_ratio": draft_package.get("narrative_ratio"),
            "title_candidates": title_candidates,
        },
    )
    _write_text_file(topic_report_dir / "draft-package.raw.txt", draft_raw)
    _write_json_file(
        topic_report_dir / "polish-package.json",
        {
            "passed": bool(polish_package.get("passed")),
            "issues": polish_package.get("issues") or [],
            "removed_or_softened_claims": removed_or_softened_claims,
            "retry_count": retry_count,
            "quote_candidate": quote_candidate,
            "quote_candidates": quote_candidates[:1],
            "quote_used": quote_used,
        },
    )
    _write_text_file(topic_report_dir / "polish-package.raw.txt", polish_raw)
    _write_text_file(topic_report_dir / "final.raw.txt", final_text)

    return {
        "status": "success",
        "topic": topic,
        "content": final_text,
        "draft_package": draft_package,
        "polish_package": polish_package,
        "retry_count": retry_count,
        "quote_used": quote_used,
    }


def __deprecated_run_chunshe_staged_task_v1(
    *,
    skill: SkillDefinition,
    brief: str,
    platform: str,
    date_str: str,
    model: str,
    event_ref: str,
    source_ref: str,
    timeout_sec: int,
    codex_cli: str,
    context_files_used: list[str],
    context_plan: dict[str, Any],
    context_warnings: list[str],
    context_errors: list[str],
    context_prompt: str,
    started: float,
    dry_run: bool,
) -> dict[str, Any]:
    config = _extract_chunshe_runtime_config(brief)
    seed_examples = match_chunshe_topic_seed_examples(
        str(config.get("seed_keyword") or ""),
        str(config.get("entry_class") or ""),
        limit=12,
    )
    if str(config.get("explicit_topic") or "").strip():
        seed_examples = [_build_chunshe_manual_topic(config)]
    elif not seed_examples:
        seed_examples = [dict(item) for item in load_chunshe_topic_seed_pool()[:12]]
    recent_history = collect_recent_chunshe_history(lookback_days=30)
    recent_history_summary = summarize_recent_history(recent_history, limit=12)
    source_materials = {
        "seed_keyword": config.get("seed_keyword"),
        "explicit_topic": config.get("explicit_topic"),
        "entry_class": config.get("entry_class"),
        "role": config.get("role"),
        "output_type": config.get("output_type"),
        "mode": config.get("mode"),
        "batch_count": config.get("batch_count"),
        "quote_enabled": config.get("quote_enabled"),
        "quote_theme_requested": config.get("explicit_quote_theme"),
        "high_boundary": config.get("high_boundary"),
        "recent_history": recent_history_summary,
        "seed_examples": seed_examples[:8],
    }
    worker_plan = {
        "topic_target_count": int(config.get("batch_count") or 1),
        "output_type": config.get("output_type") or "精简发布版",
        "role": config.get("role") or "李可",
        "mode": config.get("mode") or "平衡",
        "stages": list(CHUNSHE_SHARED_STAGES),
    }
    merged_runtime_contexts = list(dict.fromkeys(context_plan.get("context_files_merged") or []))
    preview_candidates = seed_examples[: max(5, int(config.get("batch_count") or 1))]
    selected_preview, rejected_preview = dedupe_and_pick_chunshe_topics(
        preview_candidates,
        recent_history,
        count=max(1, int(config.get("batch_count") or 1)),
    )
    selected_preview = _prefer_chunshe_seed_consistent_topics(
        selected_topics=selected_preview,
        topic_candidates=preview_candidates,
        config=config,
    )
    selected_preview = [enrich_chunshe_video_topic(item) for item in selected_preview]

    if dry_run:
        elapsed = int((time.time() - started) * 1000)
        status = "error" if context_errors else "success"
        return {
            "status": status,
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "platform": platform,
            "date": date_str,
            "model": model or DEFAULT_MODEL,
            "event_ref": event_ref,
            "source_ref": source_ref,
            "context_files_used": context_files_used,
            "context_files_auto": list(context_plan.get("context_files_auto") or []),
            "context_files_merged": merged_runtime_contexts,
            "context_warnings": context_warnings,
            "context_errors": context_errors,
            "saved_files": [],
            "full_text": "",
            "stderr": "",
            "elapsed_ms": elapsed,
            "errors": list(context_errors),
            "dry_run": True,
            "pipeline_mode": "staged_single_worker" if int(config.get("batch_count") or 1) == 1 else "staged_serial_topics",
            "shared_stages": list(CHUNSHE_SHARED_STAGES),
            "worker_plan": worker_plan,
            "source_materials": source_materials,
            "topic_pool_preview": preview_candidates,
            "selected_topics": selected_preview,
            "rejected_topics": rejected_preview[:12],
        }

    if context_errors:
        elapsed = int((time.time() - started) * 1000)
        return {
            "status": "error",
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "platform": platform,
            "date": date_str,
            "model": model or DEFAULT_MODEL,
            "event_ref": event_ref,
            "source_ref": source_ref,
            "context_files_used": context_files_used,
            "context_files_auto": list(context_plan.get("context_files_auto") or []),
            "context_files_merged": merged_runtime_contexts,
            "context_warnings": context_warnings,
            "context_errors": context_errors,
            "saved_files": [],
            "full_text": "",
            "stderr": "",
            "elapsed_ms": elapsed,
            "errors": list(context_errors),
            "pipeline_mode": "staged_single_worker" if int(config.get("batch_count") or 1) == 1 else "staged_serial_topics",
            "shared_stages": list(CHUNSHE_SHARED_STAGES),
            "worker_plan": worker_plan,
            "source_materials": source_materials,
        }

    cli_path = codex_cli.strip() or resolve_codex_cli()
    task_id = _chunshe_generation_task_id(date_str)
    report_dir = _chunshe_generation_report_dir(date_str, task_id)
    skill_content = _read_text(skill.path)

    topic_plan_payload: dict[str, Any] | None = None
    topic_plan_raw = ""
    topic_candidates: list[dict[str, Any]] = []
    used_model_topic_planner = False
    if str(config.get("explicit_topic") or "").strip():
        topic_candidates = [_build_chunshe_manual_topic(config)]
        topic_plan_payload = {
            "seed_keyword": config.get("seed_keyword"),
            "entry_class": config.get("entry_class"),
            "topic_pool": topic_candidates,
            "rejected_topics": [],
            "source": "manual_topic",
        }
    else:
        topic_candidates = [dict(item) for item in seed_examples[:12]]
        need_model_expansion = len(topic_candidates) < 3 or (
            int(config.get("batch_count") or 1) >= 7 and _is_chunshe_broad_seed_keyword(str(config.get("seed_keyword") or ""))
        )
        if need_model_expansion:
            try:
                topic_plan_payload, topic_plan_raw, _topic_stderr = _run_codex_json_stage(
                    stage_name="chunshe:topic_planner",
                    prompt=f"【技能规则】\n{skill_content}\n\n"
                    + _build_chunshe_topic_planner_prompt(
                        brief=brief,
                        config=config,
                        context_prompt=context_prompt,
                        seed_examples=seed_examples[:8],
                        recent_history=recent_history_summary,
                    ),
                    model=model or DEFAULT_MODEL,
                    codex_cli=cli_path,
                    timeout_sec=timeout_sec,
                )
                planned_candidates = _normalize_chunshe_topic_pool(topic_plan_payload, config=config)
                if planned_candidates:
                    topic_candidates = planned_candidates + topic_candidates
                    used_model_topic_planner = True
            except Exception as exc:
                context_warnings.append(f"topic planner fallback used: {exc}")
        if not topic_plan_payload:
            topic_plan_payload = {
                "seed_keyword": config.get("seed_keyword"),
                "entry_class": config.get("entry_class"),
                "topic_pool": topic_candidates,
                "rejected_topics": [],
                "source": "local_topic_engine",
            }
    if not topic_candidates:
        topic_candidates = [_build_chunshe_manual_topic(config)]

    selected_topics, rejected_topics = dedupe_and_pick_chunshe_topics(
        topic_candidates,
        recent_history,
        count=max(1, int(config.get("batch_count") or 1)),
    )
    selected_topics = _prefer_chunshe_seed_consistent_topics(
        selected_topics=selected_topics,
        topic_candidates=topic_candidates,
        config=config,
    )
    if not selected_topics:
        selected_topics = [dict(item) for item in topic_candidates[: max(1, int(config.get("batch_count") or 1))]]

    _write_json_file(report_dir / "source-materials.json", source_materials)
    _write_json_file(
        report_dir / "topic-selection.json",
        {
            "seed_keyword": config.get("seed_keyword"),
            "entry_class": config.get("entry_class"),
            "used_model_topic_planner": used_model_topic_planner,
            "topic_pool": topic_candidates,
            "selected_topics": selected_topics,
            "rejected_topics": rejected_topics,
        },
    )
    if topic_plan_raw:
        _write_text_file(report_dir / "topic-selection.raw.txt", topic_plan_raw)

    if str(config.get("output_type") or "") == "标题池":
        title_pool_text = render_chunshe_title_pool(
            seed_keyword=str(config.get("seed_keyword") or ""),
            role=str(config.get("role") or "李可"),
            entry_class=str(config.get("entry_class") or ""),
            topics=selected_topics,
        )
        saved_files = _save_generated_files(text=title_pool_text, skill=skill, platform=platform, date_str=date_str)
        elapsed = int((time.time() - started) * 1000)
        try:
            report_dir_text = report_dir.relative_to(REPO_ROOT).as_posix()
        except Exception:
            report_dir_text = str(report_dir)
        return {
            "status": "success",
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "platform": platform,
            "date": date_str,
            "model": model or DEFAULT_MODEL,
            "event_ref": event_ref,
            "source_ref": source_ref,
            "context_files_used": context_files_used,
            "context_files_auto": list(context_plan.get("context_files_auto") or []),
            "context_files_merged": merged_runtime_contexts,
            "context_warnings": context_warnings,
            "context_errors": context_errors,
            "saved_files": saved_files,
            "full_text": _read_primary_saved_file(saved_files) or title_pool_text,
            "stderr": "",
            "elapsed_ms": elapsed,
            "errors": [],
            "pipeline_mode": "staged_single_worker",
            "shared_stages": list(CHUNSHE_SHARED_STAGES),
            "worker_plan": worker_plan,
            "source_materials": source_materials,
            "selected_topics": selected_topics,
            "report_dir": report_dir_text,
        }

    topic_docs: list[dict[str, Any]] = []
    topic_results: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, topic in enumerate(selected_topics, start=1):
        topic_report_dir = report_dir / f"{index:02d}-{_sanitize_segment(str(topic.get('topic_id') or f'topic-{index}'), fallback=f'topic-{index}')}"
        topic_report_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = _run_chunshe_single_topic(
                role=str(config.get("role") or "李可"),
                output_type=str(config.get("output_type") or "精简发布版"),
                brief=brief,
                topic=topic,
                mode=str(config.get("mode") or "平衡"),
                quote_enabled=bool(config.get("quote_enabled")),
                explicit_quote_theme=str(config.get("explicit_quote_theme") or ""),
                skill_content=skill_content,
                context_prompt=context_prompt,
                model=model or DEFAULT_MODEL,
                codex_cli=cli_path,
                timeout_sec=timeout_sec,
                topic_report_dir=topic_report_dir,
            )
            output_path = _build_chunshe_output_path(
                date_str=date_str,
                role=str(config.get("role") or "李可"),
                output_type=str(config.get("output_type") or "精简发布版"),
                topic=topic,
                index=index,
            )
            topic_docs.append({"path": output_path, "content": result["content"]})
            topic_results.append({
                "topic_id": topic.get("topic_id"),
                "topic_title": topic.get("topic_title"),
                "path": output_path,
                "retry_count": result.get("retry_count"),
                "quote_used": result.get("quote_used"),
            })
        except Exception as exc:
            errors.append(f"{str(topic.get('topic_title') or topic.get('topic_id') or index).strip()}: {exc}")

    saved_files: list[str] = []
    full_text = ""
    if topic_docs:
        contract_text = _render_chunshe_file_contract(date_str=date_str, topic_docs=topic_docs)
        _write_text_file(report_dir / "chunshe-files-contract.txt", contract_text)
        saved_files = _save_generated_files(text=contract_text, skill=skill, platform=platform, date_str=date_str)
        full_text = _read_primary_saved_file(saved_files)

    status = "success"
    if errors and topic_docs:
        status = "partial_error"
    elif errors:
        status = "error"
    elapsed = int((time.time() - started) * 1000)
    try:
        report_dir_text = report_dir.relative_to(REPO_ROOT).as_posix()
    except Exception:
        report_dir_text = str(report_dir)
    return {
        "status": status,
        "skill_id": skill.skill_id,
        "skill_name": skill.name,
        "skill_path": skill.path.as_posix(),
        "platform": platform,
        "date": date_str,
        "model": model or DEFAULT_MODEL,
        "event_ref": event_ref,
        "source_ref": source_ref,
        "context_files_used": context_files_used,
        "context_files_auto": list(context_plan.get("context_files_auto") or []),
        "context_files_merged": merged_runtime_contexts,
        "context_warnings": context_warnings,
        "context_errors": context_errors,
        "saved_files": saved_files,
        "full_text": full_text,
        "stderr": "",
        "elapsed_ms": elapsed,
        "errors": errors,
        "pipeline_mode": "staged_single_worker" if len(selected_topics) == 1 else "staged_serial_topics",
        "shared_stages": list(CHUNSHE_SHARED_STAGES),
        "worker_plan": worker_plan,
        "source_materials": source_materials,
        "selected_topics": selected_topics,
        "topic_results": topic_results,
        "report_dir": report_dir_text,
    }


def _build_chunshe_result_payload(
    *,
    status: str,
    skill: SkillDefinition,
    platform: str,
    date_str: str,
    model: str,
    event_ref: str,
    source_ref: str,
    context_files_used: list[str],
    context_plan: dict[str, Any],
    merged_runtime_contexts: list[str],
    context_warnings: list[str],
    context_errors: list[str],
    saved_files: list[str],
    full_text: str,
    elapsed_ms: int,
    errors: list[str],
    worker_plan: dict[str, Any],
    source_materials: dict[str, Any],
    selected_topics: list[dict[str, Any]] | None = None,
    topic_results: list[dict[str, Any]] | None = None,
    report_dir: str | None = None,
    warnings: list[str] | None = None,
    stage_timings: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
    pipeline_mode: str = "staged_single_worker",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "status": status,
        "skill_id": skill.skill_id,
        "skill_name": skill.name,
        "skill_path": skill.path.as_posix(),
        "platform": platform,
        "date": date_str,
        "model": model or DEFAULT_MODEL,
        "event_ref": event_ref,
        "source_ref": source_ref,
        "context_files_used": context_files_used,
        "context_files_auto": list(context_plan.get("context_files_auto") or []),
        "context_files_merged": merged_runtime_contexts,
        "context_warnings": context_warnings,
        "context_errors": context_errors,
        "saved_files": saved_files,
        "full_text": full_text,
        "stderr": "",
        "elapsed_ms": elapsed_ms,
        "errors": errors,
        "pipeline_mode": pipeline_mode,
        "shared_stages": list(CHUNSHE_SHARED_STAGES),
        "worker_plan": worker_plan,
        "source_materials": source_materials,
        "selected_topics": selected_topics or [],
        "topic_results": topic_results or [],
        "warnings": warnings or [],
        "stage_timings": stage_timings or [],
    }
    if report_dir:
        payload["report_dir"] = report_dir
    if dry_run:
        payload["dry_run"] = True
    if extra:
        payload.update(extra)
    return payload


def _report_dir_text(report_dir: Path) -> str:
    try:
        return report_dir.relative_to(REPO_ROOT).as_posix()
    except Exception:
        return str(report_dir)


def _append_chunshe_stage_timing(
    stage_timings: list[dict[str, Any]],
    *,
    stage_name: str,
    started_at: dt.datetime,
    prompt_text: str = "",
    output_text: str = "",
    retry_index: int = 0,
    result_status: str = "success",
) -> None:
    ended_at = dt.datetime.now(dt.timezone.utc)
    stage_timings.append(
        {
            "stage_name": stage_name,
            "start_at": started_at.isoformat(),
            "end_at": ended_at.isoformat(),
            "duration_ms": int((ended_at - started_at).total_seconds() * 1000),
            "prompt_chars": len(str(prompt_text or "")),
            "output_chars": len(str(output_text or "")),
            "retry_index": retry_index,
            "result_status": result_status,
        }
    )


def _run_chunshe_json_stage(
    *,
    stage_timings: list[dict[str, Any]],
    stage_name: str,
    prompt: str,
    model: str,
    codex_cli: str,
    timeout_sec: int,
    retry_index: int = 0,
    raw_path: Path | None = None,
    json_path: Path | None = None,
) -> tuple[Any, str, str]:
    started_at = dt.datetime.now(dt.timezone.utc)
    raw_text = ""
    stderr = ""
    result_status = "success"
    try:
        raw_text, stderr = _run_codex(
            prompt,
            model=model,
            codex_cli=codex_cli,
            timeout_sec=timeout_sec,
        )
        if raw_path is not None:
            _write_text_file(raw_path, raw_text)
        payload = _extract_json_payload(raw_text)
        if payload is None:
            raise RuntimeError(f"invalid JSON output for stage: {stage_name}")
        if json_path is not None:
            _write_json_file(json_path, payload)
        return payload, raw_text, stderr
    except Exception:
        result_status = "error"
        if raw_path is not None and raw_text:
            _write_text_file(raw_path, raw_text)
        raise
    finally:
        _append_chunshe_stage_timing(
            stage_timings,
            stage_name=stage_name,
            started_at=started_at,
            prompt_text=prompt,
            output_text=raw_text,
            retry_index=retry_index,
            result_status=result_status,
        )


def _build_chunshe_source_pack_prompt(
    *,
    skill: SkillDefinition,
    skill_content: str,
    platform: str,
    date_str: str,
    brief: str,
    context_prompt: str,
    source_materials: dict[str, Any],
) -> str:
    return (
        "你是椿舍门店专用内容系统的 source_pack 阶段。\n"
        "目标：把门店事实、写作硬规则、brief 提取结果、选题辅助整理成紧凑 JSON，供后续 stage 复用。\n"
        "只输出一个 JSON 对象，不要输出 markdown，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "source_pack": {\n'
        '    "business_facts": {\n'
        '      "store_positioning": "string",\n'
        '      "allowed_services": ["string"],\n'
        '      "review_boundary": ["string"],\n'
        '      "pinned_comment_boundary": ["string"]\n'
        "    },\n"
        '    "hard_rules": {\n'
        '      "banned_openings": ["string"],\n'
        '      "banned_patterns": ["string"],\n'
        '      "body_cta_blacklist": ["string"],\n'
        '      "question_guards": ["string"]\n'
        "    },\n"
        '    "brief_extract": {\n'
        '      "seed_keyword": "string",\n'
        '      "entry_class": "string",\n'
        '      "role": "string",\n'
        '      "output_type": "string",\n'
        '      "mode": "string",\n'
        '      "target_audience": "string",\n'
        '      "scene_trigger_hint": "string",\n'
        '      "buy_point": "string",\n'
        '      "banned_words": "string"\n'
        "    },\n"
        '    "topic_helpers": {\n'
        '      "high_boundary": true,\n'
        '      "batch_dedupe_rules": ["string"],\n'
        '      "store_rule_options": ["string"],\n'
        '      "ending_function_options": ["string"]\n'
        "    }\n"
        "  }\n"
        "}\n\n"
        f"目标平台：{platform}\n"
        f"目标日期：{date_str}\n"
        f"【系统预提取素材】\n{json.dumps(source_materials, ensure_ascii=False, indent=2)}\n\n"
        f"{context_prompt}"
        f"【技能文档路径】\n{skill.path.as_posix()}\n\n"
        f"【技能规则】\n{skill_content}\n\n"
        f"【用户 brief】\n{brief.strip()}\n"
    )


def _normalize_chunshe_source_pack(payload: Any) -> dict[str, Any]:
    root = _normalize_wechat_stage_dict("chunshe_source_pack", payload)
    source_pack = root.get("source_pack")
    if not isinstance(source_pack, dict):
        raise RuntimeError("chunshe source_pack stage returned missing source_pack object")

    required_blocks = {
        "business_facts": dict,
        "hard_rules": dict,
        "brief_extract": dict,
        "topic_helpers": dict,
        "theme_language": dict,
    }
    for key, expected_type in required_blocks.items():
        if not isinstance(source_pack.get(key), expected_type):
            raise RuntimeError(f"chunshe source_pack missing required field: {key}")

    nested_required: list[tuple[str, dict[str, Any], tuple[str, ...]]] = [
        (
            "business_facts",
            source_pack["business_facts"],
            ("store_positioning", "allowed_services", "review_boundary", "pinned_comment_boundary"),
        ),
        (
            "hard_rules",
            source_pack["hard_rules"],
            ("banned_openings", "banned_patterns", "body_cta_blacklist", "question_guards"),
        ),
        (
            "brief_extract",
            source_pack["brief_extract"],
            ("seed_keyword", "entry_class", "role", "output_type", "mode"),
        ),
        (
            "topic_helpers",
            source_pack["topic_helpers"],
            ("high_boundary", "batch_dedupe_rules", "store_rule_options", "ending_function_options"),
        ),
        (
            "theme_language",
            source_pack["theme_language"],
            ("principle", "theme_examples", "translation_examples", "boss_judgment_examples", "low_pressure_offer_examples"),
        ),
    ]
    for block_name, block, keys in nested_required:
        for key in keys:
            if key not in block:
                raise RuntimeError(f"chunshe source_pack missing required field: {block_name}.{key}")
    return root


def _build_chunshe_topic_planner_prompt(
    *,
    brief: str,
    config: dict[str, Any],
    source_pack: dict[str, Any],
    seed_examples: list[dict[str, Any]],
    recent_history: list[dict[str, Any]],
) -> str:
    min_topics = max(5, int(config.get("batch_count") or 1))
    return (
        "你是椿舍门店专用内容系统的 topic_planner 阶段。\n"
        "任务：只做选题规划，不写正文。\n"
        "请基于 Source Pack、种子题、最近历史，输出一组可发选题。\n"
        "只输出一个 JSON 对象，不要输出 markdown，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "seed_keyword": "string",\n'
        '  "entry_class": "问题修复|信任怀疑|放松养护|本地找店",\n'
        '  "topic_pool": [\n'
        '    {"topic_id": "string", "topic_title": "string", "angle_type": "痛点确认|防御拆解|规矩托底|向往画面|本地决策|关系/身份翻译", "scene_trigger": "string", "fear": "string", "real_desire": "string", "real_buy_point": "string", "store_rule_hint": "string", "life_stage_hint": "string", "ending_function": "string", "priority_score": 0}\n'
        "  ],\n"
        '  "rejected_topics": [{"topic_title": "string", "reason": "string"}]\n'
        "}\n\n"
        f"要求：至少给出 {min_topics} 个候选，最多 12 个；题目要像能发的小红书标题，不要写成问卷或方案。\n"
        "不要把“默认7题”原样换词重写，优先让视角分散。\n\n"
        f"【运行配置】\n{json.dumps(config, ensure_ascii=False, indent=2)}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack, ensure_ascii=False, indent=2)}\n\n"
        f"【近期历史】\n{json.dumps(recent_history, ensure_ascii=False, indent=2)}\n\n"
        f"【首批种子题】\n{json.dumps(seed_examples, ensure_ascii=False, indent=2)}\n\n"
        f"【用户 brief】\n{brief.strip()}\n"
    )


def __deprecated_build_chunshe_draft_package_prompt_v2(
    *,
    role: str,
    output_type: str,
    brief: str,
    mode: str,
    source_pack: dict[str, Any],
    topic: dict[str, Any],
) -> str:
    length_hint = "220-320字" if output_type == "精简发布版" else "280-420字"
    return (
        "你是椿舍门店专用内容系统的 draft_package 阶段。\n"
        "任务：一次完成策略定稿、段落安排、3 个标题候选和首稿正文。\n"
        "只输出一个 JSON 对象，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "entry_class": "问题修复|信任怀疑|放松养护|本地找店",\n'
        '  "person": "string",\n'
        '  "recent_trigger": "string",\n'
        '  "fear": "string",\n'
        '  "real_buy_point": "string",\n'
        '  "store_rule_primary": "string",\n'
        '  "narrative_ratio": "2-5-3|4-3-3|1-5-4",\n'
        '  "title_candidates": ["string", "string", "string"],\n'
        '  "draft_markdown": "string"\n'
        "}\n\n"
        "draft_markdown 结构固定为：\n"
        "# 标题\n主标题\n## 备选标题\n- 备选1\n- 备选2\n# 正文\n正文\n# 置顶评论\n置顶评论\n# 回复模板\n回复模板\n\n"
        f"正文长度：{length_hint}。运行模式：{mode}。\n"
        "要求：\n"
        "- 先定人、冲突、真实买点，再落首稿。\n"
        "- 开头先写她为什么会在这个时候搜这个词，不要写成“有用吗？有用，但……”或“值不值，先看……”。\n"
        "- 不要把人生阶段翻译直接写进正文，不出现“这个年纪/这个阶段”这种空话。\n"
        "- 主规矩只出现一次，辅助细节最多一句，不要把后半段写成门店制度说明。\n"
        "- 不要硬塞金句，金句阶段不在这里。\n"
        "- 标题候选要彼此有区别，不是同一句换词。\n\n"
        f"【角色】{role}\n"
        f"【输出类型】{output_type}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack, ensure_ascii=False, indent=2)}\n\n"
        f"【已选题目】\n{json.dumps(topic, ensure_ascii=False, indent=2)}\n\n"
        f"【用户 brief】\n{brief.strip()}\n"
    )


def __deprecated_build_chunshe_polish_package_prompt_v2(
    *,
    markdown_text: str,
    topic: dict[str, Any],
    role: str,
    output_type: str,
    mode: str,
    source_pack: dict[str, Any],
    quote_candidate: dict[str, Any] | None,
    retry_issues: list[str] | None = None,
) -> str:
    retry_block = ""
    if retry_issues:
        retry_block = f"【上一轮问题】\n{json.dumps(retry_issues, ensure_ascii=False, indent=2)}\n\n"
    quote_block = "【候选结尾金句】\n无\n\n"
    if quote_candidate:
        quote_block = f"【候选结尾金句】\n{json.dumps(quote_candidate, ensure_ascii=False, indent=2)}\n\n"
    return (
        "你是椿舍门店专用内容系统的 polish_package 阶段。\n"
        "任务：在现有首稿上完成连贯修补、口语化二审和最终放行。\n"
        "只输出一个 JSON 对象，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "passed": true,\n'
        '  "issues": ["string"],\n'
        '  "removed_or_softened_claims": ["string"],\n'
        '  "final_markdown": "string"\n'
        "}\n\n"
        "要求：\n"
        "- 只修稿，不换题，不重写主买点。\n"
        "- 先修跳句、缺块、一段塞太多任务，再做口语化。\n"
        "- 去掉解释味和提炼腔，优先用动作、时间、场景、后果接起来。\n"
        "- 默认只允许在结尾或结尾前一句轻收 0-1 条金句；如果生硬，直接不用。\n"
        "- 必须保留标题结构：1 个主标题 + 2 个备选标题。\n"
        "- passed=true 的前提是：读起来顺、像人话、金句不抢戏。\n\n"
        f"【角色】{role}\n"
        f"【输出类型】{output_type}\n"
        f"【运行模式】{mode}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack, ensure_ascii=False, indent=2)}\n\n"
        f"【题目】\n{json.dumps(topic, ensure_ascii=False, indent=2)}\n\n"
        f"{quote_block}"
        f"{retry_block}"
        f"【当前首稿】\n{markdown_text.strip()}\n"
    )


def _build_chunshe_intro_rewrite_prompt(
    *,
    source_pack: dict[str, Any],
    topic: dict[str, Any],
    markdown_text: str,
    intro_issues: list[str],
) -> str:
    return (
        "你是椿舍门店专用内容系统的 intro_rewrite 阶段。\n"
        "任务：只修正文开头前三句和首段，去掉问答模板味、值不值模板味、身份开头、店规开头。\n"
        "禁止改题、禁止重写后半段、禁止新增正文 CTA。\n"
        "只输出一个 JSON 对象，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "final_markdown": "string",\n'
        '  "fixed_issues": ["string"]\n'
        "}\n\n"
        f"【命中的硬问题】\n{json.dumps(intro_issues, ensure_ascii=False, indent=2)}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack, ensure_ascii=False, indent=2)}\n\n"
        f"【选题】\n{json.dumps(topic, ensure_ascii=False, indent=2)}\n\n"
        f"【当前正文】\n{markdown_text.strip()}\n"
    )


def _extract_chunshe_section(markdown_text: str, marker: str) -> str:
    matched = re.search(rf"(?ms)^{re.escape(marker)}\s*(.*?)\s*(?=^# |\Z)", str(markdown_text or ""))
    if not matched:
        return ""
    return str(matched.group(1) or "").strip()


def __deprecated_validate_chunshe_markdown_v2(markdown_text: str) -> list[str]:
    text = str(markdown_text or "").strip()
    issues: list[str] = []
    required_sections = ("# 标题", "## 备选标题", "# 正文", "# 置顶评论", "# 回复模板")
    for section in required_sections:
        if section not in text:
            issues.append(f"缺少区块：{section}")

    body = _extract_chunshe_section(text, "# 正文")
    comment = _extract_chunshe_section(text, "# 置顶评论")
    reply = _extract_chunshe_section(text, "# 回复模板")
    if "# 正文" in text and not body:
        issues.append("正文为空")
    if "# 置顶评论" in text and not comment:
        issues.append("置顶评论为空")
    if "# 回复模板" in text and not reply:
        issues.append("回复模板为空")

    intro_scope = "\n".join(body.splitlines()[:4]).strip()
    if re.search(r"有用吗[？?]\s*有用[,，、 ]*但", intro_scope):
        issues.append("仍有问答模板味")
    if re.search(r"值不值[,，、 ]*先看", intro_scope):
        issues.append("仍有值不值模板味")
    if re.search(r"我是开美容院的.{0,24}所以我", intro_scope):
        issues.append("仍有身份开头")
    if "我店里有条规矩" in intro_scope:
        issues.append("仍有店规开头")

    for token in CHUNSHE_BODY_CTA_BLACKLIST:
        if token and token in body:
            issues.append(f"正文出现CTA黑名单：{token}")
            break
    return issues


def _split_chunshe_issues(issues: list[str]) -> tuple[list[str], list[str]]:
    hard: list[str] = []
    soft: list[str] = []
    seen: set[str] = set()
    soft_markers = (
        "过桥", "口语", "制度感", "节奏", "结尾", "金句", "生硬", "更顺", "更像人话", "收回", "压缩",
        "缺少区块",  # no longer hard — story-driven content may not have mechanical blocks
        "不是A", "不是...是...", "向往感不足",
        "角色称谓",
    )
    hard_markers = (
        "为空", "CTA", "问答模板味", "值不值模板味", "身份开头", "店规开头", "显式禁用句式",
        "缺少向往画面", "缺少具体人",  # story-driven bottom lines
    )
    for raw in issues:
        text = str(raw or "").strip()
        key = _normalize_key(text)
        if not text or key in seen:
            continue
        seen.add(key)
        if text in CHUNSHE_INTRO_HARD_ISSUES or any(marker in text for marker in hard_markers):
            hard.append(text)
        elif any(marker in text for marker in soft_markers):
            soft.append(text)
        else:
            hard.append(text)
    return hard, soft


def _build_local_chunshe_source_pack(*, config: dict[str, Any], source_materials: dict[str, Any]) -> dict[str, Any]:
    seed_examples = source_materials.get("seed_examples") or []

    store_rule_options: list[str] = []
    for item in seed_examples:
        rule = str((item or {}).get("store_rule_hint") or "").strip()
        if rule and rule not in store_rule_options:
            store_rule_options.append(rule)
    for rule in (
        "说不要，话题就停",
        "不强推",
        "不乱加项",
        "按次可做",
        "不适合直接说",
        "流程、时长、合理预期说清楚",
    ):
        if rule not in store_rule_options:
            store_rule_options.append(rule)

    ending_function_options: list[str] = []
    for item in seed_examples:
        ending = str((item or {}).get("ending_function") or "").strip()
        if ending and ending not in ending_function_options:
            ending_function_options.append(ending)
    for ending in (
        "边界放心收口",
        "今天没那么累",
        "下次还敢来",
        "人先松下来",
        "不被消耗地离开",
    ):
        if ending not in ending_function_options:
            ending_function_options.append(ending)

    root = {
        "source_pack": {
            "business_facts": {
                "store_positioning": "椿舍在苏州吴江，靠近江兴西路地铁站和吴江公园。",
                "allowed_services": ["基础清洁", "面部清洁", "补水舒缓", "放松护理"],
                "review_boundary": [
                    "正文不写价格、预约、团购、到店导航",
                    "正文不写医疗承诺和结果承诺",
                    "正文不写流程堆砌和项目说明书",
                ],
                "output_goal": {
                    "精简发布版": "90-140字，5-7行短句，适合20-35秒短视频口播",
                    "解释型口播版": "140-220字，7-10行短句，适合25-45秒短视频口播",
                },
                "pinned_comment_boundary": [
                    "店名、位置、预约方式放置顶评论",
                    "首次来店可从基础清洁或补水舒缓开始",
                    "现场可以给建议，但说不要就停",
                ],
            },
            "hard_rules": {
                "banned_openings": [
                    "有用吗？有用，但",
                    "值不值，先看",
                    "我是开美容院的，所以我",
                    "我店里有条规矩",
                    "很多人搜",
                    "她会搜",
                    "表面上",
                    "本质",
                ],
                "banned_patterns": [
                    "不是……而是……",
                    "更扎心的是",
                    "换句话说",
                    "总的来说",
                ],
                "body_cta_blacklist": list(CHUNSHE_BODY_CTA_BLACKLIST),
                "opening_guards": [
                    "第一句必须是冲突句、判断句或直接场景句",
                    "前两行必须有具体动作或场景，不要先讲为什么搜索",
                    "前两行总长度尽量控制在26-42字",
                    "禁止角色名开头和老板自报身份开头",
                ],
                "question_guards": [
                    "一段只承载一个任务，不要一段里同时讲害怕、规矩、结论",
                    "先写那一下不舒服，再写门店怎么接住她",
                    "先写具体人和具体时刻，不要先讲道理",
                ],
            },
            "brief_extract": {
                "seed_keyword": str(config.get("seed_keyword") or "").strip(),
                "entry_class": str(config.get("entry_class") or "").strip(),
                "role": str(config.get("role") or "李可").strip() or "李可",
                "output_type": str(config.get("output_type") or "精简发布版").strip() or "精简发布版",
                "mode": str(config.get("mode") or "平衡").strip() or "平衡",
                "target_audience": str(config.get("target_audience") or "").strip(),
                "scene_trigger_hint": str(config.get("scene_trigger_hint") or "").strip(),
                "buy_point": str(config.get("buy_point") or "").strip(),
                "banned_words": str(config.get("banned_words") or "").strip(),
            },
            "topic_helpers": {
                "high_boundary": bool(config.get("high_boundary")),
                "batch_dedupe_rules": [
                    "同一批里不要重复同一个 angle_type",
                    "同一批里不要重复同一个 scene_trigger",
                    "同一批里不要重复同一个主规矩",
                    "同一批里不要重复同一种结尾功能",
                ],
                "store_rule_options": store_rule_options[:6],
                "ending_function_options": ending_function_options[:6],
            },
        }
    }
    review_pack = select_chunshe_review_phrase_pack(
        entry_class=str(config.get("entry_class") or "").strip(),
        seed_keyword=str(config.get("seed_keyword") or "").strip(),
        topic={},
    )
    theme_pack = select_chunshe_theme_phrase_pack(
        entry_class=str(config.get("entry_class") or "").strip(),
        topic={"seed_keyword": str(config.get("seed_keyword") or "").strip()},
    )
    root["source_pack"]["review_language"] = {
        "principle": "优先直接复用差评里的原话，不要把顾客说过的话改成抽象词或文艺词。",
        "bucket": str(review_pack.get("bucket") or "").strip(),
        "opening_examples": review_pack.get("opening") or [],
        "scene_examples": review_pack.get("scene") or [],
        "landing_examples": review_pack.get("landing") or [],
    }
    root["source_pack"]["theme_language"] = {
        "principle": str(theme_pack.get("principle") or "").strip(),
        "theme_examples": theme_pack.get("theme_examples") or [],
        "translation_examples": theme_pack.get("translation_examples") or [],
        "boss_judgment_examples": theme_pack.get("boss_judgment_examples") or [],
        "low_pressure_offer_examples": theme_pack.get("low_pressure_offer_examples") or [],
    }
    return _normalize_chunshe_source_pack(root)


def _build_chunshe_pinned_comment(*, topic: dict[str, Any], source_pack: dict[str, Any]) -> str:
    business_facts = dict(source_pack.get("business_facts") or {})
    positioning = str(business_facts.get("store_positioning") or "椿舍在苏州吴江").strip().rstrip("。！？；，,;! ")
    services = [str(item).strip() for item in business_facts.get("allowed_services") or [] if str(item).strip()]
    starter = "基础清洁或补水舒缓"
    if "基础清洁" in services and "补水舒缓" in services:
        starter = "基础清洁或补水舒缓"
    elif services:
        starter = "或".join(services[:2]) if len(services) >= 2 else services[0]
    store_rule = str(topic.get("store_rule_hint") or "说不要，话题就停").strip() or "说不要，话题就停"
    return (
        f"{positioning}。店名、位置和怎么过来我放这条置顶。"
        f"第一次来，想先从{starter}开始，就按这个来；现场可以给建议，但{store_rule}。"
        "价格、预约和到店细节都放这里，不放正文里。"
    )


def _build_chunshe_reply_template(*, topic: dict[str, Any]) -> str:
    fear = str(topic.get("fear") or "先怕被教育，再怕白花钱").strip() or "先怕被教育，再怕白花钱"
    buy_point = str(topic.get("real_buy_point") or "做完不被耗掉，人还能松一点").strip() or "做完不被耗掉，人还能松一点"
    return (
        f"{fear}，这个顾虑很正常。先看边界清不清楚，再看过程稳不稳；"
        f"{buy_point}，比当场把话说得很满更重要。"
    )


def _render_chunshe_markdown(*, title_candidates: list[str], body_text: str, pinned_comment: str, reply_template: str) -> str:
    title = title_candidates[0] if title_candidates else "先别急着决定做不做脸"
    alt_1 = title_candidates[1] if len(title_candidates) > 1 else title
    alt_2 = title_candidates[2] if len(title_candidates) > 2 else alt_1
    raw_markdown = "\n".join(
        [
            "# 标题",
            title,
            "",
            "## 备选标题",
            f"- {alt_1}",
            f"- {alt_2}",
            "",
            "# 正文",
            str(body_text or "").strip(),
            "",
            "# 置顶评论",
            str(pinned_comment or "").strip(),
            "",
            "# 回复模板",
            str(reply_template or "").strip(),
        ]
    )
    return _normalize_chunshe_markdown(raw_markdown, title_candidates)


def _extract_chunshe_draft_body(payload: dict[str, Any]) -> str:
    direct_body = str(payload.get("draft_body") or "").strip()
    if direct_body:
        return direct_body
    markdown_text = str(payload.get("draft_markdown") or "").strip()
    if not markdown_text:
        return ""
    extracted = _extract_chunshe_section(markdown_text, "# 正文")
    return extracted or markdown_text


def _normalize_chunshe_polish_issues(raw_issues: list[Any]) -> list[str]:
    resolved_markers = (
        "已收回",
        "已改",
        "已改成",
        "已改为",
        "已调整",
        "已删",
        "已删除",
        "已弱化",
        "已压缩",
        "已去掉",
        "未使用候选结尾金句",
    )
    unresolved_markers = (
        "仍有",
        "依然",
        "还有",
        "缺少",
        "为空",
        "出现",
        "命中",
        "需要",
        "过桥",
        "跳句",
        "模板味",
        "AI味",
        "CTA",
        "生硬",
        "不顺",
        "抢戏",
        "不自然",
    )
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in raw_issues or []:
        text = str(raw or "").strip()
        key = _normalize_key(text)
        if not text or not key or key in seen:
            continue
        if any(marker in text for marker in resolved_markers) and not any(marker in text for marker in unresolved_markers):
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _build_chunshe_draft_package_prompt(
    *,
    role: str,
    output_type: str,
    brief: str,
    mode: str,
    source_pack: dict[str, Any],
    topic: dict[str, Any],
) -> str:
    return build_chunshe_video_draft_package_prompt(
        role=role,
        output_type=output_type,
        brief=brief,
        mode=mode,
        source_pack=source_pack,
        topic=topic,
    )


def _build_chunshe_polish_package_prompt(
    *,
    markdown_text: str,
    topic: dict[str, Any],
    role: str,
    output_type: str,
    mode: str,
    source_pack: dict[str, Any],
    quote_candidate: dict[str, Any] | None,
    retry_issues: list[str] | None = None,
) -> str:
    return build_chunshe_video_polish_package_prompt(
        markdown_text=markdown_text,
        topic=topic,
        role=role,
        output_type=output_type,
        mode=mode,
        source_pack=source_pack,
        quote_candidate=quote_candidate,
        retry_issues=retry_issues,
    )


def _validate_chunshe_markdown(markdown_text: str, topic: dict[str, Any] | None = None) -> list[str]:
    return validate_chunshe_video_markdown(markdown_text, CHUNSHE_BODY_CTA_BLACKLIST, topic)


def _run_chunshe_single_topic(
    *,
    role: str,
    output_type: str,
    brief: str,
    topic: dict[str, Any],
    mode: str,
    quote_enabled: bool,
    explicit_quote_theme: str,
    source_pack: dict[str, Any],
    model: str,
    codex_cli: str,
    timeout_sec: int,
    topic_report_dir: Path,
    topic_index: int,
    stage_timings: list[dict[str, Any]],
) -> dict[str, Any]:
    stage_prefix = f"chunshe:topic_{topic_index:02d}"
    draft_payload, _draft_raw, _draft_stderr = _run_chunshe_json_stage(
        stage_timings=stage_timings,
        stage_name=f"{stage_prefix}:draft_package",
        prompt=_build_chunshe_draft_package_prompt(
            role=role,
            output_type=output_type,
            brief=brief,
            mode=mode,
            source_pack=source_pack,
            topic=topic,
        ),
        model=model,
        codex_cli=codex_cli,
        timeout_sec=timeout_sec,
        raw_path=topic_report_dir / "draft-package.raw.txt",
        json_path=topic_report_dir / "draft-package.model.json",
    )
    draft_package = _normalize_wechat_stage_dict("chunshe_draft_package", draft_payload)
    title_candidates = _normalize_chunshe_title_candidates(topic, draft_package)
    draft_body = extract_chunshe_video_draft_body(draft_package)
    if not draft_body.strip():
        raise RuntimeError("chunshe draft package returned empty body")
    draft_body = ensure_chunshe_video_core_lines(
        draft_body,
        topic,
        output_type=output_type,
    )
    pinned_comment = build_chunshe_video_pinned_comment(topic=topic, source_pack=source_pack)
    reply_template = build_chunshe_video_reply_template(topic=topic)
    draft_text = render_chunshe_video_markdown(
        title_candidates=title_candidates,
        body_text=draft_body,
        pinned_comment=pinned_comment,
        reply_template=reply_template,
    )
    draft_issues = _validate_chunshe_markdown(draft_text, topic)
    _write_json_file(
        topic_report_dir / "draft-package.json",
        {
            "entry_class": draft_package.get("entry_class"),
            "person": draft_package.get("person"),
            "recent_trigger": draft_package.get("recent_trigger"),
            "fear": draft_package.get("fear"),
            "real_buy_point": draft_package.get("real_buy_point"),
            "store_rule_primary": draft_package.get("store_rule_primary"),
            "narrative_ratio": draft_package.get("narrative_ratio"),
            "title_candidates": title_candidates,
            "draft_body": draft_body,
            "pinned_comment": pinned_comment,
            "reply_template": reply_template,
            "draft_markdown": draft_text,
            "draft_issues": draft_issues,
        },
    )

    if mode == "快速" and not draft_issues:
        skipped_polish = {
            "passed": True,
            "issues": [],
            "hard_issues": [],
            "soft_issues": [],
            "removed_or_softened_claims": [],
            "retry_count": 0,
            "quote_candidate": None,
            "quote_candidates": [],
            "quote_used": False,
            "intro_rewrite_used": False,
            "warnings": [],
            "skipped": True,
            "reason": "fast_mode_local_validation_passed",
        }
        skipped_raw = json.dumps(skipped_polish, ensure_ascii=False, indent=2)
        polish_started_at = dt.datetime.now(dt.timezone.utc)
        _append_chunshe_stage_timing(
            stage_timings,
            stage_name=f"{stage_prefix}:polish_package",
            started_at=polish_started_at,
            output_text=skipped_raw,
            result_status="skipped_fast_mode",
        )
        _write_json_file(topic_report_dir / "polish-package.model.json", skipped_polish)
        _write_json_file(topic_report_dir / "polish-package.json", skipped_polish)
        _write_text_file(topic_report_dir / "polish-package.raw.txt", skipped_raw)
        _write_text_file(topic_report_dir / "final.raw.txt", draft_text)
        return {
            "status": "success",
            "topic": topic,
            "content": draft_text,
            "draft_package": draft_package,
            "polish_package": skipped_polish,
            "retry_count": 0,
            "quote_used": False,
            "warnings": [],
        }

    quote_candidate: dict[str, Any] | None = None
    quote_candidates: list[dict[str, Any]] = []
    if quote_enabled:
        quote_candidates = select_chunshe_quote_candidates(
            entry_class=str(topic.get("entry_class") or ""),
            topic=topic,
            explicit_theme=explicit_quote_theme,
            limit=3,
        )
        quote_candidate = quote_candidates[0] if quote_candidates else None

    retry_budget = _chunshe_retry_budget(mode)
    retry_count = 0
    retry_issues: list[str] = []
    removed_or_softened_claims: list[str] = []
    final_text = draft_text
    polish_package: dict[str, Any] = {}
    quote_used = False
    warnings: list[str] = []

    while True:
        polish_payload, _polish_raw, _polish_stderr = _run_chunshe_json_stage(
            stage_timings=stage_timings,
            stage_name=f"{stage_prefix}:polish_package",
            prompt=_build_chunshe_polish_package_prompt(
                markdown_text=final_text,
                topic=topic,
                role=role,
                output_type=output_type,
                mode=mode,
                source_pack=source_pack,
                quote_candidate=quote_candidate,
                retry_issues=retry_issues or None,
            ),
            model=model,
            codex_cli=codex_cli,
            timeout_sec=timeout_sec,
            retry_index=retry_count,
            raw_path=topic_report_dir / "polish-package.raw.txt",
            json_path=topic_report_dir / "polish-package.model.json",
        )
        polish_package = _normalize_wechat_stage_dict("chunshe_polish_package", polish_payload)
        candidate_text = normalize_chunshe_video_markdown(str(polish_package.get("final_markdown") or "").strip(), title_candidates)
        if candidate_text.strip():
            final_body = extract_chunshe_video_draft_body({"draft_markdown": candidate_text})
            final_body = ensure_chunshe_video_core_lines(
                final_body or extract_chunshe_video_draft_body({"draft_markdown": final_text}),
                topic,
                output_type=output_type,
            )
            final_text = render_chunshe_video_markdown(
                title_candidates=title_candidates,
                body_text=final_body,
                pinned_comment=pinned_comment,
                reply_template=reply_template,
            )
        model_issues = normalize_chunshe_video_polish_issues(polish_package.get("issues") or [])
        local_issues = _validate_chunshe_markdown(final_text, topic)
        merged_issues = list(dict.fromkeys([*model_issues, *local_issues]))
        hard_issues, soft_issues = _split_chunshe_issues(merged_issues)
        removed_or_softened_claims = [
            str(item or "").strip()
            for item in polish_package.get("removed_or_softened_claims") or []
            if str(item or "").strip()
        ]
        quote_used = bool(quote_candidate) and _normalize_key(final_text) != _normalize_key(draft_text)

        if not hard_issues and not soft_issues:
            warnings = []
            break
        if not hard_issues and retry_count >= retry_budget:
            warnings = soft_issues
            break
        if retry_count >= retry_budget:
            _write_json_file(
                topic_report_dir / "polish-package.json",
                {
                    "passed": False,
                    "issues": merged_issues,
                    "hard_issues": hard_issues,
                    "soft_issues": soft_issues,
                    "removed_or_softened_claims": removed_or_softened_claims,
                    "retry_count": retry_count,
                    "quote_candidate": quote_candidate,
                    "quote_candidates": quote_candidates[:1],
                    "quote_used": quote_used,
                    "intro_rewrite_used": False,
                },
            )
            _write_text_file(topic_report_dir / "final.raw.txt", final_text)
            raise RuntimeError("; ".join(hard_issues or merged_issues) or "chunshe polish package failed")

        retry_count += 1
        retry_issues = [*hard_issues, *soft_issues][:6]
        if mode == "严格" and retry_count >= 2:
            quote_candidate = None
        elif any("金句" in item or "结尾" in item or "生硬" in item for item in [*hard_issues, *soft_issues]):
            quote_candidate = None

        _write_json_file(
            topic_report_dir / "polish-package.json",
            {
                "passed": False,
                "issues": merged_issues,
                "hard_issues": hard_issues,
                "soft_issues": soft_issues,
                    "removed_or_softened_claims": removed_or_softened_claims,
                    "retry_count": retry_count,
                    "quote_candidate": quote_candidate,
                    "quote_candidates": quote_candidates[:1],
                    "quote_used": quote_used,
                    "intro_rewrite_used": False,
                },
            )
        _write_text_file(topic_report_dir / "final.raw.txt", final_text)

    polish_package["issues"] = warnings
    polish_package["passed"] = True
    _write_json_file(
        topic_report_dir / "polish-package.json",
        {
            "passed": True,
            "issues": warnings,
            "hard_issues": [],
            "soft_issues": warnings,
            "removed_or_softened_claims": removed_or_softened_claims,
            "retry_count": retry_count,
            "quote_candidate": quote_candidate,
            "quote_candidates": quote_candidates[:1],
            "quote_used": quote_used,
            "intro_rewrite_used": False,
            "warnings": warnings,
        },
    )
    _write_text_file(topic_report_dir / "final.raw.txt", final_text)

    return {
        "status": "success",
        "topic": topic,
        "content": final_text,
        "draft_package": draft_package,
        "polish_package": polish_package,
        "retry_count": retry_count,
        "quote_used": quote_used,
        "warnings": warnings,
    }


def _run_chunshe_staged_task(
    *,
    skill: SkillDefinition,
    brief: str,
    platform: str,
    date_str: str,
    model: str,
    event_ref: str,
    source_ref: str,
    timeout_sec: int,
    codex_cli: str,
    context_files_used: list[str],
    context_plan: dict[str, Any],
    context_warnings: list[str],
    context_errors: list[str],
    context_prompt: str,
    started: float,
    dry_run: bool,
) -> dict[str, Any]:
    config = _extract_chunshe_runtime_config(brief)
    seed_examples = match_chunshe_topic_seed_examples(
        str(config.get("seed_keyword") or ""),
        str(config.get("entry_class") or ""),
        limit=12,
    )
    if str(config.get("explicit_topic") or "").strip():
        seed_examples = [_build_chunshe_manual_topic(config)]
    elif not seed_examples:
        seed_examples = [dict(item) for item in load_chunshe_topic_seed_pool()[:12]]
    recent_history = collect_recent_chunshe_history(lookback_days=30)
    recent_history_summary = summarize_recent_history(recent_history, limit=12)
    source_materials = {
        "seed_keyword": config.get("seed_keyword"),
        "explicit_topic": config.get("explicit_topic"),
        "entry_class": config.get("entry_class"),
        "role": config.get("role"),
        "output_type": config.get("output_type"),
        "mode": config.get("mode"),
        "batch_count": config.get("batch_count"),
        "quote_enabled": config.get("quote_enabled"),
        "quote_theme_requested": config.get("explicit_quote_theme"),
        "high_boundary": config.get("high_boundary"),
        "recent_history": recent_history_summary,
        "seed_examples": seed_examples[:8],
    }
    worker_plan = {
        "topic_target_count": int(config.get("batch_count") or 1),
        "output_type": config.get("output_type") or "精简发布版",
        "role": config.get("role") or "李可",
        "mode": config.get("mode") or "平衡",
        "stages": list(CHUNSHE_SHARED_STAGES),
    }
    merged_runtime_contexts = list(dict.fromkeys(context_plan.get("context_files_merged") or []))
    preview_candidates = seed_examples[: max(5, int(config.get("batch_count") or 1))]
    selected_preview, rejected_preview = dedupe_and_pick_chunshe_topics(
        preview_candidates,
        recent_history,
        count=max(1, int(config.get("batch_count") or 1)),
    )
    selected_preview = _prefer_chunshe_seed_consistent_topics(
        selected_topics=selected_preview,
        topic_candidates=preview_candidates,
        config=config,
    )
    selected_preview = [enrich_chunshe_video_topic(item) for item in selected_preview]

    pipeline_mode = "staged_single_worker" if int(config.get("batch_count") or 1) == 1 else "staged_serial_topics"
    if dry_run:
        elapsed = int((time.time() - started) * 1000)
        return _build_chunshe_result_payload(
            status="error" if context_errors else "success",
            skill=skill,
            platform=platform,
            date_str=date_str,
            model=model,
            event_ref=event_ref,
            source_ref=source_ref,
            context_files_used=context_files_used,
            context_plan=context_plan,
            merged_runtime_contexts=merged_runtime_contexts,
            context_warnings=context_warnings,
            context_errors=context_errors,
            saved_files=[],
            full_text="",
            elapsed_ms=elapsed,
            errors=list(context_errors),
            worker_plan=worker_plan,
            source_materials=source_materials,
            selected_topics=selected_preview,
            topic_results=[],
            warnings=[],
            stage_timings=[],
            dry_run=True,
            pipeline_mode=pipeline_mode,
            extra={
                "topic_pool_preview": preview_candidates,
                "rejected_topics": rejected_preview[:12],
            },
        )

    if context_errors:
        elapsed = int((time.time() - started) * 1000)
        return _build_chunshe_result_payload(
            status="error",
            skill=skill,
            platform=platform,
            date_str=date_str,
            model=model,
            event_ref=event_ref,
            source_ref=source_ref,
            context_files_used=context_files_used,
            context_plan=context_plan,
            merged_runtime_contexts=merged_runtime_contexts,
            context_warnings=context_warnings,
            context_errors=context_errors,
            saved_files=[],
            full_text="",
            elapsed_ms=elapsed,
            errors=list(context_errors),
            worker_plan=worker_plan,
            source_materials=source_materials,
            selected_topics=[],
            topic_results=[],
            warnings=[],
            stage_timings=[],
            pipeline_mode=pipeline_mode,
        )

    cli_path = codex_cli.strip() or resolve_codex_cli()
    task_id = _chunshe_generation_task_id(date_str)
    report_dir = _chunshe_generation_report_dir(date_str, task_id)
    report_dir_text = _report_dir_text(report_dir)
    stage_timings: list[dict[str, Any]] = []
    warnings: list[str] = []
    selected_topics: list[dict[str, Any]] = []
    topic_results: list[dict[str, Any]] = []
    errors: list[str] = []
    saved_files: list[str] = []
    full_text = ""
    status = "success"

    _write_json_file(report_dir / "source-materials.json", source_materials)

    try:
        source_pack_started_at = dt.datetime.now(dt.timezone.utc)
        source_pack_root = _build_local_chunshe_source_pack(config=config, source_materials=source_materials)
        source_pack = dict(source_pack_root.get("source_pack") or {})
        source_pack_raw = json.dumps(source_pack_root, ensure_ascii=False, indent=2)
        _append_chunshe_stage_timing(
            stage_timings,
            stage_name="chunshe:source_pack",
            started_at=source_pack_started_at,
            output_text=source_pack_raw,
            result_status="local_source_pack",
        )
        _write_json_file(report_dir / "source-pack.model.json", source_pack_root)
        _write_json_file(report_dir / "source-pack.json", source_pack_root)
        _write_text_file(report_dir / "source-pack.raw.txt", source_pack_raw)

        topic_plan_payload: dict[str, Any] | None = None
        topic_candidates: list[dict[str, Any]] = []
        used_model_topic_planner = False
        if str(config.get("explicit_topic") or "").strip():
            planner_started_at = dt.datetime.now(dt.timezone.utc)
            topic_candidates = [_build_chunshe_manual_topic(config)]
            topic_plan_payload = {
                "seed_keyword": config.get("seed_keyword"),
                "entry_class": config.get("entry_class"),
                "topic_pool": topic_candidates,
                "rejected_topics": [],
                "source": "manual_topic",
            }
            _append_chunshe_stage_timing(
                stage_timings,
                stage_name="chunshe:topic_planner",
                started_at=planner_started_at,
                output_text=json.dumps(topic_plan_payload, ensure_ascii=False),
                result_status="manual",
            )
        else:
            topic_candidates = [dict(item) for item in seed_examples[:12]]
            need_model_expansion = len(topic_candidates) < 3 or (
                int(config.get("batch_count") or 1) >= 7 and _is_chunshe_broad_seed_keyword(str(config.get("seed_keyword") or ""))
            )
            if need_model_expansion:
                try:
                    topic_plan_payload, topic_plan_raw, _topic_stderr = _run_chunshe_json_stage(
                        stage_timings=stage_timings,
                        stage_name="chunshe:topic_planner",
                        prompt=_build_chunshe_topic_planner_prompt(
                            brief=brief,
                            config=config,
                            source_pack=source_pack,
                            seed_examples=seed_examples[:8],
                            recent_history=recent_history_summary,
                        ),
                        model=model or DEFAULT_MODEL,
                        codex_cli=cli_path,
                        timeout_sec=timeout_sec,
                        raw_path=report_dir / "topic-planner.raw.txt",
                        json_path=report_dir / "topic-planner.json",
                    )
                    planned_candidates = _normalize_chunshe_topic_pool(topic_plan_payload, config=config)
                    if planned_candidates:
                        topic_candidates = planned_candidates + topic_candidates
                        used_model_topic_planner = True
                    _write_text_file(report_dir / "topic-selection.raw.txt", topic_plan_raw)
                except Exception as exc:
                    context_warnings.append(f"topic planner fallback used: {exc}")
            if not topic_plan_payload:
                planner_started_at = dt.datetime.now(dt.timezone.utc)
                topic_plan_payload = {
                    "seed_keyword": config.get("seed_keyword"),
                    "entry_class": config.get("entry_class"),
                    "topic_pool": topic_candidates,
                    "rejected_topics": [],
                    "source": "local_topic_engine",
                }
                _append_chunshe_stage_timing(
                    stage_timings,
                    stage_name="chunshe:topic_planner",
                    started_at=planner_started_at,
                    output_text=json.dumps(topic_plan_payload, ensure_ascii=False),
                    result_status="local_seed_pool",
                )
        if not topic_candidates:
            topic_candidates = [_build_chunshe_manual_topic(config)]

        dedupe_started_at = dt.datetime.now(dt.timezone.utc)
        deduped_topics, rejected_topics = dedupe_and_pick_chunshe_topics(
            topic_candidates,
            recent_history,
            count=max(1, int(config.get("batch_count") or 1)),
        )
        _append_chunshe_stage_timing(
            stage_timings,
            stage_name="chunshe:topic_dedupe",
            started_at=dedupe_started_at,
            output_text=json.dumps({"selected_topics": deduped_topics, "rejected_topics": rejected_topics}, ensure_ascii=False),
        )

        pick_started_at = dt.datetime.now(dt.timezone.utc)
        selected_topics = _prefer_chunshe_seed_consistent_topics(
            selected_topics=deduped_topics,
            topic_candidates=topic_candidates,
            config=config,
        )
        if not selected_topics:
            selected_topics = [dict(item) for item in topic_candidates[: max(1, int(config.get("batch_count") or 1))]]
        selected_topics = [enrich_chunshe_video_topic(item) for item in selected_topics]
        _append_chunshe_stage_timing(
            stage_timings,
            stage_name="chunshe:topic_pick",
            started_at=pick_started_at,
            output_text=json.dumps(selected_topics, ensure_ascii=False),
        )

        _write_json_file(
            report_dir / "topic-selection.json",
            {
                "seed_keyword": config.get("seed_keyword"),
                "entry_class": config.get("entry_class"),
                "used_model_topic_planner": used_model_topic_planner,
                "topic_pool": topic_candidates,
                "selected_topics": selected_topics,
                "rejected_topics": rejected_topics,
            },
        )

        if str(config.get("output_type") or "") == "标题池":
            title_pool_text = render_chunshe_title_pool(
                seed_keyword=str(config.get("seed_keyword") or ""),
                role=str(config.get("role") or "李可"),
                entry_class=str(config.get("entry_class") or ""),
                topics=selected_topics,
            )
            saved_files = _save_generated_files(text=title_pool_text, skill=skill, platform=platform, date_str=date_str)
            full_text = _read_primary_saved_file(saved_files) or title_pool_text
        else:
            topic_docs: list[dict[str, Any]] = []
            for index, topic in enumerate(selected_topics, start=1):
                topic_report_dir = report_dir / f"{index:02d}-{_sanitize_segment(str(topic.get('topic_id') or f'topic-{index}'), fallback=f'topic-{index}')}"
                topic_report_dir.mkdir(parents=True, exist_ok=True)
                try:
                    result = _run_chunshe_single_topic(
                        role=str(config.get("role") or "李可"),
                        output_type=str(config.get("output_type") or "精简发布版"),
                        brief=brief,
                        topic=topic,
                        mode=str(config.get("mode") or "平衡"),
                        quote_enabled=bool(config.get("quote_enabled")),
                        explicit_quote_theme=str(config.get("explicit_quote_theme") or ""),
                        source_pack=source_pack,
                        model=model or DEFAULT_MODEL,
                        codex_cli=cli_path,
                        timeout_sec=timeout_sec,
                        topic_report_dir=topic_report_dir,
                        topic_index=index,
                        stage_timings=stage_timings,
                    )
                    output_path = _build_chunshe_output_path(
                        date_str=date_str,
                        role=str(config.get("role") or "李可"),
                        output_type=str(config.get("output_type") or "精简发布版"),
                        topic=topic,
                        index=index,
                    )
                    topic_docs.append({"path": output_path, "content": result["content"]})
                    topic_results.append(
                        {
                            "topic_id": topic.get("topic_id"),
                            "topic_title": topic.get("topic_title"),
                            "path": output_path,
                            "retry_count": result.get("retry_count"),
                            "quote_used": result.get("quote_used"),
                            "warnings": result.get("warnings") or [],
                        }
                    )
                    topic_title = str(topic.get("topic_title") or topic.get("topic_id") or index).strip()
                    for warning in result.get("warnings") or []:
                        warnings.append(f"{topic_title}: {warning}")
                except Exception as exc:
                    errors.append(f"{str(topic.get('topic_title') or topic.get('topic_id') or index).strip()}: {exc}")

            if topic_docs:
                contract_text = _render_chunshe_file_contract(date_str=date_str, topic_docs=topic_docs)
                _write_text_file(report_dir / "chunshe-files-contract.txt", contract_text)
                saved_files = _save_generated_files(text=contract_text, skill=skill, platform=platform, date_str=date_str)
                full_text = _read_primary_saved_file(saved_files)

        status = "success"
        if errors and saved_files:
            status = "partial_error"
        elif errors:
            status = "error"
    except Exception as exc:
        errors.append(str(exc))
        status = "error"

    elapsed = int((time.time() - started) * 1000)
    run_summary = {
        "task_id": task_id,
        "status": status,
        "date": date_str,
        "skill_id": skill.skill_id,
        "output_type": str(config.get("output_type") or ""),
        "mode": str(config.get("mode") or ""),
        "seed_keyword": str(config.get("seed_keyword") or ""),
        "report_dir": report_dir_text,
        "selected_topics": selected_topics,
        "topic_results": topic_results,
        "saved_files": saved_files,
        "warnings": warnings,
        "errors": errors,
        "elapsed_ms": elapsed,
    }
    _write_json_file(report_dir / "stage-timings.json", stage_timings)
    _write_json_file(report_dir / "run-summary.json", run_summary)

    return _build_chunshe_result_payload(
        status=status,
        skill=skill,
        platform=platform,
        date_str=date_str,
        model=model,
        event_ref=event_ref,
        source_ref=source_ref,
        context_files_used=context_files_used,
        context_plan=context_plan,
        merged_runtime_contexts=merged_runtime_contexts,
        context_warnings=context_warnings,
        context_errors=context_errors,
        saved_files=saved_files,
        full_text=full_text,
        elapsed_ms=elapsed,
        errors=errors,
        worker_plan=worker_plan,
        source_materials=source_materials,
        selected_topics=selected_topics,
        topic_results=topic_results,
        report_dir=report_dir_text,
        warnings=warnings,
        stage_timings=stage_timings,
        pipeline_mode="staged_single_worker" if len(selected_topics) <= 1 else "staged_serial_topics",
    )


def _build_prompt(
    *,
    skill: SkillDefinition,
    platform: str,
    date_str: str,
    brief: str,
    context_prompt: str = "",
) -> str:
    skill_content = _read_text(skill.path)
    if _is_execution_skill(skill):
        return (
            "你是仓库内的执行型技能助手。\n"
            "你必须先阅读【技能文档】并检查输入是否合法，然后直接执行需要的脚本或命令。\n"
            "不要输出 FILES_JSON，不要生成额外 markdown 文件，不要伪造已执行结果。\n"
            "如果执行失败，返回失败原因、已检查的路径和建议下一步；如果执行成功，返回简洁执行摘要与关键产物路径。\n"
            f"目标平台：{platform}\n"
            f"{context_prompt}"
            f"【技能ID】\n{skill.skill_id}\n\n"
            f"【技能文档路径】\n{skill.path.as_posix()}\n\n"
            f"【技能文档】\n{skill_content}\n\n"
            f"【用户需求】\n{brief.strip()}\n"
        )

    target_dir = f"02-内容生产/{platform}/生成内容/{date_str}/"
    return (
        "你是仓库内的内容生产执行助手。\n"
        "你必须严格依据【技能文档】执行，不要输出解释过程，不要输出多余寒暄。\n"
        f"目标平台：{platform}\n"
        f"目标落库目录：{target_dir}\n"
        "输出要求：\n"
        "1) 优先使用 FILES_JSON + FILE 块输出多文件结果；\n"
        "2) 若只输出单文件，也必须输出完整 markdown 正文；\n"
        "3) 所有示例和结论保持可发布，不编造来源。\n\n"
        f"{context_prompt}"
        f"【技能ID】\n{skill.skill_id}\n\n"
        f"【技能文档路径】\n{skill.path.as_posix()}\n\n"
        f"【技能文档】\n{skill_content}\n\n"
        f"【用户需求】\n{brief.strip()}\n"
    )


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


def _run_codex(prompt: str, *, model: str, codex_cli: str, timeout_sec: int) -> tuple[str, str]:
    args = [codex_cli, "exec", "--json", "--skip-git-repo-check", "-m", model, "-"]
    completed = subprocess.run(
        args,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        timeout=max(30, timeout_sec),
    )
    text, parse_error = _parse_codex_json_lines(completed.stdout or "")
    if completed.returncode != 0:
        err = parse_error or (completed.stderr or "").strip() or f"codex exited with {completed.returncode}"
        raise RuntimeError(err)
    if not text.strip():
        raise RuntimeError("codex returned empty output")
    return text, (completed.stderr or "").strip()


def _save_generated_files(
    *,
    text: str,
    skill: SkillDefinition,
    platform: str,
    date_str: str,
) -> list[str]:
    output_dir = OUTPUT_ROOT / _sanitize_segment(platform) / "生成内容" / date_str
    output_dir.mkdir(parents=True, exist_ok=True)

    candidate_files = _coerce_markdown_files(text)
    written: list[str] = []
    used_names: set[str] = set()

    def allocate(name: str) -> Path:
        base_name = _sanitize_filename(name)
        stem = Path(base_name).stem
        suffix = Path(base_name).suffix
        final_name = base_name
        idx = 2
        while final_name in used_names or (output_dir / final_name).exists():
            final_name = f"{stem}-{idx}{suffix}"
            idx += 1
        used_names.add(final_name)
        return output_dir / final_name

    if candidate_files:
        for item in candidate_files:
            rel_path = str(item.get("path") or "").strip()
            content = str(item.get("content") or "").rstrip()
            if not content:
                continue
            target = allocate(Path(rel_path.replace("\\", "/")).name)
            target.write_text(content + "\n", encoding="utf-8")
            written.append(target.relative_to(REPO_ROOT).as_posix())
        if written:
            return written

    title = _extract_short_title(text, fallback=skill.name or skill.skill_id)
    short = _sanitize_segment(title, fallback="生成文案")[:24]
    filename = f"{_sanitize_segment(skill.skill_id, fallback='skill')}-{_today_compact()}-{short}.md"
    target = allocate(filename)
    target.write_text(text.rstrip() + "\n", encoding="utf-8")
    written.append(target.relative_to(REPO_ROOT).as_posix())
    return written


def _read_primary_saved_file(saved_files: list[str]) -> str:
    if not saved_files:
        return ""
    material: list[dict[str, str]] = []
    for rel in saved_files:
        rel_text = str(rel or "").strip()
        if not rel_text:
            continue
        path = REPO_ROOT / rel_text
        if not path.exists() or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not content:
            continue
        material.append({"path": rel_text, "content": content})
    primary = _select_primary_file(material)
    if not primary:
        return ""
    return _render_full_text_for_reply(str(primary.get("content") or ""))


def _run_prompt_normalizer_script(
    *,
    brief: str,
    timeout_sec: int,
) -> dict[str, Any]:
    target = str(brief or "").strip()
    if not target:
        raise RuntimeError("wechat_prompt_normalize target is empty")

    script_path = REPO_ROOT / "06-工具" / "scripts" / "wechat_prompt_normalizer.py"
    if not script_path.exists():
        raise RuntimeError(f"missing script: {script_path}")

    completed = subprocess.run(
        [sys.executable, str(script_path), target],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        timeout=max(30, timeout_sec),
    )
    stdout_text = str(completed.stdout or "").strip()
    stderr_text = str(completed.stderr or "").strip()

    payload: dict[str, Any] | None = None
    if stdout_text:
        try:
            parsed = json.loads(stdout_text)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed

    if completed.returncode != 0:
        error = ""
        if isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, list) and errors:
                error = str(errors[0] or "").strip()
        if not error:
            error = stderr_text or stdout_text or f"prompt normalizer exited with {completed.returncode}"
        raise RuntimeError(error)

    if not isinstance(payload, dict) or str(payload.get("status") or "").strip().lower() != "success":
        raise RuntimeError(stderr_text or stdout_text or "invalid prompt normalizer response")
    return payload


def _run_xhs_prompt_normalizer_script(
    *,
    brief: str,
    timeout_sec: int,
) -> dict[str, Any]:
    target = str(brief or "").strip()
    if not target:
        raise RuntimeError("xhs_prompt_normalize target is empty")

    script_path = REPO_ROOT / "06-工具" / "scripts" / "xhs_prompt_normalizer.py"
    if not script_path.exists():
        raise RuntimeError(f"missing script: {script_path}")

    completed = subprocess.run(
        [sys.executable, str(script_path), target],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        timeout=max(30, timeout_sec),
    )
    stdout_text = str(completed.stdout or "").strip()
    stderr_text = str(completed.stderr or "").strip()
    payload: dict[str, Any] | None = None
    if stdout_text:
        try:
            parsed = json.loads(stdout_text)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
    if completed.returncode != 0:
        raise RuntimeError(stderr_text or stdout_text or f"xhs prompt normalizer exited with {completed.returncode}")
    if not isinstance(payload, dict) or str(payload.get("status") or "").strip().lower() != "success":
        raise RuntimeError(stderr_text or stdout_text or "invalid xhs prompt normalizer response")
    return payload


def _run_xhs_image_generator_script(
    *,
    brief: str,
    timeout_sec: int,
) -> dict[str, Any]:
    target = str(brief or "").strip()
    if not target:
        raise RuntimeError("xhs_image target is empty")

    script_path = REPO_ROOT / "06-工具" / "scripts" / "xhs_image_generator.py"
    if not script_path.exists():
        raise RuntimeError(f"missing script: {script_path}")

    completed = subprocess.run(
        [sys.executable, str(script_path), target],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        timeout=max(30, timeout_sec),
    )
    stdout_text = str(completed.stdout or "").strip()
    stderr_text = str(completed.stderr or "").strip()
    payload: dict[str, Any] | None = None
    if stdout_text:
        try:
            parsed = json.loads(stdout_text)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
    if completed.returncode != 0:
        raise RuntimeError(stderr_text or stdout_text or f"xhs image generator exited with {completed.returncode}")
    if not isinstance(payload, dict) or str(payload.get("status") or "").strip().lower() != "success":
        raise RuntimeError(stderr_text or stdout_text or "invalid xhs image generator response")
    return payload


def run_skill_task(
    *,
    skill_id: str,
    brief: str,
    platform: str = "",
    model: str = DEFAULT_MODEL,
    event_ref: str = "",
    source_ref: str = "",
    date_str: str = "",
    timeout_sec: int = 1800,
    codex_cli: str = "",
    context_files: list[str] | None = None,
    dry_run: bool = False,
    concurrency: int = 0,
) -> dict[str, Any]:
    started = time.time()
    registry = build_skill_registry()
    skill = resolve_skill(registry, skill_id)

    resolved_platform = _resolve_platform_segment(skill, platform)
    resolved_date = str(date_str or _today()).strip() or _today()
    context_plan = build_skill_context_plan(
        skill_id=skill.skill_id,
        brief=brief,
        platform=resolved_platform,
        context_files=context_files,
    )
    merged_context_files = list(context_plan.get("context_files_merged") or [])
    context_errors = list(context_plan.get("context_errors") or [])
    if skill.skill_id == "chunshe_wj":
        context_files_used = []
        context_warnings = []
        context_prompt = ""
    else:
        context_files_used, context_warnings, context_prompt = _prepare_context_blocks(merged_context_files)
    for warning in list(context_plan.get("context_warnings") or []):
        if warning not in context_warnings:
            context_warnings.append(warning)

    if not brief.strip():
        raise ValueError("brief is empty")

    if skill.skill_id == "wechat":
        return _run_wechat_staged_task(
            skill=skill,
            brief=brief,
            platform=resolved_platform,
            date_str=resolved_date,
            model=model,
            event_ref=event_ref,
            source_ref=source_ref,
            timeout_sec=timeout_sec,
            codex_cli=codex_cli,
            context_files_used=context_files_used,
            context_plan=context_plan,
            context_warnings=context_warnings,
            context_errors=context_errors,
            context_prompt=context_prompt,
            started=started,
            dry_run=dry_run,
            concurrency=concurrency,
        )

    if skill.skill_id == "xhs":
        return _run_xhs_staged_task(
            skill=skill,
            brief=brief,
            platform=resolved_platform,
            date_str=resolved_date,
            model=model,
            event_ref=event_ref,
            source_ref=source_ref,
            timeout_sec=timeout_sec,
            codex_cli=codex_cli,
            context_files_used=context_files_used,
            context_plan=context_plan,
            context_warnings=context_warnings,
            context_errors=context_errors,
            context_prompt=context_prompt,
            started=started,
            dry_run=dry_run,
        )

    if skill.skill_id == "chunshe_wj":
        return _run_chunshe_staged_task(
            skill=skill,
            brief=brief,
            platform=resolved_platform,
            date_str=resolved_date,
            model=model,
            event_ref=event_ref,
            source_ref=source_ref,
            timeout_sec=timeout_sec,
            codex_cli=codex_cli,
            context_files_used=context_files_used,
            context_plan=context_plan,
            context_warnings=context_warnings,
            context_errors=context_errors,
            context_prompt=context_prompt,
            started=started,
            dry_run=dry_run,
        )

    if dry_run:
        elapsed = int((time.time() - started) * 1000)
        status = "error" if context_errors else "success"
        return {
            "status": status,
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "platform": resolved_platform,
            "date": resolved_date,
            "model": model or DEFAULT_MODEL,
            "event_ref": event_ref,
            "source_ref": source_ref,
            "context_files_used": context_files_used,
            "context_files_auto": list(context_plan.get("context_files_auto") or []),
            "context_files_merged": merged_context_files,
            "context_warnings": context_warnings,
            "context_errors": context_errors,
            "saved_files": [],
            "full_text": "",
            "stderr": "",
            "elapsed_ms": elapsed,
            "errors": list(context_errors),
            "dry_run": True,
        }

    if context_errors:
        elapsed = int((time.time() - started) * 1000)
        return {
            "status": "error",
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "platform": resolved_platform,
            "date": resolved_date,
            "model": model or DEFAULT_MODEL,
            "event_ref": event_ref,
            "source_ref": source_ref,
            "context_files_used": context_files_used,
            "context_files_auto": list(context_plan.get("context_files_auto") or []),
            "context_files_merged": merged_context_files,
            "context_warnings": context_warnings,
            "context_errors": context_errors,
            "saved_files": [],
            "full_text": "",
            "stderr": "",
            "elapsed_ms": elapsed,
            "errors": list(context_errors),
            "dry_run": False,
        }

    if skill.skill_id == "wechat_prompt_normalize":
        payload = _run_prompt_normalizer_script(
            brief=brief,
            timeout_sec=timeout_sec,
        )
        processed_files = payload.get("processed_files") if isinstance(payload, dict) else []
        saved_files = [
            str(item.get("path") or "").strip()
            for item in processed_files
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        elapsed = int((time.time() - started) * 1000)
        return {
            "status": "success",
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "platform": resolved_platform,
            "date": resolved_date,
            "model": model or DEFAULT_MODEL,
            "event_ref": event_ref,
            "source_ref": source_ref,
            "context_files_used": context_files_used,
            "context_files_auto": list(context_plan.get("context_files_auto") or []),
            "context_files_merged": merged_context_files,
            "context_warnings": context_warnings,
            "context_errors": context_errors,
            "saved_files": saved_files,
            "full_text": json.dumps(payload, ensure_ascii=False, indent=2),
            "stderr": "",
            "elapsed_ms": elapsed,
            "errors": [],
        }

    if skill.skill_id == "xhs_prompt_normalize":
        payload = _run_xhs_prompt_normalizer_script(
            brief=brief,
            timeout_sec=timeout_sec,
        )
        processed_files = payload.get("processed_files") if isinstance(payload, dict) else []
        saved_files = [
            str(item.get("path") or "").strip()
            for item in processed_files
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        elapsed = int((time.time() - started) * 1000)
        return {
            "status": "success",
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "platform": resolved_platform,
            "date": resolved_date,
            "model": model or DEFAULT_MODEL,
            "event_ref": event_ref,
            "source_ref": source_ref,
            "context_files_used": context_files_used,
            "context_files_auto": list(context_plan.get("context_files_auto") or []),
            "context_files_merged": merged_context_files,
            "context_warnings": context_warnings,
            "context_errors": context_errors,
            "saved_files": saved_files,
            "full_text": json.dumps(payload, ensure_ascii=False, indent=2),
            "stderr": "",
            "elapsed_ms": elapsed,
            "errors": [],
        }

    if skill.skill_id == "xhs_image":
        payload = _run_xhs_image_generator_script(
            brief=brief,
            timeout_sec=timeout_sec,
        )
        processed_files = payload.get("processed_files") if isinstance(payload, dict) else []
        saved_files = [
            str(item.get("path") or "").strip()
            for item in processed_files
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        elapsed = int((time.time() - started) * 1000)
        return {
            "status": "success",
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "platform": resolved_platform,
            "date": resolved_date,
            "model": model or DEFAULT_MODEL,
            "event_ref": event_ref,
            "source_ref": source_ref,
            "context_files_used": context_files_used,
            "context_files_auto": list(context_plan.get("context_files_auto") or []),
            "context_files_merged": merged_context_files,
            "context_warnings": context_warnings,
            "context_errors": context_errors,
            "saved_files": saved_files,
            "full_text": json.dumps(payload, ensure_ascii=False, indent=2),
            "stderr": "",
            "elapsed_ms": elapsed,
            "errors": [],
        }

    cli_path = codex_cli.strip() or resolve_codex_cli()
    prompt = _build_prompt(
        skill=skill,
        platform=resolved_platform,
        date_str=resolved_date,
        brief=brief,
        context_prompt=context_prompt,
    )

    generated_text, stderr_text = _run_codex(
        prompt,
        model=model or DEFAULT_MODEL,
        codex_cli=cli_path,
        timeout_sec=timeout_sec,
    )
    if _is_execution_skill(skill):
        saved_files: list[str] = []
        full_text = _render_full_text_for_reply(generated_text)
    else:
        saved_files = _save_generated_files(
            text=generated_text,
            skill=skill,
            platform=resolved_platform,
            date_str=resolved_date,
        )
        full_text = _read_primary_saved_file(saved_files) or _render_full_text_for_reply(generated_text)

    elapsed = int((time.time() - started) * 1000)
    return {
        "status": "success",
        "skill_id": skill.skill_id,
        "skill_name": skill.name,
        "skill_path": skill.path.as_posix(),
        "platform": resolved_platform,
        "date": resolved_date,
        "model": model or DEFAULT_MODEL,
        "event_ref": event_ref,
        "source_ref": source_ref,
        "context_files_used": context_files_used,
        "context_files_auto": list(context_plan.get("context_files_auto") or []),
        "context_files_merged": merged_context_files,
        "context_warnings": context_warnings,
        "context_errors": context_errors,
        "saved_files": saved_files,
        "full_text": full_text,
        "stderr": stderr_text,
        "elapsed_ms": elapsed,
        "errors": [],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run skill generation and save markdown outputs.")
    parser.add_argument("--skill-id", default="", help="Skill id or alias.")
    parser.add_argument("--brief", default="", help="Generation brief.")
    parser.add_argument("--platform", default="", help="Platform segment, e.g. 公众号/小红书/短视频.")
    parser.add_argument("--event-ref", default="", help="Trace event ref.")
    parser.add_argument("--source-ref", default="", help="Source trace id.")
    parser.add_argument("--date", default="", help="Target date YYYY-MM-DD. Default: today.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Codex model. Default: gpt-5.4")
    parser.add_argument("--timeout-sec", type=int, default=1800, help="Codex timeout seconds.")
    parser.add_argument("--codex-cli", default="", help="Override codex binary path.")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=0,
        help="Wechat content worker concurrency. 0 = use WECHAT_CONTENT_CONCURRENCY/default.",
    )
    parser.add_argument(
        "--context-file",
        action="append",
        default=[],
        help="Relative path to context file. Can be used multiple times.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan only, do not call codex or write files.")
    parser.add_argument("--list-skills", action="store_true", help="List discovered skills.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    registry = build_skill_registry()
    if args.list_skills:
        print(json.dumps({"skills": list_skills_payload(registry)}, ensure_ascii=False, indent=2))
        return 0

    try:
        result = run_skill_task(
            skill_id=args.skill_id,
            brief=args.brief,
            platform=args.platform,
            model=args.model,
            event_ref=args.event_ref,
            source_ref=args.source_ref,
            date_str=args.date,
            timeout_sec=max(30, args.timeout_sec),
            codex_cli=args.codex_cli,
            context_files=args.context_file,
            dry_run=args.dry_run,
            concurrency=args.concurrency,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        out = {
            "status": "error",
            "skill_id": args.skill_id,
            "platform": args.platform,
            "errors": [str(exc)],
            "saved_files": [],
            "full_text": "",
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
