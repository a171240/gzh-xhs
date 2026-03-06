# WeChat Image Generator

`wechat_image_generator.py` is the execution layer for WeChat article images.
It does not infer account style on its own. The expected flow is:

1. `公众号配图提示词标准化` reads the article plus account/style rules and rewrites `## 配图提示词`.
2. `公众号图片生成` only validates that normalized contract and calls the script.
3. `wechat_image_generator.py` consumes that contract, generates images, optionally compresses them, and writes results back to markdown.

The script prefers Evolink Nano Banana when `EVOLINK_API_KEY` is configured, and only falls back to the legacy Gemini cookie workflow when Evolink is unavailable.

## Normalized Prompt Contract

The article must contain `## 配图提示词`, using bullet items like:

```md
## 配图提示词
- 封面图：封面提示词
- 配图1（对应：01 价值入口机制）：正文图提示词
- 配图2（对应：02 证据密度机制）：正文图提示词
```

Rules:
- Exactly one `封面图`
- At least one body image prompt
- Body prompts use stable numbering
- `对应：...` is optional but recommended; when present, the script inserts the image near the matching `###` heading

Legacy `### 标题 + 代码块` format is still accepted for compatibility, but the skill should output the bullet contract.

## Requirements

- Python 3.10+
- Preferred provider: Evolink config in `06-工具/scripts/.env.ingest-writer.local`
  - `EVOLINK_BASE_URL`
  - `EVOLINK_API_KEY`
  - `EVOLINK_IMAGE_MODEL`
  - `EVOLINK_IMAGE_SIZE`
  - `EVOLINK_IMAGE_QUALITY`
- Optional fallback: `gemini-webapi` plus Gemini login cookies
  - `GEMINI_SECURE_1PSID`
  - `GEMINI_SECURE_1PSIDTS`

## Usage

Single file:

```bash
python 06-工具/scripts/wechat_image_generator.py 02-内容生产/公众号/生成内容/2026-03-08/gongchang-20260308-被看见机制.md --insert-to-md --compress --max-size-kb 500
```

Batch for one date:

```bash
python 06-工具/scripts/wechat_image_generator.py --batch-date 2026-03-08 --insert-to-md --compress --max-size-kb 500
```

Generate only, skip markdown write-back:

```bash
python 06-工具/scripts/wechat_image_generator.py 02-内容生产/公众号/生成内容/2026-03-08/gongchang-20260308-被看见机制.md --no-insert --compress
```

Skip compression:

```bash
python 06-工具/scripts/wechat_image_generator.py 02-内容生产/公众号/生成内容/2026-03-08/gongchang-20260308-被看见机制.md --insert-to-md --no-compress
```

## Output

Images are written under:

```text
02-内容生产/公众号/生成内容/<DATE>/images/<abbr>/
```

File naming is standardized after generation:
- `cover.<ext>`
- `img-01.<ext>`
- `img-02.<ext>`

When `--insert-to-md` is enabled, the script also:
- writes `cover_image` into frontmatter
- inserts body image references under `## 正文`

When `--compress` is enabled, the script:
- compresses oversized images to fit `--max-size-kb`
- may convert the final output to `.jpg`
- keeps markdown references and `index.json` aligned with the final filenames

## Generated Metadata

For every article the script writes:

- `run.log`
- `index.json`

`index.json` uses POSIX relative paths, for example:

```json
{
  "source_md": "02-内容生产/公众号/生成内容/2026-03-08/gongchang-20260308-被看见机制.md",
  "images": [
    {
      "label": "封面图",
      "output": "images/gongchang/cover.jpg",
      "relative_output": "images/gongchang/cover.jpg"
    }
  ]
}
```

## Failure Modes

Common hard failures:
- missing `## 配图提示词`
- prompt contract has no `封面图`
- prompt contract has duplicate body indexes
- provider authentication / quota / model access errors
- compression enabled but final file still exceeds `--max-size-kb`
