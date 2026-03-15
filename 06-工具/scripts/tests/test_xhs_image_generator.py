#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import xhs_flow
import xhs_image_generator as generator


def _cleanup_tmp_dir() -> None:
    tmp_dir = xhs_flow.REPO_ROOT / "06-工具" / "scripts" / "tests" / "_tmp_xhs_image"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)


def _write_content(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
platform: 小红书
account: A
account_prefix: xhs-a
mode: 信息图6页
source_topic: 01-选题管理/02-待生产/demo.md
chosen_title: 选题工具别再盲做
title_candidates: [选题工具别再盲做, 选题效率翻倍, 一套流程搞定选题]
tags: [选题, 内容生产]
publish_ready: false
image_manifest:
---

## 标题候选
- 选题工具别再盲做
- 选题效率翻倍
- 一套流程搞定选题

## 标签
#选题 #内容生产

## 发布正文
别再用“灵感来了再写”做内容。

## 6页文案
P1
P2
P3
P4
P5
P6

## 置顶评论
评论区回你流程模板

## 配图提示词
- P1: p1 prompt
- P2: p2 prompt
- P3: p3 prompt
- P4: p4 prompt
- P5: p5 prompt
- P6: p6 prompt

## 质检清单
- 不夸大
""",
        encoding="utf-8",
    )


def test_normalize_xhs_prompt_file_rewrites_stable_slots(tmp_path: Path) -> None:
    _cleanup_tmp_dir()
    content_path = xhs_flow.REPO_ROOT / "06-工具" / "scripts" / "tests" / "_tmp_xhs_image" / "xhs.md"
    try:
        _write_content(content_path)

        result = xhs_flow.normalize_xhs_prompt_file(content_path)

        assert result["status"] == "success"
        text = content_path.read_text(encoding="utf-8")
        assert "- p1: p1 prompt" in text
        assert "- p6: p6 prompt" in text
    finally:
        _cleanup_tmp_dir()


def test_process_xhs_content_file_writes_manifest_and_updates_frontmatter(tmp_path: Path, monkeypatch) -> None:
    _cleanup_tmp_dir()
    content_path = xhs_flow.REPO_ROOT / "06-工具" / "scripts" / "tests" / "_tmp_xhs_image" / "xhs.md"
    try:
        _write_content(content_path)
        xhs_flow.normalize_xhs_prompt_file(content_path)

        class FakeGenerator:
            supports_concurrency = True

            def __init__(self, output_dir: Path) -> None:
                self.output_dir = output_dir

            async def start(self) -> None:
                return None

            async def generate_image(self, *, prompt: str, account: str, page_type: str, size: str | None = None) -> str:
                target = self.output_dir / f"{page_type}.png"
                target.write_bytes(prompt.encode("utf-8"))
                return str(target)

            async def close(self) -> None:
                return None

        monkeypatch.setattr(
            generator,
            "build_image_generator",
            lambda **kwargs: (FakeGenerator(kwargs["output_dir"]), "fake"),
        )

        result = generator.process_xhs_content_file(content_path, dry_run=False)

        assert result["status"] == "success"
        manifest_path = content_path.with_suffix(".images.json")
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert len(payload["images"]) == 6
        updated = content_path.read_text(encoding="utf-8")
        assert "publish_ready: true" in updated
        assert "image_manifest:" in updated
    finally:
        _cleanup_tmp_dir()
