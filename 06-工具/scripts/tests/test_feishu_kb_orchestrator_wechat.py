#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import feishu_kb_orchestrator as orchestrator


def test_orchestrator_dry_run_auto_selects_quote_theme_for_wechat() -> None:
    result = orchestrator.orchestrate_message(
        text="生成公众号内容：主题矿区/选题：普通人逆袭三步法\n核心矛盾：总想逆袭，但没有稳定执行机制\n是否调用金句库：是",
        dry_run=True,
    )

    assert result["status"] == "success"
    assert result["skill"]["status"] == "success"
    used = set(result["skill"]["result"]["context_files_used"] or [])
    assert "03-素材库/金句库/00-索引.md" in used
    assert any(path.startswith("03-素材库/金句库/") and path != "03-素材库/金句库/00-索引.md" for path in used)
