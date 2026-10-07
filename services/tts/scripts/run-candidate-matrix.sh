#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runs="${1:-3}"
gpu="${2:-}"

if ! [[ "$runs" =~ ^[1-9][0-9]*$ ]]; then echo "usage: $0 [runs] [physical-gpu-index]" >&2; exit 2; fi
if [[ -n "$gpu" ]]; then
  export TTS_INDEXTTS25_GPU="$gpu"
  export TTS_CHATTERBOX_FLASH_GPU="$gpu"
  export TTS_CHATTERBOX_NANO_GPU="$gpu"
fi

backends=(chatterbox-nano chatterbox-flash indextts25)
for backend in "${backends[@]}"; do
  echo "===== $backend ====="
  start_epoch="$(python3 -c 'import time; print(time.time())')"
  "$SERVICE_DIR/start-${backend}.sh"
  startup_seconds="$(python3 - "$start_epoch" <<'PY2'
import sys, time
print(f"{time.time() - float(sys.argv[1]):.3f}")
PY2
  )"
  echo "startup_seconds=$startup_seconds"
  TTS_BENCH_STARTUP_SECONDS="$startup_seconds" \
    "$SERVICE_DIR/scripts/benchmark.sh" "$backend" "$runs"
  "$SERVICE_DIR/compose.sh" stop "$backend" >/dev/null
  echo
done

echo "Candidate matrix complete. Compare retained benchmark directories and complete every LISTEN_REVIEW.md before promotion."
