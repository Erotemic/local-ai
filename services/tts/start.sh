#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"

keep_running=false
if [[ ${1:-} == "--keep-running" ]]; then
  keep_running=true
  shift
fi
if (( $# )); then
  echo "Usage: $0 [--keep-running]" >&2
  exit 2
fi

if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: service configuration is not initialized." >&2
  echo "Run ./setup.sh from $SERVICE_DIR" >&2
  exit 1
fi

declare -A inherited_overrides=()
while IFS='=' read -r key value; do
  case "$key" in TTS_*) inherited_overrides["$key"]="$value" ;; esac
done < <(env)
set -a
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
# shellcheck disable=SC1091
source "$SERVICE_DIR/.env"
set +a
for key in "${!inherited_overrides[@]}"; do export "$key=${inherited_overrides[$key]}"; done

backend="${TTS_ACTIVE_BACKEND:-qwentts}"
case "$backend" in
  qwentts|kokoro-gpu|kokoro-cpu|indextts25|chatterbox-flash|chatterbox-nano|breeze-tts2|fish-s2-pro) ;;
  *)
    echo "ERROR: unsupported TTS_ACTIVE_BACKEND=$backend" >&2
    exit 2
    ;;
esac

if ! $keep_running; then
  "$SERVICE_DIR/compose.sh" stop \
    qwentts-gateway qwentts kokoro-gpu kokoro-cpu \
    indextts25 chatterbox-flash chatterbox-nano breeze-tts2 fish-s2-pro >/dev/null 2>&1 || true
fi

echo "Starting active TTS backend: $backend"
case "$backend" in
  qwentts) exec "$SERVICE_DIR/start-qwentts.sh" ;;
  kokoro-gpu) exec "$SERVICE_DIR/start-kokoro-gpu.sh" ;;
  kokoro-cpu) exec "$SERVICE_DIR/start-kokoro-cpu.sh" ;;
  indextts25) exec "$SERVICE_DIR/start-indextts25.sh" ;;
  chatterbox-flash) exec "$SERVICE_DIR/start-chatterbox-flash.sh" ;;
  chatterbox-nano) exec "$SERVICE_DIR/start-chatterbox-nano.sh" ;;
  fish-s2-pro) exec "$SERVICE_DIR/start-candidate.sh" fish-s2-pro ;;
  breeze-tts2) exec "$SERVICE_DIR/start-candidate.sh" breeze-tts2 ;;
esac
