#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed contracts for XHS adapter requests/results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ALLOWED_ACTIONS = {"publish", "search", "detail", "comment", "content_data"}


@dataclass(slots=True)
class ActionRequest:
    action: str
    account_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    dry_run: bool = False
    timeout_sec: int = 180
    trace_id: str = ""

    def normalized_action(self) -> str:
        return str(self.action or "").strip().lower()

    def normalized_account(self) -> str:
        return str(self.account_id or "").strip()

    def validate(self) -> None:
        action = self.normalized_action()
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"unsupported action: {self.action}")
        account = self.normalized_account()
        if not account:
            raise ValueError("account_id is required")
        if not isinstance(self.payload, dict):
            raise ValueError("payload must be a JSON object")
        if int(self.timeout_sec) <= 0:
            raise ValueError("timeout_sec must be > 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.normalized_action(),
            "account_id": self.normalized_account(),
            "payload": dict(self.payload or {}),
            "idempotency_key": str(self.idempotency_key or "").strip(),
            "dry_run": bool(self.dry_run),
            "timeout_sec": int(self.timeout_sec),
            "trace_id": str(self.trace_id or "").strip(),
        }


@dataclass(slots=True)
class ActionResult:
    ok: bool
    action: str
    account_id: str
    trace_id: str
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "action": str(self.action or "").strip().lower(),
            "account_id": str(self.account_id or "").strip(),
            "trace_id": str(self.trace_id or "").strip(),
            "status": str(self.status or "").strip().lower(),
            "data": dict(self.data or {}),
            "error": self.error if self.error else None,
            "meta": dict(self.meta or {}),
        }

    @classmethod
    def success(
        cls,
        *,
        action: str,
        account_id: str,
        trace_id: str,
        status: str = "success",
        data: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> "ActionResult":
        return cls(
            ok=True,
            action=action,
            account_id=account_id,
            trace_id=trace_id,
            status=status,
            data=dict(data or {}),
            meta=dict(meta or {}),
        )

    @classmethod
    def failure(
        cls,
        *,
        action: str,
        account_id: str,
        trace_id: str,
        error: str,
        status: str = "error",
        data: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> "ActionResult":
        return cls(
            ok=False,
            action=action,
            account_id=account_id,
            trace_id=trace_id,
            status=status,
            error=str(error or "unknown error"),
            data=dict(data or {}),
            meta=dict(meta or {}),
        )

