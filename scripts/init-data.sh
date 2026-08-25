#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a
fi

HF_REPOS_ROOT="${HF_REPOS_ROOT:-/data/hf-repos}"
LOCAL_AI_WORKSPACES_ROOT="${LOCAL_AI_WORKSPACES_ROOT:-/data/local-ai/workspaces}"

paths=(
  "$HF_REPOS_ROOT"
  "/data/service/comfyui"
  "/data/service/docker/ace-step"
  "/data/service/triposplat"
  "$LOCAL_AI_WORKSPACES_ROOT"
)

for path in "${paths[@]}"; do
  if mkdir -p "$path" 2>/dev/null; then
    echo "OK      $path"
  else
    echo "NEEDS PERMISSION  $path" >&2
  fi
done
