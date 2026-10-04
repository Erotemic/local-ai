#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
ROOT_ENV="$ROOT_DIR/.env"
SERVICE_ENV="$SERVICE_DIR/.env"

if [[ ! -f "$ROOT_ENV" || ! -f "$SERVICE_ENV" ]]; then
  echo "ERROR: service configuration is not initialized." >&2
  echo "Run ./setup.sh from $SERVICE_DIR" >&2
  exit 1
fi

# Compose cannot evaluate local-ai's {LOCAL_AI_SERVICE_ROOT}/tts manifest
# default. Resolve the one derived TTS path here before handing control to
# Compose. Explicit caller environment still wins, followed by service .env,
# then the shared root .env.
read_env_value() {
  local path="$1"
  local key="$2"
  sed -n -E "s/^[[:space:]]*(export[[:space:]]+)?${key}=//p" "$path" | tail -n 1
}

if [[ -z "${TTS_DATA_ROOT:-}" ]]; then
  service_data_root="$(read_env_value "$SERVICE_ENV" TTS_DATA_ROOT)"
  if [[ -n "$service_data_root" ]]; then
    export TTS_DATA_ROOT="$service_data_root"
  else
    service_root="${LOCAL_AI_SERVICE_ROOT:-$(read_env_value "$ROOT_ENV" LOCAL_AI_SERVICE_ROOT)}"
    if [[ -z "$service_root" ]]; then
      echo "ERROR: LOCAL_AI_SERVICE_ROOT is not configured." >&2
      exit 1
    fi
    export TTS_DATA_ROOT="${service_root%/}/tts"
  fi
fi

exec docker compose \
  --env-file "$ROOT_ENV" \
  --env-file "$SERVICE_ENV" \
  -f "$SERVICE_DIR/compose.yaml" \
  --project-directory "$SERVICE_DIR" \
  "$@"
