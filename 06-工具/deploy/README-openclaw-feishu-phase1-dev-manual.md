# OpenClaw × Feishu 一期开发文档（实施手册）

更新时间：2026-02-28  
适用阶段：一期（微信 1 号、小红书 A/B/C、抖音 1 号）

## 1. 一期目标与边界
### 作用
- 在现有“内容收集 -> 创作”基础上补齐“媒体生成 -> 审核发布 -> 数据抓取 -> 复盘反哺”闭环。
- 给运维与自动化开发同事提供一份可直接交接执行的实施手册。

### 输入/输出
- 输入：飞书文本指令、飞书附件/URL、仓库内待发布内容、平台账号登录态。
- 输出：发布任务台账、平台发布结果、每日指标文件、复盘日报、次日创作输入。

### 失败与恢复
- 失败统一进入死信日志，业务错误不自动重试，人工按 `task_id` 补偿。
- 回滚保留旧链路说明，仅用于应急，不作为日常主流程。

---

## 2. 当前系统全景与运行拓扑
### 作用
- 明确当前“云端编排 + 本地浏览器执行”的组件边界。

### 输入/输出
- 入口流：
  1. Feishu 消息进入 OpenClaw 路由层。
  2. 路由层统一调用 `06-工具/scripts/feishu_kb_orchestrator.py`。
  3. Orchestrator 根据指令意图调用 Writer API：
     - `06-工具/scripts/ingest_writer_api.py`
  4. Writer API 调用对应 runner：
     - `media_task_runner.py`
     - `publish_action_runner.py`
     - `metrics_runner.py`
     - `retro_runner.py`
     - `automation_maintenance_runner.py`
  5. 发布执行最终由全局 skill 脚本完成（`$CODEX_HOME/skills/.../scripts/*.py`）。

- 调度流：
  - `automation_scheduler.py` 在 `Asia/Shanghai` 每天 `22:30` 执行：
    - metrics -> retro -> maintenance

### 失败与恢复
- 路由/编排错误：`06-工具/data/feishu-orchestrator/dead-letter/YYYY-MM-DD.jsonl`
- 自动化执行错误：`06-工具/data/automation/dead-letter/YYYY-MM-DD.jsonl`
- 云端服务可按 systemd 重启，见第 12 章。

---

## 3. 代码结构与模块职责
### 作用
- 建立“改哪里、查哪里、排哪里”的最短路径。

### 输入/输出
- 核心目录：
  - `06-工具/scripts/feishu_kb_orchestrator.py`：飞书意图识别、审批白名单、内部路由。
  - `06-工具/scripts/ingest_writer_api.py`：内部 API、鉴权、幂等、runner 统一入口。
  - `06-工具/scripts/feishu_skill_runner.py`：技能发现与执行（仓库 + `$CODEX_HOME/skills`）。
  - `06-工具/scripts/publish_action_runner.py`：发布两阶段适配层。
  - `06-工具/scripts/media_task_runner.py`：媒体下载、上传、VideoFly 生成与轮询。
  - `06-工具/scripts/metrics_runner.py`：抓数、落库、飞书多维表回写。
  - `06-工具/scripts/retro_runner.py`：评分、复盘日报、次日 brief。
  - `06-工具/scripts/automation_state.py`：`tasks/task_logs` 状态库。
  - `06-工具/scripts/automation_scheduler.py`：22:30 调度。
  - `06-工具/scripts/automation_maintenance_runner.py`：补偿重试与清理。
  - `06-工具/scripts/feishu_http_client.py`：飞书 HTTP API（tenant_access_token）。

### 失败与恢复
- 路径错误优先检查 `REPO_ROOT` 解析与脚本工作目录。
- 发布脚本找不到优先检查 `$CODEX_HOME/skills` 结构是否完整。

---

## 4. 飞书指令协议与编排路由规则
### 作用
- 定义飞书到自动化执行的唯一指令入口。

### 输入/输出
- 指令协议：
  - `/media generate 平台=<wechat|xhs|douyin> 模型=<...> 模式=<text|image|reference-video> 文案=<...>`
  - `/publish prepare 平台=<wechat|xhs|douyin> 账号=<...> 内容=<path或任务ID> 模式=<draft|publish|schedule>`
  - `@审批人 确认发布 任务=<task_id>`
  - `/metrics run 日期=<YYYY-MM-DD|today>`
  - `/retro run 日期=<YYYY-MM-DD|today>`

- 路由优先级（当前实现）：
  1. 自动化命令（`/media`、`/publish`、`/metrics`、`/retro`、确认发布）
  2. 传统入库/skill 路由（URL 入库、金句入库、`/skill`、弱触发 skill）
  3. 普通聊天 fallback

- 审批用户识别来源：
  - `--source-user`
  - `meta.source_user`
  - `source_ref` 中的 `open_id/user_id`

### 失败与恢复
- 自动化命令参数缺失：直接错误回复并写死信。
- 审批人不在白名单：拒绝执行并写审计/死信。

---

## 5. 内部 API 合同（请求/响应/幂等/鉴权）
### 作用
- 统一 OpenClaw 到 Writer API 的接口边界。

### 输入/输出
- 鉴权头要求：
  - `Authorization: Bearer <INGEST_SHARED_TOKEN>`
  - `X-Ingest-Timestamp`
  - `X-Ingest-Nonce`
  - `X-Ingest-Signature`

- 签名算法：
  - `HMAC_SHA256(secret, "{timestamp}\n{nonce}\n" + body_bytes)`

- 内部 API：
  - `POST /internal/media/generate`
  - `POST /internal/publish/prepare`
  - `POST /internal/publish/approve`
  - `POST /internal/metrics/run`
  - `POST /internal/retro/run`
  - `POST /internal/maintenance/run`（兼容运维入口）
  - `GET /internal/tasks/{task_id}`

- 响应约定：
  - `code=0`：`status in {success, partial, duplicate}`
  - `code=1`：`status=error`

- 幂等：
  - 任务侧以 `event_ref + task_type` 识别重复任务（`find_task_by_event_ref`）。
  - 同一 `event_ref` 重放不应重复创建同类任务。

### 失败与恢复
- 时间戳偏移或签名错误：403。
- 业务异常：500 或 `code=1`，由调用方写死信并人工补偿。

---

## 6. 媒体生成链路（附件/URL/VideoFly/reference-video）
### 作用
- 将飞书素材转换为可发布媒体资产。

### 输入/输出
- 入口脚本：`media_task_runner.py`
- 素材来源：
  - `attachments`（url/download_url/file_url）
  - `images/videos` 本地路径或 URL
  - `image_urls/video_urls/urls`
- 本地落地目录：
  - `06-工具/data/automation/media/inbox/{task_id}/`

- VideoFly 调用：
  - 上传：`POST /api/v1/upload`
  - 生成：`POST /api/v1/video/generate`
  - 轮询：`GET /api/v1/video/task/{taskId}/status` 或 `GET /api/v1/video/{videoUuid}/status`

- 模式：
  - `text`：文本生视频
  - `image`：图生视频（需至少一张图）
  - `reference-video`：参考视频生视频（需至少一个视频）

- VideoFly 一期改造事实（`E:\视频saas网站`）：
  - `upload` 支持 `video/mp4`, `video/quicktime`
  - `UPLOAD_MAX_VIDEO_MB` 控制视频上传上限
  - `video/generate` schema 支持 `videoUrls: string[]`
  - provider 映射支持 `reference-to-video -> video_urls`

### 失败与恢复
- 网络错误：指数退避重试（默认 3 次）。
- 业务错误（如缺少素材、轮询失败）：不自动重试，写死信并返回 `task_id`。

---

## 7. 发布链路（prepare/approve/retry）与平台差异
### 作用
- 统一微信/小红书/抖音发布行为并抽象为两阶段。

### 输入/输出
- 入口脚本：`publish_action_runner.py`
- 动作：
  - `prepare`：预填与预检，落库为 `pending_approval`
  - `approve`：审批通过后二阶段执行
  - `retry`：将失败任务转 `retry_pending` 后再次执行 approve

- 平台适配：
  - 微信：
    - prepare 使用 `draft`
    - 二阶段按 `publish/schedule`
    - skill 脚本：`wechat-publish-playwright/scripts/publish_wechat.py`
  - 小红书：
    - prepare 用 `publish/schedule` dry-run 预检
    - skill 脚本：`xhs-publish-playwright/scripts/publish_xhs.py`
  - 抖音：
    - prepare / approve 均以人工最终点击为目标
    - 状态会进入 `waiting_manual_publish`
    - skill 脚本：`douyin-publish-playwright/scripts/publish_douyin.py`

- 准入与幂等：
  - `event_ref` 幂等防重
  - 内容可传文件路径或已有 `task_id`

### 失败与恢复
- `prepare` 失败：直接 `error` + dead-letter。
- `approve` 失败：`error` + dead-letter，可 `retry` 补偿。
- 抖音人工窗口未完成：保留 `waiting_manual_publish` 并记录截图路径。

---

## 8. 审批白名单与审计
### 作用
- 控制发布审批权限，避免误发。

### 输入/输出
- 白名单环境变量：
  - `FEISHU_APPROVAL_OPEN_IDS`（逗号分隔）
- 保护范围：
  - 仅 `publish_approve` 路径（包括 `retry` 映射到 approve）
- 拒绝响应：
  - `approver not allowed: <open_id>`

- 审计位置：
  - 编排运行日志：`06-工具/data/feishu-orchestrator/runs/*.jsonl`
  - 编排死信：`06-工具/data/feishu-orchestrator/dead-letter/*.jsonl`
  - 任务日志：`task_logs`

### 失败与恢复
- `source_user` 缺失：拒绝审批。
- 非白名单：拒绝审批，需补充白名单后重发审批指令。

---

## 9. 数据抓取与统计落库
### 作用
- 每日汇总各平台核心指标并同步本地与飞书。

### 输入/输出
- 入口脚本：`metrics_runner.py`
- 优先级策略：
  1. 官方 API（`{WECHAT|XHS|DOUYIN}_METRICS_API_URL`）
  2. UI fallback（从发布任务结果读取）

- 本地产物：
  - `06-工具/data/automation/metrics/{date}/metrics.json`
  - `06-工具/data/automation/metrics/{date}/metrics.csv`
  - 追加到：
    - `04-数据与方法论/内容数据统计/公众号数据.md`
    - `04-数据与方法论/内容数据统计/小红书数据.md`
    - `04-数据与方法论/内容数据统计/抖音数据.md`

- 飞书双写：
  - 多维表字段：日期/平台/账号/任务ID/状态/阅读/点赞/评论/分享/收藏/链接/来源

### 失败与恢复
- 官方 API 不可用自动降级 UI fallback。
- 完全抓取失败写 dead-letter，次日可按日期重跑。

---

## 10. 自动复盘与次日创作输入回灌
### 作用
- 将当日指标自动转换为可执行优化动作，并回灌到次日创作上下文。

### 输入/输出
- 入口脚本：`retro_runner.py`
- 输入：
  - `metrics.json`
- 评分逻辑：
  - `score = (likes + 2*comments + 3*shares + 2*collects) / max(1, read_or_play)`
  - 等级：S/A/B/C（阈值 0.15/0.08/0.03）

- 输出：
  - 日报：`04-数据与方法论/方法论沉淀/日报/{date}.md`
  - 次日 brief：`01-选题管理/次日创作输入/{date}-建议Brief.md`
  - 飞书文档摘要：`FEISHU_RETRO_DOC_ID`

- 编排回灌：
  - `feishu_kb_orchestrator.py` 会自动注入最新 `次日创作输入/*.md` 到 skill `context_files`

### 失败与恢复
- 当日 metrics 不存在：retro 返回 error 并写 dead-letter。
- 飞书文档写入失败不影响本地日报生成。

---

## 11. 调度、日志、死信、补偿与重试
### 作用
- 提供无人值守运行与故障补偿机制。

### 输入/输出
- 调度器：`automation_scheduler.py`
  - 时区：`Asia/Shanghai`
  - 触发：`hour == 22 && minute >= 30 && 当日未执行`
  - 顺序：`metrics -> retro -> maintenance`

- 维护器：`automation_maintenance_runner.py`
  - 处理 `retry_pending` 任务
  - 清理旧产物（默认保留 14 天）

- 日志目录：
  - `06-工具/data/automation/runs/`
  - `06-工具/data/automation/dead-letter/`
  - `06-工具/data/automation/scheduler/`
  - `06-工具/data/automation/screenshots/`（保留位）

- 状态模型（SQLite: `automation_state.db`）：
  - `tasks` 关键字段：
    - `task_id`：任务唯一标识（如 `pub-...`、`media-...`）。
    - `event_ref`：来源事件幂等键，配合 `task_type` 防重。
    - `task_type`：任务类型（如 `media_generate`、`publish_prepare`、`metrics_run`、`retro_run`）。
    - `status`：任务状态（`running/pending_approval/success/error/retry_pending/waiting_manual_publish`）。
    - `phase`：当前阶段（如 `prepare`、`approve`）。
    - `platform/account/mode`：平台、账号、执行模式。
    - `source_user/approver`：触发人、审批人。
    - `payload_json/result_json/error_text`：入参、结果、错误文本。
    - `retry_count/next_retry_at`：补偿次数与下次重试时间。
    - `created_at/updated_at/approved_at`：时间戳。
  - `task_logs` 关键字段：
    - `id`：自增主键。
    - `task_id`：关联任务。
    - `event_type`：日志事件（如 `prepare`、`approve`、`retry_marked`）。
    - `payload_json`：事件载荷。
    - `created_at`：记录时间。

### 失败与恢复
- 网络类错误自动重试（媒体链路）。
- 业务类错误不自动重试，进入 dead-letter 后人工补偿：
  - `/publish retry 任务=<task_id>`

---

## 12. 环境变量与部署操作手册
### 作用
- 给上线和排障提供统一配置基线。

### 输入/输出
- 本地守护启动：
  - `06-工具/deploy/start-local-hub.ps1`
  - 支持同时拉起 topic pipeline 与 automation scheduler

- 云端服务：
  - `openclaw-gateway.service`
  - `ingest-writer-api.service`

- 最小健康检查：
  - `curl -fsS http://127.0.0.1:8790/internal/healthz`
  - `systemctl status ingest-writer-api --no-pager -n 20`
  - `systemctl status openclaw-gateway --no-pager -n 20`

- 值班操作最短路径：
  1. 启动：执行 `06-工具/deploy/start-local-hub.ps1`，确认本地 hub 与 scheduler 均拉起。
  2. 健康检查：执行上述 `healthz + systemctl status` 三条命令。
  3. 审批失败定位：按 `task_id` 查询 `GET /internal/tasks/{task_id}`，并检查 `task_logs` 与对应 dead-letter。
  4. 死信重试：修复根因后发送 `/publish retry 任务=<task_id>`，验证状态从 `error/retry_pending` 转为 `success/waiting_manual_publish`。
  5. 回滚：若连续失败超阈值，按第 15 章顺序执行应急回滚。

### 失败与恢复
- 缺少 token/signature 配置会导致 Writer API 拒绝请求。
- 服务重启后优先检查环境变量文件与 systemd 生效状态。

---

## 13. 验收用例（10 条）与检查步骤
### 作用
- 将一期交付转换为可逐条勾检的执行清单。

### 输入/输出
1. 附件图片触发媒体生成  
  - 执行：`/media generate ... 模式=image ...`  
  - 期望：返回 `task_id`，产物 URL 可访问  
  - 证据：automation run log + task result

2. 附件视频触发 reference-to-video  
  - 执行：`/media generate ... 模式=reference-video ...`  
  - 期望：轮询完成 `status=success`  
  - 证据：task result 中 `video_task_id/video_uuid/video_url`

3. 微信发布 prepare -> approve  
  - 执行：`/publish prepare ...` + 审批口令  
  - 期望：`pending_approval -> success`  
  - 证据：`tasks` + skill run_log

4. 小红书发布 prepare -> approve  
  - 期望：返回发布结果 `note_url`（或对应字段）  
  - 证据：approve result

5. 抖音 prepare/approve 停在人工最终点击前  
  - 期望：`waiting_manual_publish`  
  - 证据：截图与 run_log

6. 非白名单审批被拒绝  
  - 期望：返回 `approver not allowed`  
  - 证据：orchestrator dead-letter

7. 22:30 自动抓数  
  - 期望：本地统计文件追加 + 飞书多维表新增记录  
  - 证据：`metrics.json/csv` + bitable 写入结果

8. 自动复盘与次日 brief 生成  
  - 期望：日报与 brief 文件生成  
  - 证据：固定路径文件存在

9. 发布失败进入死信并可补偿  
  - 期望：dead-letter 有记录，`/publish retry` 可重试  
  - 证据：task_logs 中 `retry_marked/approve`

10. 幂等校验  
  - 执行：重复发送同一 `event_ref`  
  - 期望：返回 `duplicate`，不重复创建任务  
  - 证据：tasks 中 event_ref+task_type 仅一条有效任务

### 失败与恢复
- 任一验收失败先取 `task_id`，再按第 11 章定位日志与补偿。

---

## 14. 已知限制与二期演进建议
### 作用
- 明确当前实现边界，避免误解为“全自动”。

### 输入/输出
- 已知限制：
  - 抖音一期不自动最终点击发布。
  - 部分平台指标依赖 UI fallback，字段完整性受限。
  - 调度器当前为单进程守护，HA 未覆盖。
  - 发布执行依赖本地持久化登录态与页面选择器稳定性。

- 二期建议：
  - 引入发布任务 SLA/告警通道（飞书机器人告警）。
  - 增加 selector 配置热更新与回归测试集。
  - 指标抓取补齐更多官方接口字段并统一指标口径。
  - 增加 dead-letter 可视化看板。

### 失败与恢复
- 二期未完成前，值班以人工审批与补偿流程兜底。

---

## 15. 回滚与应急预案
### 作用
- 保证切换/故障时可在 5 分钟内恢复可用链路。

### 输入/输出
- 应急触发条件：
  - 连续 10 分钟失败率超阈值（建议 >5%）
  - 核心链路中断超过 3 分钟

- 回滚动作（建议顺序）：
  1. 暂停 OpenClaw -> Writer 写入
  2. 启动旧链路脚本（如需）：`run-feishu-ingest.ps1`、`run-feishu-tunnel.ps1`
  3. 恢复旧回调地址（若仍使用 webhook 模式）
  4. 记录故障样本：`event_ref`、错误码、堆栈摘要、时间窗

- 云端回滚：
  - `bash 06-工具/deploy/cloud-deploy.sh --repo-path /root/gzh-xhs --rollback <sha>`

### 失败与恢复
- 回滚后仍异常：优先保留日志与状态库快照，避免二次破坏证据。

---

# 附录 A：环境变量矩阵（一期）

| 变量 | 作用 | 默认值/示例 | 所在模块 |
| --- | --- | --- | --- |
| `INGEST_WRITER_BASE_URL` | Orchestrator 调 Writer API | `http://127.0.0.1:8790` | orchestrator |
| `INGEST_SHARED_TOKEN` | Writer API Bearer 鉴权 | 无默认，必填 | orchestrator/writer |
| `INGEST_HMAC_SECRET` | Writer API 签名密钥 | 默认回退 shared token | orchestrator/writer |
| `INGEST_TIMEOUT_SEC` | Writer API 调用超时 | `20` | orchestrator |
| `INGEST_VERIFY_SSL` | Writer API TLS 校验 | `true` | orchestrator |
| `INGEST_SIGNATURE_REQUIRED` | Writer 是否强制验签 | `true` | writer |
| `INGEST_AUTH_MAX_SKEW_SECONDS` | 时间戳最大偏移秒数 | `600` | writer |
| `INGEST_ALLOWED_SOURCE_KINDS` | source_kind 白名单 | 空 | writer |
| `FEISHU_APPROVAL_OPEN_IDS` | 审批白名单 open_id 列表 | 空 | orchestrator |
| `FEISHU_SKILL_MODEL` | skill 执行模型 | `gpt-5.3-codex` | orchestrator |
| `FEISHU_COMMANDER_WORKERS` | skill 并发 worker | `2` | orchestrator |
| `FEISHU_COMMANDER_TIMEOUT_SEC` | 单批 skill 超时 | `1800` | orchestrator |
| `FEISHU_COMMANDER_MAX_RETRIES` | skill 批重试次数 | `1` | orchestrator |
| `FEISHU_REPLY_MAX_CHARS` | 单条回复最大字符 | `1500` | orchestrator |
| `FEISHU_SKILL_INCLUDE_NEXTDAY_BRIEF` | 自动注入次日 brief | `true` | orchestrator |
| `ORCHESTRATOR_DRYRUN_SKIP_WRITER` | dry-run 是否跳过 writer | `true` | orchestrator |
| `VIDEOFLY_BASE_URL` | VideoFly 地址 | `http://127.0.0.1:3000` | media runner |
| `VIDEOFLY_BEARER_TOKEN` | VideoFly 认证 Token | 空 | media runner |
| `VIDEOFLY_COOKIE` | VideoFly Cookie | 空 | media runner |
| `VIDEOFLY_TIMEOUT_SEC` | VideoFly 请求超时 | `45` | media runner |
| `VIDEOFLY_POLL_INTERVAL_SEC` | 轮询间隔秒 | `8` | media runner |
| `VIDEOFLY_POLL_TIMEOUT_SEC` | 轮询超时秒 | `900` | media runner |
| `VIDEOFLY_DEFAULT_MODEL` | 默认视频模型 | `wan2.6` | media runner |
| `MEDIA_DOWNLOAD_MAX_MB` | 素材下载大小上限 MB | `500` | media runner |
| `AUTOMATION_NETWORK_RETRY` | 网络重试次数 | `3` | media/others |
| `AUTOMATION_BACKOFF_BASE_SEC` | 指数退避基数秒 | `1.0` | media |
| `WECHAT_PROFILE_DIR` | 微信登录态目录 | `06-工具/data/automation/profiles/wechat-main` | publish runner |
| `XHS_PROFILE_DIR` | 小红书登录态目录 | `06-工具/data/automation/profiles/xhs-main` | publish runner |
| `DOUYIN_PROFILE_DIR` | 抖音登录态目录 | `06-工具/data/automation/profiles/douyin-main` | publish runner |
| `WECHAT_SELECTORS_PATH` | 微信 selectors 配置 | 空 | publish runner |
| `XHS_SELECTORS_PATH` | 小红书 selectors 配置 | 空 | publish runner |
| `DOUYIN_SELECTORS_PATH` | 抖音 selectors 配置 | 空 | publish runner |
| `PUBLISH_PREPARE_RUN_WECHAT_DRAFT` | 微信 prepare 是否真实落草稿 | `true` | publish runner |
| `PUBLISH_PREPARE_RUN_DOUYIN` | 抖音 prepare 是否真实执行 | `true` | publish runner |
| `WECHAT_METRICS_API_URL` | 微信官方指标 API | 空 | metrics runner |
| `XHS_METRICS_API_URL` | 小红书官方指标 API | 空 | metrics runner |
| `DOUYIN_METRICS_API_URL` | 抖音官方指标 API | 空 | metrics runner |
| `WECHAT_METRICS_API_BEARER` | 微信指标 API token | 空 | metrics runner |
| `XHS_METRICS_API_BEARER` | 小红书指标 API token | 空 | metrics runner |
| `DOUYIN_METRICS_API_BEARER` | 抖音指标 API token | 空 | metrics runner |
| `FEISHU_APP_ID` | 飞书应用 ID | 无默认 | feishu_http_client |
| `FEISHU_APP_SECRET` | 飞书应用 Secret | 无默认 | feishu_http_client |
| `FEISHU_OPEN_BASE_URL` | 飞书开放平台地址 | `https://open.feishu.cn` | feishu_http_client |
| `FEISHU_BITABLE_APP_TOKEN` | 指标主表 app token | 空 | metrics -> feishu |
| `FEISHU_BITABLE_METRICS_TABLE_ID` | 指标主表 table_id | 空 | metrics -> feishu |
| `FEISHU_RETRO_DOC_ID` | 复盘文档 ID | 空 | retro -> feishu |
| `AUTOMATION_ARTIFACT_KEEP_DAYS` | 运维清理保留天数 | `14` | maintenance |

---

# 附录 B：指令协议速查表

```text
/media generate 平台=<wechat|xhs|douyin> 模型=<...> 模式=<text|image|reference-video> 文案=<...>
/publish prepare 平台=<wechat|xhs|douyin> 账号=<...> 内容=<path或任务ID> 模式=<draft|publish|schedule>
@审批人 确认发布 任务=<task_id>
/publish retry 任务=<task_id>
/metrics run 日期=<YYYY-MM-DD|today>
/retro run 日期=<YYYY-MM-DD|today>
```

---

# 附录 C：任务状态机与死信补偿速查

## C1. 任务状态
- `running`：执行中
- `pending_approval`：准备完成，待审批
- `waiting_manual_publish`：抖音等待人工最终点击
- `success`：执行成功
- `error`：执行失败
- `retry_pending`：待补偿重试

## C2. 死信位置
- 编排死信：`06-工具/data/feishu-orchestrator/dead-letter/YYYY-MM-DD.jsonl`
- 自动化死信：`06-工具/data/automation/dead-letter/YYYY-MM-DD.jsonl`

## C3. 补偿流程
1. 先查 `task_id` 的 `result_json/error_text/task_logs`
2. 修复根因（配置、登录态、素材、权限）
3. 发送补偿命令：
   - `/publish retry 任务=<task_id>`
4. 再验证状态是否从 `retry_pending/error` 进入 `success/waiting_manual_publish`

---

# 附录 D：10 条验收勾检模板

复制后按实际执行填写：

- [ ] 用例1 附件图片触发媒体生成  
  执行命令：  
  期望结果：  
  证据路径：

- [ ] 用例2 附件视频 reference-to-video  
  执行命令：  
  期望结果：  
  证据路径：

- [ ] 用例3 微信 prepare -> approve  
  执行命令：  
  期望结果：  
  证据路径：

- [ ] 用例4 小红书 prepare -> approve  
  执行命令：  
  期望结果：  
  证据路径：

- [ ] 用例5 抖音人工最终点击窗口  
  执行命令：  
  期望结果：  
  证据路径：

- [ ] 用例6 非白名单审批拒绝  
  执行命令：  
  期望结果：  
  证据路径：

- [ ] 用例7 22:30 自动抓数  
  执行命令：  
  期望结果：  
  证据路径：

- [ ] 用例8 自动复盘与次日 brief  
  执行命令：  
  期望结果：  
  证据路径：

- [ ] 用例9 死信补偿重试  
  执行命令：  
  期望结果：  
  证据路径：

- [ ] 用例10 event_ref 幂等校验  
  执行命令：  
  期望结果：  
  证据路径：

---

# 附录 E：事实来源清单（写作基线）

- 路由与部署：
  - `06-工具/deploy/README-openclaw-feishu-routing.md`
  - `06-工具/deploy/README-openclaw-prod.md`
  - `06-工具/deploy/openclaw-migration-log.md`
- 编排与执行：
  - `06-工具/scripts/feishu_kb_orchestrator.py`
  - `06-工具/scripts/ingest_writer_api.py`
  - `06-工具/scripts/feishu_skill_runner.py`
- 自动化 runner：
  - `06-工具/scripts/media_task_runner.py`
  - `06-工具/scripts/publish_action_runner.py`
  - `06-工具/scripts/metrics_runner.py`
  - `06-工具/scripts/retro_runner.py`
  - `06-工具/scripts/automation_scheduler.py`
  - `06-工具/scripts/automation_maintenance_runner.py`
  - `06-工具/scripts/automation_state.py`
  - `06-工具/scripts/feishu_http_client.py`
- 调度脚本：
  - `06-工具/deploy/start-local-hub.ps1`
- 业务侧输入输出：
  - `01-选题管理/02-待生产/00-首轮白名单.txt`
  - `04-数据与方法论/内容数据统计/公众号数据.md`
  - `04-数据与方法论/内容数据统计/小红书数据.md`
  - `04-数据与方法论/内容数据统计/抖音数据.md`

## Appendix A.1: Bitable target safety guard (2026-03-05)

For Douyin link ingest with Bitable enabled (`INGEST_DOUYIN_BITABLE_ENABLED=true`), these environment variables are required:

| Variable | Required | Example |
| --- | --- | --- |
| `BITABLE_APP_TOKEN` | Yes | `app_token_xxx` |
| `BITABLE_TABLE_ID` | Yes | `tbl_xxx` |
| `BITABLE_VIEW_ID` | Optional | `vew_xxx` |

Deployment behavior:
- `cloud-deploy.sh` will fail fast when Bitable is enabled but `BITABLE_APP_TOKEN` or `BITABLE_TABLE_ID` is missing.
- No hardcoded default Bitable IDs are injected anymore.
- This guard prevents accidental writes into a wrong Bitable table.
