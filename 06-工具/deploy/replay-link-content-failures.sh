#!/usr/bin/env bash
set -euo pipefail

REPO_PATH="${CLOUD_DEPLOY_PATH:-/root/gzh-xhs}"
DATE_STR="$(date +%F)"
SINCE_MINUTES=0
EVENT_REF_CONTAINS=""
MAX_EVENTS=100
DRY_RUN=false
WRITER_BASE_URL="${INGEST_WRITER_BASE_URL:-http://127.0.0.1:8790}"
TOKEN="${INGEST_SHARED_TOKEN:-}"
SECRET="${INGEST_HMAC_SECRET:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-path) REPO_PATH="${2:?missing value for --repo-path}"; shift 2 ;;
    --date) DATE_STR="${2:?missing value for --date}"; shift 2 ;;
    --since-minutes) SINCE_MINUTES="${2:?missing value for --since-minutes}"; shift 2 ;;
    --event-ref-contains) EVENT_REF_CONTAINS="${2:?missing value for --event-ref-contains}"; shift 2 ;;
    --max-events) MAX_EVENTS="${2:?missing value for --max-events}"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --writer-base-url) WRITER_BASE_URL="${2:?missing value for --writer-base-url}"; shift 2 ;;
    --token) TOKEN="${2:?missing value for --token}"; shift 2 ;;
    --secret) SECRET="${2:?missing value for --secret}"; shift 2 ;;
    *) echo "[replay] unknown argument: $1" >&2; exit 2 ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "[replay] missing command: $1" >&2; exit 1; }
}

require_cmd python3

RUN_LOG="$(ls -1 "$REPO_PATH"/06-*/data/feishu-orchestrator/runs/"${DATE_STR}.jsonl" 2>/dev/null | head -n1 || true)"
if [[ -z "$RUN_LOG" || ! -f "$RUN_LOG" ]]; then
  echo "[replay] run log missing: date=$DATE_STR repo=$REPO_PATH" >&2
  exit 1
fi

REPLAYER="$(ls -1 "$REPO_PATH"/06-*/scripts/openclaw_backfill_replay.py 2>/dev/null | head -n1 || true)"
if [[ -z "$REPLAYER" || ! -f "$REPLAYER" ]]; then
  echo "[replay] replayer script missing under $REPO_PATH/06-*/scripts/openclaw_backfill_replay.py" >&2
  exit 1
fi

TMP_INPUT="$(mktemp)"
trap 'rm -f "$TMP_INPUT"' EXIT

python3 - "$RUN_LOG" "$SINCE_MINUTES" "$EVENT_REF_CONTAINS" "$MAX_EVENTS" >"$TMP_INPUT" <<'PY'
import datetime as dt
import json
import pathlib
import sys

run_log = pathlib.Path(sys.argv[1])
since_minutes = max(0, int(sys.argv[2]))
event_ref_contains = str(sys.argv[3] or "").strip()
max_events = max(1, int(sys.argv[4]))

rows: list[dict] = []
cutoff = None
if since_minutes > 0:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=since_minutes)

for line in run_log.read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        row = json.loads(line)
    except Exception:
        continue
    event_ref = str(row.get("event_ref") or "").strip()
    if not event_ref or event_ref.startswith("smoke-"):
        continue
    if event_ref_contains and event_ref_contains not in event_ref:
        continue

    ts_raw = str(row.get("ts") or "")
    ts = None
    try:
        ts = dt.datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except Exception:
        ts = None
    if cutoff is not None and (ts is None or ts < cutoff):
        continue

    intent = row.get("intent") or {}
    if not bool(intent.get("ingest")):
        continue

    trigger = str(intent.get("ingest_trigger") or row.get("ingest_trigger") or "").strip().lower()
    route_status = str(row.get("link_route_status") or "").strip().lower()
    content_status = str(row.get("link_content_status") or "").strip().lower()

    has_link_flow = (
        trigger == "url"
        or route_status in {"success", "partial"}
        or content_status not in {"", "none"}
    )
    if not has_link_flow:
        continue
    if route_status not in {"success", "partial"}:
        continue
    if content_status not in {"failed", "none", ""}:
        continue

    rows.append(
        {
            "event_ref": event_ref,
            "ts": ts_raw,
            "route_status": route_status,
            "content_status": content_status or "none",
            "quality_reason": str(row.get("link_quality_reason") or "").strip(),
        }
    )

rows.sort(key=lambda item: item["ts"], reverse=True)
seen: set[str] = set()
count = 0
for item in rows:
    ev = item["event_ref"]
    if ev in seen:
        continue
    seen.add(ev)
    print(ev)
    count += 1
    if count >= max_events:
        break
PY

if [[ ! -s "$TMP_INPUT" ]]; then
  echo "[replay] no candidate event_ref found (date=$DATE_STR since=${SINCE_MINUTES}m)"
  exit 0
fi

echo "[replay] repo=$REPO_PATH"
echo "[replay] run_log=$RUN_LOG"
echo "[replay] replayer=$REPLAYER"
echo "[replay] candidates=$(wc -l < "$TMP_INPUT" | tr -d ' ')"
echo "[replay] writer_base_url=$WRITER_BASE_URL"
echo "[replay] --- event_refs ---"
cat "$TMP_INPUT"
echo "[replay] ------------------"

CMD=(
  python3 "$REPLAYER"
  --input-file "$TMP_INPUT"
  --writer-base-url "$WRITER_BASE_URL"
)
if [[ -n "$TOKEN" ]]; then
  CMD+=(--token "$TOKEN")
fi
if [[ -n "$SECRET" ]]; then
  CMD+=(--secret "$SECRET")
fi
if [[ "$DRY_RUN" == "true" ]]; then
  CMD+=(--dry-run)
fi

"${CMD[@]}"

