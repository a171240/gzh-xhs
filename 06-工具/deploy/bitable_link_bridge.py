#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bitable bridge for Douyin links.

Flow:
1) Find matching record by link/video id (view first, then full table).
2) If not found, create a new record with the link.
3) Poll until text field is filled by upstream extraction automation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

DEFAULT_VIDEO_ID_FIELD = "视频ID"
DEFAULT_LINK_FIELD = "视频链接"
DEFAULT_TEXT_FIELD = "文案整理"
DEFAULT_TEXT_FALLBACK_FIELD = "文案出参"

OPEN_BASE_URL = os.getenv("FEISHU_OPEN_BASE_URL", "https://open.feishu.cn").rstrip("/")
HTTP_TIMEOUT = max(5, int(os.getenv("FEISHU_HTTP_TIMEOUT_SEC", "20")))
VERIFY_SSL = str(os.getenv("FEISHU_HTTP_VERIFY_SSL", "true")).strip().lower() in {"1", "true", "yes", "on", "y"}


@dataclass
class Config:
    app_id: str
    app_secret: str
    app_token: str
    table_id: str
    view_id: str
    video_id_field: str
    link_field: str
    text_field: str
    text_fallback_field: str
    min_chars: int
    wait_seconds: int
    poll_interval: int


def _cell_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value).strip()
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            t = _cell_to_text(item)
            if t:
                chunks.append(t)
        return "\n".join(chunks).strip()
    if isinstance(value, dict):
        for key in ("text", "link", "url", "href", "name", "value"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        chunks: list[str] = []
        for raw in value.values():
            t = _cell_to_text(raw)
            if t:
                chunks.append(t)
        if chunks:
            return "\n".join(chunks).strip()
    return ""


def _normalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        p = urlparse(raw)
    except Exception:
        return raw.lower()
    host = str(p.netloc or "").lower()
    if not host:
        return raw.lower()
    scheme = str(p.scheme or "https").lower()
    path = re.sub(r"/+", "/", str(p.path or "/"))
    return f"{scheme}://{host}{path}".rstrip("/")


def _extract_video_id(url: str) -> str:
    m = re.search(r"/video/(\d{8,32})", str(url or ""), flags=re.IGNORECASE)
    return str(m.group(1) if m else "").strip()


def _expand_short_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        resp = requests.get(
            raw,
            allow_redirects=True,
            timeout=HTTP_TIMEOUT,
            verify=VERIFY_SSL,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; OpenClaw-BitableBridge/1.0)",
                "Accept": "*/*",
            },
        )
        final_url = str(resp.url or "").strip()
        if final_url:
            return final_url
    except Exception:
        pass
    return raw


def _auth_token(cfg: Config) -> str:
    r = requests.post(
        f"{OPEN_BASE_URL}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": cfg.app_id, "app_secret": cfg.app_secret},
        timeout=HTTP_TIMEOUT,
        verify=VERIFY_SSL,
    )
    r.raise_for_status()
    data = json.loads(r.content.decode("utf-8"))
    if not isinstance(data, dict):
        data = r.json()
    if int(data.get("code") or 0) != 0:
        raise RuntimeError(f"auth failed: {data.get('msg') or data}")
    token = str(data.get("tenant_access_token") or "").strip()
    if not token:
        raise RuntimeError("empty tenant_access_token")
    return token


def _req(token: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    r = requests.request(
        method=method.upper(),
        url=f"{OPEN_BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json=payload,
        timeout=HTTP_TIMEOUT,
        verify=VERIFY_SSL,
    )
    r.raise_for_status()
    data = json.loads(r.content.decode("utf-8"))
    if not isinstance(data, dict):
        data = r.json()
    if int(data.get("code") or 0) != 0:
        raise RuntimeError(f"request failed: {data.get('msg') or data}")
    return data


def _list_fields(token: str, cfg: Config) -> list[dict[str, Any]]:
    path = f"/open-apis/bitable/v1/apps/{cfg.app_token}/tables/{cfg.table_id}/fields?page_size=500"
    return ((_req(token, "GET", path).get("data") or {}).get("items") or [])


def _search_records(token: str, cfg: Config, use_view: bool) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"automatic_fields": False}
    if use_view and cfg.view_id:
        payload["view_id"] = cfg.view_id
    items: list[dict[str, Any]] = []
    page_token = ""
    for _ in range(8):
        path = f"/open-apis/bitable/v1/apps/{cfg.app_token}/tables/{cfg.table_id}/records/search?page_size=200"
        if page_token:
            path += f"&page_token={page_token}"
        data = _req(token, "POST", path, payload)
        batch = ((data.get("data") or {}).get("items") or [])
        items.extend([x for x in batch if isinstance(x, dict)])
        has_more = bool((data.get("data") or {}).get("has_more"))
        page_token = str((data.get("data") or {}).get("page_token") or "").strip()
        if not has_more or not page_token:
            break
    return items


def _match_record_by_link_or_id(cfg: Config, records: list[dict[str, Any]], url: str, expanded_url: str) -> dict[str, Any] | None:
    match_urls = {x for x in {_normalize_url(url), _normalize_url(expanded_url)} if x}
    video_ids = {x for x in {_extract_video_id(url), _extract_video_id(expanded_url)} if x}
    for item in records:
        fields = item.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        id_text = _cell_to_text(fields.get(cfg.video_id_field))
        link_text = _cell_to_text(fields.get(cfg.link_field))
        if not id_text or not link_text:
            for _, raw in fields.items():
                t = _cell_to_text(raw)
                if not t:
                    continue
                if not link_text and ("douyin.com" in t or "iesdouyin.com" in t):
                    link_text = t
                if not id_text:
                    m = re.search(r"/video/(\d{8,32})", t, flags=re.IGNORECASE)
                    if m:
                        id_text = str(m.group(1) or "").strip()
                    elif re.fullmatch(r"\d{15,22}", t):
                        id_text = t
        link_norm = _normalize_url(link_text)
        if any(v and (v in id_text or v in link_text) for v in video_ids):
            return item
        if any(mu and (mu in link_norm or mu in _normalize_url(id_text)) for mu in match_urls):
            return item
    return None


def _pick_text_from_fields(cfg: Config, fields: dict[str, Any]) -> str:
    for name in (cfg.text_fallback_field, cfg.text_field):
        if not name:
            continue
        text = _cell_to_text(fields.get(name))
        if text:
            return text
    candidates: list[tuple[int, int, str]] = []
    for key, raw in fields.items():
        text = _cell_to_text(raw)
        if not text:
            continue
        if re.fullmatch(r"\d{8,22}", text):
            continue
        if not re.search(r"[\u4e00-\u9fffA-Za-z]", text):
            continue
        lower = text.lower()
        if ("douyin.com" in lower or "iesdouyin.com" in lower) and len(text) < 200:
            continue
        key_text = str(key or "")
        pri = 3
        if ("文案" in key_text) or ("copy" in key_text.lower()):
            pri = 0
        elif ("摘要" in key_text) or ("summary" in key_text.lower()):
            pri = 1
        elif ("内容" in key_text) or ("提取" in key_text) or ("整理" in key_text):
            pri = 2
        candidates.append((pri, -len(text), text))
    if candidates:
        candidates.sort()
        return candidates[0][2]
    return ""


def _create_record(token: str, cfg: Config, field_meta: list[dict[str, Any]], url: str, expanded_url: str) -> str:
    names = {str(x.get("field_name") or "").strip() for x in field_meta if isinstance(x, dict)}
    video_id = _extract_video_id(expanded_url or url)
    fields: dict[str, Any] = {}

    if cfg.link_field in names:
        # URL field can differ by tenant type handling; try robust shapes.
        link_candidates: list[Any] = [
            {"link": expanded_url or url, "text": expanded_url or url},
            {"link": url, "text": url},
            str(expanded_url or url),
            str(url),
        ]
    else:
        link_candidates = []

    if cfg.video_id_field in names and video_id.isdigit():
        # Number field (type=2) prefers numeric values.
        fields[cfg.video_id_field] = int(video_id)

    if not link_candidates:
        raise RuntimeError(f"link field not found: {cfg.link_field}")

    last_err = ""
    for candidate in link_candidates:
        payload_fields = dict(fields)
        payload_fields[cfg.link_field] = candidate
        try:
            data = _req(
                token,
                "POST",
                f"/open-apis/bitable/v1/apps/{cfg.app_token}/tables/{cfg.table_id}/records",
                {"fields": payload_fields},
            )
            return str(((data.get("data") or {}).get("record") or {}).get("record_id") or "").strip()
        except Exception as exc:
            last_err = str(exc)
            continue
    raise RuntimeError(f"create record failed: {last_err or 'unknown'}")


def _get_record(token: str, cfg: Config, record_id: str) -> dict[str, Any]:
    data = _req(
        token,
        "GET",
        f"/open-apis/bitable/v1/apps/{cfg.app_token}/tables/{cfg.table_id}/records/{record_id}",
    )
    return ((data.get("data") or {}).get("record") or {}) if isinstance(data, dict) else {}


def run(cfg: Config, url: str) -> dict[str, Any]:
    token = _auth_token(cfg)
    expanded = _expand_short_url(url)

    view_records = _search_records(token, cfg, use_view=True)
    matched = _match_record_by_link_or_id(cfg, view_records, url, expanded)

    if not matched:
        full_records = _search_records(token, cfg, use_view=False)
        matched = _match_record_by_link_or_id(cfg, full_records, url, expanded)

    field_meta = _list_fields(token, cfg)
    created = False
    if not matched:
        record_id = _create_record(token, cfg, field_meta, url, expanded)
        created = True
    else:
        record_id = str(matched.get("record_id") or "").strip()

    if not record_id:
        raise RuntimeError("empty record_id after find/create")

    start = time.time()
    last_preview = ""
    while True:
        rec = _get_record(token, cfg, record_id)
        fields = rec.get("fields") or {}
        text = _pick_text_from_fields(cfg, fields if isinstance(fields, dict) else {})
        if text and len(text) >= cfg.min_chars:
            return {
                "ok": True,
                "created": created,
                "record_id": record_id,
                "expanded_url": expanded,
                "text_len": len(text),
                "text_preview": text[:120].replace("\n", " "),
            }
        if text:
            last_preview = text[:120].replace("\n", " ")
        if time.time() - start >= cfg.wait_seconds:
            return {
                "ok": False,
                "created": created,
                "record_id": record_id,
                "expanded_url": expanded,
                "text_len": len(text) if text else 0,
                "text_preview": last_preview,
                "error": "timeout_waiting_bitable_text",
            }
        time.sleep(max(2, cfg.poll_interval))


def main() -> int:
    parser = argparse.ArgumentParser(description="Insert Douyin link into Bitable, wait for extracted text.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--wait-seconds", type=int, default=300)
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--min-content-chars", type=int, default=120)
    args = parser.parse_args()

    app_id = str(os.getenv("FEISHU_APP_ID") or "").strip()
    app_secret = str(os.getenv("FEISHU_APP_SECRET") or "").strip()
    if not app_id or not app_secret:
        raise SystemExit("Missing FEISHU_APP_ID/FEISHU_APP_SECRET.")

    cfg = Config(
        app_id=app_id,
        app_secret=app_secret,
        app_token=str(os.getenv("BITABLE_APP_TOKEN") or "").strip(),
        table_id=str(os.getenv("BITABLE_TABLE_ID") or "").strip(),
        view_id=str(os.getenv("BITABLE_VIEW_ID") or "").strip(),
        video_id_field=str(os.getenv("BITABLE_VIDEO_ID_FIELD") or DEFAULT_VIDEO_ID_FIELD).strip(),
        link_field=str(os.getenv("BITABLE_LINK_FIELD") or DEFAULT_LINK_FIELD).strip(),
        text_field=str(os.getenv("BITABLE_TEXT_FIELD") or DEFAULT_TEXT_FIELD).strip(),
        text_fallback_field=str(os.getenv("BITABLE_TEXT_FALLBACK_FIELD") or DEFAULT_TEXT_FALLBACK_FIELD).strip(),
        min_chars=max(1, int(args.min_content_chars)),
        wait_seconds=max(30, int(args.wait_seconds)),
        poll_interval=max(2, int(args.poll_interval)),
    )
    missing: list[str] = []
    if not cfg.app_token:
        missing.append("BITABLE_APP_TOKEN")
    if not cfg.table_id:
        missing.append("BITABLE_TABLE_ID")
    if missing:
        raise SystemExit(f"Missing required env: {', '.join(missing)}.")
    result = run(cfg, args.url)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if bool(result.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())


