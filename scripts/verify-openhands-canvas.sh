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
cleanup() {
    unlink "$sample"
    if [[ "${EVAL_KEEP_WSL_FIXTURE:-0}" == "1" ]]; then
        echo "Kept WSL fixture at $host_fixture"
    else
        rm -rf -- "$host_fixture_dir"
    fi
}
trap cleanup EXIT

docker compose --profile openhands exec -T \
    -e "EVAL_SAMPLE_PATH=$sample" -e "EVAL_TRACE=${EVAL_TRACE:-0}" \
    -e "EVAL_KEEP_CONVERSATION=${EVAL_KEEP_CONVERSATION:-0}" \
    -e "EVAL_WSL_SAMPLE_PATH=$host_fixture" -e "EVAL_WSL_TOKEN=$host_token" \
    -e "EVAL_WSL_USER=$(id -un)" \
    openhands python - "$@" < scripts/eval-openhands-canvas.py
