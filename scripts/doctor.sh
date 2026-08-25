#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run "$ROOT_DIR/scripts/local_ai.py" doctor --all --repo-root "$ROOT_DIR" "$@"
