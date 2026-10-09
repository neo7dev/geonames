#!/usr/bin/env bash
# Set the per-connection download speed of slow-download-server (limit_rate in nginx.conf).
# Usage: set-download-speed.sh [RATE]   e.g. 500k, 2m, 0 (unlimited). Prompts when RATE is omitted.
set -euo pipefail

CONF="$(cd "$(dirname "$0")/.." && pwd)/nginx.conf"

current=$(sed -nE 's/^[[:space:]]*limit_rate[[:space:]]+([^;]+);.*/\1/p' "$CONF")
rate="${1:-}"

if [ -z "$rate" ]; then
    echo "Current download speed: ${current} per connection"
    echo "Enter a speed in bytes per second: 500k = 500 KB/s, 2m = 2 MB/s, 0 = unlimited"
    read -rp "New speed [${current}]: " rate
    rate="${rate:-$current}"
fi

if ! [[ "$rate" =~ ^[0-9]+[kKmM]?$ ]]; then
    echo "Invalid speed '${rate}'. Use a number with an optional k or m suffix, e.g. 500k or 2m." >&2
    exit 1
fi

# Rewrite in place (same inode): slow-download-server bind-mounts this single file,
# and a replaced file (sed -i, editors that save via rename) would not reach the container.
tmp=$(mktemp)
sed -E "s/^([[:space:]]*limit_rate[[:space:]]+)[^;]+;/\1${rate};/" "$CONF" > "$tmp"
cat "$tmp" > "$CONF"
rm -f "$tmp"

echo "Download speed set to ${rate} per connection. slow-download-server reloads within a few seconds."
