#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: configuration missing; run ./setup.sh first." >&2
  exit 1
fi
declare -A overrides=()
while IFS='=' read -r key value; do
  case "$key" in TTS_*) overrides["$key"]="$value" ;; esac
done < <(env)
set -a
source "$ROOT_DIR/.env"
source "$SERVICE_DIR/.env"
set +a
for key in "${!overrides[@]}"; do export "$key=${overrides[$key]}"; done
case "${TTS_ACTIVE_BACKEND:-qwentts}" in
  qwentts) port="${TTS_QWENTTS_GATEWAY_PORT:-11437}" ;;
  kokoro-gpu) port="${TTS_KOKORO_GPU_PORT:-8880}" ;;
  kokoro-cpu) port="${TTS_KOKORO_CPU_PORT:-8881}" ;;
  indextts25) port="${TTS_INDEXTTS25_PORT:-11438}" ;;
  chatterbox-flash) port="${TTS_CHATTERBOX_FLASH_PORT:-11439}" ;;
  chatterbox-nano) port="${TTS_CHATTERBOX_NANO_PORT:-11440}" ;;
  breeze-tts2) port="${TTS_BREEZE_PORT:-11441}" ;;
  fish-s2-pro) port="${TTS_FISH_PORT:-11442}" ;;
  *) echo "ERROR: unsupported TTS_ACTIVE_BACKEND=$TTS_ACTIVE_BACKEND" >&2; exit 2 ;;
esac
host="${TTS_BIND_ADDRESS:-127.0.0.1}"
case "$host" in 0.0.0.0|::|'[::]') host=127.0.0.1 ;; *:*) [[ "$host" == \[*\] ]] || host="[$host]" ;; esac
exec python3 "$SERVICE_DIR/scripts/smoke_test.py" "http://$host:$port" "$@"
