#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export TTS_BREEZE_FAST="${TTS_BREEZE_FAST:-1}"
export TTS_BREEZE_GPU="${TTS_BREEZE_GPU:-0}"
export TTS_BREEZE_START_TIMEOUT="${TTS_BREEZE_START_TIMEOUT:-1800}"
exec "$DIR/start-candidate.sh" breeze-tts2 "$@"
