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

# 置顶评论
先说清楚再做决定。

# 回复模板
给你补充一版判断标准。
"""

    issues = runner._validate_chunshe_markdown(markdown)

    assert "仍有问答模板味" in issues
    assert "仍有身份开头" in issues
    assert "仍有店规开头" in issues
    assert any("正文出现CTA黑名单" in item for item in issues)


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
