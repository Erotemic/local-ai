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

echo "[start] GPU index: ${ACESTEP_GPU:-1}"
echo "[start] Data root: ${ACESTEP_DATA_ROOT:-/data/service/docker/ace-step}"
echo "[start] Host bind: ${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}:${ACESTEP_UI_PORT:-7860}"

docker compose up -d --build ace-step-ui

echo
echo "ACE-Step: http://${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}:${ACESTEP_UI_PORT:-7860}"
