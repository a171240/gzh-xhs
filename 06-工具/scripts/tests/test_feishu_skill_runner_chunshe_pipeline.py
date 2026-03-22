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


def test_validate_chunshe_markdown_catches_intro_templates_and_body_cta() -> None:
    markdown = """# 标题
第一次做脸前先看这三条

## 备选标题
- 备选1
- 备选2

# 正文
有用吗？有用，但别急着做。
我是开美容院的，所以我先把话说前面。
我店里有条规矩，先判断再决定。
想知道细节可以评论我。
"""

    issues, _advisories = runner._validate_chunshe_markdown(markdown)

    assert "仍有问答模板味" in issues
    assert "仍有身份开头" in issues
    assert "前 6 句缺少顾客原话" in issues
    assert "中段服务动作少于 2 个" in issues
    assert any("CTA" in item for item in issues)


def test_split_chunshe_issues_separates_soft_and_hard() -> None:
    hard, soft = runner._split_chunshe_issues(
        [
            "仍有问答模板味",
            "已补过桥",
            "结尾还有一点生硬",
        ]
    )

    assert hard == ["仍有问答模板味"]
    assert soft == ["已补过桥", "结尾还有一点生硬"]


def test_normalize_chunshe_source_pack_requires_required_fields() -> None:
    with pytest.raises(RuntimeError, match="missing required field"):
        runner._normalize_chunshe_source_pack({"source_pack": {"brief_extract": {}}})


def test_local_chunshe_source_pack_includes_review_language() -> None:
    config = runner._extract_chunshe_runtime_config("椿舍内容：关键词=美容院做脸有用吗")
    source_pack_root = runner._build_local_chunshe_source_pack(config=config, source_materials={"seed_examples": []})
    source_pack = source_pack_root["source_pack"]

    assert "review_language" in source_pack
    assert "theme_language" in source_pack
    assert source_pack["review_language"]["principle"]
    assert isinstance(source_pack["review_language"]["opening_examples"], list)
    assert source_pack["theme_language"]["principle"]
    assert isinstance(source_pack["theme_language"]["theme_examples"], list)
    assert "显性金句最多 1 句" in source_pack["fugui_logic"]["gold_sentence_rule"]


def test_collect_recent_chunshe_history_ignores_failed_report_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_root = tmp_path / "reports" / "chunshe-generation"
    output_root = tmp_path / "02-内容生产" / "小红书" / "生成内容"
    failed_dir = report_root / "2026-03-09" / "failed-run"
    success_dir = report_root / "2026-03-09" / "success-run"
    failed_dir.mkdir(parents=True)
    success_dir.mkdir(parents=True)
    output_dir = output_root / "2026-03-09"
    output_dir.mkdir(parents=True)

    (failed_dir / "topic-selection.json").write_text(
        json.dumps({"selected_topics": [{"topic_title": "失败选题"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    success_output = output_dir / "李可-20260309-01-测试标题.md"
    success_output.write_text("# 标题\n成功选题\n\n# 正文\n正文\n", encoding="utf-8")
    (success_dir / "run-summary.json").write_text(
        json.dumps(
            {
                "status": "success",
                "output_type": "精简发布版",
                "selected_topics": [
                    {
                        "topic_id": "CS001",
                        "topic_title": "成功选题",
                        "angle_type": "防御拆解",
                    }
                ],
                "topic_results": [
                    {
                        "topic_id": "CS001",
                        "topic_title": "成功选题",
                        "path": "生成内容/2026-03-09/李可-20260309-01-测试标题.md",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(chunshe_engine, "CHUNSHE_REPORT_ROOT", report_root)
    monkeypatch.setattr(chunshe_engine, "XHS_OUTPUT_ROOT", output_root)

    history = chunshe_engine.collect_recent_chunshe_history(lookback_days=30)

    assert [item["topic_title"] for item in history] == ["成功选题"]


def test_select_primary_file_prefers_main_markdown_over_cover_and_summary() -> None:
    files = [
        {
            "path": "生成内容/2026-03-17/李可-20260317-01-主稿.cover.md",
            "content": "# 封面提示词\n\n## 中文提示词\n```text\n封面\n```",
        },
        {
            "path": "生成内容/2026-03-17/椿舍发布包-20260317-1篇汇总.md",
            "content": "# 椿舍发布包\n\n## 文件清单\n汇总",
        },
        {
            "path": "生成内容/2026-03-17/李可-20260317-01-主稿.md",
            "content": "# 标题\n成稿标题\n\n## 备选标题\n- 备选一\n- 备选二\n\n# 正文\n正文\n",
        },
    ]

    primary = runner._select_primary_file(files)

    assert primary is not None
    assert primary["path"].endswith("李可-20260317-01-主稿.md")


def test_run_chunshe_staged_task_generates_cover_sidecars_and_publish_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = runner.SkillDefinition(
        skill_id="chunshe_wj",
        name="椿舍门店专用",
        path=tmp_path / "SKILL.md",
        aliases=("椿舍内容",),
        default_platform="小红书",
        kind="content",
        default_contexts=(),
    )
    skill.path.write_text("# skill", encoding="utf-8")

    topic = {
        "topic_id": "CS001",
        "topic_title": "旧标题不会用于封面",
        "entry_class": "信任怀疑",
    }
    captured: dict[str, object] = {}

    def fake_report_dir(date_str: str, task_id: str) -> Path:
        report_dir = tmp_path / "reports" / date_str / task_id
        report_dir.mkdir(parents=True, exist_ok=True)
        return report_dir

    def fake_save_generated_files(*, text: str, skill: runner.SkillDefinition, platform: str, date_str: str) -> list[str]:
        files = runner._coerce_markdown_files(text)
        captured["files"] = files
        return [str(item.get("path") or "") for item in files]

    def fake_read_primary_saved_file(saved_files: list[str]) -> str:
        files = list(captured.get("files") or [])
        primary = runner._select_primary_file(files)
        if not primary:
            return ""
        return runner._render_full_text_for_reply(str(primary.get("content") or ""))

    monkeypatch.setattr(runner, "match_chunshe_topic_seed_examples", lambda *_args, **_kwargs: [topic])
    monkeypatch.setattr(runner, "collect_recent_chunshe_history", lambda **_kwargs: [])
    monkeypatch.setattr(runner, "summarize_recent_history", lambda history, limit=12: [])
    monkeypatch.setattr(runner, "dedupe_and_pick_chunshe_topics", lambda candidates, history, count: (candidates[:count], []))
    monkeypatch.setattr(runner, "_prefer_chunshe_seed_consistent_topics", lambda **kwargs: kwargs["selected_topics"])
    monkeypatch.setattr(runner, "enrich_chunshe_video_topic", lambda item: dict(item))
    monkeypatch.setattr(
        runner,
        "_build_local_chunshe_source_pack",
        lambda **_kwargs: {"source_pack": {"brief_extract": {"seed_keyword": "测试"}, "review_language": {}, "theme_language": {}}},
    )
    monkeypatch.setattr(
        runner,
        "_run_chunshe_single_topic",
        lambda **_kwargs: {
            "content": "# 标题\n成稿标题才用于封面\n\n## 备选标题\n- 备选一\n- 备选二\n\n# 正文\n正文\n",
            "retry_count": 0,
            "quote_used": False,
            "warnings": [],
        },
    )
    monkeypatch.setattr(runner, "_chunshe_generation_task_id", lambda _date_str: "task-001")
    monkeypatch.setattr(runner, "_chunshe_generation_report_dir", fake_report_dir)
    monkeypatch.setattr(runner, "_save_generated_files", fake_save_generated_files)
    monkeypatch.setattr(runner, "_read_primary_saved_file", fake_read_primary_saved_file)

    result = runner._run_chunshe_staged_task(
        skill=skill,
        brief="椿舍内容：关键词=美容院做脸有用吗\n配图=是",
        platform="小红书",
        date_str="2026-03-17",
        model="gpt-5.4",
        event_ref="",
        source_ref="",
        timeout_sec=30,
        codex_cli="codex",
        context_files_used=[],
        context_plan={"context_files_merged": [], "context_files_auto": []},
        context_warnings=[],
        context_errors=[],
        context_prompt="",
        started=0.0,
        dry_run=False,
    )

    files = list(captured.get("files") or [])

    assert len(files) == 3
    assert files[0]["path"].endswith(".md")
    assert files[1]["path"].endswith(".cover.md")
    assert files[2]["path"].endswith("椿舍发布包-20260317-1篇汇总.md")
    assert "成稿标题才用于封面" in str(files[1]["content"])
    assert "旧标题不会用于封面" not in str(files[1]["content"])
    assert result["topic_results"][0]["cover_path"].endswith(".cover.md")
    assert result["topic_results"][0]["cover_template"]
    assert result["topic_results"][0]["final_title"] == "成稿标题才用于封面"
    assert result["publish_pack_path"].endswith("椿舍发布包-20260317-1篇汇总.md")
    assert result["worker_plan"]["cover_prompt_enabled"] is True
    assert result["source_materials"]["cover_prompt_enabled"] is True


def test_run_chunshe_staged_task_rewrites_publish_pack_path_after_deduped_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = runner.SkillDefinition(
        skill_id="chunshe_wj",
        name="椿舍门店专用",
        path=tmp_path / "SKILL.md",
        aliases=("椿舍内容",),
        default_platform="小红书",
        kind="content",
        default_contexts=(),
    )
    skill.path.write_text("# skill", encoding="utf-8")

    topic = {
        "topic_id": "CS001",
        "topic_title": "旧标题不会用于封面",
        "entry_class": "信任怀疑",
    }

    monkeypatch.setattr(runner, "match_chunshe_topic_seed_examples", lambda *_args, **_kwargs: [topic])
    monkeypatch.setattr(runner, "collect_recent_chunshe_history", lambda **_kwargs: [])
    monkeypatch.setattr(runner, "summarize_recent_history", lambda history, limit=12: [])
    monkeypatch.setattr(runner, "dedupe_and_pick_chunshe_topics", lambda candidates, history, count: (candidates[:count], []))
    monkeypatch.setattr(runner, "_prefer_chunshe_seed_consistent_topics", lambda **kwargs: kwargs["selected_topics"])
    monkeypatch.setattr(runner, "enrich_chunshe_video_topic", lambda item: dict(item))
    monkeypatch.setattr(
        runner,
        "_build_local_chunshe_source_pack",
        lambda **_kwargs: {"source_pack": {"brief_extract": {"seed_keyword": "测试"}, "review_language": {}, "theme_language": {}}},
    )
    monkeypatch.setattr(
        runner,
        "_run_chunshe_single_topic",
        lambda **_kwargs: {
            "content": "# 标题\n成稿标题才用于封面\n\n## 备选标题\n- 备选一\n- 备选二\n\n# 正文\n正文\n",
            "retry_count": 0,
            "quote_used": False,
            "warnings": [],
        },
    )
    monkeypatch.setattr(runner, "_chunshe_generation_task_id", lambda _date_str: "task-001b")
    monkeypatch.setattr(runner, "_chunshe_generation_report_dir", lambda date_str, task_id: tmp_path / "reports" / date_str / task_id)
    monkeypatch.setattr(
        runner,
        "_save_generated_files",
        lambda **_kwargs: [
            "生成内容/2026-03-17/李可-20260317-01-旧标题不会用于封面.md",
            "生成内容/2026-03-17/李可-20260317-01-旧标题不会用于封面.cover.md",
            "生成内容/2026-03-17/椿舍发布包-20260317-1篇汇总-2.md",
        ],
    )
    monkeypatch.setattr(runner, "_read_primary_saved_file", lambda _saved_files: "# 标题\n成稿标题才用于封面\n\n# 正文\n正文")

    result = runner._run_chunshe_staged_task(
        skill=skill,
        brief="椿舍内容：关键词=美容院做脸有用吗\n配图=是",
        platform="小红书",
        date_str="2026-03-17",
        model="gpt-5.4",
        event_ref="",
        source_ref="",
        timeout_sec=30,
        codex_cli="codex",
        context_files_used=[],
        context_plan={"context_files_merged": [], "context_files_auto": []},
        context_warnings=[],
        context_errors=[],
        context_prompt="",
        started=0.0,
        dry_run=False,
    )

    assert result["publish_pack_path"].endswith("椿舍发布包-20260317-1篇汇总-2.md")
    assert result["topic_results"][0]["path"].endswith("旧标题不会用于封面.md")
    assert result["topic_results"][0]["cover_path"].endswith("旧标题不会用于封面.cover.md")


def test_run_chunshe_staged_task_ignores_cover_for_title_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = runner.SkillDefinition(
        skill_id="chunshe_wj",
        name="椿舍门店专用",
        path=tmp_path / "SKILL.md",
        aliases=("椿舍内容",),
        default_platform="小红书",
        kind="content",
        default_contexts=(),
    )
    skill.path.write_text("# skill", encoding="utf-8")

    topic = {"topic_id": "CS001", "topic_title": "标题池测试", "entry_class": "信任怀疑"}

    monkeypatch.setattr(runner, "match_chunshe_topic_seed_examples", lambda *_args, **_kwargs: [topic])
    monkeypatch.setattr(runner, "collect_recent_chunshe_history", lambda **_kwargs: [])
    monkeypatch.setattr(runner, "summarize_recent_history", lambda history, limit=12: [])
    monkeypatch.setattr(runner, "dedupe_and_pick_chunshe_topics", lambda candidates, history, count: (candidates[:count], []))
    monkeypatch.setattr(runner, "_prefer_chunshe_seed_consistent_topics", lambda **kwargs: kwargs["selected_topics"])
    monkeypatch.setattr(runner, "enrich_chunshe_video_topic", lambda item: dict(item))
    monkeypatch.setattr(
        runner,
        "_build_local_chunshe_source_pack",
        lambda **_kwargs: {"source_pack": {"brief_extract": {"seed_keyword": "测试"}, "review_language": {}, "theme_language": {}}},
    )
    monkeypatch.setattr(runner, "_chunshe_generation_task_id", lambda _date_str: "task-002")
    monkeypatch.setattr(runner, "_chunshe_generation_report_dir", lambda date_str, task_id: tmp_path / "reports" / date_str / task_id)
    monkeypatch.setattr(runner, "_save_generated_files", lambda **_kwargs: ["生成内容/2026-03-17/chunshe-20260317-标题池.md"])
    monkeypatch.setattr(runner, "_read_primary_saved_file", lambda _saved_files: "标题池正文")

    result = runner._run_chunshe_staged_task(
        skill=skill,
        brief="椿舍内容：关键词=美容院做脸有用吗\n输出=标题池\n配图=是",
        platform="小红书",
        date_str="2026-03-17",
        model="gpt-5.4",
        event_ref="",
        source_ref="",
        timeout_sec=30,
        codex_cli="codex",
        context_files_used=[],
        context_plan={"context_files_merged": [], "context_files_auto": []},
        context_warnings=[],
        context_errors=[],
        context_prompt="",
        started=0.0,
        dry_run=False,
    )

    assert any("标题池模式已忽略配图" in item for item in result["warnings"])
    assert result["publish_pack_path"] == ""
