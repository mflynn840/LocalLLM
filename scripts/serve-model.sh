#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    echo "Usage: scripts/serve-model.sh MODEL_ALIAS [--quant QUANT] [--context TOKENS] [--gpu-layers N]"
}

model=""
quant=""
context="${CONTEXT_SIZE:-8192}"
gpu_layers="${GPU_LAYERS:-auto}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --quant)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            quant="$2"
            shift 2
            ;;
        --context)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            context="$2"
            shift 2
            ;;
        --gpu-layers)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            gpu_layers="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            [[ -z "$model" ]] || { echo "Only one model may be served at a time." >&2; exit 2; }
            model="$1"
            shift
            ;;
    esac
done

[[ -n "$model" ]] || { usage >&2; exit 2; }
model_path="$("$ROOT/scripts/models.sh" resolve "$model" "$quant")"
model_file="$ROOT/models/$model_path"
llama_server="${LLAMA_SERVER_PATH:-$ROOT/llama.cpp/build/bin/llama-server}"

if [[ ! -x "$llama_server" ]]; then
    echo "llama-server is not executable: $llama_server" >&2
    echo "Build llama.cpp for this host or set LLAMA_SERVER_PATH to its llama-server binary." >&2
    exit 1
fi

exec "$llama_server" \
    -m "$model_file" \
    --alias local-model \
    --fit on \
    -ngl "$gpu_layers" \
    -fa on \
    --parallel 1 \
    --ctx-size "$context" \
    --host 0.0.0.0 \
    --port 8080
