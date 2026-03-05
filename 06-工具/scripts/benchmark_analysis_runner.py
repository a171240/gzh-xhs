#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily benchmark analysis runner.

Scans extracted benchmark documents under:
  03-素材库/对标链接库/提取正文/YYYY-MM-DD/*.md

Then generates structured analysis reports under:
  03-素材库/对标链接库/分析报告/YYYY-MM-DD/*-分析.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from automation_state import (
    REPO_ROOT,
    add_task_log,
    append_dead_letter,
    append_run_log,
    create_task,
    make_task_id,
    update_task,
)


EXTRACT_ROOT = REPO_ROOT / "03-素材库" / "对标链接库" / "提取正文"
REPORT_ROOT = REPO_ROOT / "03-素材库" / "对标链接库" / "分析报告"

TYPE_ORDER = ["系统方法论型", "心理学洞察型", "反常识观点型", "行动清单型"]
TOPIC_BY_TYPE = {
    "系统方法论型": "系统与执行",
    "心理学洞察型": "人性与沟通",
    "反常识观点型": "内容与增长",
    "行动清单型": "搞钱与生意",
}


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _now_shanghai() -> dt.datetime:
    try:
        return dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def _today_shanghai() -> str:
    return _now_shanghai().date().isoformat()


def _normalize_date(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if not text or text == "today":
        return _today_shanghai()
    if text == "yesterday":
        return (_now_shanghai().date() - dt.timedelta(days=1)).isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        raise ValueError("date must be YYYY-MM-DD / today / yesterday")
    return text


def _resolve_scan_date(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("scan_date") or payload.get("日期") or "").strip()
    if explicit:
        return _normalize_date(explicit)

    base = _normalize_date(str(payload.get("date") or "today"))
    mode = str(payload.get("scan_mode") or "yesterday").strip().lower()
    if mode == "same_day":
        return base
    base_date = dt.date.fromisoformat(base)
    return (base_date - dt.timedelta(days=1)).isoformat()


def _section(text: str, heading: str) -> str:
    match = re.search(rf"##\s*{re.escape(heading)}\s*\n+(.+?)(?=\n##\s+|\Z)", text, re.S)
    return match.group(1).strip() if match else ""


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _clean_sentence(text: str) -> str:
    s = str(text or "").replace("\u3000", " ").replace("\t", " ")
    s = re.sub(r"\s+", "", s)
    return s.strip("\"“”‘’[]()（）-")


def _split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for chunk in re.split(r"[。！？!?\n]+", str(text or "")):
        line = _clean_sentence(chunk)
        if len(line) >= 10:
            out.append(line)
    return out


def _uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _parse_title(raw: str, fallback: str) -> str:
    match = re.search(r"-\s*标题：(.+)", raw)
    if not match:
        return fallback
    return _clean_text(match.group(1))


def _parse_link(raw: str) -> str:
    match = re.search(r"-\s*原文链接：(.+)", raw)
    if not match:
        return "（待补充）"
    link = _clean_text(match.group(1))
    return link or "（待补充）"


def _parse_keypoints(raw: str) -> list[str]:
    out: list[str] = []
    for block in (_section(raw, "关键点"), _section(raw, "要点")):
        if not block:
            continue
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("-"):
                out.append(_clean_sentence(line[1:]))
    return _uniq([x for x in out if x])


def _parse_summary(raw: str) -> str:
    summary = _section(raw, "摘要")
    return _clean_text(summary)


def _parse_body(raw: str) -> str:
    if "## 正文" in raw:
        body = raw.split("## 正文", 1)[1].strip()
    else:
        body = raw

    if "完整文案：" in body:
        body = body.split("完整文案：", 1)[1].strip()

    # Fallback for odd docs where "## 摘要/## 关键点" are nested after 正文.
    if len(_clean_text(body)) < 50:
        marker = re.search(r"完整文案[:：]\s*(.+)", raw, re.S)
        if marker:
            body = marker.group(1).strip()

    return _clean_text(body)


def _strip_title_for_display(title: str) -> str:
    t = re.sub(r"-20\d{2}-\d{2}-\d{2}$", "", title).strip()
    t = re.sub(r"#[^\s#]+", "", t).strip()
    return _clean_text(t)


def _report_name_from_title(title: str) -> str:
    t = _strip_title_for_display(title)
    t = t.replace("：", "-").replace(":", "-")
    t = re.sub(r"[\\/:*?\"<>|]", "-", t)
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"-+", "-", t).strip("-")
    return f"{t or '未命名文案'}-分析.md"


def _classify_type(title: str, body: str, keypoints: list[str]) -> tuple[str, list[str]]:
    text = f"{title}\n{body}\n" + "\n".join(keypoints)

    action_keys = ["三个", "三步", "两步", "清单", "每天", "反思", "复盘", "行动", "方法"]
    psych_keys = ["拖延", "逃避", "焦虑", "心理", "自卑", "害怕", "人设"]
    anti_keys = ["反常识", "作弊", "你以为", "其实", "真相", "正大光明"]

    action_hits = sum(1 for k in action_keys if k in text)
    psych_hits = sum(1 for k in psych_keys if k in text)
    anti_hits = sum(1 for k in anti_keys if k in text)

    if (action_hits >= 2 and ("三个" in title or "三步" in title or len(keypoints) >= 2)) or (
        action_hits >= max(psych_hits, anti_hits) + 1
    ):
        return "行动清单型", ["文案给出连续动作步骤，具备强执行指令", "核心信息可直接改写为清单式内容"]

    if psych_hits >= 2 and psych_hits >= anti_hits:
        return "心理学洞察型", ["围绕心理机制解释行为问题", "结构包含现象、根因与修正建议"]

    if anti_hits >= 1:
        return "反常识观点型", ["开头使用反直觉命题制造认知冲突", "通过案例或推演完成观点论证"]

    return "系统方法论型", ["有稳定的方法主线和步骤感", "可抽象为可复用的系统模板"]


def _pick_emotions(content_type: str) -> tuple[str, str]:
    if content_type == "心理学洞察型":
        return "焦虑", "希望"
    if content_type == "反常识观点型":
        return "好奇", "希望"
    if content_type == "行动清单型":
        return "希望", "共鸣"
    return "共鸣", "希望"


def _pick_hook(first_sentence: str) -> str:
    if any(x in first_sentence for x in ("其实", "反常识", "作弊", "不要")):
        return "反常识钩子"
    if "我" in first_sentence:
        return "案例钩子"
    if "你" in first_sentence:
        return "场景钩子"
    return "观点钩子"


def _pick_views(keypoints: list[str], sentences: list[str], title: str) -> list[str]:
    views = keypoints[:3]
    if len(views) < 3:
        views.extend([s for s in sentences if any(k in s for k in ("核心", "关键", "真正", "只有", "就是"))][: 3 - len(views)])
    if len(views) < 3:
        views.extend(sentences[: 3 - len(views)])
    views = _uniq([_clean_text(x)[:72] for x in views if x])
    return views or [_strip_title_for_display(title)]


def _pick_quotes(keypoints: list[str], sentences: list[str]) -> list[str]:
    preferred = [s for s in sentences if 12 <= len(s) <= 60 and any(k in s for k in ("执行", "系统", "每天", "学习", "坚持", "信任", "复盘", "迭代", "拖延", "作弊"))]
    if len(preferred) < 5:
        preferred.extend([s for s in sentences if 12 <= len(s) <= 60])
    if len(preferred) < 5:
        preferred.extend(keypoints)
    quotes = _uniq([_clean_text(x)[:60] for x in preferred if x])
    return quotes[:5] or ["先行动，再用反馈修正方向"]


def _pick_entities(body: str) -> list[str]:
    entities = [name for name in NAMES if name in body]
    entities = _uniq(entities)
    return entities or ["作者本人"]


def _render_report(*, title: str, link: str, extract_date: str, body: str, summary: str, keypoints: list[str]) -> str:
    display_title = _strip_title_for_display(title)
    sentences = _split_sentences(body)
    first = sentences[0] if sentences else display_title
    last = sentences[-1] if sentences else (summary or display_title)

    content_type, reasons = _classify_type(display_title, body, keypoints)
    main_emotion, sub_emotion = _pick_emotions(content_type)
    hook = _pick_hook(first)
    views = _pick_views(keypoints, sentences, display_title)
    quotes = _pick_quotes(keypoints, sentences)
    entities = _pick_entities(body)

    if content_type == "心理学洞察型":
        part_titles = ["现象拆解：忙碌为何没有结果", "根因分析：逃避机制与人设维持", "解决路径：直连目标的行动闭环"]
    elif content_type == "反常识观点型":
        part_titles = ["观点抛出：反直觉命题", "案例论证：提前布局与反复博弈", "应用策略：主动选战场并持续迭代"]
    elif content_type == "行动清单型":
        p1 = (keypoints[0] if len(keypoints) > 0 else "最小动作启动")[:22]
        p2 = (keypoints[1] if len(keypoints) > 1 else "高频执行反馈")[:22]
        p3 = (keypoints[2] if len(keypoints) > 2 else "复盘迭代放大")[:22]
        part_titles = [f"行动一：{p1}", f"行动二：{p2}", f"行动三：{p3}"]
    else:
        part_titles = ["问题定位：当前困境与误区", "方法拆解：系统与步骤", "落地闭环：反馈、优化、复利"]

    part_summaries = [
        keypoints[0] if len(keypoints) > 0 else "先定位问题场景与核心矛盾。",
        keypoints[1] if len(keypoints) > 1 else "展开方法、机制或案例拆解。",
        keypoints[2] if len(keypoints) > 2 else "回到可执行动作和长期复盘。",
    ]

    close_type = "行动建议型"
    if "新年快乐" in body:
        close_type = "祝福型"
    elif "所以" in last:
        close_type = "总结型"

    if any(x in body for x in ("关注我", "评论区", "点赞")):
        cta_type = "硬CTA"
    elif "希望" in body:
        cta_type = "软CTA"
    else:
        cta_type = "无CTA（纯内容分享）"

    structure_value = "高" if content_type in {"系统方法论型", "行动清单型"} else "中"
    emotion_value = "高" if content_type in {"心理学洞察型", "反常识观点型"} else "中"
    quote_value = "高" if len(quotes) >= 4 else "中"
    case_value = "中" if entities else "低"

    part_len = max(120, len(body) // 3)
    part3_len = max(120, len(body) - part_len * 2)

    checks = [f"- [{'x' if x == content_type else ' '}] **{x}**" for x in TYPE_ORDER]

    quote_blocks: list[str] = []
    for idx, quote in enumerate(quotes, start=1):
        usage = "观点支撑"
        if idx == 1:
            usage = "开头钩子"
        elif any(k in quote for k in ("执行", "坚持", "每天")):
            usage = "行动建议"
        elif any(k in quote for k in ("但是", "不是")):
            usage = "反驳安全阀"
        quote_blocks.extend(
            [
                f"**金句{idx}**：\"{quote}\"",
                f"- 用途：{usage}",
                "- 潜力标签：`#选题` / `#框架` / `#标题`",
                f"- 适用主题：{TOPIC_BY_TYPE[content_type]}",
                "",
            ]
        )

    view_blocks: list[str] = []
    for idx, view in enumerate(views, start=1):
        support = entities[min(idx - 1, len(entities) - 1)]
        view_blocks.extend(
            [
                f"**观点{idx}**：{view}",
                f"- 支撑案例：{support}相关段落",
                "- 底层逻辑：先明确目标动作，再用反馈迭代优化",
                f"- 可改写方向：为什么你迟迟没结果？因为忽略了“{view[:16]}”",
                "",
            ]
        )

    case_blocks: list[str] = []
    case_entities = entities[:3]
    while len(case_entities) < 2:
        case_entities.append("作者本人")
    for idx, entity in enumerate(case_entities, start=1):
        case_blocks.extend(
            [
                f"**案例{idx}**：{entity}",
                f"- 人物：{entity}",
                f"- 场景：围绕“{display_title[:18]}”展开的实践场景",
                "- 结果：形成可持续输出与能力复利",
                "- 可替换性：中（可替换为自有案例）",
                "",
            ]
        )

    framework_name = {
        "心理学洞察型": "现象-根因-执行闭环",
        "反常识观点型": "反常识观点-案例验证-应用策略",
        "行动清单型": "低成本高频行动清单",
        "系统方法论型": "问题-方法-复盘系统",
    }[content_type]

    lines: list[str] = []
    lines.extend(
        [
            "# 对标文案分析报告",
            "",
            "## 基础信息",
            f"- 原文标题：{display_title}",
            f"- 原文链接：{link}",
            f"- 提取日期：{extract_date}",
            f"- 字数：{len(body)}字",
            "- 来源平台：抖音",
            "",
            "---",
            "",
            "## 爆款类型分类",
            "",
            *checks,
            "",
            "判定依据：",
            f"- {reasons[0]}",
            f"- {reasons[1]}",
            "",
            "---",
            "",
            "## 结构模板提炼",
            "",
            "### 开头（前200字）",
            f"- **钩子类型**：{hook}",
            f"- **钩子内容**：\"{first[:120]}\"",
            f"- **情绪触发点**：{main_emotion}（主）+{sub_emotion}（次）",
            "",
            "### 正文结构",
            "",
            f"**第一部分**：{part_titles[0]}",
            f"- 内容概要：{part_summaries[0]}",
            f"- 字数：约{part_len}字",
            f"- 核心观点：{views[0]}",
            "",
            f"**第二部分**：{part_titles[1]}",
            f"- 内容概要：{part_summaries[1]}",
            f"- 字数：约{part_len}字",
            f"- 核心观点：{views[1] if len(views) > 1 else part_summaries[1]}",
            "",
            f"**第三部分**：{part_titles[2]}",
            f"- 内容概要：{part_summaries[2]}",
            f"- 字数：约{part3_len}字",
            f"- 核心观点：{views[2] if len(views) > 2 else part_summaries[2]}",
            "",
            "### 结尾（后200字）",
            f"- **收口方式**：{close_type}",
            f"- **CTA类型**：{cta_type}",
            "",
            "---",
            "",
            "## 情绪触发点分析",
            "",
            "### 主要情绪",
            f"- 主要情绪：{main_emotion}",
            f"- 次要情绪：{sub_emotion}",
            "",
            "### 触发场景",
            f"- 场景描述：用户在“{display_title[:20]}”问题上寻找可执行方法",
            "- 痛点描述：知道重要但难以持续执行，容易在焦虑和空转中反复",
            "",
            "### 触发时机",
            "- 开头触发：第1段，通过冲突观点触发注意",
            "- 中段触发：中部段落，通过案例/方法触发共鸣与希望",
            "- 结尾触发：最后段落，通过行动收口触发执行意愿",
            "",
            "---",
            "",
            "## 可复用元素提炼",
            "",
            "### 金句提炼（3-5条）",
            "",
            *quote_blocks,
            "### 核心观点（1-3个）",
            "",
            *view_blocks,
            "### 案例素材（1-3个）",
            "",
            *case_blocks,
            "### 框架模板",
            "",
            f"**框架名称**：{framework_name}",
            "",
            f"**适用场景**：{display_title[:24]}相关主题的长文改写、口播脚本或信息图拆解",
            "",
            "**结构**：",
            "1. 抛问题/抛反常识观点",
            "2. 用方法或案例拆解",
            "3. 形成行动闭环与复盘机制",
            "",
            f"**可复用性**：{structure_value}",
            "",
            "---",
            "",
            "## 适配平台分析",
            "",
            "### 公众号",
            "- [x] 适合",
            "- [ ] 不适合",
            "",
            "**原因**：可扩写为“观点-机制-案例-动作”的深度文。",
            "",
            "**改写建议**：补充自有案例与数据，提升可信度。",
            "",
            "### 小红书",
            "- [x] 适合",
            "- [ ] 不适合",
            "",
            "**原因**：可拆成冲突开场+3点清单+总结行动。",
            "",
            "**改写建议**：改为6页结构，强化每页一个结论。",
            "",
            "### 抖音/视频号",
            "- [x] 适合",
            "- [ ] 不适合",
            "",
            "**原因**：原文本身是口播逻辑，节奏和冲突明显。",
            "",
            "**改写建议**：缩到30-90秒，结尾保留单一动作。",
            "",
            "---",
            "",
            "## 公众号改写建议",
            "",
            "### gongchang（系统方法论）",
            "- **改写方向**：强化机制解释与框架闭环",
            "- **核心逻辑**：目标动作→反馈数据→迭代升级",
            f"- **标题建议**：《{display_title[:18]}：一套能跑通的系统》",
            "- **开头建议**：先抛数据或样本，再给方法总图",
            "",
            "### ipgc（情绪共鸣）",
            "- **改写方向**：突出“我也经历过”的转折",
            "- **核心逻辑**：从迷茫/焦虑到可执行的一小步",
            f"- **标题建议**：《我从“{display_title[:10]}”里走出来的过程》",
            "- **开头建议**：用具体场景切入，增强代入感",
            "",
            "### zengzhang（数据增长）",
            "- **改写方向**：把方法翻译为投入-产出-反馈指标",
            "- **核心逻辑**：高频试错提升命中率，形成复利",
            f"- **标题建议**：《{display_title[:14]}：一套可量化的增长闭环》",
            "- **开头建议**：用前后对比数据开场",
            "",
            "### shizhan（轻实操）",
            "- **改写方向**：输出当天可执行的步骤清单",
            "- **核心逻辑**：最小动作启动，复盘迭代放大",
            f"- **标题建议**：《{display_title[:16]}：今天就能做的3个动作》",
            "- **开头建议**：先给“5分钟内能完成”的最小版本",
            "",
            "---",
            "",
            "## 三层表达分析（富贵方法论）",
            "",
            "### 事实层",
            f"- 主要事实：{views[0]}",
            "- 出现位置：开头问题定义+中段方法拆解",
            "",
            "### 感受层",
            "- 主要感受：从“被问题卡住”转向“我可以开始行动”",
            "- 出现位置：开头情绪定位与结尾收口",
            "",
            "### 想象层",
            "- 主要想象：形成稳定的长期能力与复利增长曲线",
            "- 出现位置：中后段关于长期坚持与结果回报的叙述",
            "",
            "### 三层转译示例",
            f"- 原文：\"{quotes[0]}\"",
            "- 事实：给出可观察、可执行的动作",
            "- 感受：降低无力感，提升可控感",
            "- 想象：看到长期坚持后的身份和结果变化",
            "",
            "---",
            "",
            "## 分析总结",
            "",
            "### 核心亮点",
            "1. 开场冲突强，能快速抓住注意力",
            "2. 中段有方法/机制拆解，不止情绪输出",
            "3. 结尾可转化为具体动作，便于二次创作",
            "",
            "### 可复用价值",
            f"- 结构模板：{structure_value}",
            f"- 情绪触发：{emotion_value}",
            f"- 金句质量：{quote_value}",
            f"- 案例质量：{case_value}",
            "",
            "### 建议用途",
            "- [x] 直接改写为公众号内容",
            "- [x] 提炼金句到金句库",
            "- [x] 提炼框架到内容框架库",
            "- [x] 提炼选题到选题池",
            "- [x] 作为参考案例学习",
            "",
            "---",
            "",
            "## 分析人/分析日期",
            "- 分析人：AI",
            f"- 分析日期：{_today_shanghai()}",
            "- 版本：v1.0",
            "",
        ]
    )
    return "\n".join(lines)


def _write_index(*, scan_date: str, rows: list[dict[str, Any]], created: int, skipped: int, dry_run: bool) -> str:
    index_dir = REPORT_ROOT / scan_date
    index_dir.mkdir(parents=True, exist_ok=True)
    index_path = index_dir / "00-自动分析索引.md"

    lines = [
        "# 对标文案自动分析索引",
        "",
        f"- 生成日期：{_today_shanghai()}",
        f"- 扫描日期：{scan_date}",
        f"- 扫描文件数：{len(rows)}",
        f"- 新增报告：{created}",
        f"- 已存在跳过：{skipped}",
        f"- 执行模式：{'dry-run' if dry_run else 'live'}",
        "",
        "| # | 原文标题 | 状态 | 提取正文 | 分析报告 |",
        "|---|---|---|---|---|",
    ]

    for idx, row in enumerate(rows, start=1):
        lines.append(
            f"| {idx} | {row.get('title', '-')} | {row.get('status', '-')} | `{row.get('source', '-')}` | `{row.get('report', '-')}` |"
        )

    lines.extend(
        [
            "",
            "## 备注",
            "- 本流程会自动扫描指定日期提取正文并按分析模板生成报告。",
            "- 默认只补齐缺失报告；如需覆盖重跑，请使用`--overwrite`。",
            "",
        ]
    )

    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path.relative_to(REPO_ROOT).as_posix()


def run_benchmark_analysis(payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    scan_date = _resolve_scan_date(payload)
    overwrite = bool(payload.get("overwrite"))
    limit = int(payload.get("limit") or 0)
    source_user = str(payload.get("source_user") or "").strip()
    event_ref = str(payload.get("event_ref") or "").strip()

    task_id = make_task_id("benchmark-analysis")
    create_task(
        task_id=task_id,
        event_ref=event_ref,
        task_type="benchmark_analysis_run",
        status="running",
        phase="scan",
        platform="benchmark",
        source_user=source_user,
        payload={"request": payload, "scan_date": scan_date},
        result={"status": "running"},
    )

    try:
        source_dir = EXTRACT_ROOT / scan_date
        if not source_dir.exists():
            result = {
                "status": "success",
                "scan_date": scan_date,
                "message": "no_source_dir",
                "source_dir": source_dir.relative_to(REPO_ROOT).as_posix(),
                "scanned": 0,
                "created": 0,
                "skipped": 0,
                "index_path": "",
                "rows": [],
            }
            update_task(task_id, status="success", phase="completed", result_json=_json_dumps(result), error_text="")
            return {"status": "success", "task_id": task_id, "result": result}

        files = sorted(source_dir.glob("*.md"))
        if limit > 0:
            files = files[:limit]

        rows: list[dict[str, Any]] = []
        created = 0
        skipped = 0
        errors: list[str] = []

        for src in files:
            raw = src.read_text(encoding="utf-8")
            title = _parse_title(raw, src.stem)
            link = _parse_link(raw)
            keypoints = _parse_keypoints(raw)
            summary = _parse_summary(raw)
            body = _parse_body(raw)

            report_name = _report_name_from_title(title)
            report_path = REPORT_ROOT / scan_date / report_name

            if report_path.exists() and not overwrite:
                skipped += 1
                rows.append(
                    {
                        "title": _strip_title_for_display(title),
                        "status": "已存在",
                        "source": src.relative_to(REPO_ROOT).as_posix(),
                        "report": report_path.relative_to(REPO_ROOT).as_posix(),
                    }
                )
                continue

            if dry_run:
                created += 1
                rows.append(
                    {
                        "title": _strip_title_for_display(title),
                        "status": "待生成(dry-run)",
                        "source": src.relative_to(REPO_ROOT).as_posix(),
                        "report": report_path.relative_to(REPO_ROOT).as_posix(),
                    }
                )
                continue

            try:
                report_content = _render_report(
                    title=title,
                    link=link,
                    extract_date=scan_date,
                    body=body,
                    summary=summary,
                    keypoints=keypoints,
                )
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(report_content, encoding="utf-8")
                created += 1
                rows.append(
                    {
                        "title": _strip_title_for_display(title),
                        "status": "新增",
                        "source": src.relative_to(REPO_ROOT).as_posix(),
                        "report": report_path.relative_to(REPO_ROOT).as_posix(),
                    }
                )
            except Exception as exc:  # noqa: PERF203
                err = f"{src.name}: {exc}"
                errors.append(err)
                rows.append(
                    {
                        "title": _strip_title_for_display(title),
                        "status": "失败",
                        "source": src.relative_to(REPO_ROOT).as_posix(),
                        "report": report_path.relative_to(REPO_ROOT).as_posix(),
                    }
                )

        index_path = _write_index(scan_date=scan_date, rows=rows, created=created, skipped=skipped, dry_run=dry_run) if not dry_run else ""

        overall_status = "success" if not errors else "partial"
        result = {
            "status": overall_status,
            "scan_date": scan_date,
            "scanned": len(files),
            "created": created,
            "skipped": skipped,
            "errors": errors,
            "index_path": index_path,
            "rows": rows,
        }
        update_task(
            task_id,
            status="success" if overall_status == "success" else "error",
            phase="completed",
            result_json=_json_dumps(result),
            error_text="\n".join(errors[:5]) if errors else "",
        )
        add_task_log(task_id, "benchmark_analysis_completed", {"scan_date": scan_date, "created": created, "skipped": skipped, "errors": len(errors)})
        run_log = append_run_log(
            "benchmark_analysis_run",
            {"task_id": task_id, "scan_date": scan_date, "created": created, "skipped": skipped, "errors": len(errors)},
        )
        return {
            "status": overall_status,
            "task_id": task_id,
            "phase": "completed",
            "scan_date": scan_date,
            "run_log": run_log,
            "result": result,
        }

    except Exception as exc:  # noqa: PERF203
        error_text = str(exc)
        update_task(task_id, status="error", phase="scan", error_text=error_text)
        add_task_log(task_id, "benchmark_analysis_failed", {"error": error_text})
        dead_log = append_dead_letter("benchmark_analysis_failed", {"task_id": task_id, "scan_date": scan_date, "error": error_text})
        return {
            "status": "error",
            "task_id": task_id,
            "phase": "scan",
            "scan_date": scan_date,
            "errors": [error_text],
            "dead_letter_log": dead_log,
        }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark analysis runner")
    parser.add_argument("--date", default="today", help="Base date (YYYY-MM-DD/today/yesterday)")
    parser.add_argument("--scan-date", default="", help="Explicit scan date (YYYY-MM-DD)")
    parser.add_argument("--mode", default="yesterday", choices=["yesterday", "same_day"], help="Scan mode relative to --date")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing reports")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of source files")
    parser.add_argument("--payload-file", default="", help="Optional payload JSON")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    payload: dict[str, Any] = {
        "date": args.date,
        "scan_mode": args.mode,
        "overwrite": bool(args.overwrite),
        "limit": int(args.limit),
    }
    if args.scan_date:
        payload["scan_date"] = args.scan_date
    if args.payload_file:
        raw = json.loads(Path(args.payload_file).read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            payload.update(raw)
    result = run_benchmark_analysis(payload, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if str(result.get("status") or "") in {"success", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))
