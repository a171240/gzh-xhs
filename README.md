# 公众号内容生成仓库（稳定收敛基线）

本仓库当前处于“全仓稳定收敛”阶段：公众号主链优先，XHS 和其他工具链保留兼容，禁止用回退或大迁移换整洁。

## 根目录契约
- `.claude/`
- `.github/`
- `.gitignore`
- `.gitattributes`
- `01-选题管理/`
- `02-内容生产/`
- `03-素材库/`
- `04-数据与方法论/`
- `05-业务运营/`
- `06-工具/`
- `skills/`
- `archive/`
- `README.md`
- `CLAUDE.md`

## 当前主链入口
- 飞书编排器：`06-工具/scripts/feishu_kb_orchestrator.py`
- 选题流水线：`06-工具/scripts/topic_pipeline.py`
- 技能执行器：`06-工具/scripts/feishu_skill_runner.py`
- 发布动作：`06-工具/scripts/publish_action_runner.py`
- repo-local skill 注册：`skills/自有矩阵/skill-manifest.json`

## 真相源与兼容层
- 公众号 repo-local skill、context profile、图片/发布脚本，以 `scripts + skills/自有矩阵` 为真相源。
- `06-工具/desktop-app/` 仍保留，但角色已经降级为兼容层，不再作为公众号主链的定义源。
- XHS 与抓取桥接里仍有部分 desktop 兼容引用，属于后续下线对象，不在本轮强拆。

## 内容资产与运行产物边界
- `01-05` 下默认视为长期经营资产：选题、方法论、素材、分析报告、人工文档。
- `reports/`、截图、run log、临时生成目录、缓存等属于运行产物，只能按运行产物策略治理。
- 不得因为“整理仓库”而把 `01-05` 内容资产按目录整体忽略、归档或删除。

## 稳定收敛文档
- 基线与分桶：`archive/repo-stabilization-20260306/README.md`
- desktop 残余依赖盘点：`archive/repo-stabilization-20260306/desktop-dependency-inventory.md`
- runtime artifact 治理：`archive/repo-stabilization-20260306/runtime-artifact-governance.md`
- runtime artifact 清理清单：`archive/repo-stabilization-20260306/runtime-artifact-cleanup-checklist.md`
- 服务重启协议：`06-工具/scripts/README-服务重启协议.md`
- WeChat layout compiler 依赖说明：`06-工具/scripts/wechat_layout_compiler/README.md`
