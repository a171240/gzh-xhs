# 金句增量导入说明

脚本：`06-工具/scripts/sync_flomo_quotes.py`

## 作用
- 从 flomo 导出 zip 增量导入到金句库（不重建历史文件）。
- 精确去重后写入；近似重复进入复核清单，不自动写入。
- 自动更新 `01-选题管理/选题规划/金句选题池.md`。
- 自动推送 TopN 选题到 `01-选题管理/01-待深化/`。
- 与飞书入库共用统一规则核心：`06-工具/scripts/quote_ingest_core.py`。

## 命令示例

### 1) 预检（dry-run，不改库）
```bash
python ".\\06-工具\\scripts\\sync_flomo_quotes.py" \
  --dry-run \
  --flomo-zip "e:\\下载\\flomo@李可-20260220.zip" \
  --report-file "04-数据与方法论/方法论沉淀/2026-02-20-flomo-import-dryrun.md" \
  --date "2026-02-20"
```

### 2) 正式写入（apply）
```bash
python ".\\06-工具\\scripts\\sync_flomo_quotes.py" \
  --apply \
  --flomo-zip "e:\\下载\\flomo@李可-20260220.zip" \
  --topic-pool "01-选题管理/选题规划/金句选题池.md" \
  --topn 20 \
  --report-file "03-素材库/金句库/导入记录/2026-02-20-flomo-import.md" \
  --date "2026-02-20"
```

## 参数
- `--flomo-zip`：flomo 导出 zip 路径。
- `--quote-dir`：金句库目录（默认 `03-素材库/金句库`）。
- `--topic-pool`：选题池文件路径（默认 `01-选题管理/选题规划/金句选题池.md`）。
- `--topn`：推送到待深化目录的数量（默认 20）。
- `--dry-run`：只统计，不写入。
- `--apply`：执行写入。
- `--report-file`：报告输出路径。
- `--date`：导入日期（`YYYY-MM-DD`）。

## 输出位置
- 金句库主题文件：`03-素材库/金句库/*.md`
- 导入记录：`03-素材库/金句库/导入记录/YYYY-MM-DD-flomo-import.md`
- 选题池：`01-选题管理/选题规划/金句选题池.md`
- 待深化 TopN：`01-选题管理/01-待深化/YYYY-MM-DD-*.md`

## 回滚方式
1. 先用 `git status` 确认本次变更文件范围。
2. 若仅回滚导入结果，恢复以下路径即可：
   - `03-素材库/金句库/*.md`
   - `03-素材库/金句库/导入记录/*`
   - `01-选题管理/选题规划/金句选题池.md`
   - `01-选题管理/01-待深化/YYYY-MM-DD-*.md`
3. 回滚前建议保留导入报告，用于核对 near-dup 与新增条目。
