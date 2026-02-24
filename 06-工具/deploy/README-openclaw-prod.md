# OpenClaw 云端生产流程（Git 主流程）

## 总原则
1. 代码唯一来源：GitHub `main`
2. 云端运行目录：`/root/gzh-xhs/06-工具/scripts`
3. 自动发布：GitHub Actions 触发 `cloud-deploy.sh`
4. 自动回写：写库成功后 `git_sync_after_write.py` 提交并推送到 `main`

## 云端服务
- `openclaw-gateway.service`
- `ingest-writer-api.service`

## 必要环境变量（/etc/openclaw/feishu.env）
- `INGEST_WRITER_BASE_URL`
- `INGEST_SHARED_TOKEN`
- `INGEST_HMAC_SECRET`
- `FEISHU_SKILL_MODEL=gpt-5.3-codex`
- `FEISHU_COMMANDER_WORKERS=2`
- `FEISHU_COMMANDER_MAX_RETRIES=1`
- `FEISHU_REPLY_MAX_CHARS=1500`
- `GIT_SYNC_ENABLED=true`
- `GIT_SYNC_REPO_ROOT=/root/gzh-xhs`
- `GIT_SYNC_REMOTE=origin`
- `GIT_SYNC_BRANCH=main`
- `GIT_SYNC_INCLUDE_PATHS=02-内容生产,03-素材库,01-选题管理`
- `GIT_SYNC_AUTHOR_NAME=feishu-bot`
- `GIT_SYNC_AUTHOR_EMAIL=feishu-bot@local`
- `GIT_SYNC_MAX_RETRIES=2`

## GitHub Actions Secrets
- `CLOUD_HOST`
- `CLOUD_PORT`
- `CLOUD_USER`
- `CLOUD_SSH_KEY`
- `CLOUD_DEPLOY_PATH`（默认 `/root/gzh-xhs`）

## 发布脚本
- `06-工具/deploy/cloud-deploy.sh`
- `06-工具/deploy/cloud-smoke-test.sh`

## 手动发布（云端）
```bash
bash 06-工具/deploy/cloud-deploy.sh --repo-path /root/gzh-xhs --branch main --remote origin
```

## 手动回滚（云端）
```bash
bash 06-工具/deploy/cloud-deploy.sh --repo-path /root/gzh-xhs --rollback <sha>
```

## 验证
```bash
systemctl status ingest-writer-api --no-pager -n 20
systemctl status openclaw-gateway --no-pager -n 20
curl -fsS http://127.0.0.1:8790/internal/healthz
```
