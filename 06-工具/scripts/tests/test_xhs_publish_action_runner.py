#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import publish_action_runner as runner


def _write_xhs_content(tmp_path: Path, *, manifest_images: int = 6, missing_last_image: bool = False) -> Path:
    if not tmp_path.is_relative_to(runner.REPO_ROOT):
        content_dir = runner.REPO_ROOT / "06-工具" / "scripts" / "tests" / "_tmp_xhs_publish"
    else:
        content_dir = tmp_path / "xhs"
    if content_dir.exists():
        shutil.rmtree(content_dir)
    image_dir = content_dir / "images" / "xhs-a"
    image_dir.mkdir(parents=True, exist_ok=True)
    content_path = content_dir / "xhs-a-20260307-test.md"
    manifest_path = content_path.with_suffix(".images.json")

    image_items = []
    slots = ["p1", "p2", "p3", "p4", "p5", "p6"][:manifest_images]
    for index, slot in enumerate(slots, start=1):
        image_path = image_dir / f"{slot}.png"
        if not (missing_last_image and index == len(slots)):
            image_path.write_bytes(b"png")
        rel_image = image_path.relative_to(runner.REPO_ROOT).as_posix() if image_path.is_relative_to(runner.REPO_ROOT) else image_path.as_posix()
        image_items.append({"slot": slot, "order": index, "rel_path": rel_image, "path": rel_image})

    manifest_payload = {
        "content_path": content_path.as_posix(),
        "account": "A",
        "account_prefix": "xhs-a",
        "mode": "信息图6页",
        "images": image_items,
    }
    manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    content_path.write_text(
        """---
platform: 小红书
account: A
account_prefix: xhs-a
mode: 信息图6页
source_topic: 01-选题管理/02-待生产/demo.md
chosen_title: 选题工具别再盲做
title_candidates: [选题工具别再盲做, 选题效率翻倍, 一套流程搞定选题]
tags: [选题, 内容生产, 小红书]
publish_ready: true
image_manifest: xhs-a-20260307-test.images.json
---

## 标题候选
- 选题工具别再盲做
- 选题效率翻倍
- 一套流程搞定选题

## 标签
#选题 #内容生产 #小红书

## 发布正文
别再用“灵感来了再写”做内容。

## 6页文案
P1 先看问题
P2 再看代价
P3 再看方法
P4 再看动作
P5 再看复盘
P6 再看下一步

## 置顶评论
评论区回你流程模板

## 配图提示词
- p1: page1 prompt
- p2: page2 prompt
- p3: page3 prompt
- p4: page4 prompt
- p5: page5 prompt
- p6: page6 prompt

## 质检清单
- 不夸大
""",
        encoding="utf-8",
    )
    return content_path


@pytest.fixture(autouse=True)
def _clear_xhs_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "XHS_A_BITBROWSER_PROFILE_ID",
        "XHS_B_BITBROWSER_PROFILE_ID",
        "XHS_C_BITBROWSER_PROFILE_ID",
        "XHS_BITBROWSER_PROFILE_ID",
        "XHS_SELECTORS_PATH",
        "XHS_A_SELECTORS_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    tmp_dir = runner.REPO_ROOT / "06-工具" / "scripts" / "tests" / "_tmp_xhs_publish"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)


def test_build_platform_payload_uses_xhs_manifest_and_account_specific_bitbrowser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content_path = _write_xhs_content(tmp_path)
    monkeypatch.setenv("XHS_A_BITBROWSER_PROFILE_ID", "profile-a")
    monkeypatch.setenv("XHS_A_SELECTORS_PATH", "selectors/a.json")

    profile = runner._load_publish_content_profile(content_path, platform="xhs")
    script_path, payload = runner._build_platform_payload(
        platform="xhs",
        mode="publish",
        content_profile=profile,
        rendered_profile=None,
        account="A",
        schedule_time="",
        images=[],
        videos=[],
    )

    assert script_path.name == "publish_xhs.py"
    assert payload["title"] == "选题工具别再盲做"
    assert len(payload["images"]) == 6
    assert payload["account_name"] == "A"
    assert payload["bitbrowser"]["profile_id"] == "profile-a"
    assert payload["selectors_path"] == "selectors/a.json"


def test_build_platform_payload_rejects_shared_xhs_bitbrowser_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content_path = _write_xhs_content(tmp_path)
    monkeypatch.setenv("XHS_BITBROWSER_PROFILE_ID", "shared")
    monkeypatch.setattr(runner, "_lookup_xhs_bitbrowser_profile", lambda account, cfg: {})

    profile = runner._load_publish_content_profile(content_path, platform="xhs")
    with pytest.raises(ValueError, match="dedicated xhs bitbrowser profile is required"):
        runner._build_platform_payload(
            platform="xhs",
            mode="publish",
            content_profile=profile,
            rendered_profile=None,
            account="A",
            schedule_time="",
            images=[],
            videos=[],
        )


def test_build_platform_payload_uses_default_xhs_selectors_when_env_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content_path = _write_xhs_content(tmp_path)
    monkeypatch.setenv("XHS_A_BITBROWSER_PROFILE_ID", "profile-a")

    profile = runner._load_publish_content_profile(content_path, platform="xhs")
    _, payload = runner._build_platform_payload(
        platform="xhs",
        mode="publish",
        content_profile=profile,
        rendered_profile=None,
        account="A",
        schedule_time="",
        images=[],
        videos=[],
    )

    assert payload["selectors_path"].endswith("selectors.xhs.sample.json")


def test_lookup_xhs_bitbrowser_profile_matches_named_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner,
        "_bitbrowser_list_profiles",
        lambda cfg: [
            {
                "id": "profile-a",
                "name": "小红书账号A",
                "browserTags": [{"tagName": "小红书"}],
            }
        ],
    )

    cfg = runner._lookup_xhs_bitbrowser_profile("A", {"api_base": "http://127.0.0.1:54345"})
    assert cfg["profile_id"] == "profile-a"
    assert cfg["resolved_by"] == "bitbrowser_profile_name"


def test_platform_bitbrowser_cfg_uses_xhs_profile_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner,
        "_lookup_xhs_bitbrowser_profile",
        lambda account, cfg: {**cfg, "profile_id": "profile-a", "resolved_by": "bitbrowser_profile_name"},
    )

    cfg = runner._platform_bitbrowser_cfg("xhs", account="A")
    assert cfg["profile_id"] == "profile-a"
    assert cfg["resolved_by"] == "bitbrowser_profile_name"


def test_prepare_publish_surfaces_xhs_preflight_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content_path = _write_xhs_content(tmp_path, missing_last_image=True)
    monkeypatch.setenv("XHS_A_BITBROWSER_PROFILE_ID", "profile-a")
    monkeypatch.setattr(runner, "_run_script", lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not call publisher when preflight fails")))

    result = runner.prepare_publish(
        {
            "platform": "xhs",
            "content": content_path.as_posix(),
            "account": "A",
        },
        dry_run=True,
    )

    assert result["status"] == "error"
    errors = result["result"]["precheck"]["errors"]
    assert any("missing image file" in item for item in errors)
