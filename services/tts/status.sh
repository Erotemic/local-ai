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
  qwentts) port="${TTS_QWENTTS_GATEWAY_PORT:-11437}"; container="ai-voice-qwentts-gateway" ;;
  kokoro-gpu) port="${TTS_KOKORO_GPU_PORT:-8880}"; container="ai-voice-kokoro-gpu" ;;
  kokoro-cpu) port="${TTS_KOKORO_CPU_PORT:-8881}"; container="ai-voice-kokoro-cpu" ;;
  indextts25) port="${TTS_INDEXTTS25_PORT:-11438}"; container="ai-voice-indextts25" ;;
  chatterbox-flash) port="${TTS_CHATTERBOX_FLASH_PORT:-11439}"; container="ai-voice-chatterbox-flash" ;;
  chatterbox-nano) port="${TTS_CHATTERBOX_NANO_PORT:-11440}"; container="ai-voice-chatterbox-nano" ;;
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
  if [[ -n "$lan_ip" ]]; then echo "lan_url=http://${lan_ip}:${port}"; else echo "lan_url=http://<server-lan-ip>:${port}"; fi
fi

state="$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || true)"
echo "container=$container"
echo "state=${state:-missing}"
if [[ "$backend" == "qwentts" ]]; then
  engine_state="$(docker inspect --format '{{.State.Status}}' ai-voice-qwentts 2>/dev/null || true)"
  echo "engine_url=http://127.0.0.1:${TTS_QWENTTS_PORT:-11436}"
  echo "engine_state=${engine_state:-missing}"
fi

if curl -fsS --max-time 2 "$local_url/health" >/dev/null 2>&1; then
  echo "health=ok"
  echo "models:"
  curl -fsS --max-time 5 "$local_url/v1/models" 2>/dev/null || true
  echo
  if [[ "$backend" == "indextts25" || "$backend" == chatterbox-* ]]; then
    echo "runtime:"
    curl -fsS --max-time 10 "$local_url/v1/runtime" 2>/dev/null || true
    echo
  fi
else
  echo "health=unavailable"
fi
