#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SERVICE_DIR/start-candidate.sh" "chatterbox-nano"
