#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate Xiaohongshu images from the normalized prompt contract."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FALLBACK_FILES = (
    ".env.ingest-writer.local",
    ".env.ingest-writer",
    ".env.feishu",
)

from topic_doc_utils import safe_repo_relative
from xhs_flow import (
    REPO_ROOT,
    format_xhs_prompt_contract,
    load_xhs_content_profile,
    save_xhs_content_profile,
    validate_xhs_prompt_contract,
    xhs_manifest_path,
)


def load_runtime_env_fallbacks() -> None:
    for name in ENV_FALLBACK_FILES:
        env_path = SCRIPT_DIR / name
        if not env_path.exists():
            continue
        text = env_path.read_text(encoding="utf-8", errors="ignore")
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and not (os.getenv(key) or "").strip():
                os.environ[key] = value


def build_image_generator(*, output_dir: Path, model: str = "") -> tuple[Any, str]:
    load_runtime_env_fallbacks()
    evolink_api_key = str(os.getenv("EVOLINK_API_KEY") or "").strip()
    if not evolink_api_key:
        raise RuntimeError("Missing EVOLINK_API_KEY for XHS image generation")
    from evolink_image_generator import EvolinkImageGenerator

    return EvolinkImageGenerator(output_dir=str(output_dir), model=(model or None)), "evolink"


def _requested_size(slot: str) -> str:
    if slot == "cover":
        return "3:4"
    return "3:4"


async def _generate_one(generator: Any, *, account_prefix: str, slot: str, prompt: str) -> str | None:
    return await generator.generate_image(
        prompt=prompt,
        account=account_prefix,
        page_type=slot,
        size=_requested_size(slot),
    )


async def _generate_all(
    *,
    generator: Any,
    account_prefix: str,
    prompt_items: list[dict[str, Any]],
) -> list[str | None]:
    results: list[str | None] = []
    for item in prompt_items:
        results.append(
            await _generate_one(
                generator,
                account_prefix=account_prefix,
                slot=str(item.get("slot") or "").strip(),
                prompt=str(item.get("prompt") or "").strip(),
            )
        )
    return results


def _manifest_payload(*, content_path: Path, profile: Any, prompt_items: list[dict[str, Any]], generated_paths: list[str]) -> dict[str, Any]:
    images: list[dict[str, Any]] = []
    for order, (item, image_path) in enumerate(zip(prompt_items, generated_paths), start=1):
        candidate = Path(image_path).resolve()
        try:
            rel_path = candidate.relative_to(REPO_ROOT).as_posix()
        except Exception:
            rel_path = candidate.as_posix()
        images.append(
            {
                "slot": str(item.get("slot") or "").strip(),
                "order": order,
                "path": rel_path,
                "rel_path": rel_path,
                "prompt": str(item.get("prompt") or "").strip(),
            }
        )
    return {
        "content_path": safe_repo_relative(content_path),
        "account": profile.account,
        "account_prefix": profile.account_prefix,
        "mode": profile.mode,
        "images": images,
    }


def process_xhs_content_file(content_path: Path, *, dry_run: bool = False, model: str = "") -> dict[str, Any]:
    profile = load_xhs_content_profile(content_path)
    prompt_items = profile.prompt_contract
    errors = validate_xhs_prompt_contract(prompt_items, mode=profile.mode)
    if errors:
        raise RuntimeError("; ".join(errors))
    if dry_run:
        return {
            "status": "success",
            "dry_run": True,
            "processed_files": [
                {
                    "path": safe_repo_relative(content_path),
                    "mode": profile.mode,
                    "prompt_slots": [str(item.get("slot") or "") for item in prompt_items],
                }
            ],
        }

    output_dir = content_path.parent / "images" / profile.account_prefix
    output_dir.mkdir(parents=True, exist_ok=True)
    generator, provider = build_image_generator(output_dir=output_dir, model=model)
    asyncio.run(generator.start())
    try:
        generated = asyncio.run(
            _generate_all(
                generator=generator,
                account_prefix=profile.account_prefix,
                prompt_items=prompt_items,
            )
        )
    finally:
        asyncio.run(generator.close())

    generated_paths = [str(item or "").strip() for item in generated]
    if any(not item for item in generated_paths):
        failed: list[str] = []
        for item, image_path in zip(prompt_items, generated_paths):
            if image_path:
                continue
            slot = str(item.get("slot") or "").strip() or "unknown"
            error = str(getattr(generator, "last_error", "") or "empty file path").strip()
            failed.append(f"{slot}: {error}")
        raise RuntimeError("xhs image generation failed for " + "; ".join(failed))

    manifest_path = xhs_manifest_path(content_path)
    payload = _manifest_payload(content_path=content_path, profile=profile, prompt_items=prompt_items, generated_paths=generated_paths)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    updated = dataclasses.replace(
        profile,
        publish_ready=True,
        image_manifest_path=safe_repo_relative(manifest_path),
        image_items=list(payload["images"]),
        images=[str(item["rel_path"] or "").strip() for item in payload["images"]],
        prompt_text=format_xhs_prompt_contract(prompt_items, mode=profile.mode),
    )
    save_xhs_content_profile(updated, target_path=content_path)
    return {
        "status": "success",
        "provider": provider,
        "processed_files": [
            {
                "path": safe_repo_relative(content_path),
                "manifest": safe_repo_relative(manifest_path),
                "images": [str(item["rel_path"] or "").strip() for item in payload["images"]],
                "prompt_slots": [str(item.get("slot") or "") for item in prompt_items],
            }
        ],
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate XHS images")
    parser.add_argument("content_file", help="Canonical XHS markdown file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default="")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        result = process_xhs_content_file(
            Path(args.content_file).resolve(),
            dry_run=bool(args.dry_run),
            model=str(args.model or "").strip(),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
