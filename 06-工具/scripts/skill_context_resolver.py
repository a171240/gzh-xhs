#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve ordered context files for topic production tasks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skill_manifest import (
    get_repo_skill_entry,
    resolve_benchmark_report_contexts,
    resolve_quote_theme_contexts,
)
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


def _topic_field(meta: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = meta.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _topic_flag(meta: dict[str, Any], *keys: str) -> bool:
    return str(_topic_field(meta, *keys)).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
        "是",
        "开启",
        "开",
    }


def resolve_context_files(topic_path: Path, *, platform: str, skill_id: str = "") -> list[str]:
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

    # 4) 优先按 skill_id 读取 repo-local default contexts；平台级上下文只做兼容兜底。
    entry = get_repo_skill_entry(skill_id)
    if entry:
        for item in entry.default_contexts:
            add(item)
    else:
        family = _platform_family(platform)
        for item in PLATFORM_CONTEXTS.get(family, []):
            add(item)

    # 5) 公众号内容 skill 的条件上下文。
    if str(skill_id or "").strip() == "wechat":
        quote_enabled = _topic_flag(meta, "是否调用金句库", "调用金句库", "quote_enabled")
        quote_theme = _topic_field(meta, "金句主题", "quote_theme")
        benchmark_ref = _topic_field(meta, "参考对标文案", "对标文案", "benchmark_ref")
        fugui_enabled = _topic_flag(meta, "富贵模块开关", "是否启用富贵模块", "fugui_enabled")

        if quote_enabled:
            for item in resolve_quote_theme_contexts(quote_theme):
                add(item)
        if benchmark_ref:
            for item in resolve_benchmark_report_contexts(benchmark_ref):
                add(item)
        if fugui_enabled:
            add("03-素材库/增强模块/富贵-打动人模块.md")

    # 6) 抓取台 latest context（如存在）
    for item in CRAWL_CONTEXT_CANDIDATES:
        abs_path = (REPO_ROOT / item).resolve()
        if abs_path.exists() and abs_path.is_file():
            add(item)

    return ordered
