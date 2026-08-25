#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f .env ]]; then
  cp .env.template .env
  chmod 600 .env
  echo "[setup] Created .env from .env.template"
fi

set -a
# shellcheck disable=SC1091
source ./.env
set +a

COMFY_DATA_ROOT="${COMFY_DATA_ROOT:-/data/service/comfyui}"
HF_REPOS_ROOT="${HF_REPOS_ROOT:-/data/hf-repos}"

mkdir -p \
  "$COMFY_DATA_ROOT/models" \
  "$COMFY_DATA_ROOT/input" \
  "$COMFY_DATA_ROOT/output" \
  "$COMFY_DATA_ROOT/user" \
  "$COMFY_DATA_ROOT/custom_nodes" \
  "$COMFY_DATA_ROOT/cache/huggingface" \
  "$COMFY_DATA_ROOT/cache/torch"

if [[ ! -d "$HF_REPOS_ROOT" ]]; then
  echo "[setup] ERROR: HF_REPOS_ROOT does not exist: $HF_REPOS_ROOT" >&2
  exit 1
fi

./start.sh
