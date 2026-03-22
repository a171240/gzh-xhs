#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared topic and quote helpers for the chunshe staged pipeline."""

from __future__ import annotations

import datetime as dt
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from quote_ingest_core import load_existing_quotes

REPO_ROOT = Path(__file__).resolve().parents[2]
CHUNSHE_TOPIC_POOL_PATH = REPO_ROOT / "skills" / "客户交付" / "椿舍门店专用" / "assets" / "专用选题池.json"
CHUNSHE_REVIEW_PHRASE_BANK_PATH = REPO_ROOT / "skills" / "客户交付" / "椿舍门店专用" / "assets" / "差评原话词库.json"
CHUNSHE_THEME_BANK_PATH = REPO_ROOT / "skills" / "客户交付" / "椿舍门店专用" / "assets" / "主题句库.json"
CHUNSHE_THEME_BANK_PRIORITY_PATH = REPO_ROOT / "skills" / "客户交付" / "椿舍门店专用" / "assets" / "高优先主题句库.json"
CHUNSHE_OWNER_SPOKEN_BANK_PATH = REPO_ROOT / "skills" / "客户交付" / "椿舍门店专用" / "assets" / "老板口语素材库.json"
CHUNSHE_REPORT_ROOT = REPO_ROOT / "reports" / "chunshe-generation"
XHS_OUTPUT_ROOT = REPO_ROOT / "02-内容生产" / "小红书" / "生成内容"

ENTRY_CLASSES = ("问题修复", "信任怀疑", "放松养护", "本地找店")
ANGLE_TYPES = ("痛点确认", "防御拆解", "规矩托底", "向往画面", "本地决策", "关系/身份翻译")
ROLE_MAP = {
    "李可": "李可",
    "luke": "李可",
    "孟欣": "孟欣",
    "mengxin": "孟欣",
    "伍霖": "伍霖",
    "wulin": "伍霖",
}
OUTPUT_TYPE_MAP = {
    "精简发布版": "精简发布版",
    "精简版": "精简发布版",
    "发布版": "精简发布版",
    "解释型口播版": "解释型口播版",
    "口播版": "解释型口播版",
    "标题池": "标题池",
    "titlepool": "标题池",
}
STORE_RULES = (
    "说不要，话题就停",
    "不强推",
    "不乱加项",
    "按次可做",
    "不适合直接说",
    "流程、时长、合理预期说清楚",
)
ENTRY_CLASS_KEYWORDS = {
    "放松养护": ("spa", "按摩", "肩颈", "放松", "解压", "舒缓", "松一松", "松下来", "不想讲话", "别跟我讲话", "先别跟我讲话", "安静一会儿", "安静会儿"),
    "本地找店": ("吴江", "附近", "推荐", "哪家", "去哪家", "到店", "门店", "江兴西路", "吴江公园"),
    "信任怀疑": ("有用吗", "靠谱吗", "值不值", "套路", "推销", "退卡", "退费", "会不会", "怎么选", "避坑", "不扫兴", "情绪价值"),
}
HIGH_BOUNDARY_TERMS = (
    "点痣",
    "祛斑",
    "产后",
    "盆底",
    "艾灸",
    "头疗",
    "水光针",
    "热玛吉",
    "超声炮",
    "玻尿酸",
    "肉毒",
    "光子嫩肤",
    "医美",
)
QUOTE_THEME_PRIORITY = {
    "问题修复": ("系统与执行", "人性与沟通", "自我与哲学（次级）"),
    "信任怀疑": ("人性与沟通", "系统与执行", "自我与哲学（次级）"),
    "放松养护": ("自我与哲学（次级）", "人性与沟通", "系统与执行"),
    "本地找店": ("人性与沟通", "系统与执行", "自我与哲学（次级）"),
}
QUOTE_BLOCK_KEYWORDS = (
    "创业",
    "公司",
    "CEO",
    "产品",
    "流量",
    "账号",
    "智能体",
    "AI",
    "程序",
    "特朗普",
    "pdf",
    "打工",
    "企业",
    "老板微信",
    "公众号",
    "抖音",
    "提示词",
    "投资",
    "密保",
)
QUOTE_TOPIC_SEED_BLOCK_MARKERS = (
    "对于其他人，只做筛选，不去教育",
    "做好当下该做的事情",
    "改变的初期总是不太舒服的",
    "研究人性，不是为了给人使绊子",
    "所有的细节都是为了专属感",
    "通过服务给顾客带来满意效果",
    "你能感觉到他不图你任何的东西",
    "他不会掠夺你",
)
QUOTE_TOPIC_SEED_HINTS = {
    "问题修复": ("稳住", "别乱", "别猛", "少折腾", "简单", "反复"),
    "信任怀疑": ("信任", "边界", "分寸", "扫兴", "停", "安心", "放松", "被照顾"),
    "放松养护": ("放松", "轻", "负担", "松", "缓一口气", "不扫兴"),
    "本地找店": ("信任", "分寸", "边界", "白跑", "值不值得", "停"),
}
QUOTE_TOPIC_ANGLE_BY_ENTRY = {
    "问题修复": "规矩托底",
    "信任怀疑": "防御拆解",
    "放松养护": "向往画面",
    "本地找店": "本地决策",
}
QUOTE_TOPIC_SCENE_BY_ENTRY = {
    "问题修复": "第一次做项目前，最怕这次又白折腾",
    "信任怀疑": "刚躺下就怕对面开始讲项目",
    "放松养护": "忙完一天只想安静躺一会儿",
    "本地找店": "第一次进店前怕白跑又一肚子气",
}
QUOTE_TOPIC_FEAR_BY_ENTRY = {
    "问题修复": "怕今天做完，明天脸更闹腾",
    "信任怀疑": "怕说不要以后，对面还继续讲",
    "放松养护": "怕来做护理还得继续被安排",
    "本地找店": "怕第一次进店就被推项目，白跑一趟",
}
QUOTE_TOPIC_DESIRE_BY_ENTRY = {
    "问题修复": "先做稳，不想再反复试错",
    "信任怀疑": "先把戒备放下，安心做一次",
    "放松养护": "先缓一口气，人能松下来",
    "本地找店": "先把店看明白，再决定要不要长期来",
}
QUOTE_TOPIC_BUY_POINT_BY_ENTRY = {
    "问题修复": "别越做越糟，先稳住",
    "信任怀疑": "说不要能停，边界感对",
    "放松养护": "今天别再被安排，人能轻一点",
    "本地找店": "第一次先看分寸感，再看项目",
}
QUOTE_TOPIC_RULE_BY_ENTRY = {
    "问题修复": "能温和就别做猛",
    "信任怀疑": "说不要，话题就停",
    "放松养护": "先看状态，再决定今天做到哪一步",
    "本地找店": "第一次来，先把店看明白",
}
QUOTE_TOPIC_ENDING_BY_ENTRY = {
    "问题修复": "先把这一趟做稳",
    "信任怀疑": "先让人敢躺下",
    "放松养护": "今天先让人轻一点",
    "本地找店": "第一次先别白跑",
}
CHUNSHE_ABSTRACT_MARKERS = (
    "状态不对",
    "被消耗",
    "绷起来",
    "做审判",
    "撑紧了",
    "缓一口气",
    "白跑",
)
CHUNSHE_OWNER_SPOKEN_LIBRARY = {
    "信任怀疑": {
        "translation_lines": [
            "很多人不是怕花钱，是怕花了钱还得受气。",
            "很多人后来不是不做脸了，是懒得再受一遍这种气。",
            "她不是怕做脸没用，她是怕刚躺下就开始后悔。",
            "她不是想听分析，她是想先别那么紧。",
            "很多人怕的不是项目贵，是你说不要以后对面脸色就变了。",
            "她来之前就在想，等下拒绝了会不会很尴尬。",
            "有些人不是不想做，是不想再听人追着她讲项目。",
            "她要的不是听你分析半天，是想先把这一趟做顺。",
            "有的人不是怕白花钱，是怕花了钱还得看人脸色。",
            "很多人不是怕没效果，是怕刚躺下就想走。",
            "有的人不是不舍得花钱，是不想花了钱还一肚子气。",
            "她最怕的不是做少了，是拒绝完以后气氛一下就变了。",
        ],
        "boss_judgment_lines": [
            "你要是来我这儿还得防着，那这店就白开了。",
            "我最烦的不是顾客问太多，是人还没躺下就先绷住了。",
            "你来做脸，要是全程都在防着我，那这趟就没意义。",
            "我不怕第一次做得少，我怕第一次就把人吓跑了。",
            "顾客第一次来，先让她把戒备放下，比讲一堆项目重要。",
            "你都说不要了，我还往下讲，那就不是服务，是添堵。",
            "做脸这件事，先让人敢躺下，再谈效果。",
            "会不会做项目先放一边，会不会停嘴更重要。",
            "你都已经说先不要了，我还继续讲，那就是我这边没分寸。",
            "我宁愿第一次做少一点，也不想让你做完以后更不敢来。",
            "第一次来我这儿，先让你觉得踏实，比什么都重要。",
        ],
        "low_pressure_offer_lines": [
            "第一次来，别做太多，先把这一趟安安稳稳做完。",
            "第一次来，先做基础清洁或者补水就够了。",
            "先做简单一点，先看你在这儿能不能放松。",
            "今天做什么，就只做什么，先别把项目拉太满。",
            "你要是第一次来，先做个基础项目，先看店合不合适。",
            "先把这一次做顺，后面要不要加，再慢慢说。",
            "先做最基础的，不用一上来全套都安排。",
            "你今天只想做个清洁，那就只做清洁。",
            "第一次来，先做个基础清洁或者补水，够了。",
            "第一次来不用一上来做一堆，先把这次做顺再说。",
            "第一次来先做最基础的，先看人和店你能不能接受。",
        ],
        "landing_lines": [
            "至少别还没做完，人先更累。",
            "至少这次做完，你不会觉得自己又白跑一趟。",
            "做完出来，别让人觉得又花钱又受气。",
            "做完这一趟，别让人心里更堵。",
            "这次先做顺，别把人做怕了。",
            "先让你敢来第二次，再谈效果。",
            "先让你敢躺下，后面再谈效果。",
            "这一趟做完，至少别让人下次更不敢来。",
            "你能安心躺一回，这趟就有意义。",
            "至少做完出来，不用一路还在生闷气。",
            "这次做完，至少你不会想赶紧删掉这家店。",
            "这一趟做完，至少你不会觉得自己又来错了。",
            "做完出来，心里别再堵着就行。",
        ],
    },
    "问题修复": {
        "translation_lines": [
            "很多人不是怕麻烦，是怕越做越糟。",
            "她不是想一下全弄干净，她是不想再白折腾一回。",
            "她怕的不是慢，是今天做完明天更难受。",
            "很多人不是不想做，是之前做怕了。",
            "她不是想一口气全弄好，她是怕今天做完脸更闹腾。",
            "有些人不是怕麻烦，是怕好不容易做一次，结果更糟。",
            "她要的不是猛，是别再反复。",
            "很多人问有没有用，其实是在问这次会不会又白折腾。",
        ],
        "boss_judgment_lines": [
            "我不怕做得慢一点，我怕把人做怕了。",
            "做清洁这事，能温和就别硬来。",
            "我宁可第一次少做一点，也不想把皮肤搞得更乱。",
            "做项目不是比狠，是看后面稳不稳。",
            "清洁不是看你下手多重，是看做完第二天稳不稳。",
            "我不追求一趟做狠，我更在意做完别翻车。",
            "第一次先做保守一点，后面才有得做。",
            "脸不是拿来硬清的，能稳住比一时干净更重要。",
        ],
        "low_pressure_offer_lines": [
            "第一次先做基础清洁，先看皮肤能不能稳住。",
            "先做一趟温和的，别一上来就追求一步到位。",
            "今天先把这一趟做明白，后面再说加不加。",
            "先做简单点，先看皮肤吃不吃得消。",
            "第一次先做最基础的，先看做完会不会泛红闹脾气。",
            "先做温和一点，别一上来就上强度。",
            "今天先做够用的，不用为了干净把皮肤弄得更薄。",
            "先把这一趟做稳，后面要不要加再说。",
        ],
        "landing_lines": [
            "先把皮肤稳住，再慢慢变干净。",
            "别这次做完，明天更难受。",
            "这一趟先做稳，后面才有用。",
            "别求一次做狠，能稳住才算数。",
            "先别追求一下见效，做完别更糟就已经赢了。",
            "这一趟先别翻车，后面才谈得上变好。",
            "今天先稳住，别让皮肤回去闹情绪。",
            "做完第二天还舒服，这一趟才算值。",
        ],
    },
    "放松养护": {
        "translation_lines": [
            "她来这儿，不是想听一堆道理，是想先缓一口气。",
            "很多人来做护理，不是为了变多漂亮，是想今天别再这么累。",
            "她不是来被安排的，她是来缓一口气的。",
            "很多人想要的不是项目多，是终于能松一下。",
            "她不是今天非要变多美，她是想别再绷着了。",
            "有些人来做护理，不是想升级项目，是想今天别再被安排。",
            "她要的不是全套流程，是终于能安静躺一会儿。",
            "很多人不是来做项目的，是来把今天这口气顺过去的。",
        ],
        "boss_judgment_lines": [
            "你要是在我这儿还放松不下来，那就是我这边没做到位。",
            "我不怕你今天做得少，我怕你做完整个人更累。",
            "来这儿如果还得绷着，那就不是放松。",
            "人先放松下来，护理才有意义。",
            "来做护理如果还得听一堆推销，那还不如不做。",
            "我宁可今天项目少一点，也不想把你做得更疲惫。",
            "做护理这件事，先让人安静下来，比流程排满重要。",
        ],
        "low_pressure_offer_lines": [
            "第一次来，先做补水舒缓或者放松护理就够了。",
            "先选轻一点的，先看看你今天到底想做到哪一步。",
            "今天先别安排太满，先把人稳下来。",
            "第一次来，先做让你不累的那一种。",
            "今天先做轻一点的，别一上来排太满。",
            "第一次来，先做补水或者舒缓，先让人轻一点。",
            "先选你今天做完不会累的那一种，别贪多。",
            "今天先别上太多项目，先把这一趟做舒服。",
        ],
        "landing_lines": [
            "做完别比来之前还累。",
            "这次先把人放松下来。",
            "至少这一趟，出来的时候人是轻的。",
            "今天先缓一缓，别再把自己排满。",
            "做完出来，人别还是紧的。",
            "至少这一趟做完，你能松口气。",
            "今天先别追求多，先让自己舒服一点。",
            "出来的时候人是轻的，这趟就没白来。",
        ],
    },
    "本地找店": {
        "translation_lines": [
            "她在本地找店，想找的不是项目多，是分寸感。",
            "很多人换店，不是因为没效果，是因为不想再受那份气。",
            "她找的不是最会讲的店，是说人话的店。",
            "本地找店，最怕的不是远，是白跑一趟还一肚子气。",
            "她找的不是离得最近的店，是进去以后不用先防着的店。",
            "很多人换店，不是因为项目不够，是因为受不了那个服务态度。",
            "本地找店最怕的不是麻烦，是去一趟心里更堵。",
            "她不是想找最热闹的店，是想找一家分寸感对的店。",
        ],
        "boss_judgment_lines": [
            "我不怕第一次做得少，我怕第一次就把人吓跑了。",
            "你第一次来我这儿，我先让你把店看明白。",
            "店离得近不算靠谱，分寸感对了才算。",
            "第一次见面就追着你讲项目，这种店我自己都不信。",
            "第一次来先看人，再看项目，心里有数比什么都重要。",
            "一家店靠不靠谱，不是看她讲得多满，是看她会不会适可而止。",
            "离家近只是方便，能不能让你不别扭才重要。",
            "第一次见面就让你有压力，这种店后面大概率也轻松不了。",
        ],
        "low_pressure_offer_lines": [
            "第一次来，先做基础项目，先把这家店看明白。",
            "先约个简单的，先看看这家店是不是适合你。",
            "第一次来不用做太多，先把这趟走顺。",
            "先做最基础的，合不合适你心里先有数。",
            "第一次来先做个基础项目，先看人和节奏合不合适。",
            "先约个轻一点的，先看看这家店会不会让你别扭。",
            "第一次来不用全做，先把这趟体验走完再说。",
            "先把这家店看明白，后面要不要长期来再决定。",
        ],
        "landing_lines": [
            "第一次先别贪多，先看看这家店值不值得再来。",
            "先把这次做顺，后面才谈长期。",
            "先别白跑，这次至少要做得明白。",
            "第一次来，先让你心里有数。",
            "第一次做完，心里不堵，下次你才会想再来。",
            "这次先把体验做顺，后面再谈长期交给谁。",
            "先看这家店会不会让你放下防备，再谈别的。",
            "第一次来，先别让自己白跑一趟。",
        ],
    },
}
CHUNSHE_MATCH_STOP_TOKENS = {
    "美容院",
    "做脸",
    "项目",
    "今天",
    "这次",
    "第一次",
    "很多人",
    "一个人",
    "一下",
    "一种",
    "是不是",
    "会不会",
    "怎么选",
    "有用吗",
    "能不能",
    "先别",
    "别跟",
    "跟我",
    "讲话",
    "说话",
}


def _normalize_key(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def _extract_chunshe_match_tokens(value: str) -> set[str]:
    text = _normalize_key(value)
    if not text:
        return set()
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9]{2,}", text):
        if token not in CHUNSHE_MATCH_STOP_TOKENS:
            tokens.add(token)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if len(chunk) <= 4 and chunk not in CHUNSHE_MATCH_STOP_TOKENS:
            tokens.add(chunk)
        for size in (2, 3, 4):
            if len(chunk) < size:
                continue
            for index in range(len(chunk) - size + 1):
                token = chunk[index : index + size]
                if token in CHUNSHE_MATCH_STOP_TOKENS:
                    continue
                tokens.add(token)
    return {token for token in tokens if len(token) >= 2}


def _count_chunshe_overlap_tokens(left: str, right: str) -> int:
    if not left or not right:
        return 0
    return len(_extract_chunshe_match_tokens(left) & _extract_chunshe_match_tokens(right))


def _build_chunshe_brief_seed_topic(seed_keyword: str, entry_class: str) -> dict[str, Any]:
    keyword = str(seed_keyword or "").strip()
    normalized_entry_class = infer_chunshe_entry_class(keyword, entry_class)
    fingerprint = sum(ord(ch) for ch in keyword)
    return {
        "topic_id": f"AUTO-{normalized_entry_class}-{fingerprint % 100000:05d}",
        "seed_keyword": keyword,
        "topic_title": keyword,
        "entry_class": normalized_entry_class,
        "angle_type": QUOTE_TOPIC_ANGLE_BY_ENTRY.get(normalized_entry_class, "防御拆解"),
        "scene_trigger": QUOTE_TOPIC_SCENE_BY_ENTRY.get(normalized_entry_class, ""),
        "fear": QUOTE_TOPIC_FEAR_BY_ENTRY.get(normalized_entry_class, ""),
        "real_desire": QUOTE_TOPIC_DESIRE_BY_ENTRY.get(normalized_entry_class, ""),
        "real_buy_point": QUOTE_TOPIC_BUY_POINT_BY_ENTRY.get(normalized_entry_class, ""),
        "store_rule_hint": QUOTE_TOPIC_RULE_BY_ENTRY.get(normalized_entry_class, "说不要，话题就停"),
        "life_stage_hint": "自动",
        "ending_function": QUOTE_TOPIC_ENDING_BY_ENTRY.get(normalized_entry_class, "安心感"),
        "priority_score": 9.9,
        "source_type": "synthetic_seed",
        "status": "brief_seed",
    }


def normalize_chunshe_role(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "李可"
    direct = ROLE_MAP.get(raw)
    if direct:
        return direct
    return ROLE_MAP.get(_normalize_key(raw), "李可")


def normalize_chunshe_output_type(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "精简发布版"
    direct = OUTPUT_TYPE_MAP.get(raw)
    if direct:
        return direct
    return OUTPUT_TYPE_MAP.get(_normalize_key(raw), "精简发布版")


def normalize_chunshe_entry_class(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or raw == "自动":
        return ""
    normalized = _normalize_key(raw)
    alias_map = {
        "问题修复": "问题修复",
        "问题": "问题修复",
        "信任怀疑": "信任怀疑",
        "信任怀疑词": "信任怀疑",
        "怀疑": "信任怀疑",
        "放松养护": "放松养护",
        "放松": "放松养护",
        "养护": "放松养护",
        "本地找店": "本地找店",
        "本地": "本地找店",
        "找店": "本地找店",
    }
    for key, target in alias_map.items():
        if normalized == _normalize_key(key):
            return target
    return ""


def infer_chunshe_entry_class(seed_keyword: str, explicit: str = "") -> str:
    normalized_explicit = normalize_chunshe_entry_class(explicit)
    if normalized_explicit:
        return normalized_explicit
    keyword = str(seed_keyword or "").strip().lower()
    if not keyword:
        return "信任怀疑"
    if any(token in keyword for token in ENTRY_CLASS_KEYWORDS["放松养护"]):
        return "放松养护"
    if any(token in keyword for token in ENTRY_CLASS_KEYWORDS["本地找店"]):
        return "本地找店"
    if any(token in keyword for token in ENTRY_CLASS_KEYWORDS["信任怀疑"]):
        return "信任怀疑"
    return "问题修复"


def is_high_boundary_keyword(seed_keyword: str) -> bool:
    keyword = str(seed_keyword or "").strip().lower()
    return any(token in keyword for token in HIGH_BOUNDARY_TERMS)


@lru_cache(maxsize=1)
def load_chunshe_topic_seed_pool() -> list[dict[str, Any]]:
    if not CHUNSHE_TOPIC_POOL_PATH.exists():
        return []
    payload = json.loads(CHUNSHE_TOPIC_POOL_PATH.read_text(encoding="utf-8"))
    items = payload.get("topics") if isinstance(payload, dict) else []
    return [dict(item) for item in items if isinstance(item, dict)]


@lru_cache(maxsize=1)
def load_chunshe_review_phrase_bank() -> dict[str, Any]:
    if not CHUNSHE_REVIEW_PHRASE_BANK_PATH.exists():
        return {}
    try:
        payload = json.loads(CHUNSHE_REVIEW_PHRASE_BANK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=1)
def load_chunshe_theme_bank() -> dict[str, Any]:
    if not CHUNSHE_THEME_BANK_PATH.exists():
        return {}
    try:
        payload = json.loads(CHUNSHE_THEME_BANK_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=1)
def load_chunshe_priority_theme_bank() -> dict[str, Any]:
    if not CHUNSHE_THEME_BANK_PRIORITY_PATH.exists():
        return {}
    try:
        payload = json.loads(CHUNSHE_THEME_BANK_PRIORITY_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=1)
def load_chunshe_owner_spoken_bank() -> dict[str, Any]:
    """Load the body_ready 老板口语素材库 (scene_openers / boss_responses / longing_moments / low_bar_endings)."""
    if not CHUNSHE_OWNER_SPOKEN_BANK_PATH.exists():
        return {}
    try:
        payload = json.loads(CHUNSHE_OWNER_SPOKEN_BANK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _pick_random_from_list(items: list[str], *, seed: str, used_texts: set[str] | None = None) -> str:
    """Pick one item from a plain string list using deterministic rotation, avoiding *used_texts*."""
    if not items:
        return ""
    used = {s.strip() for s in (used_texts or set()) if s and s.strip()}
    fingerprint = sum(ord(ch) for ch in str(seed or ""))
    start = fingerprint % len(items)
    for offset in range(len(items)):
        text = items[(start + offset) % len(items)].strip()
        if text and text not in used:
            return text
    return items[start].strip()


def _normalize_chunshe_phrase_options(options: list[Any]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in options:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            source_theme = str(item.get("source_theme") or "").strip()
            source_file = str(item.get("source_file") or "").strip()
            source_type = str(item.get("source_type") or "").strip()
            priority = str(item.get("priority") or "").strip()
            usage_mode = str(item.get("usage_mode") or "").strip() or "theme_only"
        else:
            text = str(item).strip()
            source_theme = ""
            source_file = ""
            source_type = ""
            priority = ""
            usage_mode = "theme_only"
        if not text:
            continue
        cleaned.append(
            {
                "text": text,
                "source_theme": source_theme,
                "source_file": source_file,
                "source_type": source_type,
                "priority": priority,
                "usage_mode": usage_mode,
            }
        )
    return cleaned


def _merge_chunshe_phrase_options(*groups: list[Any]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for group in groups:
        for item in _normalize_chunshe_phrase_options(group):
            text = item.get("text", "")
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(item)
    return merged


def _filter_chunshe_phrase_options(
    options: list[Any],
    *,
    usage_modes: set[str] | None = None,
) -> list[dict[str, str]]:
    cleaned = _normalize_chunshe_phrase_options(options)
    if not usage_modes:
        return cleaned
    return [item for item in cleaned if str(item.get("usage_mode") or "").strip() in usage_modes]


def _pick_chunshe_phrase(options: list[Any], *, seed: str, preferred_count: int = 0) -> str:
    cleaned = _normalize_chunshe_phrase_options(options)
    if not cleaned:
        return ""
    fingerprint = sum(ord(ch) for ch in str(seed or ""))
    if preferred_count > 0:
        preferred = cleaned[:preferred_count]
        fallback = cleaned[preferred_count:]
        if preferred and (not fallback or fingerprint % 5 != 0):
            return preferred[fingerprint % len(preferred)]["text"]
    return cleaned[fingerprint % len(cleaned)]["text"]


def _pick_distinct_chunshe_phrase(
    options: list[Any],
    *,
    seed: str,
    preferred_count: int = 0,
    used_texts: set[str] | None = None,
) -> str:
    used = {str(item or "").strip() for item in (used_texts or set()) if str(item or "").strip()}
    cleaned = _normalize_chunshe_phrase_options(options)
    if not cleaned:
        return ""
    fingerprint = sum(ord(ch) for ch in str(seed or ""))

    def _pick_from(candidates: list[dict[str, str]]) -> str:
        if not candidates:
            return ""
        start = fingerprint % len(candidates)
        for offset in range(len(candidates)):
            item = candidates[(start + offset) % len(candidates)]
            text = str(item.get("text") or "").strip()
            if text and text not in used:
                return text
        return str(candidates[start].get("text") or "").strip()

    preferred = cleaned[:preferred_count] if preferred_count > 0 else []
    fallback = cleaned[preferred_count:] if preferred_count > 0 else cleaned
    if preferred and (not fallback or fingerprint % 5 != 0):
        picked = _pick_from(preferred)
        if picked:
            return picked
    picked = _pick_from(cleaned)
    if picked:
        return picked
    return str(cleaned[0].get("text") or "").strip()


def _is_chunshe_body_ready_text(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    blocked_markers = (
        "掠夺你",
        "专属感",
        "粗糙的开始",
        "先松下来",
        "有尊严的服务者",
        "研究人性",
        "给人使绊子",
        "向你证明他自己",
        "本质",
        "真正",
    )
    return not any(marker in value for marker in blocked_markers)


def select_chunshe_theme_phrase_pack(
    *,
    entry_class: str,
    topic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = load_chunshe_theme_bank()
    priority_payload = load_chunshe_priority_theme_bank()
    classes = payload.get("entry_classes") if isinstance(payload, dict) else {}
    priority_classes = priority_payload.get("entry_classes") if isinstance(priority_payload, dict) else {}
    entry_payload = classes.get(str(entry_class or "").strip()) if isinstance(classes, dict) else None
    priority_entry_payload = (
        priority_classes.get(str(entry_class or "").strip()) if isinstance(priority_classes, dict) else None
    )
    if not isinstance(entry_payload, dict):
        return {
            "principle": "",
            "theme_line": "",
            "translation_line": "",
            "boss_judgment_line": "",
            "low_pressure_offer_line": "",
            "theme_examples": [],
            "translation_examples": [],
            "boss_judgment_examples": [],
            "low_pressure_offer_examples": [],
        }

    topic = dict(topic or {})
    selector_seed = "|".join(
        [
            str(topic.get("topic_id") or "").strip(),
            str(topic.get("topic_title") or "").strip(),
            str(topic.get("seed_keyword") or "").strip(),
            str(topic.get("angle_type") or "").strip(),
            str(topic.get("scene_trigger") or "").strip(),
        ]
    )
    principle = str(
        (priority_payload.get("meta") or {}).get("principle")
        or (payload.get("meta") or {}).get("principle")
        or ""
    ).strip()
    priority_entry_payload = priority_entry_payload if isinstance(priority_entry_payload, dict) else {}
    priority_theme_examples = list(priority_entry_payload.get("theme_lines") or [])
    priority_translation_examples = list(priority_entry_payload.get("translation_lines") or [])
    priority_boss_examples = list(priority_entry_payload.get("boss_judgment_lines") or [])
    priority_offer_examples = list(priority_entry_payload.get("low_pressure_offer_lines") or [])
    theme_examples = _merge_chunshe_phrase_options(priority_theme_examples, list(entry_payload.get("theme_lines") or []))
    translation_examples = _merge_chunshe_phrase_options(
        priority_translation_examples,
        list(entry_payload.get("translation_lines") or []),
    )
    boss_examples = _merge_chunshe_phrase_options(
        priority_boss_examples,
        list(entry_payload.get("boss_judgment_lines") or []),
    )
    offer_examples = _merge_chunshe_phrase_options(
        priority_offer_examples,
        list(entry_payload.get("low_pressure_offer_lines") or []),
    )
    translation_body_ready_examples = [
        item for item in _filter_chunshe_phrase_options(translation_examples, usage_modes={"body_ready"})
        if _is_chunshe_body_ready_text(str(item.get("text") or ""))
    ]
    boss_body_ready_examples = [
        item for item in _filter_chunshe_phrase_options(boss_examples, usage_modes={"body_ready"})
        if _is_chunshe_body_ready_text(str(item.get("text") or ""))
    ]
    offer_body_ready_examples = [
        item for item in _filter_chunshe_phrase_options(offer_examples, usage_modes={"body_ready"})
        if _is_chunshe_body_ready_text(str(item.get("text") or ""))
    ]
    preferred_theme_count = len(_normalize_chunshe_phrase_options(priority_theme_examples))
    preferred_translation_count = len(
        _filter_chunshe_phrase_options(priority_translation_examples, usage_modes={"body_ready"})
    )
    preferred_boss_count = len(_filter_chunshe_phrase_options(priority_boss_examples, usage_modes={"body_ready"}))
    preferred_offer_count = len(_filter_chunshe_phrase_options(priority_offer_examples, usage_modes={"body_ready"}))
    theme_line = _pick_distinct_chunshe_phrase(
        theme_examples,
        seed=f"{selector_seed}|theme",
        preferred_count=preferred_theme_count,
    )
    translation_line = _pick_distinct_chunshe_phrase(
        translation_body_ready_examples,
        seed=f"{selector_seed}|translation",
        preferred_count=preferred_translation_count,
        used_texts={theme_line},
    )
    boss_judgment_line = _pick_distinct_chunshe_phrase(
        boss_body_ready_examples,
        seed=f"{selector_seed}|boss",
        preferred_count=preferred_boss_count,
        used_texts={theme_line, translation_line},
    )
    low_pressure_offer_line = _pick_distinct_chunshe_phrase(
        offer_body_ready_examples,
        seed=f"{selector_seed}|offer",
        preferred_count=preferred_offer_count,
        used_texts={theme_line, translation_line, boss_judgment_line},
    )
    if not _is_chunshe_body_ready_text(translation_line):
        translation_line = ""
    if not _is_chunshe_body_ready_text(boss_judgment_line):
        boss_judgment_line = ""
    if not _is_chunshe_body_ready_text(low_pressure_offer_line):
        low_pressure_offer_line = ""
    return {
        "principle": principle,
        "theme_examples": theme_examples,
        "translation_examples": translation_examples,
        "boss_judgment_examples": boss_examples,
        "low_pressure_offer_examples": offer_examples,
        "theme_line": theme_line,
        "translation_line": translation_line,
        "boss_judgment_line": boss_judgment_line,
        "low_pressure_offer_line": low_pressure_offer_line,
    }


def select_chunshe_review_phrase_pack(
    *,
    entry_class: str,
    seed_keyword: str = "",
    topic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = load_chunshe_review_phrase_bank()
    classes = payload.get("entry_classes") if isinstance(payload, dict) else {}
    entry_payload = classes.get(str(entry_class or "").strip()) if isinstance(classes, dict) else None
    if not isinstance(entry_payload, dict):
        return {"bucket": "", "opening": [], "scene": [], "landing": []}

    topic = dict(topic or {})
    combined = " ".join(
        [
            str(seed_keyword or ""),
            str(topic.get("topic_title") or ""),
            str(topic.get("scene_trigger") or ""),
            str(topic.get("fear") or ""),
        ]
    )
    normalized = _normalize_key(combined)

    bucket = "default"
    if entry_class == "信任怀疑":
        if any(token in normalized for token in (_normalize_key("约"), _normalize_key("预约"), _normalize_key("退卡"), _normalize_key("退费"))):
            bucket = "booking"
        else:
            bucket = "sales"
    elif entry_class == "问题修复":
        if any(token in normalized for token in (_normalize_key("时间"), _normalize_key("90分钟"), _normalize_key("60分钟"), _normalize_key("项目"))):
            bucket = "duration"
        else:
            bucket = "cleaning"
    elif entry_class == "放松养护":
        if any(token in normalized for token in (_normalize_key("spa"), _normalize_key("肩颈"), _normalize_key("放松"), _normalize_key("按摩"))):
            bucket = "service"
        else:
            bucket = "pushy"
    elif entry_class == "本地找店":
        if any(token in normalized for token in (_normalize_key("约"), _normalize_key("预约"), _normalize_key("吴江"), _normalize_key("附近"))):
            bucket = "booking"
        else:
            bucket = "attitude"

    phrase_group = entry_payload.get(bucket) if isinstance(entry_payload.get(bucket), dict) else entry_payload.get("default")
    if not isinstance(phrase_group, dict):
        phrase_group = {}
    return {
        "bucket": bucket,
        "opening": [str(item).strip() for item in phrase_group.get("opening") or [] if str(item).strip()],
        "scene": [str(item).strip() for item in phrase_group.get("scene") or [] if str(item).strip()],
        "landing": [str(item).strip() for item in phrase_group.get("landing") or [] if str(item).strip()],
    }


def _prefer_review_phrase(existing: str, review_text: str) -> str:
    existing_text = str(existing or "").strip()
    review = str(review_text or "").strip()
    if not review:
        return existing_text
    if not existing_text:
        return review
    if any(marker in existing_text for marker in CHUNSHE_ABSTRACT_MARKERS):
        return review
    return existing_text


COMPLAINT_LANDING_MARKERS = (
    "推销",
    "办卡",
    "敷衍",
    "不买",
    "说不买",
    "拒绝",
    "套路",
    "忽悠",
    "白花钱",
)

RESULT_LANDING_MARKERS = (
    "更累",
    "白跑",
    "安心",
    "放松",
    "稳住",
    "做完",
    "别比来之前还累",
    "别还没做完",
)


def _is_complaint_landing_line(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    return any(marker in value for marker in COMPLAINT_LANDING_MARKERS)


def _is_result_landing_line(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    return any(marker in value for marker in RESULT_LANDING_MARKERS)


def _choose_review_landing_line(*, review_landing: str, fallback: str) -> str:
    review_value = str(review_landing or "").strip()
    fallback_value = str(fallback or "").strip()
    if not review_value:
        return fallback_value
    if _is_complaint_landing_line(review_value) and not _is_result_landing_line(review_value):
        return fallback_value
    return review_value


def default_chunshe_topics() -> list[dict[str, Any]]:
    return [dict(item) for item in load_chunshe_topic_seed_pool() if str(item.get("status") or "") == "default_fallback"]


def _build_chunshe_quote_topic_seed(
    *,
    item: dict[str, Any],
    seed_keyword: str,
    entry_class: str,
    score: float,
    index: int,
) -> dict[str, Any]:
    quote_text = str(item.get("text") or "").strip().rstrip("。！？；;")
    return {
        "topic_id": f"QUOTE-{entry_class}-{index:02d}",
        "seed_keyword": str(seed_keyword or "").strip(),
        "topic_title": quote_text,
        "entry_class": entry_class,
        "angle_type": QUOTE_TOPIC_ANGLE_BY_ENTRY.get(entry_class, "防御拆解"),
        "scene_trigger": QUOTE_TOPIC_SCENE_BY_ENTRY.get(entry_class, ""),
        "fear": QUOTE_TOPIC_FEAR_BY_ENTRY.get(entry_class, ""),
        "real_desire": QUOTE_TOPIC_DESIRE_BY_ENTRY.get(entry_class, ""),
        "real_buy_point": QUOTE_TOPIC_BUY_POINT_BY_ENTRY.get(entry_class, ""),
        "store_rule_hint": QUOTE_TOPIC_RULE_BY_ENTRY.get(entry_class, "说不要，话题就停"),
        "life_stage_hint": "自动",
        "ending_function": QUOTE_TOPIC_ENDING_BY_ENTRY.get(entry_class, "安心感"),
        "priority_score": round(score, 2),
        "source_type": "quote_topic_seed",
        "quote_seed_text": quote_text,
        "quote_seed_theme": str(item.get("theme") or "").strip(),
        "quote_seed_usage": str(item.get("usage") or "").strip(),
        "quote_seed_file": str(item.get("file_name") or "").strip(),
        "quote_seed_tags": list(item.get("tags") or []),
        "title_preserve_core_feel": True,
        "quote_direct_use_rule": "保留核心句感，不机械照抄原话",
        "benchmark_usage_rule": "对标链接只学结构，不直接借句",
    }


def _is_chunshe_quote_topic_seed(item: dict[str, Any], *, seed_keyword: str, entry_class: str) -> bool:
    tags = set(item.get("tags") or [])
    text = str(item.get("text") or "").strip()
    if "#选题" not in tags:
        return False
    if not _is_safe_chunshe_quote(item):
        return False
    if any(marker in text for marker in QUOTE_TOPIC_SEED_BLOCK_MARKERS):
        return False
    normalized_keyword = _normalize_key(seed_keyword)
    normalized_text = _normalize_key(text)
    if normalized_keyword and normalized_text and normalized_keyword == normalized_text:
        return True
    overlap_hits = _count_chunshe_overlap_tokens(seed_keyword, text)
    hint_hits = sum(1 for token in QUOTE_TOPIC_SEED_HINTS.get(entry_class, ()) if token in text)
    keyword_hits = overlap_hits
    if normalized_keyword and normalized_text and (
        normalized_keyword in normalized_text or normalized_text in normalized_keyword
    ):
        keyword_hits += 2
    if normalized_keyword and keyword_hits <= 0:
        return False
    if len(text) > 28 and keyword_hits < 2:
        return False
    if keyword_hits <= 1 and hint_hits <= 0:
        return False
    return True


def match_chunshe_quote_topic_seed_examples(seed_keyword: str, entry_class: str, *, limit: int = 12) -> list[dict[str, Any]]:
    normalized_entry_class = infer_chunshe_entry_class(seed_keyword, entry_class)
    theme_order = allowed_quote_themes(normalized_entry_class)
    normalized_keyword = _normalize_key(seed_keyword)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for index, item in enumerate(load_chunshe_quote_catalog(), start=1):
        if item.get("theme") not in theme_order:
            continue
        if not _is_chunshe_quote_topic_seed(item, seed_keyword=seed_keyword, entry_class=normalized_entry_class):
            continue
        score = 12.0
        score += max(0, 6 - theme_order.index(str(item.get("theme") or ""))) * 2.0
        usage = str(item.get("usage") or "").strip()
        if usage in {"开头钩子", "观点", "警示"}:
            score += 2.0
        if "#标题" in set(item.get("tags") or []):
            score += 1.0
        text = str(item.get("text") or "")
        normalized_text = _normalize_key(text)
        if normalized_keyword and normalized_text and normalized_keyword == normalized_text:
            score += 30.0
        elif normalized_keyword and normalized_text and (
            normalized_keyword in normalized_text or normalized_text in normalized_keyword
        ):
            score += 8.0
        overlap_hits = _count_chunshe_overlap_tokens(seed_keyword, text)
        score += min(overlap_hits * 2.4, 7.2)
        for token in QUOTE_TOPIC_SEED_HINTS.get(normalized_entry_class, ()):
            if token in text:
                score += 0.8
        ranked.append(
            (
                score,
                _build_chunshe_quote_topic_seed(
                    item=dict(item),
                    seed_keyword=seed_keyword,
                    entry_class=normalized_entry_class,
                    score=score,
                    index=index,
                ),
            )
        )

    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("quote_seed_text") or "")))
    out: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for _score, item in ranked:
        title_key = _normalize_key(str(item.get("topic_title") or ""))
        if not title_key or title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        out.append(item)
        if len(out) >= max(1, limit):
            break
    return out


def match_chunshe_topic_seed_examples(seed_keyword: str, entry_class: str, *, limit: int = 12) -> list[dict[str, Any]]:
    keyword = str(seed_keyword or "").strip()
    normalized_keyword = _normalize_key(keyword)
    pool = load_chunshe_topic_seed_pool()
    if normalized_keyword in {_normalize_key("默认7题"), _normalize_key("默认七题")}:
        return default_chunshe_topics()[: max(1, limit)]

    normalized_entry_class = infer_chunshe_entry_class(seed_keyword, entry_class)
    ranked_out: list[dict[str, Any]] = []
    quote_seeds = match_chunshe_quote_topic_seed_examples(
        seed_keyword,
        normalized_entry_class,
        limit=max(1, limit),
    )
    ranked_out.extend(quote_seeds[: max(1, limit)])

    ranked: list[tuple[float, dict[str, Any]]] = []
    best_manual_score = 0.0
    for item in pool:
        score = 0.0
        seed = str(item.get("seed_keyword") or "").strip()
        topic_title = str(item.get("topic_title") or "").strip()
        score += min(_count_chunshe_overlap_tokens(keyword, seed) * 2.2, 6.6)
        score += min(_count_chunshe_overlap_tokens(keyword, topic_title) * 1.6, 4.8)
        if normalized_keyword and _normalize_key(seed) == normalized_keyword:
            score += 8.0
        elif normalized_keyword and (normalized_keyword in _normalize_key(seed) or _normalize_key(seed) in normalized_keyword):
            score += 4.0
        if normalized_entry_class and str(item.get("entry_class") or "").strip() == normalized_entry_class:
            score += 3.0
        if normalized_keyword and normalized_keyword in _normalize_key(topic_title):
            score += 2.0
        if str(item.get("status") or "").strip() == "default_fallback":
            score += 0.8
        if score > 0:
            topic = dict(item)
            topic.setdefault("source_type", "topic_seed_pool")
            ranked.append((score, topic))
            best_manual_score = max(best_manual_score, float(score))

    if not ranked:
        ranked = [
            (
                1.0 if str(item.get("entry_class") or "").strip() == normalized_entry_class else 0.1,
                {**dict(item), "source_type": str(item.get("source_type") or "topic_seed_pool").strip() or "topic_seed_pool"},
            )
            for item in pool
            if not normalized_entry_class or str(item.get("entry_class") or "").strip() == normalized_entry_class
        ]
        best_manual_score = max((float(score) for score, _item in ranked), default=0.0)

    should_promote_brief_seed = (
        not quote_seeds
        and bool(keyword)
        and len(keyword) >= 8
        and best_manual_score <= 3.2
        and any(marker in keyword for marker in ("能不能", "不想讲话", "别跟我讲话", "安静", "推销", "办卡", "白跑", "先不要", "别讲"))
    )
    if should_promote_brief_seed:
        synthetic = _build_chunshe_brief_seed_topic(keyword, normalized_entry_class)
        ranked_out.insert(0, synthetic)

    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("topic_id") or "")))
    seen_titles = {_normalize_key(str(item.get("topic_title") or "")) for item in ranked_out}
    for score, item in ranked:
        title_key = _normalize_key(str(item.get("topic_title") or ""))
        if not title_key or title_key in seen_titles:
            continue
        clone = dict(item)
        clone["priority_score"] = max(float(clone.get("priority_score") or 0), float(score))
        ranked_out.append(clone)
        seen_titles.add(title_key)
        if len(ranked_out) >= max(1, limit):
            break
    return ranked_out[: max(1, limit)]


def __deprecated_collect_recent_chunshe_history_v1(*, lookback_days: int = 30) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    today = dt.date.today()
    cutoff = today - dt.timedelta(days=max(1, lookback_days))

    if CHUNSHE_REPORT_ROOT.exists():
        for selected_path in list(CHUNSHE_REPORT_ROOT.glob("*/*/selected-topics.json")) + list(CHUNSHE_REPORT_ROOT.glob("*/*/topic-selection.json")):
            date_text = selected_path.parts[-3]
            try:
                run_date = dt.date.fromisoformat(date_text)
            except Exception:
                continue
            if run_date < cutoff:
                continue
            try:
                payload = json.loads(selected_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for item in payload.get("selected_topics") or []:
                if isinstance(item, dict):
                    row = dict(item)
                    row["date"] = date_text
                    row["source"] = "report"
                    history.append(row)

    if XHS_OUTPUT_ROOT.exists():
        for date_dir in XHS_OUTPUT_ROOT.iterdir():
            if not date_dir.is_dir():
                continue
            try:
                run_date = dt.date.fromisoformat(date_dir.name)
            except Exception:
                continue
            if run_date < cutoff:
                continue
            for file_path in date_dir.glob("*.md"):
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                title = ""
                lines = [str(line or "").strip() for line in content.splitlines() if str(line or "").strip()]
                for idx, line in enumerate(lines):
                    if line in {"# 标题", "## 标题"} and idx + 1 < len(lines):
                        title = lines[idx + 1].strip()
                        break
                    if line.startswith("# ") and line not in {"# 标题", "# 正文", "# 口播正文", "# 置顶评论", "# 回复模板"}:
                        title = line[2:].strip()
                        break
                if title:
                    history.append(
                        {
                            "topic_title": title,
                            "date": date_dir.name,
                            "source": "output",
                        }
                    )

    history.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    return history


def collect_recent_chunshe_history(*, lookback_days: int = 30) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    today = dt.date.today()
    cutoff = today - dt.timedelta(days=max(1, lookback_days))
    role_prefixes = tuple(f"{role}-" for role in ROLE_MAP.values() if role)

    def _append_history(row: dict[str, Any]) -> None:
        title_key = _normalize_key(str(row.get("topic_title") or ""))
        date_key = str(row.get("date") or "")
        if not title_key or not date_key:
            return
        key = (title_key, date_key)
        if key in seen:
            return
        seen.add(key)
        history.append(row)

    def _resolve_output_path(path_text: str) -> Path:
        candidate = Path(path_text)
        if candidate.is_absolute():
            return candidate
        return XHS_OUTPUT_ROOT.parent / candidate

    if CHUNSHE_REPORT_ROOT.exists():
        for summary_path in CHUNSHE_REPORT_ROOT.glob("*/*/run-summary.json"):
            date_text = summary_path.parts[-3]
            try:
                run_date = dt.date.fromisoformat(date_text)
            except Exception:
                continue
            if run_date < cutoff:
                continue
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(payload.get("status") or "").strip() not in {"success", "partial_error"}:
                continue
            if normalize_chunshe_output_type(str(payload.get("output_type") or "")) == "标题池":
                continue
            topic_lookup: dict[str, dict[str, Any]] = {}
            for item in payload.get("selected_topics") or []:
                if not isinstance(item, dict):
                    continue
                item_key = str(item.get("topic_id") or "").strip()
                title_key = str(item.get("topic_title") or "").strip()
                if item_key:
                    topic_lookup[item_key] = dict(item)
                if title_key:
                    topic_lookup[title_key] = dict(item)
            for item in payload.get("topic_results") or []:
                if not isinstance(item, dict):
                    continue
                path_text = str(item.get("path") or "").strip()
                if not path_text or not _resolve_output_path(path_text).exists():
                    continue
                title = str(item.get("topic_title") or "").strip()
                matched = topic_lookup.get(str(item.get("topic_id") or "").strip()) or topic_lookup.get(title) or {}
                row = dict(matched)
                row["topic_title"] = title or str(row.get("topic_title") or "").strip()
                row["date"] = date_text
                row["source"] = "report"
                _append_history(row)

    if XHS_OUTPUT_ROOT.exists():
        for date_dir in XHS_OUTPUT_ROOT.iterdir():
            if not date_dir.is_dir():
                continue
            try:
                run_date = dt.date.fromisoformat(date_dir.name)
            except Exception:
                continue
            if run_date < cutoff:
                continue
            for file_path in date_dir.glob("*.md"):
                if not file_path.name.startswith(role_prefixes):
                    continue
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                title = ""
                lines = [str(line or "").strip() for line in content.splitlines() if str(line or "").strip()]
                for idx, line in enumerate(lines):
                    if line in {"# 标题", "## 标题"} and idx + 1 < len(lines):
                        title = lines[idx + 1].strip()
                        break
                    if line.startswith("# ") and line not in {"# 标题", "# 正文", "# 口播正文", "# 置顶评论", "# 回复模板"}:
                        title = line[2:].strip()
                        break
                if title:
                    _append_history(
                        {
                            "topic_title": title,
                            "date": date_dir.name,
                            "source": "output",
                        }
                    )

    history.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    return history


def dedupe_and_pick_chunshe_topics(
    topic_pool: list[dict[str, Any]],
    history: list[dict[str, Any]],
    *,
    count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    recent_title_keys = {
        _normalize_key(str(item.get("topic_title") or ""))
        for item in history
        if str(item.get("topic_title") or "").strip()
    }
    recent_angle_keys = [
        _normalize_key(str(item.get("angle_type") or ""))
        for item in history
        if str(item.get("angle_type") or "").strip()
    ][:10]
    recent_scene_keys = {
        _normalize_key(str(item.get("scene_trigger") or ""))
        for item in history
        if str(item.get("scene_trigger") or "").strip()
    }

    ranked = sorted(
        [dict(item) for item in topic_pool if isinstance(item, dict)],
        key=lambda item: (
            -float(item.get("priority_score") or 0),
            str(item.get("topic_id") or ""),
        ),
    )

    picked: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    seen_angles: set[str] = set()
    seen_scenes: set[str] = set()
    seen_rules: set[str] = set()
    seen_endings: set[str] = set()

    def _reject(item: dict[str, Any], reason: str) -> None:
        clone = dict(item)
        clone["reject_reason"] = reason
        rejected.append(clone)

    for item in ranked:
        title_key = _normalize_key(str(item.get("topic_title") or ""))
        angle_key = _normalize_key(str(item.get("angle_type") or ""))
        scene_key = _normalize_key(str(item.get("scene_trigger") or ""))
        rule_key = _normalize_key(str(item.get("store_rule_hint") or ""))
        ending_key = _normalize_key(str(item.get("ending_function") or ""))

        if not title_key:
            _reject(item, "空题目")
            continue
        if title_key in recent_title_keys or title_key in seen_titles:
            _reject(item, "标题过近")
            continue
        if scene_key and (scene_key in recent_scene_keys or scene_key in seen_scenes):
            _reject(item, "冲突瞬间过近")
            continue
        if count > 1 and angle_key and angle_key in seen_angles:
            _reject(item, "题角重复")
            continue
        if count > 1 and rule_key and rule_key in seen_rules:
            _reject(item, "主规矩重复")
            continue
        if count > 1 and ending_key and ending_key in seen_endings:
            _reject(item, "收口功能重复")
            continue
        if angle_key and angle_key in recent_angle_keys[:3] and len(picked) < count - 1:
            _reject(item, "近期题角过密")
            continue

        picked.append(dict(item))
        seen_titles.add(title_key)
        if angle_key:
            seen_angles.add(angle_key)
        if scene_key:
            seen_scenes.add(scene_key)
        if rule_key:
            seen_rules.add(rule_key)
        if ending_key:
            seen_endings.add(ending_key)
        if len(picked) >= max(1, count):
            break

    if len(picked) < max(1, count):
        for item in ranked:
            title_key = _normalize_key(str(item.get("topic_title") or ""))
            if not title_key or title_key in seen_titles or title_key in recent_title_keys:
                continue
            picked.append(dict(item))
            seen_titles.add(title_key)
            if len(picked) >= max(1, count):
                break

    return picked[: max(1, count)], rejected


def enrich_chunshe_video_topic(topic: dict[str, Any]) -> dict[str, Any]:
    clone = dict(topic or {})
    entry_class = str(clone.get("entry_class") or "信任怀疑").strip() or "信任怀疑"
    angle_type = str(clone.get("angle_type") or "").strip()
    scene = str(clone.get("scene_trigger") or "").strip()
    fear = str(clone.get("fear") or "").strip()
    real_buy_point = str(clone.get("real_buy_point") or clone.get("real_desire") or "").strip()
    store_rule = str(clone.get("store_rule_hint") or "说不要，话题就停").strip() or "说不要，话题就停"
    seed_keyword = str(clone.get("seed_keyword") or "").strip()
    review_pack = select_chunshe_review_phrase_pack(
        entry_class=entry_class,
        seed_keyword=seed_keyword,
        topic=clone,
    )
    review_opening = str((review_pack.get("opening") or [""])[0] or "").strip()
    review_scene = str((review_pack.get("scene") or [""])[0] or "").strip()
    review_landing = str((review_pack.get("landing") or [""])[0] or "").strip()
    theme_pack = select_chunshe_theme_phrase_pack(entry_class=entry_class, topic=clone)
    selector_seed = "|".join(
        [
            str(clone.get("topic_id") or "").strip(),
            str(clone.get("topic_title") or "").strip(),
            str(clone.get("seed_keyword") or "").strip(),
            str(clone.get("angle_type") or "").strip(),
            str(clone.get("scene_trigger") or "").strip(),
        ]
    )
    theme_line = str(theme_pack.get("theme_line") or "").strip()
    translation_theme_line = str(theme_pack.get("translation_line") or "").strip()
    boss_theme_line = str(theme_pack.get("boss_judgment_line") or "").strip()
    offer_theme_line = str(theme_pack.get("low_pressure_offer_line") or "").strip()
    spoken_bank = CHUNSHE_OWNER_SPOKEN_LIBRARY.get(entry_class, {})
    # --- new body_ready 老板口语素材库 ---
    owner_bank = load_chunshe_owner_spoken_bank().get(entry_class, {})
    scene_opener = _pick_random_from_list(
        list(owner_bank.get("scene_openers") or []),
        seed=f"{selector_seed}|scene_opener",
    )
    boss_response = _pick_random_from_list(
        list(owner_bank.get("boss_responses") or []),
        seed=f"{selector_seed}|boss_response",
        used_texts={scene_opener},
    )
    longing_moment = _pick_random_from_list(
        list(owner_bank.get("longing_moments") or []),
        seed=f"{selector_seed}|longing_moment",
        used_texts={scene_opener, boss_response},
    )
    low_bar_ending = _pick_random_from_list(
        list(owner_bank.get("low_bar_endings") or []),
        seed=f"{selector_seed}|low_bar_ending",
        used_texts={scene_opener, boss_response, longing_moment},
    )
    translation_line = _pick_distinct_chunshe_phrase(
        list(spoken_bank.get("translation_lines") or []),
        seed=f"{selector_seed}|spoken_translation",
    )
    boss_judgment_line = _pick_distinct_chunshe_phrase(
        list(spoken_bank.get("boss_judgment_lines") or []),
        seed=f"{selector_seed}|spoken_boss",
        used_texts={translation_line},
    )
    low_pressure_offer_line = _pick_distinct_chunshe_phrase(
        list(spoken_bank.get("low_pressure_offer_lines") or []),
        seed=f"{selector_seed}|spoken_offer",
        used_texts={translation_line, boss_judgment_line},
    )
    landing_line_spoken = _pick_distinct_chunshe_phrase(
        list(spoken_bank.get("landing_lines") or []),
        seed=f"{selector_seed}|spoken_landing",
        used_texts={translation_line, boss_judgment_line, low_pressure_offer_line},
    )

    family_map = {
        "痛点确认": "后果先行型",
        "防御拆解": "冲突实景型",
        "规矩托底": "直接判断型",
        "向往画面": "后果先行型",
        "本地决策": "直接判断型",
        "关系/身份翻译": "一秒后悔型",
    }
    fallback_family = {
        "问题修复": "后果先行型",
        "信任怀疑": "冲突实景型",
        "放松养护": "直接判断型",
        "本地找店": "直接判断型",
    }
    opening_family = family_map.get(angle_type) or fallback_family.get(entry_class, "直接判断型")

    if entry_class == "信任怀疑":
        first_conflict_line = review_opening or "感觉从头到尾都在推销办卡。"
        scene_line = _prefer_review_phrase(scene, review_scene) or "说不买了就很敷衍结束了。"
        translation_line = translation_line or "很多人不是怕花钱，是怕花了钱还得受气。"
        boss_judgment_line = boss_judgment_line or "你要是来我这儿还得防着，那这店就白开了。"
        low_pressure_offer_line = low_pressure_offer_line or "第一次来，别做太多，先把这一趟安安稳稳做完。"
        pivot_line = translation_line
        store_rule_line = f"{store_rule}。"
        landing_line = landing_line_spoken or "至少别还没做完，人先更累。"
    elif entry_class == "问题修复":
        first_conflict_line = review_opening or "去黑头感觉去了个寂寞。"
        scene_line = _prefer_review_phrase(scene or fear, review_scene) or "闭口粉刺还是很多。"
        translation_line = translation_line or "她不是想一步到位，她是不想再白折腾一回。"
        boss_judgment_line = boss_judgment_line or "我不怕做得慢一点，我怕把人做怕了。"
        low_pressure_offer_line = low_pressure_offer_line or "第一次先做基础清洁，先看皮肤能不能稳住。"
        pivot_line = translation_line
        store_rule_line = "能温和就别做猛。"
        landing_line = landing_line_spoken or "先把这一趟做明白，别越做越糟。"
    elif entry_class == "放松养护":
        first_conflict_line = review_opening or "完全感受不到放松。"
        scene_line = _prefer_review_phrase(scene, review_scene) or "都不能好好休息。"
        translation_line = translation_line or "她来这儿，不是要被教育，是想被好好接住。"
        boss_judgment_line = boss_judgment_line or "你要是在我这儿还放松不下来，那就是我这边没做到位。"
        low_pressure_offer_line = low_pressure_offer_line or "第一次来，先做补水舒缓或者放松护理就够了。"
        pivot_line = translation_line
        store_rule_line = "先看状态，再决定今天做到哪一步。"
        landing_line = landing_line_spoken or "做完别比来之前还累。"
    else:
        first_conflict_line = review_opening or "想去店里又约不上。"
        scene_line = _prefer_review_phrase(scene, review_scene) or "说不买了就很敷衍结束了。"
        translation_line = translation_line or "她在本地找店，想找的不是项目多，是分寸感。"
        boss_judgment_line = boss_judgment_line or "我不怕第一次做得少，我怕第一次就把人吓跑了。"
        low_pressure_offer_line = low_pressure_offer_line or "第一次来，先做基础项目，先把这家店看明白。"
        pivot_line = translation_line
        store_rule_line = "规则先说清楚。"
        landing_line = landing_line_spoken or "这次别白跑。"

    clone["opening_family"] = opening_family
    clone["opening_scene"] = scene
    clone["theme_line"] = theme_line or translation_line or boss_judgment_line
    clone["translation_theme_line"] = translation_theme_line
    clone["boss_theme_line"] = boss_theme_line
    clone["offer_theme_line"] = offer_theme_line
    clone["theme_principle"] = str(theme_pack.get("principle") or "").strip()
    clone["theme_examples"] = list(theme_pack.get("theme_examples") or [])
    clone["translation_examples"] = list(theme_pack.get("translation_examples") or [])
    clone["boss_judgment_examples"] = list(theme_pack.get("boss_judgment_examples") or [])
    clone["low_pressure_offer_examples"] = list(theme_pack.get("low_pressure_offer_examples") or [])
    clone["review_phrase_bucket"] = str(review_pack.get("bucket") or "").strip()
    clone["review_raw_opening"] = review_opening
    clone["review_raw_scene"] = review_scene
    clone["review_raw_landing"] = review_landing
    clone["first_conflict_line"] = first_conflict_line
    clone["scene_line"] = scene_line
    clone["translation_line"] = translation_line
    clone["boss_judgment_line"] = boss_judgment_line
    clone["pivot_line"] = pivot_line
    clone["store_rule_line"] = store_rule_line
    clone["low_pressure_offer_line"] = low_pressure_offer_line
    clone["landing_line"] = landing_line
    # body_ready 老板口语素材
    clone["scene_opener"] = scene_opener
    clone["boss_response"] = boss_response
    clone["longing_moment"] = longing_moment
    clone["low_bar_ending"] = low_bar_ending
    return clone


def summarize_recent_history(history: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in history[: max(1, limit)]:
        rows.append(
            {
                "topic_title": str(item.get("topic_title") or "").strip(),
                "angle_type": str(item.get("angle_type") or "").strip(),
                "scene_trigger": str(item.get("scene_trigger") or "").strip(),
                "store_rule_hint": str(item.get("store_rule_hint") or "").strip(),
                "ending_function": str(item.get("ending_function") or "").strip(),
                "date": str(item.get("date") or "").strip(),
                "source": str(item.get("source") or "").strip(),
            }
        )
    return rows


def allowed_quote_themes(entry_class: str, explicit_theme: str = "") -> list[str]:
    ordered: list[str] = []
    explicit = str(explicit_theme or "").strip()
    if explicit:
        ordered.append(explicit)
    for theme in QUOTE_THEME_PRIORITY.get(entry_class, ("人性与沟通", "系统与执行", "自我与哲学（次级）")):
        if theme not in ordered:
            ordered.append(theme)
    return ordered


@lru_cache(maxsize=1)
def load_chunshe_quote_catalog() -> list[dict[str, Any]]:
    quote_dir = REPO_ROOT / "03-素材库" / "金句库"
    catalog: list[dict[str, Any]] = []
    for item in load_existing_quotes(quote_dir):
        catalog.append(
            {
                "theme": item.theme,
                "usage": item.usage,
                "text": item.text,
                "tags": list(item.tags),
                "file_name": item.file_name,
            }
        )
    return catalog


def _is_safe_chunshe_quote(item: dict[str, Any]) -> bool:
    text = str(item.get("text") or "").strip()
    lowered = text.lower()
    if not text:
        return False
    if len(text) > 72:
        return False
    if any(token in lowered for token in QUOTE_BLOCK_KEYWORDS):
        return False
    if any(token in text for token in ("测试", "云端最终验收", "复制打开抖音", "模板在这里", "微信", "AI落地", "智能体")):
        return False
    return True


def select_chunshe_quote_candidates(
    *,
    entry_class: str,
    topic: dict[str, Any],
    explicit_theme: str = "",
    limit: int = 12,
) -> list[dict[str, Any]]:
    theme_order = allowed_quote_themes(entry_class, explicit_theme)
    haystack = " ".join(
        str(topic.get(key) or "").strip()
        for key in ("topic_title", "scene_trigger", "fear", "real_desire", "real_buy_point")
    )
    usage_pref = {
        "问题修复": ("警示", "观点", "开头钩子"),
        "信任怀疑": ("警示", "观点", "开头钩子"),
        "放松养护": ("开头钩子", "观点", "警示"),
        "本地找店": ("观点", "开头钩子", "警示"),
    }.get(entry_class, ("观点", "开头钩子", "警示"))

    scored: list[tuple[float, dict[str, Any]]] = []
    for item in load_chunshe_quote_catalog():
        if item.get("theme") not in theme_order:
            continue
        if not _is_safe_chunshe_quote(item):
            continue
        score = 0.0
        score += max(0, 6 - theme_order.index(str(item.get("theme") or ""))) * 2.0
        usage = str(item.get("usage") or "")
        if usage in usage_pref:
            score += max(0, 4 - usage_pref.index(usage)) * 1.5
        text = str(item.get("text") or "")
        for token in ("不要", "别", "不是", "而是", "安心", "放松", "松下来", "被照顾", "分寸", "边界"):
            if token in text:
                score += 0.4
        for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", haystack):
            if token and token in text:
                score += 0.6
        if "#标题" in item.get("tags", []):
            score += 0.3
        if "#选题" in item.get("tags", []):
            score += 1.2
        scored.append((score, dict(item)))

    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("text") or "")))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for score, item in scored:
        key = _normalize_key(str(item.get("text") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        item["score"] = round(score, 2)
        out.append(item)
        if len(out) >= max(1, limit):
            break
    return out


def render_chunshe_title_pool(
    *,
    seed_keyword: str,
    role: str,
    entry_class: str,
    topics: list[dict[str, Any]],
) -> str:
    lines = [
        f"# 标题池｜{seed_keyword or '椿舍选题'}（{role}）",
        "",
        f"关键词：{seed_keyword or '未指定'}  ",
        f"意图：{entry_class or '自动路由'}  ",
        "平台：小红书",
        "",
        f"## 标题与角度（{len(topics)}条）",
        "",
    ]
    for idx, item in enumerate(topics, start=1):
        title = str(item.get("topic_title") or "").strip()
        angle = str(item.get("angle_type") or "").strip()
        scene = str(item.get("scene_trigger") or "").strip()
        lines.append(f"{idx}. 标题：{title}  ")
        lines.append(f"角度：{angle}｜场景：{scene or '未标注'}  ")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
