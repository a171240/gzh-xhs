#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime helpers for the staged 椿舍 short-video workflow."""

from __future__ import annotations

import json
import re
from typing import Any

OUTPUT_SPECS = {
    "精简发布版": {
        "char_range": "280-360字",
        "line_range": "10-14行",
        "seconds": "45-65秒",
    },
    "解释型口播版": {
        "char_range": "420-650字",
        "line_range": "14-18行",
        "seconds": "75-95秒",
    },
}

COVER_TEMPLATE_LABELS = {
    "A": "A 暖调文字海报",
    "B": "B 手写感便签",
    "C": "C 对话气泡",
}

COVER_DIALOGUE_KEYWORDS = ("她说", "跟我说", "问我", "跟我聊", "发消息")
COVER_HANDWRITE_KEYWORDS = ("记到现在", "说了句话", "三个字", "说了句实话", "笑了一下")
COVER_HIGHLIGHT_CANDIDATES = (
    "动作",
    "变了",
    "不用换了",
    "不用回消息",
    "睡着了",
    "推销",
    "委屈",
    "心累",
    "难受",
    "犹豫",
    "舒服",
    "踏实",
    "轻了",
    "笑了",
    "做坏了",
    "爆痘",
    "红了",
    "疼",
)
COVER_NEGATIVE_PROMPTS = (
    "人物照片",
    "产品图",
    "英文字母",
    "二维码",
    "水印",
    "冷色调",
    "科技感",
    "3D效果",
    "卡通风格",
    "文字变形扭曲",
    "文字模糊",
    "乱码",
    "复杂背景",
    "人物变形",
)
COVER_TEMPLATE_LAYOUTS = {
    "A": "三行冲突式，标题居中偏上，大字短句，整句先可读再做局部高亮。",
    "B": "手写便签式，像老板随手记下来的真心话，版面松一点，纸张边缘有轻微阴影。",
    "C": "对话气泡式，用单个主气泡承接标题，像聊天截图，但不要做成真实聊天界面。",
}
COVER_TEMPLATE_VISUALS = {
    "A": "暖米白到浅杏色渐变背景，轻纸质肌理，留白 40-50%，手机端一眼能读完，不要信息图感。",
    "B": "奶油色便签纸背景，暖光晕染，真实纸张纹理，氛围像记下来的话，手机端可读性优先。",
    "C": "浅米色背景配白色圆角气泡，阴影柔和，画面干净，只保留一个核心气泡，不做多气泡堆叠。",
}
COVER_TEMPLATE_FONTS = {
    "A": "圆润的现代无衬线黑体，加粗，字距略松，主标题稳，重点词可用暖棕色强调。",
    "B": "略带倾斜的手写体或行楷风格，保留一点不完美感，但每个字都要清晰可读。",
    "C": "圆润的现代无衬线黑体，加粗，像聊天截图里的重点句，但不要出现系统 UI 元素。",
}
COVER_TEMPLATE_ELEMENTS = {
    "A": "背景只保留暖调渐变、纸张肌理和轻微投影，不放人物、产品、门店陈列。",
    "B": "画面围绕一张奶油色便签纸展开，可有轻微胶带或阴影质感，但不要做复杂贴纸拼贴。",
    "C": "画面只保留单个对话气泡和柔和背景，避免头像、时间戳、消息列表等界面元素。",
}
CHUNSHE_PUBLISH_TIME_SLOTS = (
    "工作日 12:00-13:00",
    "工作日 18:00-20:00",
    "周末 10:00-11:00",
    "周末 20:00-22:00",
)
CHUNSHE_PUBLISH_TAGS = (
    "#吴江美容院",
    "#吴江做脸",
    "#面部清洁",
    "#美容院推荐",
    "#做脸",
    "#护肤",
    "#素颜",
    "#皮肤管理",
    "#吴江",
    "#椿舍",
)
CHUNSHE_COVER_STRUCTURE_NOTES = (
    "单张封面：是",
    "小红书结构：3:4 竖版、手机端高可读、留白 40-50%",
    "标题策略：直接复用成稿主标题，不额外生成第二套封面标题",
    "排除项：不做多页轮播、不做运营页、不放门店信息和价格",
)

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
    "\u5457",
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
    "没着急走",
    "又坐了",
    "活儿都是一样",
    "不想走",
    "没有中途",
    "每个月都来",
    "不用做心理准备",
    "心理准备",
    "安安稳稳",
    "空着手进门",
    "第二次还敢来",
    "主动问",
    "没更差",
    "没有更红",
    "正常见人",
    "不看手机",
    "走路都慢了",
)

RAW_REVIEW_FORCE_TERMS = ("推销", "办卡", "敷衍", "白跑", "约不上", "补差价", "套路", "忽悠", "销售感")
FUGUI_FACT_MARKERS = (
    "上周",
    "前两天",
    "前几天",
    "刚躺下",
    "一进门",
    "做到一半",
    "照了照镜子",
    "手机",
    "肩膀",
    "热毛巾",
    "清黑头",
    "做完",
)
FUGUI_FEELING_MARKERS = (
    "怕",
    "紧",
    "犹豫",
    "委屈",
    "烦",
    "累",
    "绷",
    "提着",
    "堵着",
    "不想讲话",
    "难受",
    "发愁",
)
FUGUI_IMAGINATION_MARKERS = tuple(
    dict.fromkeys(
        [
            *LONGING_MARKERS,
            "下次还来",
            "下次还敢来",
            "终于敢来",
            "躺得住",
            "终于松下来",
            "不用想等会儿怎么拒绝",
            "今天人轻了一点",
        ]
    )
)
EXPLANATORY_LINE_MARKERS = (
    "很多人问",
    "很多人不是",
    "其实是在问",
    "找店我一般就看",
    "本地找店，最怕的不是",
    "做脸想让人觉得",
    "先看你敢不敢",
    "先看她敢不敢",
)
ACTION_DIALOGUE_MARKERS = (
    "她说",
    "我说",
    "她问",
    "我问",
    "我跟她说",
    "一进门",
    "刚躺下",
    "做到一半",
    "照了照镜子",
    "照着镜子",
    "我把",
    "她把",
    "我先看了看",
    "肩膀",
    "呼吸",
    "闭住",
)
CUSTOMER_QUOTE_MARKERS = (
    "她说",
    "她问",
    "顾客说",
    "顾客问",
    "跟我们美容师说",
    "跟美容师说",
    "问我们美容师",
    "问美容师",
    "第一句",
    "可以吗",
    "能不能",
    "不想讲话",
    "今天不会",
)
BEAUTICIAN_ACTION_MARKERS = (
    "美容师",
    "水温",
    "力度",
    "毛巾",
    "热一点",
    "轻一点",
    "灯光",
    "调暗",
    "毯子",
    "空调",
    "有点凉",
    "拉了拉",
    "放到旁边",
    "手机慢慢放",
    "肩膀也下来了",
)
OWNER_DIRECT_SERVICE_MARKERS = (
    "我给她敷",
    "我给她盖",
    "我给她调",
    "我给她做",
    "我给她按",
    "我给她清",
    "我给她拿",
    "我帮她敷",
    "我帮她盖",
    "我帮她调",
    "我帮她做",
    "我帮她按",
    "我帮她清",
)
ENDING_LOW_PRESSURE_MARKERS = (
    "就行",
    "就够了",
    "没白跑",
    "别堵着",
    "别觉得",
    "不用做心理准备",
    "这趟就值了",
    "这一趟就值了",
    "就挺好",
    "这样就挺好",
    "安安静静躺完",
    "安静一会儿",
    "下次不用先做心理准备",
    "轻了一点",
    "人轻了一点",
)
ABSTRACT_JUDGMENT_MARKERS = (
    "很多人",
    "我一直觉得",
    "真正让人",
    "她更想要的是",
    "她想要的不是",
    "其实是在问",
    "说到底",
    "本质",
    "真正",
)

REPEATED_ROLE_TERMS = ("美容师", "店员", "顾客")
SOFTENABLE_ROLE_PREFIXES = ("美容师", "店员")


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _split_lines(text: str) -> list[str]:
    return [str(line or "").strip() for line in str(text or "").splitlines() if str(line or "").strip()]


def _split_overloaded_chunshe_line(text: str) -> list[str]:
    line = str(text or "").strip()
    if not line:
        return []
    punctuation_count = sum(line.count(marker) for marker in ("，", "。", "；", "："))
    if punctuation_count < 3 or len(line) <= 28:
        return [line]
    clauses = [segment.strip() for segment in re.findall(r".*?[，。；：]|.+$", line) if segment and segment.strip()]
    if len(clauses) < 2:
        return [line]
    midpoint = max(1, len(clauses) // 2)
    left = "".join(clauses[:midpoint]).strip()
    right = "".join(clauses[midpoint:]).strip()
    if not left or not right:
        return [line]
    return [left, right]


def _soften_repeated_role_prefix(text: str, previous_line: str) -> str:
    line = str(text or "").strip()
    prev = str(previous_line or "").strip()
    if not line or not prev:
        return line
    for term in SOFTENABLE_ROLE_PREFIXES:
        if term in prev and line.startswith(term):
            softened = line[len(term) :].lstrip("，,:： ").strip()
            if softened:
                return softened
    return line


def _build_chunshe_retry_fix_block(retry_issues: list[str] | None) -> str:
    issues = [str(item or "").strip() for item in retry_issues or [] if str(item or "").strip()]
    if not issues:
        return ""
    fix_lines: list[str] = []
    if any("金句" in item for item in issues):
        fix_lines.append("- 如果金句还停在标题层，就在中后段补 1 句老板人话判断，优先改写 translation_line / boss_judgment_line / store_rule_line，不要照抄原句。")
    if any("称谓重复" in item for item in issues):
        fix_lines.append("- 同一个称谓最多保留 2 次，后面的“美容师/店员”优先省主语或改成“她”，不要一行一句都重复。")
    if any("某一行承载过多信息" in item for item in issues):
        fix_lines.append("- 一行里如果同时塞了两个动作再加一个判断，就拆成两行；逗号太多就直接断开。")
    if any("向往感" in item or "向往画面" in item for item in issues):
        fix_lines.append("- 结尾前补一个看得见的松弛画面，再给一个低压后效，比如手机放下、肩膀松开、今天人轻一点、下次不用先做心理准备。")
    if not fix_lines:
        return ""
    return "【这轮重点修】\n" + "\n".join(fix_lines) + "\n\n"


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
        "source_type",
        "entry_class",
        "angle_type",
        "scene_trigger",
        "fear",
        "real_desire",
        "real_buy_point",
        "store_rule_hint",
        "quote_seed_text",
        "quote_seed_theme",
        "quote_seed_usage",
        "quote_seed_file",
        "title_preserve_core_feel",
        "quote_direct_use_rule",
        "benchmark_usage_rule",
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
        "review_phrase_bucket",
        "review_raw_opening",
        "review_raw_scene",
        "review_raw_landing",
    )
    return {key: topic.get(key, "") for key in allowed_keys}


def _build_chunshe_prompt_source_pack_view(source_pack: dict[str, Any]) -> dict[str, Any]:
    business_facts = dict(source_pack.get("business_facts") or {})
    hard_rules = dict(source_pack.get("hard_rules") or {})
    brief_extract = dict(source_pack.get("brief_extract") or {})
    topic_helpers = dict(source_pack.get("topic_helpers") or {})
    review_language = dict(source_pack.get("review_language") or {})
    theme_language = dict(source_pack.get("theme_language") or {})
    fugui_logic = dict(source_pack.get("fugui_logic") or {})
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
            "topic_source_order": topic_helpers.get("topic_source_order", []),
            "quote_roles": topic_helpers.get("quote_roles", {}),
            "benchmark_rule": topic_helpers.get("benchmark_rule", ""),
        },
        "review_language": {
            "principle": review_language.get("principle", ""),
            "bucket": review_language.get("bucket", ""),
            "opening_examples": review_language.get("opening_examples", []),
            "scene_examples": review_language.get("scene_examples", []),
            "landing_examples": review_language.get("landing_examples", []),
            "raw_word_policy": review_language.get("raw_word_policy", ""),
            "must_keep_terms": review_language.get("must_keep_terms", []),
        },
        "theme_language": {
            "principle": theme_language.get("principle", ""),
            "theme_examples": theme_language.get("theme_examples", []),
            "translation_examples": theme_language.get("translation_examples", []),
            "boss_judgment_examples": theme_language.get("boss_judgment_examples", []),
            "low_pressure_offer_examples": theme_language.get("low_pressure_offer_examples", []),
        },
        "fugui_logic": {
            "core_principles": fugui_logic.get("core_principles", []),
            "single_story_ratio": fugui_logic.get("single_story_ratio", ""),
            "three_layers": fugui_logic.get("three_layers", {}),
            "bad_smells": fugui_logic.get("bad_smells", []),
            "gold_sentence_rule": fugui_logic.get("gold_sentence_rule", ""),
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
        "你是椿舍口播主链的 draft_package 阶段。\n"
        "目标：先产出口播节拍，再写一版能直接录的正文，不要写成说明文。\n"
        "角色设定：这是男老板口吻。老板负责定规矩、转述观察、收一句判断；美容师负责水温、力度、灯光、毛巾、毯子、空调和动作观察等服务细节。\n"
        "只输出一个 JSON 对象，不要解释，不要输出 markdown 分节标题。\n"
        "JSON schema:\n"
        "{\n"
        '  "title_candidates": ["string", "string", "string"],\n'
        '  "hook_line": "string",\n'
        '  "customer_quote": "string",\n'
        '  "beautician_actions": ["string", "string"],\n'
        '  "emotion_shift": "string",\n'
        '  "boss_rule_line": "string",\n'
        '  "boss_judgment_line": "string",\n'
        '  "ending_line": "string",\n'
        '  "body_markdown": "string",\n'
        '  "draft_body": "string"\n'
        "}\n\n"
        "# 口播骨架\n\n"
        "1. 前 3 句必须完成：具体人 + 具体场景或原话 + 情绪钩子。\n"
        "2. 前 6 句内必须出现顾客原话，最好直接用引号或冒号带出。\n"
        "3. 正文默认只走过程描述推进，不走观点论证推进，不要先解释‘很多人为什么会搜这个词’。\n"
        "4. 中段必须至少写 2 个美容师服务动作，动作要能拍出来。\n"
        "5. 正文只保留 1 条清晰情绪链，例如：警惕 -> 松下来，或 累 -> 安静。\n"
        "6. 老板最多只做两件事：定一条规矩，收一句判断。不要写成老板亲自给顾客敷毛巾、调力度、做服务。\n"
        "7. 结尾只能是低压收口或顾客的轻结果，不要总结升华，不要正文 CTA。\n\n"
        "# 硬约束\n\n"
        "1. 差评原话里如果已经有推销、办卡、敷衍、白跑、约不上、套路这类硬词，正文优先直接保留，不要艺术处理。\n"
        "2. 富贵方法的落法：正文至少自然体现两层，事实/动作是一层，感受或想象至少再有一层；不要把老师式总结句硬塞进正文。\n"
        "3. 金句显性表达最多 1 句，只能放在中段转折或结尾；如果翻成老板口语后仍生硬，就不要进正文。\n"
        "4. 如果 source_type=quote_topic_seed，至少 1 个标题保留 quote_seed_text 的核心句感，但正文不能机械照抄原句。\n"
        "5. 对标链接只学结构、节奏、转折，不借原句。\n"
        '6. 禁止“不是A，是B”“我是开美容院的”“评论区/私信/预约/到店”这类正文表达。\n'
        "7. 抽象判断句最多 1 句，且不能放在开头。\n\n"
        "# 正文要求\n\n"
        f"- 输出类型：{output_type}，目标 {spec['char_range']}，{spec['line_range']}，适合 {spec['seconds']} 直接录。\n"
        "- 正文按短句分行，每行只讲一个意思。\n"
        "- 单行尽量控制在 12-24 个字，超过 26 个字就主动拆行。\n"
        "- 同一个称谓全篇尽量不超过 2 次；写过一次“美容师”后，后面能省主语就省，不要每行都重复。\n"
        "- 正文里至少自然落 1 句主题翻译，优先改写 translation_line / boss_judgment_line / store_rule_line，但要像老板聊天，不像老师总结。\n"
        "- 结尾前至少给 1 个向往画面，优先写手机放下、肩膀下来了、脑子没那么吵、今天人轻一点、下次不用先做心理准备这类可拍细节。\n"
        "- 优先把 topic 里的 first_conflict_line、scene_line、boss_response、longing_moment、low_bar_ending 当候选素材，而不是模块拼装清单。\n"
        "- 写成真人口播，像老板坐在店里聊天，不像分析报告。\n\n"
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
    retry_fix_block = _build_chunshe_retry_fix_block(retry_issues)
    quote_block = "【候选结尾收口句】\n暂无\n\n"
    if quote_candidate:
        quote_block = f"【候选结尾收口句】\n{json.dumps(quote_candidate, ensure_ascii=False, indent=2)}\n\n"
    return (
        "你是椿舍口播主链的 polish_package 阶段。\n"
        "目标：把当前稿子修成能直接录的口播，不要修成说明文。\n"
        "只输出一个 JSON 对象，不要解释。\n"
        "JSON schema:\n"
        "{\n"
        '  "passed": true,\n'
        '  "issues": ["string"],\n'
        '  "removed_or_softened_claims": ["string"],\n'
        '  "final_markdown": "string"\n'
        "}\n\n"
        "# 必查门槛\n\n"
        "1. 开头不能像文章说明，前 3 句必须是具体情境，不要先解释背景。\n"
        "2. 前 6 句必须出现顾客原话。\n"
        "3. 中段必须至少有 2 个美容师动作，不够就重写过程。\n"
        "4. 男老板不能亲自服务顾客。出现‘我给她敷毛巾 / 我调力度 / 我给她做项目’这类句子就视为失败。\n"
        "5. 差评原词如果命中，优先保留‘推销 / 办卡 / 敷衍 / 白跑 / 约不上 / 套路’这类词。\n"
        "6. 抽象判断句最多 1 句，而且不能放在开头。\n"
        "7. 结尾必须是低压收口，不是总结升华，也不是正文 CTA。\n"
        "8. 金句显性表达最多 1 句；如果像老师上课，就删掉或降成隐性主题。\n\n"
        "# 修改原则\n\n"
        "- 只修标题和正文，不扩写运营信息。\n"
        "- 文艺句改成人话，解释句改成动作和对话。\n"
        "- 单行太长就拆，保证每行只装一个动作、一个情绪或者一个判断。\n"
        "- 同一个称谓不要连着重复出现；写过一次“美容师”后，后面优先省主语或换成“她”。\n"
        "- 如果标题用了金句句感，正文里要自然落 1 句翻译后的老板判断，但不要硬搬原句。\n"
        "- 向往感不靠大词，靠看得见的轻结果：手机放下、肩膀松开、脑子没那么吵、今天人轻一点、下次不用先做心理准备。\n"
        "- 发现缺关键节拍时，直接重写对应段落，不要靠补一句判断敷衍过去。\n"
        "- quote_topic_seed 题目只保留标题句感和一处自然转折，不允许把原句硬贴进正文。\n"
        "- issues 只写仍然存在的问题；passed=true 的前提是整篇删掉标题后也像真人口播。\n\n"
        f"【角色】\n{role}\n"
        f"【输出类型】\n{output_type}\n"
        f"【运行模式】\n{mode}\n\n"
        f"【Source Pack】\n{json.dumps(source_pack_view, ensure_ascii=False, indent=2)}\n\n"
        f"【脚本骨架】\n{json.dumps(topic_view, ensure_ascii=False, indent=2)}\n\n"
        f"{quote_block}{retry_block}{retry_fix_block}"
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
        if stripped in {"# 置顶评论", "## 置顶评论", "# 回复模板", "## 回复模板"}:
            current = None
            continue
        if current:
            sections[current].append(line)

    title_section_lines = [str(item or "").strip() for item in sections["title"] if str(item or "").strip()]
    body_section_lines = [str(item or "").strip() for item in sections["body"] if str(item or "").strip()]
    raw_non_empty_lines = [str(item or "").strip() for item in str(markdown_text or "").splitlines() if str(item or "").strip()]

    # Some polish responses still come back as:
    #   # 标题
    #   主标题
    #
    #   正文...
    # i.e. they omit "# 正文" and accidentally place body lines under the title section.
    if title_section_lines and not body_section_lines and len(title_section_lines) > 1:
        sections["title"] = [title_section_lines[0]]
        sections["body"] = title_section_lines[1:]
    elif not title_section_lines and raw_non_empty_lines:
        sections["title"] = [raw_non_empty_lines[0]]
        sections["body"] = raw_non_empty_lines[1:]

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
    ]
    return "\n".join(parts).strip() + "\n"


def render_chunshe_video_markdown(
    *,
    title_candidates: list[str],
    body_text: str,
    pinned_comment: str = "",
    reply_template: str = "",
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
            ]
        ),
        title_candidates,
    )


def extract_chunshe_video_title(markdown_text: str) -> str:
    title_block = _extract_section(markdown_text, ("# 标题", "## 标题"))
    for line in _split_lines(title_block):
        text = str(line or "").lstrip("- ").strip()
        if text:
            return text
    return ""


def _select_cover_template(title: str) -> str:
    text = str(title or "").strip()
    if any(keyword in text for keyword in COVER_HANDWRITE_KEYWORDS):
        return "B"
    if any(keyword in text for keyword in COVER_DIALOGUE_KEYWORDS):
        return "C"
    return "A"


def _find_highlight_word(title: str) -> str:
    text = str(title or "").strip()
    for word in COVER_HIGHLIGHT_CANDIDATES:
        if word in text:
            return word
    return "无"


def generate_cover_prompt(title: str) -> dict[str, str]:
    title_text = str(title or "").strip()
    if not title_text:
        raise ValueError("cover title is empty")
    template = _select_cover_template(title_text)
    highlight = _find_highlight_word(title_text)
    highlight_line = (
        "【标题高亮】不额外指定单词高亮，保持整句完整和居中排版。"
        if highlight == "无"
        else f"【标题高亮】重点高亮「{highlight}」，使用暖棕色强调，但不要破坏整句可读性。"
    )
    prompt = "\n".join(
        [
            "画幅比例3:4竖版。",
            "【图片类型】小红书封面",
            f"【封面文案】「{title_text}」",
            f"【版式】{COVER_TEMPLATE_LAYOUTS[template]}",
            f"【视觉风格】{COVER_TEMPLATE_VISUALS[template]}",
            f"【中文字体描述】{COVER_TEMPLATE_FONTS[template]}",
            highlight_line,
            f"【画面元素】{COVER_TEMPLATE_ELEMENTS[template]}",
            "【结构约束】只做小红书单张封面，保持单页表达，不放门店信息和价格。",
        ]
    )
    return {
        "template": template,
        "template_label": COVER_TEMPLATE_LABELS[template],
        "prompt": prompt,
        "highlight": highlight,
        "negative_prompt": "、".join(COVER_NEGATIVE_PROMPTS),
    }


def generate_cover_prompts_batch(topics: list[dict[str, Any]]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for item in topics or []:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        payload = generate_cover_prompt(title)
        payload["title"] = title
        payload["topic_id"] = str(item.get("topic_id") or "").strip()
        results.append(payload)
    return results


def render_chunshe_cover_sidecar(title: str, cover_result: dict[str, Any]) -> str:
    title_text = str(title or "").strip()
    if not title_text:
        raise ValueError("cover sidecar title is empty")
    template = str(cover_result.get("template_label") or cover_result.get("template") or "").strip() or "A 暖调文字海报"
    highlight = str(cover_result.get("highlight") or "").strip() or "无"
    prompt = str(cover_result.get("prompt") or "").strip()
    negative_prompt = str(cover_result.get("negative_prompt") or "").strip()
    parts = [
        "# 封面提示词",
        "",
        "## 基础信息",
        f"- 主标题：{title_text}",
        f"- 模板：{template}",
        f"- 标题高亮词：{highlight}",
        "",
        "## 中文提示词",
        "```text",
        prompt,
        "```",
        "",
        "## 负面提示词",
        "```text",
        negative_prompt,
        "```",
        "",
        "## 结构说明",
    ]
    parts.extend(f"- {note}" for note in CHUNSHE_COVER_STRUCTURE_NOTES)
    return "\n".join(parts).strip() + "\n"


def render_chunshe_publish_pack(date_str: str, role: str, topic_items: list[dict[str, Any]]) -> str:
    compact_date = str(date_str or "").replace("-", "")
    rows = [
        "| # | 标题 | 主文件 | 封面文件 | 模板 | 高亮 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(topic_items or [], start=1):
        rows.append(
            "| {index} | {title} | {path} | {cover_path} | {template} | {highlight} |".format(
                index=index,
                title=str(item.get("title") or "").replace("|", "｜"),
                path=str(item.get("path") or "").replace("|", "｜"),
                cover_path=str(item.get("cover_path") or "").replace("|", "｜"),
                template=str(item.get("cover_template") or "").replace("|", "｜"),
                highlight=str(item.get("cover_highlight") or "无").replace("|", "｜"),
            )
        )
    parts = [
        "# 椿舍发布包",
        "",
        "## 批次信息",
        f"- 日期：{date_str}",
        f"- 日期标记：{compact_date}",
        f"- 角色：{role}",
        f"- 篇数：{len(topic_items or [])}",
        "",
        "## 文件清单",
        *rows,
        "",
        "## 推荐发布时间",
    ]
    parts.extend(f"- {slot}" for slot in CHUNSHE_PUBLISH_TIME_SLOTS)
    parts.extend(
        [
            "",
            "## 推荐标签",
            " ".join(CHUNSHE_PUBLISH_TAGS),
            "",
            "## 图片生成步骤",
            "1. 打开 Gemini 或 Nano Banana Pro。",
            "2. 复制对应 `.cover.md` 里的中文提示词和负面提示词。",
            "3. 生成 1080×1440 的 3:4 竖版封面图。",
            "4. 检查标题是否清晰、暖调是否稳定、是否出现英文乱码或水印。",
            "5. 确认封面只保留单张结构，不带门店信息、价格和 CTA。",
        ]
    )
    return "\n".join(parts).strip() + "\n"


def extract_chunshe_video_draft_body(payload: dict[str, Any]) -> str:
    for key in ("body_markdown", "draft_body", "draft_markdown"):
        candidate = str(payload.get(key) or "").strip()
        if not candidate:
            continue
        return _extract_section(candidate, ("# 正文", "## 正文", "# 口播正文", "## 口播正文")) or candidate
    return ""


def ensure_chunshe_video_core_lines(
    body_text: str,
    topic: dict[str, Any] | None,
    *,
    output_type: str = "精简发布版",
) -> str:
    lines: list[str] = []
    for raw_line in _split_lines(body_text):
        lines.extend(_split_overloaded_chunshe_line(raw_line))
    cleaned: list[str] = []
    seen: set[str] = set()
    for line in lines:
        text = _soften_repeated_role_prefix(str(line or "").strip(), cleaned[-1] if cleaned else "")
        if not text:
            continue
        key = _normalize_key(text)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        cleaned.append(text)
    max_lines = 14 if output_type == "精简发布版" else 18
    if len(cleaned) > max_lines:
        cleaned = cleaned[:max_lines]
    return "\n".join(cleaned).strip()


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
    target_key = _normalize_key(target)
    if not target_key:
        return False
    target_grams = _char_bigram_set(target)
    for line in _split_lines(body):
        line_key = _normalize_key(line)
        if not line_key:
            continue
        if target_key in line_key or line_key in target_key:
            return True
        if target_grams:
            line_grams = _char_bigram_set(line)
            if line_grams:
                overlap = len(target_grams & line_grams)
                if (overlap / max(1, len(target_grams))) >= 0.6:
                    return True
        if _is_semantically_redundant(target, [line]):
            return True
    tokens = [token for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", target) if len(token) >= 2]
    if not tokens:
        return False
    hits = sum(1 for token in tokens if token in body)
    return hits >= min(2, len(tokens))


def _topic_force_terms(topic: dict[str, Any] | None) -> list[str]:
    if not topic:
        return []
    raw_text = "\n".join(
        [
            str(topic.get("review_raw_opening") or "").strip(),
            str(topic.get("review_raw_scene") or "").strip(),
            str(topic.get("review_raw_landing") or "").strip(),
            str(topic.get("first_conflict_line") or "").strip(),
            str(topic.get("scene_line") or "").strip(),
        ]
    )
    return [term for term in RAW_REVIEW_FORCE_TERMS if term in raw_text]


def _topic_theme_lines(topic: dict[str, Any] | None) -> list[str]:
    if not topic:
        return []
    return [
        str(topic.get("translation_line") or "").strip(),
        str(topic.get("boss_judgment_line") or "").strip(),
        str(topic.get("store_rule_line") or "").strip(),
        str(topic.get("pivot_line") or "").strip(),
        str(topic.get("theme_line") or "").strip(),
        str(topic.get("low_pressure_offer_line") or "").strip(),
        str(topic.get("landing_line") or "").strip(),
    ]


def _body_has_any_marker(body_text: str, markers: tuple[str, ...]) -> bool:
    body = str(body_text or "")
    return any(marker in body for marker in markers)


def _body_has_theme_translation(body_text: str, topic: dict[str, Any] | None) -> bool:
    body = str(body_text or "").strip()
    if not body or not topic:
        return False
    for candidate in _topic_theme_lines(topic):
        if candidate and _body_contains_target_line(body, candidate):
            return True
    quote_seed_text = str(topic.get("quote_seed_text") or "").strip()
    if not quote_seed_text:
        return False
    quote_tokens = [token for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", quote_seed_text) if len(token) >= 2]
    if not quote_tokens:
        return False
    hits = sum(1 for token in quote_tokens if token in body)
    return hits >= min(2, len(quote_tokens))


def validate_chunshe_video_markdown(
    markdown_text: str,
    body_cta_blacklist: tuple[str, ...],
    topic: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    """Return ``(issues, advisories)``.

    *issues* are hard problems that should trigger retry or block publication.
    *advisories* are style notes for human review and never trigger retry.
    """
    text = str(markdown_text or "").strip()
    issues: list[str] = []
    advisories: list[str] = []

    required_sections = ("# 标题", "## 备选标题", "# 正文")
    for section in required_sections:
        if section not in text:
            issues.append(f"缺少区块：{section}")

    title = _extract_section(text, ("# 标题", "## 标题"))
    body = _extract_section(text, ("# 正文", "## 正文", "# 口播正文", "## 口播正文"))
    if not body:
        issues.append("# 正文 为空")
        return issues, advisories

    body_lines = _split_lines(body)
    if len(body_lines) < 6 or len(body_lines) > 18:
        issues.append("正文行数不在口播区间")
    if "\n" not in body and len(body) >= 80:
        issues.append("正文仍像长段落")

    first_three = body_lines[:3]
    first_six = body_lines[:6]
    joined_first_three = "".join(first_three)
    joined_first_six = "".join(first_six)
    title_and_body = f"{title}\n{body}"

    if any(marker in title_and_body for marker in ("有用吗？有用，但", "值不值，先看")):
        issues.append("仍有问答模板味")
    if any(marker in body for marker in ("我是开美容院的", "我店里有条规矩")):
        issues.append("仍有身份开头")
    if any(marker in joined_first_three for marker in ARTICLE_OPENING_MARKERS):
        issues.append("前 3 句仍像文章说明")
    if any(marker in joined_first_three for marker in ("很多人", "其实是在问", "表面上", "本质", "真正")):
        issues.append("前 3 句仍像文章说明")
    if any(line.startswith(marker) for marker in ROLE_NAME_MARKERS for line in first_three):
        issues.append("仍有角色名开头")

    customer_quote_hit = any(marker in joined_first_six for marker in CUSTOMER_QUOTE_MARKERS)
    if not customer_quote_hit:
        for line in first_six:
            if "：“" in line or "：\"" in line:
                if "我说" not in line and "我问" not in line:
                    customer_quote_hit = True
                    break
    if not customer_quote_hit:
        issues.append("前 6 句缺少顾客原话")

    beautician_action_count = sum(1 for line in body_lines if any(marker in line for marker in BEAUTICIAN_ACTION_MARKERS))
    if beautician_action_count < 2:
        issues.append("中段服务动作少于 2 个")

    if any(marker in body for marker in OWNER_DIRECT_SERVICE_MARKERS):
        issues.append("男老板视角写成老板亲自服务")

    if any(token in body for token in body_cta_blacklist):
        hit = next(token for token in body_cta_blacklist if token in body)
        issues.append(f"正文出现 CTA 黑名单：{hit}")

    required_force_terms = _topic_force_terms(topic)
    if required_force_terms and not any(term in body for term in required_force_terms):
        issues.append("差评原词被磨平")

    abstract_judgment_count = sum(1 for line in body_lines if any(marker in line for marker in ABSTRACT_JUDGMENT_MARKERS))
    if abstract_judgment_count > 1:
        issues.append("抽象判断句超过 1 句")

    if not _body_has_any_marker(body, FUGUI_FACT_MARKERS):
        issues.append("缺少事实/动作层")
    if not (_body_has_any_marker(body, FUGUI_FEELING_MARKERS) or _body_has_any_marker(body, FUGUI_IMAGINATION_MARKERS)):
        issues.append("缺少感受或想象层")

    if any(marker in body for marker in EXPLANATORY_BRIDGE_MARKERS):
        issues.append("中段仍有解释式过桥")
    if any(marker in body for marker in BODY_UNSAFE_MARKERS):
        issues.append("正文仍有主题句直塞")
    if any(marker in body for marker in TEACHER_TONE_MARKERS):
        issues.append("正文仍有老师口气")
    literary_hit_count = sum(1 for marker in LITERARY_MARKERS if marker in body)
    if literary_hit_count >= 2 or any(marker in joined_first_three for marker in LITERARY_MARKERS):
        issues.append("正文有点文艺，不够直白")

    ending_window = "\n".join(body_lines[-2:]) if body_lines else ""
    ending_low_pressure_hit = any(marker in ending_window for marker in ENDING_LOW_PRESSURE_MARKERS)
    if not ending_low_pressure_hit and re.search(r"(?:人)?轻.{0,1}一点", ending_window):
        ending_low_pressure_hit = True
    if not ending_low_pressure_hit:
        issues.append("结尾不是低压收口")

    source_type = str((topic or {}).get("source_type") or "").strip()
    if source_type == "quote_topic_seed":
        quote_seed_text = str((topic or {}).get("quote_seed_text") or "").strip()
        if quote_seed_text and _normalize_key(quote_seed_text) and _normalize_key(quote_seed_text) in _normalize_key(body):
            issues.append("正文机械照抄金句原句")
        if not _body_has_theme_translation(body, topic):
            issues.append("金句判断没落进正文")
    elif topic and not _body_has_theme_translation(body, topic):
        advisories.append("金句还停在标题层")

    repeated_role_hit = any(body.count(term) >= 3 for term in REPEATED_ROLE_TERMS)
    if not repeated_role_hit:
        for previous, current in zip(body_lines, body_lines[1:]):
            if any(previous.startswith(term) and current.startswith(term) for term in SOFTENABLE_ROLE_PREFIXES):
                repeated_role_hit = True
                break
    if repeated_role_hit:
        advisories.append("称谓重复，读起来有点吵")
    if any(sum(part in line for part in ("，", "。", "；", "：")) >= 3 and len(line) >= 28 for line in body_lines):
        advisories.append("某一行承载过多信息")
    not_a_is_b_count = len(re.findall(r"不是.{0,12}?[，,、 ]?.{0,4}?是", body))
    if not_a_is_b_count >= 1:
        advisories.append(f'“不是A，是B”用了{not_a_is_b_count}次')
    if not _body_has_any_marker(body, FUGUI_IMAGINATION_MARKERS):
        advisories.append("向往感还可以更强")
    elif not any(marker in body for marker in LONGING_MARKERS):
        advisories.append("向往画面还可以更强")

    deduped_issues: list[str] = []
    seen_issues: set[str] = set()
    for item in issues:
        key = _normalize_key(item)
        if not key or key in seen_issues:
            continue
        seen_issues.add(key)
        deduped_issues.append(item)

    deduped_advisories: list[str] = []
    seen_advisories: set[str] = set()
    for item in advisories:
        key = _normalize_key(item)
        if not key or key in seen_advisories or any(_normalize_key(issue) == key for issue in deduped_issues):
            continue
        seen_advisories.add(key)
        deduped_advisories.append(item)

    return deduped_issues, deduped_advisories
