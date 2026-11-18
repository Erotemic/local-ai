#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
stop_others=false
if [[ "${1:-}" == --stop-other-backends ]]; then stop_others=true; shift; fi
if (( $# )); then echo "Usage: $0 [--stop-other-backends]" >&2; exit 2; fi
if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: run ./breeze.sh setup from $ROOT_DIR first." >&2
  exit 1
fi
# Keep one-shot overrides consistent with the existing candidate launcher.
declare -A inherited_overrides=()
while IFS='=' read -r key value; do
  case "$key" in TTS_*) inherited_overrides["$key"]="$value" ;; esac
done < <(env)
set -a
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
# shellcheck disable=SC1091
source "$SERVICE_DIR/.env"
set +a
for key in "${!inherited_overrides[@]}"; do export "$key=${inherited_overrides[$key]}"; done
first_gpu="${TTS_BREEZE_DUAL_GPU_0:-0}"
second_gpu="${TTS_BREEZE_DUAL_GPU_1:-1}"
for gpu in "$first_gpu" "$second_gpu"; do
  if ! [[ "$gpu" =~ ^[0-9]+$ ]]; then
    echo "ERROR: dual-GPU device indexes must be nonnegative integers." >&2
    exit 2
  fi
done
if (( 10#$first_gpu == 10#$second_gpu )); then
  echo "ERROR: TTS_BREEZE_DUAL_GPU_0 and TTS_BREEZE_DUAL_GPU_1 must name different physical GPUs." >&2
  exit 2
fi
timeout_s="${TTS_BREEZE_DUAL_START_TIMEOUT:-1800}"
if ! [[ "$timeout_s" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: TTS_BREEZE_DUAL_START_TIMEOUT must be a positive integer." >&2
  exit 2
fi
uv run "$ROOT_DIR/scripts/local_ai.py" model check breeze-tts2 --service-dir "$SERVICE_DIR"
"$SERVICE_DIR/compose.sh" config --quiet
# Fail before replacing a working server if setup has not built both images.
for image in local/tts-breeze-tts2:candidate local/tts-breeze-gateway:py313; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "ERROR: image $image is missing. Run ./breeze.sh setup first." >&2
    exit 1
  fi
done
if command -v nvidia-smi >/dev/null; then
  nvidia-smi --id="$first_gpu,$second_gpu" --query-gpu=index,name,memory.free --format=csv || \
    echo "WARNING: GPU diagnostics failed; Docker will validate the requested devices." >&2
fi
echo "Starting two Breeze replicas on physical GPUs $first_gpu and $second_gpu (fast=${TTS_BREEZE_FAST:-0})."
echo "Replacing single-GPU Breeze if running; its unfinished jobs will be lost."
if $stop_others; then
  "$SERVICE_DIR/compose.sh" stop qwentts-gateway qwentts kokoro-gpu kokoro-cpu \
    indextts25 chatterbox-flash chatterbox-nano breeze-tts2 fish-s2-pro
else
  "$SERVICE_DIR/compose.sh" stop breeze-tts2
fi
if ! "$SERVICE_DIR/compose.sh" up -d --no-build --pull never breeze-gateway; then
  echo "ERROR: Breeze startup failed; inspect ./breeze.sh logs." >&2
  exit 1
fi
bind_address="${TTS_BIND_ADDRESS:-127.0.0.1}"
case "$bind_address" in 0.0.0.0|::|"[::]") bind_address=127.0.0.1 ;; esac
if [[ "$bind_address" == *:* && "$bind_address" != \[* ]]; then bind_address="[$bind_address]"; fi
base="http://${bind_address}:${TTS_BREEZE_PORT:-11441}"
deadline=$((SECONDS + timeout_s))
while (( SECONDS < deadline )); do
  if curl -fsS --max-time 10 "$base/health" 2>/dev/null | python3 -c \
    'import json,sys; d=json.load(sys.stdin); sys.exit(0 if len(d.get("workers", [])) == 2 and all(w["ready"] for w in d["workers"]) else 1)' 2>/dev/null; then
    echo "Dual-GPU Breeze ready: $base/v1"
    echo "The client URL, model alias, and voice names are unchanged. Inspect workers with ./breeze.sh status."
    exit 0
  fi
  sleep 3
done
echo "ERROR: both Breeze replicas were not ready within ${timeout_s}s. Inspect ./breeze.sh logs." >&2
"$SERVICE_DIR/compose.sh" ps -a breeze-worker-0 breeze-worker-1 breeze-gateway >&2 || true
exit 1
