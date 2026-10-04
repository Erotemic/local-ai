#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"

if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: service configuration is not initialized." >&2
  echo "Run ./setup.sh from $SERVICE_DIR" >&2
  exit 1
fi

# Do not duplicate manifest-default resolution in shell/Compose. Delegate to the
# same helper used by setup/start so blank TTS_DATA_ROOT and other derived values
# resolve identically for every lifecycle path. Caller environment overrides are
# preserved by local_ai.py's host_command_env().
exec uv run "$ROOT_DIR/scripts/local_ai.py" compose \
  --service-dir "$SERVICE_DIR" \
  -- "$@"
