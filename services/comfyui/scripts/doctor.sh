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

printf '%s\n' '== Host GPUs =='
./scripts/gpu_topology.sh || true

printf '\n%s\n' '== Docker service =='
docker compose ps comfyui || true

port="${COMFYUI_PORT:-8188}"
bind="${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}"
probe_host="$bind"
if [[ "$probe_host" == "0.0.0.0" || "$probe_host" == "::" ]]; then
  probe_host="127.0.0.1"
fi

if command -v curl >/dev/null 2>&1 && curl -fsS "http://${probe_host}:${port}/system_stats" >/tmp/local-ai-comfyui-system-stats.json 2>/dev/null; then
  echo "ComfyUI API is responding on ${probe_host}:${port}."
else
  echo "ComfyUI API is not responding on ${probe_host}:${port}." >&2
fi

if ./scripts/check_minimax_h3_models.sh --quiet; then
  echo "MiniMax H3 canonical weights: complete recommended set."
else
  echo "NOTE: MiniMax H3 canonical weights are incomplete or not installed." >&2
fi
