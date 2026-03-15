#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entrypoint for the repo-local Xiaohongshu pipeline."""

from __future__ import annotations

import json
import sys

from xhs_flow import build_xhs_pipeline_parser, run_xhs_pipeline_command


def main(argv: list[str]) -> int:
    parser = build_xhs_pipeline_parser()
    args = parser.parse_args(argv)
    try:
        result = run_xhs_pipeline_command(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if str(result.get("status") or "") in {"success", "partial"} else 1
    except Exception as exc:
        print(json.dumps({"status": "error", "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
