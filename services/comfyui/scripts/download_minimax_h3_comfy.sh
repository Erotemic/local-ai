#!/usr/bin/env bash
set -euo pipefail

HF_REPOS_ROOT="${HF_REPOS_ROOT:-/data/hf-repos}"
TARGET="$HF_REPOS_ROOT/Comfy-Org/MiniMax-H3"
MODE="recommended"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --all-variants) MODE="all" ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      cat <<HELP
usage: $0 [--all-variants] [--dry-run]

Default: download the native ComfyUI files needed for FL2VA + Ref2VA.
--all-variants mirrors the entire Comfy-Org/MiniMax-H3 repository.
HELP
      exit 0
      ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if ! command -v hf >/dev/null 2>&1; then
  echo "hf CLI not found on the host" >&2
  exit 1
fi

mkdir -p "$TARGET"

args=(download Comfy-Org/MiniMax-H3 --local-dir "$TARGET")
if [[ "$MODE" == "recommended" ]]; then
  args+=(--include
    'diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors'
    'diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors'
    'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors'
    'vae/minimax_h3_video_vae_fp16.safetensors'
    'vae/minimax_h3_audio_vae_fp32.safetensors')
fi
if (( DRY_RUN )); then
  args+=(--dry-run)
fi

echo "[download] Target: $TARGET"
echo "[download] Mode:   $MODE"
HF_XET_HIGH_PERFORMANCE=1 hf "${args[@]}"
