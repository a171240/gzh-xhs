#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build a themed quote library (金句库) from a flomo export zip.

Design goals:
- Deterministic, no network.
- Output markdown files split by themes.
- Keep quotes short, directly usable in writing.
"""

from __future__ import annotations

import argparse
import html as html_lib
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


THEMES = [
    ("01-搞钱与生意.md", "搞钱与生意"),
    ("02-内容与增长.md", "内容与增长"),
    ("03-系统与执行.md", "系统与执行"),
    ("04-人性与沟通.md", "人性与沟通"),
    ("90-婚恋与家庭（次级）.md", "婚恋与家庭（次级）"),
    ("91-自我与哲学（次级）.md", "自我与哲学（次级）"),
]


@dataclass(frozen=True)
class Quote:
    theme: str
    usage: str
    text: str


def _decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _strip_tags(html: str) -> str:
    # Normalize common breaks first.
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    # Drop images.
    html = re.sub(r"(?is)<img\b[^>]*>", "", html)
    # Drop all remaining tags.
    html = re.sub(r"(?is)<[^>]+>", "", html)
    # Unescape entities.
    html = html_lib.unescape(html)
    return html


def _iter_flomo_memo_texts(html: str) -> Iterable[str]:
    # flomo export: <div class="content"> ... </div>
    for block in re.findall(r'(?is)<div\s+class="content"[^>]*>(.*?)</div>', html):
        text = _strip_tags(block)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \\t]+", " ", text)
        yield text.strip()


def _split_candidates(text: str) -> list[str]:
    # Split by newlines first.
    parts: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Remove leading bullets/numbering.
        line = re.sub(r"^[-*•]\s+", "", line)
        line = re.sub(r"^\d+\s*[、.．)]\s*", "", line)
        line = line.strip()
        if not line:
            continue
        parts.append(line)

    out: list[str] = []
    for part in parts:
        # If too long, try sentence split.
        if len(part) >= 70 and re.search(r"[。！？!?]", part):
            # Split on sentence-ending punctuation.
            sentences = re.split(r"(?<=[。！？!?])\s*", part)
            for s in sentences:
                s = s.strip()
                if s:
                    out.append(s)
        elif len(part) >= 90 and re.search(r"[；;]", part):
            # Fallback split for long lines without sentence-ending punctuation.
            chunks = re.split(r"[；;]\s*", part)
            for c in chunks:
                c = c.strip()
                if c:
                    out.append(c)
        else:
            out.append(part)
    return out


def _looks_like_noise(line: str) -> bool:
    if not line:
        return True
    lower = line.lower()
    stripped = line.strip()
    if re.search(r"-{3,}", line):
        return True
    if stripped.lower().startswith(("prompt:", "提示词", "ai评论")):
        return True
    if any(k in line for k in ("使用须知", "风控", "密码", "密保")):
        return True
    # Pure math / counters
    if re.match(r"^[0-9\s()+\-*/%.,=]+$", stripped):
        return True
    # Shouty English fragments
    if re.match(r"^[A-Z][A-Z\s]{5,}$", stripped):
        return True
    # Links or link-ish patterns.
    if "http://" in line or "https://" in line or "://" in line:
        return True
    if "apps.googleusercontent" in lower:
        return True
    if "flomoapp" in lower or "flomo" in lower:
        return True
    # Pure short headings like "卖点体系构建"
    if len(line) <= 10 and not re.search(r"[，。！？!?：:]", line):
        return True
    # Very short numbered headings.
    if re.match(r"^\d+[、.．)]\s*\S{1,12}$", line):
        return True
    # Too much ASCII garbage (client IDs, exports, etc.).
    ascii_letters_digits = sum(ch.isascii() and (ch.isalnum() or ch in "@._-") for ch in line)
    if ascii_letters_digits >= 18 and ascii_letters_digits / max(len(line), 1) > 0.45:
        return True
    return False


def _canonical(line: str) -> str:
    s = re.sub(r"\s+", "", line)
    s = s.replace("：", ":").replace("，", ",").replace("。", ".")
    s = s.lower()
    return s


def _classify_theme(text: str) -> str:
    scores = {
        "搞钱与生意": 0,
        "内容与增长": 0,
        "系统与执行": 0,
        "人性与沟通": 0,
        "婚恋与家庭（次级）": 0,
        "自我与哲学（次级）": 0,
    }

    def add(theme: str, n: int = 1) -> None:
        scores[theme] = scores.get(theme, 0) + n

    t = text

    for kw in ("赚钱", "搞钱", "变现", "生意", "客户", "销售", "成交", "转化", "复购", "定价", "利润", "现金流", "产品", "商业"):
        if kw in t:
            add("搞钱与生意", 2)

    for kw in ("内容", "选题", "标题", "爆文", "公众号", "小红书", "涨粉", "流量", "完读", "运营", "增长", "私域", "素材"):
        if kw in t:
            add("内容与增长", 2)

    for kw in ("系统", "流程", "SOP", "复盘", "迭代", "执行", "习惯", "目标", "计划", "标准", "交付", "模板", "结构"):
        if kw.lower() in t.lower():
            add("系统与执行", 2)

    for kw in ("人性", "情绪", "沟通", "关系", "信任", "说服", "合作", "管理", "社交", "认同"):
        if kw in t:
            add("人性与沟通", 2)

    for kw in ("婚姻", "恋爱", "夫妻", "家庭", "分手", "相亲", "男", "女"):
        if kw in t:
            add("婚恋与家庭（次级）", 2)

    for kw in ("自我", "成长", "价值观", "意义", "选择", "命运", "哲学", "自由"):
        if kw in t:
            add("自我与哲学（次级）", 2)

    # Fallback preference: business/content/system/human, then secondary.
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if ranked and ranked[0][1] > 0:
        return ranked[0][0]
    return "系统与执行"


def _classify_usage(text: str) -> str:
    t = text
    if re.match(r"^(如果|当|一旦|你以为|很多人)", t):
        return "开头钩子"
    if "不要" in t or "别" in t or "千万" in t:
        return "警示"
    if "本质" in t or "其实" in t or "说白了" in t:
        return "观点"
    if "对比" in t or "vs" in t.lower() or "分水岭" in t:
        return "对比"
    if "第一步" in t or "清单" in t or "步骤" in t or "照做" in t:
        return "行动"
    if t.endswith("？") or t.endswith("?"):
        return "提问"
    return "观点"


def build_quotes(flomo_zip: Path) -> list[Quote]:
    if not flomo_zip.exists():
        raise FileNotFoundError(flomo_zip)

    with zipfile.ZipFile(flomo_zip, "r") as zf:
        html_names = [n for n in zf.namelist() if n.lower().endswith(".html")]
        if not html_names:
            raise RuntimeError("No .html found in zip")
        # Prefer root-level html when possible.
        html_names.sort(key=lambda n: (n.count("/"), len(n)))
        html_name = html_names[0]
        raw = zf.read(html_name)

    html = _decode_bytes(raw)

    seen: set[str] = set()
    quotes: list[Quote] = []

    for memo in _iter_flomo_memo_texts(html):
        for line in _split_candidates(memo):
            line = line.strip()
            if _looks_like_noise(line):
                continue
            if len(line) < 8:
                continue
            if len(line) > 140:
                continue
            canon = _canonical(line)
            if canon in seen:
                continue
            seen.add(canon)

            theme = _classify_theme(line)
            usage = _classify_usage(line)
            quotes.append(Quote(theme=theme, usage=usage, text=line))

    return quotes


def write_library(out_dir: Path, quotes: list[Quote]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # Index
    index_lines: list[str] = []
    index_lines.append("# 金句库索引\n")
    index_lines.append("用法建议：\n")
    index_lines.append("- 写公众号：每篇挑 2-4 句做“开头钩子/中段转折/收口/行动建议”，其余用自己的话展开。\n")
    index_lines.append("- 写小红书：每条挑 1-2 句做标题/封面/首段；不要堆砌。\n")
    index_lines.append("- 仅当你的 Brief 明确“是否调用金句库：是”才注入；否则不强制。\n")
    index_lines.append("\n主题文件：\n")
    for filename, theme in THEMES:
        index_lines.append(f"- `{filename}`：{theme}\n")
    index_lines.append("\n标签说明：每条前缀为【用途】。\n")

    (out_dir / "00-索引.md").write_text("".join(index_lines), encoding="utf-8")

    # Theme files
    by_theme: dict[str, list[Quote]] = {}
    for q in quotes:
        by_theme.setdefault(q.theme, []).append(q)

    # Stable ordering: usage then text
    for filename, theme in THEMES:
        items = by_theme.get(theme, [])
        items.sort(key=lambda q: (q.usage, q.text))

        lines: list[str] = []
        lines.append(f"# {theme}（金句库）\n\n")
        lines.append("> 每条标注【用途】；建议“挑少用准”，不要堆砌。\n\n")
        lines.append("## 金句\n\n")
        for q in items:
            lines.append(f"- 【{q.usage}】{q.text}\n")
        if not items:
            lines.append("- （待补充）\n")

        (out_dir / filename).write_text("".join(lines), encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build 金句库 from a flomo export zip")
    parser.add_argument(
        "--flomo-zip",
        default=r"e:\\下载\\flomo@李可-20260208.zip",
        help="Path to flomo export zip",
    )
    parser.add_argument(
        "--out-dir",
        default="金句库",
        help="Output directory (relative to repo root by default)",
    )

    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    flomo_zip = Path(args.flomo_zip)
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = repo_root / out_dir

    quotes = build_quotes(flomo_zip)
    write_library(out_dir, quotes)

    # Console summary
    counts: dict[str, int] = {}
    for q in quotes:
        counts[q.theme] = counts.get(q.theme, 0) + 1
    total = len(quotes)
    sys.stdout.write(f"Total quotes: {total}\\n")
    for theme, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        sys.stdout.write(f"- {theme}: {n}\\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
