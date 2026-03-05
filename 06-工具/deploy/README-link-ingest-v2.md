# 云端入库链路 V2 运行手册

## 1. 部署
```bash
cd /root/gzh-xhs
git fetch origin main
git checkout main
git reset --hard origin/main
bash -x 06-工具/deploy/cloud-deploy.sh --repo-path /root/gzh-xhs --branch main --remote origin
```

说明：
- `cloud-deploy.sh` 会写入：
  - `INGEST_LINK_MIN_CONTENT_CHARS=120`
  - `INGEST_LINK_ALLOW_TEST_SKIP=true`
- 若 `INSTALL_EXTRACTORS=true`（默认），会调用 `install-link-extractors.sh` 安装锁定依赖：
  - `yt-dlp`
  - `f2`（可选，失败不阻断）

## 2. 真实链路验收
### 2.1 金句验收（非链接）
```bash
bash 06-工具/deploy/verify-real-feishu-chain.sh \
  --repo-path /root/gzh-xhs \
  --since-minutes 20 \
  --expect-ingest true \
  --require-git-sync true
```

### 2.2 链接验收（要求正文达标）
```bash
bash 06-工具/deploy/verify-real-feishu-chain.sh \
  --repo-path /root/gzh-xhs \
  --since-minutes 20 \
  --expect-ingest true \
  --require-git-sync true \
  --require-content-success true \
  --allow-test-url-skip true \
  --min-content-chars 120
```

## 3. 回补“路由成功但正文失败”事件
### 3.1 先预览候选（dry-run）
```bash
bash 06-工具/deploy/replay-link-content-failures.sh \
  --repo-path /root/gzh-xhs \
  --since-minutes 1440 \
  --max-events 200 \
  --dry-run
```

### 3.2 执行回补
```bash
bash 06-工具/deploy/replay-link-content-failures.sh \
  --repo-path /root/gzh-xhs \
  --since-minutes 1440 \
  --max-events 200
```

可选参数：
- `--date YYYY-MM-DD`：指定 run log 日期。
- `--event-ref-contains xxx`：按关键字过滤事件。
- `--writer-base-url`、`--token`、`--secret`：覆盖默认环境变量。

## 4. 关键判定规则（V2）
- 路由成功：`link_route_status in {success, partial}`。
- 正文状态：
  - `success`：正文字符数达到阈值。
  - `failed`：提取失败或正文不足阈值。
  - `skipped_test`：测试链接（如 `example.com`）被跳过质量门禁。
- 验收脚本启用 `--require-content-success true` 时，仅对链接流进行正文门禁，不会误伤纯金句事件。

## 5. Bitable Safety Requirements (2026-03-05)

When `INGEST_DOUYIN_BITABLE_ENABLED=true`, deployment requires explicit Bitable target values:

```bash
BITABLE_APP_TOKEN=<your_bitable_app_token>
BITABLE_TABLE_ID=<your_bitable_table_id>
# optional
BITABLE_VIEW_ID=<your_bitable_view_id>
```

Notes:
- `cloud-deploy.sh` no longer injects hardcoded Bitable defaults.
- If required Bitable values are missing, deployment fails fast to prevent writing to a wrong table.
- To intentionally disable Bitable path, set `INGEST_DOUYIN_BITABLE_ENABLED=false`.
