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
runs="${2:-3}"
text="${TTS_BENCH_TEXT:-In this lecture, we will examine how a numerical algorithm transforms a sequence of approximations into a stable solution. The important point is not only the final answer, but also the assumptions that make each intermediate step valid. We will keep those assumptions explicit as we proceed.}"
bind="${TTS_BIND_ADDRESS:-127.0.0.1}"
host="$bind"
case "$host" in 0.0.0.0|::|"[::]") host="127.0.0.1" ;; esac

case "$backend" in
  qwentts)
    base_url="http://${host}:${TTS_QWENTTS_PORT:-11436}"
    model="${TTS_BENCH_QWENTTS_MODEL:-qwen-0.6-customvoice-q8-ggml}"
    voice="${TTS_BENCH_QWENTTS_VOICE:-ryan}"
    dtype="Q8_0"; gpu="${TTS_QWENTTS_GPU:-}"; runtime_container="ai-voice-qwentts"
    runtime_image="${TTS_QWENTTS_IMAGE:-ghcr.io/serveurpersocom/qwentts.cpp:cuda12}"
    ;;
  kokoro-gpu)
    base_url="http://${host}:${TTS_KOKORO_GPU_PORT:-8880}"
    model="${TTS_BENCH_KOKORO_MODEL:-kokoro}"; voice="${TTS_BENCH_KOKORO_VOICE:-af_heart}"
    dtype="PyTorch GPU"; gpu="${TTS_KOKORO_GPU:-}"; runtime_container="ai-voice-kokoro-gpu"
    runtime_image="${TTS_KOKORO_GPU_IMAGE:-ghcr.io/remsky/kokoro-fastapi-gpu:v0.3.0-amd64}"
    ;;
  kokoro-cpu)
    base_url="http://${host}:${TTS_KOKORO_CPU_PORT:-8881}"
    model="${TTS_BENCH_KOKORO_MODEL:-kokoro}"; voice="${TTS_BENCH_KOKORO_VOICE:-af_heart}"
    dtype="PyTorch CPU"; gpu=""; runtime_container="ai-voice-kokoro-cpu"
    runtime_image="${TTS_KOKORO_CPU_IMAGE:-ghcr.io/remsky/kokoro-fastapi-cpu:v0.3.0-amd64}"
    ;;
  indextts25)
    base_url="http://${host}:${TTS_INDEXTTS25_PORT:-11438}"
    model="${TTS_INDEXTTS25_MODEL_ALIAS:-indextts-2.5}"; voice="${TTS_BENCH_CANDIDATE_VOICE:-ryan}"
    dtype="${TTS_INDEXTTS25_DTYPE:-auto}"; gpu="${TTS_INDEXTTS25_GPU:-}"; runtime_container="ai-voice-indextts25"
    runtime_image="${TTS_INDEXTTS25_IMAGE:-local/indextts25:cu126}"
    ;;
  chatterbox-flash)
    base_url="http://${host}:${TTS_CHATTERBOX_FLASH_PORT:-11439}"
    model="${TTS_CHATTERBOX_FLASH_MODEL_ALIAS:-chatterbox-flash}"; voice="${TTS_BENCH_CANDIDATE_VOICE:-ryan}"
    dtype="${TTS_CHATTERBOX_FLASH_DTYPE:-auto}"; gpu="${TTS_CHATTERBOX_FLASH_GPU:-}"; runtime_container="ai-voice-chatterbox-flash"
    runtime_image="${TTS_CHATTERBOX_IMAGE:-local/chatterbox-experiments:cu126}"
    ;;
  chatterbox-nano)
    base_url="http://${host}:${TTS_CHATTERBOX_NANO_PORT:-11440}"
    model="${TTS_CHATTERBOX_NANO_MODEL_ALIAS:-chatterbox-nano}"; voice="${TTS_BENCH_CANDIDATE_VOICE:-ryan}"
    dtype="upstream-default"; gpu="${TTS_CHATTERBOX_NANO_GPU:-}"; runtime_container="ai-voice-chatterbox-nano"
    runtime_image="${TTS_CHATTERBOX_IMAGE:-local/chatterbox-experiments:cu126}"
    ;;
  *)
    echo "usage: $0 [qwentts|kokoro-gpu|kokoro-cpu|indextts25|chatterbox-flash|chatterbox-nano] [runs]" >&2
    exit 2
    ;;
esac

if ! [[ "$runs" =~ ^[1-9][0-9]*$ ]]; then echo "ERROR: runs must be a positive integer." >&2; exit 2; fi
for command in python3 curl; do command -v "$command" >/dev/null || { echo "ERROR: $command is required." >&2; exit 1; }; done

data_root="${TTS_DATA_ROOT:-${LOCAL_AI_SERVICE_ROOT:-/data/services/local-ai}/tts}"
output_root="${TTS_BENCH_OUTPUT_DIR:-$data_root/benchmarks}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
slug_model="${model//[^A-Za-z0-9._-]/_}"; slug_voice="${voice//[^A-Za-z0-9._-]/_}"
run_dir="$output_root/${stamp}-${backend}-${slug_model}-${slug_voice}"
mkdir -p "$run_dir"

payload="$(MODEL="$model" VOICE="$voice" TEXT="$text" python3 - <<'PY'
import json, os
print(json.dumps({
    "model": os.environ["MODEL"], "voice": os.environ["VOICE"],
    "input": os.environ["TEXT"], "response_format": "wav", "speed": 1.0,
    "stream": False, "seed": 42,
}))
PY
)"
printf '%s\n' "$text" > "$run_dir/input.txt"
printf '%s\n' "$payload" > "$run_dir/request.json"
printf 'run\tphase\tcurl_exit\thttp_code\telapsed_s\taudio_s\trtf\tbytes\tfile\n' > "$run_dir/results.tsv"

actual_runtime_image="$(docker inspect --format '{{.Config.Image}}' "$runtime_container" 2>/dev/null || true)"
[[ -n "$actual_runtime_image" ]] && runtime_image="$actual_runtime_image"
runtime_image_id="$(docker inspect --format '{{.Image}}' "$runtime_container" 2>/dev/null || true)"
runtime_device_ids="$(docker inspect --format '{{range .HostConfig.DeviceRequests}}{{range .DeviceIDs}}{{printf "%s " .}}{{end}}{{end}}' "$runtime_container" 2>/dev/null | xargs || true)"

container_env_value() {
  local key="$1"
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$runtime_container" 2>/dev/null \
    | sed -n "s/^${key}=//p" | tail -n 1
}
runtime_knobs_json='{}'
if [[ "$backend" == "qwentts" ]]; then
  runtime_knobs_json="$(python3 - \
    "$(container_env_value CLAMP_FP16)" \
    "$(container_env_value NO_FA)" \
    "$(container_env_value TTS_LANG)" \
    "$(container_env_value MODEL_PATH)" \
    "$(container_env_value CODEC_PATH)" \
    "$(container_env_value MODEL_ALIAS)" <<'PY2'
import json, sys
keys=['clamp_fp16','no_fa','language','model_path','codec_path','model_alias']
print(json.dumps({k:v for k,v in zip(keys,sys.argv[1:]) if v}))
PY2
  )"
fi

{
  echo "hostname=$(hostname)"
  echo "kernel=$(uname -srmo)"
  echo "backend=$backend"
  echo "configured_gpu=$gpu"
  echo "docker_device_ids=$runtime_device_ids"
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=index,name,uuid,driver_version,memory.used,memory.total --format=csv,noheader 2>/dev/null || true
  fi
} > "$run_dir/host-hardware.txt"

if ! curl -fsS --max-time 3 "$base_url/health" > "$run_dir/health.json"; then
  echo "ERROR: $backend is not healthy at $base_url/health" >&2
  docker logs --tail=240 "$runtime_container" > "$run_dir/container.log" 2>&1 || true
  exit 1
fi

candidate=0
case "$backend" in indextts25|chatterbox-flash|chatterbox-nano) candidate=1 ;; esac
if (( candidate )); then
  curl -fsS --max-time 15 "$base_url/v1/runtime" > "$run_dir/runtime-before.json"
  python3 - "$run_dir/runtime-before.json" <<'PY'
import json, re, sys
p=sys.argv[1]; d=json.load(open(p))
cap=d.get('cuda_compute_capability')
arches=d.get('compiled_cuda_arch_list', [])
if not d.get('cuda_available'):
    raise SystemExit('candidate runtime did not expose CUDA')
if not cap or len(cap) != 2:
    raise SystemExit(f'missing compute capability: {cap!r}')
device_major, device_minor = (int(cap[0]), int(cap[1]))
compatible = []
for arch in arches:
    m = re.fullmatch(r"sm_(\d)(\d+)", str(arch))
    if not m:
        continue
    major, minor = int(m.group(1)), int(m.group(2))
    # NVIDIA guarantees cubin binary compatibility within a compute-capability
    # major when the running GPU minor is >= the cubin target minor.  For
    # example, an sm_60 cubin is valid on a compute-capability 6.1 Pascal GPU.
    if major == device_major and minor <= device_minor:
        compatible.append((minor, arch))
if not compatible:
    wanted=f"sm_{device_major}{device_minor}"
    raise SystemExit(
        f'PyTorch build has no binary-compatible cubin for device {wanted}; '
        f'compiled arch list={arches}'
    )
compatible.sort()
selected = compatible[-1][1]
print(
    f"CUDA architecture proof: device={d.get('cuda_device_name')} "
    f"capability={cap} compatible_cubin={selected} "
    f"compiled_arches={arches} dtype={d.get('effective_dtype')}"
)
PY
fi

BACKEND="$backend" BASE_URL="$base_url" MODEL="$model" VOICE="$voice" TEXT="$text" RUNS="$runs" DTYPE="$dtype" RUN_DIR="$run_dir" \
RUNTIME_IMAGE="$runtime_image" RUNTIME_IMAGE_ID="$runtime_image_id" RUNTIME_CONTAINER="$runtime_container" RUNTIME_DEVICE_IDS="$runtime_device_ids" \
RUNTIME_KNOBS_JSON="$runtime_knobs_json" CONFIGURED_GPU="$gpu" \
python3 - <<'PY' > "$run_dir/metadata.json"
import json, os
base={k.lower(): v for k,v in {
    'BACKEND':os.environ['BACKEND'], 'BASE_URL':os.environ['BASE_URL'], 'MODEL':os.environ['MODEL'],
    'VOICE':os.environ['VOICE'], 'TEXT':os.environ['TEXT'], 'DTYPE':os.environ['DTYPE'],
    'RUNTIME_IMAGE':os.environ['RUNTIME_IMAGE'], 'RUNTIME_IMAGE_ID':os.environ['RUNTIME_IMAGE_ID'],
    'RUNTIME_CONTAINER':os.environ['RUNTIME_CONTAINER'], 'RUNTIME_DEVICE_IDS':os.environ['RUNTIME_DEVICE_IDS'],
    'RUN_DIR':os.environ['RUN_DIR'],
}.items()}
base.update({
    'runs': int(os.environ['RUNS']),
    'cache_state':'not reset by benchmark.sh',
    'seed':42,
    'configured_host_gpu_index': os.environ.get('CONFIGURED_GPU',''),
    'runtime_knobs': json.loads(os.environ.get('RUNTIME_KNOBS_JSON','{}')),
})
startup = os.environ.get('TTS_BENCH_STARTUP_SECONDS', '').strip()
if startup:
    base['startup_seconds'] = float(startup)
print(json.dumps(base, indent=2, sort_keys=True))
PY

echo "backend=$backend url=$base_url model=$model voice=$voice runs=$runs dtype=$dtype"
echo "artifacts=$run_dir"
for ((i=1; i<=runs; i++)); do
  stem="$run_dir/run-$(printf '%02d' "$i")"; out="$stem.wav"; err="$stem.curl.stderr.txt"
  phase="repeat"; [[ "$i" -eq 1 ]] && phase="first"
  set +e
  metrics="$(curl -sS --connect-timeout 5 --max-time 300 -o "$out" -w '%{http_code} %{time_total}\n' -H 'Content-Type: application/json' -d "$payload" "$base_url/v1/audio/speech" 2>"$err")"
  curl_exit=$?
  set -e
  http_code="000"; elapsed="0"; [[ -n "$metrics" ]] && read -r http_code elapsed <<< "$metrics"
  if [[ "$curl_exit" -ne 0 || "$http_code" != "200" ]]; then
    echo "ERROR: run $i failed: curl_exit=$curl_exit HTTP=$http_code elapsed=${elapsed}s" >&2
    cat "$err" >&2 || true
    docker logs --tail=240 "$runtime_container" > "$stem.container.log" 2>&1 || true
    exit 1
  fi
  rm -f "$err"
  if ! python3 "$SERVICE_DIR/scripts/audio_sanity.py" "$out" > "$stem.sanity.json"; then
    echo "ERROR: run $i returned mechanically implausible WAV; see $stem.sanity.json" >&2
    docker logs --tail=240 "$runtime_container" > "$stem.container.log" 2>&1 || true
    exit 1
  fi
  duration="$(python3 - "$stem.sanity.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))[0]['duration_s'])
PY
)"
  bytes="$(wc -c < "$out")"
  rtf="$(awk -v t="$elapsed" -v d="$duration" 'BEGIN {if (d>0) printf "%.4f", t/d; else print "n/a"}')"
  printf 'run=%d phase=%s elapsed=%.3fs audio=%.3fs RTF=%s bytes=%s file=%s\n' "$i" "$phase" "$elapsed" "$duration" "$rtf" "$bytes" "$out"
  printf '%d\t%s\t%d\t%s\t%s\t%s\t%s\t%s\t%s\n' "$i" "$phase" "$curl_exit" "$http_code" "$elapsed" "$duration" "$rtf" "$bytes" "$out" >> "$run_dir/results.tsv"
done

if (( candidate )); then curl -fsS --max-time 15 "$base_url/v1/runtime" > "$run_dir/runtime-after.json"; fi
cat > "$run_dir/LISTEN_REVIEW.md" <<EOF
# Human listening gate

Backend: $backend
Model: $model
Voice/reference: $voice

Mechanical audio sanity passed for every retained WAV. That is necessary but **not sufficient** after the qwentts/Pascal failure.

Listen to every run and record:

- [ ] requested words are recognizable and in the correct order
- [ ] no repeated/omitted clauses or hallucinated speech
- [ ] no metallic/high-pitched/numerical corruption
- [ ] Ryan identity is acceptably preserved (where cloning is supported)
- [ ] prosody is acceptable for multi-sentence lecture narration
- [ ] no obvious degradation between first and repeat generations

Only promote a backend after this gate passes on the GTX 1080 Ti.
EOF

echo "saved=$run_dir"
