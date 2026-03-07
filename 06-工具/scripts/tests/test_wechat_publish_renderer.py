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

import publish_action_runner as runner
import wechat_publish_renderer as renderer
from wechat_article_model import build_article_model


def _write_article(root: Path, *, include_summary: bool = True) -> Path:
    article_dir = root / "02-内容生产" / "公众号" / "生成内容" / "2099-01-01"
    image_dir = article_dir / "images" / "gongchang"
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "cover.jpg").write_bytes(b"cover")
    (image_dir / "img-01.jpg").write_bytes(b"body")

    summary_line = 'summary: "这是可用于导语卡片的摘要。"\n' if include_summary else ""
    article = (
        "---\n"
        '账号: "IP内容工厂"\n'
        f"{summary_line}"
        'cover_image: "images/gongchang/cover.jpg"\n'
        "---\n\n"
        "## 标题\n"
        "主标题：测试文章标题\n\n"
        "## 正文\n"
        "第一段正文。\n\n"
        "### 小节一\n"
        "第二段正文，带 **加粗**。\n\n"
        "![配图1](images/gongchang/img-01.jpg)\n\n"
        "第三段正文。\n\n"
        "## CTA\n"
        "评论区回复「测试手册」领取模板。\n"
    )
    article_path = article_dir / "gongchang-20990101-测试文章.md"
    article_path.write_text(article, encoding="utf-8")
    return article_path


def test_build_article_model_keeps_section_order_and_images(tmp_path: Path) -> None:
    article_path = _write_article(tmp_path)
    raw = article_path.read_text(encoding="utf-8")
    meta, body = renderer.parse_frontmatter(raw)
    article_model = build_article_model(
        article_path=article_path,
        title="测试文章标题",
        summary=str(meta.get("summary") or ""),
        body_markdown=renderer._find_section(body, "body"),
        cta_markdown=renderer._find_section(body, "cta"),
    )

    assert article_model["lead_markdown"] == "第一段正文。"
    assert len(article_model["sections"]) == 1
    assert article_model["sections"][0]["heading"] == "小节一"
    assert article_model["sections"][0]["body_markdown"] == "第二段正文，带 **加粗**。\n\n第三段正文。"
    assert len(article_model["sections"][0]["images"]) == 1
    assert article_model["sections"][0]["images"][0]["raw_path"] == "images/gongchang/img-01.jpg"


def test_build_render_payload_creates_preview_and_publish_safe_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(renderer, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(renderer, "REPORT_ROOT", tmp_path / "reports")
    article_path = _write_article(tmp_path)

    payload = renderer.build_render_payload(article_path, task_id="pub-test")

    assert payload["layout_profile"] == "raphael_wechat_v1"
    assert payload["theme_id"] == "sspai"
    assert payload["body_image_count"] == 1
    assert payload["section_count"] == 1
    assert Path(payload["preview_html_path"]).exists()
    assert Path(payload["clipboard_html_path"]).exists()
    assert Path(payload["manifest_path"]).exists()
    assert payload["cover_path"].endswith("cover.jpg")

    blocks = payload["content_blocks"]
    publish_blocks = payload["publish_blocks"]
    preview_blocks = payload["preview_blocks"]
    assert blocks == publish_blocks
    assert preview_blocks != publish_blocks
    assert blocks[0]["role"] == "summary"
    assert any(block["role"] == "lead" for block in blocks)
    assert any(block["role"] == "section_heading" for block in blocks)
    assert any(block["type"] == "image" for block in blocks)
    assert blocks[-1]["role"] == "cta"
    assert any(block["role"] == "image_spacer_before" for block in blocks)
    assert any(block["role"] == "image_spacer_after" for block in blocks)

    body_block = next(block for block in blocks if block.get("role") in {"lead", "body"})
    assert 'data-role="body"' not in body_block["html"]
    assert 'data-role="lead"' not in body_block["html"]
    assert "font-weight:400 !important;" in body_block["html"]
    assert "<table" not in body_block["html"]
    assert "font-size:16px" in body_block["html"]

    heading_block = next(block for block in blocks if block.get("role") == "section_heading")
    assert "01 01" not in heading_block["html"]
    assert "小节一" in heading_block["html"]

    heading_index = next(idx for idx, block in enumerate(blocks) if block.get("role") == "section_heading")
    paragraph_index = next(
        idx
        for idx, block in enumerate(blocks)
        if idx > heading_index and block.get("role") == "body" and "<p" in str(block.get("html") or "")
    )
    spacer_before_index = next(idx for idx, block in enumerate(blocks) if block.get("role") == "image_spacer_before")
    image_index = next(idx for idx, block in enumerate(blocks) if block.get("type") == "image")
    spacer_after_index = next(idx for idx, block in enumerate(blocks) if block.get("role") == "image_spacer_after")
    assert heading_index < paragraph_index < spacer_before_index < image_index < spacer_after_index

    manifest = json.loads(Path(payload["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["title"] == "测试文章标题"
    assert manifest["theme_id"] == "sspai"
    assert manifest["section_count"] == 1
    assert manifest["article_model"]["sections"][0]["heading"] == "小节一"
    assert manifest["publish_blocks"] == publish_blocks
    assert Path(manifest["preview_html"]).exists()
    assert Path(manifest["clipboard_html"]).exists()

    preview_html = Path(payload["preview_html_path"]).read_text(encoding="utf-8")
    clipboard_html = Path(payload["clipboard_html_path"]).read_text(encoding="utf-8")
    assert 'data-role="lead"' in preview_html
    assert 'data-role="section-heading"' in preview_html
    assert "<section" in clipboard_html
    assert "font-size:24px" in preview_html


def test_build_render_payload_requires_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(renderer, "REPORT_ROOT", tmp_path / "reports")
    article_path = _write_article(tmp_path, include_summary=False)

    with pytest.raises(renderer.RenderError, match="summary"):
        renderer.build_render_payload(article_path, task_id="pub-test")


def test_preview_publish_returns_preview_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(renderer, "REPORT_ROOT", tmp_path / "reports")
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)

    article_path = _write_article(tmp_path)
    result = runner.preview_publish({"platform": "wechat", "content": str(article_path)})

    assert result["status"] == "success"
    assert result["platform"] == "wechat"
    assert result["body_image_count"] == 1
    assert result["section_count"] == 1
    assert Path(result["preview_html"]).exists()
    assert Path(result["render_manifest"]).exists()
