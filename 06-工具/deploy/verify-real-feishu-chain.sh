#!/usr/bin/env bash
set -euo pipefail

REPO_PATH="${CLOUD_DEPLOY_PATH:-/root/gzh-xhs}"
WORKSPACE_DIR="${OPENCLAW_WORKSPACE_DIR:-/root/.openclaw/workspace}"
GATEWAY_SERVICE="${OPENCLAW_GATEWAY_SERVICE:-openclaw-gateway}"
WRITER_SERVICE="${INGEST_WRITER_SERVICE:-ingest-writer-api}"
SINCE_MINUTES=30
EVENT_REF_CONTAINS=""
KEYWORD=""
EXPECT_INGEST=true
REQUIRE_GIT_SYNC=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-path) REPO_PATH="${2:?missing value for --repo-path}"; shift 2 ;;
    --workspace-dir) WORKSPACE_DIR="${2:?missing value for --workspace-dir}"; shift 2 ;;
    --gateway-service) GATEWAY_SERVICE="${2:?missing value for --gateway-service}"; shift 2 ;;
    --writer-service) WRITER_SERVICE="${2:?missing value for --writer-service}"; shift 2 ;;
    --since-minutes) SINCE_MINUTES="${2:?missing value for --since-minutes}"; shift 2 ;;
    --event-ref-contains) EVENT_REF_CONTAINS="${2:?missing value for --event-ref-contains}"; shift 2 ;;
    --keyword) KEYWORD="${2:?missing value for --keyword}"; shift 2 ;;
    --expect-ingest) EXPECT_INGEST="${2:?missing value for --expect-ingest}"; shift 2 ;;
    --require-git-sync) REQUIRE_GIT_SYNC="${2:?missing value for --require-git-sync}"; shift 2 ;;
    *) echo "[verify] unknown argument: $1" >&2; exit 2 ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "[verify] missing command: $1" >&2; exit 1; }
}

as_bool() {
  local raw="${1:-}"
  raw="$(echo "$raw" | tr '[:upper:]' '[:lower:]')"
  [[ "$raw" == "1" || "$raw" == "true" || "$raw" == "yes" || "$raw" == "y" || "$raw" == "on" ]]
}

require_cmd python3
require_cmd systemctl
require_cmd curl
require_cmd grep
require_cmd journalctl

echo "[verify] repo=$REPO_PATH"
echo "[verify] workspace=$WORKSPACE_DIR"
echo "[verify] since_minutes=$SINCE_MINUTES"

test -d "$REPO_PATH"
test -d "$WORKSPACE_DIR"
test -f "$WORKSPACE_DIR/FEISHU_ROUTING_PROMPT.md"
test -f "$WORKSPACE_DIR/AGENTS.md"

grep -q "run-feishu-kb-orchestrator.sh" "$WORKSPACE_DIR/FEISHU_ROUTING_PROMPT.md" \
  || { echo "[verify] FAIL: FEISHU_ROUTING_PROMPT.md does not use wrapper"; exit 1; }
grep -q "run-feishu-kb-orchestrator.sh" "$WORKSPACE_DIR/AGENTS.md" \
  || { echo "[verify] FAIL: AGENTS.md does not use wrapper"; exit 1; }

systemctl is-active --quiet "$WRITER_SERVICE" || { echo "[verify] FAIL: writer service inactive"; exit 1; }
systemctl is-active --quiet "$GATEWAY_SERVICE" || { echo "[verify] FAIL: gateway service inactive"; exit 1; }
curl -fsS "http://127.0.0.1:8790/internal/healthz" >/dev/null || { echo "[verify] FAIL: writer /healthz failed"; exit 1; }

LOG_TMP="$(mktemp)"
RUN_TMP="$(mktemp)"
trap 'rm -f "$LOG_TMP" "$RUN_TMP"' EXIT

journalctl -u "$GATEWAY_SERVICE" --since "${SINCE_MINUTES} min ago" --no-pager >"$LOG_TMP" || true
grep -E "received message|dispatching to agent|dispatch complete" "$LOG_TMP" >/dev/null \
  || { echo "[verify] FAIL: gateway log has no feishu dispatch records in last ${SINCE_MINUTES}m"; exit 1; }

RUN_LOG="$(ls -1 "$REPO_PATH"/06-*/data/feishu-orchestrator/runs/"$(date +%F).jsonl" 2>/dev/null | head -n1 || true)"
test -n "$RUN_LOG" || { echo "[verify] FAIL: run log missing under $REPO_PATH/06-*/data/feishu-orchestrator/runs/"; exit 1; }
test -f "$RUN_LOG" || { echo "[verify] FAIL: run log not a file: $RUN_LOG"; exit 1; }

if ! python3 - "$RUN_LOG" "$SINCE_MINUTES" "$EVENT_REF_CONTAINS" "$EXPECT_INGEST" "$REQUIRE_GIT_SYNC" >"$RUN_TMP" <<'PY'
import datetime as dt
import json
import pathlib
import sys

run_log = pathlib.Path(sys.argv[1])
since_minutes = int(sys.argv[2])
event_ref_contains = sys.argv[3].strip()
expect_ingest = sys.argv[4].strip().lower() in {"1", "true", "yes", "y", "on"}
require_git_sync = sys.argv[5].strip().lower() in {"1", "true", "yes", "y", "on"}

cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=since_minutes)

def parse_ts(raw: str):
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None

rows = []
for line in run_log.read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        row = json.loads(line)
    except Exception:
        continue
    event_ref = str(row.get("event_ref") or "")
    if event_ref.startswith("smoke-"):
        continue
    if event_ref_contains and event_ref_contains not in event_ref:
        continue
    ts = parse_ts(row.get("ts"))
    if ts is None or ts < cutoff:
        continue
    rows.append((ts, row))

if not rows:
    print("FAIL:no_non_smoke_event")
    sys.exit(1)

rows.sort(key=lambda item: item[0])
latest_ts, latest = rows[-1]
intent = latest.get("intent") or {}
status = str(latest.get("status") or "")
ingest = bool(intent.get("ingest"))
trigger = str(intent.get("ingest_trigger") or latest.get("ingest_trigger") or "")
git_sync_status = latest.get("git_sync_status")
git_sync_commit = str(latest.get("git_sync_commit") or "")

if status not in {"success", "partial"}:
    print(f"FAIL:bad_status:{status}")
    sys.exit(1)

if expect_ingest and not ingest:
    print("FAIL:ingest_not_triggered")
    sys.exit(1)

if require_git_sync and ingest:
    if git_sync_status in {None, "", "None"}:
        print("FAIL:git_sync_status_missing")
        sys.exit(1)
    if str(git_sync_status) == "error":
        print("FAIL:git_sync_error")
        sys.exit(1)

print("OK")
print(f"event_ref={latest.get('event_ref')}")
print(f"status={status}")
print(f"ingest={ingest}")
print(f"trigger={trigger}")
print(f"git_sync_status={git_sync_status}")
print(f"git_sync_commit={git_sync_commit}")
PY
then
  cat "$RUN_TMP" >&2 || true
  echo "[verify] FAIL: run log validation failed" >&2
  exit 1
fi

if ! grep -q '^OK$' "$RUN_TMP"; then
  cat "$RUN_TMP" >&2
  echo "[verify] FAIL: run log validation failed" >&2
  exit 1
fi

LATEST_EVENT_REF="$(grep '^event_ref=' "$RUN_TMP" | head -n1 | cut -d= -f2-)"
test -n "$LATEST_EVENT_REF" || { echo "[verify] FAIL: latest event_ref missing"; exit 1; }

DB_PATH="$(ls -1 "$REPO_PATH"/06-*/data/ingest-writer/writer_state.db 2>/dev/null | head -n1 || true)"
test -n "$DB_PATH" || { echo "[verify] FAIL: writer db missing under $REPO_PATH/06-*/data/ingest-writer/"; exit 1; }
test -f "$DB_PATH" || { echo "[verify] FAIL: writer db not a file: $DB_PATH"; exit 1; }

python3 - "$DB_PATH" "$LATEST_EVENT_REF" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
event_ref = sys.argv[2]
prefix = f"{event_ref}#%"

conn = sqlite3.connect(db_path)
try:
    cur = conn.cursor()
    total = cur.execute(
        "select count(1) from requests where event_ref like ?",
        (prefix,),
    ).fetchone()[0]
    if int(total or 0) < 1:
        print(f"[verify] FAIL: writer db has no row for event_ref={event_ref}", file=sys.stderr)
        sys.exit(1)

    bad = cur.execute(
        "select count(1) from requests where event_ref like ? and status != 'success'",
        (prefix,),
    ).fetchone()[0]
    if int(bad or 0) > 0:
        print(f"[verify] FAIL: writer db has non-success rows for event_ref={event_ref}", file=sys.stderr)
        for row in cur.execute(
            "select event_ref,mode,status,updated_at from requests where event_ref like ? order by updated_at desc",
            (prefix,),
        ):
            print(row, file=sys.stderr)
        sys.exit(1)
finally:
    conn.close()
PY

if [[ -n "$KEYWORD" ]]; then
  if ! grep -R -n --include="*.md" -- "$KEYWORD" "$REPO_PATH"/03-* "$REPO_PATH"/01-* >/dev/null 2>&1; then
    echo "[verify] FAIL: keyword not found in markdown files under 01-*/03-* : $KEYWORD" >&2
    exit 1
  fi
fi

echo "[verify] PASS"
cat "$RUN_TMP"
python3 - "$DB_PATH" "$LATEST_EVENT_REF" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
event_ref = sys.argv[2]
prefix = f"{event_ref}#%"

conn = sqlite3.connect(db_path)
try:
    for row in conn.execute(
        "select event_ref,mode,status,updated_at from requests where event_ref like ? order by updated_at desc",
        (prefix,),
    ):
        print(row)
finally:
    conn.close()
PY
