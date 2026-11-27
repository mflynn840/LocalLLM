#!/usr/bin/env bash
# Detect whether this machine is an NVIDIA Jetson (the backend host target).
# Exits 0 (success) when running on a Jetson, 1 otherwise.
# Checks /proc/device-tree/model which on Jetson contains "NVIDIA Jetson ...".
set -euo pipefail

if [[ -r /proc/device-tree/model ]]; then
    model="$(tr '\0' ' ' < /proc/device-tree/model 2>/dev/null || true)"
    case "$model" in
        *Jetson*|*jetson*)
            echo "on-jetson"
            exit 0
            ;;
    esac
fi

# Fallback: some Jetson boards expose the model via nvidia-smi or /etc/nv_tegra_release.
if [[ -r /etc/nv_tegra_release ]]; then
    echo "on-jetson"
    exit 0
fi

echo "not-jetson"
exit 1