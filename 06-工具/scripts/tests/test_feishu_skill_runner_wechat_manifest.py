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


def test_xhs_registry_prefers_repo_manifest_entries() -> None:
    registry = runner.build_skill_registry()

    assert runner.resolve_skill(registry, "xhs").skill_id == "xhs"
    assert runner.resolve_skill(registry, "xhs_prompt_normalize").skill_id == "xhs_prompt_normalize"
    assert runner.resolve_skill(registry, "xhs_image").skill_id == "xhs_image"


def test_xhs_registry_keeps_global_skill_ids_distinct() -> None:
    registry = runner.build_skill_registry()

    assert "xhs-dual" in registry.by_id
    assert "xhs-publish-playwright" in registry.by_id


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
是否需要人设故事：自动
故事用途：开头立人设
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
    assert "03-素材库/故事素材库/李可IP故事库.md" in merged
    assert not plan["context_errors"]


def test_wechat_context_plan_auto_selects_quote_theme_when_missing() -> None:
    plan = runner.build_skill_context_plan(
        skill_id="wechat",
        brief="主题矿区/选题：普通人逆袭三步法\n核心矛盾：总想逆袭，但没有稳定执行机制\n是否调用金句库：是",
        platform="公众号",
        context_files=[],
    )

    merged = set(plan["context_files_merged"])
    assert "03-素材库/金句库/00-索引.md" in merged
    assert any(path.startswith("03-素材库/金句库/") and path != "03-素材库/金句库/00-索引.md" for path in merged)
    assert not plan["context_errors"]


def test_wechat_context_plan_errors_when_quote_theme_invalid() -> None:
    plan = runner.build_skill_context_plan(
        skill_id="wechat",
        brief="是否调用金句库：是\n金句主题：不存在的主题",
        platform="公众号",
        context_files=[],
    )

    assert "quote theme not found: 不存在的主题" in plan["context_errors"]


def test_wechat_context_plan_warns_when_benchmark_report_missing() -> None:
    plan = runner.build_skill_context_plan(
        skill_id="wechat",
        brief="参考对标文案：03-素材库/对标链接库/分析报告/2026-03-05/不存在-分析.md",
        platform="公众号",
        context_files=[],
    )

    assert not plan["context_errors"]
    assert any("benchmark analysis report pending auto-generation" in item for item in plan["context_warnings"])


def test_run_skill_task_dry_run_fails_when_context_errors_exist() -> None:
    result = runner.run_skill_task(
        skill_id="wechat",
        brief="是否调用金句库：是\n金句主题：不存在的主题",
        platform="公众号",
        dry_run=True,
    )

    assert result["status"] == "error"
    assert "quote theme not found: 不存在的主题" in result["errors"]


def test_wechat_context_plan_ignores_empty_multiline_optional_fields() -> None:
    brief = """
日期(YYYY-MM-DD，可空，默认今天)：2026-03-06
主题矿区/选题：普通人逆袭三步法
参考对标文案：
金句主题：
故事用途：
富贵模块开关：
""".strip()

    plan = runner.build_skill_context_plan(
        skill_id="wechat",
        brief=brief,
        platform="公众号",
        context_files=[],
    )

    assert not plan["context_errors"]


def test_wechat_platform_resolves_to_canonical_label_when_blank() -> None:
    result = runner.run_skill_task(
        skill_id="wechat",
        brief="主题矿区/选题：测试选题\n目标人群：内容创业者",
        platform="",
        dry_run=True,
    )

    assert result["status"] == "success"
    assert result["platform"] == "公众号"


def test_wechat_platform_resolves_from_english_alias() -> None:
    result = runner.run_skill_task(
        skill_id="wechat",
        brief="主题矿区/选题：测试选题\n目标人群：内容创业者",
        platform="wechat",
        dry_run=True,
    )

    assert result["status"] == "success"
    assert result["platform"] == "公众号"
