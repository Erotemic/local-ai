#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
exec uv run "$ROOT_DIR/scripts/local_ai.py" doctor --service-dir "$SERVICE_DIR" "$@"
