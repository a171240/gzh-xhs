# 对标链接库

本目录用于记录飞书直投链接（视频/小红书/网页）的处理结果。

## 文件约定
- 每日记录：`YYYY-MM-DD-feishu-links.md`
- 每条记录至少包含：
  - 原始链接 URL
  - 处理状态（success/failed）
  - 抓取摘要文件路径（如有）
  - 提炼文本字符数
  - 入库新增条数 / 近似复核条数
  - 失败原因（如有）

## 使用说明
- 该目录作为链接来源审计，不替代金句库。
- 真正的可复用内容仍沉淀在：
  - `03-素材库/金句库/*.md`
  - `01-选题管理/选题规划/金句选题池.md`

## 固定流程：每天自动分析昨天提取文档

### 1) 手动执行一次（推荐先验收）

```powershell
python 06-工具/scripts/benchmark_analysis_runner.py --mode yesterday
```

等价 PowerShell 包装脚本：

```powershell
powershell -ExecutionPolicy Bypass -File 06-工具/scripts/run-benchmark-analysis.ps1 -Mode yesterday
```

### 2) 产出位置

- 输入目录：`03-素材库/对标链接库/提取正文/YYYY-MM-DD/*.md`
- 输出目录：`03-素材库/对标链接库/分析报告/YYYY-MM-DD/*-分析.md`
- 索引文件：`03-素材库/对标链接库/分析报告/YYYY-MM-DD/00-自动分析索引.md`

### 3) 自动调度（已接入）

`06-工具/scripts/automation_scheduler.py` 已接入该任务，会在调度运行时自动扫描“昨天”的提取正文并补齐分析报告。

### 4) Windows 任务计划程序（可选）

可配置为每天固定时间执行以下命令：

```powershell
powershell -ExecutionPolicy Bypass -File e:\公众号内容生成\06-工具\scripts\run-benchmark-analysis.ps1 -Mode yesterday
```
