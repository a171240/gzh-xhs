#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import feishu_skill_runner as runner
import topic_pipeline as pipeline


def test_wechat_registry_resolves_repo_manifest_aliases() -> None:
    registry = runner.build_skill_registry()

    assert runner.resolve_skill(registry, "生成公众号内容").skill_id == "wechat"
    assert runner.resolve_skill(registry, "标准化公众号配图提示词").skill_id == "wechat_prompt_normalize"
    assert runner.resolve_skill(registry, "公众号图片生成").skill_id == "wechat_image"
    assert registry.by_id["wechat"].path.as_posix().endswith("skills/自有矩阵/公众号批量生产.md")
    assert runner.resolve_skill(registry, "公众号爆文写作").skill_id == "wechat"


def test_wechat_registry_does_not_depend_on_desktop_mapping(tmp_path, monkeypatch) -> None:
    missing_desktop_json = tmp_path / "skills.json"
    monkeypatch.setattr(runner, "DESKTOP_SKILLS_JSON", missing_desktop_json)

    registry = runner.build_skill_registry()

    assert runner.resolve_skill(registry, "公众号爆文写作").skill_id == "wechat"
    assert runner.resolve_skill(registry, "生成公众号内容").skill_id == "wechat"


def test_xhs_registry_still_uses_desktop_compat_fallback() -> None:
    registry = runner.build_skill_registry()

    assert runner.resolve_skill(registry, "小红书内容生成").skill_id == "xhs"


def test_topic_pipeline_dispatch_resolves_via_registry() -> None:
    assert pipeline._resolve_dispatch_skill("公众号") == ("wechat", "公众号")


def test_wechat_context_plan_loads_optional_contexts_when_enabled() -> None:
    brief = """
日期(YYYY-MM-DD，可空，默认今天)：2026-03-06
主题矿区/选题：普通人逆袭三步法
是否调用金句库：是
金句主题：系统与执行
参考对标文案：03-素材库/对标链接库/分析报告/2026-03-05/普通人逆袭三步法-分析.md
富贵模块开关：是
""".strip()

    plan = runner.build_skill_context_plan(
        skill_id="wechat",
        brief=brief,
        platform="公众号",
        context_files=[],
    )

    merged = set(plan["context_files_merged"])
    assert "02-内容生产/公众号/账号矩阵.md" in merged
    assert "03-素材库/内容框架库/内容框架知识库.md" in merged
    assert "03-素材库/增强模块/公众号爆文增强版.md" in merged
    assert "02-内容生产/公众号/prompts/公众号内容质检规则.md" in merged
    assert "03-素材库/金句库/00-索引.md" in merged
    assert "03-素材库/金句库/03-系统与执行.md" in merged
    assert "03-素材库/对标链接库/分析报告/2026-03-05/普通人逆袭三步法-分析.md" in merged
    assert "03-素材库/增强模块/富贵-打动人模块.md" in merged
    assert not plan["context_warnings"]
    assert not plan["context_errors"]


def test_wechat_context_plan_errors_when_quote_theme_missing() -> None:
    plan = runner.build_skill_context_plan(
        skill_id="wechat",
        brief="是否调用金句库：是",
        platform="公众号",
        context_files=[],
    )

    assert "quote library enabled but 金句主题 is empty" in plan["context_errors"]


def test_wechat_context_plan_errors_when_quote_theme_invalid() -> None:
    plan = runner.build_skill_context_plan(
        skill_id="wechat",
        brief="是否调用金句库：是\n金句主题：不存在的主题",
        platform="公众号",
        context_files=[],
    )

    assert "quote theme not found: 不存在的主题" in plan["context_errors"]


def test_wechat_context_plan_errors_when_benchmark_report_missing() -> None:
    plan = runner.build_skill_context_plan(
        skill_id="wechat",
        brief="参考对标文案：03-素材库/对标链接库/分析报告/2026-03-05/不存在-分析.md",
        platform="公众号",
        context_files=[],
    )

    assert "benchmark analysis report not found for: 03-素材库/对标链接库/分析报告/2026-03-05/不存在-分析.md" in plan["context_errors"]


def test_run_skill_task_dry_run_fails_when_context_errors_exist() -> None:
    result = runner.run_skill_task(
        skill_id="wechat",
        brief="是否调用金句库：是",
        platform="公众号",
        dry_run=True,
    )

    assert result["status"] == "error"
    assert "quote library enabled but 金句主题 is empty" in result["errors"]
