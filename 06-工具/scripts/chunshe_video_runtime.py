#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime helpers for the staged 椿舍 short-video workflow."""

from __future__ import annotations

import json
import re
from typing import Any

OUTPUT_SPECS = {
    "精简发布版": {
        "char_range": "90-140字",
        "line_range": "5-7行",
        "seconds": "20-35秒",
    },
    "解释型口播版": {
        "char_range": "140-220字",
        "line_range": "7-10行",
        "seconds": "25-45秒",
    },
}

OPENING_FAMILY_BY_ANGLE = {
    "痛点确认": "后果先行型",
    "防御拆解": "冲突实景型",
    "规矩托底": "直接判断型",
    "向往画面": "后果先行型",
    "本地决策": "直接判断型",
    "关系/身份翻译": "一秒后悔型",
}

OPENING_FALLBACK_BY_ENTRY = {
    "问题修复": "后果先行型",
    "信任怀疑": "冲突实景型",
    "放松养护": "直接判断型",
    "本地找店": "直接判断型",
}

OPENING_ACTION_TOKENS = (
    "镜子",
    "电梯",
    "下班",
    "刚躺下",
    "灯一关",
    "说不要",
    "不买了",
    "继续讲",
    "推销",
    "办卡",
    "收尾",
    "拒绝",
    "预约前",
    "门一推开",
    "肩膀",
    "脸一照",
    "有个姑娘",
    "有个顾客",
    "有个老客户",
    "有一次",
    "上周",
    "上礼拜",
    "前两天",
    "前几天",
    "之前有",
    "第一次来",
    "做完脸",
    "做到一半",
)

ARTICLE_OPENING_MARKERS = (
    "很多人搜",
    "她会搜",
    "表面上",
    "真正",
    "本质",
    "对她来说",
    "往往是在一个很具体的晚上",
)

ROLE_NAME_MARKERS = ("李可", "孟欣", "伍霖")

EXPLANATORY_BRIDGE_MARKERS = (
    "所以她找的",
    "她真正想要的",
    "她想确认的重点",
    "她真正发愁的",
    "她在找的",
)

LITERARY_MARKERS = (
    "像做审判",
    "被耗掉",
    "状态不对",
    "人先绷住",
    "人先紧了",
    "松口气",
    "这趟就值了",
)

BODY_UNSAFE_MARKERS = (
    "掏钱付费",
    "专属感",
    "粗糙的开始",
    "先松下来",
    "有尊严的服务感",
    "研究人性",
    "给人使绊子",
    "向你证明他自己",
    "只做筛选，不去教育",
    "对于其他人，只做筛选",
    "做好当下该做的事情",
    "改变的初期总是不太舒服的",
    "已经非常非常好了",
    "经历一遍被追着讲的过程",
    "经历一次这种过程",
)

COMPLAINT_BODY_MARKERS = (
    "推销",
    "办卡",
    "敷衍",
    "不买",
    "说不买",
    "拒绝",
    "套路",
    "忽悠",
)

TEACHER_TONE_MARKERS = (
    "只做筛选，不去教育",
    "做好当下该做的事情",
    "改变的初期总是不太舒服的",
)

BOSS_VOICE_MARKERS = (
    "我说",
    "我跟她说",
    "我就回",
    "哎，",
    "行，",
    "你先",
    "你放心",
    "在我这",
)

LOW_PRESSURE_MARKERS = (
    "就行",
    "就够了",
    "吧",
    "没什么的",
    "没白跑",
    "别堵着",
    "别觉得",
    "别比来之前",
    "先别",
    "没人找你",
)

STORY_MARKERS = (
    "有个姑娘",
    "有个顾客",
    "有个老客户",
    "有一次",
    "上周",
    "上礼拜",
    "前两天",
    "前几天",
    "之前有",
    "她说",
    "他说",
    "第一次来",
)

LONGING_MARKERS = (
    "睡着",
    "闭眼",
    "舒服",
    "安静",
    "没人找",
    "不用回消息",
    "没事",
    "还行",
    "轻了",
    "不用再找",
    "不用提前",
    "不用解释",
    "心情",
    "空了",
)

REPEATED_ROLE_TERMS = ("美容师", "店员", "顾客")


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _split_lines(text: str) -> list[str]:
    return [str(line or "").strip() for line in str(text or "").splitlines() if str(line or "").strip()]


def _extract_section(markdown_text: str, names: tuple[str, ...]) -> str:
    valid_names = set(names)
    all_headers = {
        "# 标题",
        "## 标题",
        "# 备选标题",
        "## 备选标题",
        "# 标题候选",
        "## 标题候选",
        "# 正文",
        "## 正文",
        "# 口播正文",
        "## 口播正文",
        "# 置顶评论",
        "## 置顶评论",
        "# 回复模板",
        "## 回复模板",
    }
    current = False
    lines: list[str] = []
    for raw in str(markdown_text or "").splitlines():
        stripped = str(raw or "").strip()
        if stripped in valid_names:
            current = True
            continue
        if stripped in all_headers:
            if current:
                break
            current = False
            continue
        if current:
            lines.append(str(raw or "").rstrip())
    return "\n".join(lines).strip()


def _is_body_safe_line(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    return not any(marker in value for marker in BODY_UNSAFE_MARKERS)


def _build_chunshe_prompt_topic_view(topic: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = (
        "topic_id",
        "topic_title",
        "seed_keyword",
        "entry_class",
        "angle_type",
        "scene_trigger",
        "fear",
        "real_desire",
        "real_buy_point",
        "store_rule_hint",
        "opening_family",
        "opening_scene",
        "first_conflict_line",
        "scene_line",
        "theme_line",
        "translation_line",
        "boss_judgment_line",
        "pivot_line",
        "store_rule_line",
        "low_pressure_offer_line",
        "landing_line",
        "scene_opener",
        "boss_response",
        "longing_moment",
        "low_bar_ending",
    )
    return {key: topic.get(key, "") for key in allowed_keys}


def _build_chunshe_prompt_source_pack_view(source_pack: dict[str, Any]) -> dict[str, Any]:
    business_facts = dict(source_pack.get("business_facts") or {})
    hard_rules = dict(source_pack.get("hard_rules") or {})
    brief_extract = dict(source_pack.get("brief_extract") or {})
    topic_helpers = dict(source_pack.get("topic_helpers") or {})
    review_language = dict(source_pack.get("review_language") or {})
    theme_language = dict(source_pack.get("theme_language") or {})
    return {
        "business_facts": {
            "store_positioning": business_facts.get("store_positioning", ""),
            "allowed_services": business_facts.get("allowed_services", []),
            "review_boundary": business_facts.get("review_boundary", []),
            "output_goal": business_facts.get("output_goal", {}),
        },
        "hard_rules": {
            "banned_openings": hard_rules.get("banned_openings", []),
            "banned_patterns": hard_rules.get("banned_patterns", []),
            "body_cta_blacklist": hard_rules.get("body_cta_blacklist", []),
            "opening_guards": hard_rules.get("opening_guards", []),
            "question_guards": hard_rules.get("question_guards", []),
        },
        "brief_extract": {
            "seed_keyword": brief_extract.get("seed_keyword", ""),
            "entry_class": brief_extract.get("entry_class", ""),
            "role": brief_extract.get("role", ""),
            "output_type": brief_extract.get("output_type", ""),
            "mode": brief_extract.get("mode", ""),
            "target_audience": brief_extract.get("target_audience", ""),
            "scene_trigger_hint": brief_extract.get("scene_trigger_hint", ""),
            "buy_point": brief_extract.get("buy_point", ""),
        },
        "topic_helpers": {
            "high_boundary": topic_helpers.get("high_boundary", False),
            "store_rule_options": topic_helpers.get("store_rule_options", []),
            "ending_function_options": topic_helpers.get("ending_function_options", []),
        },
        "review_language": {
            "principle": review_language.get("principle", ""),
            "bucket": review_language.get("bucket", ""),
            "opening_examples": review_language.get("opening_examples", []),
            "scene_examples": review_language.get("scene_examples", []),
            "landing_examples": review_language.get("landing_examples", []),
        },
        "theme_language": {
            "principle": theme_language.get("principle", ""),
        },
    }


def _char_bigram_set(text: str) -> set[str]:
    value = _normalize_key(text)
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _is_semantically_redundant(candidate: str, existing_lines: list[str]) -> bool:
    candidate_text = str(candidate or "").strip()
    if not candidate_text:
        return False
    candidate_key = _normalize_key(candidate_text)
    candidate_grams = _char_bigram_set(candidate_text)
    for line in existing_lines:
        line_text = str(line or "").strip()
        if not line_text:
            continue
        line_key = _normalize_key(line_text)
        if candidate_key and (candidate_key in line_key or line_key in candidate_key):
            return True
        line_grams = _char_bigram_set(line_text)
        if not candidate_grams or not line_grams:
            continue
        overlap = len(candidate_grams & line_grams)
        union = len(candidate_grams | line_grams)
        if union and (overlap / union) >= 0.55:
            return True
    return False


def enrich_chunshe_video_topic(topic: dict[str, Any]) -> dict[str, Any]:
    from chunshe_engine import enrich_chunshe_video_topic as _engine_enrich_chunshe_video_topic

    return _engine_enrich_chunshe_video_topic(topic)


def build_chunshe_video_draft_package_prompt(
    *,
    role: str,
    output_type: str,
    brief: str,
    mode: str,
    source_pack: dict[str, Any],
    topic: dict[str, Any],
) -> str:
    spec = OUTPUT_SPECS.get(output_type, OUTPUT_SPECS["精简发布版"])
    opening_family = str(topic.get("opening_family") or "").strip()
    topic_view = _build_chunshe_prompt_topic_view(topic)
    source_pack_view = _build_chunshe_prompt_source_pack_view(source_pack)
    return (
        "你是椿舍短视频内容系统的 draft_package 阶段。\n"
        "目标：写一条像老板坐在店里跟朋友聊天一样的口播稿。\n"
        "口播标准：像老板嘴里直接说出来的话，不是文案。\n"
        "你只负责两件事：给 3 个标题，写 1 条口播正文。\n"
        "只输出一个 JSON 对象，不要解释，不要输出 markdown 分节标题。\n"
        "JSON schema:\n"
        "{\n"
        '  "entry_class": "string",\n'
        '  "person": "string",\n'
        '  "recent_trigger": "string",\n'
        '  "fear": "string",\n'
        '  "real_buy_point": "string",\n'
        '  "store_rule_primary": "string",\n'
        '  "narrative_ratio": "string",\n'
        '  "title_candidates": ["string", "string", "string"],\n'
        '  "draft_body": "string"\n'
        "}\n\n"
        "# 满分稿标准（必须遵守）\n\n"
        "1. 开头是一个具体的人的具体经历，不是解释型前言。\n"
        "2. 向往感占正文 50% 以上，要写能让人松下来的具体画面。\n"
        "3. 老板不立规矩，老板回应；多写“我说”“你先”“在我这”。\n"
        "4. 叙事比例 2-5-3：20% 冲突 + 50% 向往画面 + 30% 规则托底。\n"
        "5. 标题要像能直接发的小红书/短视频标题，不要像分析结论。\n\n"
        "# 满分稿示例 A\n\n"
        "上周有个姑娘第一次来做脸。\n"
        "刚躺下就跟我说，姐，你别推销啊。\n"
        "我说行，你先闭眼，做完叫你。\n"
        "做到一半我进去瞄了一眼。\n"
        "哎，睡着了。\n"
        "在我这做脸，真不用那么紧张。\n"
        "忙了一天了，先躺一会儿嘛。\n\n"
        "# 满分稿示例 B\n\n"
        "前两天有个姑娘做完脸跟我说了句话。\n"
        "她说姐，推销我忍忍也就算了。\n"
        "最难受的是我说了不买。\n"
        "她手上的动作就变了。\n"
        "哎，我懂这种感觉。\n"
        "在我这做脸，你买不买东西。\n"
        "手上的活儿都是一样的。\n"
        "做完出来，起码别觉得自己受了委屈。\n\n"
        "# 硬要求\n\n"
        f"- 输出类型：{output_type}，目标 {spec['char_range']}，{spec['line_range']}，适合 {spec['seconds']} 直接录。\n"
        "- 正文按短句分行，每行只讲一个意思。\n"
        "- 优先使用脚本骨架里的 scene_opener、boss_response、longing_moment、low_bar_ending。\n"
        "- 不要把正文写成固定模块拼装。\n"
        "- 不要先做搜索动机解释，再进入故事。\n"
        "- 禁止“不是A，是B”、禁止“我是开美容院的”、禁止正文 CTA。\n\n"
        f"【角色】\n{role}\n"
        f"【运行模式】\n{mode}\n"
        f"【开头家族】\n{opening_family}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack_view, ensure_ascii=False, indent=2)}\n\n"
        f"【脚本骨架】\n{json.dumps(topic_view, ensure_ascii=False, indent=2)}\n\n"
        f"【用户 brief】\n{brief.strip()}\n"
    )


def build_chunshe_video_polish_package_prompt(
    *,
    markdown_text: str,
    topic: dict[str, Any],
    role: str,
    output_type: str,
    mode: str,
    source_pack: dict[str, Any],
    quote_candidate: dict[str, Any] | None,
    retry_issues: list[str] | None = None,
) -> str:
    topic_view = _build_chunshe_prompt_topic_view(topic)
    source_pack_view = _build_chunshe_prompt_source_pack_view(source_pack)
    retry_block = ""
    if retry_issues:
        retry_block = f"【上一轮仍未解决的问题】\n{json.dumps(retry_issues, ensure_ascii=False, indent=2)}\n\n"
    quote_block = "【候选结尾收口句】\n暂无\n\n"
    if quote_candidate:
        quote_block = f"【候选结尾收口句】\n{json.dumps(quote_candidate, ensure_ascii=False, indent=2)}\n\n"
    return (
        "你是椿舍短视频内容系统的 polish_package 阶段。\n"
        "目标：确认稿子像老板在说人话，并修到满分稿标准。\n"
        "只输出一个 JSON 对象，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "passed": true,\n'
        '  "issues": ["string"],\n'
        '  "removed_or_softened_claims": ["string"],\n'
        '  "final_markdown": "string"\n'
        "}\n\n"
        "# 质量检查（逐项过）\n\n"
        "1. 这是故事，不是判断说明。开头必须有具体的人和具体经历。\n"
        "2. 向往感够不够，至少要有能让人放松的画面。\n"
        "3. 老板像不像真人，说话能不能直接念出口。\n"
        "4. 有没有“不是A，是B”模板句，有就删掉。\n"
        "5. 结尾是不是低标准承诺，而不是空泛安抚。\n"
        "6. 老板是在回应，不是在立规矩。\n"
        "7. 有没有正文 CTA，有就删掉。\n\n"
        "# 修改原则\n\n"
        "- 只修标题和正文，不扩写运营信息。\n"
        "- 文艺话改成人话，解释句改成故事句。\n"
        "- 如果缺向往感，就从 longing_moment 里补一句。\n"
        "- 如果缺老板回应，就从 boss_response 里补一句。\n"
        "- issues 只写仍存在的问题，不写“已修好”。\n"
        "- passed=true 的前提：故事感有了、向往感够了、老板像真人、没有“不是A，是B”。\n\n"
        f"【角色】\n{role}\n"
        f"【输出类型】\n{output_type}\n"
        f"【运行模式】\n{mode}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack_view, ensure_ascii=False, indent=2)}\n\n"
        f"【脚本骨架】\n{json.dumps(topic_view, ensure_ascii=False, indent=2)}\n\n"
        f"{quote_block}{retry_block}"
        f"【当前成稿】\n{markdown_text.strip()}\n"
    )


def build_chunshe_video_pinned_comment(*, topic: dict[str, Any], source_pack: dict[str, Any]) -> str:
    business_facts = dict(source_pack.get("business_facts") or {})
    positioning = str(business_facts.get("store_positioning") or "椿舍在苏州吴江").strip().rstrip("。！？；;! ")
    starter = "基础清洁或补水舒缓"
    store_rule = str(topic.get("store_rule_hint") or "说不要，话题就停").strip() or "说不要，话题就停"
    return (
        f"{positioning}。店名、位置和怎么过来放这条置顶里。\n"
        f"第一次来，先从{starter}开始就够了；现场可以给建议，但{store_rule}。"
    )


def build_chunshe_video_reply_template(*, topic: dict[str, Any]) -> str:
    fear = str(topic.get("fear") or "怕刚躺下，对面就开始把问题越说越重").strip()
    store_rule = str(topic.get("store_rule_hint") or "说不要，话题就停").strip() or "说不要，话题就停"
    low_pressure_offer = str(topic.get("low_pressure_offer_line") or "第一次来，先把这一趟安安稳稳做完。").strip()
    return f"{fear}很正常。{store_rule}。{low_pressure_offer}"


def normalize_chunshe_video_markdown(markdown_text: str, title_candidates: list[str]) -> str:
    sections = {
        "title": [],
        "alt_titles": [],
        "body": [],
        "comment": [],
        "reply": [],
    }
    current: str | None = None
    for raw_line in str(markdown_text or "").splitlines():
        line = str(raw_line or "").rstrip()
        stripped = line.strip()
        if stripped in {"# 标题", "## 标题"}:
            current = "title"
            continue
        if stripped in {"# 标题候选", "## 标题候选", "# 备选标题", "## 备选标题"}:
            current = "alt_titles"
            continue
        if stripped in {"# 正文", "## 正文", "# 口播正文", "## 口播正文"}:
            current = "body"
            continue
        if stripped in {"# 置顶评论", "## 置顶评论"}:
            current = "comment"
            continue
        if stripped in {"# 回复模板", "## 回复模板"}:
            current = "reply"
            continue
        if current:
            sections[current].append(line)

    def _first_non_empty(lines: list[str]) -> str:
        for item in lines:
            text = str(item or "").strip()
            if text:
                return text.lstrip("- ").strip()
        return ""

    fallback_title = "先别急着决定做不做脸"
    title = _first_non_empty(sections["title"]) or (title_candidates[0] if title_candidates else fallback_title)
    alt_titles: list[str] = []
    seen_alt: set[str] = {_normalize_key(title)}
    for item in sections["alt_titles"]:
        text = str(item or "").strip().lstrip("- ").strip()
        key = _normalize_key(text)
        if not text or not key or key in seen_alt:
            continue
        seen_alt.add(key)
        alt_titles.append(text)
    for item in title_candidates[1:]:
        text = str(item or "").strip()
        key = _normalize_key(text)
        if not text or not key or key in seen_alt:
            continue
        seen_alt.add(key)
        alt_titles.append(text)
        if len(alt_titles) >= 2:
            break

    parts = [
        "# 标题",
        title,
        "",
        "## 备选标题",
        f"- {alt_titles[0] if len(alt_titles) > 0 else (title_candidates[1] if len(title_candidates) > 1 else title)}",
        f"- {alt_titles[1] if len(alt_titles) > 1 else (title_candidates[2] if len(title_candidates) > 2 else title)}",
        "",
        "# 正文",
        "\n".join(sections["body"]).strip(),
        "",
        "# 置顶评论",
        "\n".join(sections["comment"]).strip(),
        "",
        "# 回复模板",
        "\n".join(sections["reply"]).strip(),
    ]
    return "\n".join(parts).strip() + "\n"


def render_chunshe_video_markdown(
    *,
    title_candidates: list[str],
    body_text: str,
    pinned_comment: str,
    reply_template: str,
) -> str:
    fallback_title = "先别急着决定做不做脸"
    return normalize_chunshe_video_markdown(
        "\n".join(
            [
                "# 标题",
                title_candidates[0] if title_candidates else fallback_title,
                "",
                "## 备选标题",
                f"- {title_candidates[1] if len(title_candidates) > 1 else (title_candidates[0] if title_candidates else fallback_title)}",
                f"- {title_candidates[2] if len(title_candidates) > 2 else (title_candidates[0] if title_candidates else fallback_title)}",
                "",
                "# 正文",
                str(body_text or "").strip(),
                "",
                "# 置顶评论",
                str(pinned_comment or "").strip(),
                "",
                "# 回复模板",
                str(reply_template or "").strip(),
            ]
        ),
        title_candidates,
    )


def extract_chunshe_video_draft_body(payload: dict[str, Any]) -> str:
    direct_body = str(payload.get("draft_body") or "").strip()
    if direct_body:
        return direct_body
    markdown_text = str(payload.get("draft_markdown") or "").strip()
    if not markdown_text:
        return ""
    return _extract_section(markdown_text, ("# 正文", "## 正文", "# 口播正文", "## 口播正文")) or markdown_text


def ensure_chunshe_video_core_lines(
    body_text: str,
    topic: dict[str, Any] | None,
    *,
    output_type: str = "精简发布版",
) -> str:
    lines = _split_lines(body_text)
    if not topic:
        return "\n".join(lines).strip()

    boss_judgment_line = str(topic.get("boss_judgment_line") or "").strip()
    boss_response = str(topic.get("boss_response") or "").strip()
    low_pressure_offer_line = str(topic.get("low_pressure_offer_line") or "").strip()
    low_bar_ending = str(topic.get("low_bar_ending") or "").strip()

    if not _is_body_safe_line(boss_judgment_line):
        boss_judgment_line = ""
    if not _is_body_safe_line(boss_response):
        boss_response = ""
    if not _is_body_safe_line(low_pressure_offer_line):
        low_pressure_offer_line = ""
    if not _is_body_safe_line(low_bar_ending):
        low_bar_ending = ""

    body_joined = "\n".join(lines)

    def _has_line(candidate: str) -> bool:
        if not candidate:
            return False
        candidate_key = _normalize_key(candidate)
        if not candidate_key:
            return False
        for line in lines:
            if _normalize_key(line) == candidate_key:
                return True
        return _body_contains_target_line(body_joined, candidate)

    has_boss = any(marker in body_joined for marker in BOSS_VOICE_MARKERS)
    if not has_boss:
        inject = boss_response or boss_judgment_line
        if inject and not _has_line(inject):
            insert_at = min(max(len(lines) - 2, 2), len(lines))
            lines.insert(insert_at, inject)

    has_low = any(marker in body_joined for marker in LOW_PRESSURE_MARKERS)
    if not has_low:
        inject = low_bar_ending or low_pressure_offer_line
        if inject and not _has_line(inject) and not _is_semantically_redundant(inject, lines):
            lines.append(inject)

    max_lines = 7 if output_type == "精简发布版" else 10
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    return "\n".join(line for line in lines if str(line or "").strip()).strip()


def normalize_chunshe_video_polish_issues(raw_issues: list[Any]) -> list[str]:
    resolved_markers = (
        "已收口",
        "已改",
        "已改成",
        "已改为",
        "已调整",
        "已删",
        "已删除",
        "已弱化",
        "已压缩",
        "已去掉",
        "未使用候选结尾金句",
    )
    unresolved_markers = (
        "仍有",
        "依然",
        "还有",
        "缺少",
        "为空",
        "出现",
        "命中",
        "需要",
        "过桥",
        "跳句",
        "模板味",
        "AI味",
        "CTA",
        "生硬",
        "不顺",
        "抢戏",
        "不自然",
        "文章开头",
        "短视频",
    )
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in raw_issues or []:
        text = str(raw or "").strip()
        key = _normalize_key(text)
        if not text or not key or key in seen:
            continue
        if any(marker in text for marker in resolved_markers) and not any(marker in text for marker in unresolved_markers):
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _body_contains_target_line(body_text: str, target_line: str) -> bool:
    body = str(body_text or "")
    target = str(target_line or "").strip()
    if not body or not target:
        return False
    tokens = [token for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", target) if len(token) >= 2]
    if not tokens:
        return False
    hits = sum(1 for token in tokens if token in body)
    return hits >= min(2, len(tokens))


def validate_chunshe_video_markdown(
    markdown_text: str,
    body_cta_blacklist: tuple[str, ...],
    topic: dict[str, Any] | None = None,
) -> list[str]:
    text = str(markdown_text or "").strip()
    issues: list[str] = []
    required_sections = ("# 标题", "## 备选标题", "# 正文", "# 置顶评论", "# 回复模板")
    for section in required_sections:
        if section not in text:
            issues.append(f"缺少区块：{section}")

    title = _extract_section(text, ("# 标题", "## 标题"))
    body = _extract_section(text, ("# 正文", "## 正文", "# 口播正文", "## 口播正文"))
    comment = _extract_section(text, ("# 置顶评论", "## 置顶评论"))
    reply = _extract_section(text, ("# 回复模板", "## 回复模板"))
    if not body:
        issues.append("# 正文 为空")
        return issues
    if not comment:
        issues.append("# 置顶评论 为空")
    if not reply:
        issues.append("# 回复模板 为空")

    first_lines = _split_lines(body)[:2]
    joined_first_lines = "".join(first_lines)
    if "有用吗？有用，但" in text:
        issues.append("仍有问答模板味")
    if "值不值，先看" in text:
        issues.append("仍有值不值模板味")
    if "我是开美容院的" in body or "我是开美容院的" in title:
        issues.append("仍有身份开头")
    if "我店里有条规矩" in body:
        issues.append("仍有店规开头")
    if any(marker in joined_first_lines for marker in ARTICLE_OPENING_MARKERS):
        issues.append("前两行仍是文章开头")
    if any(line.startswith(marker) or f"{marker}会搜" in line for marker in ROLE_NAME_MARKERS for line in first_lines):
        issues.append("仍有角色名开头")
    if first_lines and not any(token in joined_first_lines for token in OPENING_ACTION_TOKENS):
        issues.append("前两行缺少具体动作/场景")
    if first_lines and not (24 <= len(joined_first_lines) <= 48):
        issues.append("前两行长度不适合短视频口播")

    body_lines = _split_lines(body)
    if len(body_lines) < 5 or len(body_lines) > 10:
        issues.append("正文还像长段落")
    if "\n" not in body and len(body) >= 80:
        issues.append("正文还像长段落")
    abstract_markers = ("很多人搜", "她会搜", "表面上", "本质", "真正", "对她来说")
    abstract_hits = sum(1 for line in body_lines[:4] if any(marker in line for marker in abstract_markers))
    if abstract_hits >= 2 and "正文还像长段落" not in issues:
        issues.append("正文还像长段落")

    literary_hit_count = sum(1 for marker in LITERARY_MARKERS if marker in body)
    if literary_hit_count >= 2 or any(marker in joined_first_lines for marker in LITERARY_MARKERS):
        issues.append("正文有点文艺，不够直白")

    if any(marker in body for marker in EXPLANATORY_BRIDGE_MARKERS):
        issues.append("中段仍有解释式过桥")
    if any(marker in body for marker in BODY_UNSAFE_MARKERS):
        issues.append("正文仍有主题句直塞")
    if any(marker in body for marker in TEACHER_TONE_MARKERS):
        issues.append("正文仍有方法论老师口气")
    if any(body.count(term) > 1 for term in REPEATED_ROLE_TERMS):
        issues.append("角色称谓重复，读起来啰嗦")
    if any(sum(part in line for part in ("。", "，", "；", "：")) >= 2 for line in body_lines):
        issues.append("单行承载过多判断")
    if any(token in body for token in body_cta_blacklist):
        hit = next(token for token in body_cta_blacklist if token in body)
        issues.append(f"正文出现CTA黑名单：{hit}")
    if any(token in joined_first_lines for token in ("表面上", "本质", "真正", "对她来说")):
        issues.append("仍有解释味或AI味")
    if "不是" in body and "而是" in body:
        issues.append("仍有解释味或AI味")

    if not any(marker in body for marker in STORY_MARKERS):
        issues.append("缺少具体人")
    if not any(marker in body for marker in LONGING_MARKERS):
        issues.append("缺少向往画面")

    not_a_is_b_count = len(re.findall(r"不是.{0,12}?[，,、 ]?.{0,4}?是", body))
    if not_a_is_b_count >= 2:
        issues.append(f"“不是A，是B”用了{not_a_is_b_count}次")
    elif not_a_is_b_count == 1:
        issues.append("向往感不足")

    if not any(marker in body for marker in BOSS_VOICE_MARKERS):
        issues.append("缺少老板回应")
    if not any(marker in body for marker in LOW_PRESSURE_MARKERS):
        issues.append("缺少低压力承接")
    return issues
