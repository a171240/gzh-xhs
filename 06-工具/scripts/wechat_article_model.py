#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a stable article model for WeChat rendering/publishing."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


SECTION_HEADING_RE = re.compile(r"^\s*###\s+(.+?)\s*$")
IMAGE_LINE_RE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$")


def _compact_markdown(lines: list[str]) -> str:
    text = "\n".join(lines).replace("\r\n", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _resolve_image(article_path: Path, raw_path: str, alt: str) -> dict[str, str]:
    raw = str(raw_path or "").strip()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (article_path.parent / candidate).resolve()
    return {
        "alt": str(alt or "").strip(),
        "raw_path": raw,
        "path": str(candidate),
    }


def build_article_model(
    *,
    article_path: Path,
    title: str,
    summary: str,
    body_markdown: str,
    cta_markdown: str = "",
) -> dict[str, Any]:
    """Parse a WeChat markdown article body into a stable semantic model."""

    lines = str(body_markdown or "").replace("\r\n", "\n").split("\n")
    lead_lines: list[str] = []
    lead_images: list[dict[str, str]] = []
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def ensure_current(heading: str) -> dict[str, Any]:
        nonlocal current
        current = {
            "heading": heading,
            "body_lines": [],
            "images": [],
        }
        sections.append(current)
        return current

    for line in lines:
        heading_match = SECTION_HEADING_RE.match(line)
        if heading_match:
            ensure_current(str(heading_match.group(1) or "").strip())
            continue

        image_match = IMAGE_LINE_RE.match(line)
        if image_match:
            image_info = _resolve_image(
                article_path,
                str(image_match.group(2) or "").strip(),
                str(image_match.group(1) or "").strip(),
            )
            if current is None:
                lead_images.append(image_info)
            else:
                current["images"].append(image_info)
            continue

        if current is None:
            lead_lines.append(line)
        else:
            current["body_lines"].append(line)

    normalized_sections: list[dict[str, Any]] = []
    for section in sections:
        heading = str(section.get("heading") or "").strip()
        body_text = _compact_markdown(list(section.get("body_lines") or []))
        images = list(section.get("images") or [])
        if not heading and not body_text and not images:
            continue
        normalized_sections.append(
            {
                "heading": heading,
                "body_markdown": body_text,
                "images": images,
            }
        )

    return {
        "title": str(title or "").strip(),
        "summary": str(summary or "").strip(),
        "lead_markdown": _compact_markdown(lead_lines),
        "lead_images": lead_images,
        "sections": normalized_sections,
        "cta_markdown": str(cta_markdown or "").strip(),
    }
