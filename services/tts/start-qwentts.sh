#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: service configuration is not initialized." >&2
  echo "Run ./setup.sh from $SERVICE_DIR" >&2
  exit 1
fi
uv run "$ROOT_DIR/scripts/local_ai.py" model check qwen-0.6-customvoice-q8-gguf --service-dir "$SERVICE_DIR"
set -a
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
# shellcheck disable=SC1091
source "$SERVICE_DIR/.env"
set +a
cd "$SERVICE_DIR"
docker compose config --quiet
docker compose pull qwentts
exec docker compose up -d --no-build --pull never qwentts
