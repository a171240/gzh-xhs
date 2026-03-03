#!/usr/bin/env bash
set -euo pipefail

REPO_PATH="${CLOUD_DEPLOY_PATH:-/root/gzh-xhs}"
REMOTE="${CLOUD_DEPLOY_REMOTE:-origin}"
BRANCH="${CLOUD_DEPLOY_BRANCH:-main}"
GATEWAY_SERVICE="${OPENCLAW_GATEWAY_SERVICE:-openclaw-gateway}"
WRITER_SERVICE="${INGEST_WRITER_SERVICE:-ingest-writer-api}"
ASYNC_WORKER_SERVICE="${BITABLE_LINK_WORKER_SERVICE:-bitable-link-worker}"
OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-/root/.openclaw/openclaw.json}"
ROLLBACK_ON_FAIL="${ROLLBACK_ON_FAIL:-true}"
INSTALL_EXTRACTORS="${INSTALL_EXTRACTORS:-true}"
ROLLBACK_SHA=""
WORKSPACE_DIR="/root/.openclaw/workspace"
WORKSPACE_DIRS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-path) REPO_PATH="${2:?missing value for --repo-path}"; shift 2 ;;
    --remote) REMOTE="${2:?missing value for --remote}"; shift 2 ;;
    --branch) BRANCH="${2:?missing value for --branch}"; shift 2 ;;
    --gateway-service) GATEWAY_SERVICE="${2:?missing value for --gateway-service}"; shift 2 ;;
    --writer-service) WRITER_SERVICE="${2:?missing value for --writer-service}"; shift 2 ;;
    --async-worker-service) ASYNC_WORKER_SERVICE="${2:?missing value for --async-worker-service}"; shift 2 ;;
    --install-extractors) INSTALL_EXTRACTORS="${2:?missing value for --install-extractors}"; shift 2 ;;
    --rollback) ROLLBACK_SHA="${2:?missing value for --rollback}"; shift 2 ;;
    --rollback-on-fail) ROLLBACK_ON_FAIL="${2:?missing value for --rollback-on-fail}"; shift 2 ;;
    *) echo "[deploy] unknown argument: $1" >&2; exit 2 ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "[deploy] missing command: $1" >&2; exit 1; }
}

set_env_kv() {
  local file="$1"
  local key="$2"
  local value="$3"
  local escaped="${value//\//\\/}"
  escaped="${escaped//&/\\&}"
  if grep -qE "^${key}=" "$file"; then
    sed -i "s/^${key}=.*/${key}=${escaped}/" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >>"$file"
  fi
}

find_runtime_layout() {
  local fixed_runtime fixed_deploy orch
  fixed_runtime="$REPO_PATH/06-工具/scripts"
  fixed_deploy="$REPO_PATH/06-工具/deploy"
  if [[ -f "$fixed_runtime/feishu_kb_orchestrator.py" && -d "$fixed_deploy" ]]; then
    RUNTIME_DIR="$fixed_runtime"
    DEPLOY_DIR="$fixed_deploy"
    TOOL_DIR="$(dirname "$RUNTIME_DIR")"
  else
    orch="$(find "$REPO_PATH" -maxdepth 4 -type f -path '*/scripts/feishu_kb_orchestrator.py' | head -n1)"
    test -n "$orch"
    RUNTIME_DIR="$(dirname "$orch")"
    TOOL_DIR="$(dirname "$RUNTIME_DIR")"
    DEPLOY_DIR="$TOOL_DIR/deploy"
  fi
  SMOKE_SCRIPT="$DEPLOY_DIR/cloud-smoke-test.sh"
  test -d "$RUNTIME_DIR"
  test -d "$DEPLOY_DIR"
  test -f "$SMOKE_SCRIPT"
}

install_or_update_writer_service() {
  local service_file="/etc/systemd/system/${WRITER_SERVICE}.service"
  local app_dir="$RUNTIME_DIR"

  if systemctl cat "$WRITER_SERVICE" >/dev/null 2>&1; then
    mkdir -p "/etc/systemd/system/${WRITER_SERVICE}.service.d"
    cat >"/etc/systemd/system/${WRITER_SERVICE}.service.d/10-runtime-appdir.conf" <<EOF
[Service]
WorkingDirectory=${app_dir}
EnvironmentFile=-/etc/openclaw/feishu.env
ExecStart=
ExecStart=/usr/bin/python3 -m uvicorn ingest_writer_api:app --app-dir ${app_dir} --host 127.0.0.1 --port 8790 --workers 1
EOF
  else
    cat >"$service_file" <<EOF
[Unit]
Description=OpenClaw Ingest Writer API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${app_dir}
EnvironmentFile=-/etc/openclaw/feishu.env
ExecStart=/usr/bin/python3 -m uvicorn ingest_writer_api:app --app-dir ${app_dir} --host 127.0.0.1 --port 8790 --workers 1
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
EOF
    systemctl enable "$WRITER_SERVICE" >/dev/null 2>&1 || true
  fi
}

install_or_update_gateway_env() {
  mkdir -p "/etc/systemd/system/${GATEWAY_SERVICE}.service.d"
  cat >"/etc/systemd/system/${GATEWAY_SERVICE}.service.d/10-feishu-env.conf" <<EOF
[Service]
EnvironmentFile=-/etc/openclaw/feishu.env
Environment=GIT_SYNC_ENABLED=true
Environment=GIT_SYNC_REPO_ROOT=${REPO_PATH}
Environment=GIT_SYNC_REMOTE=${REMOTE}
Environment=GIT_SYNC_BRANCH=${BRANCH}
Environment=GIT_SYNC_INCLUDE_PATHS=02-内容生产,03-素材库,01-选题管理
Environment=GIT_SYNC_AUTHOR_NAME=feishu-bot
Environment=GIT_SYNC_AUTHOR_EMAIL=feishu-bot@local
Environment=GIT_SYNC_MAX_RETRIES=2
EOF
}

install_or_update_async_worker() {
  local service_file="/etc/systemd/system/${ASYNC_WORKER_SERVICE}.service"
  local timer_file="/etc/systemd/system/${ASYNC_WORKER_SERVICE}.timer"
  local app_dir="$RUNTIME_DIR"
  local worker_py="$RUNTIME_DIR/bitable_link_worker.py"

  test -f "$worker_py" || { echo "[deploy] async worker missing: $worker_py" >&2; exit 1; }

  cat >"$service_file" <<EOF
[Unit]
Description=OpenClaw Bitable Link Async Worker
After=network.target ${WRITER_SERVICE}.service
Requires=${WRITER_SERVICE}.service

[Service]
Type=oneshot
User=root
WorkingDirectory=${app_dir}
EnvironmentFile=-/etc/openclaw/feishu.env
ExecStart=/usr/bin/python3 ${worker_py} --once --limit 5
EOF

  cat >"$timer_file" <<EOF
[Unit]
Description=Run ${ASYNC_WORKER_SERVICE} every minute

[Timer]
OnBootSec=20s
OnUnitActiveSec=60s
AccuracySec=5s
Unit=${ASYNC_WORKER_SERVICE}.service
Persistent=true

[Install]
WantedBy=timers.target
EOF

  systemctl enable "${ASYNC_WORKER_SERVICE}.timer" >/dev/null 2>&1 || true
}

install_link_extractors() {
  local installer="$DEPLOY_DIR/install-link-extractors.sh"
  if [[ "$INSTALL_EXTRACTORS" != "true" ]]; then
    echo "[deploy] skip extractor install (INSTALL_EXTRACTORS=$INSTALL_EXTRACTORS)"
    return 0
  fi
  if [[ ! -f "$installer" ]]; then
    echo "[deploy] extractor installer missing, skip: $installer"
    return 0
  fi
  bash "$installer" --repo-path "$REPO_PATH" --python python3
}
apply_workspace_prompt() {
  local src="$DEPLOY_DIR/openclaw-feishu-routing-prompt.md"
  local orch_py="$RUNTIME_DIR/feishu_kb_orchestrator.py"
  local runner_sh="$RUNTIME_DIR/run-feishu-kb-orchestrator.sh"
  local ws
  for ws in "${WORKSPACE_DIRS[@]}"; do
    [[ -n "$ws" ]] || continue
    mkdir -p "$ws"
    cp -f "$src" "$ws/FEISHU_ROUTING_PROMPT.md"
    # Use absolute wrapper path to ensure env injection and avoid gateway cwd drift.
    sed -i "s#06-工具/scripts/run-feishu-kb-orchestrator.sh#${runner_sh}#g" "$ws/FEISHU_ROUTING_PROMPT.md"
    sed -i "s#python 06-工具/scripts/feishu_kb_orchestrator.py#${runner_sh}#g" "$ws/FEISHU_ROUTING_PROMPT.md"
    sed -i "s#python3 ${orch_py}#${runner_sh}#g" "$ws/FEISHU_ROUTING_PROMPT.md"
    cat >"$ws/AGENTS.md" <<EOF
# AGENTS.md (Managed)
Feishu router mode.
Treat FEISHU_ROUTING_PROMPT.md as highest priority.
Always call: ${runner_sh} --text "<incoming_feishu_text>" --event-ref "<event_id_or_hash>" --source-ref "<source_ref>" --source-time "<iso8601>" --meta-json "<meta_json_or_empty>".
Do not free-form reply from model memory.
Only return orchestrator output: reply_segments (in order) or reply.
EOF
  done
}
resolve_workspace_dirs() {
  local cfg="${OPENCLAW_CONFIG_PATH}"
  local detected_raw
  detected_raw="$(python3 - <<PY
import json, pathlib
p = pathlib.Path(r'''$cfg''')
out = ["/root/.openclaw/workspace"]
try:
    if p.exists():
        obj = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        def walk(node):
            if isinstance(node, dict):
                ws = node.get("workspace")
                if isinstance(ws, str) and ws.strip():
                    out.append(ws.strip())
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(obj)
except Exception:
    pass
seen = set()
for item in out:
    if item in seen:
        continue
    seen.add(item)
    print(item)
PY
)"
  mapfile -t WORKSPACE_DIRS <<<"$detected_raw"
  if [[ ${#WORKSPACE_DIRS[@]} -eq 0 ]]; then
    WORKSPACE_DIRS=("/root/.openclaw/workspace")
  fi
  WORKSPACE_DIR="${WORKSPACE_DIRS[0]}"
}

require_cmd git
require_cmd python3
require_cmd systemctl
require_cmd curl

cd "$REPO_PATH"
git rev-parse --is-inside-work-tree >/dev/null
PREV_SHA="$(git rev-parse HEAD)"

rollback() {
  if [[ "$ROLLBACK_ON_FAIL" == "true" ]]; then
    echo "[deploy] failed, rollback to ${PREV_SHA}"
    git -C "$REPO_PATH" reset --hard "$PREV_SHA" || true
    systemctl daemon-reload || true
    systemctl restart "$WRITER_SERVICE" || true
    systemctl restart "$GATEWAY_SERVICE" || true
    systemctl restart "${ASYNC_WORKER_SERVICE}.timer" || true
  fi
}
trap rollback ERR

if [[ -n "$ROLLBACK_SHA" ]]; then
  echo "[deploy] rollback requested => $ROLLBACK_SHA"
  git fetch "$REMOTE" "$BRANCH"
  git checkout "$BRANCH"
  git reset --hard "$ROLLBACK_SHA"
else
  echo "[deploy] fetch ${REMOTE}/${BRANCH}"
  git fetch "$REMOTE" "$BRANCH"
  git checkout "$BRANCH"
  git reset --hard "${REMOTE}/${BRANCH}"
fi

find_runtime_layout

echo "[deploy] runtime dir: $RUNTIME_DIR"
echo "[deploy] deploy dir:  $DEPLOY_DIR"
resolve_workspace_dirs
echo "[deploy] workspace:   $WORKSPACE_DIR"
echo "[deploy] workspaces:  ${WORKSPACE_DIRS[*]}"

mkdir -p /etc/openclaw
touch /etc/openclaw/feishu.env
chmod 600 /etc/openclaw/feishu.env

set_env_kv /etc/openclaw/feishu.env FEISHU_COMMANDER_WORKERS "2"
set_env_kv /etc/openclaw/feishu.env FEISHU_COMMANDER_MAX_RETRIES "1"
set_env_kv /etc/openclaw/feishu.env FEISHU_SKILL_MODEL "gpt-5.3-codex"
set_env_kv /etc/openclaw/feishu.env FEISHU_CHAT_MODEL "gpt-5.3-codex"
set_env_kv /etc/openclaw/feishu.env FEISHU_PLAIN_TEXT_MODE "chat"
set_env_kv /etc/openclaw/feishu.env FEISHU_REPLY_MAX_CHARS "1500"
set_env_kv /etc/openclaw/feishu.env INGEST_LINK_MIN_CONTENT_CHARS "120"
set_env_kv /etc/openclaw/feishu.env INGEST_LINK_ALLOW_TEST_SKIP "true"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_STRICT_FULL_TEXT "true"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_SUMMARY_BLOCK "true"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_MIN_SENTENCES "3"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_SOURCE_MODE "${INGEST_DOUYIN_SOURCE_MODE:-hybrid}"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_PIPELINE_MODE "${INGEST_DOUYIN_PIPELINE_MODE:-asr_primary}"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_BITABLE_ENABLED "true"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_BITABLE_READ_FIRST "true"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_BITABLE_FALLBACK_FULL_SCAN "true"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_BITABLE_WRITE_BACK "true"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_ASR_ENABLED "${INGEST_DOUYIN_ASR_ENABLED:-true}"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_ASR_API_KEY "${INGEST_DOUYIN_ASR_API_KEY:-${API_KEY:-}}"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_ASR_TIMEOUT_SEC "${INGEST_DOUYIN_ASR_TIMEOUT_SEC:-600}"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_DEDUP_KEY_MODE "${INGEST_DOUYIN_DEDUP_KEY_MODE:-video_or_canonical_url}"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_WRITE_SUMMARY "${INGEST_DOUYIN_WRITE_SUMMARY:-true}"
set_env_kv /etc/openclaw/feishu.env INGEST_DOUYIN_WRITE_KEYPOINTS "${INGEST_DOUYIN_WRITE_KEYPOINTS:-true}"
set_env_kv /etc/openclaw/feishu.env BITABLE_APP_TOKEN "${BITABLE_APP_TOKEN:-UrwobWA3JadzAcsLqJbc6CThnRd}"
set_env_kv /etc/openclaw/feishu.env BITABLE_TABLE_ID "${BITABLE_TABLE_ID:-tblr1mvEh1bFsUAS}"
set_env_kv /etc/openclaw/feishu.env BITABLE_VIEW_ID "${BITABLE_VIEW_ID:-vew5Oj8RIj}"
set_env_kv /etc/openclaw/feishu.env BITABLE_TEXT_FIELD "${BITABLE_TEXT_FIELD:-文案整理}"
set_env_kv /etc/openclaw/feishu.env BITABLE_TEXT_FALLBACK_FIELD "${BITABLE_TEXT_FALLBACK_FIELD:-文案出参}"
set_env_kv /etc/openclaw/feishu.env FEISHU_LINK_ASYNC_ENABLED "true"
set_env_kv /etc/openclaw/feishu.env FEISHU_LINK_ASYNC_POLL_INTERVAL_SEC "60"
set_env_kv /etc/openclaw/feishu.env FEISHU_LINK_ASYNC_TIMEOUT_MIN "20"
set_env_kv /etc/openclaw/feishu.env FEISHU_LINK_ASYNC_BATCH "5"
set_env_kv /etc/openclaw/feishu.env FEISHU_TOOL_DIR "$TOOL_DIR"
set_env_kv /etc/openclaw/feishu.env FEISHU_LINK_ASYNC_DB "$TOOL_DIR/data/feishu-orchestrator/link_async_jobs.db"
set_env_kv /etc/openclaw/feishu.env GIT_SYNC_ENABLED "true"
set_env_kv /etc/openclaw/feishu.env GIT_SYNC_REPO_ROOT "$REPO_PATH"
set_env_kv /etc/openclaw/feishu.env GIT_SYNC_REMOTE "$REMOTE"
set_env_kv /etc/openclaw/feishu.env GIT_SYNC_BRANCH "$BRANCH"
set_env_kv /etc/openclaw/feishu.env GIT_SYNC_INCLUDE_PATHS "02-内容生产,03-素材库,01-选题管理"
set_env_kv /etc/openclaw/feishu.env GIT_SYNC_AUTHOR_NAME "feishu-bot"
set_env_kv /etc/openclaw/feishu.env GIT_SYNC_AUTHOR_EMAIL "feishu-bot@local"
set_env_kv /etc/openclaw/feishu.env GIT_SYNC_MAX_RETRIES "2"

apply_workspace_prompt
install_or_update_writer_service
install_or_update_gateway_env
install_or_update_async_worker
install_link_extractors

python3 -m py_compile \
  "$RUNTIME_DIR/feishu_ingest_router.py" \
  "$RUNTIME_DIR/feishu_kb_orchestrator.py" \
  "$RUNTIME_DIR/feishu_skill_runner.py" \
  "$RUNTIME_DIR/feishu_http_client.py" \
  "$RUNTIME_DIR/link_async_jobs.py" \
  "$RUNTIME_DIR/bitable_link_worker.py" \
  "$RUNTIME_DIR/link_to_quotes.py" \
  "$RUNTIME_DIR/git_sync_after_write.py"
if [[ -f "$RUNTIME_DIR/douyin_asr_extractor.py" ]]; then
  python3 -m py_compile "$RUNTIME_DIR/douyin_asr_extractor.py"
fi

chmod +x "$SMOKE_SCRIPT" "$RUNTIME_DIR/run-feishu-kb-orchestrator.sh"
if [[ -f "$DEPLOY_DIR/verify-real-feishu-chain.sh" ]]; then
  chmod +x "$DEPLOY_DIR/verify-real-feishu-chain.sh"
fi
if [[ -f "$DEPLOY_DIR/verify-link-by-event.sh" ]]; then
  chmod +x "$DEPLOY_DIR/verify-link-by-event.sh"
fi

systemctl daemon-reload
systemctl restart "$WRITER_SERVICE"
systemctl restart "$GATEWAY_SERVICE"
systemctl restart "${ASYNC_WORKER_SERVICE}.timer"
systemctl start "${ASYNC_WORKER_SERVICE}.service" || true
systemctl is-active --quiet "$WRITER_SERVICE"
systemctl is-active --quiet "$GATEWAY_SERVICE"
systemctl is-active --quiet "${ASYNC_WORKER_SERVICE}.timer"

bash "$SMOKE_SCRIPT" --repo-path "$REPO_PATH" --scripts-dir "$RUNTIME_DIR" --writer-service "$WRITER_SERVICE" --gateway-service "$GATEWAY_SERVICE"

trap - ERR
echo "[deploy] OK"
echo "[deploy] now at commit: $(git rev-parse HEAD)"
