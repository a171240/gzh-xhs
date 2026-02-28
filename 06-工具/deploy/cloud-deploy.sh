#!/usr/bin/env bash
set -euo pipefail

REPO_PATH="${CLOUD_DEPLOY_PATH:-/root/gzh-xhs}"
REMOTE="${CLOUD_DEPLOY_REMOTE:-origin}"
BRANCH="${CLOUD_DEPLOY_BRANCH:-main}"
GATEWAY_SERVICE="${OPENCLAW_GATEWAY_SERVICE:-openclaw-gateway}"
WRITER_SERVICE="${INGEST_WRITER_SERVICE:-ingest-writer-api}"
ROLLBACK_ON_FAIL="${ROLLBACK_ON_FAIL:-true}"
ROLLBACK_SHA=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-path) REPO_PATH="${2:?missing value for --repo-path}"; shift 2 ;;
    --remote) REMOTE="${2:?missing value for --remote}"; shift 2 ;;
    --branch) BRANCH="${2:?missing value for --branch}"; shift 2 ;;
    --gateway-service) GATEWAY_SERVICE="${2:?missing value for --gateway-service}"; shift 2 ;;
    --writer-service) WRITER_SERVICE="${2:?missing value for --writer-service}"; shift 2 ;;
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
  cat >"/etc/systemd/system/${GATEWAY_SERVICE}.service.d/10-feishu-env.conf" <<'EOF'
[Service]
EnvironmentFile=-/etc/openclaw/feishu.env
EOF
}

apply_workspace_prompt() {
  local src="$DEPLOY_DIR/openclaw-feishu-routing-prompt.md"
  local ws="/root/.openclaw/workspace"
  local orch_py="$RUNTIME_DIR/feishu_kb_orchestrator.py"
  mkdir -p "$ws"
  cp -f "$src" "$ws/FEISHU_ROUTING_PROMPT.md"
  # Use absolute orchestrator path to avoid gateway cwd drift.
  sed -i "s#python 06-工具/scripts/feishu_kb_orchestrator.py#python3 ${orch_py}#g" "$ws/FEISHU_ROUTING_PROMPT.md"
  cat >"$ws/AGENTS.md" <<EOF
Feishu router mode.
Always call: python3 ${orch_py} ...
Do not free-form reply.
Only return reply or reply_segments.
EOF
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

mkdir -p /etc/openclaw
touch /etc/openclaw/feishu.env
chmod 600 /etc/openclaw/feishu.env

set_env_kv /etc/openclaw/feishu.env FEISHU_COMMANDER_WORKERS "2"
set_env_kv /etc/openclaw/feishu.env FEISHU_COMMANDER_MAX_RETRIES "1"
set_env_kv /etc/openclaw/feishu.env FEISHU_SKILL_MODEL "gpt-5.3-codex"
set_env_kv /etc/openclaw/feishu.env FEISHU_CHAT_MODEL "gpt-5.3-codex"
set_env_kv /etc/openclaw/feishu.env FEISHU_PLAIN_TEXT_MODE "chat"
set_env_kv /etc/openclaw/feishu.env FEISHU_REPLY_MAX_CHARS "1500"
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

python3 -m py_compile \
  "$RUNTIME_DIR/feishu_ingest_router.py" \
  "$RUNTIME_DIR/feishu_kb_orchestrator.py" \
  "$RUNTIME_DIR/feishu_skill_runner.py" \
  "$RUNTIME_DIR/link_to_quotes.py" \
  "$RUNTIME_DIR/git_sync_after_write.py"

chmod +x "$SMOKE_SCRIPT"

systemctl daemon-reload
systemctl restart "$WRITER_SERVICE"
systemctl restart "$GATEWAY_SERVICE"
systemctl is-active --quiet "$WRITER_SERVICE"
systemctl is-active --quiet "$GATEWAY_SERVICE"

bash "$SMOKE_SCRIPT" --repo-path "$REPO_PATH" --scripts-dir "$RUNTIME_DIR" --writer-service "$WRITER_SERVICE" --gateway-service "$GATEWAY_SERVICE"

trap - ERR
echo "[deploy] OK"
echo "[deploy] now at commit: $(git rev-parse HEAD)"
