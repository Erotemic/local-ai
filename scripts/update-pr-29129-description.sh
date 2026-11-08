#!/usr/bin/env bash
set -euo pipefail

repo=TauCetiProject/TauCeti
pr=29129
body_file="$(mktemp)"
trap 'rm -f "$body_file"' EXIT

gh pr view "$pr" --repo "$repo" --json body --jq .body > "$body_file"
sed -i \
  -e 's/LinearPMap\.inverseCayleyCoord/Complex.inverseCayleyCoord/g' \
  -e 's/LinearPMap\.measurable_inverseCayleyCoord/Complex.measurable_inverseCayleyCoord/g' \
  -e 's/LinearPMap\.spectralPVM/IsSelfAdjoint.spectralPVM/g' \
  "$body_file"
gh pr edit "$pr" --repo "$repo" --body-file "$body_file"
