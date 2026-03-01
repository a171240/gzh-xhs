#!/usr/bin/env bash
set -euo pipefail

REPO_PATH="${CLOUD_DEPLOY_PATH:-/root/gzh-xhs}"
SCRIPTS_DIR=""
GATEWAY_SERVICE="${OPENCLAW_GATEWAY_SERVICE:-openclaw-gateway}"
WRITER_SERVICE="${INGEST_WRITER_SERVICE:-ingest-writer-api}"
WRITER_HEALTH_URL="${INGEST_WRITER_HEALTH_URL:-http://127.0.0.1:8790/internal/healthz}"
WRITER_HEALTH_RETRIES="${INGEST_WRITER_HEALTH_RETRIES:-20}"
WRITER_HEALTH_RETRY_INTERVAL_SEC="${INGEST_WRITER_HEALTH_RETRY_INTERVAL_SEC:-1}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-path) REPO_PATH="${2:?missing value for --repo-path}"; shift 2 ;;
    --scripts-dir) SCRIPTS_DIR="${2:?missing value for --scripts-dir}"; shift 2 ;;
    --gateway-service) GATEWAY_SERVICE="${2:?missing value for --gateway-service}"; shift 2 ;;
    --writer-service) WRITER_SERVICE="${2:?missing value for --writer-service}"; shift 2 ;;
    *) echo "[smoke] unknown argument: $1" >&2; exit 2 ;;
  esac
done

wait_for_writer_health() {
  local retries interval i
  retries="${WRITER_HEALTH_RETRIES}"
  interval="${WRITER_HEALTH_RETRY_INTERVAL_SEC}"

  for ((i=1; i<=retries; i++)); do
    if systemctl is-active --quiet "$WRITER_SERVICE" && curl -fsS "$WRITER_HEALTH_URL" >/dev/null; then
      return 0
    fi
    sleep "$interval"
  done

  echo "[smoke] FAIL: writer health check timeout after ${retries} retries (${WRITER_HEALTH_URL})" >&2
  systemctl status "$WRITER_SERVICE" --no-pager -l || true
  journalctl -u "$WRITER_SERVICE" -n 60 --no-pager || true
  return 1
}

if [[ -z "$SCRIPTS_DIR" ]]; then
  if [[ -f "$REPO_PATH/06-工具/scripts/feishu_kb_orchestrator.py" ]]; then
    SCRIPTS_DIR="$REPO_PATH/06-工具/scripts"
  else
    ORCH_FILE="$(find "$REPO_PATH" -maxdepth 4 -type f -path '*/scripts/feishu_kb_orchestrator.py' | head -n1)"
    test -n "$ORCH_FILE"
    SCRIPTS_DIR="$(dirname "$ORCH_FILE")"
  fi
fi

ORCH="$SCRIPTS_DIR/feishu_kb_orchestrator.py"

echo "[smoke] repo=$REPO_PATH"
echo "[smoke] scripts=$SCRIPTS_DIR"

test -d "$REPO_PATH"
test -d "$SCRIPTS_DIR"
test -f "$ORCH"
test -f "$SCRIPTS_DIR/feishu_ingest_router.py"
test -f "$SCRIPTS_DIR/feishu_skill_runner.py"
test -f "$SCRIPTS_DIR/link_to_quotes.py"
test -f "$SCRIPTS_DIR/git_sync_after_write.py"

python3 -m py_compile \
  "$SCRIPTS_DIR/feishu_ingest_router.py" \
  "$SCRIPTS_DIR/feishu_kb_orchestrator.py" \
  "$SCRIPTS_DIR/feishu_skill_runner.py" \
  "$SCRIPTS_DIR/link_to_quotes.py" \
  "$SCRIPTS_DIR/git_sync_after_write.py"

systemctl is-active --quiet "$WRITER_SERVICE"
systemctl is-active --quiet "$GATEWAY_SERVICE"
wait_for_writer_health

python3 - "$ORCH" <<'PY'
import json
import subprocess
import sys
import time

orch = sys.argv[1]
ts = int(time.time())

cases = [
    ("plain", "先完成再完美", False, False, "none"),
    ("quote_at", "@Winnie蛋：真正拉开差距的，从来不是天赋，而是长期正确。", True, False, "at_prefix"),
    ("quote_text", "金句：复利不是天赋，是情绪稳定下的重复执行。", True, False, "text_prefix"),
    ("link", "https://raw.githubusercontent.com/openai/openai-python/main/README.md", True, False, "url"),
    ("skill", "/skill wechat 平台=公众号 需求=写一篇关于复利思维的文章，1200字", False, True, "none"),
]

for idx, (name, text, ingest, skill, trigger) in enumerate(cases):
    cmd = [
        "python3",
        orch,
        "--text",
        text,
        "--event-ref",
        f"smoke-{name}-{ts}-{idx}",
        "--source-ref",
        "cloud-smoke",
        "--dry-run",
    ]
    raw = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace")
    data = json.loads(raw)
    intent = data.get("intent") or {}

    if bool(intent.get("ingest")) != ingest:
        raise SystemExit(f"smoke {name}: ingest mismatch, got={intent.get('ingest')}, want={ingest}")
    if bool(intent.get("skill")) != skill:
        raise SystemExit(f"smoke {name}: skill mismatch, got={intent.get('skill')}, want={skill}")
    if str(data.get("ingest_trigger") or "") != trigger:
        raise SystemExit(f"smoke {name}: trigger mismatch, got={data.get('ingest_trigger')}, want={trigger}")
    if not str(data.get("reply") or "").strip():
        raise SystemExit(f"smoke {name}: empty reply")

print("smoke checks passed")
PY

echo "[smoke] PASS"
