#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feishu HTTP API helper for automation runners."""

from __future__ import annotations

import dataclasses
import json
import os
import time
from typing import Any

import requests


@dataclasses.dataclass(frozen=True)
class FeishuHttpSettings:
    app_id: str
    app_secret: str
    base_url: str
    timeout_sec: int
    verify_ssl: bool


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def load_feishu_settings() -> FeishuHttpSettings:
    return FeishuHttpSettings(
        app_id=str(os.getenv("FEISHU_APP_ID") or "").strip(),
        app_secret=str(os.getenv("FEISHU_APP_SECRET") or "").strip(),
        base_url=str(os.getenv("FEISHU_OPEN_BASE_URL") or "https://open.feishu.cn").strip().rstrip("/"),
        timeout_sec=max(5, int(os.getenv("FEISHU_HTTP_TIMEOUT_SEC", "20"))),
        verify_ssl=_as_bool(os.getenv("FEISHU_HTTP_VERIFY_SSL"), default=True),
    )


_TOKEN_CACHE: dict[str, Any] = {"token": "", "expire_at": 0.0}


def _request_json(
    *,
    settings: FeishuHttpSettings,
    method: str,
    path: str,
    token: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{settings.base_url}{path}"
    headers: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.request(
        method,
        url,
        headers=headers,
        json=payload,
        timeout=settings.timeout_sec,
        verify=settings.verify_ssl,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"feishu http {response.status_code}: {response.text[:1000]}")
    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(f"feishu invalid json: {exc}") from exc
    code = int(data.get("code") or 0)
    if code != 0:
        msg = str(data.get("msg") or data)
        raise RuntimeError(f"feishu business error code={code}: {msg}")
    return data


def get_tenant_access_token(settings: FeishuHttpSettings | None = None) -> str:
    s = settings or load_feishu_settings()
    if not s.app_id or not s.app_secret:
        raise RuntimeError("FEISHU_APP_ID/FEISHU_APP_SECRET not configured")

    now = time.time()
    if _TOKEN_CACHE.get("token") and float(_TOKEN_CACHE.get("expire_at") or 0.0) - 60 > now:
        return str(_TOKEN_CACHE["token"])

    data = _request_json(
        settings=s,
        method="POST",
        path="/open-apis/auth/v3/tenant_access_token/internal",
        payload={"app_id": s.app_id, "app_secret": s.app_secret},
    )
    token = str(data.get("tenant_access_token") or "").strip()
    expire = int(data.get("expire") or 7200)
    if not token:
        raise RuntimeError("empty tenant_access_token from feishu")

    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expire_at"] = now + max(300, expire)
    return token


def add_bitable_record(
    *,
    app_token: str,
    table_id: str,
    fields: dict[str, Any],
    settings: FeishuHttpSettings | None = None,
) -> dict[str, Any]:
    s = settings or load_feishu_settings()
    token = get_tenant_access_token(s)
    data = _request_json(
        settings=s,
        method="POST",
        path=f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records",
        token=token,
        payload={"fields": fields},
    )
    return data.get("data") or {}


def append_doc_markdown_summary(
    *,
    document_id: str,
    markdown_text: str,
    settings: FeishuHttpSettings | None = None,
) -> dict[str, Any]:
    """Append a plain text paragraph into a docx document.

    Feishu docx write API requires block-based payload. This helper uses root block append
    with a simple text paragraph for robust automation summaries.
    """

    text = str(markdown_text or "").strip()
    if not text:
        return {"skipped": True, "reason": "empty summary"}

    s = settings or load_feishu_settings()
    token = get_tenant_access_token(s)

    # Root block id can be fetched from GET /documents/{document_id}.
    doc_meta = _request_json(
        settings=s,
        method="GET",
        path=f"/open-apis/docx/v1/documents/{document_id}",
        token=token,
    )
    root_id = str((doc_meta.get("data") or {}).get("document") or "")
    if not root_id:
        root_id = str((doc_meta.get("data") or {}).get("document_id") or document_id)

    paragraph = {
        "children": [
            {
                "block_type": 2,
                "paragraph": {
                    "elements": [
                        {
                            "text_run": {
                                "content": text,
                            }
                        }
                    ]
                },
            }
        ]
    }

    data = _request_json(
        settings=s,
        method="POST",
        path=f"/open-apis/docx/v1/documents/{document_id}/blocks/{root_id}/children",
        token=token,
        payload=paragraph,
    )
    return data.get("data") or {}


def safe_sync_metrics_record(fields: dict[str, Any]) -> dict[str, Any]:
    app_token = str(os.getenv("FEISHU_BITABLE_APP_TOKEN") or "").strip()
    table_id = str(os.getenv("FEISHU_BITABLE_METRICS_TABLE_ID") or "").strip()
    if not app_token or not table_id:
        return {"skipped": True, "reason": "bitable env not configured"}
    try:
        data = add_bitable_record(app_token=app_token, table_id=table_id, fields=fields)
        return {"ok": True, "data": data}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def safe_sync_retro_summary(summary_text: str) -> dict[str, Any]:
    doc_id = str(os.getenv("FEISHU_RETRO_DOC_ID") or "").strip()
    if not doc_id:
        return {"skipped": True, "reason": "FEISHU_RETRO_DOC_ID not configured"}
    try:
        data = append_doc_markdown_summary(document_id=doc_id, markdown_text=summary_text)
        return {"ok": True, "data": data}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _debug_dump() -> str:
    settings = load_feishu_settings()
    return json.dumps(dataclasses.asdict(settings), ensure_ascii=False)
