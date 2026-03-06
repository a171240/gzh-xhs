#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import feishu_skill_runner as runner


def test_wechat_image_context_plan_is_execution_only() -> None:
    plan = runner.build_skill_context_plan(
        skill_id="wechat_image",
        brief="生成公众号图片：2026-03-06",
        platform="公众号",
        context_files=[],
    )

    merged = set(plan["context_files_merged"])
    assert "06-工具/scripts/README_WECHAT_IMAGE_GENERATOR.md" in merged
    assert "skills/自有矩阵/公众号批量生产.md" not in merged
    assert "02-内容生产/公众号/prompts/视觉风格库.md" not in merged
    assert "03-素材库/内容框架库/内容框架知识库.md" not in merged
    assert not plan["context_warnings"]


def test_wechat_prompt_normalize_context_plan_includes_rule_files() -> None:
    plan = runner.build_skill_context_plan(
        skill_id="wechat_prompt_normalize",
        brief="标准化公众号配图提示词：02-内容生产/公众号/生成内容/2026-03-06/gongchang-示例.md",
        platform="公众号",
        context_files=[],
    )

    merged = set(plan["context_files_merged"])
    assert "02-内容生产/公众号/账号矩阵.md" in merged
    assert "02-内容生产/公众号/prompts/视觉风格库.md" in merged
    assert "02-内容生产/公众号/prompts/P4.md" in merged
    assert "03-素材库/内容框架库/内容框架知识库.md" in merged
    assert not plan["context_warnings"]
