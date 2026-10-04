#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"

if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: service configuration is not initialized." >&2
  echo "Run ./setup.sh from $SERVICE_DIR" >&2
  exit 1
fi

# Preserve one-shot TTS/Wavhost overrides across the .env load. This keeps
# commands such as TTS_ACTIVE_BACKEND=kokoro ./start.sh and
# TTS_QWENTTS_CLAMP_FP16=0 ./start.sh useful without editing persistent config.
declare -A inherited_overrides=()
while IFS='=' read -r key value; do
  case "$key" in
    TTS_*|WAVHOST_*) inherited_overrides["$key"]="$value" ;;
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
  qwentts|wavhost|kokoro) ;;
  *)
    echo "ERROR: TTS_ACTIVE_BACKEND must be qwentts, wavhost, or kokoro; got '$backend'." >&2
    exit 2
    ;;
esac

# Operational mode is intentionally exclusive. Direct start-*.sh commands are
# still available for side-by-side benchmark experiments.
for other in qwentts wavhost kokoro; do
  if [[ "$other" != "$backend" ]]; then
    "$SERVICE_DIR/compose.sh" stop "$other" >/dev/null 2>&1 || true
  fi
done

echo "Starting active TTS backend: $backend"
case "$backend" in
  qwentts) exec "$SERVICE_DIR/start-qwentts.sh" ;;
  wavhost) exec "$SERVICE_DIR/start-wavhost.sh" ;;
  kokoro) exec "$SERVICE_DIR/start-kokoro.sh" ;;
esac
