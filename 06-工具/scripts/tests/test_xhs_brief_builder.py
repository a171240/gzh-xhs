#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import topic_brief_builder as builder


def test_xhs_brief_preserves_mode_account_and_optional_switches() -> None:
    payload = {
        "meta": {
            "topic": "从低效选题到稳定产出",
            "target": "想做个人IP的创始人",
            "date": "2026-03-07",
            "account": "C",
            "模式": "情绪冲突文字帖",
            "账号角色": "观点型操盘手",
            "核心目标": "评论",
            "置顶互动开关": "OFF",
            "是否调用金句库": "是",
            "金句主题": "系统与执行",
            "富贵模块开关": "是",
        },
        "sections": {
            "选题分析": "痛点：内容总在追热点，却没有自己的结构。",
            "内容大纲": "CTA：评论区留下你最卡的一步",
        },
    }

    brief = builder.build_brief_from_payload(payload, platform="小红书")

    assert "模式：情绪冲突文字帖" in brief
    assert "账号角色：观点型操盘手" in brief
    assert "核心目标：评论" in brief
    assert "置顶互动开关：OFF" in brief
    assert "是否调用金句库：是" in brief
    assert "金句主题：系统与执行" in brief
    assert "富贵模块开关：是" in brief
    assert "是否需要人设故事：自动" in brief
    assert "故事用途：" in brief
