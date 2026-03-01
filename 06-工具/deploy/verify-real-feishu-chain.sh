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
REQUIRE_CONTENT_SUCCESS=false
ALLOW_TEST_URL_SKIP=true
MIN_CONTENT_CHARS=120

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
    --require-content-success) REQUIRE_CONTENT_SUCCESS="${2:?missing value for --require-content-success}"; shift 2 ;;
    --allow-test-url-skip) ALLOW_TEST_URL_SKIP="${2:?missing value for --allow-test-url-skip}"; shift 2 ;;
    --min-content-chars) MIN_CONTENT_CHARS="${2:?missing value for --min-content-chars}"; shift 2 ;;
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

if ! python3 - "$RUN_LOG" "$SINCE_MINUTES" "$EVENT_REF_CONTAINS" "$EXPECT_INGEST" "$REQUIRE_GIT_SYNC" "$REQUIRE_CONTENT_SUCCESS" "$ALLOW_TEST_URL_SKIP" "$MIN_CONTENT_CHARS" >"$RUN_TMP" <<'PY'
import datetime as dt
import json
import pathlib
import sys

run_log = pathlib.Path(sys.argv[1])
since_minutes = int(sys.argv[2])
event_ref_contains = sys.argv[3].strip()
expect_ingest = sys.argv[4].strip().lower() in {"1", "true", "yes", "y", "on"}
require_git_sync = sys.argv[5].strip().lower() in {"1", "true", "yes", "y", "on"}
require_content_success = sys.argv[6].strip().lower() in {"1", "true", "yes", "y", "on"}
allow_test_url_skip = sys.argv[7].strip().lower() in {"1", "true", "yes", "y", "on"}
min_content_chars = max(1, int(sys.argv[8]))

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

def row_meta(row):
    intent = row.get("intent") or {}
    status = str(row.get("status") or "")
    ingest = bool(intent.get("ingest"))
    trigger = str(intent.get("ingest_trigger") or row.get("ingest_trigger") or "")
    git_sync_status = row.get("git_sync_status")
    git_sync_commit = str(row.get("git_sync_commit") or "")
    link_route_status = str(row.get("link_route_status") or "")
    link_content_status = str(row.get("link_content_status") or "")
    link_content_chars = int(row.get("link_content_chars") or 0)
    link_provider = str(row.get("link_provider") or "")
    link_is_test = bool(row.get("link_is_test"))
    link_quality_reason = str(row.get("link_quality_reason") or "")
    return {
        "intent": intent,
        "status": status,
        "ingest": ingest,
        "trigger": trigger,
        "git_sync_status": git_sync_status,
        "git_sync_commit": git_sync_commit,
        "link_route_status": link_route_status,
        "link_content_status": link_content_status,
        "link_content_chars": link_content_chars,
        "link_provider": link_provider,
        "link_is_test": link_is_test,
        "link_quality_reason": link_quality_reason,
    }

def row_failures(row, meta):
    fails = []
    status = meta["status"]
    ingest = meta["ingest"]
    trigger = meta["trigger"]
    git_sync_status = meta["git_sync_status"]
    link_route_status = meta["link_route_status"]
    link_content_status = meta["link_content_status"]
    link_content_chars = meta["link_content_chars"]
    link_is_test = meta["link_is_test"]

    if status not in {"success", "partial"}:
        fails.append(f"bad_status:{status or 'missing'}")
    if expect_ingest and not ingest:
        fails.append("ingest_not_triggered")
    if require_git_sync and ingest:
        if git_sync_status in {None, "", "None"}:
            fails.append("git_sync_status_missing")
        elif str(git_sync_status) == "error":
            fails.append("git_sync_error")

    if require_content_success and ingest:
        has_link_flow = (
            str(trigger).lower() == "url"
            or link_route_status in {"success", "partial"}
            or link_content_status not in {"", "none"}
        )
        if has_link_flow:
            if allow_test_url_skip and link_content_status == "skipped_test":
                pass
            elif link_content_status != "success":
                fails.append(f"content_not_success:{link_content_status or 'missing'}")
            elif (not link_is_test) and link_content_chars < min_content_chars:
                fails.append(f"content_chars_too_low:{link_content_chars}<{min_content_chars}")
    return fails

rows.sort(key=lambda item: item[0], reverse=True)
selected = None
failed = []
for ts, row in rows:
    meta = row_meta(row)
    fails = row_failures(row, meta)
    if not fails:
        selected = (ts, row, meta)
        break
    failed.append((ts, row, meta, fails))

if selected is None:
    latest_ts, latest_row, latest_meta, latest_fails = failed[0]
    print("FAIL:no_qualified_event")
    print(f"LAST_FAIL:event_ref={latest_row.get('event_ref')}")
    print(f"LAST_FAIL:status={latest_meta['status']}")
    print(f"LAST_FAIL:ingest={latest_meta['ingest']}")
    print(f"LAST_FAIL:trigger={latest_meta['trigger']}")
    print(f"LAST_FAIL:reasons={','.join(latest_fails)}")
    sys.exit(1)

latest_ts, latest, meta = selected
status = meta["status"]
ingest = meta["ingest"]
trigger = meta["trigger"]
git_sync_status = meta["git_sync_status"]
git_sync_commit = meta["git_sync_commit"]
link_route_status = meta["link_route_status"]
link_content_status = meta["link_content_status"]
link_content_chars = meta["link_content_chars"]
link_provider = meta["link_provider"]
link_is_test = meta["link_is_test"]
link_quality_reason = meta["link_quality_reason"]

print("OK")
print(f"event_ref={latest.get('event_ref')}")
print(f"status={status}")
print(f"ingest={ingest}")
print(f"trigger={trigger}")
print(f"git_sync_status={git_sync_status}")
print(f"git_sync_commit={git_sync_commit}")
print(f"link_route_status={link_route_status}")
print(f"link_content_status={link_content_status}")
print(f"link_content_chars={link_content_chars}")
print(f"link_provider={link_provider}")
print(f"link_is_test={link_is_test}")
print(f"link_quality_reason={link_quality_reason}")
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

python3 - "$DB_PATH" "$LATEST_EVENT_REF" "$REQUIRE_CONTENT_SUCCESS" "$ALLOW_TEST_URL_SKIP" "$MIN_CONTENT_CHARS" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
event_ref = sys.argv[2]
require_content_success = sys.argv[3].strip().lower() in {"1", "true", "yes", "y", "on"}
allow_test_url_skip = sys.argv[4].strip().lower() in {"1", "true", "yes", "y", "on"}
min_content_chars = max(1, int(sys.argv[5]))
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

    if require_content_success:
        cols = {r[1] for r in cur.execute("pragma table_info(requests)").fetchall()}
        needed = {"content_status", "content_chars", "is_test_url"}
        if needed.issubset(cols):
            rows = list(
                cur.execute(
                    "select event_ref,mode,content_status,content_chars,is_test_url,quality_reason from requests where event_ref like ? order by updated_at desc",
                    (prefix,),
                )
            )
            for ev, mode, c_status, c_chars, is_test, quality in rows:
                c_status = str(c_status or "")
                c_chars = int(c_chars or 0)
                is_test = bool(is_test)
                mode = str(mode or "")
                if mode not in {"link", "mixed"} and c_status in {"", "none"}:
                    continue
                if allow_test_url_skip and c_status == "skipped_test":
                    continue
                if c_status and c_status != "success":
                    print(f"[verify] FAIL: content_status not success for {ev}: {c_status} ({quality or ''})", file=sys.stderr)
                    sys.exit(1)
                if (not is_test) and c_status == "success" and c_chars < min_content_chars:
                    print(f"[verify] FAIL: content_chars too low for {ev}: {c_chars}<{min_content_chars}", file=sys.stderr)
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
