#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from persona_story_library import (
    load_persona_story_cards,
    resolve_persona_story_contexts,
    select_persona_story_cards,
)


def test_resolve_persona_story_contexts_prefers_canonical_library() -> None:
    paths = resolve_persona_story_contexts()

    assert "03-素材库/故事素材库/李可IP故事库.md" in paths


def test_load_persona_story_cards_reads_canonical_story_cards() -> None:
    cards = load_persona_story_cards()

    assert len(cards) >= 5
    assert any(card.get("story_id") == "LK-001" for card in cards)


def test_select_persona_story_cards_matches_topic_and_usage() -> None:
    cards = select_persona_story_cards(
        topic="创业复盘与重启",
        conflict="连续失败后如何重新站起来",
        usage="结尾回扣",
        limit=2,
    )

    assert cards
    assert all(card.get("story_id") for card in cards)
