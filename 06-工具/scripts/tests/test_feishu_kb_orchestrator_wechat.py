#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import feishu_kb_orchestrator as orchestrator


def test_orchestrator_dry_run_fails_when_wechat_context_is_incomplete() -> None:
    result = orchestrator.orchestrate_message(
        text="生成公众号内容：是否调用金句库：是",
        dry_run=True,
    )

    assert result["status"] == "error"
    assert result["skill"]["status"] == "error"
    assert "quote library enabled but 金句主题 is empty" in (result["skill"]["result"]["errors"] or [])
