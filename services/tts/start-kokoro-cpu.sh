#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: service configuration is not initialized." >&2
  echo "Run ./setup.sh from $SERVICE_DIR" >&2
  exit 1
fi
"$SERVICE_DIR/compose.sh" config --quiet
"$SERVICE_DIR/compose.sh" pull kokoro-cpu
exec "$SERVICE_DIR/compose.sh" up -d --no-build --pull never kokoro-cpu
