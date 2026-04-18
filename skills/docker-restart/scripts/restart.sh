#!/usr/bin/env bash
# Restart the OpenClaw container from inside itself via Docker socket.
# Also clears stale lock files before restarting.
set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-openclaw-xrt9-openclaw-1}"

echo "Clearing stale lock files..."
find /data/.openclaw -name "*.lock" -delete 2>/dev/null || true

echo "Sending restart signal to container: $CONTAINER_NAME"

# Use the Docker socket to restart our own container
# Install curl if docker CLI isn't available
if command -v docker &>/dev/null; then
    docker restart "$CONTAINER_NAME"
else
    # Use Docker Engine API directly via socket
    curl -s --unix-socket /var/run/docker.sock \
        -X POST "http://localhost/containers/$CONTAINER_NAME/restart?t=10" \
        -o /dev/null -w "HTTP %{http_code}"
    echo ""
fi

echo "Restart signal sent. Connection will drop momentarily."
