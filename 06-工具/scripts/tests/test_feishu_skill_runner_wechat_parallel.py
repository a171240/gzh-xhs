#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import feishu_skill_runner as runner


def test_wechat_dry_run_reports_staged_parallel_defaults() -> None:
    result = runner.run_skill_task(
        skill_id="wechat",
        brief="主题矿区/选题：测试选题\n目标人群：内容创业者",
        platform="公众号",
        dry_run=True,
    )

    assert result["status"] == "success"
    assert result["pipeline_mode"] == "staged_parallel"
    assert result["concurrency"] == 4
    assert result["shared_stages"] == list(runner.WECHAT_SHARED_STAGES)
    assert [item["account"] for item in result["account_workers"]] == list(runner.WECHAT_ACCOUNT_ORDER)
    assert "source_materials" in result
    assert "story_selection_plan" in result["source_materials"]
    assert result["source_materials"]["story_selection_plan"]["max_story_count"] == 1
    assert result["source_materials"]["story_selection_plan"]["placement"] == ["关键案例"]
    assert "quote_candidates_source" in result["source_materials"]
    assert "benchmark_candidates_source" in result["source_materials"]
    assert "story_candidates_source" in result["source_materials"]


def test_wechat_dry_run_caps_concurrency(monkeypatch) -> None:
    monkeypatch.setenv("WECHAT_CONTENT_CONCURRENCY", "2")
    result = runner.run_skill_task(
        skill_id="wechat",
        brief="主题矿区/选题：测试选题\n目标人群：内容创业者",
        platform="公众号",
        dry_run=True,
        concurrency=99,
    )

    assert result["status"] == "success"
    assert result["concurrency"] == 4


def test_wechat_partial_error_keeps_successful_accounts(monkeypatch, tmp_path) -> None:
    saved = {"text": ""}

    def fake_stage(*, stage_name: str, **_kwargs):
        if stage_name == "source_pack":
            payload = {
                "source_pack": {
                    "topic": "测试选题",
                    "target_audience": "内容创业者",
                    "core_conflict": "不会表达",
                    "cta_keyword": "回复清单",
                    "benchmark_structure_template": ["开头", "01", "02", "03"],
                    "benchmark_viewpoints": ["观点A", "观点B"],
                    "benchmark_candidates_source": ["03-素材库/对标链接库/分析报告/2026-03-05/demo.md"],
                    "benchmark_quote_candidates": [],
                    "benchmark_case_slots": ["案例1"],
                    "quote_theme_selected": "系统与执行",
                    "quote_candidates_source": ["03-素材库/金句库/03-系统与执行.md"],
                    "quote_candidates": [],
                    "framework_choice": {"name": "三段式", "reason": "适合", "skeleton": ["开头", "分析", "行动"]},
                    "story_candidates_source": ["03-素材库/故事素材库/李可IP故事库.md"],
                    "persona_story_cards": [],
                    "story_selection_plan": {
                        "enabled": True,
                        "usage": "自动",
                        "max_story_count": 1,
                        "placement": ["关键案例"],
                        "selected_story_ids": [],
                        "selection_reason": "matched canonical persona story cards for 关键案例",
                    },
                    "self_case_pool": ["案例A"],
                    "data_anchors": ["数据1"],
                    "must_cover_points": ["点1"],
                    "anti_repetition_rules": ["标题不要重复"],
                }
            }
            return payload, "{}", ""
        if stage_name == "strategy_matrix":
            payload = {
                "accounts": {
                    account: {
                        "positioning": account,
                        "title_formula": f"{account}-公式",
                        "core_angle": f"{account}-角度",
                        "tone": f"{account}-语气",
                        "must_use_points": [f"{account}-点"],
                        "case_plan": [f"{account}-案例"],
                        "structure_notes": [f"{account}-结构"],
                        "avoid_overlap": [f"{account}-去重"],
                        "image_style_hint": f"{account}-风格",
                    }
                    for account in runner.WECHAT_ACCOUNT_ORDER
                }
            }
            return payload, "{}", ""
        raise AssertionError(f"unexpected stage: {stage_name}")

    def fake_worker(*, account: str, date_str: str, **_kwargs):
        if account == "zengzhang":
            raise RuntimeError("draft failed")
        content = (
            "---\n"
            f"账号: {account}\n"
            f"日期: {date_str}\n"
            f"选题: 测试选题\n"
            f"标题公式: {account}-公式\n"
            f"配图风格: {account}-风格\n"
            "summary: 测试摘要\n"
            "---\n\n"
            f"## 标题\n\n{account} 标题\n\n"
            "## 正文\n\n### 章节一\n\n正文内容。\n\n"
            "## CTA\n\n回复清单\n\n"
            "## 配图提示词\n\n- 封面图：测试\n"
        )
        return {
            "status": "success",
            "account": account,
            "path": f"生成内容/{date_str}/{account}-20260307-{account}.md",
            "content": content,
            "qc": {"passed": True},
        }

    def fake_save_generated_files(*, text: str, **_kwargs):
        saved["text"] = text
        return [
            "02-内容生产/公众号/生成内容/2026-03-07/gongchang-20260307-gongchang.md",
            "02-内容生产/公众号/生成内容/2026-03-07/ipgc-20260307-ipgc.md",
            "02-内容生产/公众号/生成内容/2026-03-07/shizhan-20260307-shizhan.md",
        ]

    monkeypatch.setattr(runner, "_run_codex_json_stage", fake_stage)
    monkeypatch.setattr(runner, "_run_wechat_account_worker", fake_worker)
    monkeypatch.setattr(runner, "_save_generated_files", fake_save_generated_files)
    monkeypatch.setattr(runner, "_read_primary_saved_file", lambda _saved_files: "primary")
    monkeypatch.setattr(runner, "_wechat_generation_report_dir", lambda _date, _task: tmp_path / "reports")

    result = runner.run_skill_task(
        skill_id="wechat",
        brief="主题矿区/选题：测试选题\n目标人群：内容创业者",
        platform="公众号",
        codex_cli="codex",
        date_str="2026-03-07",
    )

    assert result["status"] == "partial_error"
    assert "zengzhang: draft failed" in result["errors"]
    assert [item["account"] for item in result["account_results"]] == list(runner.WECHAT_ACCOUNT_ORDER)
    assert "gongchang-20260307-gongchang.md" in saved["text"]
    assert "ipgc-20260307-ipgc.md" in saved["text"]
    assert "shizhan-20260307-shizhan.md" in saved["text"]
    assert "zengzhang-20260307-zengzhang.md" not in saved["text"]


def test_wechat_story_plan_defaults_to_single_main_story() -> None:
    result = runner.run_skill_task(
        skill_id="wechat",
        brief="主题矿区/选题：测试选题\n目标人群：内容创业者\n是否需要人设故事：自动",
        platform="公众号",
        dry_run=True,
    )

    assert result["status"] == "success"
    plan = result["source_materials"]["story_selection_plan"]
    assert plan["max_story_count"] == 1
    assert plan["placement"] == ["关键案例"]
