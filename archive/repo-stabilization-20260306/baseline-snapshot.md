# 基线快照（2026-03-06）

## Git 基线
- 分支：`hotfix/verify-main2-linkfirst-20260303`
- 基线提交：`10cee64`
- 当前形态：在可运行工作树上继续收敛，不回退、不重置

## 回归基线
- 命令：`python -m pytest 06-工具/scripts/tests -q`
- 当前结果：`34 passed`
- 最近一次本地回归：`36 passed`
- 说明：当前测试覆盖公众号 skill/图片/排版发布、skill manifest、XHS adapter security 等核心脚本链路

## 主入口清单
- 编排器：`06-工具/scripts/feishu_kb_orchestrator.py`
- 选题流水线：`06-工具/scripts/topic_pipeline.py`
- skill 执行器：`06-工具/scripts/feishu_skill_runner.py`
- 发布动作：`06-工具/scripts/publish_action_runner.py`
- ingest writer API：`06-工具/scripts/ingest_writer_api.py`

## 运维脚本入口
- `06-工具/scripts/run-benchmark-analysis.ps1`
- `06-工具/scripts/run-feishu-backfill.ps1`
- `06-工具/scripts/run-feishu-ingest.ps1`
- `06-工具/scripts/run-feishu-tunnel.ps1`
- `06-工具/scripts/run-ingest-writer-api.ps1`

## 当前 tracked 改动热点
- 仓库策略：`.gitattributes`、`.gitignore`、`README.md`
- 公众号规范：`02-内容生产/公众号/prompts/P4.md`、`02-内容生产/公众号/prompts/视觉风格库.md`
- 数据与方法论：`04-数据与方法论/内容数据统计/*.md`
- desktop 兼容层：`06-工具/desktop-app/README.md`、`06-工具/desktop-app/data/prompts.default.json`
- 公众号主链脚本：
  - `feishu_kb_orchestrator.py`
  - `feishu_skill_runner.py`
  - `skill_context_resolver.py`
  - `topic_brief_builder.py`
  - `topic_pipeline.py`
  - `wechat_image_generator.py`
  - `publish_action_runner.py`
  - `ingest_writer_api.py`
- 公众号主 skill：`skills/自有矩阵/公众号批量生产.md`

## 当前 untracked 新增热点
- repo-local skill 注册与新增公众号 skill 文档：
  - `skills/自有矩阵/skill-manifest.json`
  - `公众号图片生成.md`
  - `公众号对标文案分析.md`
  - `公众号排版与发布.md`
  - `公众号选题深化.md`
  - `公众号配图提示词标准化.md`
- 新脚本与发布组件：
  - `06-工具/scripts/skill_manifest.py`
  - `06-工具/scripts/wechat_prompt_normalizer.py`
  - `06-工具/scripts/wechat_publish_renderer.py`
  - `06-工具/scripts/publish_wechat_playwright.py`
  - `06-工具/scripts/evolink_image_generator.py`
- WeChat layout compiler 子项目：
  - `06-工具/scripts/wechat_layout_compiler/package.json`
  - `06-工具/scripts/wechat_layout_compiler/package-lock.json`
  - `06-工具/scripts/wechat_layout_compiler/render.js`
  - `06-工具/scripts/wechat_layout_compiler/README.md`
- 测试：
  - `06-工具/scripts/tests/test_*`
- XHS adapter 相关：
  - `06-工具/scripts/adapters/xhs/*`
  - `06-工具/scripts/config/xhs_adapter.*.json`
  - `06-工具/scripts/xhs_adapter_cli.py`
  - `06-工具/scripts/README-xhs-adapter.md`
- 内容资产与日报：
  - `01-选题管理/次日创作输入/2026-03-06-建议Brief.md`
  - `03-素材库/对标链接库/分析报告/2026-03-05/00-自动分析索引.md`
  - `04-数据与方法论/方法论沉淀/日报/2026-03-06.md`

## 当前约束
- `desktop-app` 仍在仓库内，且 XHS/抓取桥接仍有残余耦合
- `skill-manifest.py` 存在进程内缓存，改 manifest 后长驻进程需重启
- 行尾策略已设定，但已修改文件暂不做一次性 renormalize，避免大面积噪音 diff
- `06-工具/data/profiles/` 仍是运行态目录，但承载登录态，不能按普通缓存目录一并清空
