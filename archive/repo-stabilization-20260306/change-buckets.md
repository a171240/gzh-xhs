# 改动分桶（owner-aware）

## `source`
这些是应该继续纳管、继续演进、继续测试的内容。

- 仓库级策略与文档
  - `README.md`
  - `.gitattributes`
  - `.gitignore`
  - `archive/repo-stabilization-20260306/*`
- repo-local skill 主链
  - `skills/自有矩阵/skill-manifest.json`
  - `skills/自有矩阵/公众号*.md`
  - `skills/自有矩阵/小红书内容生产.md`
- 核心脚本
  - `06-工具/scripts/feishu_skill_runner.py`
  - `06-工具/scripts/feishu_kb_orchestrator.py`
  - `06-工具/scripts/skill_manifest.py`
  - `06-工具/scripts/skill_context_resolver.py`
  - `06-工具/scripts/topic_brief_builder.py`
  - `06-工具/scripts/topic_pipeline.py`
  - `06-工具/scripts/wechat_*`
  - `06-工具/scripts/publish_*`
  - `06-工具/scripts/evolink_image_generator.py`
- 正式依赖与测试
  - `06-工具/scripts/wechat_layout_compiler/*`（不含 `node_modules/`）
  - `06-工具/scripts/tests/*`
  - `06-工具/scripts/config/selectors.wechat.json`
  - `06-工具/scripts/adapters/xhs/*`

## `compat`
这些内容保留，但不再作为公众号主链真相源。

- `06-工具/desktop-app/*`
- `06-工具/scripts/crawl_bridge.py`
- `06-工具/desktop-app/data/prompts.default.json`
- 仍引用 desktop 的兼容说明文档

## `content-assets`
这些默认视为长期经营资产，不得按运行产物策略清理。

- `01-选题管理/*`
- `02-内容生产/*` 下人工维护的模板、方法论、正式内容
- `03-素材库/*`
- `04-数据与方法论/*`
- `05-业务运营/*`

当前应显式视为内容资产的新增示例：
- `01-选题管理/次日创作输入/2026-03-06-建议Brief.md`
- `03-素材库/对标链接库/分析报告/2026-03-05/00-自动分析索引.md`
- `04-数据与方法论/方法论沉淀/日报/2026-03-06.md`

## `runtime-artifacts`
这些只能按运行产物治理，不应继续污染 source/source-like 区域。

- `reports/`
- `reports/*` 下的日期目录、smoke 目录、dry-run payload、副本截图与 render 预览
- 截图、run log、临时生成目录
- `06-工具/data/*` 下的自动化状态、队列、日志、心跳、运行时副本
- `06-工具/data/profiles/`（特殊项：属于 runtime state，但不能在整理仓库时顺手删除）
- 本地缓存、`__pycache__`
- `node_modules/`

## 分桶原则
- 先判定所有权，再决定忽略/归档/清理策略
- 不因目录名包含“日报/生成/报告”就直接判定为 runtime artifact
- 有人工复用价值、经营价值、方法论价值的内容，默认归入 `content-assets`
