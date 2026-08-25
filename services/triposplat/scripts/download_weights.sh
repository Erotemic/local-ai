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

if ! command -v hf >/dev/null 2>&1; then
  echo "hf CLI not found on the host" >&2
  exit 1
fi

root="${HF_REPOS_ROOT:-/data/hf-repos}/VAST-AI/TripoSplat"
mkdir -p "$root"

echo "[download] VAST-AI/TripoSplat -> $root"
HF_XET_HIGH_PERFORMANCE=1 hf download VAST-AI/TripoSplat --local-dir "$root" "$@"
