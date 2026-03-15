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


def test_resolve_context_files_uses_xhs_defaults_and_conditionals() -> None:
    temp_dir = REPO_ROOT / "06-工具" / "scripts" / "tests" / "_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    topic_path = temp_dir / "xhs-context-topic.md"
    topic_path.write_text(
        """---
topic: 从低效选题到稳定产出
target: 想做个人IP的创始人
date: 2026-03-07
account: C
模式: 情绪冲突文字帖
是否调用金句库: 是
金句主题: 系统与执行
富贵模块开关: 是
---

## 选题分析

痛点：内容总在追热点，却没有自己的结构。
""",
        encoding="utf-8",
    )
    try:
        items = resolver.resolve_context_files(topic_path, platform="小红书", skill_id="xhs")
        merged = set(items)
        assert "06-工具/scripts/tests/_tmp/xhs-context-topic.md" in merged
        assert "02-内容生产/小红书/账号矩阵.md" in merged
        assert "02-内容生产/小红书/templates/信息图模板库.md" in merged
        assert "02-内容生产/小红书/resources/情绪冲突内容引擎.md" in merged
        assert "03-素材库/金句库/00-索引.md" in merged
        assert "03-素材库/增强模块/富贵-打动人模块.md" in merged
    finally:
        if topic_path.exists():
            topic_path.unlink()
