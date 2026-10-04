#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"

if [[ ! -f "$ROOT_DIR/.env" || ! -f "$SERVICE_DIR/.env" ]]; then
  echo "ERROR: service configuration is not initialized." >&2
  echo "Run ./setup.sh from $SERVICE_DIR" >&2
  exit 1
fi

uv run "$ROOT_DIR/scripts/local_ai.py" model check qwen-0.6-customvoice-q8-gguf --service-dir "$SERVICE_DIR"

set -a
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
# shellcheck disable=SC1091
source "$SERVICE_DIR/.env"
set +a

cd "$SERVICE_DIR"
docker compose config --quiet
docker compose pull qwentts
docker compose up -d --no-build --pull never qwentts

container="ai-voice-qwentts"
port="${TTS_QWENTTS_PORT:-11436}"
bind_address="${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}"
health_host="$bind_address"
case "$health_host" in
  0.0.0.0|::|"[::]") health_host="127.0.0.1" ;;
esac
health_url="http://${health_host}:${port}/health"
timeout_s="${TTS_QWENTTS_START_TIMEOUT:-180}"

if ! [[ "$timeout_s" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: TTS_QWENTTS_START_TIMEOUT must be a positive integer; got '$timeout_s'." >&2
  exit 2
fi

echo "Waiting for qwentts to become ready: $health_url (timeout ${timeout_s}s)"
deadline=$((SECONDS + timeout_s))
while (( SECONDS < deadline )); do
  if curl -fsS --max-time 3 "$health_url" >/dev/null 2>&1; then
    echo "qwentts ready: http://${health_host}:${port}"
    exit 0
  fi

  state="$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || true)"
  case "$state" in
    running)
      ;;
    created|restarting|exited|dead|removing|paused|"")
      echo "ERROR: qwentts did not remain running while starting (state=${state:-missing})." >&2
      docker compose ps -a qwentts >&2 || true
      echo "--- qwentts logs ---" >&2
      docker compose logs --tail=200 qwentts >&2 || true
      exit 1
      ;;
  esac
  sleep 2
done

echo "ERROR: qwentts did not become healthy within ${timeout_s}s." >&2
docker compose ps -a qwentts >&2 || true
echo "--- qwentts logs ---" >&2
docker compose logs --tail=200 qwentts >&2 || true
exit 1
