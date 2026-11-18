#!/usr/bin/env bash
# Repository-root convenience entry point; provisioning remains in setup.sh.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$ROOT_DIR/services/tts"
action="${1:-help}"
if (( $# )); then shift; fi
case "$action" in
  setup) TTS_ACTIVE_BACKEND=breeze-tts2-dual exec "$SERVICE_DIR/setup.sh" "$@" ;;
  start) TTS_ACTIVE_BACKEND=breeze-tts2-dual exec "$SERVICE_DIR/start.sh" "$@" ;;
  single) TTS_ACTIVE_BACKEND=breeze-tts2 exec "$SERVICE_DIR/start.sh" "$@" ;;
  stop) exec "$SERVICE_DIR/compose.sh" stop breeze-gateway breeze-worker-0 breeze-worker-1 breeze-tts2 ;;
  status) exec "$SERVICE_DIR/compose.sh" ps -a breeze-gateway breeze-worker-0 breeze-worker-1 breeze-tts2 ;;
  logs) exec "$SERVICE_DIR/compose.sh" logs --tail=100 -f breeze-gateway breeze-worker-0 breeze-worker-1 ;;
  help|-h|--help)
    cat <<'HELP'
Usage: ./breeze.sh setup|start|stop|status|logs|single
  setup   Run the normal TTS setup, provisioning Breeze and building its gateway.
  start   Run two replicas behind the existing Breeze port (GPUs 0 and 1 by default).
  stop    Stop Breeze in either mode; keep models, voices and cached audio.
  status  Show dual-GPU services and the single-GPU Breeze service.
  logs    Follow the gateway and both worker logs.
  single  Switch back to the existing single-GPU Breeze configuration.

Configure services/tts/.env:
  TTS_BREEZE_DUAL_GPU_0=0
  TTS_BREEZE_DUAL_GPU_1=1
  TTS_BREEZE_FAST=0           # optional CUDA graphs: 1; longer warmup, more VRAM
  TTS_BREEZE_PORT=11441       # retain your existing port if already configured

Start/single stop other TTS backends by default, like services/tts/start.sh.
Pass --keep-running to preserve other backends; both Breeze modes share a port
and always replace one another. Switching modes loses unfinished server jobs.
HELP
    ;;
  *) echo "ERROR: unknown command '$action'; run ./breeze.sh help." >&2; exit 2 ;;
esac
