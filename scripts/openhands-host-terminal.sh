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
        python3 - "$ROOT/runtime/openhands-ssh-hosts.json" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as handle:
        hosts = json.load(handle)
except FileNotFoundError:
    hosts = {}
jetson = hosts.setdefault("jetson", {
    "target": "jetson", "port": 22,
    "description": "NVIDIA Jetson development host (USB SSH alias)",
    "metadata": {},
})
jetson.setdefault("target", "jetson")
jetson.setdefault("port", 22)
jetson.setdefault("description", "NVIDIA Jetson development host (USB SSH alias)")
metadata = jetson.setdefault("metadata", {})
metadata.setdefault("platform", "NVIDIA Jetson")
metadata.setdefault("connection", "WSL SSH alias; USB networking prepared automatically")
metadata.setdefault("home", "/home/michael")
metadata.setdefault("default_project_root", "/home/michael/Code/task1")
os.makedirs(os.path.dirname(path), exist_ok=True)
temporary = path + ".tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(hosts, handle, ensure_ascii=False, indent=2)
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY
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
