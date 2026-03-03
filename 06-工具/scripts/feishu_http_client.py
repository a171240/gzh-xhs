#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feishu HTTP API helper for automation runners."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

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


def _bitable_cell_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value).strip()
    if isinstance(value, list):
        chunks = [_bitable_cell_to_text(item) for item in value]
        return "\n".join([item for item in chunks if item]).strip()
    if isinstance(value, dict):
        for key in ("text", "link", "url", "href", "name", "value"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        chunks = [_bitable_cell_to_text(raw) for raw in value.values()]
        return "\n".join([item for item in chunks if item]).strip()
    return ""


def _normalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw.lower()
    host = str(parsed.netloc or "").lower()
    if not host:
        return raw.lower()
    scheme = str(parsed.scheme or "https").lower()
    path = re.sub(r"/+", "/", str(parsed.path or "/")).rstrip("/")
    return f"{scheme}://{host}{path}"


def _extract_video_id(url: str) -> str:
    matched = re.search(r"/video/(\d{8,32})", str(url or ""), re.IGNORECASE)
    return str(matched.group(1) if matched else "").strip()


def _build_link_candidates(raw_url: str, normalized_url: str, video_id: str) -> list[str]:
    out: list[str] = []
    for item in (
        str(raw_url or "").strip(),
        str(normalized_url or "").strip(),
        (f"https://www.douyin.com/video/{video_id}" if video_id else ""),
    ):
        if item and item not in out:
            out.append(item)
    return out


def search_bitable_records(
    *,
    app_token: str,
    table_id: str,
    view_id: str = "",
    settings: FeishuHttpSettings | None = None,
    page_size: int = 200,
    max_pages: int = 8,
) -> list[dict[str, Any]]:
    s = settings or load_feishu_settings()
    token = get_tenant_access_token(s)
    items: list[dict[str, Any]] = []
    page_token = ""
    body: dict[str, Any] = {"automatic_fields": False}
    if str(view_id or "").strip():
        body["view_id"] = str(view_id).strip()

    for _ in range(max(1, int(max_pages))):
        path = f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search?page_size={max(10, min(500, int(page_size)))}"
        if page_token:
            path += f"&page_token={page_token}"
        data = _request_json(
            settings=s,
            method="POST",
            path=path,
            token=token,
            payload=body,
        )
        batch = ((data.get("data") or {}).get("items") or [])
        items.extend([item for item in batch if isinstance(item, dict)])
        has_more = bool((data.get("data") or {}).get("has_more"))
        page_token = str((data.get("data") or {}).get("page_token") or "").strip()
        if not has_more or not page_token:
            break
    return items


def ensure_bitable_link_record(
    *,
    app_token: str,
    table_id: str,
    url: str,
    link_field: str = "视频链接",
    video_id_field: str = "视频ID",
    view_id: str = "",
    fallback_full_scan: bool = True,
    settings: FeishuHttpSettings | None = None,
) -> dict[str, Any]:
    """Ensure a Bitable row exists for the Douyin URL and return record metadata."""

    raw_url = str(url or "").strip()
    if not raw_url:
        return {"ok": False, "error": "empty_url"}

    s = settings or load_feishu_settings()
    normalized = _normalize_url(raw_url)
    video_id = _extract_video_id(raw_url)
    scans = [True]
    if fallback_full_scan and str(view_id or "").strip():
        scans.append(False)

    for use_view in scans:
        records = search_bitable_records(
            app_token=app_token,
            table_id=table_id,
            view_id=(view_id if use_view else ""),
            settings=s,
        )
        for item in records:
            fields = item.get("fields") or {}
            if not isinstance(fields, dict):
                continue
            link_text = _bitable_cell_to_text(fields.get(link_field))
            vid_text = _bitable_cell_to_text(fields.get(video_id_field))
            if not link_text:
                for raw in fields.values():
                    text = _bitable_cell_to_text(raw)
                    if ("douyin.com" in text) or ("iesdouyin.com" in text):
                        link_text = text
                        break
            if not vid_text and link_text:
                vid_text = _extract_video_id(link_text)

            link_norm = _normalize_url(link_text)
            if normalized and link_norm and normalized == link_norm:
                return {
                    "ok": True,
                    "record_id": str(item.get("record_id") or "").strip(),
                    "created": False,
                    "match": "url",
                }
            # Link-first strategy: only use video-id match when this row has no usable link.
            if (
                video_id
                and (not link_norm)
                and (video_id == vid_text or video_id in link_text)
            ):
                return {
                    "ok": True,
                    "record_id": str(item.get("record_id") or "").strip(),
                    "created": False,
                    "match": "video_id",
                }

    link_key = str(link_field or "视频链接")
    last_error = ""
    for candidate in _build_link_candidates(raw_url, normalized, video_id):
        payload_variants: list[Any] = [
            candidate,
            {"text": candidate, "link": candidate},
        ]
        for value in payload_variants:
            try:
                data = add_bitable_record(
                    app_token=app_token,
                    table_id=table_id,
                    fields={link_key: value},
                    settings=s,
                )
                record = data.get("record") if isinstance(data, dict) else {}
                record_id = str((record or {}).get("record_id") or data.get("record_id") or "").strip()
                return {
                    "ok": True,
                    "record_id": record_id,
                    "created": True,
                    "match": "created",
                }
            except Exception as exc:
                last_error = str(exc)
                # URL fields occasionally reject one representation; keep trying.
                continue

    return {"ok": False, "error": last_error or "bitable_create_failed"}


def send_text_message(
    *,
    receive_id: str,
    text: str,
    receive_id_type: str = "chat_id",
    settings: FeishuHttpSettings | None = None,
) -> dict[str, Any]:
    rid = str(receive_id or "").strip()
    body_text = str(text or "").strip()
    if not rid:
        raise RuntimeError("receive_id is empty")
    if not body_text:
        raise RuntimeError("text is empty")
    id_type = str(receive_id_type or "chat_id").strip() or "chat_id"

    s = settings or load_feishu_settings()
    token = get_tenant_access_token(s)
    payload = {
        "receive_id": rid,
        "msg_type": "text",
        "content": json.dumps({"text": body_text}, ensure_ascii=False),
    }
    data = _request_json(
        settings=s,
        method="POST",
        path=f"/open-apis/im/v1/messages?receive_id_type={id_type}",
        token=token,
        payload=payload,
    )
    return data.get("data") or {}


def reply_message(
    *,
    message_id: str,
    text: str,
    settings: FeishuHttpSettings | None = None,
) -> dict[str, Any]:
    mid = str(message_id or "").strip()
    body_text = str(text or "").strip()
    if not mid:
        raise RuntimeError("message_id is empty")
    if not body_text:
        raise RuntimeError("text is empty")

    s = settings or load_feishu_settings()
    token = get_tenant_access_token(s)
    payload = {
        "msg_type": "text",
        "content": json.dumps({"text": body_text}, ensure_ascii=False),
    }
    data = _request_json(
        settings=s,
        method="POST",
        path=f"/open-apis/im/v1/messages/{mid}/reply",
        token=token,
        payload=payload,
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
    data = doc_meta.get("data") if isinstance(doc_meta, dict) else {}
    if not isinstance(data, dict):
        data = {}
    document_obj = data.get("document")
    root_id = ""
    if isinstance(document_obj, dict):
        for key in ("root_id", "document_id", "block_id"):
            value = str(document_obj.get(key) or "").strip()
            if value:
                root_id = value
                break
    elif isinstance(document_obj, str):
        root_id = document_obj.strip()
    if not root_id:
        root_id = str(data.get("document_id") or "").strip() or str(document_id)

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
