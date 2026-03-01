#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCH_PY="${SCRIPT_DIR}/feishu_kb_orchestrator.py"

trim_spaces() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

load_env_file() {
  local envf="$1"
  [[ -f "$envf" ]] || return 0
  while IFS= read -r raw || [[ -n "$raw" ]]; do
    local line="$raw"
    line="${line%$'\r'}"
    line="${line#$'\ufeff'}"
    [[ -n "$line" ]] || continue
    [[ "${line:0:1}" != "#" ]] || continue
    line="${line#export }"
    [[ "$line" == *=* ]] || continue

    local key="${line%%=*}"
    local value="${line#*=}"
    key="$(trim_spaces "$key")"
    value="$(trim_spaces "$value")"

    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi

    [[ -n "$key" ]] || continue
    export "$key=$value"
  done < "$envf"
}

load_env_file /etc/openclaw/feishu.env
load_env_file "${SCRIPT_DIR}/.env.ingest-writer.local"
load_env_file "${SCRIPT_DIR}/.env.ingest-writer"
load_env_file "${SCRIPT_DIR}/.env.feishu"

exec python3 "$ORCH_PY" "$@"