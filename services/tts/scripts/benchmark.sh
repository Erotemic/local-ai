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

backend="${1:-qwentts}"
runs="${2:-2}"
text="${TTS_BENCH_TEXT:-In this lecture, we will examine how a numerical algorithm transforms a sequence of approximations into a stable solution. The important point is not only the final answer, but also the assumptions that make each intermediate step valid. We will keep those assumptions explicit as we proceed.}"

bind="${TTS_BIND_ADDRESS:-127.0.0.1}"
host="$bind"
case "$host" in 0.0.0.0|::|"[::]") host="127.0.0.1" ;; esac

case "$backend" in
  qwentts)
    base_url="http://${host}:${TTS_QWENTTS_PORT:-11436}"
    model="${TTS_BENCH_QWENTTS_MODEL:-qwen-0.6-customvoice-q8-ggml}"
    voice="${TTS_BENCH_QWENTTS_VOICE:-ryan}"
    dtype="Q8_0"
    runtime_image="${TTS_QWENTTS_IMAGE:-ghcr.io/serveurpersocom/qwentts.cpp:cuda12}"
    runtime_container="ai-voice-qwentts"
    ;;
  kokoro-gpu)
    base_url="http://${host}:${TTS_KOKORO_GPU_PORT:-8880}"
    model="${TTS_BENCH_KOKORO_MODEL:-kokoro}"
    voice="${TTS_BENCH_KOKORO_VOICE:-af_heart}"
    dtype="PyTorch GPU"
    runtime_image="${TTS_KOKORO_GPU_IMAGE:-ghcr.io/remsky/kokoro-fastapi-gpu:v0.3.0-amd64}"
    runtime_container="ai-voice-kokoro-gpu"
    ;;
  kokoro-cpu)
    base_url="http://${host}:${TTS_KOKORO_CPU_PORT:-8881}"
    model="${TTS_BENCH_KOKORO_MODEL:-kokoro}"
    voice="${TTS_BENCH_KOKORO_VOICE:-af_heart}"
    dtype="PyTorch CPU"
    runtime_image="${TTS_KOKORO_CPU_IMAGE:-ghcr.io/remsky/kokoro-fastapi-cpu:v0.3.0-amd64}"
    runtime_container="ai-voice-kokoro-cpu"
    ;;
  *)
    echo "usage: $0 [qwentts|kokoro-gpu|kokoro-cpu] [runs]" >&2
    exit 2
    ;;
esac

if ! [[ "$runs" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: runs must be a positive integer; got '$runs'." >&2
  exit 2
fi

for command in python3 curl; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "ERROR: $command is required." >&2
    exit 1
  fi
done

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

runtime_image_id=""
runtime_device_ids=""
runtime_clamp_fp16=""
runtime_no_fa=""
runtime_language=""
runtime_model_path=""
runtime_codec_path=""
runtime_model_alias=""

container_env_value() {
  local key="$1"
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$runtime_container" 2>/dev/null \
    | sed -n "s/^${key}=//p" | tail -n 1
}

if command -v docker >/dev/null 2>&1; then
  actual_runtime_image="$(docker inspect --format '{{.Config.Image}}' "$runtime_container" 2>/dev/null || true)"
  [[ -n "$actual_runtime_image" ]] && runtime_image="$actual_runtime_image"
  runtime_image_id="$(docker inspect --format '{{.Image}}' "$runtime_container" 2>/dev/null || true)"
  runtime_device_ids="$(docker inspect --format '{{range .HostConfig.DeviceRequests}}{{range .DeviceIDs}}{{printf "%s " .}}{{end}}{{end}}' "$runtime_container" 2>/dev/null | xargs || true)"
  if [[ "$backend" == "qwentts" ]]; then
    runtime_clamp_fp16="$(container_env_value CLAMP_FP16)"
    runtime_no_fa="$(container_env_value NO_FA)"
    runtime_language="$(container_env_value TTS_LANG)"
    runtime_model_path="$(container_env_value MODEL_PATH)"
    runtime_codec_path="$(container_env_value CODEC_PATH)"
    runtime_model_alias="$(container_env_value MODEL_ALIAS)"
  fi
fi

BACKEND="$backend" BASE_URL="$base_url" MODEL="$model" VOICE="$voice" \
TEXT="$text" RUNS="$runs" DTYPE="$dtype" RUN_DIR="$run_dir" \
RUNTIME_IMAGE="$runtime_image" RUNTIME_IMAGE_ID="$runtime_image_id" RUNTIME_CONTAINER="$runtime_container" \
RUNTIME_DEVICE_IDS="$runtime_device_ids" RUNTIME_CLAMP_FP16="$runtime_clamp_fp16" \
RUNTIME_NO_FA="$runtime_no_fa" RUNTIME_LANGUAGE="$runtime_language" \
RUNTIME_MODEL_PATH="$runtime_model_path" RUNTIME_CODEC_PATH="$runtime_codec_path" \
RUNTIME_MODEL_ALIAS="$runtime_model_alias" \
python3 - <<'PY' > "$run_dir/metadata.json"
import json
import os

runtime_knobs = {}
for key, env_key in [
    ("clamp_fp16", "RUNTIME_CLAMP_FP16"),
    ("no_fa", "RUNTIME_NO_FA"),
    ("language", "RUNTIME_LANGUAGE"),
    ("model_path", "RUNTIME_MODEL_PATH"),
    ("codec_path", "RUNTIME_CODEC_PATH"),
    ("model_alias", "RUNTIME_MODEL_ALIAS"),
]:
    value = os.environ.get(env_key, "")
    if value:
        runtime_knobs[key] = value

print(json.dumps({
    "backend": os.environ["BACKEND"],
    "base_url": os.environ["BASE_URL"],
    "model": os.environ["MODEL"],
    "voice": os.environ["VOICE"],
    "text": os.environ["TEXT"],
    "runs": int(os.environ["RUNS"]),
    "precision_or_quant": os.environ["DTYPE"],
    "runtime_image": os.environ["RUNTIME_IMAGE"],
    "runtime_image_id": os.environ["RUNTIME_IMAGE_ID"],
    "runtime_container": os.environ["RUNTIME_CONTAINER"],
    "runtime_docker_device_ids": os.environ["RUNTIME_DEVICE_IDS"],
    "runtime_knobs": runtime_knobs,
    "artifact_dir": os.environ["RUN_DIR"],
    "cache_state": "not reset by benchmark.sh",
}, indent=2, sort_keys=True))
PY

printf 'run\tphase\tcurl_exit\thttp_code\telapsed_s\taudio_s\trtf\tbytes\tfile\n' > "$run_dir/results.tsv"
printf '%s\n' "$text" > "$run_dir/input.txt"
printf '%s\n' "$payload" > "$run_dir/request.json"

echo "backend=$backend url=$base_url model=$model voice=$voice runs=$runs dtype=$dtype"
echo "artifacts=$run_dir"
if [[ "$backend" == "qwentts" ]]; then
  echo "runtime_knobs=clamp_fp16=${runtime_clamp_fp16:-unknown} no_fa=${runtime_no_fa:-unknown} language=${runtime_language:-unknown}"
fi
echo "note=benchmark.sh does not reset the server/model cache; phase=first means first request in this invocation, not necessarily a cold model load"

if ! curl -fsS --max-time 3 "$base_url/health" >/dev/null 2>&1; then
  echo "ERROR: $backend is not healthy at $base_url/health; refusing to start a benchmark." >&2
  docker logs --tail=200 "$runtime_container" >&2 2>/dev/null || true
  exit 1
fi

for ((i = 1; i <= runs; i++)); do
  out="$run_dir/run-$(printf '%02d' "$i").wav"
  curl_stderr="$run_dir/run-$(printf '%02d' "$i").curl.stderr.txt"
  phase="repeat"
  [[ "$i" -eq 1 ]] && phase="first"

  set +e
  metrics="$(curl -sS --connect-timeout 5 -o "$out" -w '%{http_code} %{time_total}\n' \
    -H 'Content-Type: application/json' -d "$payload" "$base_url/v1/audio/speech" 2>"$curl_stderr")"
  curl_exit=$?
  set -e

  http_code="000"
  elapsed="0"
  [[ -n "$metrics" ]] && read -r http_code elapsed <<< "$metrics"

  if [[ "$curl_exit" -ne 0 || "$http_code" != "200" ]]; then
    echo "ERROR: run $i failed: curl_exit=$curl_exit HTTP=$http_code elapsed=${elapsed}s" >&2
    cat "$curl_stderr" >&2 || true
    docker logs --tail=200 "$runtime_container" > "$run_dir/run-$(printf '%02d' "$i").container.log" 2>&1 || true
    exit 1
  fi
  rm -f "$curl_stderr"

  duration=""
  if command -v ffprobe >/dev/null 2>&1; then
    duration="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$out" 2>/dev/null || true)"
  fi
  bytes="$(wc -c < "$out")"
  if [[ -n "$duration" ]]; then
    rtf="$(awk -v t="$elapsed" -v d="$duration" 'BEGIN { if (d > 0) printf "%.3f", t / d; else print "n/a" }')"
    printf 'run=%d phase=%s elapsed=%.3fs audio=%.3fs RTF=%s bytes=%s file=%s\n' \
      "$i" "$phase" "$elapsed" "$duration" "$rtf" "$bytes" "$out"
    printf '%d\t%s\t%d\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$i" "$phase" "$curl_exit" "$http_code" "$elapsed" "$duration" "$rtf" "$bytes" "$out" \
      >> "$run_dir/results.tsv"
  else
    printf 'run=%d phase=%s elapsed=%.3fs bytes=%s file=%s (install ffprobe for RTF)\n' \
      "$i" "$phase" "$elapsed" "$bytes" "$out"
    printf '%d\t%s\t%d\t%s\t%s\t\t\t%s\t%s\n' \
      "$i" "$phase" "$curl_exit" "$http_code" "$elapsed" "$bytes" "$out" \
      >> "$run_dir/results.tsv"
  fi
done

echo "saved=$run_dir"
