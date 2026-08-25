#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SERVICE_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a
fi

root="${HF_REPOS_ROOT:-/data/hf-repos}/VAST-AI/TripoSplat"
quiet=0
[[ "${1:-}" == "--quiet" ]] && quiet=1

files=(
  diffusion_models/triposplat_fp16.safetensors
  vae/triposplat_vae_decoder_fp16.safetensors
  clip_vision/dino_v3_vit_h.safetensors
  vae/flux2-vae.safetensors
  background_removal/birefnet.safetensors
)

missing=0
for rel in "${files[@]}"; do
  if [[ -f "$root/$rel" ]]; then
    (( quiet )) || echo "OK      $root/$rel"
  else
    (( quiet )) || echo "MISSING $root/$rel"
    missing=1
  fi
done

exit "$missing"
