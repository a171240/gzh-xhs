#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import chunshe_engine
import feishu_skill_runner as runner


def test_chunshe_registry_resolves_repo_manifest_aliases() -> None:
    registry = runner.build_skill_registry()

    assert runner.resolve_skill(registry, "椿舍内容").skill_id == "chunshe_wj"
    assert runner.resolve_skill(registry, "椿舍门店专用").skill_id == "chunshe_wj"
    assert runner.resolve_skill(registry, "chunshe").skill_id == "chunshe_wj"
    assert registry.by_id["chunshe_wj"].path.as_posix().endswith("skills/客户交付/椿舍门店专用/SKILL.md")


def test_chunshe_context_plan_loads_business_truth_and_methodology() -> None:
    plan = runner.build_skill_context_plan(
        skill_id="chunshe_wj",
        brief="椿舍内容：美容院套路最狠的那一秒",
        platform="小红书",
        context_files=[],
    )

    merged = plan["context_files_merged"]
    assert merged[:9] == [
        "skills/客户交付/美容内容底座/运行总纲.md",
        "skills/客户交付/美容内容底座/关键词意图与阶段路由.md",
        "skills/客户交付/美容内容底座/XHS短视频输出规范.md",
        "skills/客户交付/椿舍门店专用/references/选题引擎规则.md",
        "skills/客户交付/椿舍门店专用/references/内容策略.md",
        "skills/客户交付/椿舍门店专用/references/富贵内容方法论.md",
        "skills/客户交付/椿舍门店专用/references/样例输出.md",
        "skills/客户交付/椿舍门店专用/references/审稿机制.md",
        "skills/客户交付/椿舍门店专用/references/口语化二审.md",
    ]
    assert not plan["context_errors"]


def test_chunshe_context_plan_auto_loads_quote_theme_when_enabled() -> None:
    plan = runner.build_skill_context_plan(
        skill_id="chunshe_wj",
        brief="椿舍内容：美容院套路最狠的那一秒\n是否调用金句库：是\n金句主题：人性与沟通",
        platform="小红书",
        context_files=[],
    )

    merged = set(plan["context_files_merged"])
    assert "03-素材库/金句库/00-索引.md" in merged
    assert "03-素材库/金句库/04-人性与沟通.md" in merged
    assert not plan["context_errors"]


def test_brief_field_supports_equals_and_inline_skill_prefix() -> None:
    brief = "椿舍内容：关键词=SPA\n输出=标题池\n批量=7"
    assert runner._brief_field(brief, "关键词") == "SPA"
    assert runner._brief_field(brief, "输出") == "标题池"
    assert runner._brief_field(brief, "批量") == "7"


def test_chunshe_dry_run_uses_topic_pipeline() -> None:
    result = runner.run_skill_task(
        skill_id="chunshe_wj",
        brief="椿舍内容：关键词=SPA\n输出=标题池\n批量=7",
        platform="小红书",
        dry_run=True,
    )

    assert result["status"] == "success"
    assert result["pipeline_mode"] == "staged_serial_topics"
    assert result["shared_stages"] == ["source_pack", "topic_planner", "topic_dedupe", "topic_pick", "draft_package", "polish_package"]
    assert result["worker_plan"]["topic_target_count"] == 7
    assert result["worker_plan"]["mode"] == "平衡"
    assert result["source_materials"]["seed_keyword"] == "SPA"
    assert len(result["topic_pool_preview"]) >= 5
    assert len(result["selected_topics"]) >= 1
    assert result["selected_topics"][0]["opening_family"]
    assert result["selected_topics"][0]["first_conflict_line"]
    assert result["warnings"] == []
    assert result["stage_timings"] == []


def test_extract_chunshe_runtime_config_defaults_to_balanced_mode() -> None:
    config = runner._extract_chunshe_runtime_config("椿舍内容：关键词=美容院做脸有用吗")

    assert config["mode"] == "平衡"
    assert config["quote_enabled"] is False


def test_extract_chunshe_runtime_config_accepts_explicit_mode() -> None:
    config = runner._extract_chunshe_runtime_config("椿舍内容：关键词=SPA\n模式=快速")

    assert config["mode"] == "快速"


def test_extract_chunshe_runtime_config_accepts_cover_prompt_flag() -> None:
    config = runner._extract_chunshe_runtime_config("椿舍内容：关键词=SPA\n配图=是")

    assert config["cover_prompt_enabled"] is True


def test_resolve_codex_cli_materializes_windowsapps_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "Program Files" / "WindowsApps" / "OpenAI.Codex" / "app" / "resources"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "codex.exe"
    source_path.write_bytes(b"codex-binary")

    cache_dir = tmp_path / "cache-root"
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)

    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr(runner, "REPO_ROOT", repo_root)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: str(source_path))
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(cache_dir))

    resolved = Path(runner.resolve_codex_cli())

    assert resolved.exists()
    assert resolved.read_bytes() == b"codex-binary"
    assert "WindowsApps" not in resolved.parts


def test_chunshe_context_plan_does_not_auto_load_quotes_by_default() -> None:
    plan = runner.build_skill_context_plan(
        skill_id="chunshe_wj",
        brief="椿舍内容：关键词=美容院做脸有用吗",
        platform="小红书",
        context_files=[],
    )

    merged = set(plan["context_files_merged"])
    assert "03-素材库/金句库/00-索引.md" not in merged
