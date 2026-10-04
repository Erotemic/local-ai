#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"

if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: service configuration is not initialized." >&2
  echo "Run ./setup.sh from $SERVICE_DIR" >&2
  exit 1
fi

# Preserve explicit one-shot overrides across the .env load below.
qwentts_override_keys=(
  TTS_BIND_ADDRESS
  TTS_QWENTTS_PORT
  TTS_QWENTTS_GPU
  TTS_QWENTTS_IMAGE
  TTS_QWENTTS_MODEL_ALIAS
  TTS_QWENTTS_MODEL_FILE
  TTS_QWENTTS_CODEC_FILE
  TTS_QWENTTS_LANGUAGE
  TTS_QWENTTS_CLAMP_FP16
  TTS_QWENTTS_NO_FA
  TTS_QWENTTS_START_TIMEOUT
)
declare -A inherited_overrides=()
for key in "${qwentts_override_keys[@]}"; do
  if [[ -v "$key" ]]; then
    inherited_overrides["$key"]="${!key}"
  fi
done

set -a
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
# shellcheck disable=SC1091
source "$SERVICE_DIR/.env"
set +a
for key in "${!inherited_overrides[@]}"; do
  export "$key=${inherited_overrides[$key]}"
done

validate_bool() {
  local key="$1"
  local value="${!key:-}"
  if [[ "$value" != "0" && "$value" != "1" ]]; then
    echo "ERROR: $key must be 0 or 1; got '$value'." >&2
    exit 2
  fi
}
validate_bool TTS_QWENTTS_CLAMP_FP16
validate_bool TTS_QWENTTS_NO_FA

timeout_s="${TTS_QWENTTS_START_TIMEOUT:-180}"
if ! [[ "$timeout_s" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: TTS_QWENTTS_START_TIMEOUT must be a positive integer; got '$timeout_s'." >&2
  exit 2
fi

model_root="${HF_REPOS_ROOT:-/data/services/hf-repos}/Serveurperso/Qwen3-TTS-GGUF"
model_file="${TTS_QWENTTS_MODEL_FILE:-qwen-talker-0.6b-customvoice-Q8_0.gguf}"
codec_file="${TTS_QWENTTS_CODEC_FILE:-qwen-tokenizer-12hz-Q8_0.gguf}"
missing=()
[[ -f "$model_root/$model_file" ]] || missing+=("$model_root/$model_file")
[[ -f "$model_root/$codec_file" ]] || missing+=("$model_root/$codec_file")
if (( ${#missing[@]} )); then
  echo "ERROR: qwentts model mount is incomplete:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo "Configured HF_REPOS_ROOT=${HF_REPOS_ROOT:-/data/services/hf-repos}" >&2
  echo "Run ./setup.sh --with-model qwen-0.6-customvoice-q8-gguf" >&2
  exit 1
fi

bind_address="${TTS_BIND_ADDRESS:-127.0.0.1}"
port="${TTS_QWENTTS_PORT:-11436}"

echo "qwentts effective configuration:"
echo "  bind=$bind_address:$port"
echo "  gpu=${TTS_QWENTTS_GPU:-1}"
echo "  image=${TTS_QWENTTS_IMAGE:-ghcr.io/serveurpersocom/qwentts.cpp:cuda12}"
echo "  model=$model_root/$model_file"
echo "  codec=$model_root/$codec_file"
echo "  language=${TTS_QWENTTS_LANGUAGE:-English}"
echo "  clamp_fp16=${TTS_QWENTTS_CLAMP_FP16:-1}"
echo "  no_fa=${TTS_QWENTTS_NO_FA:-0}"

uv run "$ROOT_DIR/scripts/local_ai.py" model check qwen-0.6-customvoice-q8-gguf --service-dir "$SERVICE_DIR"

"$SERVICE_DIR/compose.sh" config --quiet
"$SERVICE_DIR/compose.sh" pull qwentts
"$SERVICE_DIR/compose.sh" up -d --no-build --pull never qwentts

container="ai-voice-qwentts"
health_host="$bind_address"
case "$health_host" in
  0.0.0.0|::|"[::]") health_host="127.0.0.1" ;;
esac
health_url="http://${health_host}:${port}/health"

echo "Waiting for qwentts to become ready: $health_url (timeout ${timeout_s}s)"
deadline=$((SECONDS + timeout_s))
while (( SECONDS < deadline )); do
  if curl -fsS --max-time 3 "$health_url" >/dev/null 2>&1; then
    echo "qwentts ready: http://${health_host}:${port}"
    if [[ "$bind_address" == "0.0.0.0" || "$bind_address" == "::" || "$bind_address" == "[::]" ]]; then
      echo "LAN clients may connect to http://<server-lan-ip>:${port}"
    else
      echo "client endpoint: http://${bind_address}:${port}"
    fi
    exit 0
  fi

  state="$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || true)"
  case "$state" in
    running) ;;
    created|restarting|exited|dead|removing|paused|"")
      echo "ERROR: qwentts did not remain running while starting (state=${state:-missing})." >&2
      "$SERVICE_DIR/compose.sh" ps -a qwentts >&2 || true
      echo "--- qwentts logs ---" >&2
      "$SERVICE_DIR/compose.sh" logs --tail=200 qwentts >&2 || true
      exit 1
      ;;
  esac
  sleep 2
done

echo "ERROR: qwentts did not become healthy within ${timeout_s}s." >&2
"$SERVICE_DIR/compose.sh" ps -a qwentts >&2 || true
echo "--- qwentts logs ---" >&2
"$SERVICE_DIR/compose.sh" logs --tail=200 qwentts >&2 || true
exit 1
