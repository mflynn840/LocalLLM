#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing .env; run ./install.sh first." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

workspace="${WORKSPACE_DIR:-$ROOT/workspace}"
if [[ "$workspace" != /* ]]; then
    workspace="$ROOT/$workspace"
fi
workspace="$(realpath -m "$workspace")"
root="$(realpath -e "$ROOT")"
if [[ ! -d "$workspace" ]]; then
    echo "OpenHands workspace does not exist: $workspace" >&2
    exit 1
fi
case "$workspace/" in
    "$root/"*) ;;
    *)
        echo "For safety, OpenHands workspaces must be inside the project directory ($root)." >&2
        exit 1
        ;;
esac

host_gid="$(id -g)"
if [[ "${OPENHANDS_WORKSPACE_GID:-$host_gid}" != "$host_gid" ]]; then
    echo "OPENHANDS_WORKSPACE_GID must match this user's primary group ID ($host_gid)." >&2
    exit 1
fi

mkdir -p "$ROOT/runtime/openhands-npm-cache" "$ROOT/runtime/openhands-npm-global"
chgrp "$host_gid" "$ROOT/runtime/openhands-npm-cache" "$ROOT/runtime/openhands-npm-global"
chmod 2770 "$ROOT/runtime/openhands-npm-cache" "$ROOT/runtime/openhands-npm-global"
manifest="$ROOT/runtime/mcp-packages.json"
if [[ ! -e "$manifest" ]]; then
    printf '[]\n' > "$manifest"
fi
chgrp "$host_gid" "$manifest"
chmod 640 "$manifest"

find "$workspace" -xdev \
    \( -type d \( -name .git -o -name .venv -o -name node_modules -o -name models -o -name runtime -o -name build \) \
       -o -type f \( -name .env -o -name '.env.*' \) \) -prune -o \
    \( -type f -o -type d \) -exec chgrp "$host_gid" {} + \
    -type d -exec chmod g+rwx,g+s {} + -o \
    -type f -exec chmod g+rw {} +

if [[ -f "$ROOT/.env" ]]; then
    chmod 600 "$ROOT/.env"
fi
