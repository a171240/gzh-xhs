# 公众号内容生成仓库（主干收敛版）

本仓库已按 `d9adb1e` 主干结构收敛，根目录仅保留核心入口。

## 根目录契约
- `.claude/`
- `.github/`
- `.gitignore`
- `01-选题管理/`
- `02-内容生产/`
- `03-素材库/`
- `04-数据与方法论/`
- `05-业务运营/`
- `06-工具/`
- `skills/`
- `archive/`
- `CLAUDE.md`
- `README.md`
- `manifest-*.txt`

## 主流程入口
- 飞书编排器：`06-工具/scripts/feishu_kb_orchestrator.py`
- 选题流水线：`06-工具/scripts/topic_pipeline.py`
- 技能执行器：`06-工具/scripts/feishu_skill_runner.py`
- 本地状态检查：`06-工具/deploy/status-local-hub.ps1`

## 本次整理记录
- 整理批次：`archive/reorg-20260228/`
- 映射清单：`archive/reorg-20260228/reports/path-mapping.csv`
- 缺失审计：`archive/reorg-20260228/reports/missing-files.csv`
- 搬迁结果：`archive/reorg-20260228/reports/move-results.csv`

历史根目录入口与产物已迁入 `archive/reorg-20260228/legacy-root` 与 `archive/reorg-20260228/artifacts`。
