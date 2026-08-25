#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SERVICE_DIR"

if [[ ! -f .env ]]; then
  cp .env.template .env
  chmod 600 .env
  echo "[setup] Created .env from .env.template"
fi

set -a
# shellcheck disable=SC1091
source ./.env
set +a

root="${ACESTEP_DATA_ROOT:-/data/service/docker/ace-step}"
mkdir -p \
  "$root/models" \
  "$root/outputs" \
  "$root/huggingface" \
  "$root/torch" \
  "$root/uv-cache" \
  "$root/cache"

./start.sh
