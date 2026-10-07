#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
backend="${1:-}"

if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: service configuration is not initialized." >&2
  echo "Run ./setup.sh from $SERVICE_DIR" >&2
  exit 1
fi

# Preserve one-shot TTS_* experiment overrides across .env loading.
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
for key in "${!inherited_overrides[@]}"; do
  export "$key=${inherited_overrides[$key]}"
done

case "$backend" in
  indextts25)
    service="indextts25"
    bundle="indextts-2.5"
    container="ai-voice-indextts25"
    port="${TTS_INDEXTTS25_PORT:-11438}"
    timeout_s="${TTS_INDEXTTS25_START_TIMEOUT:-900}"
    ;;
  chatterbox-flash)
    service="chatterbox-flash"
    bundle="chatterbox-flash"
    container="ai-voice-chatterbox-flash"
    port="${TTS_CHATTERBOX_FLASH_PORT:-11439}"
    timeout_s="${TTS_CHATTERBOX_FLASH_START_TIMEOUT:-600}"
    ;;
  chatterbox-nano)
    service="chatterbox-nano"
    bundle="chatterbox-nano"
    container="ai-voice-chatterbox-nano"
    port="${TTS_CHATTERBOX_NANO_PORT:-11440}"
    timeout_s="${TTS_CHATTERBOX_NANO_START_TIMEOUT:-600}"
    ;;
  *)
    echo "usage: $0 [indextts25|chatterbox-flash|chatterbox-nano]" >&2
    exit 2
    ;;
esac

if ! [[ "$timeout_s" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: start timeout must be a positive integer; got '$timeout_s'." >&2
  exit 2
fi

uv run "$ROOT_DIR/scripts/local_ai.py" model check "$bundle" --service-dir "$SERVICE_DIR"
"$SERVICE_DIR/compose.sh" config --quiet
"$SERVICE_DIR/compose.sh" up -d --no-build --pull never "$service"

bind_address="${TTS_BIND_ADDRESS:-127.0.0.1}"
health_host="$bind_address"
case "$health_host" in 0.0.0.0|::|"[::]") health_host="127.0.0.1" ;; esac
health_url="http://${health_host}:${port}/health"

echo "Waiting for $backend to become ready: $health_url (timeout ${timeout_s}s)"
deadline=$((SECONDS + timeout_s))
while (( SECONDS < deadline )); do
  if curl -fsS --max-time 3 "$health_url" >/dev/null 2>&1; then
    echo "$backend ready: http://${health_host}:${port}"
    echo "runtime:"
    curl -fsS --max-time 10 "http://${health_host}:${port}/v1/runtime" || true
    echo
    exit 0
  fi
  state="$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || true)"
  case "$state" in
    running|created) ;;
    *)
      echo "ERROR: $backend did not remain runnable (state=${state:-missing})." >&2
      "$SERVICE_DIR/compose.sh" ps -a "$service" >&2 || true
      "$SERVICE_DIR/compose.sh" logs --tail=240 "$service" >&2 || true
      exit 1
      ;;
  esac
  sleep 3
done

echo "ERROR: $backend did not become healthy within ${timeout_s}s." >&2
"$SERVICE_DIR/compose.sh" ps -a "$service" >&2 || true
"$SERVICE_DIR/compose.sh" logs --tail=240 "$service" >&2 || true
exit 1
