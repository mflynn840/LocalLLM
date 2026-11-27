#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p runtime
sample="$(mktemp "$ROOT/runtime/openhands-eval-XXXXXXXX.txt")"
trap 'unlink "$sample"' EXIT

docker compose --profile openhands exec -T \
    -e "EVAL_SAMPLE_PATH=$sample" -e "EVAL_TRACE=${EVAL_TRACE:-0}" \
    openhands python - "$@" < scripts/eval-openhands-canvas.py
