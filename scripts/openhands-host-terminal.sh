#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$ROOT/runtime/openhands-host"
SOCKET="$DIR/terminal.sock"
WORKSPACE="${1:-$ROOT}"
UNIT="localllm-openhands-host-$(printf '%s' "$ROOT" | sha256sum | cut -c1-12).service"
case "${2:-start}" in
    start)
        mkdir -p "$DIR"
        chgrp "$(id -g)" "$DIR"
        chmod 2770 "$DIR"
        if [[ -S "$SOCKET" ]] && systemctl --user is-active --quiet "$UNIT"; then
            chmod 660 "$SOCKET"
            exit 0
        fi
        systemctl --user stop "$UNIT" >/dev/null 2>&1 || true
        rm -f -- "$SOCKET"
        systemd-run --user --quiet --collect --unit "$UNIT" \
            --property=KillMode=control-group --property=TimeoutStopSec=3s \
            --setenv="PATH=$PATH" /usr/bin/python3 "$ROOT/services/openhands/host-terminal.py" \
            serve "$SOCKET" "$WORKSPACE"
        for _ in $(seq 1 30); do
            [[ -S "$SOCKET" ]] && break
            sleep 0.1
        done
        [[ -S "$SOCKET" ]] || { echo "Host terminal socket did not start." >&2; exit 1; }
        chmod 660 "$SOCKET"
        printf '%s\n' "$WORKSPACE" > "$DIR/workspace"
        ;;
    stop)
        systemctl --user stop "$UNIT" >/dev/null 2>&1 || true
        rm -f -- "$SOCKET"
        ;;
    *) echo "usage: $0 [workspace] [start|stop]" >&2; exit 2 ;;
esac
