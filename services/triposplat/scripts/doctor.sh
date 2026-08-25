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

if ./scripts/check_weights.sh --quiet; then
  echo "OK      TripoSplat weights"
else
  echo "MISSING one or more TripoSplat weights" >&2
  ./scripts/check_weights.sh || true
fi

outputs="${TRIPOSPLAT_DATA_ROOT:-/data/service/triposplat}/outputs"
if [[ -d "$outputs" ]]; then
  echo "OK      $outputs"
else
  echo "MISSING $outputs" >&2
fi

docker compose ps triposplat || true

port="${TRIPOSPLAT_PORT:-7861}"
bind="${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}"
probe_host="$bind"
if [[ "$probe_host" == "0.0.0.0" || "$probe_host" == "::" ]]; then
  probe_host="127.0.0.1"
fi

if command -v curl >/dev/null 2>&1 && curl -fsS "http://${probe_host}:${port}/" >/dev/null 2>&1; then
  echo "TripoSplat UI is responding on ${probe_host}:${port}."
else
  echo "TripoSplat UI is not responding on ${probe_host}:${port}." >&2
fi

if [[ "${TRIPOSPLAT_REF:-main}" == "main" || "${TRIPOSPLAT_REF:-main}" == "master" ]]; then
  echo "NOTE: TRIPOSPLAT_REF=${TRIPOSPLAT_REF:-main} follows a branch; pin it for reproducible rebuilds." >&2
fi
