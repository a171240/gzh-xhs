#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve ordered context files for topic production tasks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from topic_doc_utils import normalize_related, parse_frontmatter

REPO_ROOT = Path(__file__).resolve().parents[2]

PLANNING_FILES = [
    "01-选题管理/选题规划/选题素材库.md",
    "01-选题管理/选题规划/金句选题池.md",
]

PLATFORM_CONTEXTS: dict[str, list[str]] = {
    "公众号": [
        "skills/自有矩阵/公众号批量生产.md",
        "03-素材库/增强模块/公众号爆文增强版.md",
        "03-素材库/增强模块/富贵-打动人模块.md",
    ],
    "小红书": [
        "skills/自有矩阵/小红书内容生产.md",
        "02-内容生产/小红书/resources/30天内容日历.md",
        "02-内容生产/小红书/resources/高情绪标题公式.md",
        "02-内容生产/小红书/resources/证据素材库.md",
        "02-内容生产/小红书/resources/消费者洞察词库.md",
        "03-素材库/增强模块/富贵-打动人模块.md",
    ],
    "短视频": [
        "skills/自有矩阵/短视频脚本生产.md",
        "03-素材库/内容框架库/认知框架库.md",
        "03-素材库/情绪价值点库/情绪价值点库.md",
    ],
}

CRAWL_CONTEXT_CANDIDATES = [
    "06-工具/内容抓取/抓取内容/contexts/latest-url-crawl.md",
    "06-工具/内容抓取/抓取内容/contexts/latest-keyword-matrix.md",
    "06-工具/内容抓取/抓取内容/contexts/latest-crawl-brief.md",
]


def _safe_rel(path: str) -> str:
    text = str(path or "").strip().replace("\\", "/")
    if not text:
        return ""
    return text.lstrip("./")


def _platform_family(platform: str) -> str:
    text = str(platform or "").strip()
    if text in {"抖音", "视频号"}:
        return "短视频"
    return text


def _read_topic_meta(topic_path: Path) -> dict[str, Any]:
    try:
        text = topic_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}
    meta, _ = parse_frontmatter(text)
    return meta if isinstance(meta, dict) else {}


def resolve_context_files(topic_path: Path, *, platform: str) -> list[str]:
    rel_topic = topic_path.resolve().relative_to(REPO_ROOT).as_posix()
    meta = _read_topic_meta(topic_path)

    ordered: list[str] = []
    seen: set[str] = set()

    def add(item: str) -> None:
        rel = _safe_rel(item)
        if not rel or rel in seen:
            return
        seen.add(rel)
        ordered.append(rel)

    # 1) 当前待生产文件
    add(rel_topic)

    # 2) related 引用
    for item in normalize_related(meta.get("related")):
        add(item)

    # 3) 选题规划文档
    for item in PLANNING_FILES:
        add(item)

    # 4) 平台核心与资源
    family = _platform_family(platform)
    for item in PLATFORM_CONTEXTS.get(family, []):
        add(item)

    # 5) 抓取台 latest context（如存在）
    for item in CRAWL_CONTEXT_CANDIDATES:
        abs_path = (REPO_ROOT / item).resolve()
        if abs_path.exists() and abs_path.is_file():
            add(item)

    return ordered

