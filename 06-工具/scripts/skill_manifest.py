#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Load repo-local skill manifest and shared context helpers.

The manifest maps and alias caches are process-local. After changing
`skills/自有矩阵/skill-manifest.json` or related context routing rules,
restart long-running processes or call `clear_repo_skill_manifest_caches()`.
"""

from __future__ import annotations

import dataclasses
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_MANIFEST_PATH = REPO_ROOT / "skills" / "自有矩阵" / "skill-manifest.json"
BENCHMARK_REPORT_ROOT = REPO_ROOT / "03-素材库" / "对标链接库" / "分析报告"

QUOTE_THEME_TO_PATH = {
    "搞钱与生意": "03-素材库/金句库/01-搞钱与生意.md",
    "内容与增长": "03-素材库/金句库/02-内容与增长.md",
    "系统与执行": "03-素材库/金句库/03-系统与执行.md",
    "人性与沟通": "03-素材库/金句库/04-人性与沟通.md",
    "婚恋与家庭": "03-素材库/金句库/90-婚恋与家庭（次级）.md",
    "婚恋与家庭（次级）": "03-素材库/金句库/90-婚恋与家庭（次级）.md",
    "自我与哲学": "03-素材库/金句库/91-自我与哲学（次级）.md",
    "自我与哲学（次级）": "03-素材库/金句库/91-自我与哲学（次级）.md",
}

QUOTE_THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "搞钱与生意": ("搞钱", "生意", "成交", "转化", "客单价", "创业", "门店", "客户", "营收", "变现"),
    "内容与增长": ("内容", "增长", "流量", "选题", "标题", "分发", "账号", "传播", "获客"),
    "系统与执行": ("系统", "执行", "复盘", "方法", "流程", "结构", "清单", "动作", "习惯", "效率"),
    "人性与沟通": ("关系", "沟通", "情绪", "扫兴", "理解", "冲突", "亲密", "说话", "信任", "边界"),
    "婚恋与家庭": ("婚恋", "家庭", "伴侣", "父母", "婚姻", "孩子", "亲子"),
    "自我与哲学": ("自我", "人生", "意义", "哲学", "成长", "孤独", "内耗"),
}


@dataclasses.dataclass(frozen=True)
class RepoSkillEntry:
    skill_id: str
    name: str
    path: str
    aliases: tuple[str, ...]
    default_platform: str
    kind: str
    default_contexts: tuple[str, ...]

    @property
    def abs_path(self) -> Path:
        return (REPO_ROOT / self.path).resolve()


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except Exception:
            continue
    return default


@lru_cache(maxsize=1)
def load_repo_skill_entries() -> tuple[RepoSkillEntry, ...]:
    payload = _read_json(SKILL_MANIFEST_PATH, {"skills": []})
    skills = payload.get("skills") if isinstance(payload, dict) else []
    items: list[RepoSkillEntry] = []
    if not isinstance(skills, list):
        return tuple(items)

    for raw in skills:
        if not isinstance(raw, dict):
            continue
        skill_id = str(raw.get("skill_id") or "").strip()
        name = str(raw.get("name") or skill_id).strip()
        path = str(raw.get("path") or "").strip().replace("\\", "/")
        aliases = tuple(
            sorted(
                {
                    skill_id,
                    name,
                    path,
                    *[
                        str(item).strip()
                        for item in (raw.get("aliases") if isinstance(raw.get("aliases"), list) else [])
                        if str(item).strip()
                    ],
                }
            )
        )
        default_platform = str(raw.get("default_platform") or "").strip() or "通用"
        kind = str(raw.get("kind") or "").strip() or "content"
        default_contexts = tuple(
            dict.fromkeys(
                str(item).strip().replace("\\", "/")
                for item in (raw.get("default_contexts") if isinstance(raw.get("default_contexts"), list) else [])
                if str(item).strip()
            )
        )
        if not skill_id or not path:
            continue
        items.append(
            RepoSkillEntry(
                skill_id=skill_id,
                name=name,
                path=path,
                aliases=aliases,
                default_platform=default_platform,
                kind=kind,
                default_contexts=default_contexts,
            )
        )
    return tuple(items)


@lru_cache(maxsize=1)
def load_repo_skill_map() -> dict[str, RepoSkillEntry]:
    return {item.skill_id: item for item in load_repo_skill_entries()}


@lru_cache(maxsize=1)
def load_repo_skill_alias_map() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for item in load_repo_skill_entries():
        for alias in item.aliases:
            key = _normalize_key(alias)
            if key and key not in aliases:
                aliases[key] = item.skill_id
    return aliases


def clear_repo_skill_manifest_caches() -> None:
    """Clear process-local manifest caches for explicit reload flows."""
    load_repo_skill_entries.cache_clear()
    load_repo_skill_map.cache_clear()
    load_repo_skill_alias_map.cache_clear()


def get_repo_skill_entry(skill_id: str) -> RepoSkillEntry | None:
    return load_repo_skill_map().get(str(skill_id or "").strip())


def resolve_quote_theme_contexts(theme: str) -> list[str]:
    normalized = str(theme or "").strip()
    if not normalized:
        return []
    direct = QUOTE_THEME_TO_PATH.get(normalized)
    if direct:
        return ["03-素材库/金句库/00-索引.md", direct]

    lowered = _normalize_key(normalized)
    for key, path in QUOTE_THEME_TO_PATH.items():
        if lowered == _normalize_key(key):
            return ["03-素材库/金句库/00-索引.md", path]
    return []


def suggest_quote_theme(*texts: str) -> str:
    haystack = " ".join(str(item or "").strip() for item in texts if str(item or "").strip())
    if not haystack:
        return "系统与执行"
    scores: dict[str, int] = {}
    for theme, keywords in QUOTE_THEME_KEYWORDS.items():
        scores[theme] = sum(1 for keyword in keywords if keyword and keyword in haystack)
    best_theme = max(scores.items(), key=lambda item: item[1])[0]
    if scores.get(best_theme, 0) <= 0:
        return "系统与执行"
    return best_theme


def resolve_benchmark_report_contexts(reference: str) -> list[str]:
    raw = str(reference or "").strip().replace("\\", "/")
    if not raw:
        return []

    if raw.startswith("03-素材库/对标链接库/分析报告/") and raw.endswith(".md"):
        candidate = (REPO_ROOT / raw).resolve()
        if candidate.exists() and candidate.is_file():
            return [raw]
        return []

    candidate_path = (REPO_ROOT / raw).resolve()
    if candidate_path.exists() and candidate_path.is_file():
        try:
            rel = candidate_path.relative_to(REPO_ROOT).as_posix()
        except Exception:
            rel = ""
        if "/分析报告/" in rel:
            return [rel]
        if "/提取正文/" in rel:
            return _search_benchmark_report_matches(rel)

    return _search_benchmark_report_matches(raw)


def _search_benchmark_report_matches(reference: str) -> list[str]:
    if not BENCHMARK_REPORT_ROOT.exists():
        return []
    raw = str(reference or "").strip().replace("\\", "/")
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    stem = Path(raw).stem
    stem = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", stem)
    stem = re.sub(r"-分析$", "", stem)
    terms = [item for item in {stem, raw.split("/")[-1], raw} if item]
    roots = [BENCHMARK_REPORT_ROOT]
    if date_match:
        roots.insert(0, BENCHMARK_REPORT_ROOT / date_match.group(1))

    for root in roots:
        if not root.exists():
            continue
        for candidate in root.rglob("*.md"):
            rel = candidate.relative_to(REPO_ROOT).as_posix()
            normalized_rel = _normalize_key(rel)
            normalized_stem = _normalize_key(candidate.stem)
            for term in terms:
                key = _normalize_key(term)
                if key and (key in normalized_rel or key in normalized_stem):
                    return [rel]
    return []
