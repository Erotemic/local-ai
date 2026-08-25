#!/usr/bin/env bash
set -euo pipefail

HF_REPOS_ROOT="${HF_REPOS_ROOT:-/data/hf-repos}"
ROOT="$HF_REPOS_ROOT/Comfy-Org/MiniMax-H3"
QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

files=(
  'diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors'
  'diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors'
  'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors'
  'vae/minimax_h3_video_vae_fp16.safetensors'
  'vae/minimax_h3_audio_vae_fp32.safetensors'
)

missing=0
for rel in "${files[@]}"; do
  if [[ -f "$ROOT/$rel" ]]; then
    (( QUIET )) || echo "OK      $ROOT/$rel"
  else
    (( QUIET )) || echo "MISSING $ROOT/$rel"
    missing=1
  fi
done

exit "$missing"
