#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replay missing event_refs to Writer API and generate backfill audit report."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import uuid
from pathlib import Path
from typing import Any

import requests


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return dt.date.today().isoformat()


def _signature(secret: str, *, timestamp: str, nonce: str, body: bytes) -> str:
    payload = f"{timestamp}\n{nonce}\n".encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _headers(token: str, secret: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(dt.datetime.now(dt.timezone.utc).timestamp()))
    nonce = uuid.uuid4().hex
    sig = _signature(secret, timestamp=timestamp, nonce=nonce, body=body)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Ingest-Timestamp": timestamp,
        "X-Ingest-Nonce": nonce,
        "X-Ingest-Signature": sig,
    }


def _load_event_refs(args: argparse.Namespace) -> list[str]:
    refs: list[str] = []
    for item in args.event_ref:
        value = item.strip()
        if value:
            refs.append(value)
    if args.input_file:
        path = Path(args.input_file)
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            value = raw.strip()
            if value and not value.startswith("#"):
                refs.append(value)
    # dedupe while preserving order
    out: list[str] = []
    seen: set[str] = set()
    for item in refs:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _append_report(*, report_path: Path, rows: list[dict[str, Any]]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if not report_path.exists():
        lines.append("# 飞书离线补偿回放记录\n\n")
    lines.append(f"## {_now_iso()}\n")
    for row in rows:
        lines.append(f"- event_ref: `{row['event_ref']}`\n")
        lines.append(f"  - status: {row['status']}\n")
        lines.append(f"  - message: {row['message']}\n")
    lines.append("\n")
    original = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    report_path.write_text(original + "".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay event_refs to ingest writer API")
    parser.add_argument("--event-ref", action="append", default=[], help="event_ref to replay, repeatable")
    parser.add_argument("--input-file", default="", help="Text file containing event_refs, one per line")
    parser.add_argument("--writer-base-url", default=os.getenv("INGEST_WRITER_BASE_URL", "http://127.0.0.1:8790"))
    parser.add_argument("--token", default=os.getenv("INGEST_SHARED_TOKEN", ""))
    parser.add_argument("--secret", default=os.getenv("INGEST_HMAC_SECRET", ""))
    parser.add_argument("--timeout-sec", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report-file", default="", help="Override audit report file path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    refs = _load_event_refs(args)
    if not refs:
        print("no event_ref provided")
        return 1

    token = args.token.strip()
    secret = args.secret.strip() or token
    if not token or not secret:
        print("INGEST_SHARED_TOKEN / INGEST_HMAC_SECRET is required")
        return 1

    base = args.writer_base_url.strip().rstrip("/")
    endpoint = f"{base}/internal/ingest/v1/replay"
    rows: list[dict[str, Any]] = []

    for event_ref in refs:
        payload = {"event_ref": event_ref}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if args.dry_run:
            rows.append({"event_ref": event_ref, "status": "dry-run", "message": endpoint})
            continue

        try:
            response = requests.post(
                endpoint,
                headers=_headers(token, secret, body),
                data=body,
                timeout=max(1, args.timeout_sec),
            )
            if response.status_code >= 400:
                rows.append({"event_ref": event_ref, "status": "fail", "message": f"{response.status_code} {response.text}"})
                continue
            data = response.json()
            result = data.get("result") or {}
            rows.append(
                {
                    "event_ref": event_ref,
                    "status": result.get("status", "ok"),
                    "message": f"added={result.get('added', 0)} skipped={result.get('skipped', 0)}",
                }
            )
        except Exception as exc:
            rows.append({"event_ref": event_ref, "status": "error", "message": str(exc)})

    repo_root = _repo_root()
    report_path = (
        Path(args.report_file)
        if args.report_file
        else repo_root / "03-素材库" / "金句库" / "导入记录" / f"{_today()}-feishu-backfill.md"
    )
    _append_report(report_path=report_path, rows=rows)

    for row in rows:
        print(f"{row['event_ref']}: {row['status']} - {row['message']}")
    print(f"report={report_path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
