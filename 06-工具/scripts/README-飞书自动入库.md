# 飞书自动入库与选题生产链路（当前实现）

## 1. 入口脚本

- 消息编排入口：`06-工具/scripts/feishu_kb_orchestrator.py`
- 批量技能执行：`06-工具/scripts/codex_commander.py`
- 技能执行与落盘：`06-工具/scripts/feishu_skill_runner.py`
- 选题主链流水线：`06-工具/scripts/topic_pipeline.py`

## 2. 三类运行模式

### 2.1 消息编排（默认）
```powershell
python .\06-工具\scripts\feishu_kb_orchestrator.py --text "/skill wechat 平台=公众号 需求=写一篇复盘文"
```

### 2.2 选题流水线单次执行（验收/手动）
```powershell
python .\06-工具\scripts\feishu_kb_orchestrator.py --pipeline-mode once --pipeline-dry-run
python .\06-工具\scripts\feishu_kb_orchestrator.py --pipeline-mode once --pipeline-force-batch
```

### 2.3 选题流水线守护执行（轮询）
```powershell
python .\06-工具\scripts\feishu_kb_orchestrator.py --pipeline-mode daemon --pipeline-poll-sec 60
```

## 3. 本地启动/状态脚本

- 启动：`06-工具/deploy/start-local-hub.ps1`
- 状态：`06-工具/deploy/status-local-hub.ps1`

示例：
```powershell
.\06-工具\deploy\start-local-hub.ps1 -StartPipeline
.\06-工具\deploy\status-local-hub.ps1
```

## 4. 主链规则

1. `01-待深化 -> 02-待生产`  
- 仅处理 frontmatter `status: 待深化` 的文件。  
- 首轮按 `01-选题管理/02-待生产/00-首轮白名单.txt`。  
- 首轮完成后自动切增量。  

2. `02-待生产 -> 各平台生成内容`  
- 每日北京时间 10:00 触发一次。  
- 每批最多处理 3 个待生产选题。  
- 平台严格按选题文件 `platforms` 字段分发。  

3. 平台映射  
- `公众号 -> wechat / 公众号`  
- `小红书 -> xhs / 小红书`  
- `抖音 -> 短视频脚本生产 / 抖音`  
- `视频号 -> 短视频脚本生产 / 视频号`  

## 5. 知识库注入

- 由 `skill_context_resolver.py` 统一生成 `context_files`，按以下顺序注入：  
  1) 待生产文件本体  
  2) related 引用文件  
  3) 选题规划文档（固定两份）  
  4) 平台技能/资源文档  
  5) 抓取台 latest context（存在时）  

- `feishu_skill_runner.py` 会在结果中返回：  
  - `context_files_used`  
  - `context_warnings`

## 6. 关键日志与状态文件

- 运行日志：`06-工具/data/feishu-orchestrator/topic-pipeline/runs/*.jsonl`
- 流水线状态：`06-工具/data/feishu-orchestrator/topic-pipeline/state.json`
- 心跳文件：`06-工具/data/feishu-orchestrator/topic-pipeline/heartbeat.json`

## 7. 故障恢复建议

1. 先看状态：
```powershell
.\06-工具\deploy\status-local-hub.ps1
```

2. 若守护进程未运行，重启：
```powershell
.\06-工具\deploy\start-local-hub.ps1 -StartPipeline
```

3. 若某选题反复失败：  
- 检查待生产文件 `platforms` 与 `related` 路径是否有效。  
- 查看 `state.json` 的 `retry_counts`，超过重试上限需人工处理后重置。  
- 用 `--pipeline-mode once --pipeline-dry-run --pipeline-force-batch` 先做预演。  
