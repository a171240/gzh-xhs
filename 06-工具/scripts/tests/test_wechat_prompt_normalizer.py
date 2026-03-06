#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import feishu_skill_runner as runner
import wechat_prompt_normalizer as normalizer


def _write_reference_files(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    account_matrix = tmp_path / "账号矩阵.md"
    account_matrix.write_text(
        "# 公众号四账号矩阵\n\n"
        "## 账号定位\n\n"
        "| 账号 | 前缀 | 核心风格 | 内容倾向 |\n"
        "|------|------|----------|----------|\n"
        "| 商业IP实战笔记 | shizhan | 轻实操 | 避坑、清单、可马上执行 |\n\n"
        "## 配图风格（账号绑定）\n\n"
        "- shizhan：手账笔记风\n",
        encoding="utf-8",
    )
    style_library = tmp_path / "视觉风格库.md"
    style_library.write_text(
        "# 视觉风格库\n\n"
        "## 风格D：手账笔记风格（商业IP实战笔记专用）\n\n"
        "### 封面图提示词模板\n\n"
        "```text\n手账封面模板\n```\n\n"
        "### 文章配图提示词模板\n\n"
        "```text\n手账正文模板\n```\n",
        encoding="utf-8",
    )
    p4 = tmp_path / "P4.md"
    p4.write_text("# P4\n", encoding="utf-8")
    framework = tmp_path / "内容框架知识库.md"
    framework.write_text("# Framework\n", encoding="utf-8")
    return account_matrix, style_library, p4, framework


def _article_text(style_name: str = "手账笔记风") -> str:
    return (
        "---\n"
        '账号: "商业IP实战笔记"\n'
        '日期: "2026-03-08"\n'
        '选题: "为什么同样努力，有的人更容易被看见"\n'
        f'配图风格: "{style_name}"\n'
        'summary: "这是一篇可直接照做的实操清单。"\n'
        "---\n\n"
        "## 标题\n"
        "主标题：先别加班写长文：5步把你的价值入口改成“秒懂”\n\n"
        "## 正文\n"
        "开头段落。\n\n"
        "### 第1步：一句话重写你的价值入口（5分钟）\n"
        "观点：一篇内容的生死，常常在第一句。\n"
        "案例细节：原句没有停留。\n"
        "行动第一步：按模板写第一句。\n\n"
        "### 第2步：把“我想说”改成“你能用”\n"
        "观点：读者关注的是自己的问题。\n"
        "底层逻辑：从自我中心切到读者任务。\n"
        "行动第一步：把前120字改成收益视角。\n\n"
        "## 配图提示词\n"
        "- 封面图：手账笔记风，荧光笔标注“5步改成秒懂入口”。\n"
        "- 图1：一句话模板卡片图。\n"
        "- 图2：改前改后段落对照。\n"
    )


def test_normalize_markdown_file_rewrites_bullet_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    account_matrix, style_library, p4, framework = _write_reference_files(tmp_path)
    article_path = tmp_path / "article.md"
    article_path.write_text(_article_text(), encoding="utf-8")

    monkeypatch.setattr(normalizer, "ACCOUNT_MATRIX_PATH", account_matrix)
    monkeypatch.setattr(normalizer, "STYLE_LIBRARY_PATH", style_library)
    monkeypatch.setattr(normalizer, "P4_PATH", p4)
    monkeypatch.setattr(normalizer, "FRAMEWORK_KB_PATH", framework)

    payload = normalizer.normalize_markdown_file(article_path)
    updated = article_path.read_text(encoding="utf-8")

    assert payload["style"] == "手账笔记风格"
    assert payload["prompt_count"] == 3
    assert "- 封面图：" in updated
    assert "- 配图1（对应：第1步：一句话重写你的价值入口（5分钟））：" in updated
    assert "中央 383×383 安全区" in updated
    assert "1080×1440 像素" in updated
    assert "3:4 竖版" in updated
    assert "统一风格约束" in updated
    assert "手账笔记风格" in updated


def test_normalize_markdown_file_fails_when_style_conflicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    account_matrix, style_library, p4, framework = _write_reference_files(tmp_path)
    article_path = tmp_path / "article.md"
    article_path.write_text(_article_text(style_name="科技蓝数据图风"), encoding="utf-8")

    monkeypatch.setattr(normalizer, "ACCOUNT_MATRIX_PATH", account_matrix)
    monkeypatch.setattr(normalizer, "STYLE_LIBRARY_PATH", style_library)
    monkeypatch.setattr(normalizer, "P4_PATH", p4)
    monkeypatch.setattr(normalizer, "FRAMEWORK_KB_PATH", framework)

    with pytest.raises(RuntimeError, match="配图风格与账号绑定冲突"):
        normalizer.normalize_markdown_file(article_path)


def test_run_skill_task_executes_prompt_normalizer_script(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_runner(*, brief: str, timeout_sec: int) -> dict[str, object]:
        assert brief == "02-内容生产/公众号/生成内容/2026-03-08/demo.md"
        assert timeout_sec == 1800
        return {
            "status": "success",
            "processed_files": [
                {
                    "path": "02-内容生产/公众号/生成内容/2026-03-08/demo.md",
                    "prompt_count": 3,
                }
            ],
        }

    monkeypatch.setattr(runner, "_run_prompt_normalizer_script", fake_runner)

    result = runner.run_skill_task(
        skill_id="wechat_prompt_normalize",
        brief="02-内容生产/公众号/生成内容/2026-03-08/demo.md",
        platform="公众号",
    )

    assert result["status"] == "success"
    assert result["saved_files"] == ["02-内容生产/公众号/生成内容/2026-03-08/demo.md"]
    assert "processed_files" in result["full_text"]
