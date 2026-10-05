#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
SERVICE_ENV="$SERVICE_DIR/.env"

# Schema v6 removes the retired Wavhost deployment. Clean those keys before the
# generic config refresher runs so they are not carried forward as local extras.
if [[ -f "$SERVICE_ENV" ]]; then
  current_backend="$(sed -n 's/^TTS_ACTIVE_BACKEND=//p' "$SERVICE_ENV" | tail -n 1)"
  case "$current_backend" in
    kokoro)
      sed -i 's/^TTS_ACTIVE_BACKEND=kokoro$/TTS_ACTIVE_BACKEND=kokoro-gpu/' "$SERVICE_ENV"
      ;;
    wavhost)
      echo "Migrating retired TTS_ACTIVE_BACKEND=wavhost to qwentts." >&2
      sed -i 's/^TTS_ACTIVE_BACKEND=wavhost$/TTS_ACTIVE_BACKEND=qwentts/' "$SERVICE_ENV"
      ;;
  esac

  tmp="$(mktemp "$SERVICE_DIR/.env.migrate.XXXXXX")"
  awk '
    /^TTS_WAVHOST_/ {next}
    /^WAVHOST_/ {next}
    /^TTS_TORCH_VERSION=/ {next}
    /^TTS_TORCHAUDIO_VERSION=/ {next}
    /^TTS_TORCH_CUDA_TAG=/ {next}
    /^TTS_QWEN_TTS_VERSION=/ {next}
    /^TTS_BENCH_QWEN_MODEL=/ {next}
    /^TTS_BENCH_QWEN_VOICE=/ {next}
    /^TTS_KOKORO_PORT=/ {next}
    /^TTS_KOKORO_IMAGE=/ {next}
    {print}
  ' "$SERVICE_ENV" > "$tmp"
  chmod 0600 "$tmp"
  mv "$tmp" "$SERVICE_ENV"
fi

uv run "$ROOT_DIR/scripts/local_ai.py" setup --service-dir "$SERVICE_DIR" "$@"

for arg in "$@"; do
  if [[ "$arg" == "--plan" ]]; then
    exit 0
  fi
done

# Pull only the selected operational runtime. qwentts-gateway is a tiny local
# image and was already built by local_ai.py above.
set -a
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
# shellcheck disable=SC1091
source "$SERVICE_DIR/.env"
set +a

case "${TTS_ACTIVE_BACKEND:-qwentts}" in
  qwentts)
    "$SERVICE_DIR/compose.sh" pull qwentts
    ;;
  kokoro-gpu)
    "$SERVICE_DIR/compose.sh" pull kokoro-gpu
    ;;
  kokoro-cpu)
    "$SERVICE_DIR/compose.sh" pull kokoro-cpu
    ;;
  *)
    echo "ERROR: TTS_ACTIVE_BACKEND must be qwentts, kokoro-gpu, or kokoro-cpu." >&2
    exit 2
    ;;
esac
