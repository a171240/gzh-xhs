#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize WeChat article image prompts into a stable bullet contract."""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from pathlib import Path
from typing import Any

from topic_doc_utils import parse_frontmatter
from wechat_image_generator import extract_prompt_specs


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
WECHAT_CONTENT_ROOT = REPO_ROOT / "02-内容生产" / "公众号" / "生成内容"
ACCOUNT_MATRIX_PATH = REPO_ROOT / "02-内容生产" / "公众号" / "账号矩阵.md"
STYLE_LIBRARY_PATH = REPO_ROOT / "02-内容生产" / "公众号" / "prompts" / "视觉风格库.md"
P4_PATH = REPO_ROOT / "02-内容生产" / "公众号" / "prompts" / "P4.md"
FRAMEWORK_KB_PATH = REPO_ROOT / "03-素材库" / "内容框架库" / "内容框架知识库.md"

SECTION_RE = re.compile(r"(?ms)^##\s+(.+?)\s*\n(.*?)(?=^##\s+|\Z)")
SUBSECTION_RE = re.compile(r"(?ms)^###\s+(.+?)\s*\n(.*?)(?=^###\s+|^##\s+|\Z)")
FIELD_RE = re.compile(r"(?im)^\s*(观点|案例细节|底层逻辑|行动第一步|反驳安全阀(?:A|B)?)\s*[:：]\s*(.+?)\s*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STYLE_SECTION_RE = re.compile(r"^##\s+风格([A-Z])：(.+?)（(.+?)专用）\s*$", re.MULTILINE)
BODY_IMAGE_LINE_RE = re.compile(r"(?m)^\s*!\[[^\]]*\]\([^)]+\)\s*$")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip().lower()


def _yaml_string(value: str) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


@dataclasses.dataclass(frozen=True)
class ArticleSection:
    title: str
    content: str


@dataclasses.dataclass(frozen=True)
class AccountProfile:
    account: str
    prefix: str
    style_name: str


@dataclasses.dataclass(frozen=True)
class StyleConfig:
    style_name: str
    account: str
    cover_visual: str
    body_visual: str
    body_decorations: str
    cover_palette: str
    body_negative: str
    cover_negative: str


COVER_SIZE_TEXT = "900×383 像素"
COVER_RATIO_TEXT = "21:9 横版宽幅"
BODY_SIZE_TEXT = "1080×1440 像素"
BODY_RATIO_TEXT = "3:4 竖版"


STYLE_CONFIGS: dict[str, StyleConfig] = {
    "知识黑板报风格": StyleConfig(
        style_name="知识黑板报风格",
        account="IP内容工厂",
        cover_visual="深墨绿色黑板背景，粉笔灰纹理，白色粉笔标题，淡黄色副标题，左右保留可裁切留白，整体像一张干净的知识黑板报封面",
        body_visual="深色黑板背景，粉笔手绘标题，流程图/图谱/结构图表达，强调框架、机制、对照关系",
        body_decorations="手绘箭头、星号、虚线、灯泡/书本图标",
        cover_palette="深墨绿色、白色、淡黄色、浅灰",
        body_negative="杂乱、信息过载、文字过小、真实照片、3D效果",
        cover_negative="文字变形扭曲、文字模糊不清、重要内容偏离中央、真实照片、3D效果",
    ),
    "温暖故事插画风格": StyleConfig(
        style_name="温暖故事插画风格",
        account="IP工厂",
        cover_visual="柔和暖色调背景，手绘水彩/彩铅质感，圆润手写体，插画人物或生活物件围绕标题，整体像一张温暖关系故事封面",
        body_visual="温暖故事插画风格，暖色调，人物状态与情绪场景化表达，突出关系张力和人生阶段感",
        body_decorations="光斑、植物、咖啡杯、书本、柔和留白",
        cover_palette="米黄、浅橙、暖粉、柔绿、深棕",
        body_negative="冷色调、科技感、硬朗线条、深色背景",
        cover_negative="文字变形扭曲、冷色调、锐利边缘、科技感、深色背景、真实照片",
    ),
    "科技蓝数据图风格": StyleConfig(
        style_name="科技蓝数据图风格",
        account="IP增长引擎",
        cover_visual="深蓝渐变背景，科技网格与数据节点，亮青色主标题与亮绿色数据，整体像一张高对比增长数据封面",
        body_visual="科技蓝数据可视化风格，深蓝背景，柱状图/折线图/矩阵图/流程图表达，突出指标、样本、增长杠杆",
        body_decorations="细线、光点、数据流、简洁图标",
        cover_palette="深蓝、亮青、亮绿、蓝色光点",
        body_negative="手绘风格、暖色调、复杂装饰、文字过多",
        cover_negative="文字变形扭曲、暖色调、手绘风格、卡通风格、文字偏离中央",
    ),
    "手账笔记风格": StyleConfig(
        style_name="手账笔记风格",
        account="商业IP实战笔记",
        cover_visual="米白色纸张背景，纸张纹理和轻微折痕，活泼手写体，荧光笔高亮，便签和贴纸围绕标题，整体像一张手账笔记封面",
        body_visual="手账笔记风格，纸张背景，手写手绘感，突出步骤、清单、checkbox 和马上执行的动作提示",
        body_decorations="和纸胶带、手绘 checkbox、便签条、贴纸图标、涂鸦箭头、荧光笔标记",
        cover_palette="米白、奶油色、深棕/黑色、黄色/粉色荧光",
        body_negative="正式版式、电子感、深色系、复杂背景",
        cover_negative="文字变形扭曲、正式排版、深色背景、科技感、3D效果、文字偏离中央",
    ),
}

STYLE_ALIASES = {
    "知识黑板报/结构图风": "知识黑板报风格",
    "知识黑板报风格": "知识黑板报风格",
    "温暖故事插画风": "温暖故事插画风格",
    "温暖故事插画风格": "温暖故事插画风格",
    "科技蓝数据图风": "科技蓝数据图风格",
    "科技蓝数据图风格": "科技蓝数据图风格",
    "手账笔记风": "手账笔记风格",
    "手账笔记风格": "手账笔记风格",
}


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _repo_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except Exception:
        return path.resolve().as_posix()


def _read_sections(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for matched in SECTION_RE.finditer(body):
        title = str(matched.group(1) or "").strip()
        content = str(matched.group(2) or "").strip()
        if title:
            out[title] = content
    return out


def _extract_subsections(body: str) -> list[ArticleSection]:
    sections: list[ArticleSection] = []
    for matched in SUBSECTION_RE.finditer(str(body or "")):
        title = str(matched.group(1) or "").strip()
        content = str(matched.group(2) or "")
        content = BODY_IMAGE_LINE_RE.sub("", content)
        content = re.sub(r"\n{3,}", "\n\n", content).strip()
        if title:
            sections.append(ArticleSection(title=title, content=content))
    return sections


def _extract_title(title_section: str) -> str:
    for raw_line in str(title_section or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "：" in line:
            key, value = line.split("：", 1)
            if key.strip() in {"主标题", "标题", "title"} and value.strip():
                return value.strip()
        return line.strip("-* ").strip()
    raise RuntimeError("missing article title")


def _replace_section(markdown_text: str, heading: str, new_content: str) -> str:
    pattern = re.compile(rf"(?ms)^##\s*{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)")
    replacement = f"## {heading}\n{new_content.strip()}\n\n"
    if pattern.search(markdown_text):
        return pattern.sub(replacement, markdown_text, count=1)
    stripped = markdown_text.rstrip() + "\n\n"
    return stripped + replacement


def _load_account_profiles() -> dict[str, AccountProfile]:
    text = _load_text(ACCOUNT_MATRIX_PATH)
    profiles_by_key: dict[str, AccountProfile] = {}
    prefix_to_style: dict[str, str] = {}

    in_style_section = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "## 配图风格（账号绑定）":
            in_style_section = True
            continue
        if in_style_section and line.startswith("## "):
            in_style_section = False
        if in_style_section:
            matched = re.match(r"^-\s*([A-Za-z0-9_-]+)\s*[:：]\s*(.+?)\s*$", line)
            if matched:
                prefix_to_style[str(matched.group(1) or "").strip()] = str(matched.group(2) or "").strip()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [item.strip() for item in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        if cells[0] in {"账号", "------"} or set(cells[0]) == {"-"}:
            continue
        account = cells[0]
        prefix = cells[1]
        style_name = prefix_to_style.get(prefix, "")
        if not account or not prefix or not style_name:
            continue
        profile = AccountProfile(account=account, prefix=prefix, style_name=style_name)
        profiles_by_key[_norm(account)] = profile
        profiles_by_key[_norm(prefix)] = profile
    return profiles_by_key


def _load_style_library() -> dict[str, StyleConfig]:
    text = _load_text(STYLE_LIBRARY_PATH)
    style_map = dict(STYLE_CONFIGS)
    for matched in STYLE_SECTION_RE.finditer(text):
        style_name = str(matched.group(2) or "").strip()
        account = str(matched.group(3) or "").strip()
        canonical = STYLE_ALIASES.get(style_name, style_name)
        current = style_map.get(canonical)
        if current:
            style_map[canonical] = dataclasses.replace(current, account=account)
    return style_map


def _resolve_article_paths(target: str) -> list[Path]:
    raw = str(target or "").strip().replace("\\", "/")
    if not raw:
        raise RuntimeError("empty target")
    if DATE_RE.match(raw):
        target_dir = WECHAT_CONTENT_ROOT / raw
        if not target_dir.exists():
            raise RuntimeError(f"date directory not found: {_repo_rel(target_dir)}")
        files = sorted(path.resolve() for path in target_dir.glob("*.md") if path.is_file())
        if not files:
            raise RuntimeError(f"no markdown files under: {_repo_rel(target_dir)}")
        return files
    path = Path(raw)
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"markdown file not found: {raw}")
    return [path]


def _extract_semantic_items(section_text: str) -> dict[str, str]:
    items: dict[str, str] = {}
    for matched in FIELD_RE.finditer(str(section_text or "")):
        items[str(matched.group(1) or "").strip()] = str(matched.group(2) or "").strip()
    return items


def _clean_text(text: str, limit: int = 100) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip(" ；;，,。.")
    return value[:limit].strip()


def _semantic_hint(text: str, *, limit: int = 120) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    if "【图片类型】" in value or "【尺寸】" in value or "【建议布局】" in value:
        return ""
    value = re.sub(r"【[^】]+】", " ", value)
    value = re.sub(r"\s+", " ", value)
    return _clean_text(value, limit=limit)


def _style_summary(style: StyleConfig) -> str:
    return f"{style.body_visual}；装饰元素包含 {style.body_decorations}；配色以 {style.cover_palette} 为主"


def _style_consistency_rule(style: StyleConfig) -> str:
    return f"整组图片必须统一为“{style.style_name}”视觉语言，禁止混入其他画风、材质和摄影风格。"


def _pick_visual_subject(title: str, summary: str) -> str:
    merged = "；".join(part for part in [_clean_text(title, 30), _clean_text(summary, 80)] if part)
    if not merged:
        return "与标题强相关的单一视觉主体，避免多个冲突焦点"
    return f"围绕“{merged}”提炼单一主视觉，不做无关背景堆砌"


def _cover_subheadline(summary: str) -> str:
    value = str(summary or "").strip()
    if not value:
        return ""
    first_sentence = re.split(r"[。；;！？?!]", value, maxsplit=1)[0]
    return _clean_text(first_sentence, 32)


def _compress_cover_title(title: str) -> str:
    raw = _clean_text(title, 48)
    if not raw:
        return ""

    matched = re.search(r"把(?P<a>[^当]{1,8})当(?P<b>.+?)(?:忽略|$)", raw)
    if matched:
        return _clean_text(f"{matched.group('a')}不等于{matched.group('b')}", 16)

    matched = re.search(r"一开口就(?P<a>.+?人).*?(?P<b>做错这[一二三四五六七八九十0-9]+步)", raw)
    if matched:
        return _clean_text(f"{matched.group('a')}{matched.group('b').replace('这', '')}", 16)

    matched = re.search(r"(把[^；，。!?]{1,8}降到\d+)", raw)
    if matched:
        return _clean_text(matched.group(1), 16)

    cleaned = re.sub(r"^今晚就能用的", "", raw)
    cleaned = re.sub(r"附[^；，。!?]{0,10}$", "", cleaned)
    matched = re.search(r"([^；，。!?]{1,18}?模板)", cleaned)
    if matched:
        return _clean_text(matched.group(1), 16)

    if len(raw) <= 16:
        return raw
    return _clean_text(raw, 16)


def _default_cover_subject(style: StyleConfig) -> str:
    mapping = {
        "知识黑板报风格": "一个粉笔手绘的核心符号，如箭头、对话气泡、连接线或灯泡图标，辅助标题表达关系与沟通主题",
        "温暖故事插画风格": "一组关系感明确的人物对话插画，人物表情和姿态清楚，辅助标题表达亲密关系场景",
        "科技蓝数据图风格": "一个简洁的数据仪表盘、趋势箭头或数值面板，辅助标题表达增长和指标变化",
        "手账笔记风格": "一张手写清单卡片、勾选框或便签纸，辅助标题表达可马上执行的步骤感",
    }
    return mapping.get(style.style_name, "一个单一、明确、可一眼识别的主视觉主体")


def _pick_cover_visual_subject(title: str, summary: str, semantic_hint: str, style: StyleConfig) -> str:
    hint = _semantic_hint(semantic_hint, limit=42)
    if hint:
        return f"围绕“{hint}”设计一个单一、明确、可一眼识别的主视觉"
    title_hint = _clean_text(title, 18)
    subject = _default_cover_subject(style)
    if not title_hint:
        return subject
    return f"围绕“{title_hint}”主题，用{subject}"


def _field_block(fields: dict[str, str]) -> str:
    ordered_keys = ("观点", "案例细节", "底层逻辑", "行动第一步", "反驳安全阀A", "反驳安全阀B")
    parts: list[str] = []
    for key in ordered_keys:
        value = _clean_text(fields.get(key, ""), 90)
        if value:
            parts.append(f"{key}={value}")
    return "；".join(parts)


def _match_existing_prompt(prompt_specs: list[Any], *, is_cover: bool, index: int, title: str) -> str:
    for spec in prompt_specs:
        if bool(getattr(spec, "is_cover", False)) != is_cover:
            continue
        if is_cover:
            return str(getattr(spec, "prompt", "") or "").strip()
        anchor = str(getattr(spec, "anchor", "") or "").strip()
        if anchor and anchor == title:
            return str(getattr(spec, "prompt", "") or "").strip()
        if int(getattr(spec, "index", 0) or 0) == index:
            return str(getattr(spec, "prompt", "") or "").strip()
    return ""


def _build_cover_prompt(
    *,
    title: str,
    summary: str,
    profile: AccountProfile,
    style: StyleConfig,
    semantic_hint: str,
) -> str:
    visual_subject = _pick_cover_visual_subject(title, summary, semantic_hint, style)
    cover_title = _compress_cover_title(title) or _clean_text(title, 16)
    parts = [
        "【图片类型】微信公众号封面图",
        "【输出目标】用于公众号头条封面，画面稳定、干净、适合中央裁切后的阅读",
        f"【账号】{profile.account}",
        f"【固定风格】{style.cover_visual}",
        f"【统一风格约束】{_style_consistency_rule(style)}",
        f"【标题文字】{cover_title}",
        f"【核心画面】{visual_subject}",
        "【构图要求】横向封面构图，但所有文字和主视觉都必须压缩在画面中央的正方形焦点区内；标题按 2 到 4 行堆叠成居中标题块；左右两侧只保留可裁切装饰，不放任何文字",
        "【文字要求】默认只保留一组清晰可读的中文主标题，不写副标题；主标题必须短、粗、居中，宽度明显小于整张封面宽度；不要出现额外说明文字、小字注释、账号名、水印、参数字样",
        "【细节要求】画面干净、焦点单一、标题醒目、文字数量尽量少，适合手机端一眼识别",
        f"【视觉语言】{style.cover_palette}",
        f"【装饰元素】{style.body_decorations}",
    ]
    parts.append(
        "【负面提示词】"
        f"{style.cover_negative}、尺寸数字、像素标注、比例标注、边框线、裁切示意框、箭头标注、教程说明文字、过多小字、复杂拼贴、横向铺满整张图的长标题、边缘文字"
    )
    return "；".join(parts)


def _build_body_prompt(
    *,
    index: int,
    section: ArticleSection,
    profile: AccountProfile,
    style: StyleConfig,
    semantic_hint: str,
) -> str:
    fields = _extract_semantic_items(section.content)
    focus_parts = [
        _clean_text(fields.get("观点", ""), 60),
        _clean_text(fields.get("案例细节", ""), 70),
        _clean_text(fields.get("底层逻辑", ""), 70),
        _clean_text(fields.get("行动第一步", ""), 70),
    ]
    focus = "；".join(part for part in focus_parts if part)
    if not focus:
        focus = _clean_text(section.content, 150)
    structured_block = _field_block(fields)

    parts = [
        "【图片类型】微信公众号正文配图",
        f"【尺寸】{BODY_SIZE_TEXT}",
        f"【比例】{BODY_RATIO_TEXT}",
        "【输出目标】公众号正文竖版信息图/步骤图，适合在手机端单张阅读，不要做横版缩略图",
        f"【账号】{profile.account}",
        f"【固定风格】{_style_summary(style)}",
        f"【统一风格约束】{_style_consistency_rule(style)}",
        f"【对应章节】{section.title}",
        f"【章节重点】{focus}",
        f"【结构化信息】{structured_block or focus}",
        f"【建议布局】顶部做章节标题区，突出“{section.title}”；中部做主信息卡片/步骤区；底部做行动提示或补充说明；整张图按从上到下的手机阅读路径排版",
        "【排版要求】必须是竖版海报式构图，主标题、关键词、图示、卡片之间层级清晰，文字不能太密，单张图只表达一个章节核心",
        f"【装饰元素】{style.body_decorations}",
        "【画面要求】避免大面积空背景，也避免过度堆满；允许少量中文关键词，但必须大字、短句、可读；不要真实摄影，不要复杂拼贴",
    ]
    parts.append(f"【负面提示词】{style.body_negative}")
    return "；".join(parts)


def _render_prompt_section(lines: list[str]) -> str:
    return "\n".join(lines).rstrip()


def normalize_markdown_file(md_path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    profiles = _load_account_profiles()
    styles = _load_style_library()
    _ = _load_text(P4_PATH)
    _ = _load_text(FRAMEWORK_KB_PATH)

    raw = _load_text(md_path)
    meta, body = parse_frontmatter(raw)
    sections = _read_sections(body)
    title = _extract_title(sections.get("标题", ""))
    body_section = sections.get("正文", "")
    if not body_section:
        raise RuntimeError(f"missing `## 正文`: {_repo_rel(md_path)}")

    subsections = _extract_subsections(body_section)
    if not subsections:
        raise RuntimeError(f"missing `###` sections under `## 正文`: {_repo_rel(md_path)}")

    summary = str(meta.get("summary") or "").strip()
    if not summary:
        raise RuntimeError(f"missing frontmatter field `summary`: {_repo_rel(md_path)}")

    account_value = str(meta.get("账号") or "").strip()
    if not account_value:
        raise RuntimeError(f"missing frontmatter field `账号`: {_repo_rel(md_path)}")

    profile = profiles.get(_norm(account_value))
    if not profile:
        raise RuntimeError(f"unknown account in frontmatter: {account_value}")

    expected_style = STYLE_ALIASES.get(profile.style_name, profile.style_name)
    style = styles.get(expected_style)
    if not style:
        raise RuntimeError(f"style template missing for account: {profile.account}")

    configured_style = str(meta.get("配图风格") or "").strip()
    if configured_style:
        normalized_configured_style = STYLE_ALIASES.get(configured_style, configured_style)
        if _norm(normalized_configured_style) != _norm(expected_style):
            raise RuntimeError(
                f"配图风格与账号绑定冲突: account={profile.account}, expected={expected_style}, actual={configured_style}"
            )

    existing_prompt_specs = extract_prompt_specs(raw)
    cover_hint = _match_existing_prompt(existing_prompt_specs, is_cover=True, index=0, title="")

    prompt_lines = [
        f"- 封面图：{_build_cover_prompt(title=title, summary=summary, profile=profile, style=style, semantic_hint=cover_hint)}"
    ]
    for idx, subsection in enumerate(subsections, start=1):
        semantic_hint = _match_existing_prompt(
            existing_prompt_specs,
            is_cover=False,
            index=idx,
            title=subsection.title,
        )
        prompt = _build_body_prompt(
            index=idx,
            section=subsection,
            profile=profile,
            style=style,
            semantic_hint=semantic_hint,
        )
        prompt_lines.append(f"- 配图{idx}（对应：{subsection.title}）：{prompt}")

    new_section = _render_prompt_section(prompt_lines)
    rewritten = _replace_section(raw, "配图提示词", new_section)

    if not dry_run:
        md_path.write_text(rewritten, encoding="utf-8")

    return {
        "path": _repo_rel(md_path),
        "account": profile.account,
        "style": expected_style,
        "prompt_count": len(prompt_lines),
        "body_prompt_count": len(subsections),
        "rewritten": not dry_run,
        "references": [
            _repo_rel(ACCOUNT_MATRIX_PATH),
            _repo_rel(STYLE_LIBRARY_PATH),
            _repo_rel(P4_PATH),
            _repo_rel(FRAMEWORK_KB_PATH),
        ],
    }


def run_target(target: str, *, dry_run: bool = False) -> dict[str, Any]:
    md_files = _resolve_article_paths(target)
    results: list[dict[str, Any]] = []
    for md_path in md_files:
        results.append(normalize_markdown_file(md_path, dry_run=dry_run))
    return {
        "status": "success",
        "target": target,
        "dry_run": dry_run,
        "processed_files": results,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize WeChat image prompt sections.")
    parser.add_argument("target", help="Batch date YYYY-MM-DD or markdown path")
    parser.add_argument("--dry-run", action="store_true", help="Validate and preview without rewriting markdown")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        payload = run_target(args.target, dry_run=bool(args.dry_run))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "target": args.target,
                    "dry_run": bool(args.dry_run),
                    "errors": [str(exc)],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
