#!/bin/sh
set -eu

ip route del default
exec su-exec nginx nginx -g 'daemon off;'
