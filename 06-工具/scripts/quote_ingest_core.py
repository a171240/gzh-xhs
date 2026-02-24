#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared quote-ingest core for flomo and Feishu pipelines.

This module centralizes quote normalization, dedupe, tagging, and topic-pool
construction so multiple ingestion entries keep one rule set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from build_quote_library import THEMES, _canonical, _classify_theme, _classify_usage, _split_candidates

USAGE_ALLOWED = {"开头钩子", "观点", "提问", "警示"}
TAG_ORDER = ("#选题", "#框架", "#标题")
DEFAULT_NEAR_DUP_THRESHOLD = 0.88

THEME_BY_FILE = {filename: theme for filename, theme in THEMES}
FILE_BY_THEME = {theme: filename for filename, theme in THEMES}

PLATFORMS_BY_THEME = {
    "搞钱与生意": ["公众号", "小红书"],
    "内容与增长": ["公众号", "小红书", "抖音"],
    "系统与执行": ["公众号", "视频号"],
    "人性与沟通": ["小红书", "公众号"],
    "婚恋与家庭（次级）": ["小红书", "视频号"],
    "自我与哲学（次级）": ["小红书", "公众号"],
}

TARGET_BY_THEME = {
    "搞钱与生意": "想做生意或打造个人IP的创作者/创业者",
    "内容与增长": "正在做内容增长、需要稳定选题的内容团队",
    "系统与执行": "希望提升执行力与长期产能的创作者与团队",
    "人性与沟通": "关注关系沟通、表达能力和情绪管理的人群",
    "婚恋与家庭（次级）": "关注亲密关系、家庭议题与代际沟通的人群",
    "自我与哲学（次级）": "关注自我成长、认知升级与人生选择的人群",
}

TOPIC_REGEX = re.compile(
    r"(为什么|如何|到底|是不是|本质|真相|不要|别|不是|而是|只有|才|越|最|必须|敢|害怕|焦虑|痛苦|认知|命运|值得)"
)
FRAMEWORK_REGEX = re.compile(
    r"(步骤|方法|流程|清单|公式|框架|原则|模型|体系|路径|节点|策略|SOP|第一步|^\d+[、.．)])"
)
TITLE_REGEX = re.compile(r"(不是|而是|越|最|一定|别|不要|只有|才|就|会|能|让|真相|本质|为什么)")
TAG_EXTRACT_RE = re.compile(r"#(?:选题|框架|标题)\b")
TRAILING_EMOTE_RE = re.compile(r"(?:\[[^\[\]\n]{1,8}\]\s*)+$")
TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class SourceTextItem:
    source_time: str
    text: str
    source_kind: str = "text"
    source_ref: str = ""


@dataclass(frozen=True)
class ExistingQuote:
    file_name: str
    theme: str
    usage: str
    text: str
    tags: tuple[str, ...]
    norm: str
    fuzzy: str


@dataclass(frozen=True)
class CandidateQuote:
    file_name: str
    theme: str
    usage: str
    text: str
    tags: tuple[str, ...]
    norm: str
    fuzzy: str
    source_time: str
    source_kind: str = "text"
    source_ref: str = ""


@dataclass(frozen=True)
class NearDupItem:
    source_time: str
    text: str
    matched_text: str
    matched_file: str
    ratio: float
    source_kind: str = "text"
    source_ref: str = ""


@dataclass(frozen=True)
class TopicEntry:
    text: str
    norm: str
    tags: tuple[str, ...]
    theme: str
    file_name: str
    source_time: str
    score: float


def resolve_path(repo_root: Path, maybe_relative: str) -> Path:
    path = Path(maybe_relative)
    return path if path.is_absolute() else (repo_root / path)


def normalize_fuzzy(text: str) -> str:
    out = re.sub(r"\s+", "", text)
    out = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", out)
    return out.lower().strip()


def sanitize_text(text: str) -> str:
    value = text.replace("\u200b", "").replace("\ufeff", "")
    value = re.sub(r"\s+", " ", value).strip()
    value = TRAILING_EMOTE_RE.sub("", value).strip()
    if len(value) >= 2 and value[0] in ('"', "“", "‘", "'") and value[-1] in ('"', "”", "’", "'"):
        value = value[1:-1].strip()
    return value


def is_hard_noise(text: str) -> bool:
    if not text:
        return True
    lower = text.lower().strip()

    if "http://" in lower or "https://" in lower or "://" in lower:
        return True
    if any(k in text for k in ("密保", "密码", "风控", "使用须知")):
        return True
    if "----" in text:
        return True
    if re.fullmatch(r"[0-9\s+\-*/%=().]+", text):
        return True
    if re.fullmatch(r"[A-Za-z0-9 _./:@-]{28,}", text):
        return True

    noise_tokens = (
        "apps.googleusercontent",
        "ebookid=",
        "trialreadingid=",
        "pub_key_id",
        "微信密钥",
        "google客户id",
        "客户id",
        "ace111",
        "ace222",
        "ace333",
        "ai评论",
        "提示词：",
        "提示词:",
    )
    if any(token in lower for token in noise_tokens):
        return True

    flomo_noise = (
        "何谓 flomo",
        "欢迎/简介",
        "欢迎/指南",
        "除了微信输入",
        "共建微信群",
        "下载手机/电脑客户端",
        "导入微信读书",
        "像发微博一样",
        "flomo 是新一代",
    )
    if any(k in text for k in flomo_noise):
        return True
    if "flomo" in lower and ("欢迎" in text or "输入" in text or "帮助" in text):
        return True

    ascii_letters_digits = sum(ch.isascii() and (ch.isalnum() or ch in "@._-=") for ch in text)
    if ascii_letters_digits >= 18 and ascii_letters_digits / max(len(text), 1) > 0.38:
        return True

    letters = sum(ch.isalpha() for ch in text)
    if letters >= 8 and text.upper() == text and re.search(r"[A-Z]", text):
        return True

    return False


def normalize_usage(text: str) -> str:
    usage = _classify_usage(text)
    if usage in USAGE_ALLOWED:
        return usage
    if usage in {"行动", "对比"}:
        return "观点"
    if text.endswith(("？", "?")):
        return "提问"
    if any(token in text for token in ("不要", "别", "警惕", "小心", "谨慎", "千万")):
        return "警示"
    if re.match(r"^(如果|当|很多人|你以为)", text):
        return "开头钩子"
    return "观点"


def suggest_tags(text: str) -> tuple[str, ...]:
    tags: list[str] = []
    no_space = re.sub(r"\s+", "", text)
    if TOPIC_REGEX.search(text):
        tags.append("#选题")
    if FRAMEWORK_REGEX.search(text):
        tags.append("#框架")
    if len(no_space) <= 28 and (TITLE_REGEX.search(text) or text.endswith(("？", "?"))):
        tags.append("#标题")

    unique: list[str] = []
    seen: set[str] = set()
    for tag in TAG_ORDER:
        if tag in tags and tag not in seen:
            seen.add(tag)
            unique.append(tag)
    return tuple(unique)


def topic_score(text: str, tags: tuple[str, ...]) -> float:
    score = 0.0
    compact = re.sub(r"\s+", "", text)

    if "#选题" in tags:
        score += 5.0
    if "#标题" in tags:
        score += 1.3
    if "#框架" in tags:
        score += 1.0

    for kw in ("不是", "而是", "却", "但", "反而", "越", "最", "别", "不要", "只有", "才", "为什么", "真相", "本质"):
        if kw in text:
            score += 0.45
    if text.endswith(("？", "?")):
        score += 0.9
    if 10 <= len(compact) <= 36:
        score += 1.0
    if "你" in text and any(k in text for k in ("不是", "别", "不要", "为什么")):
        score += 0.6

    return score


def parse_existing_quote_line(line: str) -> tuple[str, str, tuple[str, ...]] | None:
    match = re.match(r"^-\s*【(?P<usage>[^】]+)】(?P<body>.+)$", line.strip())
    if not match:
        return None

    usage = match.group("usage").strip()
    body = match.group("body").strip()
    tags_raw = tuple(TAG_EXTRACT_RE.findall(body))
    text = TAG_EXTRACT_RE.sub("", body).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None

    tags = tuple(tag for tag in TAG_ORDER if tag in tags_raw)
    return usage, text, tags


def load_existing_quotes(quote_dir: Path) -> list[ExistingQuote]:
    existing: list[ExistingQuote] = []
    for file_name, theme in THEMES:
        path = quote_dir / file_name
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            parsed = parse_existing_quote_line(raw_line)
            if not parsed:
                continue
            usage, text, tags = parsed
            norm = _canonical(text)
            fuzzy = normalize_fuzzy(text)
            existing.append(
                ExistingQuote(
                    file_name=file_name,
                    theme=theme,
                    usage=usage if usage in USAGE_ALLOWED else "观点",
                    text=text,
                    tags=tags,
                    norm=norm,
                    fuzzy=fuzzy,
                )
            )
    return existing


def extract_date(value: str) -> str:
    matched = TIME_RE.search(value)
    return matched.group(0) if matched else ""


def find_near_duplicate(
    fuzzy: str,
    known_items: list[ExistingQuote | CandidateQuote],
    *,
    near_dup_threshold: float,
) -> tuple[ExistingQuote | CandidateQuote, float] | None:
    best_item: ExistingQuote | CandidateQuote | None = None
    best_ratio = 0.0
    new_len = len(fuzzy)

    for item in known_items:
        if not item.fuzzy:
            continue
        old_len = len(item.fuzzy)
        if abs(new_len - old_len) > max(4, int(max(new_len, old_len) * 0.35)):
            continue
        ratio = SequenceMatcher(None, fuzzy, item.fuzzy).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_item = item

    if best_item is None:
        return None
    if best_ratio >= near_dup_threshold:
        return best_item, best_ratio
    return None


def build_candidates(
    source_items: Iterable[SourceTextItem],
    existing: list[ExistingQuote],
    *,
    near_dup_threshold: float = DEFAULT_NEAR_DUP_THRESHOLD,
    split_input: bool = True,
) -> tuple[list[CandidateQuote], list[NearDupItem], int]:
    existing_norm = {item.norm for item in existing}
    known_for_near: list[ExistingQuote | CandidateQuote] = list(existing)
    added: list[CandidateQuote] = []
    near_dups: list[NearDupItem] = []
    exact_dup_count = 0
    seen_in_batch: set[str] = set()

    for source in source_items:
        chunks = _split_candidates(source.text) if split_input else [source.text]
        for chunk in chunks:
            clean = sanitize_text(chunk)
            if is_hard_noise(clean):
                continue
            if len(clean) < 6 or len(clean) > 180:
                continue
            norm = _canonical(clean)
            if not norm:
                continue
            if norm in seen_in_batch:
                continue
            seen_in_batch.add(norm)

            if norm in existing_norm:
                exact_dup_count += 1
                continue

            fuzzy = normalize_fuzzy(clean)
            if len(fuzzy) < 6:
                continue

            near = find_near_duplicate(
                fuzzy=fuzzy,
                known_items=known_for_near,
                near_dup_threshold=near_dup_threshold,
            )
            if near is not None:
                matched, ratio = near
                near_dups.append(
                    NearDupItem(
                        source_time=source.source_time,
                        text=clean,
                        matched_text=matched.text,
                        matched_file=matched.file_name,
                        ratio=ratio,
                        source_kind=source.source_kind,
                        source_ref=source.source_ref,
                    )
                )
                continue

            theme = _classify_theme(clean)
            file_name = FILE_BY_THEME.get(theme, "03-系统与执行.md")
            usage = normalize_usage(clean)
            tags = suggest_tags(clean)

            candidate = CandidateQuote(
                file_name=file_name,
                theme=THEME_BY_FILE.get(file_name, theme),
                usage=usage,
                text=clean,
                tags=tags,
                norm=norm,
                fuzzy=fuzzy,
                source_time=source.source_time,
                source_kind=source.source_kind,
                source_ref=source.source_ref,
            )
            added.append(candidate)
            known_for_near.append(candidate)
            existing_norm.add(norm)

    return added, near_dups, exact_dup_count


def append_quotes(quote_dir: Path, new_quotes: list[CandidateQuote]) -> list[Path]:
    by_file: dict[str, list[CandidateQuote]] = {}
    for quote in new_quotes:
        by_file.setdefault(quote.file_name, []).append(quote)

    touched: list[Path] = []
    for file_name, _theme in THEMES:
        entries = by_file.get(file_name, [])
        if not entries:
            continue

        path = quote_dir / file_name
        if not path.exists():
            header = (
                f"# {THEME_BY_FILE[file_name]}（金句库）\n\n"
                "> 每条标注【用途】；建议“挑少用准”，不要堆砌。\n\n"
                "## 金句\n\n"
            )
            path.write_text(header, encoding="utf-8")

        original = path.read_text(encoding="utf-8")
        tail = "" if original.endswith("\n") else "\n"
        lines = []
        for item in entries:
            suffix = f" {' '.join(item.tags)}" if item.tags else ""
            lines.append(f"- 【{item.usage}】{item.text}{suffix}\n")
        path.write_text(original + tail + "".join(lines), encoding="utf-8")
        touched.append(path)

    return touched


def unique_topic_entries(quotes: list[ExistingQuote | CandidateQuote], new_time_map: dict[str, str]) -> list[TopicEntry]:
    out: list[TopicEntry] = []
    seen: set[str] = set()

    for item in quotes:
        tags = tuple(tag for tag in item.tags if tag in TAG_ORDER)
        if "#选题" not in tags:
            continue
        if item.norm in seen:
            continue
        seen.add(item.norm)
        source_time = new_time_map.get(item.norm, "历史存量")
        score = topic_score(item.text, tags)
        out.append(
            TopicEntry(
                text=item.text,
                norm=item.norm,
                tags=tags,
                theme=item.theme,
                file_name=item.file_name,
                source_time=source_time,
                score=score,
            )
        )

    out.sort(key=lambda item: (-item.score, item.theme, item.text))
    return out


def select_top_topics(entries: list[TopicEntry], topn: int) -> list[TopicEntry]:
    if topn <= 0 or not entries:
        return []

    buckets: dict[str, list[TopicEntry]] = {}
    for item in entries:
        buckets.setdefault(item.theme, []).append(item)

    for theme in buckets:
        buckets[theme].sort(key=lambda item: (-item.score, item.text))

    theme_order = sorted(
        buckets.keys(),
        key=lambda theme: buckets[theme][0].score if buckets[theme] else 0,
        reverse=True,
    )

    selected: list[TopicEntry] = []
    picked_norms: set[str] = set()
    while len(selected) < topn:
        any_take = False
        for theme in theme_order:
            while buckets[theme] and buckets[theme][0].norm in picked_norms:
                buckets[theme].pop(0)
            if not buckets[theme]:
                continue
            item = buckets[theme].pop(0)
            picked_norms.add(item.norm)
            selected.append(item)
            any_take = True
            if len(selected) >= topn:
                break
        if not any_take:
            break

    if len(selected) < topn:
        for item in entries:
            if item.norm in picked_norms:
                continue
            picked_norms.add(item.norm)
            selected.append(item)
            if len(selected) >= topn:
                break

    return selected


def render_topic_pool(entries: list[TopicEntry], pushed_norms: set[str] | None = None) -> str:
    pushed_norms = pushed_norms or set()

    lines: list[str] = []
    lines.append("# 金句选题池\n\n")
    lines.append("> 来源：`03-素材库/金句库/` 中带 `#选题` 标签的条目（历史存量 + 本次新增）。\n")
    lines.append("> 用途：给“深化选题”快速命中高潜种子；`待深化（已推送）` 表示已写入待深化目录。\n\n")
    lines.append("| ID | 选题种子 | 来源金句文件 | 来源时间 | 标签 | 主题 | 推荐平台 | 状态 |\n")
    lines.append("|---|---|---|---|---|---|---|---|\n")

    for idx, item in enumerate(entries, start=1):
        row_id = f"T{idx:03d}"
        tags = " ".join(item.tags)
        source_file = f"`03-素材库/金句库/{item.file_name}`"
        platforms = "/".join(PLATFORMS_BY_THEME.get(item.theme, ["公众号"]))
        status = "待深化（已推送）" if item.norm in pushed_norms else "待筛选"
        text = item.text.replace("|", "\\|")
        lines.append(
            f"| {row_id} | {text} | {source_file} | {item.source_time} | {tags} | {item.theme} | {platforms} | {status} |\n"
        )

    return "".join(lines)


def load_pushed_norms(topic_pool_path: Path) -> set[str]:
    if not topic_pool_path.exists():
        return set()

    pushed: set[str] = set()
    for raw in topic_pool_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line.startswith("| T"):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 9:
            continue
        text = cells[2]
        status = cells[8]
        if "已推送" not in status:
            continue
        norm = _canonical(text.replace("\\|", "|"))
        if norm:
            pushed.add(norm)
    return pushed


def write_topic_pool(topic_pool_path: Path, entries: list[TopicEntry], pushed_norms: set[str] | None = None) -> None:
    if pushed_norms is None:
        pushed_norms = load_pushed_norms(topic_pool_path)
    topic_pool_path.parent.mkdir(parents=True, exist_ok=True)
    topic_pool_path.write_text(render_topic_pool(entries, pushed_norms=pushed_norms), encoding="utf-8")


__all__ = [
    "CandidateQuote",
    "DEFAULT_NEAR_DUP_THRESHOLD",
    "ExistingQuote",
    "FILE_BY_THEME",
    "NearDupItem",
    "PLATFORMS_BY_THEME",
    "SourceTextItem",
    "TAG_ORDER",
    "TARGET_BY_THEME",
    "THEME_BY_FILE",
    "THEMES",
    "TopicEntry",
    "USAGE_ALLOWED",
    "append_quotes",
    "build_candidates",
    "extract_date",
    "load_existing_quotes",
    "load_pushed_norms",
    "render_topic_pool",
    "resolve_path",
    "sanitize_text",
    "select_top_topics",
    "unique_topic_entries",
    "write_topic_pool",
]
