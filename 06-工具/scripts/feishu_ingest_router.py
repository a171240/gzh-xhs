#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Routing and processing helpers for Feishu message ingestion."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from link_to_quotes import LinkToQuotesResult, process_urls_to_quotes
from quote_ingest_core import (
    DEFAULT_NEAR_DUP_THRESHOLD,
    NearDupItem,
    SourceTextItem,
    append_quotes,
    build_candidates,
    extract_date,
    load_existing_quotes,
    unique_topic_entries,
    write_topic_pool,
)

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
SHORT_LINK_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:v\.douyin\.com|xhslink\.com|b23\.tv)/[A-Za-z0-9_-]+/?(?:\?[^\s<>\"'`]+)?",
    re.IGNORECASE,
)
COMMAND_RE = re.compile(r"^\s*/[\w\u4e00-\u9fff-]+")
REPLY_PREFIX_RE = re.compile(
    r"^\s*(?:(?:\d+\s*[\.、]\s*)?)?(?:(?:回复|reply)\s+[^:：\n]{1,80}\s*[:：]\s*)",
    re.IGNORECASE,
)
BLOCKQUOTE_PREFIX_RE = re.compile(r"^\s*(?:[|｜>＞]+\s*)+")
LEADING_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[·•●▪▫◦○・\-*]+\s*)+")
QUOTE_AT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:\d+\s*[\.、]\s*)?)?(?:(?:回复|reply)\s*[^:：\n]{0,80}\s*[:：]\s*)?(?:[·•●▪▫◦○・\-*]+\s*)?[\"'“”‘’]?[@＠]\s*(?P<mention>[^:：，,\n]+?)\s*(?:[:：]\s*|\n+)(?P<body>[\s\S]+?)\s*$",
    re.IGNORECASE,
)
QUOTE_TEXT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:\d+\s*[\.、]\s*)?)?(?:(?:回复|reply)\s*[^:：\n]{0,80}\s*[:：]\s*)?(?:[·•●▪▫◦○・\-*]+\s*)?金句\s*(?:[:：]\s*|\n+)(?P<body>[\s\S]+?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RoutedMessage:
    mode: str
    urls: list[str]
    quote_text: str
    original_text: str


@dataclass(frozen=True)
class QuoteIngestResult:
    added_count: int
    near_dup_count: int
    exact_dup_count: int
    touched_files: list[Path]
    topic_total: int
    near_dups: list[NearDupItem]


@dataclass(frozen=True)
class MessageProcessSummary:
    mode: str
    quote_added_count: int
    quote_near_dup_count: int
    quote_exact_dup_count: int
    link_total: int
    link_success: int
    link_failed: int
    link_doc_saved_count: int
    touched_files: list[str]
    errors: list[str]
    import_record_path: str


def parse_feishu_text_content(content: str) -> str:
    raw = str(content or "").strip()
    if not raw:
        return ""

    try:
        data = json.loads(raw)
    except Exception:
        return raw

    if isinstance(data, dict):
        return str(data.get("text") or "").strip()
    return raw


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        cleaned = str(url or "").strip().rstrip(".,;:!?，。；：！？）)]》」』")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def _extract_urls(text: str) -> list[str]:
    raw = str(text or "")
    urls: list[str] = list(URL_RE.findall(raw))
    for match in SHORT_LINK_RE.finditer(raw):
        value = str(match.group(0) or "").strip()
        if not value:
            continue
        if not value.lower().startswith("http"):
            value = f"https://{value}"
        urls.append(value)
    return _dedupe_urls(urls)


def _strip_command_shell(text: str) -> str:
    cleaned = COMMAND_RE.sub("", text, count=1)
    return cleaned.strip()


def _extract_quote_after_mention(text: str) -> tuple[bool, str]:
    raw = str(text or "").strip()
    if not raw:
        return False, ""

    # Feishu thread replies may prefix the text with `回复 某某：`.
    # Strip at most two such prefixes before trigger detection.
    normalized = raw
    for _ in range(3):
        normalized = BLOCKQUOTE_PREFIX_RE.sub("", normalized).lstrip()
        normalized = LEADING_BULLET_PREFIX_RE.sub("", normalized).lstrip()
        prefix = REPLY_PREFIX_RE.match(normalized)
        if not prefix:
            break
        normalized = normalized[prefix.end() :].lstrip()

    # Some clients keep one or more quote markers before the actual trigger.
    normalized = BLOCKQUOTE_PREFIX_RE.sub("", normalized).lstrip()
    normalized = LEADING_BULLET_PREFIX_RE.sub("", normalized).lstrip()

    matched = QUOTE_AT_PREFIX_RE.match(normalized) or QUOTE_TEXT_PREFIX_RE.match(normalized)
    if not matched:
        return False, ""

    quote_text = re.sub(r"\s+", " ", str(matched.group("body") or "")).strip(" \t\r\n:：")
    if not quote_text:
        return False, ""
    return True, quote_text


def route_message_text(text: str) -> RoutedMessage:
    original = str(text or "").strip()
    if not original:
        return RoutedMessage(mode="ignore", urls=[], quote_text="", original_text="")

    if re.match(r"^\s*/push-topics\b", original, flags=re.IGNORECASE):
        return RoutedMessage(mode="ignore", urls=[], quote_text="", original_text=original)

    body = _strip_command_shell(original)
    urls = _extract_urls(body)

    # Product rule: if a message contains URL(s), treat it as benchmark-link input only.
    if urls:
        return RoutedMessage(mode="link_mode", urls=urls, quote_text="", original_text=original)

    quote_hit, quote_text = _extract_quote_after_mention(body)
    if quote_hit:
        return RoutedMessage(mode="quote_mode", urls=[], quote_text=quote_text, original_text=original)

    return RoutedMessage(mode="ignore", urls=[], quote_text="", original_text=original)


def ingest_quote_text(
    *,
    quote_text: str,
    quote_dir: Path,
    topic_pool_path: Path,
    apply_mode: bool,
    source_time: str,
    source_ref: str,
    near_dup_threshold: float,
) -> QuoteIngestResult:
    if not quote_text.strip():
        return QuoteIngestResult(
            added_count=0,
            near_dup_count=0,
            exact_dup_count=0,
            touched_files=[],
            topic_total=0,
            near_dups=[],
        )

    existing = load_existing_quotes(quote_dir)
    source_items = [
        SourceTextItem(
            source_time=source_time,
            text=quote_text,
            source_kind="feishu-text",
            source_ref=source_ref,
        )
    ]
    added, near_dups, exact_dup_count = build_candidates(
        source_items,
        existing,
        near_dup_threshold=max(0.1, min(0.99, near_dup_threshold)),
        split_input=False,
    )

    touched_files: list[Path] = []
    topic_total = 0
    if apply_mode:
        touched_files.extend(append_quotes(quote_dir, added))
        updated_existing = load_existing_quotes(quote_dir)
        new_time_map = {item.norm: extract_date(item.source_time) or source_time[:10] for item in added}
        topic_entries = unique_topic_entries(updated_existing, new_time_map)
        write_topic_pool(topic_pool_path, topic_entries)
        touched_files.append(topic_pool_path)
        topic_total = len(topic_entries)

    return QuoteIngestResult(
        added_count=len(added),
        near_dup_count=len(near_dups),
        exact_dup_count=exact_dup_count,
        touched_files=touched_files,
        topic_total=topic_total,
        near_dups=near_dups,
    )


def _append_import_record(
    *,
    import_record_path: Path,
    source_time: str,
    source_ref: str,
    mode: str,
    routed: RoutedMessage,
    quote_result: QuoteIngestResult,
    link_result: LinkToQuotesResult | None,
    errors: list[str],
) -> None:
    import_record_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    if not import_record_path.exists():
        lines.append("# 飞书金句入库记录\n\n")

    lines.append(f"## {source_time} event={source_ref}\n")
    lines.append(f"- mode: `{mode}`\n")
    lines.append(f"- quote_added: {quote_result.added_count}\n")
    lines.append(f"- quote_near_dup: {quote_result.near_dup_count}\n")
    lines.append(f"- quote_exact_dup: {quote_result.exact_dup_count}\n")

    if routed.quote_text:
        preview = routed.quote_text
        if len(preview) > 120:
            preview = preview[:117] + "..."
        lines.append(f"- quote_preview: {preview}\n")

    if routed.urls:
        lines.append(f"- links: {len(routed.urls)}\n")
        for url in routed.urls:
            lines.append(f"  - {url}\n")

    if link_result is not None:
        success = sum(1 for item in link_result.items if item.status == "success")
        failed = len(link_result.items) - success
        saved_docs = sum(1 for item in link_result.items if item.body_file)
        lines.append(f"- link_success: {success}\n")
        lines.append(f"- link_failed: {failed}\n")
        lines.append(f"- link_doc_saved: {saved_docs}\n")
        for item in link_result.items:
            if item.body_file:
                lines.append(f"  - {item.url} -> {item.body_file}\n")

    if quote_result.near_dups:
        lines.append("- quote_near_dup_examples:\n")
        for item in quote_result.near_dups[:10]:
            lines.append(f"  - {item.text} -> {item.matched_file} ({item.ratio:.3f})\n")

    if errors:
        lines.append("- errors:\n")
        for item in errors:
            lines.append(f"  - {item}\n")

    lines.append("\n")

    original = import_record_path.read_text(encoding="utf-8") if import_record_path.exists() else ""
    import_record_path.write_text(original + "".join(lines), encoding="utf-8")


def process_message(
    *,
    text: str,
    quote_dir: Path,
    topic_pool_path: Path,
    import_record_path: Path,
    link_log_path: Path,
    apply_mode: bool,
    source_time: str,
    source_ref: str,
    near_dup_threshold: float = DEFAULT_NEAR_DUP_THRESHOLD,
) -> MessageProcessSummary:
    routed = route_message_text(text)
    errors: list[str] = []
    touched_files: set[str] = set()

    quote_result = QuoteIngestResult(
        added_count=0,
        near_dup_count=0,
        exact_dup_count=0,
        touched_files=[],
        topic_total=0,
        near_dups=[],
    )
    link_result: LinkToQuotesResult | None = None

    if routed.mode == "quote_mode" and routed.quote_text:
        quote_result = ingest_quote_text(
            quote_text=routed.quote_text,
            quote_dir=quote_dir,
            topic_pool_path=topic_pool_path,
            apply_mode=apply_mode,
            source_time=source_time,
            source_ref=source_ref,
            near_dup_threshold=near_dup_threshold,
        )
        touched_files.update(path.as_posix() for path in quote_result.touched_files)

    if routed.mode == "link_mode" and routed.urls:
        link_result = process_urls_to_quotes(
            urls=routed.urls,
            quote_dir=quote_dir,
            topic_pool_path=topic_pool_path,
            apply_mode=apply_mode,
            source_time=source_time,
            source_ref=source_ref,
            near_dup_threshold=near_dup_threshold,
            link_log_path=link_log_path,
        )
        touched_files.update(path.as_posix() for path in link_result.touched_files)
        if link_result.link_log_path:
            touched_files.add(link_result.link_log_path.as_posix())
        for row in link_result.items:
            if row.status != "success" and row.error:
                errors.append(f"{row.url}: {row.error}")

    _append_import_record(
        import_record_path=import_record_path,
        source_time=source_time,
        source_ref=source_ref,
        mode=routed.mode,
        routed=routed,
        quote_result=quote_result,
        link_result=link_result,
        errors=errors,
    )
    touched_files.add(import_record_path.as_posix())

    link_total = len(link_result.items) if link_result is not None else 0
    link_success = sum(1 for item in (link_result.items if link_result is not None else []) if item.status == "success")
    link_failed = link_total - link_success
    link_doc_saved_count = sum(1 for item in (link_result.items if link_result is not None else []) if item.body_file)

    return MessageProcessSummary(
        mode=routed.mode,
        quote_added_count=quote_result.added_count,
        quote_near_dup_count=quote_result.near_dup_count,
        quote_exact_dup_count=quote_result.exact_dup_count,
        link_total=link_total,
        link_success=link_success,
        link_failed=link_failed,
        link_doc_saved_count=link_doc_saved_count,
        touched_files=sorted(touched_files),
        errors=errors,
        import_record_path=import_record_path.as_posix(),
    )


def build_short_reply(summary: MessageProcessSummary) -> str:
    if summary.mode == "ignore":
        return "未识别可处理内容。"

    if summary.errors and summary.quote_added_count <= 0 and summary.link_success <= 0:
        return f"处理失败：{summary.errors[0]}"

    if summary.mode == "quote_mode":
        return (
            "金句已入库："
            f"新增{summary.quote_added_count}，"
            f"近似{summary.quote_near_dup_count}，"
            f"重复{summary.quote_exact_dup_count}"
        )

    if summary.link_total > 0:
        return (
            "链接已入库："
            f"成功{summary.link_success}/{summary.link_total}，"
            f"正文{summary.link_doc_saved_count}"
        )

    return "已处理。"
