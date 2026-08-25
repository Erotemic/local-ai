#!/usr/bin/env bash
set -euo pipefail

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "nvidia-smi not found" >&2
  exit 1
fi

QUERY='index,name,pci.bus_id,pcie.link.width.current,pcie.link.width.max,memory.total'
mapfile -t ROWS < <(nvidia-smi --query-gpu="$QUERY" --format=csv,noheader,nounits)

trim() {
  local x="$1"
  x="${x#"${x%%[![:space:]]*}"}"
  x="${x%"${x##*[![:space:]]}"}"
  printf '%s' "$x"
}

best_index=""
best_width=-1

for row in "${ROWS[@]}"; do
  IFS=',' read -r idx name bus width_current width_max memory <<<"$row"
  idx="$(trim "$idx")"
  width_current="$(trim "$width_current")"
  if [[ "$width_current" =~ ^[0-9]+$ ]] && (( width_current > best_width )); then
    best_width="$width_current"
    best_index="$idx"
  fi
done

case "${1:-}" in
  --best-index)
    if [[ -z "$best_index" ]]; then
      echo "Could not determine GPU PCIe widths" >&2
      exit 1
    fi
    printf '%s\n' "$best_index"
    ;;
  "")
    printf '%-5s %-24s %-14s %-8s %-8s %-10s\n' INDEX GPU PCI_BUS WIDTH MAX_WIDTH VRAM_MIB
    for row in "${ROWS[@]}"; do
      IFS=',' read -r idx name bus width_current width_max memory <<<"$row"
      printf '%-5s %-24s %-14s x%-7s x%-7s %-10s\n' \
        "$(trim "$idx")" "$(trim "$name")" "$(trim "$bus")" \
        "$(trim "$width_current")" "$(trim "$width_max")" "$(trim "$memory")"
    done
    echo "Recommended primary GPU index: ${best_index} (widest current PCIe link: x${best_width})"
    ;;
  *)
    echo "usage: $0 [--best-index]" >&2
    exit 2
    ;;
esac
