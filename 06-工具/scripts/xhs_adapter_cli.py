#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI for XHS adapter boundary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from adapters.xhs.client import XHSAdapterClient


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="XHS adapter CLI")
    parser.add_argument("--action", required=True, choices=("publish", "search", "detail", "comment", "content_data"))
    parser.add_argument("--account", required=True, help="account id")
    parser.add_argument("--input", default="", help="JSON payload file path")
    parser.add_argument("--payload-json", default="", help="inline JSON payload")
    parser.add_argument("--idempotency-key", default="", help="dedupe key")
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--trace-id", default="")
    parser.add_argument("--config", default="", help="xhs adapter config path")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload_json:
        data = json.loads(args.payload_json)
        if not isinstance(data, dict):
            raise ValueError("--payload-json must be a JSON object")
        return data
    if args.input:
        path = Path(args.input).resolve()
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("--input JSON must be a JSON object")
        return data
    return {}


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    payload = _load_payload(args)
    if args.config:
        config_path = Path(args.config).resolve()
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        client = XHSAdapterClient(config=config, repo_root=Path.cwd())
    else:
        client = XHSAdapterClient.from_env(repo_root=Path.cwd())
    result = client.execute(
        action=args.action,
        account_id=args.account,
        payload=payload,
        idempotency_key=args.idempotency_key,
        dry_run=args.dry_run,
        timeout_sec=args.timeout_sec,
        trace_id=args.trace_id,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=True, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

