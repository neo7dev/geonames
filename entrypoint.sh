#!/bin/bash
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Create group and user with the requested IDs
if ! getent group appuser > /dev/null 2>&1; then
    groupadd -g "$PGID" appuser
else
    groupmod -g "$PGID" appuser
fi

if ! id appuser > /dev/null 2>&1; then
    useradd -m -u "$PUID" -g appuser -s /bin/bash appuser
else
    usermod -u "$PUID" -g appuser appuser
fi

chown -R appuser:appuser /app

exec gosu appuser "$@"
