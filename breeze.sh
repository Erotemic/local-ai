#!/usr/bin/env bash
# Repository-root convenience entry point; provisioning remains in setup.sh.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$ROOT_DIR/services/tts"
action="${1:-help}"
if (( $# )); then shift; fi
case "$action" in
  setup) TTS_ACTIVE_BACKEND=breeze-tts2 exec "$SERVICE_DIR/setup.sh" "$@" ;;
  start) TTS_ACTIVE_BACKEND=breeze-tts2 exec "$SERVICE_DIR/start.sh" "$@" ;;
  stop|status|logs|config) exec python3 "$SERVICE_DIR/scripts/breeze_deploy.py" "$action" "$@" ;;
  help|-h|--help)
    cat <<'HELP'
Usage: ./breeze.sh setup|config|start|stop|status|logs
  setup   Review configuration, provision Breeze, and build serving images.
  config  Show the selected GPU IDs, GPU/replica count, and fast-mode setting.
  start   Start one replica per GPU in TTS_BREEZE_GPUS; honor the saved selection.
  stop    Stop every Breeze replica and the gateway, preserving stored files.
  status  Show the selected GPUs/count and actual Breeze container states.
  logs    Follow the gateway and configured worker logs.

Configure ONE setting in services/tts/.env:
  TTS_BREEZE_GPUS=0          # 1 GPU, 1 replica
  TTS_BREEZE_GPUS=0,1        # 2 GPUs, 2 replicas
  TTS_BREEZE_GPUS=0,1,2,3    # 4 GPUs, 4 replicas

The same start command works for any explicit GPU list. The existing Breeze
port, model, voice, and TTS_BREEZE_FAST settings are retained. Start stops other
TTS backends by default; --keep-running preserves them. Removing a GPU or
changing inference settings interrupts jobs on affected workers.
HELP
    ;;
  *) echo "ERROR: unknown command '$action'; run ./breeze.sh help." >&2; exit 2 ;;
esac
