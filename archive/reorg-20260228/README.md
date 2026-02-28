# reorg-20260228

本目录记录 2026-02-28 的仓库收敛整理。

## 目标
- 以 `d9adb1e` 主干结构为目标收敛。
- 历史入口与散落文件迁入归档，不直接删除业务内容。
- 输出可追踪映射与恢复报告。

## 结构
- `reports/path-mapping.csv`：旧路径到新路径映射。
- `reports/missing-files.csv`：缺失与恢复状态。
- `reports/move-results.csv`：实际搬迁执行结果。
- `reports/summary.md`：本次整理摘要。
- `legacy-root/`：历史根目录入口归档。
- `artifacts/`：产物目录归档（tmp/reports/zip）。
- `root-md-unclassified/`：根目录未归类文档。

## 回溯
如需回溯单文件，请按 `reports/path-mapping.csv` 的 `old_path` 与 `new_path` 做逆向移动。
