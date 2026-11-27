#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
llama_server="${LLAMA_SERVER_PATH:-$ROOT/llama.cpp/build/bin/llama-server}"
[[ -x "$llama_server" ]] || {
    echo "llama-server is not executable: $llama_server" >&2
    exit 1
}
help_output="$("$llama_server" --help 2>&1 || true)"
if [[ "$help_output" != *"--models-preset"* ]]; then
    echo "This llama-server build does not support router presets. Update llama.cpp or set LLAMA_SERVER_PATH to a router-capable build." >&2
    exit 1
fi
[[ -f "$ROOT/runtime/llama-router-host.ini" ]] || {
    echo "Missing generated router preset: $ROOT/runtime/llama-router-host.ini" >&2
    exit 1
}

export LLAMA_CACHE="$ROOT/models/.llama-cache"
exec "$llama_server" \
    --models-dir "$ROOT/models" \
    --models-preset "$ROOT/runtime/llama-router-host.ini" \
    --models-max 1 \
    -ngl "${GPU_LAYERS:-auto}" \
    --host 0.0.0.0 \
    --port 8080
