#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"

if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: service configuration is not initialized." >&2
  echo "Run ./setup.sh from $SERVICE_DIR" >&2
  exit 1
fi

# Preserve explicit one-shot TTS overrides across the .env load. This keeps
# commands such as TTS_ACTIVE_BACKEND=kokoro-cpu ./start.sh and one-off qwentts
# model selections useful without editing persistent configuration.
declare -A inherited_overrides=()
while IFS='=' read -r key value; do
  case "$key" in
    TTS_*) inherited_overrides["$key"]="$value" ;;
  esac
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

backend="${TTS_ACTIVE_BACKEND:-qwentts}"
case "$backend" in
  qwentts|kokoro-gpu|kokoro-cpu) ;;
  *)
    echo "ERROR: TTS_ACTIVE_BACKEND must be qwentts, kokoro-gpu, or kokoro-cpu; got '$backend'." >&2
    exit 2
    ;;
esac

# Normal operation is exclusive. Direct start-* scripts remain available when
# intentionally comparing the two Kokoro variants side-by-side.
case "$backend" in
  qwentts)
    "$SERVICE_DIR/compose.sh" stop kokoro-gpu kokoro-cpu >/dev/null 2>&1 || true
    ;;
  kokoro-gpu)
    "$SERVICE_DIR/compose.sh" stop qwentts-gateway qwentts kokoro-cpu >/dev/null 2>&1 || true
    ;;
  kokoro-cpu)
    "$SERVICE_DIR/compose.sh" stop qwentts-gateway qwentts kokoro-gpu >/dev/null 2>&1 || true
    ;;
esac

echo "Starting active TTS backend: $backend"
case "$backend" in
  qwentts) exec "$SERVICE_DIR/start-qwentts.sh" ;;
  kokoro-gpu) exec "$SERVICE_DIR/start-kokoro-gpu.sh" ;;
  kokoro-cpu) exec "$SERVICE_DIR/start-kokoro-cpu.sh" ;;
esac
