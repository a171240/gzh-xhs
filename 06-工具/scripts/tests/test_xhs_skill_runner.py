#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import feishu_skill_runner as runner


def test_xhs_context_plan_uses_repo_manifest_defaults() -> None:
    plan = runner.build_skill_context_plan(
        skill_id="xhs",
        brief="模式：信息图6页\n是否调用金句库：否\n富贵模块开关：否",
        platform="小红书",
        context_files=[],
    )

    merged = set(plan["context_files_merged"])
    assert "02-内容生产/小红书/账号矩阵.md" in merged
    assert "02-内容生产/小红书/templates/信息图模板库.md" in merged
    assert "02-内容生产/小红书/templates/视觉风格库.md" in merged
    assert "03-素材库/故事素材库/李可IP故事库.md" in merged
    assert not plan["context_errors"]


def test_xhs_context_plan_loads_optional_quote_and_emotion_contexts() -> None:
    plan = runner.build_skill_context_plan(
        skill_id="xhs",
        brief="模式：情绪冲突文字帖\n是否调用金句库：是\n金句主题：系统与执行\n富贵模块开关：是",
        platform="小红书",
        context_files=[],
    )

    merged = set(plan["context_files_merged"])
    assert "02-内容生产/小红书/resources/情绪冲突内容引擎.md" in merged
    assert "03-素材库/金句库/00-索引.md" in merged
    assert "03-素材库/金句库/03-系统与执行.md" in merged
    assert "03-素材库/增强模块/富贵-打动人模块.md" in merged
    assert "03-素材库/故事素材库/李可IP故事库.md" in merged
    assert not plan["context_errors"]


def test_run_skill_task_dry_run_uses_repo_local_xhs_contexts() -> None:
    result = runner.run_skill_task(
        skill_id="xhs",
        brief="模式：信息图6页\n是否调用金句库：否\n富贵模块开关：否",
        platform="小红书",
        dry_run=True,
    )

    assert result["status"] == "success"
    assert "02-内容生产/小红书/账号矩阵.md" in set(result["context_files_used"])
    assert "03-素材库/故事素材库/李可IP故事库.md" in set(result["context_files_used"])
    assert result["pipeline_mode"] == "staged_single_worker"
    assert result["shared_stages"] == list(runner.XHS_SHARED_STAGES)
    assert result["worker_plan"]["stages"] == ["delivery", "qc", "retry_once"]
    assert result["source_materials"]["story_selection_plan"]["enabled"] is True


def test_xhs_platform_resolves_to_canonical_label_when_blank() -> None:
    result = runner.run_skill_task(
        skill_id="xhs",
        brief="模式：信息图6页\n是否调用金句库：否\n富贵模块开关：否",
        platform="",
        dry_run=True,
    )

    assert result["status"] == "success"
    assert result["platform"] == "小红书"


def test_xhs_platform_resolves_from_english_alias() -> None:
    result = runner.run_skill_task(
        skill_id="xhs",
        brief="模式：信息图6页\n是否调用金句库：否\n富贵模块开关：否",
        platform="xhs",
        dry_run=True,
    )

    assert result["status"] == "success"
    assert result["platform"] == "小红书"
