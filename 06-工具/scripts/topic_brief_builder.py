#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build platform-specific generation briefs from topic files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from topic_doc_utils import ensure_required_topic_meta, parse_frontmatter, parse_sections

XHS_ACCOUNT_ROLE = {
    "A": "A号（转化）",
    "B": "B号（交付）",
    "C": "C号（观点）",
}


def _clean_line(text: str) -> str:
    item = str(text or "").strip()
    item = re.sub(r"^[\-\*\d\.\、\s]+", "", item)
    return item.strip()


def _extract_core_conflict(topic_analysis: str) -> str:
    lines = [_clean_line(line) for line in str(topic_analysis or "").splitlines() if _clean_line(line)]
    if not lines:
        return "核心冲突未明确，请先给出反差与痛点。"
    for line in lines:
        if "矛盾" in line or "痛点" in line:
            return line
    return lines[0]


def _extract_cta(outline: str) -> str:
    text = str(outline or "")
    matched = re.search(r"(?im)\bCTA\b\s*[:：]\s*(.+)$", text)
    if matched:
        return _clean_line(matched.group(1))

    lines = [_clean_line(line) for line in text.splitlines() if _clean_line(line)]
    for line in reversed(lines):
        if "行动" in line or "反馈" in line or "评论" in line:
            return line
    return "评论区聊聊你的真实场景"


def _collect_related(meta: dict[str, Any]) -> str:
    related = meta.get("related")
    if isinstance(related, list):
        items = [str(item).strip() for item in related if str(item).strip()]
    else:
        items = []
    return "\n".join(f"- {item}" for item in items) if items else "- 无"


def _pick_topic_value(meta: dict[str, Any], sections: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = meta.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    for key in keys:
        value = sections.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return ""


def _pick_topic_value_with_default(meta: dict[str, Any], sections: dict[str, str], default: str, *keys: str) -> str:
    picked = _pick_topic_value(meta, sections, *keys)
    return picked if picked else str(default or "")


def _normalize_xhs_account(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())
    mapping = {
        "a": "A",
        "a号": "A",
        "转化": "A",
        "转化号": "A",
        "xhsa": "A",
        "b": "B",
        "b号": "B",
        "交付": "B",
        "交付号": "B",
        "xhsb": "B",
        "c": "C",
        "c号": "C",
        "观点": "C",
        "观点号": "C",
        "xhsc": "C",
    }
    return mapping.get(normalized, "")


def load_topic_payload(topic_path: Path) -> dict[str, Any]:
    raw = topic_path.read_text(encoding="utf-8", errors="ignore")
    meta, body = parse_frontmatter(raw)
    sections = parse_sections(body)
    normalized = ensure_required_topic_meta(meta)
    return {
        "meta": normalized,
        "body": body,
        "sections": sections,
    }


def build_brief_from_payload(payload: dict[str, Any], *, platform: str) -> str:
    meta = payload.get("meta") or {}
    sections = payload.get("sections") or {}

    topic = str(meta.get("topic") or "").strip() or "未命名选题"
    target = str(meta.get("target") or "").strip() or "未指定"
    date_str = str(meta.get("date") or "").strip()
    topic_analysis = str(sections.get("选题分析") or "").strip()
    outline = str(sections.get("内容大纲") or "").strip()
    cta = _extract_cta(outline)
    conflict = _extract_core_conflict(topic_analysis)
    related_block = _collect_related(meta)
    platform_text = str(platform or "").strip()

    quote_default = "是" if platform_text == "公众号" else "否"
    quote_enabled = _pick_topic_value_with_default(meta, sections, quote_default, "是否调用金句库", "调用金句库")
    quote_theme = _pick_topic_value(meta, sections, "金句主题", "quote_theme")
    benchmark_ref = _pick_topic_value(meta, sections, "参考对标文案", "对标文案", "benchmark_ref")
    fugui_enabled = _pick_topic_value(meta, sections, "富贵模块开关", "是否启用富贵模块", "fugui_enabled")
    story_enabled = _pick_topic_value_with_default(meta, sections, "自动", "是否需要人设故事", "人设故事", "need_story")
    story_usage = _pick_topic_value(meta, sections, "故事用途", "story_usage")

    if platform_text == "公众号":
        return (
            f"日期(YYYY-MM-DD，可空，默认今天)：{date_str}\n"
            f"主题矿区/选题：{topic}\n"
            f"目标人群：{target}\n"
            f"核心矛盾：{conflict}\n"
            f"场景证据/案例素材：\n{topic_analysis or '待补充'}\n"
            f"希望读者做的第一步：{cta}\n"
            f"引流物(CTA关键词)：{cta}\n"
            "禁区(可选)：不夸大收益，不编造来源\n"
            f"是否调用金句库：{quote_enabled}\n"
            f"金句主题：{quote_theme}\n"
            f"参考对标文案：{benchmark_ref}\n"
            f"富贵模块开关：{fugui_enabled}\n"
            f"是否需要人设故事：{story_enabled}\n"
            f"故事用途：{story_usage}\n"
            f"related参考：\n{related_block}\n"
        )

    if platform_text == "小红书":
        account = _normalize_xhs_account(_pick_topic_value(meta, sections, "account", "账号"))
        mode = _pick_topic_value(meta, sections, "模式", "mode") or ("情绪冲突文字帖" if account == "C" else "信息图6页")
        account_role = _pick_topic_value(meta, sections, "账号角色", "account_role") or XHS_ACCOUNT_ROLE.get(account, "A号（转化）")
        goal = _pick_topic_value(meta, sections, "核心目标", "goal") or "收藏+评论"
        pinned = _pick_topic_value(meta, sections, "置顶互动开关", "置顶评论开关", "pinned_comment") or "ON"
        return (
            f"模式：{mode}\n"
            f"账号角色：{account_role}\n"
            f"主关键词：{topic}\n"
            f"目标人群：{target}\n"
            f"核心目标：{goal}\n"
            f"置顶互动开关：{pinned}\n"
            f"是否调用金句库：{quote_enabled}\n"
            f"金句主题：{quote_theme}\n"
            f"核心矛盾：{conflict}\n"
            f"场景证据：\n{topic_analysis or '待补充'}\n"
            f"希望读者做的第一步：{cta}\n"
            f"参考对标文案：{benchmark_ref}\n"
            f"富贵模块开关：{fugui_enabled or '否'}\n"
            f"是否需要人设故事：{story_enabled}\n"
            f"故事用途：{story_usage}\n"
            f"related参考：\n{related_block}\n"
        )

    if platform_text in {"抖音", "视频号"}:
        return (
            f"平台：{platform_text}\n"
            f"选题：{topic}\n"
            f"目标人群：{target}\n"
            "时长：60秒\n"
            "风格：讲解型\n"
            f"核心矛盾：{conflict}\n"
            f"CTA：{cta}\n"
            f"素材备注：\n{topic_analysis or '待补充'}\n"
            f"related参考：\n{related_block}\n"
        )

    return (
        f"主题：{topic}\n"
        f"目标人群：{target}\n"
        f"核心矛盾：{conflict}\n"
        f"CTA：{cta}\n"
        f"相关素材：\n{related_block}\n"
    )


def build_brief(topic_path: Path, *, platform: str) -> str:
    payload = load_topic_payload(topic_path)
    return build_brief_from_payload(payload, platform=platform)
