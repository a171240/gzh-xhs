# Git 整理与分批提交清单

## 目的
当前工作树改动较多，且同时包含：
- repo/source 级代码与配置
- desktop 兼容层
- XHS 适配器
- 内容资产与方法论文档

为避免一次性 `git add .` 把仓库搞乱，本清单把改动拆成可独立验收的批次。

## 使用方式
推荐按批次执行：

```powershell
git add --pathspec-from-file archive/repo-stabilization-20260306/staging-plan/01-repo-governance.pathspec
git add --pathspec-from-file archive/repo-stabilization-20260306/staging-plan/02-wechat-main-chain.pathspec
git add --pathspec-from-file archive/repo-stabilization-20260306/staging-plan/03-desktop-and-xhs-compat.pathspec
```

不要直接执行：

```powershell
git add .
```

## 批次定义

### Batch 01：仓库治理与稳定化文档
范围：
- `.gitattributes`
- `.gitignore`
- 根 `README.md`
- `archive/repo-stabilization-20260306/*`
- 维护协议与主链说明文档

特点：
- 不改变运行逻辑
- 只收口真相源、兼容层、重启协议、artifact 边界

### Batch 02：公众号主链代码与测试
范围：
- repo-local skill manifest
- 公众号 skill 文档
- 公众号 runner/orchestrator/context/pipeline
- prompt normalizer / image generator / publish renderer / publish action
- wechat layout compiler
- 公众号测试与 wechat selectors

特点：
- 是当前真正的功能主包
- 可以独立做主链回归：`python -m pytest 06-工具/scripts/tests -q`

### Batch 03：desktop 兼容层与 XHS 适配器
范围：
- `06-工具/desktop-app/*` 中本次涉及的兼容层文件
- `crawl_bridge.py`
- XHS adapter / CLI / lock / 说明文档
- 小红书 skill 文档

特点：
- 明确是兼容层，不应和公众号主链真相源混提
- 这批主要解决“旧入口仍保留，但不再冒充主定义源”

## 暂缓单独复核
以下内容不建议和上面三批混提：
- `01-选题管理/*` 下新增/修改的人工内容
- `03-素材库/*` 下新增的分析索引
- `04-数据与方法论/*` 下新增/修改的数据与日报
- `TASK-公众号全流程打通-Phase1.md`
- `06-工具/deploy/install-feishu-official-plugin.sh`

这些内容要么属于长期内容资产，要么属于独立项目文档/杂项工具，适合单独 review、单独 commit。

当前建议单独复核的内容资产只有以下 6 个对象：
- `01-选题管理/次日创作输入/2026-03-06-建议Brief.md`
- `03-素材库/对标链接库/分析报告/2026-03-05/00-自动分析索引.md`
- `04-数据与方法论/内容数据统计/公众号数据.md`
- `04-数据与方法论/内容数据统计/小红书数据.md`
- `04-数据与方法论/内容数据统计/抖音数据.md`
- `04-数据与方法论/方法论沉淀/日报/2026-03-06.md`

不建议把 `01-选题管理/` 或 `04-数据与方法论/` 整目录直接放进本轮代码提交。

## 提交前检查
- [ ] 不包含 `reports/`、`06-工具/data/`、缓存目录
- [ ] 不包含 `01-05` 下的非目标内容资产
- [ ] `python -m pytest 06-工具/scripts/tests -q` 通过
- [ ] 没有误把 desktop 兼容层和 repo-local 主链混成一个“超大提交”
