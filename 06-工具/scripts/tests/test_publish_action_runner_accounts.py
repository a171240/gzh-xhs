#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import publish_action_runner as runner


@pytest.fixture(autouse=True)
def _clear_wechat_publish_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "WECHAT_METRICS_BITBROWSER_PROFILE_ID",
        "WECHAT_BITBROWSER_PROFILE_ID",
        "WECHAT_GONGCHANG_BITBROWSER_PROFILE_ID",
        "WECHAT_IPGC_BITBROWSER_PROFILE_ID",
        "WECHAT_ZENGZHANG_BITBROWSER_PROFILE_ID",
        "WECHAT_SHIZHAN_BITBROWSER_PROFILE_ID",
        "WECHAT_ALLOW_SHARED_PROFILE_FALLBACK",
    ):
        monkeypatch.delenv(key, raising=False)


def test_platform_bitbrowser_cfg_looks_up_wechat_profile_by_account_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner,
        "_bitbrowser_list_profiles",
        lambda cfg: [
            {
                "id": "profile-shizhan",
                "name": "商业IP实战笔记",
                "platform": "https://mp.weixin.qq.com/",
            }
        ],
    )

    cfg = runner._platform_bitbrowser_cfg("wechat", account="shizhan")

    assert cfg["profile_id"] == "profile-shizhan"
    assert cfg["profile_name"] == "商业IP实战笔记"
    assert cfg["resolved_by"] == "bitbrowser_profile_name"


def test_platform_bitbrowser_cfg_prefers_account_specific_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WECHAT_IPGC_BITBROWSER_PROFILE_ID", "profile-ipgc-env")
    monkeypatch.setattr(
        runner,
        "_bitbrowser_list_profiles",
        lambda cfg: [
            {
                "id": "profile-ipgc-api",
                "name": "IP工厂",
                "platform": "https://mp.weixin.qq.com/",
            }
        ],
    )

    cfg = runner._platform_bitbrowser_cfg("wechat", account="IP工厂")

    assert cfg["profile_id"] == "profile-ipgc-env"
    assert cfg["resolved_by"] == "env_account_profile"


def test_build_platform_payload_rejects_shared_wechat_profile_for_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WECHAT_METRICS_BITBROWSER_PROFILE_ID", "shared-profile")
    monkeypatch.setattr(runner, "_bitbrowser_list_profiles", lambda cfg: [])
    monkeypatch.setattr(runner, "_has_dedicated_wechat_profile_dir", lambda account: False)

    with pytest.raises(ValueError, match="shared wechat publish profile is not allowed"):
        runner._build_platform_payload(
            platform="wechat",
            mode="draft",
            content_profile={
                "title": "测试标题",
                "body": "测试正文",
                "meta": {"账号": "IP工厂"},
                "tags": [],
            },
            rendered_profile={
                "title": "测试标题",
                "author": "IP工厂",
                "content_blocks": [{"type": "html", "role": "body", "html": "<p>测试正文</p>"}],
                "content_html": "<p>测试正文</p>",
                "cover_path": "",
                "layout_profile": "raphael_wechat_v1",
                "theme_id": "sunset",
                "clipboard_html_path": "",
                "preview_html_path": "",
                "manifest_path": "",
            },
            account="IP工厂",
            schedule_time="",
            images=[],
            videos=[],
        )
