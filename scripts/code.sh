#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE_FILE="$ROOT/runtime/active-agent"
LOCK_DIR="$ROOT/runtime/opencode.lock"

if [[ ! -f "$ROOT/.env" ]]; then
    echo "Run scripts/stack.sh up --backend ... --agent opencode --model ... first." >&2
    exit 1
fi
if [[ ! -f "$MODE_FILE" || "$(<"$MODE_FILE")" != "opencode" ]]; then
    echo "OpenCode mode is not selected. Switch with scripts/stack.sh up --agent opencode ..." >&2
    exit 1
fi
if ! command -v opencode >/dev/null 2>&1; then
    echo "OpenCode CLI is required. Install it once while online, then run this script again." >&2
    exit 1
fi

mkdir -p "$ROOT/runtime"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "Another OpenCode session is already running for this workspace." >&2
    exit 1
fi
printf '%s\n' "$$" > "$LOCK_DIR/pid"
cleanup() {
    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR"
}
trap cleanup EXIT INT TERM

set -a
# shellcheck disable=SC1090
. "$ROOT/.env"
set +a
export OPENCODE_CONFIG="$ROOT/config/opencode.json"

opencode "$@"
