#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Utilities for topic markdown files with YAML-like frontmatter."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

FRONTMATTER_RE = re.compile(r"^\s*---\s*\n([\s\S]*?)\n---\s*\n?", re.MULTILINE)
SECTION_RE = re.compile(r"(?ms)^##\s+(.+?)\s*\n(.*?)(?=^##\s+|\Z)")


def today_str() -> str:
    return dt.date.today().isoformat()


def safe_repo_relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT).as_posix()


def parse_scalar(text: str) -> Any:
    value = str(text or "").strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        parts = [item.strip().strip('"').strip("'") for item in inner.split(",")]
        return [item for item in parts if item]
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    raw = str(text or "").lstrip("\ufeff")
    matched = FRONTMATTER_RE.match(raw)
    if not matched:
        return {}, raw

    front_text = matched.group(1)
    body = raw[matched.end() :]
    data: dict[str, Any] = {}
    for line in front_text.splitlines():
        item = line.strip()
        if not item or item.startswith("#") or ":" not in item:
            continue
        key, value = item.split(":", 1)
        key = key.strip()
        if not key:
            continue
        data[key] = parse_scalar(value)
    return data, body


def format_scalar(value: Any) -> str:
    if isinstance(value, list):
        clean = [str(item).strip() for item in value if str(item).strip()]
        return "[" + ", ".join(clean) + "]"
    text = str(value if value is not None else "").strip()
    return text


def dump_frontmatter(meta: dict[str, Any], body: str, *, key_order: list[str] | None = None) -> str:
    lines: list[str] = ["---"]
    seen: set[str] = set()
    if key_order:
        for key in key_order:
            if key in meta:
                lines.append(f"{key}: {format_scalar(meta[key])}")
                seen.add(key)
    for key, value in meta.items():
        if key in seen:
            continue
        lines.append(f"{key}: {format_scalar(value)}")
    lines.append("---")
    lines.append("")
    content = (body or "").lstrip("\n")
    return "\n".join(lines) + content


def parse_sections(body: str) -> dict[str, str]:
    text = str(body or "")
    sections: dict[str, str] = {}
    for matched in SECTION_RE.finditer(text):
        title = str(matched.group(1) or "").strip()
        content = str(matched.group(2) or "").strip()
        if title:
            sections[title] = content
    return sections


def normalize_platforms(value: Any) -> list[str]:
    if isinstance(value, list):
        values = value
    elif isinstance(value, str):
        raw = value.strip()
        values = [item.strip() for item in re.split(r"[,，/|]", raw) if item.strip()]
    else:
        values = []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item).strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out


def normalize_related(value: Any) -> list[str]:
    values = normalize_platforms(value)
    return [item.replace("\\", "/") for item in values]


def ensure_required_topic_meta(meta: dict[str, Any], *, source: str = "") -> dict[str, Any]:
    topic = str(meta.get("topic") or "").strip() or "未命名选题"
    target = str(meta.get("target") or "").strip() or "未指定"
    date_str = str(meta.get("date") or "").strip() or today_str()
    platforms = normalize_platforms(meta.get("platforms")) or ["公众号"]
    related = normalize_related(meta.get("related"))
    status = str(meta.get("status") or "").strip() or "待生产"

    next_meta = dict(meta)
    next_meta["date"] = date_str
    next_meta["topic"] = topic
    next_meta["target"] = target
    next_meta["platforms"] = platforms
    next_meta["related"] = related
    next_meta["status"] = status
    if source:
        next_meta["source"] = source
    elif str(next_meta.get("source") or "").strip():
        next_meta["source"] = str(next_meta.get("source") or "").strip().replace("\\", "/")
    else:
        next_meta["source"] = ""
    return next_meta
