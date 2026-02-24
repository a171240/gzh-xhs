# OpenClaw × Feishu 路由部署说明

## 目标
- Feishu 消息统一走 `06-工具/scripts/feishu_kb_orchestrator.py`
- 外层路由只负责转发 `reply/reply_segments`
- 业务逻辑（入库/技能/普通聊天）全部在 orchestrator 内部

## 快速切主
1. 将路由提示词写到云端：
- `/root/.openclaw/workspace/FEISHU_ROUTING_PROMPT.md`
- `/root/.openclaw/workspace/AGENTS.md`

2. 重启服务：
- `systemctl restart ingest-writer-api`
- `systemctl restart openclaw-gateway`

3. 验证：
- `journalctl -u openclaw-gateway -f --no-pager | grep -Ei 'received message|dispatching to agent|dispatch complete|failed before reply|error'`

## Feishu 触发规则
- `@用户名：正文`：金句入库
- `金句：正文`：金句入库
- 含 URL：链接入库
- `用{skill名}生成...` 或 `/skill ...`：skill 生成
- 其他文本：普通聊天回复

## 关键日志
- 编排器日志：`06-工具/data/feishu-orchestrator/runs/YYYY-MM-DD.jsonl`
- 编排器死信：`06-工具/data/feishu-orchestrator/dead-letter/YYYY-MM-DD.jsonl`
- 指挥器日志：`06-工具/data/codex-commander/runs/YYYY-MM-DD.jsonl`

## 回滚
- 恢复上一个 Git 提交并重启服务：
  - `bash 06-工具/deploy/cloud-deploy.sh --repo-path /root/gzh-xhs --rollback <sha>`
