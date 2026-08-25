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

root="${ACESTEP_DATA_ROOT:-/data/service/docker/ace-step}"
for rel in models outputs huggingface torch uv-cache cache; do
  if [[ -d "$root/$rel" ]]; then
    echo "OK      $root/$rel"
  else
    echo "MISSING $root/$rel" >&2
  fi
done

echo
docker compose ps ace-step-ui || true

port="${ACESTEP_UI_PORT:-7860}"
bind="${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}"
probe_host="$bind"
if [[ "$probe_host" == "0.0.0.0" || "$probe_host" == "::" ]]; then
  probe_host="127.0.0.1"
fi

if command -v curl >/dev/null 2>&1 && curl -fsS "http://${probe_host}:${port}/" >/dev/null 2>&1; then
  echo "ACE-Step UI is responding on ${probe_host}:${port}."
else
  echo "ACE-Step UI is not responding on ${probe_host}:${port}." >&2
fi

if [[ "${ACESTEP_REF:-main}" == "main" || "${ACESTEP_REF:-main}" == "master" ]]; then
  echo "NOTE: ACESTEP_REF=${ACESTEP_REF:-main} follows a branch; pin it for reproducible rebuilds." >&2
fi
