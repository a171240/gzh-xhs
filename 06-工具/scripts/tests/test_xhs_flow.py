#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import shutil
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import xhs_flow


def _cleanup_tmp_dir() -> Path:
    tmp_dir = xhs_flow.REPO_ROOT / "06-工具" / "scripts" / "tests" / "_tmp_xhs_flow"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir


def test_canonicalize_xhs_saved_files_merges_multipart_output() -> None:
    tmp_dir = _cleanup_tmp_dir()
    try:
        package_path = tmp_dir / "xhs-a-发布包.md"
        prompt_path = tmp_dir / "xhs-a-配图提示词.md"
        comment_path = tmp_dir / "xhs-a-置顶评论.md"

        package_path.write_text(
            """# 小红书发布包（A号｜信息图6页）

## 模式
信息图6页（对比型）

## 标题（3条）
1. 优质客户是聊出来的，不是筛出来的
2. 别急着筛人，先把话聊对
3. 15分钟把咨询聊到下一步

## 标签（10个）
#优质客户 #客户沟通 #成交对话

## Step2 2000字级结构稿（模块1-4）
这是正文内容。

## 6页落地文案（P1-P6）
### P1 封面
封面文案

## 质检
- [x] 不夸大
""",
            encoding="utf-8",
        )
        prompt_path.write_text(
            """# 配图提示词（A号｜信息图6页）

## P1 封面信息图提示词（对比型）
p1 prompt

## P2 痛点场景页提示词
p2 prompt

## P3 原因机制页提示词
p3 prompt

## P4 方法步骤页提示词
p4 prompt

## P5 效果证据页提示词
p5 prompt

## P6 CTA页提示词
p6 prompt
""",
            encoding="utf-8",
        )
        comment_path.write_text(
            """# 置顶评论（开关ON）

先给你可直接套用的首聊5问。

## 评论区快捷回复模板
- 模板1
""",
            encoding="utf-8",
        )

        canonical_path = xhs_flow.canonicalize_xhs_saved_files(
            [package_path],
            account="A",
            source_topic="01-选题管理/01-待深化/demo.md",
        )

        text = canonical_path.read_text(encoding="utf-8")
        assert "chosen_title: 优质客户是聊出来的，不是筛出来的" in text
        assert "## 发布正文" in text
        assert "这是正文内容。" in text
        assert "- p1: p1 prompt" in text
        assert "- p6: p6 prompt" in text
        assert "先给你可直接套用的首聊5问。" in text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_parse_xhs_prompt_contract_keeps_multiline_prompt_blocks() -> None:
    items = xhs_flow.parse_xhs_prompt_contract(
        """- p1: 第一行
第二行：继续说明
第三行
- p2: 第二页
字段1：值1
字段2：值2
""",
        mode=xhs_flow.INFO_MODE,
    )

    assert [item["slot"] for item in items] == ["p1", "p2"]
    assert "第二行：继续说明" in items[0]["prompt"]
    assert "字段2：值2" in items[1]["prompt"]


def test_profile_from_generated_files_synthesizes_prompts_from_layout_when_prompt_file_missing() -> None:
    tmp_dir = _cleanup_tmp_dir()
    try:
        package_path = tmp_dir / "xhs-a-发布包.md"
        execution_path = tmp_dir / "xhs-a-发布执行与质检.md"
        package_path.write_text(
            """# 小红书发布包

## 模式
信息图6页

## 标题（3条）
1. 优质客户是聊出来的，不是筛出来的

## 标签（10个）
#优质客户 #客户沟通

## 6页落地文案（P1-P6）
- P1：第一页
- P2：第二页
- P3：第三页
- P4：第四页
- P5：第五页
- P6：第六页
""",
            encoding="utf-8",
        )
        execution_path.write_text(
            """# 发布执行与质检

## 置顶评论（ON）
评论【诊断】领模板

## 发布质检（硬规则）
- [x] 标题合格
""",
            encoding="utf-8",
        )

        profile = xhs_flow._profile_from_generated_files([package_path, execution_path], account="A")

        assert [item["slot"] for item in profile.prompt_contract] == ["p1", "p2", "p3", "p4", "p5", "p6"]
        assert "评论【诊断】领模板" in profile.pinned_comment
        assert "标题合格" in profile.qc_text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
