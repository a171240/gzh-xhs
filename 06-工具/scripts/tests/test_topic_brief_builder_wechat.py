#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import topic_brief_builder as builder


def test_wechat_brief_includes_quote_benchmark_and_fugui_fields() -> None:
    payload = {
        "meta": {
            "topic": "普通人逆袭三步法",
            "target": "想做个人IP的上班族",
            "date": "2026-03-06",
            "是否调用金句库": "是",
            "金句主题": "系统与执行",
            "参考对标文案": "03-素材库/对标链接库/分析报告/2026-03-05/普通人逆袭三步法-分析.md",
            "富贵模块开关": "是",
            "related": ["03-素材库/内容框架库/内容框架知识库.md"],
        },
        "sections": {
            "选题分析": "痛点：总想逆袭，但没有稳定执行机制。",
            "内容大纲": "CTA：评论区回复清单",
        },
    }

    brief = builder.build_brief_from_payload(payload, platform="公众号")

    assert "主题矿区/选题：普通人逆袭三步法" in brief
    assert "是否调用金句库：是" in brief
    assert "金句主题：系统与执行" in brief
    assert "参考对标文案：03-素材库/对标链接库/分析报告/2026-03-05/普通人逆袭三步法-分析.md" in brief
    assert "富贵模块开关：是" in brief
    assert "引流物(CTA关键词)：评论区回复清单" in brief
