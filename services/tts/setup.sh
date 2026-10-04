#!/usr/bin/env bash
set -euo pipefail
SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"

# setup.sh remains the provisioning entry point. Populate the source submodule
# automatically on a fresh clone, but never reset an existing Wavhost checkout.
if [[ ! -f "$ROOT_DIR/submodules/wavhost/pyproject.toml" ]]; then
  git -C "$ROOT_DIR" submodule update --init --recursive submodules/wavhost
fi

uv run "$ROOT_DIR/scripts/local_ai.py" setup --service-dir "$SERVICE_DIR" "$@"

for arg in "$@"; do
  if [[ "$arg" == "--plan" ]]; then
    exit 0
  fi
done

# Pull only the configured operational backend. The old Kokoro-FastAPI image is
# no longer an unconditional setup dependency when qwentts is the active server.
set -a
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
# shellcheck disable=SC1091
source "$SERVICE_DIR/.env"
set +a

case "${TTS_ACTIVE_BACKEND:-qwentts}" in
  qwentts)
    "$SERVICE_DIR/compose.sh" pull qwentts
    ;;
  kokoro)
    "$SERVICE_DIR/compose.sh" pull kokoro
    ;;
  wavhost)
    # Built locally by local_ai.py setup; no registry image pull required.
    ;;
  *)
    echo "ERROR: TTS_ACTIVE_BACKEND must be qwentts, wavhost, or kokoro." >&2
    exit 2
    ;;
esac
