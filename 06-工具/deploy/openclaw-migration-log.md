# OpenClaw 中枢迁移日志

> 目标：把“飞书 webhook 直写本机”迁移为“OpenClaw 长连接中枢 + Writer API 入库”，并保留 5 分钟回滚能力。

## Phase 0 基线冻结（2026-02-21）

### 0.1 仓库迁移态快照
- 执行时间：2026-02-21
- 工作区变更总数：`44`
- 状态分布：`14` 个修改（`M`）+ `30` 个新增（`??`）
- 主要变更目录（按顶层）：
  - `06-工具`：23
  - `03-素材库`：8
  - `.claude`：4
  - `01-选题管理`：2

### 0.2 旧链路状态（回滚目标）
- 旧入口：`06-工具/scripts/feishu_ingest_server.py`
- 旧启动脚本：
  - `06-工具/scripts/run-feishu-ingest.ps1`
  - `06-工具/scripts/run-feishu-tunnel.ps1`
- 当前导入记录文件：
  - `03-素材库/金句库/导入记录/2026-02-20-feishu-import.md`（最后写入：2026-02-20 20:54:46）
  - `03-素材库/对标链接库/2026-02-20-feishu-links.md`（最后写入：2026-02-20 20:51:11）
- 旧幂等状态库现状：
  - `06-工具/data/feishu-ingest/state.db` 当前不存在（仅保留 README）

### 0.3 OpenClaw 状态快照（来自 `openclaw status`）
- 安装版本：`OpenClaw 2026.2.15`
- Gateway：`unreachable (ECONNREFUSED 127.0.0.1:18789)`
- Gateway service：`systemd not installed`
- Feishu channel：`enabled/configured`
- Memory：`unavailable`（缺 provider key）
- 安全审计：`2 critical`
  - `channels.feishu.groupPolicy=open`
  - Feishu group 风险（开放群触发）

## 回滚开关（必须保留）

### 回滚条件
- 切主后连续 10 分钟失败率超阈值（建议 > 5%）；
- 或关键链路不可用超过 3 分钟。

### 回滚动作（5 分钟内）
1. 暂停 OpenClaw -> Writer 写入。
2. 启动旧链路：
   - `.\06-工具\scripts\run-feishu-ingest.ps1`
   - `.\06-工具\scripts\run-feishu-tunnel.ps1`
3. 飞书回调地址切回旧入口：`/api/feishu/events`（如果仍使用 webhook）。
4. 在本日志记录故障样本（`event_ref`、错误码、堆栈摘要、时间窗）。

## 迁移阶段记录

| 时间 | 阶段 | 变更摘要 | 负责人 | 结果 |
|---|---|---|---|---|
| 2026-02-21 | Phase 0 | 基线冻结与回滚点建立 | Codex | 完成 |
| 2026-02-21 | Phase 1-4 | 新增 Writer API、OpenClaw Bridge、systemd 模板、部署文档 | Codex | 完成 |

## 待补（Phase 1+）
- [ ] Server A 安装并启用 OpenClaw gateway service
- [ ] Server B 部署 Writer API 并开机自启
- [ ] 内部鉴权联调（Bearer + Timestamp + Nonce + HMAC）
- [ ] 影子期双跑报表（成功率、延迟、重复写入）
- [ ] 切主与回滚演练记录
