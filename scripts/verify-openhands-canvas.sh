#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p runtime
sample="$(mktemp "$ROOT/runtime/openhands-eval-XXXXXXXX.txt")"
host_fixture_dir="$(mktemp -d "${TMPDIR:-/tmp}/openhands-wsl-eval-XXXXXXXX")"
host_fixture="$host_fixture_dir/wsl-file.txt"
host_token="WSL_FILE_$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
printf 'TOKEN=%s\n' "$host_token" > "$host_fixture"
chmod 0400 "$host_fixture"
trap 'unlink "$sample"; rm -rf -- "$host_fixture_dir"' EXIT

fixture_before="$(sha256sum "$host_fixture" | cut -d' ' -f1)"
set +e
docker compose --profile openhands exec -T \
    -e "EVAL_SAMPLE_PATH=$sample" -e "EVAL_TRACE=${EVAL_TRACE:-0}" \
    -e "EVAL_WSL_SAMPLE_PATH=$host_fixture" -e "EVAL_WSL_TOKEN=$host_token" \
    -e "EVAL_WSL_USER=$(id -un)" \
    openhands python - "$@" < scripts/eval-openhands-canvas.py
eval_status=$?
set -e
fixture_after="$(sha256sum "$host_fixture" | cut -d' ' -f1)"
if [[ "$fixture_after" != "$fixture_before" ]]; then
    echo "OpenHands modified the read-only WSL evaluation fixture." >&2
    exit 1
fi
exit "$eval_status"
