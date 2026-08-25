#!/usr/bin/env bash
set -euo pipefail
cat >&2 <<'MSG'
init-data.sh is no longer a required first-run step.

Each service's ./setup.sh now initializes configuration first, prints the exact
host paths it will use, and creates only the directories that service needs.

Run, for example:
    cd services/triposplat
    ./setup.sh
MSG
