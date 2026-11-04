#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/start-candidate.sh" breeze-tts2 "$@"
