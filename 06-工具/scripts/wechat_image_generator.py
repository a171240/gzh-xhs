#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate WeChat article images from normalized prompt sections."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
TOOLS_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = TOOLS_ROOT.parent
WECHAT_CONTENT_ROOT = WORKSPACE_ROOT / "02-内容生产" / "公众号" / "生成内容"
LEGACY_WECHAT_CONTENT_ROOT = TOOLS_ROOT / "生成内容"

ENV_FALLBACK_FILES = (
    ".env.ingest-writer.local",
    ".env.ingest-writer",
    ".env.feishu",
)
FRONTMATTER_LINE_RE = re.compile(r"^([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*:\s*(.*?)\s*$")
SECTION_HEADING_RE = re.compile(r"^##\s+")
BODY_IMAGE_RE_TEMPLATE = r"^\s*!\[[^\]]*\]\({prefix}/[^)]+\)\s*$"
PROMPT_BULLET_RE = re.compile(
    r"^(?P<label>(?:封面图|配图\d+(?:[（(]\s*对应\s*[:：]?\s*[^)）]+[)）])?|图\d+(?:[（(]\s*对应\s*[:：]?\s*[^)）]+[)）])?))\s*[:：]\s*(?P<prompt>.+?)\s*$"
)
FATAL_GENERATION_ERROR_CODES = {
    "authentication_error",
    "invalid_api_key",
    "insufficient_quota",
    "model_access_denied",
    "image_generation_unavailable",
}

DEFAULT_COVER_SIZE = "21:9"
DEFAULT_BODY_SIZE = "3:4"


@dataclasses.dataclass(frozen=True)
class PromptSpec:
    label: str
    prompt: str
    is_cover: bool
    index: int
    anchor: str
    filename_stub: str
    page_type: str


def requested_size_for_spec(spec: PromptSpec) -> str:
    if spec.is_cover:
        return DEFAULT_COVER_SIZE
    return DEFAULT_BODY_SIZE


def read_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def write_markdown(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


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


def build_image_generator(*, output_dir: Path, model: str) -> tuple[Any, str]:
    load_runtime_env_fallbacks()

    evolink_api_key = str(os.getenv("EVOLINK_API_KEY") or "").strip()
    if evolink_api_key:
        from evolink_image_generator import EvolinkImageGenerator

        generator = EvolinkImageGenerator(
            output_dir=str(output_dir),
            model=(model or None),
        )
        return generator, "evolink"

    from gemini_api_generator import GeminiAPIGenerator

    generator = GeminiAPIGenerator(
        output_dir=str(output_dir),
        model=(model or "pro"),
    )
    return generator, "gemini_webapi"


def split_frontmatter(text: str) -> tuple[str, str, str]:
    raw = text.lstrip("\ufeff")
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    if not raw.startswith("---"):
        return bom, "", raw

    lines = raw.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return bom, "", raw

    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            block = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            return bom, block, body
    return bom, "", raw


def parse_frontmatter(text: str) -> dict[str, str]:
    _, block, _ = split_frontmatter(text)
    data: dict[str, str] = {}
    if not block:
        return data
    for line in block.splitlines():
        matched = FRONTMATTER_LINE_RE.match(line.strip())
        if not matched:
            continue
        key = str(matched.group(1) or "").strip()
        value = str(matched.group(2) or "").strip().strip('"').strip("'")
        if key:
            data[key] = value
    return data


def yaml_string(value: str) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def upsert_frontmatter(text: str, updates: dict[str, str]) -> str:
    bom, block, body = split_frontmatter(text)
    lines = block.splitlines() if block else []
    seen: set[str] = set()
    output_lines: list[str] = []

    for line in lines:
        matched = FRONTMATTER_LINE_RE.match(line.strip())
        if not matched:
            output_lines.append(line)
            continue
        key = str(matched.group(1) or "").strip()
        if key in updates:
            output_lines.append(f"{key}: {yaml_string(updates[key])}")
            seen.add(key)
        else:
            output_lines.append(line)

    for key, value in updates.items():
        if key not in seen:
            output_lines.append(f"{key}: {yaml_string(value)}")

    body_text = body.lstrip("\r\n")
    frontmatter = "\n".join(output_lines).strip("\n")
    if not frontmatter:
        return f"{bom}{body_text}"
    return f"{bom}---\n{frontmatter}\n---\n\n{body_text}"


def find_section_range(lines: list[str], heading: str) -> tuple[int, int] | None:
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            start = idx + 1
            break
    if start is None:
        return None

    end = len(lines)
    for idx in range(start, len(lines)):
        stripped = lines[idx].strip()
        if SECTION_HEADING_RE.match(stripped) and stripped != heading:
            end = idx
            break
    return start, end


def _parse_bullet_prompts(section_lines: list[str]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    current_label = ""
    current_parts: list[str] = []

    def split_prompt_item(item_text: str) -> tuple[str, str] | None:
        text = str(item_text or "").strip()
        if not text:
            return None
        if text.startswith("封面图"):
            remainder = text[len("封面图") :].lstrip()
            if remainder.startswith((":", "：")):
                return "封面图", remainder[1:].strip()
            return None

        matched = re.match(r"^(配图\d+|图\d+)", text)
        if not matched:
            return None
        label_end = matched.end()
        remainder = text[label_end:].lstrip()
        label = matched.group(1)

        if remainder.startswith(("（", "(")):
            stack = [remainder[0]]
            idx = 1
            while idx < len(remainder) and stack:
                char = remainder[idx]
                if char in {"（", "("}:
                    stack.append(char)
                elif char in {"）", ")"}:
                    stack.pop()
                idx += 1
            if stack:
                return None
            label = f"{label}{remainder[:idx]}"
            remainder = remainder[idx:].lstrip()

        if not remainder.startswith((":", "：")):
            return None
        return label.strip(), remainder[1:].strip()

    def flush() -> None:
        nonlocal current_label, current_parts
        prompt = "\n".join(part for part in current_parts if part.strip()).strip()
        if current_label and prompt:
            items.append((current_label, prompt))
        current_label = ""
        current_parts = []

    for raw_line in section_lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith(("- ", "* ")):
            flush()
            item_text = stripped[2:].strip()
            parsed = split_prompt_item(item_text)
            if parsed:
                current_label, first_prompt = parsed
                current_parts = [first_prompt]
            else:
                separator_indexes = [idx for idx, char in enumerate(item_text) if char in {":", "："}]
                if separator_indexes:
                    split_at = separator_indexes[0]
                    current_label = item_text[:split_at].strip()
                    current_parts = [item_text[split_at + 1 :].strip()]
                else:
                    current_label = item_text
                    current_parts = []
            continue
        if current_label:
            current_parts.append(stripped)
    flush()
    return items


def _parse_code_block_prompts(section_lines: list[str]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    current_label = ""
    in_code = False
    buffer: list[str] = []

    for raw_line in section_lines:
        stripped = raw_line.strip()
        if stripped.startswith("### ") and not in_code:
            current_label = stripped[4:].strip()
            continue
        if stripped.startswith("```"):
            if in_code:
                prompt = "\n".join(buffer).strip()
                if current_label and prompt:
                    items.append((current_label, prompt))
                buffer = []
                in_code = False
            else:
                in_code = True
                buffer = []
            continue
        if in_code:
            buffer.append(raw_line.rstrip())
    return items


def _prompt_index(label: str, order: int) -> int:
    matched = re.search(r"(\d+)", str(label or ""))
    if matched:
        return max(1, int(matched.group(1)))
    return max(1, order)


def _prompt_anchor(label: str) -> str:
    text = str(label or "").strip()
    matched = re.match(r"^(?:配图\d+|图\d+)[（(]\s*对应\s*[:：]?\s*(.+)[)）]\s*$", text)
    if matched:
        return str(matched.group(1) or "").strip()
    matched = re.search(r"[（(]\s*对应\s*[:：]?\s*([^)）]+)[)）]", text)
    if matched:
        return str(matched.group(1) or "").strip()
    matched = re.search(r"对应\s*[:：]?\s*([^\s]+(?:\s+[^\s]+)*)", text)
    if matched:
        return str(matched.group(1) or "").strip()
    return ""


def _prompt_label(label: str, *, is_cover: bool, index: int, anchor: str) -> str:
    raw = str(label or "").strip()
    if raw:
        return raw
    if is_cover:
        return "封面图"
    if anchor:
        return f"配图{index}（对应：{anchor}）"
    return f"配图{index}"


def _build_prompt_spec(label: str, prompt: str, order: int) -> PromptSpec:
    raw_label = str(label or "").strip()
    is_cover = "封面" in raw_label
    index = 0 if is_cover else _prompt_index(raw_label, order)
    anchor = "" if is_cover else _prompt_anchor(raw_label)
    final_label = _prompt_label(raw_label, is_cover=is_cover, index=index, anchor=anchor)
    filename_stub = "cover" if is_cover else f"img-{index:02d}"
    page_type = "cover" if is_cover else f"img-{index:02d}"
    return PromptSpec(
        label=final_label,
        prompt=str(prompt or "").strip(),
        is_cover=is_cover,
        index=index,
        anchor=anchor,
        filename_stub=filename_stub,
        page_type=page_type,
    )


def extract_prompt_specs(text: str) -> list[PromptSpec]:
    lines = text.splitlines()
    section = find_section_range(lines, "## 配图提示词")
    if not section:
        return []
    start, end = section
    section_lines = lines[start:end]
    raw_items = _parse_bullet_prompts(section_lines)
    if not raw_items:
        raw_items = _parse_code_block_prompts(section_lines)

    specs: list[PromptSpec] = []
    body_order = 1
    for label, prompt in raw_items:
        spec = _build_prompt_spec(label, prompt, body_order)
        specs.append(spec)
        if not spec.is_cover:
            body_order = spec.index + 1
    return specs


def extract_prompts(text: str) -> list[tuple[str, str]]:
    return [(spec.label, spec.prompt) for spec in extract_prompt_specs(text)]


def validate_prompt_specs(specs: list[PromptSpec]) -> None:
    if not specs:
        raise RuntimeError('missing prompts under "## 配图提示词"')

    cover_count = sum(1 for item in specs if item.is_cover)
    if cover_count != 1:
        raise RuntimeError("prompt contract requires exactly one cover prompt")

    body_specs = [item for item in specs if not item.is_cover]
    if not body_specs:
        raise RuntimeError("prompt contract requires at least one body image prompt")

    indexes = [item.index for item in body_specs]
    if len(indexes) != len(set(indexes)):
        raise RuntimeError("duplicate body image indexes found in prompt contract")


def account_abbr(account: str) -> str:
    mapping = {
        "IP内容工厂": "gongchang",
        "IP工厂": "ipgc",
        "IP增长引擎": "zengzhang",
        "商业IP实战笔记": "shizhan",
    }
    return mapping.get(str(account or "").strip(), re.sub(r"\s+", "", str(account or "").strip()) or "wechat")


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger(str(log_path))
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def list_md_files(date_dir: Path) -> list[Path]:
    if not date_dir.exists():
        return []
    return sorted(path for path in date_dir.glob("*.md") if path.is_file())


def normalize_blank_lines(lines: list[str]) -> list[str]:
    output: list[str] = []
    blank_count = 0
    for line in lines:
        if line.strip():
            output.append(line)
            blank_count = 0
            continue
        if blank_count == 0:
            output.append("")
        blank_count += 1
    return output


def _normalize_text_key(value: str) -> str:
    lowered = str(value or "").strip().lower()
    if not lowered:
        return ""
    lowered = re.sub(r"^[0-9０-９]+[\s\.\-、:：]+", "", lowered)
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", lowered)


def _heading_number(value: str) -> str:
    matched = re.match(r"^\s*([0-9０-９]+)", str(value or ""))
    if not matched:
        return ""
    return matched.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def _match_heading_index(anchor: str, headings: list[tuple[int, str]]) -> int | None:
    anchor_text = str(anchor or "").strip()
    if not anchor_text:
        return None
    anchor_key = _normalize_text_key(anchor_text)
    anchor_number = _heading_number(anchor_text)

    for idx, title in headings:
        title_key = _normalize_text_key(title)
        if anchor_key and (anchor_key == title_key or anchor_key in title_key or title_key in anchor_key):
            return idx
        if anchor_number and anchor_number == _heading_number(title):
            return idx
    return None


def insert_body_images(text: str, body_refs: list[dict[str, str]], *, abbr: str) -> str:
    if not body_refs:
        return text

    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    section = find_section_range(lines, "## 正文")
    if not section:
        raise RuntimeError("missing `## 正文` section")

    start, end = section
    body_lines = list(lines[start:end])
    image_line_re = re.compile(BODY_IMAGE_RE_TEMPLATE.format(prefix=re.escape(f"images/{abbr}")))
    body_lines = [line for line in body_lines if not image_line_re.match(line.strip())]

    heading_positions: list[tuple[int, str]] = []
    for idx, line in enumerate(body_lines):
        stripped = line.strip()
        if stripped.startswith("### "):
            heading_positions.append((idx, stripped[4:].strip()))

    assignments: dict[int, list[dict[str, str]]] = {}
    unassigned: list[dict[str, str]] = []
    sequential_heading_indexes = [idx for idx, _ in heading_positions]
    sequential_pointer = 0

    for ref in body_refs:
        anchor = str(ref.get("anchor") or "").strip()
        matched_idx = _match_heading_index(anchor, heading_positions)
        if matched_idx is None and sequential_pointer < len(sequential_heading_indexes):
            matched_idx = sequential_heading_indexes[sequential_pointer]
            sequential_pointer += 1
        if matched_idx is None:
            unassigned.append(ref)
            continue
        assignments.setdefault(matched_idx, []).append(ref)

    rebuilt: list[str] = []
    for idx, line in enumerate(body_lines):
        rebuilt.append(line)
        if idx not in assignments:
            continue
        if rebuilt and rebuilt[-1].strip():
            rebuilt.append("")
        for ref in assignments[idx]:
            rebuilt.append(f"![{ref['label']}]({ref['relative_output']})")
            rebuilt.append("")

    if unassigned:
        if rebuilt and rebuilt[-1].strip():
            rebuilt.append("")
        for ref in unassigned:
            rebuilt.append(f"![{ref['label']}]({ref['relative_output']})")
            rebuilt.append("")

    rebuilt = normalize_blank_lines(rebuilt)
    new_lines = lines[:start] + rebuilt + lines[end:]
    return newline.join(new_lines) + newline


def write_back_markdown(md_path: Path, results: list[dict[str, Any]], *, abbr: str, logger: logging.Logger) -> None:
    text = read_markdown(md_path)
    cover_ref = next((item for item in results if item.get("is_cover") and item.get("relative_output")), None)
    updates: dict[str, str] = {}
    if cover_ref:
        updates["cover_image"] = str(cover_ref["relative_output"])
    if updates:
        text = upsert_frontmatter(text, updates)

    body_refs = [
        {
            "label": str(item.get("label") or f"配图{item.get('index') or ''}").strip(),
            "relative_output": str(item.get("relative_output") or "").strip(),
            "anchor": str(item.get("anchor") or "").strip(),
        }
        for item in results
        if not item.get("is_cover") and str(item.get("relative_output") or "").strip()
    ]
    if body_refs:
        text = insert_body_images(text, body_refs, abbr=abbr)
    write_markdown(md_path, text)
    logger.info("Updated markdown: %s", md_path)


def compress_image(image_path: Path, *, max_size_kb: int, logger: logging.Logger) -> Path:
    max_bytes = max(1, max_size_kb) * 1024
    if image_path.stat().st_size <= max_bytes:
        return image_path

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("`--compress` requires Pillow (`pip install Pillow`)") from exc

    final_path = image_path.with_suffix(".jpg")
    quality_steps = [92, 85, 78, 72, 66, 60, 54, 48, 42]

    with Image.open(image_path) as original:
        image = original.convert("RGB")
        current = image
        for resize_factor in (1.0, 0.92, 0.84, 0.76):
            if resize_factor < 1.0:
                width = max(1, int(image.width * resize_factor))
                height = max(1, int(image.height * resize_factor))
                current = image.resize((width, height))
            for quality in quality_steps:
                current.save(final_path, format="JPEG", quality=quality, optimize=True)
                if final_path.stat().st_size <= max_bytes:
                    if final_path.resolve() != image_path.resolve() and image_path.exists():
                        image_path.unlink()
                    logger.info(
                        "Compressed %s -> %s (%.1f KB)",
                        image_path.name,
                        final_path.name,
                        final_path.stat().st_size / 1024,
                    )
                    return final_path

    raise RuntimeError(f"failed to compress `{image_path.name}` below {max_size_kb} KB")


def rel_output(md_path: Path, asset_path: Path) -> str:
    return asset_path.relative_to(md_path.parent).as_posix()


def repo_posix(path: Path) -> str:
    candidate = path.resolve()
    try:
        return candidate.relative_to(WORKSPACE_ROOT).as_posix()
    except Exception:
        return candidate.as_posix()


def _standardized_output_path(output_dir: Path, filename_stub: str, source_path: Path) -> Path:
    suffix = source_path.suffix.lower() or ".png"
    return output_dir / f"{filename_stub}{suffix}"


def _move_to_standard_name(source_path: Path, target_path: Path) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() == target_path.resolve():
        return target_path
    if target_path.exists():
        target_path.unlink()
    source_path.replace(target_path)
    return target_path


def post_process_results(
    *,
    md_path: Path,
    output_dir: Path,
    results: list[dict[str, Any]],
    abbr: str,
    insert_to_md: bool,
    compress: bool,
    max_size_kb: int,
    logger: logging.Logger,
) -> tuple[list[dict[str, Any]], bool]:
    had_failures = any(not item.get("output") for item in results)
    final_results: list[dict[str, Any]] = []

    for item in results:
        current = dict(item)
        output = str(current.get("output") or "").strip()
        if not output:
            final_results.append(current)
            continue

        output_path = Path(output).resolve()
        if compress:
            output_path = compress_image(output_path, max_size_kb=max_size_kb, logger=logger)

        output_path = _move_to_standard_name(
            output_path,
            _standardized_output_path(output_dir, str(current.get("filename_stub") or ""), output_path),
        )

        current["absolute_output"] = str(output_path.resolve())
        current["output"] = rel_output(md_path, output_path)
        current["relative_output"] = current["output"]
        current["repo_output"] = repo_posix(output_path)
        final_results.append(current)

    if insert_to_md:
        write_back_markdown(md_path, final_results, abbr=abbr, logger=logger)

    return final_results, had_failures


async def run_generate(
    md_path: Path,
    model: str,
    limit: int,
    retries: int,
    *,
    insert_to_md: bool,
    compress: bool,
    max_size_kb: int,
) -> int:
    text = read_markdown(md_path)
    fm = parse_frontmatter(text)
    specs = extract_prompt_specs(text)

    try:
        validate_prompt_specs(specs)
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 1

    account = str(fm.get("账号") or "").strip() or "公众号"
    date_str = str(fm.get("日期") or md_path.parent.name).strip() or md_path.parent.name
    abbr = account_abbr(account)
    output_dir = md_path.parent / "images" / abbr
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path = output_dir / "run.log"
    logger = setup_logger(log_path)
    logger.info("Start generation for %s", md_path)
    logger.info(
        "Account=%s | Abbr=%s | Date=%s | Model=%s | insert_to_md=%s | compress=%s | max_size_kb=%s",
        account,
        abbr,
        date_str,
        model or "default",
        insert_to_md,
        compress,
        max_size_kb,
    )

    if limit > 0:
        body_limit = 1 + max(0, limit)
        specs = [specs[0]] + [item for item in specs if not item.is_cover][:limit]
        if len(specs) > body_limit:
            specs = specs[:body_limit]

    try:
        generator, provider = build_image_generator(output_dir=output_dir, model=model)
    except Exception as exc:
        print(f"[FAIL] Cannot initialize image generator: {exc}")
        return 1

    resolved_model = str(getattr(generator, "model_id", "") or model or "default")
    logger.info("Provider=%s | ResolvedModel=%s", provider, resolved_model)

    raw_results: list[dict[str, Any]] = []
    exit_code = 0

    try:
        await generator.start()
        for spec in specs:
            logger.info("Generating %s", spec.label)
            generated_path: str | None = None
            error = ""
            error_code = ""

            for attempt in range(1, max(1, retries) + 2):
                generated_path = await generator.generate_image(
                    spec.prompt,
                    account=abbr,
                    page_type=spec.page_type,
                    size=requested_size_for_spec(spec),
                )
                if generated_path:
                    break
                error = str(getattr(generator, "last_error", "") or "").strip()
                error_code = str(getattr(generator, "last_error_code", "") or "").strip()
                logger.warning(
                    "Attempt %s failed for %s | code=%s | error=%s",
                    attempt,
                    spec.label,
                    error_code or "unknown",
                    error or "unknown",
                )
                if error_code in FATAL_GENERATION_ERROR_CODES:
                    break

            if not generated_path:
                exit_code = 1
                raw_results.append(
                    {
                        "label": spec.label,
                        "prompt": spec.prompt,
                        "index": spec.index,
                        "anchor": spec.anchor,
                        "filename_stub": spec.filename_stub,
                        "is_cover": spec.is_cover,
                        "page_type": spec.page_type,
                        "output": "",
                        "error": error or "image generation failed",
                        "error_code": error_code or "image_generation_failed",
                    }
                )
                if error_code in FATAL_GENERATION_ERROR_CODES:
                    logger.error("Fatal provider error, stop batch for file: %s", error_code)
                    break
                continue

            raw_results.append(
                {
                    "label": spec.label,
                    "prompt": spec.prompt,
                    "index": spec.index,
                    "anchor": spec.anchor,
                    "filename_stub": spec.filename_stub,
                    "is_cover": spec.is_cover,
                    "page_type": spec.page_type,
                    "output": generated_path,
                    "error": "",
                    "error_code": "",
                }
            )
    finally:
        await generator.close()

    try:
        final_results, had_failures = post_process_results(
            md_path=md_path,
            output_dir=output_dir,
            results=raw_results,
            abbr=abbr,
            insert_to_md=insert_to_md,
            compress=compress,
            max_size_kb=max_size_kb,
            logger=logger,
        )
        if had_failures:
            exit_code = 1
    except Exception as exc:
        logger.error("Post process failed: %s", exc)
        final_results = raw_results
        exit_code = 1

    index_path = output_dir / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "account": account,
                "date": date_str,
                "source_md": repo_posix(md_path),
                "provider": provider,
                "requested_model": model or None,
                "model": str(getattr(generator, "model_id", "") or resolved_model),
                "insert_to_md": insert_to_md,
                "compress": compress,
                "max_size_kb": max_size_kb,
                "images": final_results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info("Index: %s", index_path)
    logger.info("Finished generation for %s with exit_code=%s", md_path.name, exit_code)
    return exit_code


def _resolve_date_dir(batch_date: str) -> Path:
    date_dir = WECHAT_CONTENT_ROOT / batch_date
    if date_dir.exists():
        return date_dir
    return LEGACY_WECHAT_CONTENT_ROOT / batch_date


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate WeChat article images via configured image provider")
    parser.add_argument("md_file", nargs="?", help="Path to the article md file with prompts")
    parser.add_argument(
        "--model",
        default="",
        help="Model id or alias. Evolink defaults to EVOLINK_IMAGE_MODEL; Gemini fallback defaults to pro.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit number of body images to generate")
    parser.add_argument("--retries", type=int, default=2, help="Retry count per image")
    parser.add_argument("--batch-date", help="Generate all md files under 02-内容生产/公众号/生成内容/<DATE>")

    insert_group = parser.add_mutually_exclusive_group()
    insert_group.add_argument(
        "--insert-to-md",
        dest="insert_to_md",
        action="store_true",
        help="Write cover_image and body image references back to markdown",
    )
    insert_group.add_argument(
        "--no-insert",
        dest="insert_to_md",
        action="store_false",
        help="Skip markdown write-back",
    )

    compress_group = parser.add_mutually_exclusive_group()
    compress_group.add_argument(
        "--compress",
        dest="compress",
        action="store_true",
        help="Compress generated images to fit the size limit",
    )
    compress_group.add_argument(
        "--no-compress",
        dest="compress",
        action="store_false",
        help="Skip compression",
    )

    parser.set_defaults(insert_to_md=False, compress=False)
    parser.add_argument(
        "--max-size-kb",
        type=int,
        default=500,
        help="Target max file size in KB when --compress is enabled",
    )
    args = parser.parse_args()

    if args.batch_date:
        date_dir = _resolve_date_dir(args.batch_date)
        md_files = list_md_files(date_dir)
        if not md_files:
            print(f"[FAIL] No md files found under: {date_dir}")
            return 1
        exit_code = 0
        for md_path in md_files:
            code = asyncio.run(
                run_generate(
                    md_path.resolve(),
                    args.model,
                    args.limit,
                    args.retries,
                    insert_to_md=args.insert_to_md,
                    compress=args.compress,
                    max_size_kb=args.max_size_kb,
                )
            )
            if code != 0:
                exit_code = code
        return exit_code

    if not args.md_file:
        print("[FAIL] Please provide md_file or --batch-date")
        return 1

    md_path = Path(args.md_file)
    if not md_path.exists():
        print(f"[FAIL] File not found: {md_path}")
        return 1

    return asyncio.run(
        run_generate(
            md_path.resolve(),
            args.model,
            args.limit,
            args.retries,
            insert_to_md=args.insert_to_md,
            compress=args.compress,
            max_size_kb=args.max_size_kb,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
