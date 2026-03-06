# 仓库稳定收敛基线（2026-03-06）

## 当前基线
- 分支：`hotfix/verify-main2-linkfirst-20260303`
- 基线提交：`10cee64`
- 回归基线：`python -m pytest 06-工具/scripts/tests -q`
- 快照结果：`34 passed`
- 最近一次本地回归：`36 passed`
- 快照细节：`baseline-snapshot.md`
- 分桶清单：`change-buckets.md`
- runtime artifact 治理：`runtime-artifact-governance.md`
- runtime artifact 清理清单：`runtime-artifact-cleanup-checklist.md`

## 当前进度
- 第 1 批：基线快照 + owner-aware 分桶，已完成
- 第 2 批：repo-local 真相源收口（公众号优先），已完成
- 第 3 批：desktop 降级与 XHS 残余耦合边界标注，已完成
- 第 4 批：依赖/行尾/忽略策略收口，已完成
- 第 5 批：文档收口 + runtime artifact 治理，已完成

## owner-aware 分桶
- `source`
  - `06-工具/scripts/`
  - `skills/`
  - `.gitignore` / `.gitattributes`
  - 正式测试、配置、依赖定义
- `compat`
  - `06-工具/desktop-app/`
  - 仍保留旧入口的桥接脚本与说明
- `content-assets`
  - `01-选题管理/`
  - `02-内容生产/` 下人工维护的模板、方法论、正式内容资产
  - `03-素材库/`
  - `04-数据与方法论/`
  - `05-业务运营/`
- `runtime-artifacts`
  - `reports/`
  - 截图、run log、缓存、临时生成目录

## 收敛原则
- 不做版本回退
- 不做大规模目录迁移
- 不按目录粗暴清理 `01-05`
- 任何忽略、归档、删除策略只针对 `runtime-artifacts`
- 行尾策略本轮已设定，但对当前已修改文件的 CRLF/LF 一次性 renormalize 延后处理，避免制造大面积无效 diff
