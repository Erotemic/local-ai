#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"

# setup.sh remains the only required provisioning entry point. Populate the
# source submodule automatically on a fresh clone, but never reset an existing
# Wavhost checkout that may contain local work for the erotemic fork.
if [[ ! -f "$ROOT_DIR/submodules/wavhost/pyproject.toml" ]]; then
  git -C "$ROOT_DIR" submodule update --init --recursive submodules/wavhost
fi

uv run "$ROOT_DIR/scripts/local_ai.py" setup --service-dir "$SERVICE_DIR" "$@"

for arg in "$@"; do
  if [[ "$arg" == "--plan" ]]; then
    exit 0
  fi
done

# Keep the known-good Kokoro image available without making start download it.
"$SERVICE_DIR/compose.sh" pull kokoro
