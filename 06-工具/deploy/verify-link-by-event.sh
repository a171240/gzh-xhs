#!/usr/bin/env bash
set -euo pipefail

REPO_PATH="${CLOUD_DEPLOY_PATH:-/root/gzh-xhs}"
SINCE_MINUTES=120
EVENT_REF=""
EVENT_REF_CONTAINS=""
REQUIRE_GIT_SYNC=true
MIN_CONTENT_CHARS=120
REQUIRE_BITABLE_CONSISTENCY=false
REQUIRE_TEXT_SOURCE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-path) REPO_PATH="${2:?missing value for --repo-path}"; shift 2 ;;
    --since-minutes) SINCE_MINUTES="${2:?missing value for --since-minutes}"; shift 2 ;;
    --event-ref) EVENT_REF="${2:?missing value for --event-ref}"; shift 2 ;;
    --event-ref-contains) EVENT_REF_CONTAINS="${2:?missing value for --event-ref-contains}"; shift 2 ;;
    --require-git-sync) REQUIRE_GIT_SYNC="${2:?missing value for --require-git-sync}"; shift 2 ;;
    --min-content-chars) MIN_CONTENT_CHARS="${2:?missing value for --min-content-chars}"; shift 2 ;;
    --require-bitable-consistency) REQUIRE_BITABLE_CONSISTENCY="${2:?missing value for --require-bitable-consistency}"; shift 2 ;;
    --require-text-source) REQUIRE_TEXT_SOURCE="${2:?missing value for --require-text-source}"; shift 2 ;;
    *) echo "[verify-event] unknown argument: $1" >&2; exit 2 ;;
  esac
done

as_bool() {
  local raw="${1:-}"
  raw="$(echo "$raw" | tr '[:upper:]' '[:lower:]')"
  [[ "$raw" == "1" || "$raw" == "true" || "$raw" == "yes" || "$raw" == "y" || "$raw" == "on" ]]
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "[verify-event] missing command: $1" >&2; exit 1; }
}

require_cmd python3
require_cmd grep

echo "[verify-event] repo=$REPO_PATH"

RUN_LOG="$(ls -1 "$REPO_PATH"/06-*/data/feishu-orchestrator/runs/"$(date +%F).jsonl" 2>/dev/null | head -n1 || true)"
test -n "$RUN_LOG" || { echo "[verify-event] FAIL: run log missing"; exit 1; }
DB_PATH="$(ls -1 "$REPO_PATH"/06-*/data/ingest-writer/writer_state.db 2>/dev/null | head -n1 || true)"
test -n "$DB_PATH" || { echo "[verify-event] FAIL: writer db missing"; exit 1; }

META_TMP="$(mktemp)"
trap 'rm -f "$META_TMP"' EXIT

python3 - "$RUN_LOG" "$SINCE_MINUTES" "$EVENT_REF" "$EVENT_REF_CONTAINS" "$REQUIRE_GIT_SYNC" >"$META_TMP" <<'PY'
import datetime as dt
import json
import pathlib
import sys

run_log = pathlib.Path(sys.argv[1])
since_minutes = int(sys.argv[2])
event_ref_exact = str(sys.argv[3] or "").strip()
event_ref_contains = str(sys.argv[4] or "").strip()
require_git_sync = str(sys.argv[5] or "").strip().lower() in {"1", "true", "yes", "y", "on"}
cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=max(1, since_minutes))

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
    if not event_ref or event_ref.startswith("smoke-"):
        continue
    ts = parse_ts(row.get("ts"))
    if ts is None:
        continue
    if event_ref_exact and event_ref != event_ref_exact:
        continue
    if (not event_ref_exact) and event_ref_contains and event_ref_contains not in event_ref:
        continue
    if (not event_ref_exact) and ts < cutoff:
        continue
    rows.append((ts, row))

if not rows:
    print("FAIL:no_event")
    sys.exit(1)

rows.sort(key=lambda x: x[0], reverse=True)
ts, row = rows[0]
status = str(row.get("status") or "")
git_sync_status = str(row.get("git_sync_status") or "")
if status not in {"success", "partial"}:
    print(f"FAIL:run_status:{status or 'missing'}")
    sys.exit(1)
if require_git_sync:
    if not git_sync_status:
        print("FAIL:git_sync_status:missing")
        sys.exit(1)
    if git_sync_status == "error":
        print("FAIL:git_sync_status:error")
        sys.exit(1)

print("OK")
print(f"event_ref={row.get('event_ref')}")
print(f"run_status={status}")
print(f"git_sync_status={git_sync_status}")
print(f"git_sync_commit={str(row.get('git_sync_commit') or '')}")
print(f"link_route_status={str(row.get('link_route_status') or '')}")
print(f"link_content_status={str(row.get('link_content_status') or '')}")
print(f"link_content_chars={int(row.get('link_content_chars') or 0)}")
print(f"link_provider={str(row.get('link_provider') or '')}")
print(f"link_is_test={bool(row.get('link_is_test'))}")
print(f"link_quality_reason={str(row.get('link_quality_reason') or '')}")
print(f"link_summary_detected={bool(row.get('link_summary_detected'))}")
print(f"link_text_source={str(row.get('link_text_source') or '')}")
print(f"link_reject_reason={str(row.get('link_reject_reason') or '')}")
PY

if ! grep -q '^OK$' "$META_TMP"; then
  cat "$META_TMP" >&2
  echo "[verify-event] FAIL: run log validation failed" >&2
  exit 1
fi

EVENT_REF_PICKED="$(grep '^event_ref=' "$META_TMP" | head -n1 | cut -d= -f2-)"
test -n "$EVENT_REF_PICKED" || { echo "[verify-event] FAIL: missing event_ref in metadata"; exit 1; }
PREFIX="${EVENT_REF_PICKED}#%"

python3 - "$DB_PATH" "$PREFIX" "$MIN_CONTENT_CHARS" "$REQUIRE_TEXT_SOURCE" <<'PY'
import json
import sqlite3
import sys

db_path = sys.argv[1]
prefix = sys.argv[2]
min_chars = max(1, int(sys.argv[3]))
required_text_source = str(sys.argv[4] or "").strip().lower()

conn = sqlite3.connect(db_path)
try:
    cur = conn.cursor()
    rows = list(
        cur.execute(
            "select event_ref,mode,status,request_json,result_json,"
            "coalesce(content_status,''),coalesce(content_chars,0),"
            "coalesce(summary_detected,0),coalesce(text_source,''),coalesce(reject_reason,''),"
            "coalesce(quality_reason,''),coalesce(provider,''),coalesce(is_test_url,0),updated_at "
            "from requests where event_ref like ? order by updated_at desc",
            (prefix,),
        )
    )
    if not rows:
        print("FAIL:db:no_row")
        sys.exit(1)
    # Prefer link mode row.
    chosen = next((r for r in rows if str(r[1]) == "link"), rows[0])
    event_ref, mode, status, request_json, result_json, content_status, content_chars, summary_detected, text_source, reject_reason, quality_reason, provider, is_test_url, updated_at = chosen
    content_chars = int(content_chars or 0)
    is_test_url = bool(is_test_url)
    summary_detected = bool(summary_detected)

    print(f"DB_EVENT_REF={event_ref}")
    print(f"DB_STATUS={status}")
    print(f"DB_MODE={mode}")
    print(f"DB_CONTENT_STATUS={content_status}")
    print(f"DB_CONTENT_CHARS={content_chars}")
    print(f"DB_SUMMARY_DETECTED={summary_detected}")
    print(f"DB_TEXT_SOURCE={text_source}")
    print(f"DB_REJECT_REASON={reject_reason}")
    print(f"DB_QUALITY_REASON={quality_reason}")
    print(f"DB_PROVIDER={provider}")
    print(f"DB_UPDATED_AT={updated_at}")

    if str(status) != "success":
        print(f"FAIL:db_status:{status}")
        sys.exit(1)
    if str(content_status) != "success":
        print(f"FAIL:content_status:{content_status or 'missing'}")
        sys.exit(1)
    if required_text_source:
        actual_text_source = str(text_source or "").strip().lower()
        accepted_sources = {required_text_source}
        if required_text_source == "bitable":
            accepted_sources.add("bitable_text")
        if actual_text_source not in accepted_sources:
            print(f"FAIL:text_source:{text_source or 'missing'}!={required_text_source}")
            sys.exit(1)
    if summary_detected:
        print("FAIL:summary_detected:true")
        sys.exit(1)
    if (not is_test_url) and content_chars < min_chars:
        print(f"FAIL:content_chars:{content_chars}<{min_chars}")
        sys.exit(1)
finally:
    conn.close()
PY

if as_bool "$REQUIRE_BITABLE_CONSISTENCY"; then
  python3 - "$DB_PATH" "$PREFIX" <<'PY'
import json
import os
import re
import sqlite3
import sys
import time
from urllib.parse import urlparse
import requests

db_path = sys.argv[1]
prefix = sys.argv[2]

app_id = str(os.getenv("FEISHU_APP_ID") or "").strip()
app_secret = str(os.getenv("FEISHU_APP_SECRET") or "").strip()
app_token = str(os.getenv("BITABLE_APP_TOKEN") or os.getenv("FEISHU_BITABLE_APP_TOKEN") or "").strip()
table_id = str(os.getenv("BITABLE_TABLE_ID") or os.getenv("FEISHU_BITABLE_TABLE_ID") or "").strip()
view_id = str(os.getenv("BITABLE_VIEW_ID") or os.getenv("FEISHU_BITABLE_VIEW_ID") or "").strip()
base = str(os.getenv("FEISHU_OPEN_BASE_URL") or "https://open.feishu.cn").strip().rstrip("/")
video_id_field = str(os.getenv("BITABLE_VIDEO_ID_FIELD") or "\u89c6\u9891ID").strip()
link_field = str(os.getenv("BITABLE_LINK_FIELD") or "\u89c6\u9891\u94fe\u63a5").strip()
text_field = str(os.getenv("BITABLE_TEXT_FIELD") or "AI \u97f3\u89c6\u9891\u6458\u8981 & \u6587\u6848\u63d0\u53d6.\u6587\u6848").strip()
backup_field = str(os.getenv("BITABLE_TEXT_FALLBACK_FIELD") or "\u6574\u7406\u597d\u5185\u5bb9").strip()

if not (app_id and app_secret and app_token and table_id):
    print("FAIL:bitable_env_missing")
    sys.exit(1)

conn = sqlite3.connect(db_path)
try:
    row = conn.execute(
        "select request_json from requests where event_ref like ? and mode='link' order by updated_at desc limit 1",
        (prefix,),
    ).fetchone()
    if not row:
        print("FAIL:bitable_link_request_missing")
        sys.exit(1)
    req = json.loads(str(row[0] or "{}"))
    url = str(req.get("url") or "").strip()
    if not url:
        print("FAIL:bitable_url_missing")
        sys.exit(1)
finally:
    conn.close()

resp = requests.post(
    f"{base}/open-apis/auth/v3/tenant_access_token/internal",
    json={"app_id": app_id, "app_secret": app_secret},
    timeout=20,
)
resp.raise_for_status()
data = resp.json()
if int(data.get("code") or 0) != 0:
    print(f"FAIL:bitable_auth:{data.get('msg') or data}")
    sys.exit(1)
token = str(data.get("tenant_access_token") or "").strip()

video_id = ""
m = re.search(r"/video/(\\d{8,32})", url)
if m:
    video_id = str(m.group(1))

short_url = url.split('?')[0]
payload = {}
if view_id:
    payload["view_id"] = view_id

def cell_to_text(v):
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        out = []
        for x in v:
            if isinstance(x, dict) and isinstance(x.get("text"), str):
                out.append(x.get("text").strip())
            elif isinstance(x, str):
                out.append(x.strip())
        return "\\n".join([x for x in out if x]).strip()
    if isinstance(v, dict):
        return str(v.get("text") or v.get("name") or "").strip()
    return ""

items = []
page_token = ""
for _ in range(5):
    endpoint = f"{base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search?page_size=200"
    if page_token:
        endpoint = f"{endpoint}&page_token={page_token}"
    r = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        json=payload,
        timeout=20,
    )
    r.raise_for_status()
    j = r.json()
    if int(j.get("code") or 0) != 0:
        print(f"FAIL:bitable_search:{j.get('msg') or j}")
        sys.exit(1)
    data_block = j.get("data") or {}
    batch = data_block.get("items") or []
    if isinstance(batch, list):
        items.extend(batch)
    if not data_block.get("has_more"):
        break
    page_token = str(data_block.get("page_token") or "").strip()
    if not page_token:
        break

if not items:
    print("FAIL:bitable_no_record")
    sys.exit(1)

matched = False
for item in items:
    fields = item.get("fields") or {}
    id_text = cell_to_text(fields.get(video_id_field))
    link_text = cell_to_text(fields.get(link_field))
    is_match = False
    if video_id and (video_id in id_text or video_id in link_text):
        is_match = True
    if (not is_match) and short_url and short_url in link_text:
        is_match = True
    if not is_match:
        continue
    text = cell_to_text(fields.get(text_field)) or cell_to_text(fields.get(backup_field))
    if text:
        matched = True
        break

if not matched:
    print("FAIL:bitable_text_empty_or_not_matched")
    sys.exit(1)
print("BITABLE_OK=true")
PY
fi

echo "[verify-event] PASS"
cat "$META_TMP"
