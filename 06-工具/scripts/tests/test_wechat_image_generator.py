#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import sys
import asyncio
from pathlib import Path

import pytest

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import wechat_image_generator as generator


def _article_text() -> str:
    return (
        "---\n"
        '账号: "IP内容工厂"\n'
        'summary: "摘要"\n'
        "---\n\n"
        "## 标题\n"
        "主标题：测试标题\n\n"
        "## 正文\n"
        "开头段落。\n\n"
        "### 01 第一节\n"
        "正文一。\n\n"
        "### 02 第二节\n"
        "正文二。\n\n"
        "## 配图提示词\n"
        "- 封面图：封面提示词\n"
        "- 配图1（对应：01 第一节）：第一节提示词\n"
        "- 配图2（对应：02 第二节）：第二节提示词\n"
    )


def test_extract_prompt_specs_supports_bullet_contract() -> None:
    specs = generator.extract_prompt_specs(_article_text())

    assert len(specs) == 3
    assert specs[0].is_cover is True
    assert specs[1].index == 1
    assert specs[1].anchor == "01 第一节"
    assert specs[2].filename_stub == "img-02"


def test_validate_prompt_specs_requires_cover() -> None:
    text = _article_text().replace("- 封面图：封面提示词\n", "")
    specs = generator.extract_prompt_specs(text)

    with pytest.raises(RuntimeError, match="cover"):
        generator.validate_prompt_specs(specs)


def test_extract_prompt_specs_handles_anchor_titles_with_colons() -> None:
    text = (
        "---\n"
        '账号: "商业IP实战笔记"\n'
        "---\n\n"
        "## 配图提示词\n"
        "- 封面图：封面提示词\n"
        "- 配图1（对应：第1步：一句话重写你的价值入口（5分钟））：章节提示词\n"
    )

    specs = generator.extract_prompt_specs(text)

    assert specs[1].label == "配图1（对应：第1步：一句话重写你的价值入口（5分钟））"
    assert specs[1].anchor == "第1步：一句话重写你的价值入口（5分钟）"
    assert specs[1].prompt == "章节提示词"


def test_requested_size_for_spec_matches_cover_and_body_defaults() -> None:
    specs = generator.extract_prompt_specs(_article_text())

    assert generator.requested_size_for_spec(specs[0]) == "21:9"
    assert generator.requested_size_for_spec(specs[1]) == "3:4"


def test_extract_prompt_specs_handles_colon_rich_detailed_prompts() -> None:
    text = (
        "## 配图提示词\n"
        "- 封面图：【图片类型】微信公众号封面图；【尺寸】900×383 像素；【比例】21:9 横版宽幅；【标题】测试标题\n"
        "- 配图1（对应：第1步：一句话重写你的价值入口（5分钟））：【图片类型】微信公众号正文配图；【尺寸】1080×1440 像素；【比例】3:4 竖版；【对应章节】第1步：一句话重写你的价值入口（5分钟）；【建议布局】顶部做章节标题区；中部做主信息卡片\n"
    )

    specs = generator.extract_prompt_specs(text)

    assert len(specs) == 2
    assert specs[0].is_cover is True
    assert specs[1].is_cover is False
    assert specs[1].label == "配图1（对应：第1步：一句话重写你的价值入口（5分钟））"
    assert "1080×1440 像素" in specs[1].prompt


def test_write_back_markdown_updates_cover_and_body_images(tmp_path: Path) -> None:
    md_path = tmp_path / "article.md"
    image_dir = tmp_path / "images" / "gongchang"
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "cover.jpg").write_bytes(b"cover")
    (image_dir / "img-01.jpg").write_bytes(b"img1")
    (image_dir / "img-02.jpg").write_bytes(b"img2")
    md_path.write_text(_article_text(), encoding="utf-8")

    logger = logging.getLogger("test-wechat-image-generator")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())

    generator.write_back_markdown(
        md_path,
        [
            {
                "label": "封面图",
                "relative_output": "images/gongchang/cover.jpg",
                "is_cover": True,
                "anchor": "",
            },
            {
                "label": "配图1（对应：01 第一节）",
                "relative_output": "images/gongchang/img-01.jpg",
                "is_cover": False,
                "anchor": "01 第一节",
            },
            {
                "label": "配图2（对应：02 第二节）",
                "relative_output": "images/gongchang/img-02.jpg",
                "is_cover": False,
                "anchor": "02 第二节",
            },
        ],
        abbr="gongchang",
        logger=logger,
    )

    updated = md_path.read_text(encoding="utf-8")
    frontmatter = generator.parse_frontmatter(updated)
    assert frontmatter["cover_image"] == "images/gongchang/cover.jpg"
    assert "![配图1（对应：01 第一节）](images/gongchang/img-01.jpg)" in updated
    assert "![配图2（对应：02 第二节）](images/gongchang/img-02.jpg)" in updated


def test_index_output_uses_posix_paths(tmp_path: Path) -> None:
    md_path = tmp_path / "article.md"
    md_path.write_text(_article_text(), encoding="utf-8")
    output = tmp_path / "images" / "gongchang" / "img-01.jpg"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"img")

    assert generator.rel_output(md_path, output) == "images/gongchang/img-01.jpg"
    payload = {"output": generator.rel_output(md_path, output)}
    assert "/" in payload["output"]
    json.dumps(payload, ensure_ascii=False)


def test_resolve_generation_concurrency_respects_provider_capability() -> None:
    class DummyParallel:
        supports_concurrency = True

    class DummySerial:
        supports_concurrency = False

    assert generator.resolve_generation_concurrency("evolink", DummyParallel(), requested=0, prompt_count=5) == 3
    assert generator.resolve_generation_concurrency("evolink", DummyParallel(), requested=2, prompt_count=5) == 2
    assert generator.resolve_generation_concurrency("custom", DummySerial(), requested=4, prompt_count=5) == 1


def test_build_image_generator_requires_evolink_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EVOLINK_API_KEY", raising=False)
    monkeypatch.delenv("EVOLINK_BASE_URL", raising=False)
    monkeypatch.delenv("EVOLINK_IMAGE_MODEL", raising=False)
    monkeypatch.setattr(generator, "ENV_FALLBACK_FILES", ())

    with pytest.raises(RuntimeError, match="EVOLINK_API_KEY"):
        generator.build_image_generator(output_dir=tmp_path / "images", model="")


def test_run_generate_parallelizes_supported_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    md_path = tmp_path / "article.md"
    md_path.write_text(_article_text(), encoding="utf-8")

    tracker = {"active": 0, "max_active": 0, "factory_calls": 0}

    class DummyGenerator:
        supports_concurrency = True

        def __init__(self, output_dir: str, model: str) -> None:
            self.output_dir = Path(output_dir)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.model_id = model or "dummy-model"
            self.last_error = ""
            self.last_error_code = ""
            tracker["factory_calls"] += 1

        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

        async def generate_image(self, prompt: str, account: str, page_type: str, size: str | None = None) -> str | None:
            tracker["active"] += 1
            tracker["max_active"] = max(tracker["max_active"], tracker["active"])
            try:
                await asyncio.sleep(0.01)
                path = self.output_dir / f"{account}-{page_type}.png"
                path.write_bytes(b"img")
                return str(path)
            finally:
                tracker["active"] -= 1

    def fake_build_image_generator(*, output_dir: Path, model: str):
        return DummyGenerator(str(output_dir), model), "evolink"

    monkeypatch.setattr(generator, "build_image_generator", fake_build_image_generator)

    exit_code = asyncio.run(
        generator.run_generate(
            md_path=md_path,
            model="",
            limit=0,
            retries=1,
            insert_to_md=False,
            compress=False,
            max_size_kb=500,
            concurrency=3,
        )
    )

    assert exit_code == 0
    assert tracker["max_active"] > 1
    assert tracker["factory_calls"] == 4


def test_insert_body_images_places_images_after_section_content() -> None:
    text = (
        "## 正文\n"
        "### 01 第一节\n"
        "这一节的正文。\n\n"
        "### 02 第二节\n"
        "第二节的正文。\n"
    )

    updated = generator.insert_body_images(
        text,
        [
            {
                "label": "配图1（对应：01 第一节）",
                "relative_output": "images/gongchang/img-01.jpg",
                "anchor": "01 第一节",
            },
            {
                "label": "配图2（对应：02 第二节）",
                "relative_output": "images/gongchang/img-02.jpg",
                "anchor": "02 第二节",
            },
        ],
        abbr="gongchang",
    )

    lines = updated.splitlines()
    idx_heading_1 = lines.index("### 01 第一节")
    idx_body_1 = lines.index("这一节的正文。")
    idx_image_1 = lines.index("![配图1（对应：01 第一节）](images/gongchang/img-01.jpg)")
    idx_heading_2 = lines.index("### 02 第二节")
    idx_body_2 = lines.index("第二节的正文。")
    idx_image_2 = lines.index("![配图2（对应：02 第二节）](images/gongchang/img-02.jpg)")

    assert idx_heading_1 < idx_body_1 < idx_image_1 < idx_heading_2
    assert idx_heading_2 < idx_body_2 < idx_image_2
