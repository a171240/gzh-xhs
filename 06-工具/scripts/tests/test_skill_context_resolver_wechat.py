#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import skill_context_resolver as resolver


def test_resolve_context_files_prefers_manifest_and_wechat_conditionals() -> None:
    temp_dir = REPO_ROOT / "06-工具" / "scripts" / "tests" / "_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    topic_path = temp_dir / "wechat-context-topic.md"
    topic_path.write_text(
        """---
topic: 普通人逆袭三步法
target: 想做个人IP的上班族
date: 2026-03-06
是否调用金句库: 是
金句主题: 系统与执行
参考对标文案: 03-素材库/对标链接库/分析报告/2026-03-05/普通人逆袭三步法-分析.md
富贵模块开关: 是
related:
  - 03-素材库/内容框架库/内容框架知识库.md
---

# 选题分析

痛点：总想逆袭，但没有稳定执行机制。
""",
        encoding="utf-8",
    )

    try:
        items = resolver.resolve_context_files(topic_path, platform="公众号", skill_id="wechat")
        merged = set(items)
        assert "06-工具/scripts/tests/_tmp/wechat-context-topic.md" in merged
        assert "02-内容生产/公众号/账号矩阵.md" in merged
        assert "03-素材库/增强模块/公众号爆文增强版.md" in merged
        assert "02-内容生产/公众号/prompts/公众号内容质检规则.md" in merged
        assert "03-素材库/金句库/00-索引.md" in merged
        assert "03-素材库/金句库/03-系统与执行.md" in merged
        assert "03-素材库/对标链接库/分析报告/2026-03-05/普通人逆袭三步法-分析.md" in merged
        assert "03-素材库/增强模块/富贵-打动人模块.md" in merged
    finally:
        if topic_path.exists():
            topic_path.unlink()
