#!/usr/bin/env bash
set -euo pipefail

PLUGIN_URL="${PLUGIN_URL:-https://sf3-cn.feishucdn.com/obj/open-platform-opendoc/195a94cb3d9a45d862d417313ff62c9c_gfW8JbxtTd.tgz}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmjs.org}"
RUN_ONBOARD="false"
RUN_GATEWAY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-onboard) RUN_ONBOARD="true"; shift ;;
    --run-gateway) RUN_GATEWAY="true"; shift ;;
    --plugin-url) PLUGIN_URL="${2:?missing value for --plugin-url}"; shift 2 ;;
    --npm-registry) NPM_REGISTRY="${2:?missing value for --npm-registry}"; shift 2 ;;
    *)
      echo "[feishu-plugin] unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[feishu-plugin] missing command: $1" >&2
    exit 1
  }
}

require_cmd curl
require_cmd npm

TMP_TGZ="$(mktemp /tmp/feishu-openclaw-plugin-onboard-cli.XXXXXX.tgz)"
cleanup() {
  rm -f "$TMP_TGZ"
}
trap cleanup EXIT

echo "[feishu-plugin] npm_registry=$NPM_REGISTRY"
npm config set registry "$NPM_REGISTRY" >/dev/null

echo "[feishu-plugin] download=$PLUGIN_URL"
curl -fsSL -o "$TMP_TGZ" "$PLUGIN_URL"

echo "[feishu-plugin] installing global npm package"
npm install -g "$TMP_TGZ"

if ! command -v feishu-plugin-onboard >/dev/null 2>&1; then
  echo "[feishu-plugin] feishu-plugin-onboard command not found after install" >&2
  exit 1
fi

echo "[feishu-plugin] installed command=$(command -v feishu-plugin-onboard)"

if ! command -v openclaw >/dev/null 2>&1; then
  echo "[feishu-plugin] WARN openclaw command not found in PATH" >&2
  echo "[feishu-plugin] install succeeded, but onboarding must run on the host that provides 'openclaw gateway run'" >&2
fi

if [[ "$RUN_ONBOARD" == "true" ]]; then
  echo "[feishu-plugin] starting interactive onboarding"
  feishu-plugin-onboard install
else
  echo "[feishu-plugin] next: feishu-plugin-onboard install"
fi

if [[ "$RUN_GATEWAY" == "true" ]]; then
  if ! command -v openclaw >/dev/null 2>&1; then
    echo "[feishu-plugin] cannot run gateway because openclaw is unavailable" >&2
    exit 1
  fi
  echo "[feishu-plugin] starting gateway"
  openclaw gateway run
else
  echo "[feishu-plugin] next: openclaw gateway run"
fi
