#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
runs="${1:-3}"
gpu="${2:-0}"
voice="${3:-crall-math-dk}"
if ! [[ "$runs" =~ ^[1-9][0-9]*$ && "$gpu" =~ ^[0-9]+$ && "$voice" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "usage: $0 [runs] [physical-gpu-index] [voice]" >&2; exit 2
fi
[[ -f "$ROOT_DIR/.env" && -f "$SERVICE_DIR/.env" ]] || { echo "ERROR: run ./setup.sh first." >&2; exit 1; }
[[ -s "$SERVICE_DIR/voice-references/$voice.wav" && -s "$SERVICE_DIR/voice-references/$voice.txt" ]] || { echo "ERROR: voice WAV and transcript are required for $voice." >&2; exit 1; }
command -v nvidia-smi >/dev/null || { echo "ERROR: nvidia-smi is required." >&2; exit 1; }
gpu_name="$(nvidia-smi --id="$gpu" --query-gpu=name --format=csv,noheader)"
[[ "$gpu_name" == *"RTX 3090"* ]] || { echo "ERROR: GPU $gpu is '$gpu_name', not an RTX 3090." >&2; exit 1; }
set -a
source "$ROOT_DIR/.env"
source "$SERVICE_DIR/.env"
set +a
export TTS_BREEZE_GPU="$gpu" TTS_BREEZE_FAST=1 TTS_BENCH_CANDIDATE_VOICE="$voice"
export TTS_BREEZE_START_TIMEOUT="${TTS_BREEZE_FAST_START_TIMEOUT:-1800}"
data_root="${TTS_DATA_ROOT:-${LOCAL_AI_SERVICE_ROOT:-/data/services/local-ai}/tts}"
output_root="${TTS_BENCH_OUTPUT_DIR:-$data_root/benchmarks}"
mkdir -p "$output_root"
session_dir="$(mktemp -d "$output_root/$(date -u +%Y%m%dT%H%M%SZ)-breeze-fast-3090-XXXXXX")"
export TTS_BENCH_OUTPUT_DIR="$session_dir"
exec > >(tee "$session_dir/session.log") 2>&1
echo "GPU=$gpu ($gpu_name) voice=$voice runs=$runs mode=fast-all"
echo "artifacts=$session_dir"
# Fish must release its memory before the graph warmup.
"$SERVICE_DIR/compose.sh" stop breeze-tts2 fish-s2-pro
cleanup() { "$SERVICE_DIR/compose.sh" stop breeze-tts2 || true; }
trap cleanup EXIT
start_epoch="$(python3 -c 'import time; print(time.monotonic())')"
"$SERVICE_DIR/start-candidate.sh" breeze-tts2
startup_seconds="$(python3 - "$start_epoch" <<'PY'
import sys, time
print(f'{time.monotonic() - float(sys.argv[1]):.3f}')
PY
)"
TTS_BENCH_STARTUP_SECONDS="$startup_seconds" "$SERVICE_DIR/scripts/benchmark.sh" breeze-tts2 "$runs"
echo "Benchmark complete: $session_dir"
