#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helpers for the canonical Li Ke persona story library."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_STORY_LIBRARY = REPO_ROOT / "03-素材库" / "故事素材库" / "李可IP故事库.md"
LEGACY_STORY_LIBRARY = REPO_ROOT / "03-素材库" / "故事素材库" / "故事素材库.md"

STORY_HEADER_RE = re.compile(r"(?ms)^##\s+(?P<header>.+?)\s*\n(?P<body>.*?)(?=^##\s+|\Z)")
FIELD_RE = re.compile(r"(?m)^-\s*(?P<label>[^：:]+)\s*[：:]\s*(?P<value>.+?)\s*$")

LIST_FIELDS = {
    "核心情绪",
    "可支撑观点",
    "适用内容类型",
    "禁用场景",
    "可引用数据/证据",
    "可改写金句",
    "故事标签",
}


def _safe_rel(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT).as_posix()


def _normalize_list(value: str) -> list[str]:
    parts = re.split(r"[；;、\n]+", str(value or ""))
    return [item.strip() for item in parts if item.strip()]


def resolve_persona_story_contexts() -> list[str]:
    for path in (CANONICAL_STORY_LIBRARY, LEGACY_STORY_LIBRARY):
        if path.exists() and path.is_file():
            return [_safe_rel(path)]
    return []


def _story_source_path() -> Path | None:
    for candidate in (CANONICAL_STORY_LIBRARY, LEGACY_STORY_LIBRARY):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _normalize_story_card(card: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "story_id": str(card.get("story_id") or "").strip(),
        "故事标题": str(card.get("故事标题") or "").strip(),
        "时间/阶段": str(card.get("时间/阶段") or "").strip(),
        "场景": str(card.get("场景") or "").strip(),
        "事件": str(card.get("事件") or "").strip(),
        "转折点": str(card.get("转折点") or "").strip(),
    }
    for field in LIST_FIELDS:
        normalized[field] = _normalize_list(str(card.get(field) or ""))
    return normalized


def load_persona_story_cards() -> list[dict[str, Any]]:
    source = _story_source_path()
    if source is None:
        return []

    text = source.read_text(encoding="utf-8", errors="ignore")
    cards: list[dict[str, Any]] = []
    for matched in STORY_HEADER_RE.finditer(text):
        header = str(matched.group("header") or "").strip()
        body = str(matched.group("body") or "").strip()
        if not header or not body:
            continue
        if "｜" in header:
            story_id, title = [item.strip() for item in header.split("｜", 1)]
        elif "|" in header:
            story_id, title = [item.strip() for item in header.split("|", 1)]
        else:
            story_id = re.sub(r"\s+", "-", header)
            title = header
        card: dict[str, Any] = {"story_id": story_id, "故事标题": title}
        for field in FIELD_RE.finditer(body):
            label = str(field.group("label") or "").strip()
            value = str(field.group("value") or "").strip()
            if label:
                card[label] = value
        normalized = _normalize_story_card(card)
        if normalized["story_id"] and normalized["故事标题"]:
            cards.append(normalized)
    return cards


def select_persona_story_cards(
    *,
    topic: str,
    conflict: str,
    usage: str,
    limit: int = 2,
) -> list[dict[str, Any]]:
    cards = load_persona_story_cards()
    if not cards:
        return []

    query = " ".join(item for item in [topic, conflict, usage] if str(item or "").strip())
    query_terms = [item for item in re.split(r"[^\w\u4e00-\u9fff]+", query) if len(item) >= 2]

    scored: list[tuple[int, dict[str, Any]]] = []
    for index, card in enumerate(cards):
        haystack = " ".join(
            [
                card["故事标题"],
                card["时间/阶段"],
                card["场景"],
                card["事件"],
                " ".join(card["核心情绪"]),
                " ".join(card["可支撑观点"]),
                " ".join(card["适用内容类型"]),
                " ".join(card["故事标签"]),
            ]
        )
        score = 0
        for term in query_terms:
            if term in haystack:
                score += 2
        if usage and usage in haystack:
            score += 3
        if any(term in usage for term in ("开头", "立人设")) and any(term in haystack for term in ("起点", "地推", "辍学", "厨师")):
            score += 1
        if any(term in usage for term in ("中段", "举例")) and any(term in haystack for term in ("创业", "踩坑", "复盘", "失败")):
            score += 1
        if any(term in usage for term in ("结尾", "回扣", "收束")) and any(term in haystack for term in ("重启", "复盘", "方法论")):
            score += 1
        scored.append((score * 100 - index, card))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[: max(1, min(limit, len(scored)))]]
