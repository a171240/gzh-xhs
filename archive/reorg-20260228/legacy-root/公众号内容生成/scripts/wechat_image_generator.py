import argparse
import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple


def parse_frontmatter(text: str) -> Dict[str, str]:
    data = {}
    text = text.lstrip('\ufeff')
    if text.startswith('---'):
        parts = text.split('---', 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ':' in line:
                    key, value = line.split(':', 1)
                    data[key.strip()] = value.strip()
    return data


def extract_prompts(text: str) -> List[Tuple[str, str]]:
    lines = text.splitlines()
    in_section = False
    in_code = False
    current_label = None
    buf: List[str] = []
    prompts: List[Tuple[str, str]] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('## ') and '配图提示词' in stripped:
            in_section = True
            continue
        if not in_section:
            continue
        if stripped.startswith('## ') and '配图提示词' not in stripped and not in_code:
            # End of prompt section
            break
        if stripped.startswith('### ') and not in_code:
            current_label = stripped.lstrip('#').strip()
            continue
        if stripped.startswith('```'):
            if not in_code:
                in_code = True
                buf = []
            else:
                in_code = False
                prompt = '\n'.join(buf).strip()
                if current_label and prompt:
                    prompts.append((current_label, prompt))
                buf = []
            continue
        if in_code:
            buf.append(line)

    return prompts


def account_abbr(account: str) -> str:
    mapping = {
        'IP内容工厂': 'gongchang',
        'IP工厂': 'ipgc',
        'IP增长引擎': 'zengzhang',
        '商业IP实战笔记': 'shizhan',
    }
    return mapping.get(account, re.sub(r'\s+', '', account))


def filename_for_label(label: str, index: int) -> str:
    if '封面' in label:
        return 'cover.png'
    m = re.search(r'配图\s*(\d+)', label)
    if m:
        return f"img-{int(m.group(1)):02d}.png"
    return f"img-{index:02d}.png"


def safe_page_type(label: str) -> str:
    return re.sub(r'\s+', '', label.replace('：', '-').replace(':', '-'))


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger(str(log_path))
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


def list_md_files(date_dir: Path) -> List[Path]:
    if not date_dir.exists():
        return []
    return sorted([p for p in date_dir.glob('*.md') if p.is_file()])


async def run_generate(md_path: Path, model: str, limit: int, retries: int) -> int:
    text = md_path.read_text(encoding='utf-8')
    fm = parse_frontmatter(text)
    prompts = extract_prompts(text)

    if not prompts:
        print('[WARN] No prompts found under "配图提示词" section.')
        return 1

    account = fm.get('账号', 'A')
    date = fm.get('日期', md_path.parent.name)
    abbr = account_abbr(account)

    # md_path: <root>/公众号内容生成/生成内容/<date>/file.md
    # crawler lives under <root>/小红书内容生成/crawler
    project_root = md_path.parents[3]
    crawler_dir = project_root / '小红书内容生成' / 'crawler'
    sys.path.insert(0, str(crawler_dir))
    try:
        from gemini_api_generator import GeminiAPIGenerator
    except Exception as exc:
        print(f'[FAIL] Cannot import GeminiAPIGenerator: {exc}')
        return 1

    output_dir = md_path.parent / 'images' / abbr
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / 'run.log'
    logger = setup_logger(log_path)
    logger.info(f"Start generation for {md_path}")
    logger.info(f"Account: {account} | Abbr: {abbr} | Date: {date} | Model: {model}")

    if limit > 0:
        prompts = prompts[:limit]

    generator = GeminiAPIGenerator(output_dir=str(output_dir), model=model)
    try:
        await generator.start()
    except Exception as exc:
        logger.error(f"Failed to init Gemini client: {exc}")
        return 1

    results = []
    try:
        for idx, (label, prompt) in enumerate(prompts, 1):
            page_type = safe_page_type(label)
            image_path = None
            for attempt in range(1, retries + 2):
                logger.info(f"[{idx}/{len(prompts)}] {label} attempt {attempt}/{retries+1}")
                image_path = await generator.generate_image(prompt, account=abbr, page_type=page_type)
                if image_path:
                    break
                if attempt <= retries:
                    logger.warning("Generation failed, retrying after 2s...")
                    await asyncio.sleep(2)
            target_name = filename_for_label(label, idx)
            target_path = output_dir / target_name

            if image_path:
                src = Path(image_path)
                if src.exists():
                    if target_path.exists():
                        target_path.unlink()
                    src.rename(target_path)
                image_path = str(target_path)
                logger.info(f"Saved: {image_path}")
            else:
                logger.error(f"Failed: {label}")

            results.append({
                'label': label,
                'prompt': prompt,
                'output': image_path,
            })
    finally:
        await generator.close()

    index_path = output_dir / 'index.json'
    index_path.write_text(
        json.dumps({
            'account': account,
            'date': date,
            'source_md': str(md_path),
            'model': model,
            'images': results,
        }, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    logger.info(f"Index: {index_path}")
    logger.info("NOTE: Watermark/Logo (if any) is preserved as generated by Gemini.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Generate WeChat article images via Gemini (API)')
    parser.add_argument('md_file', nargs='?', help='Path to the article md file with prompts')
    parser.add_argument('--model', default='pro', choices=['pro', '2.5-pro', '2.5-flash'], help='Gemini model')
    parser.add_argument('--limit', type=int, default=0, help='Limit number of images to generate')
    parser.add_argument('--retries', type=int, default=2, help='Retry count per image')
    parser.add_argument('--batch-date', help='Generate all md files under 生成内容/<DATE>')
    args = parser.parse_args()

    if args.batch_date:
        base_dir = Path(__file__).resolve().parents[1]
        date_dir = base_dir / '生成内容' / args.batch_date
        md_files = list_md_files(date_dir)
        if not md_files:
            print(f'[FAIL] No md files found under: {date_dir}')
            return 1
        exit_code = 0
        for md_path in md_files:
            code = asyncio.run(run_generate(md_path, args.model, args.limit, args.retries))
            if code != 0:
                exit_code = code
        return exit_code

    if not args.md_file:
        print('[FAIL] Please provide md_file or --batch-date')
        return 1

    md_path = Path(args.md_file)
    if not md_path.exists():
        print(f'[FAIL] File not found: {md_path}')
        return 1

    return asyncio.run(run_generate(md_path, args.model, args.limit, args.retries))


if __name__ == '__main__':
    raise SystemExit(main())

