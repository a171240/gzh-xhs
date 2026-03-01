#!/usr/bin/env bash
set -euo pipefail

REPO_PATH=""
PYTHON_BIN="${PYTHON_BIN:-python3}"
ALLOW_F2_FAILURE="${ALLOW_F2_FAILURE:-true}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-path) REPO_PATH="${2:?missing value for --repo-path}"; shift 2 ;;
    --python) PYTHON_BIN="${2:?missing value for --python}"; shift 2 ;;
    --allow-f2-failure) ALLOW_F2_FAILURE="${2:?missing value for --allow-f2-failure}"; shift 2 ;;
    *) echo "[extractors] unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$REPO_PATH" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  REPO_PATH="$(cd "$SCRIPT_DIR/../.." && pwd)"
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "[extractors] missing command: $1" >&2; exit 1; }
}

require_cmd "$PYTHON_BIN"

PIP_CMD=("$PYTHON_BIN" -m pip)
if ! "${PIP_CMD[@]}" --version >/dev/null 2>&1; then
  "$PYTHON_BIN" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi
if ! "${PIP_CMD[@]}" --version >/dev/null 2>&1; then
  if command -v pip3 >/dev/null 2>&1; then
    PIP_CMD=(pip3)
  elif command -v pip >/dev/null 2>&1; then
    PIP_CMD=(pip)
  else
    echo "[extractors] pip is unavailable for $PYTHON_BIN" >&2
    exit 1
  fi
fi

LOCK_FILE="$(find "$REPO_PATH" -maxdepth 4 -type f -path '*/scripts/requirements.ingest-link.lock.txt' | head -n1 || true)"
if [[ -z "$LOCK_FILE" ]]; then
  echo "[extractors] lock file missing under $REPO_PATH (expected */scripts/requirements.ingest-link.lock.txt)" >&2
  exit 1
fi

echo "[extractors] repo=$REPO_PATH"
echo "[extractors] python=$PYTHON_BIN"
echo "[extractors] lock=$LOCK_FILE"

while IFS= read -r raw || [[ -n "$raw" ]]; do
  line="$(echo "$raw" | sed 's/\r$//')"
  [[ -n "$line" ]] || continue
  [[ "${line:0:1}" != "#" ]] || continue
  package="$line"
  lower="$(echo "$package" | tr '[:upper:]' '[:lower:]')"

  if [[ "$lower" == f2==* ]] && [[ "$ALLOW_F2_FAILURE" == "true" ]]; then
    if "${PIP_CMD[@]}" install --disable-pip-version-check "$package"; then
      echo "[extractors] installed optional package: $package"
    else
      echo "[extractors] WARN optional package install failed: $package" >&2
    fi
    continue
  fi

  "${PIP_CMD[@]}" install --disable-pip-version-check "$package"
  echo "[extractors] installed: $package"
done < "$LOCK_FILE"

if command -v yt-dlp >/dev/null 2>&1; then
  echo "[extractors] yt-dlp binary: $(yt-dlp --version 2>/dev/null || echo unknown)"
else
  echo "[extractors] WARN yt-dlp command not found in PATH (python module may still be available)" >&2
fi

"$PYTHON_BIN" - <<'PY'
import importlib.util
import pkg_resources

def version_of(name: str) -> str:
    try:
        return pkg_resources.get_distribution(name).version
    except Exception:
        return "not-installed"

print(f"[extractors] python package yt-dlp: {version_of('yt-dlp')}")
print(f"[extractors] python module f2: {'installed' if importlib.util.find_spec('f2') else 'not-installed'}")
PY

echo "[extractors] done"
