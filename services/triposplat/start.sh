#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SERVICE_DIR"

if [[ ! -f .env ]]; then
  cp .env.template .env
  chmod 600 .env
  echo "[start] Created .env from .env.template"
fi

set -a
# shellcheck disable=SC1091
source ./.env
set +a

echo "[start] GPU index: ${TRIPOSPLAT_GPU:-0}"
echo "[start] Weights:   ${HF_REPOS_ROOT:-/data/hf-repos}/VAST-AI/TripoSplat"
echo "[start] Outputs:   ${TRIPOSPLAT_DATA_ROOT:-/data/service/triposplat}/outputs"
echo "[start] Host bind: ${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}:${TRIPOSPLAT_PORT:-7861}"

docker compose up -d --build triposplat

echo
echo "TripoSplat: http://${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}:${TRIPOSPLAT_PORT:-7861}"
