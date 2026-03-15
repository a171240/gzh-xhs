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
    assert "老板不立规矩，老板回应" in prompt
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

# 置顶评论
补充说明。

# 回复模板
给你细说。
"""

    issues = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "前两行仍是文章开头" in issues
    assert any(item.startswith("正文出现CTA黑名单") for item in issues)
    assert "正文还像长段落" in issues


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

# 置顶评论
补充说明。
# 回复模板
补充说明。"""

    issues = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "正文有点文艺，不够直白" in issues


def test_validate_chunshe_video_markdown_requires_boss_and_offer_blocks() -> None:
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

# 置顶评论
补充说明。

# 回复模板
补充说明。
"""
    issues = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST, topic)

    # new functional checks: no boss voice markers and no low-pressure markers
    assert "缺少老板回应" in issues
    assert "缺少低压力承接" in issues
    # story-driven checks
    assert "缺少具体人" in issues


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

# 置顶评论
补充说明。

# 回复模板
补充说明。
"""
    issues = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "正文仍有方法论老师口气" in issues


def test_validate_chunshe_video_markdown_flags_repeated_role_terms() -> None:
    markdown = """# 标题
做脸先别看效果

## 备选标题
- 先看会不会停
- 先看对面会不会停

# 正文
刚躺下，对面的美容师就开始推销。
你说先不要，美容师还一直往下讲。
很多人后来不是不做脸了，是懒得再受一遍这种气。
我最烦的不是顾客问太多，是还没开始做，人就先紧了。
说不要，话题就在这里停住。
第一次来，先把这一趟安安稳稳做完。

# 置顶评论
补充说明。

# 回复模板
补充说明。
"""
    issues = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "角色称谓重复，读起来啰嗦" in issues


def test_ensure_chunshe_video_core_lines_backfills_boss_and_offer() -> None:
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

    # should inject boss_response (preferred over boss_judgment_line)
    assert "我说行，你先闭眼，做完叫你。" in fixed
    # should inject low_bar_ending (preferred over low_pressure_offer_line)
    assert "做完出来，起码别觉得自己受了委屈。" in fixed


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

# 置顶评论
补充说明。

# 回复模板
补充说明。
"""
    issues = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "缺少向往画面" in issues


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

# 置顶评论
补充说明。

# 回复模板
补充说明。
"""
    issues = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert "缺少具体人" in issues


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

# 置顶评论
补充说明。

# 回复模板
补充说明。
"""
    issues = runtime.validate_chunshe_video_markdown(markdown, runner.CHUNSHE_BODY_CTA_BLACKLIST)

    assert any("不是A，是B" in issue for issue in issues)


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
