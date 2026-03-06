# desktop 兼容层残余依赖盘点

## 当前归属总览
| 领域 | 当前主定义源 | 当前兼容层 | 备注 |
|------|--------------|------------|------|
| 公众号 | repo-local `skills/自有矩阵/skill-manifest.json` | desktop 仅保留历史兼容，不再扩展 alias/定义 | 已完成主链脱钩 |
| 小红书 | desktop `skills.json` + adapter 边界 | repo-local 文档仅做技能说明，不是注册真相源 | 本轮只做 freeze now |
| 抓取台 | desktop 兼容桥接 | `crawl_bridge.py` | 仍属 remove later 范围 |

## 仍然存在的耦合
- `06-工具/scripts/feishu_skill_runner.py`
  - 仍读取 `06-工具/desktop-app/data/skills.json`
  - 公众号 repo-local skill 已不再从 desktop metadata 扩展 alias/定义
  - 当前仅保留 desktop-only skill 的兼容 fallback（如 XHS、抓取台）
- `06-工具/scripts/crawl_bridge.py`
  - 当前仍是 desktop-app 的抓取兼容桥接层
- `06-工具/desktop-app/main.js`
  - 仍直接读取 `data/prompts.default.json` 与 `data/skills.json`
- `skills/自有矩阵/小红书内容生产.md`
  - 仍有与 desktop-app 对齐的文档描述，属于 XHS 兼容说明，不是公众号真相源

## freeze now
- 保留 desktop 目录、启动器、旧 prompt/skill 配置
- 保留 XHS/抓取兼容引用
- 不再向 desktop 侧新增公众号主链逻辑

## remove later
- 等 XHS 与抓取桥接脱钩后，再把 desktop 从“兼容层”降到“历史归档/下线对象”
