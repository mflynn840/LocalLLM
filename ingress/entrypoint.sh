#!/bin/sh
set -eu

: "${OPENHANDS_LAN_AUTH_USER:?Set OPENHANDS_LAN_AUTH_USER in .env}"
: "${OPENHANDS_LAN_AUTH_PASSWORD:?Set OPENHANDS_LAN_AUTH_PASSWORD in .env}"
case "$OPENHANDS_LAN_AUTH_USER" in
  *[!A-Za-z0-9._-]*|'') echo "Invalid OpenHands LAN auth username." >&2; exit 1 ;;
esac
if [ "${#OPENHANDS_LAN_AUTH_PASSWORD}" -lt 24 ]; then
  echo "OpenHands LAN auth password must contain at least 24 characters." >&2
  exit 1
fi

umask 077
printf '%s\n' "$OPENHANDS_LAN_AUTH_PASSWORD" \
  | htpasswd -B -i -c /tmp/openhands.htpasswd "$OPENHANDS_LAN_AUTH_USER"
chmod 644 /tmp/openhands.htpasswd

ip route del default
exec su-exec nginx nginx -g 'daemon off;'
