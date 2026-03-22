#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import chunshe_engine
import chunshe_video_runtime as runtime
import feishu_skill_runner as runner


def test_enrich_chunshe_video_topic_adds_opening_skeleton() -> None:
    topic = {
        "entry_class": "信任怀疑",
        "angle_type": "防御拆解",
        "topic_title": "美容院做脸有用吗",
        "scene_trigger": "刚躺下，对面就开始把问题越说越重",
        "fear": "怕说不要之后还继续讲",
        "real_buy_point": "不是被说服，是先松下来",
        "store_rule_hint": "说不要，话题就停",
    }

    enriched = chunshe_engine.enrich_chunshe_video_topic(topic)

    assert enriched["opening_family"] == "冲突实景型"
    assert enriched["first_conflict_line"]
    assert enriched["scene_line"]
    assert enriched["translation_line"]
    assert enriched["boss_judgment_line"]
    assert enriched["store_rule_line"]
    assert enriched["low_pressure_offer_line"]
    assert enriched["landing_line"]


def test_enrich_chunshe_video_topic_prefers_review_phrases() -> None:
    topic = {
        "entry_class": "信任怀疑",
        "angle_type": "防御拆解",
        "seed_keyword": "美容院做脸有用吗",
        "topic_title": "美容院做脸有用吗",
        "scene_trigger": "",
        "fear": "怕推销",
        "real_buy_point": "说不要就停",
        "store_rule_hint": "说不要，话题就停",
    }

    enriched = chunshe_engine.enrich_chunshe_video_topic(topic)

    assert enriched["review_raw_opening"]
    assert enriched["review_raw_scene"]
    assert enriched["review_raw_landing"]
    assert enriched["first_conflict_line"] == enriched["review_raw_opening"]
    assert enriched["scene_line"] == enriched["review_raw_scene"]


def test_chunshe_theme_phrase_pack_uses_real_quote_sources() -> None:
    pack = chunshe_engine.select_chunshe_theme_phrase_pack(
        entry_class="信任怀疑",
        topic={"topic_title": "美容院做脸有用吗", "angle_type": "防御拆解"},
    )

    assert pack["theme_line"]
    assert pack["theme_examples"][0]["source_file"]
    assert pack["theme_examples"][0]["source_theme"] in {"人性与沟通", "系统与执行", "自我与哲学（次级）"}


def test_chunshe_theme_bank_has_enough_rotation_and_competitor_sources() -> None:
    payload = chunshe_engine.load_chunshe_theme_bank()
    trust_payload = payload["entry_classes"]["信任怀疑"]

    assert len(trust_payload["theme_lines"]) >= 5
    assert len(trust_payload["translation_lines"]) >= 5
    assert len(trust_payload["boss_judgment_lines"]) >= 5
    assert len(trust_payload["low_pressure_offer_lines"]) >= 5
    assert any(
        str(item.get("source_type") or "") == "对标链接库"
        for item in trust_payload["theme_lines"] + trust_payload["translation_lines"] + trust_payload["boss_judgment_lines"]
    )


def test_chunshe_priority_theme_bank_has_enough_rotation() -> None:
    payload = chunshe_engine.load_chunshe_priority_theme_bank()
    trust_payload = payload["entry_classes"]["信任怀疑"]

    assert len(trust_payload["theme_lines"]) >= 4
    assert len(trust_payload["translation_lines"]) >= 4
    assert len(trust_payload["boss_judgment_lines"]) >= 4
    assert len(trust_payload["low_pressure_offer_lines"]) >= 4
    assert any(
        str(item.get("source_type") or "") == "对标链接库"
        for item in trust_payload["theme_lines"] + trust_payload["translation_lines"]
    )


def test_chunshe_theme_pack_prefers_priority_bank_but_keeps_rotation() -> None:
    pack = chunshe_engine.select_chunshe_theme_phrase_pack(
        entry_class="信任怀疑",
        topic={
            "topic_id": "trust-demo",
            "topic_title": "美容院做脸有用吗",
            "seed_keyword": "美容院做脸有用吗",
            "angle_type": "防御拆解",
            "scene_trigger": "刚躺下就开始推销",
        },
    )

    assert len(pack["theme_examples"]) >= 5
    assert len(pack["translation_examples"]) >= 5
    assert pack["theme_line"]
    assert any(str(item.get("priority") or "") == "high" for item in pack["theme_examples"][:4])


def test_enriched_chunshe_topic_fills_owner_lines_when_theme_pack_is_theme_only() -> None:
    topic = chunshe_engine.enrich_chunshe_video_topic(
        topic={
            "entry_class": "本地找店",
            "topic_id": "local-demo",
            "topic_title": "吴江美容院",
            "seed_keyword": "吴江美容院",
            "angle_type": "本地决策",
            "scene_trigger": "第一次找店怕白跑",
        }
    )

    selected = {
        topic["translation_line"],
        topic["boss_judgment_line"],
        topic["low_pressure_offer_line"],
        topic["landing_line"],
    }

    assert all(selected)
    assert len(selected) == 4


def test_trust_topic_landing_prefers_result_not_complaint() -> None:
    topic = chunshe_engine.enrich_chunshe_video_topic(
        {
            "entry_class": "信任怀疑",
            "angle_type": "防御拆解",
            "seed_keyword": "美容院做脸有用吗",
            "topic_title": "美容院做脸有用吗",
            "scene_trigger": "刚躺下就开始推销",
            "fear": "怕说不要以后还继续讲",
            "real_buy_point": "不是被说服，是先松下来",
            "store_rule_hint": "说不要，话题就停",
        }
    )

    landing_line = str(topic.get("landing_line") or "")
    assert landing_line
    assert "敷衍" not in landing_line
    assert "办卡" not in landing_line


def test_chunshe_video_draft_prompt_targets_short_video_not_article() -> None:
    topic = chunshe_engine.enrich_chunshe_video_topic(
        {
            "entry_class": "信任怀疑",
            "angle_type": "防御拆解",
            "topic_title": "美容院做脸有用吗",
            "scene_trigger": "刚躺下，对面就开始把问题越说越重",
            "fear": "怕说不要之后还继续讲",
            "real_buy_point": "不是被说服，是先松下来",
            "store_rule_hint": "说不要，话题就停",
        }
    )
    prompt = runtime.build_chunshe_video_draft_package_prompt(
        role="李可",
        output_type="精简发布版",
        brief="椿舍内容：关键词=美容院做脸有用吗",
        mode="平衡",
        source_pack={"brief_extract": {"seed_keyword": "美容院做脸有用吗"}},
        topic=topic,
    )

    # story-driven requirements
    assert "开头是一个具体的人的具体经历" in prompt
    assert "向往感占正文 50% 以上" in prompt
    assert "老板要多说话" in prompt
    assert "叙事比例 2-5-3" in prompt
    # inline examples
    assert "满分稿示例 A" in prompt
    assert "满分稿示例 B" in prompt
    # old mechanical instruction should be gone
    assert "正文默认要同时出现 4 个块" not in prompt
    assert "先写她为什么会搜这个词" not in prompt


def test_validate_chunshe_video_markdown_flags_article_intro() -> None:
    markdown = """# 标题
很多人搜美容院做脸有用吗

## 备选标题
- 备选1
- 备选2

# 正文
很多人搜美容院做脸有用吗。
她会搜这个词，是因为她怕白花钱。
表面上在问效果。
本质上在问边界。
你可以评论我。
"""

    issues, advisories = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "前 3 句仍像文章说明" in issues
    assert "前 6 句缺少顾客原话" in issues
    assert any(item.startswith("正文出现 CTA 黑名单") for item in issues)


def test_validate_chunshe_video_markdown_flags_literary_tone() -> None:
    markdown = """# 标题
做脸怕的不是没效果

## 备选标题
- 说不要就停
- 先别急着做

# 正文
做脸最烦的，不是没效果。
镜子一照，状态不对，人先绷住了。
还没开始，就怕自己又被耗掉。
她想确认的，就一件事：说不要能不能停。
在椿舍，说不要，话题就在这里停住。
这趟做完，人先松下来。
"""

    issues, advisories = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "正文有点文艺，不够直白" in issues


def test_validate_chunshe_video_markdown_requires_oral_core_beats() -> None:
    topic = {
        "boss_judgment_line": "你要是来我这儿还得防着，那这店就白开了。",
        "low_pressure_offer_line": "第一次来，别做太多，先把这一趟安安稳稳做完。",
    }
    markdown = """# 标题
做脸最烦的不是没效果

## 备选标题
- 先看能不能拒绝
- 别再被推销了

# 正文
感觉从头到尾都在推销办卡。
说不买了就很敷衍结束了。
很多人后来不是不做脸了，
是懒得再经历一次这种过程。
说到底还是要看分寸。
至少别做完整个人更累。
"""
    issues, advisories = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST, topic)

    assert "前 6 句缺少顾客原话" in issues
    assert "中段服务动作少于 2 个" in issues
    assert "结尾不是低压收口" in issues


def test_validate_chunshe_video_markdown_flags_teacher_tone() -> None:
    markdown = """# 标题
做脸最烦的不是没效果

## 备选标题
- 先看能不能拒绝
- 别再被推销了

# 正文
感觉从头到尾都在推销办卡。
说不买了就很敷衍结束了。
对于其他人，只做筛选，不去教育。
做好当下该做的事情，就已经非常非常好了。
说不要，话题就在这里停住。
改变的初期总是不太舒服的。
"""
    issues, advisories = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "正文仍有老师口气" in issues


def test_validate_chunshe_video_markdown_flags_repeated_role_terms() -> None:
    markdown = """# 标题
做脸先别看效果

## 备选标题
- 先看会不会停
- 先看对面会不会停

# 正文
刚躺下，对面的美容师就开始推销。
你说先不要，美容师还一直往下讲。
美容师讲完项目，又开始问你办不办卡。
很多人后来不是不做脸了，是懒得再受一遍这种气。
我最烦的不是顾客问太多，是还没开始做，人就先紧了。
说不要，话题就在这里停住。
第一次来，先把这一趟安安稳稳做完。
"""
    issues, advisories = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "称谓重复，读起来有点吵" in advisories


def test_ensure_chunshe_video_core_lines_only_dedupes_and_does_not_inject() -> None:
    topic = {
        "first_conflict_line": "感觉从头到尾都在推销办卡。",
        "scene_line": "说不买了就很敷衍结束了。",
        "translation_line": "很多人不是怕花钱，是怕花了钱还得受气。",
        "boss_judgment_line": "你要是来我这儿还得防着，那这店就白开了。",
        "boss_response": "我说行，你先闭眼，做完叫你。",
        "store_rule_line": "说不要，我们就停。",
        "low_pressure_offer_line": "第一次来，别做太多，先把这一趟安安稳稳做完。",
        "low_bar_ending": "做完出来，起码别觉得自己受了委屈。",
        "landing_line": "别还没变好，人先被搞累了。",
    }

    # body completely lacks boss voice and low-pressure markers
    body = "\n".join(
        [
            "感觉从头到尾都在推销办卡。",
            "说不买了就很敷衍结束了。",
            "很多人后来不是不做脸了。",
            "是懒得再经历一次这种过程。",
        ]
    )

    fixed = runtime.ensure_chunshe_video_core_lines(body, topic, output_type="精简发布版")

    assert fixed == body


def test_ensure_chunshe_video_core_lines_preserves_story_body() -> None:
    """Softened ensure_core_lines should NOT rearrange a well-written story body."""
    topic = {
        "first_conflict_line": "感觉从头到尾都在推销办卡。",
        "scene_line": "说不买了就很敷衍结束了。",
        "boss_judgment_line": "你要是来我这儿还得防着，那这店就白开了。",
        "boss_response": "我说行，你先闭眼，做完叫你。",
        "low_pressure_offer_line": "第一次来，别做太多，先把这一趟安安稳稳做完。",
        "low_bar_ending": "做完出来，起码别觉得自己受了委屈。",
    }

    # A good story body that already has boss voice and low-pressure
    body = "\n".join([
        "上周有个姑娘第一次来做脸，",
        "刚躺下就跟我说，姐，你别推销啊。",
        "我说行，你先闭眼，做完叫你。",
        "做到一半我进去瞅了一眼，",
        "嗯，睡着了。",
        "在我这做脸嘛，真不用那么紧张。",
        "忙了一天了，先躺一会儿呗。",
    ])

    fixed = runtime.ensure_chunshe_video_core_lines(body, topic, output_type="精简发布版")

    # body should be preserved unchanged — no forced injection
    assert fixed == body


def test_ensure_chunshe_video_core_lines_does_not_force_inject_landing() -> None:
    """Softened version should NOT force-inject landing_line or translation_line."""
    topic = {
        "entry_class": "信任怀疑",
        "first_conflict_line": "从头到尾都在推销办卡。",
        "scene_line": "说不买了就很敷衍结束了。",
        "translation_line": "很多人不是怕花钱，是怕花了钱还得受气。",
        "boss_judgment_line": "你要是来我这儿还得防着，那这店就白开了。",
        "boss_response": "我说行，你先闭眼，做完叫你。",
        "store_rule_line": "说不要，话题就在这里停住。",
        "low_pressure_offer_line": "第一次来，别做太多，先把这一趟安安稳稳做完。",
        "low_bar_ending": "做完出来，起码别觉得自己受了委屈。",
        "landing_line": "一说不买了，就敷衍结束了。",
    }

    # body already has boss markers ("我说") and low-pressure markers ("呗")
    body = "\n".join(
        [
            "从头到尾都在推销办卡。",
            "说不买了就很敷衍结束了。",
            "我说行，你先闭眼，做完叫你。",
            "忙了一天了，先躺一会儿呗。",
        ]
    )

    fixed = runtime.ensure_chunshe_video_core_lines(body, topic, output_type="精简发布版")

    # should NOT inject landing_line or translation_line
    assert "一说不买了，就敷衍结束了。" not in fixed
    assert "很多人不是怕花钱" not in fixed


def test_polish_prompt_uses_topic_view_not_full_topic_payload() -> None:
    topic = {
        "topic_id": "trust-1",
        "topic_title": "做脸先看敢不敢停",
        "seed_keyword": "美容院做脸有用吗",
        "entry_class": "信任怀疑",
        "angle_type": "防御拆解",
        "scene_trigger": "刚躺下就开始推销",
        "fear": "怕一躺下就被推项目",
        "real_desire": "想安心做一次",
        "real_buy_point": "说不要能停",
        "store_rule_hint": "说不要我们就停",
        "opening_family": "冲突实景型",
        "opening_scene": "刚躺下",
        "first_conflict_line": "感觉从头到尾都在推销办卡。",
        "scene_line": "说不买了就很敷衍结束了。",
        "theme_line": "很多人不是怕花钱，是怕花了钱还得受气。",
        "translation_line": "很多人后来不是不做脸了，是懒得再经历一次这种过程。",
        "boss_judgment_line": "你要是来我这儿还得防着，那这店就白开了。",
        "pivot_line": "所以我现在先看一句话：你说不要，她会不会停。",
        "store_rule_line": "说不要，话题就在这里停住。",
        "low_pressure_offer_line": "第一次来，别做太多，先把这一趟安安稳稳做完。",
        "landing_line": "做完这一趟，别让人更防着。",
        "theme_examples": ["不该出现在 prompt 里"],
        "translation_examples": ["也不该出现在 prompt 里"],
    }

    prompt = runtime.build_chunshe_video_polish_package_prompt(
        markdown_text="# 标题\n测试\n\n# 正文\n感觉从头到尾都在推销办卡。",
        topic=topic,
        role="李可",
        output_type="精简发布版",
        mode="快速",
        source_pack={"seed_keyword": "美容院做脸有用吗"},
        quote_candidate=None,
    )

    assert '"theme_examples"' not in prompt
    assert '"translation_examples"' not in prompt


def test_draft_prompt_uses_slim_source_pack_view() -> None:
    topic = {
        "topic_id": "trust-1",
        "topic_title": "做脸先看敢不敢停",
        "seed_keyword": "美容院做脸有用吗",
        "entry_class": "信任怀疑",
        "angle_type": "防御拆解",
        "scene_trigger": "刚躺下就开始推销",
        "fear": "怕一躺下就被推项目",
        "real_desire": "想安心做一次",
        "real_buy_point": "说不要能停",
        "store_rule_hint": "说不要我们就停",
        "opening_family": "冲突实景型",
        "opening_scene": "刚躺下",
        "first_conflict_line": "感觉从头到尾都在推销办卡。",
        "scene_line": "说不买了就很敷衍结束了。",
        "theme_line": "很多人不是怕花钱，是怕花了钱还得受气。",
        "translation_line": "很多人后来不是不做脸了，是懒得再经历一次这种过程。",
        "boss_judgment_line": "你要是来我这儿还得防着，那这店就白开了。",
        "pivot_line": "所以我现在先看一句话：你说不要，她会不会停。",
        "store_rule_line": "说不要，话题就在这里停住。",
        "low_pressure_offer_line": "第一次来，别做太多，先把这一趟安安稳稳做完。",
        "landing_line": "做完这一趟，别让人更防着。",
    }
    source_pack = {
        "business_facts": {"store_positioning": "椿舍在吴江", "allowed_services": ["基础清洁"], "review_boundary": [], "output_goal": {}},
        "hard_rules": {"banned_openings": [], "banned_patterns": [], "body_cta_blacklist": [], "opening_guards": [], "question_guards": []},
        "brief_extract": {"seed_keyword": "美容院做脸有用吗", "entry_class": "信任怀疑", "role": "李可", "output_type": "精简发布版", "mode": "快速"},
        "topic_helpers": {"high_boundary": False, "store_rule_options": ["说不要就停"], "ending_function_options": ["安心感"]},
        "review_language": {"principle": "保留差评原话", "bucket": "sales", "opening_examples": ["感觉从头到尾都在推销办卡。"], "scene_examples": [], "landing_examples": []},
        "theme_language": {
            "principle": "主题句只做方向参考",
            "theme_examples": ["不该出现在 prompt 里"],
            "translation_examples": ["也不该出现在 prompt 里"],
            "boss_judgment_examples": ["更不该出现在 prompt 里"],
            "low_pressure_offer_examples": ["都不该出现在 prompt 里"],
        },
    }

    prompt = runtime.build_chunshe_video_draft_package_prompt(
        role="李可",
        output_type="精简发布版",
        brief="椿舍内容：关键词=美容院做脸有用吗",
        mode="快速",
        source_pack=source_pack,
        topic=topic,
    )

    assert '"theme_examples"' not in prompt
    assert '"translation_examples"' not in prompt
    assert '"boss_judgment_examples"' not in prompt
    assert '"low_pressure_offer_examples"' not in prompt


def test_runner_keeps_single_active_chunshe_definition() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")

    assert source.count("def _build_chunshe_draft_package_prompt(") == 1
    assert source.count("def _build_chunshe_polish_package_prompt(") == 1
    assert source.count("def _validate_chunshe_markdown(") == 1
    assert source.count("def _run_chunshe_single_topic(") == 1
    assert source.count("def _run_chunshe_staged_task(") == 1


def test_chunshe_video_benchmark_has_minimum_cases() -> None:
    benchmark_path = (
        Path(__file__).resolve().parents[3]
        / "skills"
        / "客户交付"
        / "椿舍门店专用"
        / "assets"
        / "短视频评测集.json"
    )

    payload = json.loads(benchmark_path.read_text(encoding="utf-8"))

    assert len(payload["cases"]) >= 12


def test_enriched_topic_includes_body_ready_spoken_fields() -> None:
    topic = chunshe_engine.enrich_chunshe_video_topic(
        {
            "entry_class": "信任怀疑",
            "angle_type": "防御拆解",
            "topic_title": "美容院做脸有用吗",
            "seed_keyword": "美容院做脸有用吗",
        }
    )

    assert topic["scene_opener"]
    assert topic["boss_response"]
    assert topic["longing_moment"]
    assert topic["low_bar_ending"]
    # all four should be different strings
    assert len({topic["scene_opener"], topic["boss_response"], topic["longing_moment"], topic["low_bar_ending"]}) == 4


def test_match_chunshe_topic_seed_examples_prioritizes_quote_topic_seed(monkeypatch) -> None:
    monkeypatch.setattr(
        chunshe_engine,
        "load_chunshe_quote_catalog",
        lambda: [
            {
                "theme": "人性与沟通",
                "usage": "观点",
                "text": "不扫兴，就是情绪价值的体现",
                "tags": ["#选题", "#标题"],
                "file_name": "04-人性与沟通.md",
            }
        ],
    )
    monkeypatch.setattr(
        chunshe_engine,
        "load_chunshe_topic_seed_pool",
        lambda: [
            {
                "topic_id": "LOCAL-01",
                "seed_keyword": "做脸边界感",
                "topic_title": "做脸先看对面会不会停",
                "entry_class": "信任怀疑",
                "angle_type": "防御拆解",
                "scene_trigger": "刚躺下就开始讲项目",
                "store_rule_hint": "说不要，话题就停",
            }
        ],
    )

    topics = chunshe_engine.match_chunshe_topic_seed_examples("不扫兴", "信任怀疑", limit=3)

    assert topics
    assert topics[0]["source_type"] == "quote_topic_seed"
    assert topics[0]["quote_seed_text"] == "不扫兴，就是情绪价值的体现"
    assert topics[0]["title_preserve_core_feel"] is True


def test_match_chunshe_topic_seed_examples_downgrades_teacher_tone_quote(monkeypatch) -> None:
    monkeypatch.setattr(
        chunshe_engine,
        "load_chunshe_quote_catalog",
        lambda: [
            {
                "theme": "人性与沟通",
                "usage": "观点",
                "text": "对于其他人，只做筛选，不去教育。",
                "tags": ["#选题"],
                "file_name": "04-人性与沟通.md",
            }
        ],
    )
    monkeypatch.setattr(chunshe_engine, "load_chunshe_topic_seed_pool", lambda: [])

    topics = chunshe_engine.match_chunshe_topic_seed_examples("筛选", "信任怀疑", limit=3)

    assert topics == []


def test_match_chunshe_topic_seed_examples_keeps_quote_seed_when_keyword_overlaps(monkeypatch) -> None:
    monkeypatch.setattr(
        chunshe_engine,
        "load_chunshe_quote_catalog",
        lambda: [
            {
                "theme": "人性与沟通",
                "usage": "观点",
                "text": "不扫兴，就是情绪价值的体现",
                "tags": ["#选题", "#标题"],
                "file_name": "04-人性与沟通.md",
            },
            {
                "theme": "系统与执行",
                "usage": "观点",
                "text": "几乎任何事情都是越做越简单，越想越困难",
                "tags": ["#选题"],
                "file_name": "03-系统与执行.md",
            },
        ],
    )
    monkeypatch.setattr(
        chunshe_engine,
        "load_chunshe_topic_seed_pool",
        lambda: [
            {
                "topic_id": "LOCAL-01",
                "seed_keyword": "做脸边界感",
                "topic_title": "做脸先看对面会不会停",
                "entry_class": "信任怀疑",
                "angle_type": "防御拆解",
                "scene_trigger": "刚躺下就开始讲项目",
                "store_rule_hint": "说不要，话题就停",
            }
        ],
    )

    topics = chunshe_engine.match_chunshe_topic_seed_examples("不扫兴", "信任怀疑", limit=3)

    assert topics
    assert topics[0]["source_type"] == "quote_topic_seed"
    assert topics[0]["quote_seed_text"] == "不扫兴，就是情绪价值的体现"


def test_match_chunshe_topic_seed_examples_rejects_generic_quote_hijack_for_relaxation(monkeypatch) -> None:
    monkeypatch.setattr(
        chunshe_engine,
        "load_chunshe_quote_catalog",
        lambda: [
            {
                "theme": "自我与哲学（次级）",
                "usage": "观点",
                "text": "@To: 你永远不会错过属于你的任何东西 命运会把属于你的东西推到你的面前",
                "tags": ["#选题"],
                "file_name": "91-自我与哲学（次级）.md",
            },
            {
                "theme": "人性与沟通",
                "usage": "观点",
                "text": "你在谁面前最放松，谁最爱你",
                "tags": ["#选题", "#标题"],
                "file_name": "04-人性与沟通.md",
            },
        ],
    )
    monkeypatch.setattr(
        chunshe_engine,
        "load_chunshe_topic_seed_pool",
        lambda: [
            {
                "topic_id": "CS009",
                "seed_keyword": "肩颈按摩",
                "topic_title": "上班久了才知道，很多人做肩颈不是矫情，是撑太久了",
                "entry_class": "放松养护",
                "angle_type": "关系/身份翻译",
                "scene_trigger": "会议开完，肩膀还顶着，回家都不想讲话",
                "store_rule_hint": "先看状态，再决定做多轻",
            }
        ],
    )

    topics = chunshe_engine.match_chunshe_topic_seed_examples("肩颈一硬", "放松养护", limit=3)

    assert topics
    assert topics[0]["source_type"] != "quote_topic_seed"
    assert topics[0]["topic_id"] == "CS009"


def test_match_chunshe_topic_seed_examples_prefers_local_store_decision_seed_over_generic_quote(monkeypatch) -> None:
    monkeypatch.setattr(
        chunshe_engine,
        "load_chunshe_quote_catalog",
        lambda: [
            {
                "theme": "系统与执行",
                "usage": "警示",
                "text": "你不要跟着人群走。你转向质量而不是数量，停止与任何人竞争",
                "tags": ["#选题"],
                "file_name": "03-系统与执行.md",
            }
        ],
    )
    monkeypatch.setattr(
        chunshe_engine,
        "load_chunshe_topic_seed_pool",
        lambda: [
            {
                "topic_id": "CS007",
                "seed_keyword": "吴江美容院",
                "topic_title": "吴江美容院先看哪三条，不然第一次白跑",
                "entry_class": "本地找店",
                "angle_type": "本地决策",
                "scene_trigger": "点评看了一圈，越看越怕踩雷",
                "store_rule_hint": "流程、时长、边界提前说清楚",
            }
        ],
    )

    topics = chunshe_engine.match_chunshe_topic_seed_examples("吴江美容院先看哪三条", "本地找店", limit=3)

    assert topics
    assert topics[0]["source_type"] != "quote_topic_seed"
    assert topics[0]["topic_id"] == "CS007"


def test_match_chunshe_topic_seed_examples_promotes_synthetic_brief_seed_for_phrase_brief(monkeypatch) -> None:
    monkeypatch.setattr(chunshe_engine, "load_chunshe_quote_catalog", lambda: [])
    monkeypatch.setattr(
        chunshe_engine,
        "load_chunshe_topic_seed_pool",
        lambda: [
            {
                "topic_id": "CS015",
                "seed_keyword": "美容院怎么选不会被推销",
                "topic_title": "美容院怎么选不会被推销，先看她能不能在你说不要后停住",
                "entry_class": "信任怀疑",
                "angle_type": "规矩托底",
                "scene_trigger": "项目还没开始，心里已经先绷起来了",
                "store_rule_hint": "推荐可以有，但拒绝必须安全",
            },
            {
                "topic_id": "CS008",
                "seed_keyword": "SPA",
                "topic_title": "很多人搜SPA，不是想精致，是想先不紧绷",
                "entry_class": "放松养护",
                "angle_type": "向往画面",
                "scene_trigger": "下班快九点，肩颈硬得不想说话",
                "store_rule_hint": "你说今天只想安静待会儿，就按这个来",
            },
        ],
    )

    topics = chunshe_engine.match_chunshe_topic_seed_examples("今天能不能先别跟我讲话", "放松养护", limit=3)

    assert topics
    assert topics[0]["source_type"] == "synthetic_seed"
    assert topics[0]["status"] == "brief_seed"
    assert topics[0]["topic_title"] == "今天能不能先别跟我讲话"
    assert topics[0]["entry_class"] == "放松养护"


def test_validate_flags_missing_longing_scene() -> None:
    markdown = """# 标题
做脸先别看效果

## 备选标题
- 先看能不能停
- 先看对面会不会停

# 正文
前两天有个姑娘做完脸跟我说了句话。
她说感觉从头到尾都在推销办卡。
说不买了就很敷衍结束了。
我说你放心，说不要就是不要。
第一次来，先把这一趟安安稳稳做完。
"""
    issues, advisories = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "缺少向往画面" in advisories


def test_validate_flags_missing_story_element() -> None:
    markdown = """# 标题
做脸先别看效果

## 备选标题
- 先看能不能停
- 先看对面会不会停

# 正文
感觉从头到尾都在推销办卡。
说不买了就很敷衍结束了。
做完出来是舒服的，不是松口气的。
行，你先闭眼，做完叫你。
做完出来，起码心里别堵着就行了。
"""
    issues, advisories = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    # "缺少具体人" check was removed (prescriptive whitelist)
    assert "缺少具体人" not in issues
    assert "缺少具体人" not in advisories


def test_validate_flags_excessive_not_a_is_b() -> None:
    markdown = """# 标题
做脸先别看效果

## 备选标题
- 先看能不能停
- 先看对面会不会停

# 正文
前两天有个姑娘做完脸跟我说了句话。
她不是怕花钱，是怕花了钱还得受气。
我说你放心，在我这做脸嘛，
不是要被教育，是想被好好接住。
做完出来，起码心里别堵着就行了。
"""
    issues, advisories = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert any("不是A，是B" in adv for adv in advisories)


def test_owner_spoken_bank_loader() -> None:
    bank = chunshe_engine.load_chunshe_owner_spoken_bank()
    assert "信任怀疑" in bank
    assert "问题修复" in bank
    assert "放松养护" in bank
    assert "本地找店" in bank
    for entry_class in ("信任怀疑", "问题修复", "放松养护", "本地找店"):
        section = bank[entry_class]
        assert len(section["scene_openers"]) >= 5
        assert len(section["boss_responses"]) >= 5
        assert len(section["longing_moments"]) >= 5
        assert len(section["low_bar_endings"]) >= 4


def test_extract_chunshe_video_title_reads_final_title_block() -> None:
    markdown = """# 标题
做完脸她说了句话我记到现在

## 备选标题
- 备选一
- 备选二

# 正文
正文
"""

    assert runtime.extract_chunshe_video_title(markdown) == "做完脸她说了句话我记到现在"


def test_normalize_chunshe_video_markdown_strips_comment_and_reply_sections() -> None:
    markdown = """# 标题
主标题

## 备选标题
- 备选一
- 备选二

# 正文
正文第一行。
正文第二行。

# 置顶评论
这块不再进主稿。

# 回复模板
这块也不再进主稿。
"""

    normalized = runtime.normalize_chunshe_video_markdown(markdown, ["主标题", "备选一", "备选二"])

    assert "# 置顶评论" not in normalized
    assert "# 回复模板" not in normalized
    assert "# 正文" in normalized


def test_cover_template_prioritizes_handwrite_over_dialogue() -> None:
    assert runtime._select_cover_template("做完脸她说了句话我记到现在") == "B"


def test_cover_template_uses_dialogue_for_question_style_title() -> None:
    assert runtime._select_cover_template("做完脸冒了几颗痘，她问我是不是做坏了") == "C"
    assert runtime._select_cover_template("美容院做脸有用吗") == "A"


def test_generate_cover_prompt_follows_xhs_single_cover_structure() -> None:
    payload = runtime.generate_cover_prompt("说了不买，手上的动作就变了")

    assert payload["template"] == "A"
    assert payload["highlight"] == "动作"
    assert payload["prompt"].startswith("画幅比例3:4竖版。")
    assert "【图片类型】小红书封面" in payload["prompt"]
    assert "【中文字体描述】" in payload["prompt"]
    assert "【结构约束】只做小红书单张封面" in payload["prompt"]
    assert "6 页信息图" not in payload["prompt"]
    assert "P2-P6" not in payload["prompt"]
    assert "CTA 页" not in payload["prompt"]
    assert "英文字母" in payload["negative_prompt"]


def test_render_chunshe_cover_sidecar_contains_required_sections() -> None:
    payload = runtime.generate_cover_prompt("从盛泽专门过来做脸，她说这次不用换了")
    sidecar = runtime.render_chunshe_cover_sidecar("从盛泽专门过来做脸，她说这次不用换了", payload)

    assert "# 封面提示词" in sidecar
    assert "## 基础信息" in sidecar
    assert "## 中文提示词" in sidecar
    assert "## 负面提示词" in sidecar
    assert "## 结构说明" in sidecar
    assert "标题高亮词：不用换了" in sidecar


def test_render_chunshe_publish_pack_contains_schedule_tags_and_steps() -> None:
    pack = runtime.render_chunshe_publish_pack(
        date_str="2026-03-17",
        role="李可",
        topic_items=[
            {
                "title": "说了不买，手上的动作就变了",
                "path": "生成内容/2026-03-17/李可-20260317-01-说了不买.md",
                "cover_path": "生成内容/2026-03-17/李可-20260317-01-说了不买.cover.md",
                "cover_template": "A 暖调文字海报",
                "cover_highlight": "动作",
            }
        ],
    )

    assert "# 椿舍发布包" in pack
    assert "工作日 12:00-13:00" in pack
    assert "#吴江美容院" in pack
    assert "打开 Gemini 或 Nano Banana Pro" in pack


def test_chunshe_video_draft_prompt_targets_short_video_not_article() -> None:
    topic = chunshe_engine.enrich_chunshe_video_topic(
        {
            "entry_class": "信任怀疑",
            "angle_type": "防御拆解",
            "topic_title": "美容院做脸有用吗",
            "scene_trigger": "刚躺下，对面就开始把问题越说越重",
            "fear": "怕说不要之后还继续讲",
            "real_buy_point": "不是被说服，是先松下来",
            "store_rule_hint": "说不要，话题就停",
        }
    )
    prompt = runtime.build_chunshe_video_draft_package_prompt(
        role="李可",
        output_type="精简发布版",
        brief="椿舍内容：关键词=美容院做脸有用吗",
        mode="平衡",
        source_pack={"brief_extract": {"seed_keyword": "美容院做脸有用吗"}},
        topic=topic,
    )

    assert '"hook_line"' in prompt
    assert '"customer_quote"' in prompt
    assert '"beautician_actions"' in prompt
    assert '"body_markdown"' in prompt
    assert "前 3 句必须完成" in prompt
    assert "男老板口吻" in prompt
    assert "正文默认要同时出现 4 个块" not in prompt
    assert "先写她为什么会搜这个词" not in prompt


def test_validate_flags_missing_longing_scene() -> None:
    markdown = """# 标题
做脸先别看效果

## 备选标题
- 先看能不能停
- 先看对面会不会停

# 正文
前两天有个姑娘做完脸跟我说了句话。
她说感觉从头到尾都在推销办卡。
说不买了就很敷衍结束了。
我说你放心，说不要就是不要。
第一次来，先把这一趟安安稳稳做完。
"""
    issues, advisories = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "向往感还可以更强" in advisories


def test_normalize_chunshe_video_markdown_salvages_body_when_polish_omits_body_header() -> None:
    markdown = """# 标题
有些人来做脸，第一句不是问项目

她一进门就问今天能不能先别跟她讲话。
美容师只问水温和力度。
做完她说今天这趟没白跑。
"""

    normalized = runtime.normalize_chunshe_video_markdown(
        markdown,
        ["有些人来做脸，第一句不是问项目", "备选一", "备选二"],
    )

    assert "# 标题" in normalized
    assert "## 备选标题" in normalized
    assert "# 正文" in normalized
    assert "她一进门就问今天能不能先别跟她讲话。" in normalized
    assert "做完她说今天这趟没白跑。" in normalized


def test_validate_quote_seed_accepts_store_rule_as_translated_judgment() -> None:
    topic = {
        "source_type": "quote_topic_seed",
        "quote_seed_text": "不扫兴，就是情绪价值的体现",
        "translation_line": "很多人后来不是不做脸了，是懒得再受一遍这种气。",
        "boss_judgment_line": "顾客第一次来，先让她把戒备放下，比讲一堆项目重要。",
        "store_rule_line": "说不要，话题就停。",
        "theme_line": "不扫兴，先让人躺得住。",
    }
    markdown = """# 标题
说了不要，话题就在这停

## 备选标题
- 刚躺下就怕听见办卡
- 人不用一直防着，才敢把眼睛闭上

# 正文
她包还没放下，就先问今天不会一直讲办卡吧。
上次在别家，她原话就是：感觉从头到尾都在推销办卡。
说完她才躺下，肩膀还是提着的，手也一直攥着。
我只回她一句，你说不要，话题就在这停。
美容师先把床头垫高一点，又把空调风口拨开。
毛巾盖到小腿，先试水温，再敷脸。
人不用一直防着，才敢把眼睛闭上。
做完她把镜子放下，只说今天总算没白跑。
"""

    issues, advisories = runtime.validate_chunshe_video_markdown(
        markdown,
        runner.CHUNSHE_BODY_CTA_BLACKLIST,
        topic,
    )

    assert "金句判断没落进正文" not in issues


def test_validate_flags_softened_review_terms_when_topic_requires_raw_words() -> None:
    topic = {
        "review_raw_opening": "姐，你今天别给我推销，也别让我办卡。",
        "review_raw_scene": "她说上次去别家，明明都说不买了，对面还是一轮一轮往下讲。",
        "first_conflict_line": "姐，你今天别给我推销，也别让我办卡。",
    }
    markdown = """# 标题
做脸前最怕的不是花钱

## 备选标题
- 她刚躺下就先设防
- 做个脸之前先想怎么拒绝

# 正文
前两天有个姑娘刚躺下就看着我。
她说之前做脸最烦的，就是对面一直讲项目。
明明说了先不要，还是会一直往下聊。
我说你先别紧张，今天做什么就做什么。
做到一半她肩膀慢慢松了。
"""
    issues, advisories = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST, topic)

    assert "差评原词被磨平" in issues


def test_validate_requires_quote_seed_theme_translation() -> None:
    topic = {
        "source_type": "quote_topic_seed",
        "quote_seed_text": "不扫兴，就是情绪价值的体现",
        "translation_line": "你来做脸，不想聊就不聊，人先松下来更重要。",
        "boss_judgment_line": "在我这，说不要就停，不拿气氛压人。",
        "theme_line": "不扫兴，先让人躺得住。",
    }
    markdown = """# 标题
做脸的时候别扫兴，人真的躺得住

## 备选标题
- 她刚躺下就先问今天会不会一直讲项目
- 说了先不要以后，屋里还能不能安静下来

# 正文
前两天有个姑娘刚躺下就问我一句。
她说之前去别家，总怕自己一说不要，气氛就冷掉。
我说你先躺着，今天按你现在的状态慢慢来。
做到一半她肩膀才慢慢往下掉。
做完她照了照镜子，说今天舒服一点。
"""
    issues, advisories = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST, topic)

    assert "金句判断没落进正文" in issues


def test_extract_chunshe_video_draft_body_prefers_body_markdown() -> None:
    payload = {
        "body_markdown": "# 正文\n她刚坐下就说今天别聊项目。\n美容师只问水温和力度。\n做完她说这趟没白跑。",
        "draft_body": "旧正文",
    }

    body = runtime.extract_chunshe_video_draft_body(payload)

    assert "今天别聊项目" in body
    assert "旧正文" not in body


def test_ensure_chunshe_video_core_lines_does_not_force_inject_landing() -> None:
    topic = {
        "entry_class": "信任怀疑",
        "first_conflict_line": "从头到尾都在推销办卡。",
        "scene_line": "说不买了就很敷衍结束了。",
        "translation_line": "很多人不是怕花钱，是怕花了钱还得受气。",
        "boss_judgment_line": "你要是来我这儿还得防着，那这店就白开了。",
        "boss_response": "我说行，你先闭眼，做完叫你。",
        "store_rule_line": "说不要，话题就在这里停住。",
        "low_pressure_offer_line": "第一次来，别做太多，先把这一趟安安稳稳做完。",
        "low_bar_ending": "做完出来，起码别觉得自己受了委屈。",
        "landing_line": "一说不买了，就敷衍结束了。",
    }
    body = "\n".join(
        [
            "从头到尾都在推销办卡。",
            "说不买了就很敷衍结束了。",
            "我说行，你先闭眼，做完叫你。",
            "忙了一天了，先躺一会儿呗。",
        ]
    )

    fixed = runtime.ensure_chunshe_video_core_lines(body, topic, output_type="精简发布版")

    assert "一说不买了，就敷衍结束了。" not in fixed
    assert "很多人不是怕花钱，是怕花了钱还得受气。" not in fixed


def test_polish_prompt_uses_topic_view_not_full_topic_payload() -> None:
    topic = {
        "topic_id": "trust-1",
        "topic_title": "做脸先看敢不敢停",
        "seed_keyword": "美容院做脸有用吗",
        "entry_class": "信任怀疑",
        "angle_type": "防御拆解",
        "scene_trigger": "刚躺下就开始推销",
        "fear": "怕一躺下就被推项目",
        "real_desire": "想安心做一次",
        "real_buy_point": "说不要能停",
        "store_rule_hint": "说不要我们就停",
        "opening_family": "冲突实景型",
        "opening_scene": "刚躺下",
        "first_conflict_line": "感觉从头到尾都在推销办卡。",
        "scene_line": "说不买了就很敷衍结束了。",
        "theme_line": "很多人不是怕花钱，是怕花了钱还得受气。",
        "translation_line": "很多人后来不是不做脸了，是懒得再经历一次这种过程。",
        "boss_judgment_line": "你要是来我这儿还得防着，那这店就白开了。",
        "pivot_line": "所以我现在先看一句话：你说不要，她会不会停。",
        "store_rule_line": "说不要，话题就在这里停住。",
        "low_pressure_offer_line": "第一次来，别做太多，先把这一趟安安稳稳做完。",
        "landing_line": "做完这一趟，别让人更防着。",
        "theme_examples": ["不该出现在脚本骨架里"],
    }

    prompt = runtime.build_chunshe_video_polish_package_prompt(
        markdown_text="# 标题\n测试\n\n# 正文\n感觉从头到尾都在推销办卡。",
        topic=topic,
        role="李可",
        output_type="精简发布版",
        mode="快速",
        source_pack={"seed_keyword": "美容院做脸有用吗"},
        quote_candidate=None,
    )

    assert '"theme_examples"' not in prompt.split("【脚本骨架】", 1)[1]
    assert "前 6 句必须出现顾客原话" in prompt
    assert "中段必须至少有 2 个美容师动作" in prompt
    assert "男老板不能亲自服务顾客" in prompt


def test_draft_prompt_uses_slim_source_pack_view() -> None:
    topic = {
        "topic_id": "trust-1",
        "topic_title": "做脸先看敢不敢停",
        "seed_keyword": "美容院做脸有用吗",
        "entry_class": "信任怀疑",
        "angle_type": "防御拆解",
        "scene_trigger": "刚躺下就开始推销",
        "fear": "怕一躺下就被推项目",
        "real_desire": "想安心做一次",
        "real_buy_point": "说不要能停",
        "store_rule_hint": "说不要我们就停",
        "opening_family": "冲突实景型",
        "opening_scene": "刚躺下",
        "first_conflict_line": "感觉从头到尾都在推销办卡。",
        "scene_line": "说不买了就很敷衍结束了。",
        "theme_line": "很多人不是怕花钱，是怕花了钱还得受气。",
        "translation_line": "很多人后来不是不做脸了，是懒得再经历一次这种过程。",
        "boss_judgment_line": "你要是来我这儿还得防着，那这店就白开了。",
        "pivot_line": "所以我现在先看一句话：你说不要，她会不会停。",
        "store_rule_line": "说不要，话题就在这里停住。",
        "low_pressure_offer_line": "第一次来，别做太多，先把这一趟安安稳稳做完。",
        "landing_line": "做完这一趟，别让人更防着。",
    }
    source_pack = {
        "business_facts": {"store_positioning": "椿舍在吴江", "allowed_services": ["基础清洁"], "review_boundary": [], "output_goal": {}},
        "hard_rules": {"banned_openings": [], "banned_patterns": [], "body_cta_blacklist": [], "opening_guards": [], "question_guards": []},
        "brief_extract": {"seed_keyword": "美容院做脸有用吗", "entry_class": "信任怀疑", "role": "李可", "output_type": "精简发布版", "mode": "快速"},
        "topic_helpers": {"high_boundary": False, "store_rule_options": ["说不要就停"], "ending_function_options": ["安心感"]},
        "review_language": {
            "principle": "保留差评原话",
            "bucket": "sales",
            "opening_examples": ["感觉从头到尾都在推销办卡。"],
            "scene_examples": [],
            "landing_examples": [],
            "raw_word_policy": "差评原词直接用",
            "must_keep_terms": ["推销", "办卡"],
        },
        "theme_language": {
            "principle": "主题句只做方向参考",
            "theme_examples": ["不该出现在脚本骨架里"],
            "translation_examples": ["但可以留在 source pack 里辅助转译"],
            "boss_judgment_examples": ["也可以留在 source pack 里辅助判断"],
            "low_pressure_offer_examples": ["都放在 source pack 里"],
        },
    }

    prompt = runtime.build_chunshe_video_draft_package_prompt(
        role="李可",
        output_type="精简发布版",
        brief="椿舍内容：关键词=美容院做脸有用吗",
        mode="快速",
        source_pack=source_pack,
        topic=topic,
    )

    assert '"theme_examples"' not in prompt.split("【脚本骨架】", 1)[1]
    assert '"must_keep_terms"' in prompt
    assert '"theme_examples"' in prompt.split("【Source Pack】", 1)[1]


def test_validate_flags_missing_longing_scene() -> None:
    markdown = """# 标题
做脸先别看效果

## 备选标题
- 先看能不能停
- 先看对面会不会停

# 正文
前两天有个姑娘做完脸跟我说了句话。
她说感觉从头到尾都在推销办卡。
说不买了就很敷衍结束了。
我说你放心，说不要就是不要。
第一次来，先把这一趟做完。
"""
    issues, advisories = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "向往感还可以更强" in advisories
def test_ensure_chunshe_video_core_lines_splits_overloaded_line() -> None:
    body = "美容师先把热毛巾在手背试温，再轻轻敷到额头和眼周，再把毯子往上提一点，空调风口挪开。"

    fixed = runtime.ensure_chunshe_video_core_lines(body, {}, output_type="精简发布版")

    assert "\n" in fixed
    assert "热毛巾在手背试温" in fixed
    assert "空调风口挪开" in fixed


def test_ensure_chunshe_video_core_lines_softens_repeated_beautician_prefix() -> None:
    body = "\n".join(
        [
            "美容师先把灯调暗一点。",
            "美容师再把热毛巾放到手背上试温。",
            "她点头以后，美容师才轻轻碰额头。",
        ]
    )

    fixed = runtime.ensure_chunshe_video_core_lines(body, {}, output_type="精简发布版")
    lines = fixed.splitlines()

    assert lines[0] == "美容师先把灯调暗一点。"
    assert lines[1].startswith("再把热毛巾放到手背上试温。")
    assert not lines[1].startswith("美容师")


def test_validate_chunshe_video_markdown_accepts_light_relief_ending() -> None:
    markdown = """# 标题
今天能不能先别跟我讲话

## 备选标题
- 她只想安静躺一会儿
- 今天先别再被安排

# 正文
她进门时手机还攥着。
刚躺下就说：“今天能不能先别跟我讲话。”
“我今天真的很累。”
旁边只回她一句：好，今天先安静待会儿。
灯光压暗，毛巾边角掖平。
试完水温，只问她冷不冷。
做到一半，她把手机扣在旁边。
呼吸慢下来了，肩膀也一点点松开。
人已经够累了，进门就别再接着扛。
做完她拿起包的时候，只说了一句：“嗯，今天人轻一点了。”
"""

    issues, advisories = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "结尾不是低压收口" not in issues
