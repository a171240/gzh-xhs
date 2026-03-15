#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared helpers for the repo-local Xiaohongshu pipeline."""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import shutil
from pathlib import Path
from typing import Any

from topic_brief_builder import build_brief_from_payload, load_topic_payload
from topic_doc_utils import dump_frontmatter, parse_frontmatter, parse_sections, safe_repo_relative, today_str

REPO_ROOT = Path(__file__).resolve().parents[2]
XHS_CONTENT_ROOT = REPO_ROOT / "02-内容生产" / "小红书" / "生成内容"
XHS_ARCHIVE_ROOT = REPO_ROOT / "01-选题管理" / "03-已发布" / "小红书"

INFO_MODE = "信息图6页"
EMOTION_MODE = "情绪冲突文字帖"
ACCOUNT_PREFIX = {"A": "xhs-a", "B": "xhs-b", "C": "xhs-c"}
ACCOUNT_ROLE = {"A": "A号（转化）", "B": "B号（交付）", "C": "C号（观点）"}
ACCOUNT_DEFAULT_MODE = {"A": INFO_MODE, "B": INFO_MODE, "C": EMOTION_MODE}
XHS_FRONTMATTER_ORDER = [
    "platform",
    "account",
    "account_prefix",
    "mode",
    "source_topic",
    "chosen_title",
    "title_candidates",
    "tags",
    "publish_ready",
    "image_manifest",
]
LABEL_BLOCK_RE = re.compile(r"(?ms)^([^\n:：]{2,24})\s*[:：]\s*(.*?)(?=^[^\n:：]{2,24}\s*[:：]\s*|\Z)")
PROMPT_SLOT_ORDER = {
    INFO_MODE: ["p1", "p2", "p3", "p4", "p5", "p6"],
    EMOTION_MODE: ["cover", "body-01", "body-02", "body-03"],
}


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def canonicalize_xhs_account(account: str, *, allow_empty: bool = False) -> str:
    text = str(account or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise ValueError("xhs account is required")
    normalized = _normalize_key(text)
    alias_map = {
        "a": "A",
        "a号": "A",
        "a号转化": "A",
        "转化": "A",
        "转化号": "A",
        "xhsa": "A",
        "xhsa号": "A",
        "xhsa号": "A",
        "xhsa": "A",
        "b": "B",
        "b号": "B",
        "b号交付": "B",
        "交付": "B",
        "交付号": "B",
        "xhsb": "B",
        "xhsb号": "B",
        "c": "C",
        "c号": "C",
        "c号观点": "C",
        "观点": "C",
        "观点号": "C",
        "xhsc": "C",
        "xhsc号": "C",
    }
    if normalized in alias_map:
        return alias_map[normalized]
    for canonical, prefix in ACCOUNT_PREFIX.items():
        if normalized in {
            _normalize_key(prefix),
            _normalize_key(ACCOUNT_ROLE[canonical]),
            _normalize_key(f"{canonical}号"),
        }:
            return canonical
    if allow_empty:
        return ""
    raise ValueError(f"unsupported xhs account: {account}")


def xhs_account_prefix(account: str) -> str:
    return ACCOUNT_PREFIX[canonicalize_xhs_account(account)]


def xhs_account_role(account: str) -> str:
    return ACCOUNT_ROLE[canonicalize_xhs_account(account)]


def normalize_xhs_mode(mode: str, *, account: str = "") -> str:
    normalized = _normalize_key(mode)
    if normalized in {"信息图", "信息图6页", _normalize_key(INFO_MODE)}:
        return INFO_MODE
    if normalized in {"情绪冲突", "文字帖", "情绪冲突文字帖", _normalize_key(EMOTION_MODE)}:
        return EMOTION_MODE
    if account:
        return ACCOUNT_DEFAULT_MODE[canonicalize_xhs_account(account)]
    return INFO_MODE


def xhs_prompt_slots(mode: str) -> list[str]:
    return list(PROMPT_SLOT_ORDER[normalize_xhs_mode(mode)])


def _slugify(value: str, *, limit: int = 24) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", " ", str(value or "")).strip()
    text = re.sub(r"\s+", "-", text).strip("-")
    return text[:limit].strip("-") or "untitled"


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out


def _clean_list(text: str) -> list[str]:
    items: list[str] = []
    for raw in str(text or "").splitlines():
        line = re.sub(r"^[\-\*\d\.\、\)）\s]+", "", str(raw or "").strip())
        if line:
            items.append(line)
    return items


def _extract_label_block(text: str, *labels: str) -> str:
    wanted = {str(label or "").strip() for label in labels if str(label or "").strip()}
    for matched in LABEL_BLOCK_RE.finditer(str(text or "")):
        label = str(matched.group(1) or "").strip()
        if label in wanted:
            return str(matched.group(2) or "").strip()
    return ""


def _section_text(sections: dict[str, str], body: str, *names: str) -> str:
    for name in names:
        value = str(sections.get(name) or "").strip()
        if value:
            return value
    return _extract_label_block(body, *names)


def _title_candidates(meta: dict[str, Any], sections: dict[str, str], body: str) -> list[str]:
    raw = meta.get("title_candidates")
    if isinstance(raw, list):
        return _dedupe([str(item).strip() for item in raw])[:3]
    for key in ("标题候选",):
        section_text = _section_text(sections, body, key, "标题")
        if section_text:
            return _dedupe(_clean_list(section_text))[:3]
    for key in ("chosen_title", "title", "标题"):
        value = str(meta.get(key) or "").strip()
        if value:
            return [value]
    return []


def _tag_list(meta: dict[str, Any], sections: dict[str, str], body: str) -> list[str]:
    raw = meta.get("tags")
    if isinstance(raw, list):
        return _dedupe([str(item).strip().lstrip("#") for item in raw])[:10]
    if isinstance(raw, str) and raw.strip():
        return _dedupe([item.strip().lstrip("#") for item in re.split(r"[\s,，]+", raw) if item.strip()])[:10]
    text = _section_text(sections, body, "标签")
    tags = [item.lstrip("#") for item in re.findall(r"#?([A-Za-z0-9_\-\u4e00-\u9fff]{1,24})", text)]
    return _dedupe(tags)[:10]


def _normalize_prompt_slot(label: str, *, mode: str, order: int) -> str:
    text = str(label or "").strip()
    if normalize_xhs_mode(mode) == INFO_MODE and ("封面" in text or _normalize_key(text) == "cover"):
        return "p1"
    if "封面" in text or _normalize_key(text) == "cover":
        return "cover"
    matched = re.search(r"(\d+)", text)
    index = int(matched.group(1)) if matched else max(1, order)
    if normalize_xhs_mode(mode) == INFO_MODE:
        return f"p{index}"
    return f"body-{index:02d}"


def parse_xhs_prompt_contract(prompt_text: str, *, mode: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    bullet_re = re.compile(r"^\s*[-*]\s*([^:：]+)\s*[:：]\s*(.*)$")
    for raw in str(prompt_text or "").splitlines():
        line = str(raw or "").rstrip()
        matched = bullet_re.match(line)
        if matched:
            if current:
                items.append(current)
            label = str(matched.group(1) or "").strip()
            prompt = str(matched.group(2) or "").strip()
            current = {
                "slot": _normalize_prompt_slot(label, mode=mode, order=len(items) + 1),
                "label": label,
                "prompt": prompt,
            }
            continue
        if current is not None:
            continuation = line.strip()
            if continuation:
                current["prompt"] = f"{current['prompt']}\n{continuation}".strip()
    if current:
        items.append(current)
    if items:
        return sort_xhs_prompt_contract(items, mode=mode)

    order = 1
    for raw in str(prompt_text or "").splitlines():
        line = str(raw or "").strip()
        if not line:
            continue
        if ":" in line:
            label, prompt = line.split(":", 1)
        elif "：" in line:
            label, prompt = line.split("：", 1)
        else:
            continue
        items.append(
            {
                "slot": _normalize_prompt_slot(label, mode=mode, order=order),
                "label": str(label or "").strip(),
                "prompt": str(prompt or "").strip(),
            }
        )
        order += 1
    return sort_xhs_prompt_contract(items, mode=mode)


def sort_xhs_prompt_contract(items: list[dict[str, Any]], *, mode: str) -> list[dict[str, Any]]:
    order_map = {slot: index for index, slot in enumerate(xhs_prompt_slots(mode))}
    return sorted(items, key=lambda item: order_map.get(str(item.get("slot") or ""), 999))


def validate_xhs_prompt_contract(items: list[dict[str, Any]], *, mode: str) -> list[str]:
    errors: list[str] = []
    allowed = set(xhs_prompt_slots(mode))
    seen: set[str] = set()
    for item in items:
        slot = str(item.get("slot") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if slot not in allowed:
            errors.append(f"unsupported prompt slot: {slot or 'empty'}")
            continue
        if slot in seen:
            errors.append(f"duplicate prompt slot: {slot}")
        if not prompt:
            errors.append(f"empty prompt for slot: {slot}")
        seen.add(slot)
    if normalize_xhs_mode(mode) == INFO_MODE:
        expected = xhs_prompt_slots(mode)
        missing = [slot for slot in expected if slot not in seen]
        if missing:
            errors.append(f"missing infographic prompt slots: {', '.join(missing)}")
    else:
        if "cover" not in seen:
            errors.append("missing prompt slot: cover")
        if len([slot for slot in seen if slot.startswith("body-")]) > 3:
            errors.append("emotion mode supports at most 3 body prompts")
    return errors


def format_xhs_prompt_contract(items: list[dict[str, Any]], *, mode: str) -> str:
    ordered = sort_xhs_prompt_contract(items, mode=mode)
    return "\n".join(f"- {item['slot']}: {str(item.get('prompt') or '').strip()}" for item in ordered if str(item.get("prompt") or "").strip())


def xhs_manifest_path(content_path: Path) -> Path:
    return content_path.with_suffix(".images.json")


def _section_text_by_prefix(sections: dict[str, str], *prefixes: str) -> str:
    wanted = [_normalize_key(prefix) for prefix in prefixes if str(prefix or "").strip()]
    for title, value in sections.items():
        normalized = _normalize_key(title)
        if any(normalized.startswith(prefix) for prefix in wanted):
            text = str(value or "").strip()
            if text:
                return text
    return ""


def _strip_leading_h1(text: str) -> str:
    return re.sub(r"(?ms)^\s*#\s+.+?\n", "", str(text or "").strip(), count=1).strip()


def _extract_generated_titles(text: str) -> list[str]:
    return _dedupe(_clean_list(text))[:3]


def _extract_generated_tags(text: str) -> list[str]:
    return _dedupe([item.lstrip("#") for item in re.findall(r"#([A-Za-z0-9_\-\u4e00-\u9fff]{1,24})", str(text or ""))])[:10]


def _extract_generated_pinned_comment(text: str) -> str:
    stripped = _strip_leading_h1(text)
    if not stripped:
        return "关闭"
    return re.split(r"(?m)^##\s+", stripped, maxsplit=1)[0].strip() or "关闭"


def _parse_generated_prompt_file(text: str, *, mode: str) -> list[dict[str, Any]]:
    sections = parse_sections(_strip_leading_h1(text))
    items: list[dict[str, Any]] = []
    for order, (title, body) in enumerate(sections.items(), start=1):
        prompt = str(body or "").strip()
        if not prompt:
            continue
        items.append(
            {
                "slot": _normalize_prompt_slot(title, mode=mode, order=order),
                "label": str(title or "").strip(),
                "prompt": prompt,
            }
        )
    return sort_xhs_prompt_contract(items, mode=mode)


def _extract_layout_pages(layout_body: str) -> dict[str, str]:
    pages: dict[str, str] = {}
    for matched in re.finditer(r"(?m)^[\-\*]\s*P?(\d+)\s*[:：]\s*(.+)$", str(layout_body or "")):
        pages[f"p{int(matched.group(1))}"] = str(matched.group(2) or "").strip()
    return pages


def _synthesize_prompt_items_from_layout(*, mode: str, layout_body: str) -> list[dict[str, Any]]:
    if normalize_xhs_mode(mode) != INFO_MODE:
        return []
    pages = _extract_layout_pages(layout_body)
    items: list[dict[str, Any]] = []
    for index, slot in enumerate(xhs_prompt_slots(mode), start=1):
        summary = pages.get(slot, "")
        page_kind = "封面信息图" if slot == "p1" else "轮播图内页"
        page_title = f"P{index} {page_kind}"
        prompt_lines = [
            "画幅比例3:4竖版。",
            f"【图片类型】小红书{page_kind}",
            "【视觉风格】浅色背景、紫色高亮、信息清晰，适合手机端阅读。",
        ]
        if summary:
            prompt_lines.append(f"【核心文案】{summary}")
        prompt_lines.append("【版式要求】标题醒目，主信息置中，留白充足，避免人物与复杂背景。")
        items.append({"slot": slot, "label": page_title, "prompt": "\n".join(prompt_lines)})
    return items


def _profile_from_generated_files(
    saved_files: list[Path],
    *,
    account: str = "",
    source_topic: str = "",
    title_index: int = 1,
    chosen_title: str = "",
) -> XHSContentProfile:
    resolved_account = canonicalize_xhs_account(account or "A", allow_empty=True) or "A"
    package_path = next((path for path in saved_files if "发布包" in path.stem), saved_files[0])
    structure_path = next((path for path in saved_files if "结构稿" in path.stem), None)
    prompt_path = next((path for path in saved_files if "配图提示词" in path.stem), None)
    comment_path = next((path for path in saved_files if "置顶评论" in path.stem), None)
    execution_path = next((path for path in saved_files if "发布执行与质检" in path.stem), None)

    package_text = package_path.read_text(encoding="utf-8", errors="ignore")
    package_sections = parse_sections(package_text)
    execution_sections = (
        parse_sections(execution_path.read_text(encoding="utf-8", errors="ignore"))
        if execution_path is not None
        else {}
    )
    mode = normalize_xhs_mode(_section_text_by_prefix(package_sections, "模式"), account=resolved_account)
    title_candidates = _extract_generated_titles(_section_text_by_prefix(package_sections, "标题"))
    selected_title = str(chosen_title or "").strip()
    if not selected_title and title_candidates:
        safe_index = max(1, int(title_index or 1))
        selected_title = title_candidates[min(safe_index, len(title_candidates)) - 1]
    publish_body = _section_text_by_prefix(package_sections, "发布正文")
    if not publish_body:
        publish_body = _section_text_by_prefix(package_sections, "Step2")
    if not publish_body and structure_path is not None:
        publish_body = _strip_leading_h1(structure_path.read_text(encoding="utf-8", errors="ignore"))
    layout_body = _section_text_by_prefix(package_sections, "6页落地文案", "正文最终稿")
    if not layout_body and structure_path is not None:
        layout_body = publish_body
    prompt_items = _parse_generated_prompt_file(
        prompt_path.read_text(encoding="utf-8", errors="ignore") if prompt_path else "",
        mode=mode,
    )
    if not prompt_items:
        prompt_items = _synthesize_prompt_items_from_layout(mode=mode, layout_body=layout_body)
    pinned_comment = _extract_generated_pinned_comment(comment_path.read_text(encoding="utf-8", errors="ignore")) if comment_path else ""
    if not pinned_comment and execution_sections:
        pinned_comment = _section_text_by_prefix(execution_sections, "置顶评论")
    qc_text = str(_section_text_by_prefix(package_sections, "质检") or "").strip()
    if not qc_text and execution_sections:
        qc_text = str(_section_text_by_prefix(execution_sections, "发布质检") or "").strip()
    return XHSContentProfile(
        path=package_path.resolve(),
        rel_path=safe_repo_relative(package_path),
        meta={"date": today_str()},
        mode=mode,
        account=resolved_account,
        account_prefix=ACCOUNT_PREFIX[resolved_account],
        source_topic=str(source_topic or "").strip(),
        chosen_title=selected_title,
        title_candidates=title_candidates[:3],
        tags=_extract_generated_tags(_section_text_by_prefix(package_sections, "标签")),
        publish_body=str(publish_body or "").strip(),
        layout_body=str(layout_body or "").strip(),
        pinned_comment=pinned_comment or "关闭",
        prompt_text=format_xhs_prompt_contract(prompt_items, mode=mode) if prompt_items else "",
        prompt_contract=prompt_items,
        qc_text=qc_text,
        publish_ready=False,
        image_manifest_path="",
        images=[],
        image_items=[],
        raw_body=package_text,
    )


@dataclasses.dataclass
class XHSContentProfile:
    path: Path
    rel_path: str
    meta: dict[str, Any]
    mode: str
    account: str
    account_prefix: str
    source_topic: str
    chosen_title: str
    title_candidates: list[str]
    tags: list[str]
    publish_body: str
    layout_body: str
    pinned_comment: str
    prompt_text: str
    prompt_contract: list[dict[str, Any]]
    qc_text: str
    publish_ready: bool
    image_manifest_path: str
    images: list[str]
    image_items: list[dict[str, Any]]
    raw_body: str


def parse_xhs_content(
    text: str,
    *,
    source_path: Path | None = None,
    account: str = "",
    source_topic: str = "",
    title_index: int = 1,
    chosen_title: str = "",
) -> XHSContentProfile:
    meta, body = parse_frontmatter(text)
    sections = parse_sections(body)
    resolved_account = canonicalize_xhs_account(account or str(meta.get("account") or meta.get("账号") or ""), allow_empty=True) or "A"
    mode = normalize_xhs_mode(str(meta.get("mode") or meta.get("模式") or _extract_label_block(body, "模式")), account=resolved_account)
    title_candidates = _title_candidates(meta, sections, body)
    selected_title = str(chosen_title or meta.get("chosen_title") or "").strip()
    if not selected_title and title_candidates:
        safe_index = max(1, int(title_index or 1))
        selected_title = title_candidates[min(safe_index, len(title_candidates)) - 1]
    publish_body = _section_text(sections, body, "发布正文", "正文")
    layout_heading = "6页文案" if mode == INFO_MODE else "正文最终稿"
    layout_body = _section_text(sections, body, layout_heading, "正文/文案", "文案")
    if not publish_body:
        publish_body = layout_body
    prompt_text = _section_text(sections, body, "配图提示词")
    manifest_rel = str(meta.get("image_manifest") or "").strip()
    manifest_items: list[dict[str, Any]] = []
    manifest_images: list[str] = []
    if source_path is not None:
        if manifest_rel:
            raw_manifest = Path(manifest_rel)
            if raw_manifest.is_absolute():
                manifest_path = raw_manifest.resolve()
            else:
                repo_manifest = (REPO_ROOT / manifest_rel).resolve()
                sibling_manifest = (source_path.parent / manifest_rel).resolve()
                manifest_path = repo_manifest if repo_manifest.exists() else sibling_manifest
        else:
            manifest_path = xhs_manifest_path(source_path)
        if manifest_path.exists():
            payload = json.loads(manifest_path.read_text(encoding="utf-8", errors="ignore"))
            raw_images = payload.get("images") if isinstance(payload, dict) else []
            if isinstance(raw_images, list):
                for item in raw_images:
                    if not isinstance(item, dict):
                        continue
                    manifest_items.append(item)
                    rel = str(item.get("rel_path") or item.get("path") or "").strip()
                    if rel:
                        manifest_images.append(rel)
            try:
                manifest_rel = manifest_path.relative_to(REPO_ROOT).as_posix()
            except Exception:
                manifest_rel = manifest_path.as_posix()
    rel_path = ""
    if source_path is not None:
        try:
            rel_path = source_path.resolve().relative_to(REPO_ROOT).as_posix()
        except Exception:
            rel_path = source_path.resolve().as_posix()
    return XHSContentProfile(
        path=source_path.resolve() if source_path else Path("."),
        rel_path=rel_path,
        meta=meta,
        mode=mode,
        account=resolved_account,
        account_prefix=ACCOUNT_PREFIX[resolved_account],
        source_topic=str(source_topic or meta.get("source_topic") or meta.get("source") or "").strip(),
        chosen_title=selected_title,
        title_candidates=title_candidates[:3],
        tags=_tag_list(meta, sections, body),
        publish_body=str(publish_body or "").strip(),
        layout_body=str(layout_body or "").strip(),
        pinned_comment=str(_section_text(sections, body, "置顶评论") or "关闭").strip() or "关闭",
        prompt_text=str(prompt_text or "").strip(),
        prompt_contract=parse_xhs_prompt_contract(prompt_text, mode=mode),
        qc_text=str(_section_text(sections, body, "质检清单") or "").strip(),
        publish_ready=str(meta.get("publish_ready") or "").strip().lower() in {"1", "true", "yes", "y", "on"},
        image_manifest_path=manifest_rel,
        images=manifest_images,
        image_items=manifest_items,
        raw_body=body,
    )


def render_xhs_content(profile: XHSContentProfile) -> str:
    meta = {
        "platform": "小红书",
        "account": profile.account,
        "account_prefix": profile.account_prefix,
        "mode": profile.mode,
        "source_topic": profile.source_topic,
        "chosen_title": profile.chosen_title,
        "title_candidates": profile.title_candidates[:3],
        "tags": profile.tags[:10],
        "publish_ready": "true" if profile.publish_ready else "false",
        "image_manifest": profile.image_manifest_path,
    }
    sections: list[str] = []
    sections.append("## 标题候选\n" + "\n".join(f"- {item}" for item in profile.title_candidates if item))
    sections.append("## 标签\n" + " ".join(f"#{item}" for item in profile.tags if item))
    sections.append("## 发布正文\n" + profile.publish_body)
    sections.append(f"## {'6页文案' if profile.mode == INFO_MODE else '正文最终稿'}\n" + profile.layout_body)
    sections.append("## 置顶评论\n" + (profile.pinned_comment or "关闭"))
    sections.append(
        "## 配图提示词\n"
        + (
            format_xhs_prompt_contract(profile.prompt_contract, mode=profile.mode)
            if profile.prompt_contract
            else profile.prompt_text
        )
    )
    sections.append("## 质检清单\n" + profile.qc_text)
    body = "\n\n".join(section.rstrip() for section in sections if section.strip()) + "\n"
    return dump_frontmatter(meta, body, key_order=XHS_FRONTMATTER_ORDER)


def canonical_xhs_content_path(*, account: str, date_str: str, chosen_title: str) -> Path:
    canonical = canonicalize_xhs_account(account)
    stamp = str(date_str or today_str()).strip() or today_str()
    return XHS_CONTENT_ROOT / stamp / f"{ACCOUNT_PREFIX[canonical]}-{stamp.replace('-', '')}-{_slugify(chosen_title)}.md"


def save_xhs_content_profile(profile: XHSContentProfile, *, target_path: Path | None = None) -> Path:
    date_str = str(profile.meta.get("date") or today_str()).strip() or today_str()
    final_path = target_path or canonical_xhs_content_path(
        account=profile.account,
        date_str=date_str,
        chosen_title=profile.chosen_title or (profile.title_candidates[0] if profile.title_candidates else "未命名"),
    )
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(render_xhs_content(profile), encoding="utf-8")
    return final_path


def canonicalize_xhs_content_file(
    source_path: Path,
    *,
    account: str = "",
    source_topic: str = "",
    title_index: int = 1,
    chosen_title: str = "",
) -> Path:
    profile = parse_xhs_content(
        source_path.read_text(encoding="utf-8", errors="ignore"),
        source_path=source_path,
        account=account,
        source_topic=source_topic,
        title_index=title_index,
        chosen_title=chosen_title,
    )
    return save_xhs_content_profile(profile)


def canonicalize_xhs_saved_files(
    saved_files: list[Path],
    *,
    account: str = "",
    source_topic: str = "",
    title_index: int = 1,
    chosen_title: str = "",
) -> Path:
    if not saved_files:
        raise RuntimeError("xhs content generation returned no markdown files")
    expanded: list[Path] = []
    seen: set[str] = set()

    def add_candidate(path: Path) -> None:
        resolved = path.resolve()
        key = resolved.as_posix()
        if key in seen or not resolved.exists() or not resolved.is_file():
            return
        seen.add(key)
        expanded.append(resolved)

    for path in saved_files:
        add_candidate(path)
    for path in list(expanded):
        prefix = path.stem.rsplit("-", 1)[0]
        for sibling in sorted(path.parent.glob(f"{prefix}-*.md")):
            add_candidate(sibling)

    if len(expanded) == 1:
        return canonicalize_xhs_content_file(
            expanded[0],
            account=account,
            source_topic=source_topic,
            title_index=title_index,
            chosen_title=chosen_title,
        )
    profile = _profile_from_generated_files(
        expanded,
        account=account,
        source_topic=source_topic,
        title_index=title_index,
        chosen_title=chosen_title,
    )
    return save_xhs_content_profile(profile)


def normalize_xhs_prompt_file(content_path: Path) -> dict[str, Any]:
    profile = parse_xhs_content(content_path.read_text(encoding="utf-8", errors="ignore"), source_path=content_path)
    errors = validate_xhs_prompt_contract(profile.prompt_contract, mode=profile.mode)
    if errors:
        raise RuntimeError("; ".join(errors))
    save_xhs_content_profile(profile, target_path=content_path)
    return {
        "status": "success",
        "processed_files": [
            {
                "path": safe_repo_relative(content_path),
                "mode": profile.mode,
                "prompt_slots": [str(item.get("slot") or "") for item in profile.prompt_contract],
            }
        ],
    }


def load_xhs_content_profile(content_path: Path) -> XHSContentProfile:
    return parse_xhs_content(content_path.read_text(encoding="utf-8", errors="ignore"), source_path=content_path)


def validate_xhs_image_manifest(profile: XHSContentProfile) -> list[str]:
    errors: list[str] = []
    if not profile.image_manifest_path:
        errors.append("image_manifest is empty")
        return errors
    if not profile.images:
        errors.append("image manifest has no images")
        return errors
    slots = [str(item.get("slot") or "").strip() for item in profile.image_items]
    if profile.mode == INFO_MODE and slots != xhs_prompt_slots(profile.mode):
        errors.append(f"infographic image slots must be exactly: {', '.join(xhs_prompt_slots(profile.mode))}")
    if profile.mode == EMOTION_MODE:
        if not slots or slots[0] != "cover":
            errors.append("emotion mode image manifest must start with cover")
        if len(slots) < 1 or len(slots) > 4:
            errors.append("emotion mode image count must be between 1 and 4")
    for rel in profile.images:
        if not (REPO_ROOT / rel).resolve().exists():
            errors.append(f"missing image file: {rel}")
    return errors


def validate_xhs_publish_profile(profile: XHSContentProfile) -> list[str]:
    errors: list[str] = []
    if not profile.chosen_title:
        errors.append("chosen_title is empty")
    if not profile.publish_body:
        errors.append("publish body is empty")
    errors.extend(validate_xhs_image_manifest(profile))
    return _dedupe(errors)


def build_xhs_brief_from_topic(topic_path: Path, *, account: str = "") -> str:
    payload = load_topic_payload(topic_path)
    if account:
        payload = dict(payload)
        meta = dict(payload.get("meta") or {})
        meta["account"] = canonicalize_xhs_account(account)
        payload["meta"] = meta
    return build_brief_from_payload(payload, platform="小红书")


def run_xhs_content(
    *,
    brief: str,
    topic_file: Path | None = None,
    account: str = "",
    title_index: int = 1,
    chosen_title: str = "",
    model: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    from feishu_skill_runner import DEFAULT_MODEL, run_skill_task

    source_topic = safe_repo_relative(topic_file) if topic_file else ""
    result = run_skill_task(
        skill_id="xhs",
        brief=brief,
        platform="小红书",
        model=model or DEFAULT_MODEL,
        source_ref=f"xhs-pipeline:{source_topic}" if source_topic else "xhs-pipeline",
        dry_run=dry_run,
    )
    if dry_run or str(result.get("status") or "") != "success":
        return result
    saved_files: list[Path] = []
    for item in result.get("saved_files") or []:
        path = Path(str(item))
        saved_files.append(path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve())
    canonical_path = canonicalize_xhs_saved_files(
        saved_files,
        account=account,
        source_topic=source_topic,
        title_index=title_index,
        chosen_title=chosen_title,
    )
    updated = dict(result)
    updated["raw_saved_files"] = [safe_repo_relative(path) for path in saved_files]
    updated["saved_files"] = [safe_repo_relative(canonical_path)]
    updated["content_path"] = safe_repo_relative(canonical_path)
    return updated


def run_xhs_prompt_normalize(*, content_file: Path) -> dict[str, Any]:
    return normalize_xhs_prompt_file(content_file)


def run_xhs_images(*, content_file: Path, dry_run: bool = False) -> dict[str, Any]:
    from xhs_image_generator import process_xhs_content_file

    return process_xhs_content_file(content_file, dry_run=dry_run)


def prepare_xhs_publish(*, content_file: Path, dry_run: bool = False) -> dict[str, Any]:
    from publish_action_runner import prepare_publish

    profile = load_xhs_content_profile(content_file)
    return prepare_publish(
        {"platform": "xhs", "content": safe_repo_relative(content_file), "account": profile.account},
        dry_run=dry_run,
    )


def approve_xhs_publish(*, task_id: str, dry_run: bool = False) -> dict[str, Any]:
    from publish_action_runner import approve_publish

    return approve_publish({"task_id": task_id}, dry_run=dry_run)


def archive_xhs_publish_result(task: dict[str, Any], approve_result: dict[str, Any]) -> str:
    payload = task.get("payload_json") or {}
    content_ref = str(payload.get("content_path") or payload.get("content_ref") or "").strip()
    if not content_ref:
        return ""
    content_path = (REPO_ROOT / content_ref).resolve()
    if not content_path.exists():
        return ""
    profile = load_xhs_content_profile(content_path)
    date_str = str(profile.meta.get("date") or today_str()).strip() or today_str()
    archive_dir = XHS_ARCHIVE_ROOT / date_str
    archive_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(content_path, archive_dir / content_path.name)
    manifest_path = xhs_manifest_path(content_path)
    if manifest_path.exists():
        shutil.copy2(manifest_path, archive_dir / manifest_path.name)
    snapshot = {
        "title": profile.chosen_title,
        "tags": profile.tags,
        "note_id": str((approve_result.get("approve") or {}).get("note_id") or ""),
        "note_url": str((approve_result.get("approve") or {}).get("note_url") or ""),
        "screenshot_paths": list((approve_result.get("approve") or {}).get("screenshot_paths") or []),
        "run_log": str((approve_result.get("approve") or {}).get("run_log") or ""),
    }
    snapshot_path = archive_dir / f"{content_path.stem}.publish.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return safe_repo_relative(archive_dir)


def build_xhs_pipeline_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="XHS pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_brief_parser = subparsers.add_parser("build-brief")
    build_brief_parser.add_argument("--topic-file", required=True)
    build_brief_parser.add_argument("--account", default="")

    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--topic-file", default="")
    generate_parser.add_argument("--brief-file", default="")
    generate_parser.add_argument("--account", default="")
    generate_parser.add_argument("--title-index", type=int, default=1)
    generate_parser.add_argument("--chosen-title", default="")
    generate_parser.add_argument("--dry-run", action="store_true")

    images_parser = subparsers.add_parser("images")
    images_parser.add_argument("--content-file", required=True)
    images_parser.add_argument("--dry-run", action="store_true")

    prepare_parser = subparsers.add_parser("publish-prepare")
    prepare_parser.add_argument("--content-file", required=True)
    prepare_parser.add_argument("--dry-run", action="store_true")

    approve_parser = subparsers.add_parser("publish-approve")
    approve_parser.add_argument("--task-id", required=True)
    approve_parser.add_argument("--dry-run", action="store_true")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--topic-file", required=True)
    run_parser.add_argument("--account", default="")
    run_parser.add_argument("--title-index", type=int, default=1)
    run_parser.add_argument("--chosen-title", default="")
    run_parser.add_argument("--dry-run", action="store_true")
    return parser


def _read_brief_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def run_xhs_pipeline_command(args: argparse.Namespace) -> dict[str, Any]:
    command = str(args.command or "").strip()
    if command == "build-brief":
        topic_path = Path(args.topic_file).resolve()
        return {"status": "success", "brief": build_xhs_brief_from_topic(topic_path, account=str(args.account or "").strip())}

    if command == "generate":
        topic_path: Path | None = None
        if str(args.topic_file or "").strip():
            topic_path = Path(args.topic_file).resolve()
            brief = build_xhs_brief_from_topic(topic_path, account=str(args.account or "").strip())
        elif str(args.brief_file or "").strip():
            brief = _read_brief_file(Path(args.brief_file).resolve())
        else:
            raise ValueError("topic-file or brief-file is required")
        return run_xhs_content(
            brief=brief,
            topic_file=topic_path,
            account=str(args.account or "").strip(),
            title_index=int(args.title_index or 1),
            chosen_title=str(args.chosen_title or "").strip(),
            dry_run=bool(args.dry_run),
        )

    if command == "images":
        return run_xhs_images(content_file=Path(args.content_file).resolve(), dry_run=bool(args.dry_run))

    if command == "publish-prepare":
        return prepare_xhs_publish(content_file=Path(args.content_file).resolve(), dry_run=bool(args.dry_run))

    if command == "publish-approve":
        return approve_xhs_publish(task_id=str(args.task_id or "").strip(), dry_run=bool(args.dry_run))

    if command == "run":
        topic_path = Path(args.topic_file).resolve()
        brief = build_xhs_brief_from_topic(topic_path, account=str(args.account or "").strip())
        content_result = run_xhs_content(
            brief=brief,
            topic_file=topic_path,
            account=str(args.account or "").strip(),
            title_index=int(args.title_index or 1),
            chosen_title=str(args.chosen_title or "").strip(),
            dry_run=bool(args.dry_run),
        )
        stages: dict[str, Any] = {"content": content_result}
        if bool(args.dry_run):
            stages["prompt_normalize"] = {"status": "skipped", "reason": "dry_run_content_has_no_output"}
            stages["images"] = {"status": "skipped", "reason": "dry_run_content_has_no_output"}
            stages["publish_prepare"] = {"status": "skipped", "reason": "dry_run_content_has_no_output"}
            stages["publish_approve"] = {"status": "skipped", "reason": "dry_run_content_has_no_output"}
            return {"status": "success", "stages": stages}
        content_path = (REPO_ROOT / str(content_result.get("content_path") or "")).resolve()
        stages["prompt_normalize"] = run_xhs_prompt_normalize(content_file=content_path)
        stages["images"] = run_xhs_images(content_file=content_path, dry_run=False)
        stages["publish_prepare"] = prepare_xhs_publish(content_file=content_path, dry_run=False)
        return {"status": "success", "stages": stages}

    raise ValueError(f"unsupported command: {command}")
