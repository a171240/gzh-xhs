#!/usr/bin/env bash
set -euo pipefail

REPO_PATH="${CLOUD_DEPLOY_PATH:-/root/gzh-xhs}"
EVENT_REF=""
REQUIRE_GIT_SYNC=true
REQUIRE_CONTENT_SUCCESS=true
ALLOW_TEST_URL_SKIP=true
MIN_CONTENT_CHARS=120
KEYWORD=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-path) REPO_PATH="${2:?missing value for --repo-path}"; shift 2 ;;
    --event-ref) EVENT_REF="${2:?missing value for --event-ref}"; shift 2 ;;
    --require-git-sync) REQUIRE_GIT_SYNC="${2:?missing value for --require-git-sync}"; shift 2 ;;
    --require-content-success) REQUIRE_CONTENT_SUCCESS="${2:?missing value for --require-content-success}"; shift 2 ;;
    --allow-test-url-skip) ALLOW_TEST_URL_SKIP="${2:?missing value for --allow-test-url-skip}"; shift 2 ;;
    --min-content-chars) MIN_CONTENT_CHARS="${2:?missing value for --min-content-chars}"; shift 2 ;;
    --keyword) KEYWORD="${2:?missing value for --keyword}"; shift 2 ;;
    *) echo "[verify-event] unknown argument: $1" >&2; exit 2 ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "[verify-event] missing command: $1" >&2; exit 1; }
}

require_cmd python3

echo "[verify-event] repo=$REPO_PATH"
test -d "$REPO_PATH"

RUN_LOG="$(ls -1 "$REPO_PATH"/06-*/data/feishu-orchestrator/runs/"$(date +%F).jsonl" 2>/dev/null | head -n1 || true)"
DB_PATH="$(ls -1 "$REPO_PATH"/06-*/data/ingest-writer/writer_state.db 2>/dev/null | head -n1 || true)"
test -n "$RUN_LOG" || { echo "[verify-event] FAIL: run log missing"; exit 1; }
test -f "$RUN_LOG" || { echo "[verify-event] FAIL: run log not file: $RUN_LOG"; exit 1; }
test -n "$DB_PATH" || { echo "[verify-event] FAIL: writer db missing"; exit 1; }
test -f "$DB_PATH" || { echo "[verify-event] FAIL: writer db not file: $DB_PATH"; exit 1; }

TMP_OUT="$(mktemp)"
trap 'rm -f "$TMP_OUT"' EXIT

if ! python3 - "$RUN_LOG" "$DB_PATH" "$EVENT_REF" "$REQUIRE_GIT_SYNC" "$REQUIRE_CONTENT_SUCCESS" "$ALLOW_TEST_URL_SKIP" "$MIN_CONTENT_CHARS" >"$TMP_OUT" <<'PY'
import datetime as dt
import json
import pathlib
import sqlite3
import sys

run_log = pathlib.Path(sys.argv[1])
db_path = sys.argv[2]
event_ref = str(sys.argv[3] or "").strip()
require_git_sync = sys.argv[4].strip().lower() in {"1", "true", "yes", "y", "on"}
require_content_success = sys.argv[5].strip().lower() in {"1", "true", "yes", "y", "on"}
allow_test_url_skip = sys.argv[6].strip().lower() in {"1", "true", "yes", "y", "on"}
min_content_chars = max(1, int(sys.argv[7]))


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


def parse_summary(result_json_raw):
    if not result_json_raw:
        return {}
    try:
        payload = json.loads(result_json_raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    details = payload.get("details")
    if not isinstance(details, dict):
        return {}
    summary = details.get("summary")
    return summary if isinstance(summary, dict) else {}


rows = []
for line in run_log.read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        row = json.loads(line)
    except Exception:
        continue
    ev = str(row.get("event_ref") or "")
    if not ev or ev.startswith("smoke-"):
        continue
    rows.append(row)

if not rows:
    print("FAIL:no_non_smoke_event")
    sys.exit(1)

if not event_ref:
    event_ref = str(rows[-1].get("event_ref") or "").strip()
    if not event_ref:
        print("FAIL:latest_event_ref_missing")
        sys.exit(1)

run_row = None
for row in reversed(rows):
    if str(row.get("event_ref") or "").strip() == event_ref:
        run_row = row
        break

if run_row is None:
    print(f"FAIL:event_ref_not_found_in_run_log:{event_ref}")
    sys.exit(1)

if require_git_sync:
    git_sync_status = str(run_row.get("git_sync_status") or "")
    git_sync_commit = str(run_row.get("git_sync_commit") or "")
    if git_sync_status != "success":
        print(f"FAIL:git_sync_status:{git_sync_status or 'missing'}")
        sys.exit(1)
    if not git_sync_commit:
        print("FAIL:git_sync_commit_missing")
        sys.exit(1)

prefix = f"{event_ref}#%"
conn = sqlite3.connect(db_path)
try:
    db_rows = list(
        conn.execute(
            """
            select event_ref, mode, status, result_json, updated_at,
                   content_status, content_chars, is_test_url, quality_reason
            from requests
            where event_ref like ?
            order by updated_at desc
            """,
            (prefix,),
        )
    )
finally:
    conn.close()

if not db_rows:
    print(f"FAIL:no_db_rows:{prefix}")
    sys.exit(1)

link_rows = [r for r in db_rows if str(r[1] or "") in {"link", "mixed"}]
if not link_rows:
    print("FAIL:no_link_or_mixed_rows")
    sys.exit(1)

latest = link_rows[0]
ev, mode, status, result_json_raw, updated_at, c_status, c_chars, is_test_url, quality_reason = latest
status = str(status or "")
if status not in {"success", "partial"}:
    print(f"FAIL:db_status:{status or 'missing'}")
    sys.exit(1)

summary = parse_summary(result_json_raw)
link_total = safe_int(summary.get("link_total"), 0)
route_ok = max(
    safe_int(summary.get("link_success"), 0),
    safe_int(summary.get("link_route_success_count"), 0),
)
content_ok = max(
    safe_int(summary.get("link_doc_saved_count"), 0),
    safe_int(summary.get("link_content_success_count"), 0),
)

c_status = str(c_status or "")
if not c_status:
    c_status = str(summary.get("link_content_status") or "")
if not c_status:
    if link_total <= 0:
        c_status = "none"
    elif safe_int(summary.get("link_content_failed_count"), 0) > 0:
        c_status = "failed"
    elif content_ok > 0:
        c_status = "success"
    elif safe_int(summary.get("link_content_skipped_test_count"), 0) > 0:
        c_status = "skipped_test"
    else:
        c_status = "none"

c_chars = safe_int(c_chars, 0)
if c_chars <= 0:
    c_chars = safe_int(summary.get("link_content_chars_total"), 0)

is_test = bool(is_test_url)
if not is_test:
    is_test = bool(summary.get("link_is_test"))

if require_content_success:
    if allow_test_url_skip and c_status == "skipped_test":
        pass
    elif c_status != "success":
        print(f"FAIL:content_status:{c_status or 'missing'}")
        print(f"DB_STATUS:{status}")
        print(f"SUMMARY:link_total={link_total},route_ok={route_ok},content_ok={content_ok},content_chars={c_chars}")
        if quality_reason:
            print(f"QUALITY:{quality_reason}")
        sys.exit(1)
    elif (not is_test) and c_chars < min_content_chars:
        print(f"FAIL:content_chars:{c_chars}<{min_content_chars}")
        print(f"DB_STATUS:{status}")
        print(f"SUMMARY:link_total={link_total},route_ok={route_ok},content_ok={content_ok}")
        sys.exit(1)

if link_total < 1:
    print("FAIL:link_total<1")
    sys.exit(1)
if route_ok < 1:
    print("FAIL:route_ok<1")
    sys.exit(1)
if require_content_success and (not is_test) and content_ok < 1:
    print("FAIL:content_ok<1")
    sys.exit(1)

print("PASS")
print(f"event_ref={event_ref}")
print(f"db_event_ref={ev}")
print(f"mode={mode}")
print(f"status={status}")
print(f"updated_at={updated_at}")
print(f"link_total={link_total}")
print(f"route_ok={route_ok}")
print(f"content_ok={content_ok}")
print(f"content_status={c_status}")
print(f"content_chars={c_chars}")
print(f"is_test={is_test}")
print(f"quality_reason={quality_reason or ''}")
PY
then
  cat "$TMP_OUT" >&2 || true
  echo "[verify-event] FAIL: event validation failed" >&2
  exit 1
fi

if [[ -n "$KEYWORD" ]]; then
  if ! grep -R -n --include="*.md" -- "$KEYWORD" "$REPO_PATH"/03-* "$REPO_PATH"/01-* >/dev/null 2>&1; then
    echo "[verify-event] FAIL: keyword not found under 01-*/03-* : $KEYWORD" >&2
    exit 1
  fi
fi

echo "[verify-event] PASS"
cat "$TMP_OUT"
