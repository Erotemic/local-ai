#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

status=0

check_cmd() {
  local cmd="$1"
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "OK      command: $cmd"
  else
    echo "MISSING command: $cmd" >&2
    status=1
  fi
}

check_cmd docker
check_cmd nvidia-smi

if command -v docker >/dev/null 2>&1; then
  if docker compose version >/dev/null 2>&1; then
    echo "OK      docker compose"
  else
    echo "MISSING docker compose plugin" >&2
    status=1
  fi
fi

while IFS= read -r env_file; do
  mode="$(stat -c '%a' "$env_file" 2>/dev/null || true)"
  if [[ -n "$mode" ]]; then
    # Warn only. Some .env files contain no secrets, but 0600 is the safe default.
    if (( (8#$mode & 8#077) != 0 )); then
      echo "WARNING $env_file is mode $mode; use chmod 600 if it contains credentials." >&2
    else
      echo "OK      permissions: $env_file ($mode)"
    fi
  fi
done < <(find services -mindepth 3 -maxdepth 3 -name .env -type f -print 2>/dev/null | sort)

for service in comfyui ace-step triposplat; do
  doctor="services/$service/scripts/doctor.sh"
  if [[ -x "$doctor" ]]; then
    echo
    echo "== $service =="
    "$doctor" || true
  fi
done

exit "$status"
