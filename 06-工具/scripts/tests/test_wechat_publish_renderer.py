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


def test_build_render_payload_creates_preview_and_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(renderer, "REPORT_ROOT", tmp_path / "reports")
    article_path = _write_article(tmp_path)

    payload = renderer.build_render_payload(article_path, task_id="pub-test")

    assert payload["layout_profile"] == "raphael_wechat_v1"
    assert payload["theme_id"] == "sspai"
    assert payload["body_image_count"] == 1
    assert Path(payload["preview_html_path"]).exists()
    assert Path(payload["clipboard_html_path"]).exists()
    assert Path(payload["manifest_path"]).exists()
    assert payload["cover_path"].endswith("cover.jpg")

    blocks = payload["content_blocks"]
    assert blocks[0]["role"] == "summary"
    assert any(block["type"] == "image" for block in blocks)
    assert blocks[-1]["role"] == "cta"

    manifest = json.loads(Path(payload["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["title"] == "测试文章标题"
    assert manifest["theme_id"] == "sspai"
    assert Path(manifest["preview_html"]).exists()
    assert Path(manifest["clipboard_html"]).exists()


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
    assert Path(result["preview_html"]).exists()
    assert Path(result["render_manifest"]).exists()
