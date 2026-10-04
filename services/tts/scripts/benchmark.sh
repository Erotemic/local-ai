#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"

if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: run ../setup.sh first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
# shellcheck disable=SC1091
source "$SERVICE_DIR/.env"
set +a

backend="${1:-wavhost}"
runs="${2:-2}"
text="${TTS_BENCH_TEXT:-In this lecture, we will examine how a numerical algorithm transforms a sequence of approximations into a stable solution. The important point is not only the final answer, but also the assumptions that make each intermediate step valid. We will keep those assumptions explicit as we proceed.}"

case "$backend" in
  wavhost)
    base_url="http://${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}:${TTS_WAVHOST_PORT:-11435}"
    model="${TTS_BENCH_QWEN_MODEL:-qwen-0.6-customvoice}"
    voice="${TTS_BENCH_QWEN_VOICE:-Ryan}"
    gpu="${TTS_WAVHOST_GPU:-}"
    dtype="${WAVHOST_QWEN_DTYPE:-auto}"
    ;;
  kokoro)
    base_url="http://${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}:${TTS_KOKORO_PORT:-8880}"
    model="${TTS_BENCH_KOKORO_MODEL:-kokoro}"
    voice="${TTS_BENCH_KOKORO_VOICE:-af_heart}"
    gpu="${TTS_KOKORO_GPU:-}"
    dtype="n/a"
    ;;
  *)
    echo "usage: $0 [wavhost|kokoro] [runs]" >&2
    exit 2
    ;;
esac

if ! [[ "$runs" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: runs must be a positive integer; got '$runs'." >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required to construct the JSON request." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required." >&2
  exit 1
fi

data_root="${TTS_DATA_ROOT:-${LOCAL_AI_SERVICE_ROOT:-/data/services/local-ai}/tts}"
output_root="${TTS_BENCH_OUTPUT_DIR:-$data_root/benchmarks}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
slug_model="${model//[^A-Za-z0-9._-]/_}"
slug_voice="${voice//[^A-Za-z0-9._-]/_}"
run_dir="$output_root/${stamp}-${backend}-${slug_model}-${slug_voice}"
mkdir -p "$run_dir"

payload="$(MODEL="$model" VOICE="$voice" TEXT="$text" python3 - <<'PY'
import json
import os
print(json.dumps({
    "model": os.environ["MODEL"],
    "voice": os.environ["VOICE"],
    "input": os.environ["TEXT"],
    "response_format": "wav",
    "speed": 1.0,
    "stream": False,
}))
PY
)"

BACKEND="$backend" BASE_URL="$base_url" MODEL="$model" VOICE="$voice" \
TEXT="$text" RUNS="$runs" GPU="$gpu" DTYPE="$dtype" RUN_DIR="$run_dir" \
python3 - <<'PY' > "$run_dir/metadata.json"
import json
import os

print(json.dumps({
    "backend": os.environ["BACKEND"],
    "base_url": os.environ["BASE_URL"],
    "model": os.environ["MODEL"],
    "voice": os.environ["VOICE"],
    "text": os.environ["TEXT"],
    "runs": int(os.environ["RUNS"]),
    "host_gpu_index": os.environ["GPU"],
    "qwen_dtype": os.environ["DTYPE"],
    "artifact_dir": os.environ["RUN_DIR"],
    "cache_state": "not reset by benchmark.sh",
}, indent=2, sort_keys=True))
PY
printf 'run\tphase\thttp_code\telapsed_s\taudio_s\trtf\tbytes\tfile\n' > "$run_dir/results.tsv"
printf '%s\n' "$text" > "$run_dir/input.txt"
printf '%s\n' "$payload" > "$run_dir/request.json"

echo "backend=$backend url=$base_url model=$model voice=$voice runs=$runs dtype=$dtype gpu=$gpu"
echo "artifacts=$run_dir"
echo "note=benchmark.sh does not reset the server/model cache; phase=first means first request in this invocation, not necessarily a cold model load"

for ((i = 1; i <= runs; i++)); do
  out="$run_dir/run-$(printf '%02d' "$i").wav"
  read -r http_code elapsed < <(
    curl -sS \
      -o "$out" \
      -w '%{http_code} %{time_total}\n' \
      -H 'Content-Type: application/json' \
      -d "$payload" \
      "$base_url/v1/audio/speech"
  )
  phase="repeat"
  [[ "$i" -eq 1 ]] && phase="first"

  if [[ "$http_code" != "200" ]]; then
    error_out="${out%.wav}.error.txt"
    mv "$out" "$error_out"
    printf '%d\t%s\t%s\t%s\t\t\t%s\t%s\n' \
      "$i" "$phase" "$http_code" "$elapsed" "$(wc -c < "$error_out")" "$error_out" \
      >> "$run_dir/results.tsv"
    echo "run=$i phase=$phase HTTP=$http_code elapsed=${elapsed}s response saved to $error_out" >&2
    cat "$error_out" >&2
    exit 1
  fi

  duration=""
  if command -v ffprobe >/dev/null 2>&1; then
    duration="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$out" 2>/dev/null || true)"
  fi
  bytes="$(wc -c < "$out")"
  if [[ -n "$duration" ]]; then
    rtf="$(awk -v t="$elapsed" -v d="$duration" 'BEGIN { if (d > 0) printf "%.3f", t / d; else print "n/a" }')"
    printf 'run=%d phase=%s elapsed=%.3fs audio=%.3fs RTF=%s bytes=%s file=%s\n' \
      "$i" "$phase" "$elapsed" "$duration" "$rtf" "$bytes" "$out"
    printf '%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$i" "$phase" "$http_code" "$elapsed" "$duration" "$rtf" "$bytes" "$out" \
      >> "$run_dir/results.tsv"
  else
    printf 'run=%d phase=%s elapsed=%.3fs bytes=%s file=%s (install ffprobe for RTF)\n' \
      "$i" "$phase" "$elapsed" "$bytes" "$out"
    printf '%d\t%s\t%s\t%s\t\t\t%s\t%s\n' \
      "$i" "$phase" "$http_code" "$elapsed" "$bytes" "$out" \
      >> "$run_dir/results.tsv"
  fi
done

echo "saved=$run_dir"
