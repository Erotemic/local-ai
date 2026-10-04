#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"

if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: run ./setup.sh first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
# shellcheck disable=SC1091
source "$SERVICE_DIR/.env"
set +a

backend="${TTS_ACTIVE_BACKEND:-qwentts}"
bind="${TTS_BIND_ADDRESS:-127.0.0.1}"
case "$backend" in
  qwentts) port="${TTS_QWENTTS_PORT:-11436}"; container="ai-voice-qwentts" ;;
  wavhost) port="${TTS_WAVHOST_PORT:-11435}"; container="ai-voice-wavhost" ;;
  kokoro) port="${TTS_KOKORO_PORT:-8880}"; container="ai-voice-kokoro" ;;
  *) echo "ERROR: invalid TTS_ACTIVE_BACKEND=$backend" >&2; exit 2 ;;
esac

local_host="$bind"
case "$local_host" in 0.0.0.0|::|"[::]") local_host="127.0.0.1" ;; esac
local_url="http://${local_host}:${port}"

echo "active_backend=$backend"
echo "bind=$bind"
echo "local_url=$local_url"

if [[ "$bind" == "0.0.0.0" || "$bind" == "::" || "$bind" == "[::]" ]]; then
  lan_ip="$(hostname -I 2>/dev/null | tr ' ' '\n' | awk '/^[0-9]+\./ && $0 !~ /^127\./ {print; exit}')"
  if [[ -n "$lan_ip" ]]; then
    echo "lan_url=http://${lan_ip}:${port}"
  else
    echo "lan_url=http://<server-lan-ip>:${port}"
  fi
fi

state="$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || true)"
echo "container=$container"
echo "state=${state:-missing}"

if curl -fsS --max-time 2 "$local_url/health" >/dev/null 2>&1; then
  echo "health=ok"
  echo "models:"
  curl -fsS --max-time 5 "$local_url/v1/models" 2>/dev/null || true
  echo
else
  echo "health=unavailable"
fi
