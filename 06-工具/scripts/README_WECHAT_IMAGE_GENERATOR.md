# WeChat Image Generator (Gemini API)

Generate images from WeChat article prompts using the Gemini reverse-call API (gemini-webapi).
Watermarks/Logos produced by Gemini are preserved (no removal).

## Requirements
- Python 3.10+
- `gemini-webapi`
- Gemini login cookies:
  - `GEMINI_SECURE_1PSID`
  - `GEMINI_SECURE_1PSIDTS` (optional)

These can be placed in environment variables or in:
`e:\公众号内容生成\06-工具\scripts\.env`

## Usage

```bash
python e:\公众号内容生成\06-工具\scripts\wechat_image_generator.py \
  e:\公众号内容生成\02-内容生产\公众号\生成内容\2026-01-29\ipgc-20260129-底层戾气.md
```

Optional:
```bash
python e:\公众号内容生成\06-工具\scripts\wechat_image_generator.py <md_file> --model 2.5-pro --limit 2
```

## Output
Images are saved under:
```
02-内容生产/公众号/生成内容/<DATE>/images/<账号缩写>/
```
An index file is generated:
```
index.json
```

## Notes
- Prompts are extracted from the `## 配图提示词` section (code blocks).
- If a label contains "封面", the output filename is `cover.png`.
- If a label contains "配图N", filename is `img-NN.png`.

Batch (same date, all md files):
```bash
python e:\公众号内容生成\06-工具\scripts\wechat_image_generator.py --batch-date 2026-01-29
```
Batch mode scans `02-内容生产/公众号/生成内容/<DATE>/` first, then falls back to legacy `06-工具/生成内容/<DATE>/`.

Retries:
```bash
python e:\公众号内容生成\06-工具\scripts\wechat_image_generator.py <md_file> --retries 2
```
Logs:
```
02-内容生产/公众号/生成内容/<DATE>/images/<账号缩写>/run.log
```
