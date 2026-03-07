#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render WeChat article markdown into preview assets and publish payloads."""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from topic_doc_utils import parse_frontmatter
from wechat_article_model import build_article_model


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = REPO_ROOT / "reports"
COMPILER_ROOT = Path(__file__).resolve().parent / "wechat_layout_compiler"
COMPILER_ENTRY = COMPILER_ROOT / "render.js"
COMPILER_NODE_MODULES = COMPILER_ROOT / "node_modules"

TITLE_LINE_RE = re.compile(r"^(?:主标题|标题|title)\s*[:：]\s*(.+?)\s*$", re.IGNORECASE)

META_ALIASES = {
    "account": ("账号", "account"),
    "author": ("作者", "author", "作者名"),
    "summary": ("summary", "摘要"),
    "cover_image": ("cover_image", "封面图", "cover"),
    "title": ("title", "标题"),
    "theme_id": ("theme_id", "排版主题", "theme"),
}

SECTION_ALIASES = {
    "title": ("标题",),
    "body": ("正文",),
    "cta": ("CTA", "行动建议"),
    "image_prompts": ("配图提示词",),
}

ACCOUNT_THEME_MAP = {
    "IP内容工厂": "sspai",
    "gongchang": "sspai",
    "IP工厂": "sunset",
    "ipgc": "sunset",
    "IP增长引擎": "github",
    "zengzhang": "github",
    "商业IP实战笔记": "mint",
    "shizhan": "mint",
}


class RenderError(RuntimeError):
    """Raised when the article cannot be rendered into a publishable format."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip().lower()


def _meta_value(meta: dict[str, Any], key: str) -> str:
    aliases = META_ALIASES.get(key, ())
    for alias in aliases:
        for candidate_key, value in meta.items():
            if _norm(candidate_key) == _norm(alias):
                return str(value or "").strip()
    return ""


def _find_section(body: str, key: str) -> str:
    aliases = SECTION_ALIASES.get(key, ())
    text = str(body or "")
    for alias in aliases:
        pattern = re.compile(rf"(?ms)^##\s*{re.escape(alias)}\s*\n(.*?)(?=^##\s+|\Z)")
        matched = pattern.search(text)
        if matched:
            return str(matched.group(1) or "").strip()
    return ""


def _extract_title(meta: dict[str, Any], title_section: str, body: str) -> str:
    from_meta = _meta_value(meta, "title")
    if from_meta:
        return from_meta
    for line in str(title_section or "").splitlines():
        stripped = line.strip().strip("-").strip("*").strip()
        if not stripped:
            continue
        matched = TITLE_LINE_RE.match(stripped)
        if matched:
            return str(matched.group(1) or "").strip()
        return stripped
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return re.sub(r"^#+\s*", "", stripped).strip()
    raise RenderError("missing article title")


def _resolve_local_path(md_path: Path, raw_path: str, *, required: bool = True) -> Path | None:
    value = str(raw_path or "").strip()
    if not value:
        if required:
            raise RenderError("missing local path")
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = (md_path.parent / candidate).resolve()
    if required and not candidate.exists():
        raise RenderError(f"file not found: {candidate}")
    return candidate


def _preview_dir(md_path: Path, *, preview_root: Path | None = None, task_id: str = "") -> Path:
    if preview_root is not None:
        return preview_root
    stamp = dt.datetime.now().strftime("%Y-%m-%d")
    suffix = task_id or uuid.uuid4().hex[:8]
    return REPORT_ROOT / stamp / f"wechat-layout-{md_path.stem}-{suffix}"


def _theme_for_article(meta: dict[str, Any], article_path: Path) -> str:
    explicit = _meta_value(meta, "theme_id")
    if explicit:
        return explicit
    account = _meta_value(meta, "account")
    if account in ACCOUNT_THEME_MAP:
        return ACCOUNT_THEME_MAP[account]
    stem = article_path.stem.split("-", 1)[0]
    return ACCOUNT_THEME_MAP.get(stem, "sspai")


def _run_compiler(payload: dict[str, Any], *, preview_dir: Path) -> dict[str, Any]:
    if not COMPILER_ENTRY.exists():
        raise RenderError(f"missing compiler entry: {COMPILER_ENTRY}")
    if not COMPILER_NODE_MODULES.exists():
        raise RenderError(f"missing compiler dependencies under {COMPILER_ROOT}; run `npm install` first")

    compiler_input = preview_dir / "compiler-input.json"
    compiler_output = preview_dir / "compiler-output.json"
    compiler_input.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    proc = subprocess.run(
        ["node", str(COMPILER_ENTRY), "--input", str(compiler_input)],
        cwd=str(COMPILER_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    if proc.returncode != 0:
        stderr = str(proc.stderr or proc.stdout or "").strip()
        raise RenderError(f"layout compiler failed: {stderr}")

    compiler_output.write_text(proc.stdout, encoding="utf-8")
    try:
        return json.loads(proc.stdout)
    except Exception as exc:
        raise RenderError(f"invalid compiler output: {exc}") from exc


def build_render_payload(
    md_path: Path,
    *,
    layout_profile: str = "raphael_wechat_v1",
    preview_root: Path | None = None,
    task_id: str = "",
) -> dict[str, Any]:
    article_path = Path(md_path).resolve()
    raw = article_path.read_text(encoding="utf-8", errors="ignore")
    meta, body = parse_frontmatter(raw)

    title_section = _find_section(body, "title")
    body_section = _find_section(body, "body")
    cta_section = _find_section(body, "cta")
    summary = _meta_value(meta, "summary")
    account = _meta_value(meta, "account")
    author = _meta_value(meta, "author") or account
    title = _extract_title(meta, title_section, body)
    cover_image = _meta_value(meta, "cover_image")
    theme_id = _theme_for_article(meta, article_path)

    if not summary:
        raise RenderError("missing frontmatter field: summary")
    if not body_section:
        raise RenderError("missing `## 正文` section")
    if not cover_image:
        raise RenderError("missing frontmatter field: cover_image")

    cover_path = _resolve_local_path(article_path, cover_image, required=True)
    article_model = build_article_model(
        article_path=article_path,
        title=title,
        summary=summary,
        body_markdown=body_section,
        cta_markdown=cta_section,
    )
    preview_dir = _preview_dir(article_path, preview_root=preview_root, task_id=task_id)
    preview_dir.mkdir(parents=True, exist_ok=True)

    compiled = _run_compiler(
        {
            "article_path": str(article_path),
            "article_dir": str(article_path.parent),
            "article_model": article_model,
            "theme_id": theme_id,
            "layout_profile": layout_profile,
        },
        preview_dir=preview_dir,
    )

    publish_blocks = list(compiled.get("publish_blocks") or compiled.get("content_blocks") or [])
    preview_blocks = list(compiled.get("preview_blocks") or [])
    body_image_count = sum(1 for block in publish_blocks if block.get("type") == "image")
    section_count = len(list(article_model.get("sections") or []))

    preview_html_path = preview_dir / "preview.html"
    preview_html_path.write_text(str(compiled.get("preview_html") or ""), encoding="utf-8")

    clipboard_html_path = preview_dir / "clipboard.html"
    clipboard_html_path.write_text(str(compiled.get("clipboard_html") or ""), encoding="utf-8")

    manifest_path = preview_dir / "render-manifest.json"
    manifest = {
        "article_path": article_path.relative_to(REPO_ROOT).as_posix()
        if article_path.is_relative_to(REPO_ROOT)
        else article_path.as_posix(),
        "title": title,
        "account": account,
        "author": author,
        "theme_id": str(compiled.get("theme_id") or theme_id),
        "layout_profile": str(compiled.get("layout_profile") or layout_profile),
        "summary": summary,
        "cover_path": str(cover_path),
        "cover_path_rel": cover_path.relative_to(article_path.parent).as_posix(),
        "section_count": section_count,
        "article_model": article_model,
        "preview_html": preview_html_path.as_posix(),
        "clipboard_html": clipboard_html_path.as_posix(),
        "preview_blocks": preview_blocks,
        "publish_blocks": publish_blocks,
        "content_blocks": publish_blocks,
        "content_html": str(compiled.get("content_html") or ""),
        "body_image_count": body_image_count,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        **manifest,
        "preview_dir": preview_dir.as_posix(),
        "preview_html_path": preview_html_path.as_posix(),
        "clipboard_html_path": clipboard_html_path.as_posix(),
        "manifest_path": manifest_path.as_posix(),
    }
