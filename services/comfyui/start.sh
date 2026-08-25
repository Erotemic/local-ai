#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f .env ]]; then
  cp .env.template .env
  chmod 600 .env
  echo "[start] Created .env from .env.template"
fi

set -a
# shellcheck disable=SC1091
source ./.env
set +a

if [[ "${PRIMARY_GPU:-auto}" == "auto" ]]; then
  PRIMARY_GPU_RESOLVED="$(./scripts/gpu_topology.sh --best-index)"
else
  PRIMARY_GPU_RESOLVED="${PRIMARY_GPU}"
fi
export PRIMARY_GPU_RESOLVED

echo "[start] Primary GPU index: ${PRIMARY_GPU_RESOLVED}"
echo "[start] Host bind:         ${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}:${COMFYUI_PORT:-8188}"
echo "[start] HF repositories:   ${HF_REPOS_ROOT:-/data/hf-repos}"
echo "[start] ComfyUI data:      ${COMFY_DATA_ROOT:-/data/service/comfyui}"

docker compose up -d --build comfyui

echo
echo "ComfyUI: http://${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}:${COMFYUI_PORT:-8188}"
