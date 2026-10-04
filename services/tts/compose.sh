#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"

if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: service configuration is not initialized." >&2
  echo "Run ./setup.sh from $SERVICE_DIR" >&2
  exit 1
fi

# Compose normally reads only services/tts/.env when invoked from this
# directory. The machine-wide storage/network policy lives in ../../.env, so a
# raw `docker compose ...` can silently fall back to compose.yaml defaults when
# recreating a container. Always supply both files explicitly. Environment
# variables exported by the caller still have normal Compose precedence, which
# makes one-off experiment overrides safe and reproducible.
exec docker compose \
  --env-file "$ROOT_DIR/.env" \
  --env-file "$SERVICE_DIR/.env" \
  -f "$SERVICE_DIR/compose.yaml" \
  --project-directory "$SERVICE_DIR" \
  "$@"
