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
    ;;
  kokoro)
    base_url="http://${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}:${TTS_KOKORO_PORT:-8880}"
    model="${TTS_BENCH_KOKORO_MODEL:-kokoro}"
    voice="${TTS_BENCH_KOKORO_VOICE:-af_heart}"
    ;;
  *)
    echo "usage: $0 [wavhost|kokoro] [runs]" >&2
    exit 2
    ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required to construct the JSON request." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required." >&2
  exit 1
fi

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

echo "backend=$backend url=$base_url model=$model voice=$voice runs=$runs"
for ((i = 1; i <= runs; i++)); do
  out="$(mktemp --suffix=.wav)"
  trap 'rm -f "$out"' EXIT
  read -r http_code elapsed < <(
    curl -sS \
      -o "$out" \
      -w '%{http_code} %{time_total}\n' \
      -H 'Content-Type: application/json' \
      -d "$payload" \
      "$base_url/v1/audio/speech"
  )
  if [[ "$http_code" != "200" ]]; then
    echo "run=$i HTTP=$http_code elapsed=${elapsed}s response:" >&2
    cat "$out" >&2
    exit 1
  fi

  duration=""
  if command -v ffprobe >/dev/null 2>&1; then
    duration="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$out" 2>/dev/null || true)"
  fi
  if [[ -n "$duration" ]]; then
    rtf="$(awk -v t="$elapsed" -v d="$duration" 'BEGIN { if (d > 0) printf "%.3f", t / d; else print "n/a" }')"
    label="warm"
    [[ "$i" -eq 1 ]] && label="cold"
    printf 'run=%d (%s) elapsed=%.3fs audio=%.3fs RTF=%s bytes=%s\n' \
      "$i" "$label" "$elapsed" "$duration" "$rtf" "$(wc -c < "$out")"
  else
    printf 'run=%d elapsed=%.3fs bytes=%s (install ffprobe for RTF)\n' \
      "$i" "$elapsed" "$(wc -c < "$out")"
  fi
  rm -f "$out"
  trap - EXIT
done
