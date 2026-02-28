#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Incremental flomo -> 金句库 sync, with 选题池融合."""

from __future__ import annotations

import argparse
import datetime as dt
import html as html_lib
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from build_quote_library import _decode_bytes, _strip_tags
from quote_ingest_core import (
    DEFAULT_NEAR_DUP_THRESHOLD,
    PLATFORMS_BY_THEME,
    TARGET_BY_THEME,
    CandidateQuote,
    NearDupItem,
    SourceTextItem,
    append_quotes,
    build_candidates,
    extract_date,
    load_existing_quotes,
    resolve_path,
    select_top_topics,
    unique_topic_entries,
    write_topic_pool,
)

TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class MemoItem:
    time: str
    text: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_flomo_html(flomo_zip: Path) -> tuple[str, str]:
    if not flomo_zip.exists():
        raise FileNotFoundError(f"flomo zip not found: {flomo_zip}")

    with zipfile.ZipFile(flomo_zip, "r") as zf:
        html_names = [name for name in zf.namelist() if name.lower().endswith(".html")]
        if not html_names:
            raise RuntimeError("No .html found in flomo zip")
        html_names.sort(key=lambda name: (name.count("/"), len(name)))
        chosen = html_names[0]
        raw = zf.read(chosen)
    return _decode_bytes(raw), chosen


def _iter_memo_items(html: str) -> Iterable[MemoItem]:
    pattern = re.compile(
        r'<div class="memo">\s*<div class="time">(?P<time>.*?)</div>\s*<div class="content">(?P<content>.*?)</div>',
        re.S,
    )
    for match in pattern.finditer(html):
        source_time = html_lib.unescape(match.group("time")).strip()
        content_html = match.group("content")
        text = _strip_tags(content_html)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text).strip()
        if text:
            yield MemoItem(time=source_time, text=text)


def _short_title(text: str, max_len: int = 18) -> str:
    cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", text).strip()
    if not cleaned:
        cleaned = "选题"
    return cleaned[:max_len]


def _build_pending_content(topic_text: str, theme: str, tags: tuple[str, ...], source_file: str, date_str: str) -> str:
    platforms = PLATFORMS_BY_THEME.get(theme, ["公众号"])
    target = TARGET_BY_THEME.get(theme, "对该话题有共鸣、愿意行动的内容消费者")
    related = f"03-素材库/金句库/{source_file}"
    tags_text = " ".join(tags) if tags else "无"
    platforms_text = ", ".join(platforms)

    lines: list[str] = []
    lines.append("---\n")
    lines.append(f"date: {date_str}\n")
    lines.append(f"topic: {topic_text}\n")
    lines.append(f"target: {target}\n")
    lines.append(f"platforms: [{platforms_text}]\n")
    lines.append(f"related: [{related}]\n")
    lines.append("status: 待深化\n")
    lines.append("---\n\n")
    lines.append("## 选题分析\n")
    lines.append(f"- 核心触发句：{topic_text}\n")
    lines.append("- 核心矛盾/痛点：从“现状不舒服”与“理想状态”之间的落差切入。\n")
    lines.append("- 内容价值：提供可理解、可执行、可复用的认知与动作路径。\n\n")
    lines.append("## 内容大纲\n")
    lines.append("1. 开头：用真实场景和冲突句把读者带入问题。\n")
    lines.append("2. 拆解：解释为什么多数人会卡在这个问题上（认知/行为/环境）。\n")
    lines.append("3. 方法：给出 3-5 个可执行动作或判断标准。\n")
    lines.append("4. 案例：补一个小案例或反例，增强可信度。\n")
    lines.append("5. 结尾 CTA：引导读者做一个最小行动并留下反馈。\n\n")
    lines.append("## 可复用素材\n")
    lines.append(f"- 金句来源：`{related}`\n")
    lines.append(f"- 标签：{tags_text}\n")
    lines.append(f"- 建议平台：{platforms_text}\n")
    return "".join(lines)


def _write_pending_topics(pending_dir: Path, selected, date_str: str) -> list[Path]:
    pending_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for item in selected:
        base_name = _short_title(item.text)
        file_path = pending_dir / f"{date_str}-{base_name}.md"
        suffix = 2
        while file_path.exists():
            existing = file_path.read_text(encoding="utf-8")
            if f"topic: {item.text}" in existing:
                break
            file_path = pending_dir / f"{date_str}-{base_name}-{suffix:02d}.md"
            suffix += 1

        if file_path.exists() and f"topic: {item.text}" in file_path.read_text(encoding="utf-8"):
            continue

        content = _build_pending_content(
            topic_text=item.text,
            theme=item.theme,
            tags=item.tags,
            source_file=item.file_name,
            date_str=date_str,
        )
        file_path.write_text(content, encoding="utf-8")
        written.append(file_path)

    return written


def _render_report(
    *,
    mode: str,
    date_str: str,
    flomo_zip: Path,
    html_entry: str,
    memos_total: int,
    existing_total: int,
    exact_new_candidates: int,
    near_dup_count: int,
    exact_dup_count: int,
    added: list[CandidateQuote],
    near_dups: list[NearDupItem],
    topic_total: int,
    top_selected,
    written_pending: list[Path],
) -> str:
    by_theme: dict[str, int] = {}
    for item in added:
        by_theme[item.theme] = by_theme.get(item.theme, 0) + 1

    lines: list[str] = []
    lines.append(f"# flomo 金句增量导入报告（{date_str}）\n\n")
    lines.append(f"- 模式：`{mode}`\n")
    lines.append(f"- 数据源：`{flomo_zip}`\n")
    lines.append(f"- HTML 条目：`{html_entry}`\n\n")

    lines.append("## 预检统计\n")
    lines.append(f"- `memos_total`: {memos_total}\n")
    lines.append(f"- `existing_quotes_total`: {existing_total}\n")
    lines.append(f"- `exact_new_candidates`: {exact_new_candidates}\n")
    lines.append(f"- `near_dup_candidates`: {near_dup_count}\n")
    lines.append(f"- `exact_dup_candidates`: {exact_dup_count}\n\n")

    lines.append("## 新增写入统计\n")
    lines.append(f"- 新增写入总数：{len(added)}\n")
    if by_theme:
        for theme, count in sorted(by_theme.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- {theme}: {count}\n")
    else:
        lines.append("- 无新增写入。\n")
    lines.append("\n")

    if added:
        lines.append("## 新增条目明细\n")
        for item in added:
            tags = " ".join(item.tags)
            suffix = f" {tags}" if tags else ""
            lines.append(f"- [{item.source_time}] `{item.file_name}` 【{item.usage}】{item.text}{suffix}\n")
        lines.append("\n")

    if near_dups:
        lines.append("## 近似重复复核（未自动写入）\n")
        lines.append("| 来源时间 | 候选文本 | 匹配文本 | 匹配文件 | 相似度 |\n")
        lines.append("|---|---|---|---|---|\n")
        for item in near_dups:
            lines.append(
                f"| {item.source_time} | {item.text.replace('|', '\\|')} | "
                f"{item.matched_text.replace('|', '\\|')} | {item.matched_file} | {item.ratio:.3f} |\n"
            )
        lines.append("\n")

    lines.append("## 选题融合统计\n")
    lines.append(f"- 选题池总条目（`#选题`）：{topic_total}\n")
    lines.append(f"- 本次推送到待深化（TopN）：{len(top_selected)}\n")
    lines.append(f"- 实际新建待深化文件：{len(written_pending)}\n\n")

    if top_selected:
        lines.append("## TopN 推送列表\n")
        for item in top_selected:
            lines.append(f"- [{item.theme}] {item.text} ({' '.join(item.tags)})\n")
        lines.append("\n")

    if written_pending:
        lines.append("## 新建待深化文件\n")
        for file_path in written_pending:
            lines.append(f"- `{file_path.as_posix()}`\n")
        lines.append("\n")

    return "".join(lines)


def _print_summary(*, memos_total: int, existing_total: int, exact_new_candidates: int, near_dup_count: int, apply_mode: bool) -> None:
    mode = "apply" if apply_mode else "dry-run"
    sys.stdout.write(f"[{mode}] memos_total={memos_total}\n")
    sys.stdout.write(f"[{mode}] existing_quotes_total={existing_total}\n")
    sys.stdout.write(f"[{mode}] exact_new_candidates={exact_new_candidates}\n")
    sys.stdout.write(f"[{mode}] near_dup_candidates={near_dup_count}\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Incremental flomo -> 金句库 sync with 选题池融合")
    parser.add_argument("--flomo-zip", default=r"e:\下载\flomo@李可-20260220.zip", help="Path to flomo export zip")
    parser.add_argument("--quote-dir", default=r"03-素材库/金句库", help="Quote library directory")
    parser.add_argument("--topic-pool", default=r"01-选题管理/选题规划/金句选题池.md", help="Topic pool markdown file path")
    parser.add_argument("--topn", type=int, default=20, help="Top N topic seeds to push into 01-待深化")
    parser.add_argument("--dry-run", action="store_true", help="Preview only. No repo writes.")
    parser.add_argument("--apply", action="store_true", help="Apply writes to quote files/topic pool/pending files.")
    parser.add_argument("--report-file", default="", help="Optional report output path. Relative paths resolve to repo root.")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="Date for report and pending files (YYYY-MM-DD).")
    parser.add_argument(
        "--near-dup-threshold",
        type=float,
        default=DEFAULT_NEAR_DUP_THRESHOLD,
        help="Near-duplicate threshold for SequenceMatcher ratio (default 0.88).",
    )
    args = parser.parse_args(argv)

    apply_mode = bool(args.apply)
    dry_run_mode = bool(args.dry_run or not args.apply)

    repo_root = _repo_root()
    flomo_zip = Path(args.flomo_zip)
    quote_dir = resolve_path(repo_root, args.quote_dir)
    topic_pool_path = resolve_path(repo_root, args.topic_pool)
    pending_dir = repo_root / "01-选题管理" / "01-待深化"

    html, html_entry = _read_flomo_html(flomo_zip)
    memo_items = list(_iter_memo_items(html))
    existing = load_existing_quotes(quote_dir)
    source_items = [
        SourceTextItem(
            source_time=item.time,
            text=item.text,
            source_kind="flomo",
            source_ref=flomo_zip.as_posix(),
        )
        for item in memo_items
    ]
    added, near_dups, exact_dup_count = build_candidates(
        source_items,
        existing,
        near_dup_threshold=max(0.1, min(0.99, args.near_dup_threshold)),
        split_input=True,
    )

    memos_total = len(memo_items)
    existing_total = len(existing)
    exact_new_candidates = len(added) + len(near_dups)
    near_dup_count = len(near_dups)

    _print_summary(
        memos_total=memos_total,
        existing_total=existing_total,
        exact_new_candidates=exact_new_candidates,
        near_dup_count=near_dup_count,
        apply_mode=apply_mode,
    )

    if dry_run_mode and not apply_mode:
        report_text = _render_report(
            mode="dry-run",
            date_str=args.date,
            flomo_zip=flomo_zip,
            html_entry=html_entry,
            memos_total=memos_total,
            existing_total=existing_total,
            exact_new_candidates=exact_new_candidates,
            near_dup_count=near_dup_count,
            exact_dup_count=exact_dup_count,
            added=added,
            near_dups=near_dups,
            topic_total=0,
            top_selected=[],
            written_pending=[],
        )
        if args.report_file:
            report_path = resolve_path(repo_root, args.report_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report_text, encoding="utf-8")
            sys.stdout.write(f"[dry-run] report written: {report_path}\n")
        else:
            sys.stdout.write(report_text)
        return 0

    if not apply_mode:
        return 0

    append_quotes(quote_dir, added)

    updated_existing = load_existing_quotes(quote_dir)
    new_time_map = {item.norm: extract_date(item.source_time) or args.date for item in added}
    topic_entries = unique_topic_entries(updated_existing, new_time_map)
    selected = select_top_topics(topic_entries, args.topn)
    pushed_norms = {item.norm for item in selected}
    write_topic_pool(topic_pool_path, topic_entries, pushed_norms)

    written_pending = _write_pending_topics(pending_dir, selected, args.date)

    report_text = _render_report(
        mode="apply",
        date_str=args.date,
        flomo_zip=flomo_zip,
        html_entry=html_entry,
        memos_total=memos_total,
        existing_total=existing_total,
        exact_new_candidates=exact_new_candidates,
        near_dup_count=near_dup_count,
        exact_dup_count=exact_dup_count,
        added=added,
        near_dups=near_dups,
        topic_total=len(topic_entries),
        top_selected=selected,
        written_pending=written_pending,
    )

    if args.report_file:
        report_path = resolve_path(repo_root, args.report_file)
    else:
        report_path = quote_dir / "导入记录" / f"{args.date}-flomo-import.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")

    sys.stdout.write(f"[apply] topic pool updated: {topic_pool_path}\n")
    sys.stdout.write(f"[apply] pending topics written: {len(written_pending)}\n")
    sys.stdout.write(f"[apply] report written: {report_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
