#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import time

import pytest

from adapters.xhs.client import XHSAdapterClient
from adapters.xhs.lock import AccountActionLock
from adapters.xhs.security_guard import SecurityError, safe_join, validate_account_id, validate_url


def test_validate_account_id_accepts_safe_value() -> None:
    assert validate_account_id("acc_001-A") == "acc_001-A"


def test_validate_account_id_rejects_path_traversal() -> None:
    with pytest.raises(SecurityError):
        validate_account_id("../evil")


def test_safe_join_rejects_escape() -> None:
    with pytest.raises(SecurityError):
        safe_join("E:/tmp/root", "..", "escape")


def test_validate_url_blocks_localhost() -> None:
    with pytest.raises(SecurityError):
        validate_url("http://127.0.0.1:8000/test", block_private_ip=True)


def test_stale_lock_can_be_recovered(tmp_path) -> None:
    lock = AccountActionLock(
        lock_root=tmp_path,
        account_id="acc1",
        action="publish",
        timeout_sec=0.5,
        poll_interval_sec=0.05,
        stale_after_sec=1.0,
    )
    lock.lock_path.write_text(
        json.dumps(
            {
                "pid": 999999,
                "account_id": "acc1",
                "action": "publish",
                "created_at": int(time.time()) - 7200,
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    assert lock.acquire() is True
    lock.release()


def test_runner_command_policy_blocks_untrusted_binary(tmp_path) -> None:
    client = XHSAdapterClient(
        config={
            "enabled": True,
            "profile_root": str(tmp_path / "profiles"),
            "lock_root": str(tmp_path / "locks"),
            "runner": {"allowed_binaries": ["python"]},
        },
        repo_root=tmp_path,
    )
    with pytest.raises(SecurityError):
        client._enforce_command_policy(["cmd.exe", "/c", "echo", "x"])
