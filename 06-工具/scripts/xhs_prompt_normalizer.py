#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize XHS visual prompts into a stable bullet contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from xhs_flow import normalize_xhs_prompt_file


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize XHS prompt contract")
    parser.add_argument("target", help="Canonical XHS markdown file")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        result = normalize_xhs_prompt_file(Path(args.target).resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
