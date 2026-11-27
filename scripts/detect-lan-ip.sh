#!/usr/bin/env bash
# Detect the machine's primary LAN IP address for use as BACKEND_PUBLIC_URL.
# Prefers a private RFC1918 address (10.x, 172.16-31.x, 192.168.x) and falls
# back to the first non-loopback IPv4 found. Prints one IP to stdout.
set -euo pipefail

detect_from_ip_route() {
    # ip route get 1.1.1.1 prints the source address used to reach the internet.
    local src
    src="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}')"
    if [[ -n "${src:-}" && "$src" != 127.* ]]; then
        echo "$src"
        return 0
    fi
    return 1
}

detect_from_hostname() {
    local addr
    addr="$(hostname -I 2>/dev/null || true)"
    for a in $addr; do
        case "$a" in
            10.*|172.1[6-9].*|172.2[0-9].*|172.3[0-1].*|192.168.*)
                echo "$a"
                return 0
                ;;
        esac
    done
    # Fall back to first non-loopback.
    for a in $addr; do
        case "$a" in
            127.*|::*) continue ;;
            *) echo "$a"; return 0 ;;
        esac
    done
    return 1
}

if command -v ip >/dev/null 2>&1; then
    if ip=$(detect_from_ip_route); then
        echo "$ip"
        exit 0
    fi
fi

if ip=$(detect_from_hostname); then
    echo "$ip"
    exit 0
fi

echo "Could not detect a LAN IP address." >&2
exit 1