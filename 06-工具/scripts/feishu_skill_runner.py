#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run skill-based content generation for Feishu messages.

Responsibilities:
- Discover skills from repository `skills/` and desktop-app `skills.json`.
- Resolve skill aliases (id/name/stem/relative path) to one canonical skill.
- Invoke Codex CLI with model `gpt-5.3-codex` (configurable).
- Parse `FILES_JSON + FILE` contract output when present.
- Persist generated markdown under:
  `02-内容生产/{平台}/生成内容/YYYY-MM-DD/`
"""

from __future__ import annotations

import argparse
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

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"
DESKTOP_SKILLS_JSON = REPO_ROOT / "06-工具" / "desktop-app" / "data" / "skills.json"
OUTPUT_ROOT = REPO_ROOT / "02-内容生产"

DEFAULT_MODEL = "gpt-5.3-codex"
FILE_JSON_START = "<!--FILES_JSON_START-->"
FILE_JSON_END = "<!--FILES_JSON_END-->"
FILE_BLOCK_START = "<!--FILE_START-->"
FILE_BLOCK_END = "<!--FILE_END-->"

DEFAULT_PLATFORM_BY_SKILL = {
    "wechat": "公众号",
    "公众号批量生产": "公众号",
    "xhs": "小红书",
    "小红书内容生产": "小红书",
    "短视频脚本生产": "短视频",
}


@dataclasses.dataclass(frozen=True)
class SkillDefinition:
    skill_id: str
    name: str
    path: Path
    aliases: tuple[str, ...]
    default_platform: str


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


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


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
    return (base / relative_path).resolve()


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


def build_skill_registry() -> SkillRegistry:
    by_id: dict[str, SkillDefinition] = {}

    def upsert(skill_id: str, name: str, path: Path, aliases: set[str]) -> None:
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
        )

    # 1) Desktop app skill mapping
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

    # 2) All local skill markdown files
    if SKILLS_ROOT.exists():
        for path in SKILLS_ROOT.rglob("*.md"):
            if not path.is_file():
                continue
            relative = path.relative_to(SKILLS_ROOT).as_posix()
            stem = path.stem
            skill_id = stem
            aliases = {stem, relative, path.as_posix()}
            # If it looks like known skill names, map to stable ids.
            normalized_stem = _normalize_key(stem)
            for known_id in ("wechat", "xhs"):
                if normalized_stem == _normalize_key(known_id):
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
        _normalize_key("wechat"): "公众号批量生产",
        _normalize_key("公众号"): "公众号批量生产",
        _normalize_key("xhs"): "小红书内容生产",
        _normalize_key("小红书"): "小红书内容生产",
        _normalize_key("shortvideo"): "短视频脚本生产",
        _normalize_key("短视频"): "短视频脚本生产",
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


def _build_prompt(*, skill: SkillDefinition, platform: str, date_str: str, brief: str) -> str:
    skill_content = _read_text(skill.path)
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
    dry_run: bool = False,
) -> dict[str, Any]:
    started = time.time()
    registry = build_skill_registry()
    skill = resolve_skill(registry, skill_id)

    resolved_platform = _sanitize_segment(platform or skill.default_platform or "通用")
    resolved_date = str(date_str or _today()).strip() or _today()

    if not brief.strip():
        raise ValueError("brief is empty")

    if dry_run:
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
            "saved_files": [],
            "full_text": "",
            "stderr": "",
            "elapsed_ms": elapsed,
            "errors": [],
            "dry_run": True,
        }

    cli_path = codex_cli.strip() or resolve_codex_cli()
    prompt = _build_prompt(skill=skill, platform=resolved_platform, date_str=resolved_date, brief=brief)

    generated_text, stderr_text = _run_codex(
        prompt,
        model=model or DEFAULT_MODEL,
        codex_cli=cli_path,
        timeout_sec=timeout_sec,
    )
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
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Codex model. Default: gpt-5.3-codex")
    parser.add_argument("--timeout-sec", type=int, default=1800, help="Codex timeout seconds.")
    parser.add_argument("--codex-cli", default="", help="Override codex binary path.")
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
            dry_run=args.dry_run,
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
