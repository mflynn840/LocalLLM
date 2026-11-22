#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/runtime"
log_file="$ROOT/runtime/backend-boot.log"
"$ROOT/start.sh" >"$log_file" 2>&1
result=$?
printf 'Backend boot startup exited with status %s at %s\n' "$result" "$(date --iso-8601=seconds)" >>"$log_file"
exit "$result"
