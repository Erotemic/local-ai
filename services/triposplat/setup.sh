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

mkdir -p "${TRIPOSPLAT_DATA_ROOT:-/data/service/triposplat}/outputs"

if ! ./scripts/check_weights.sh --quiet; then
  cat >&2 <<MSG
[setup] TripoSplat weights are incomplete.
        Download them with:
          ./scripts/download_weights.sh
MSG
  exit 1
fi

./start.sh
