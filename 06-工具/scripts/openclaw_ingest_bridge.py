#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bridge script: OpenClaw message -> internal Writer API."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from typing import Any

import requests

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FALLBACKS = (
    os.path.join(SCRIPT_DIR, ".env.ingest-writer.local"),
    os.path.join(SCRIPT_DIR, ".env.ingest-writer"),
)


@dataclass(frozen=True)
class BridgeSettings:
    writer_base_url: str
    shared_token: str
    hmac_secret: str
    timeout_sec: int
    verify_ssl: bool


@dataclass(frozen=True)
class RoutedPayload:
    mode: str
    quote_text: str
    urls: list[str]


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _load_env_fallbacks() -> None:
    # Keep usage simple: if process env is missing, read local writer env files.
    needs = {"INGEST_SHARED_TOKEN", "INGEST_HMAC_SECRET", "INGEST_WRITER_BASE_URL"}
    missing = [key for key in needs if not (os.getenv(key) or "").strip()]
    if not missing:
        return
    for path in ENV_FALLBACKS:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip().lstrip("\ufeff")
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and value and not (os.getenv(key) or "").strip():
                    os.environ[key] = value
        missing = [key for key in needs if not (os.getenv(key) or "").strip()]
        if not missing:
            return


def _load_settings() -> BridgeSettings:
    _load_env_fallbacks()
    base_url = os.getenv("INGEST_WRITER_BASE_URL", "http://127.0.0.1:8790").strip().rstrip("/")
    shared_token = os.getenv("INGEST_SHARED_TOKEN", "").strip()
    hmac_secret = os.getenv("INGEST_HMAC_SECRET", "").strip() or shared_token
    timeout_sec = max(3, int(os.getenv("INGEST_TIMEOUT_SEC", "20")))
    verify_ssl = _as_bool(os.getenv("INGEST_VERIFY_SSL"), default=True)
    return BridgeSettings(
        writer_base_url=base_url,
        shared_token=shared_token,
        hmac_secret=hmac_secret,
        timeout_sec=timeout_sec,
        verify_ssl=verify_ssl,
    )


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in URL_RE.findall(str(text or "")):
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in urls:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _strip_urls(text: str) -> str:
    no_url = URL_RE.sub(" ", str(text or ""))
    return re.sub(r"\s+", " ", no_url).strip()


def _route_payload(*, text: str, urls: list[str], forced_mode: str) -> RoutedPayload:
    merged_urls = _dedupe_urls(list(urls) + _extract_urls(text))
    quote_text = _strip_urls(text)

    if forced_mode in {"quote", "link", "mixed"}:
        if forced_mode == "quote":
            return RoutedPayload(mode="quote", quote_text=quote_text, urls=[])
        if forced_mode == "link":
            return RoutedPayload(mode="link", quote_text="", urls=merged_urls)
        return RoutedPayload(mode="mixed", quote_text=quote_text, urls=merged_urls)

    if merged_urls and quote_text:
        return RoutedPayload(mode="mixed", quote_text=quote_text, urls=merged_urls)
    if merged_urls:
        return RoutedPayload(mode="link", quote_text="", urls=merged_urls)
    if quote_text:
        return RoutedPayload(mode="quote", quote_text=quote_text, urls=[])
    return RoutedPayload(mode="ignore", quote_text="", urls=[])


def _build_signature(secret: str, *, timestamp: str, nonce: str, body: bytes) -> str:
    payload = f"{timestamp}\n{nonce}\n".encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _build_headers(settings: BridgeSettings, body: bytes) -> dict[str, str]:
    timestamp = str(int(dt.datetime.now(dt.timezone.utc).timestamp()))
    nonce = uuid.uuid4().hex
    signature = _build_signature(settings.hmac_secret, timestamp=timestamp, nonce=nonce, body=body)
    return {
        "Authorization": f"Bearer {settings.shared_token}",
        "Content-Type": "application/json",
        "X-Ingest-Timestamp": timestamp,
        "X-Ingest-Nonce": nonce,
        "X-Ingest-Signature": signature,
    }


def _post(settings: BridgeSettings, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.shared_token:
        raise RuntimeError("INGEST_SHARED_TOKEN is empty")
    if not settings.hmac_secret:
        raise RuntimeError("INGEST_HMAC_SECRET is empty")

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = _build_headers(settings, body)
    url = f"{settings.writer_base_url}{endpoint}"
    response = requests.post(
        url,
        headers=headers,
        data=body,
        timeout=settings.timeout_sec,
        verify=settings.verify_ssl,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Writer API {response.status_code}: {response.text}")
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Writer API business error: {data}")
    return data


def _print_result(data: dict[str, Any]) -> None:
    result = data.get("result") or {}
    status = result.get("status", "-")
    mode = result.get("mode", "-")
    added = result.get("added", 0)
    near_dup = result.get("near_dup", 0)
    skipped = result.get("skipped", 0)
    touched_files = result.get("touched_files") or []
    errors = result.get("errors") or []

    print(f"status={status} mode={mode} added={added} near_dup={near_dup} skipped={skipped}")
    print(f"event_ref={data.get('event_ref')}")
    if data.get("duplicate"):
        print("duplicate=true")
    if touched_files:
        print("touched_files:")
        for path in touched_files:
            print(f"  - {path}")
    if errors:
        print("errors:")
        for item in errors:
            print(f"  - {item}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send OpenClaw message payload to Writer API")
    parser.add_argument("--text", default="", help="Original text content")
    parser.add_argument("--url", dest="urls", action="append", default=[], help="URL input, repeatable")
    parser.add_argument("--event-ref", default="", help="Idempotency key. Default: generated UUID")
    parser.add_argument("--source-kind", default="openclaw-feishu", help="Source kind marker")
    parser.add_argument("--source-ref", default="", help="Source trace ID")
    parser.add_argument("--source-time", default="", help="Source timestamp (ISO-8601)")
    parser.add_argument("--mode", choices=["auto", "quote", "link", "mixed"], default="auto")
    parser.add_argument("--writer-base-url", default="", help="Override INGEST_WRITER_BASE_URL")
    parser.add_argument("--timeout-sec", type=int, default=0, help="Override timeout seconds")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    settings = _load_settings()

    if args.writer_base_url.strip():
        settings = dataclass_replace(settings, writer_base_url=args.writer_base_url.strip().rstrip("/"))
    if args.timeout_sec > 0:
        settings = dataclass_replace(settings, timeout_sec=max(1, args.timeout_sec))
    if args.insecure:
        settings = dataclass_replace(settings, verify_ssl=False)

    forced_mode = args.mode if args.mode != "auto" else ""
    routed = _route_payload(text=args.text, urls=args.urls, forced_mode=forced_mode)
    if routed.mode == "ignore":
        print("ignore: empty payload")
        return 0

    event_ref = args.event_ref.strip() or f"openclaw-{uuid.uuid4().hex}"
    source_time = args.source_time.strip() or _now_iso()
    source_ref = args.source_ref.strip() or event_ref

    payload = {
        "event_ref": event_ref,
        "source_kind": args.source_kind.strip() or "openclaw-feishu",
        "source_ref": source_ref,
        "source_time": source_time,
    }

    if routed.mode == "quote":
        endpoint = "/internal/ingest/v1/quote"
        payload["text"] = routed.quote_text
    elif routed.mode == "link":
        endpoint = "/internal/ingest/v1/link"
        payload["urls"] = routed.urls
        payload["text"] = args.text
    else:
        endpoint = "/internal/ingest/v1/mixed"
        payload["text"] = routed.quote_text
        payload["urls"] = routed.urls

    data = _post(settings, endpoint, payload)
    _print_result(data)
    return 0


def dataclass_replace(settings: BridgeSettings, **kwargs: Any) -> BridgeSettings:
    data = {
        "writer_base_url": settings.writer_base_url,
        "shared_token": settings.shared_token,
        "hmac_secret": settings.hmac_secret,
        "timeout_sec": settings.timeout_sec,
        "verify_ssl": settings.verify_ssl,
    }
    data.update(kwargs)
    return BridgeSettings(**data)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
