#!/usr/bin/env bash
# update_token.sh — atomically update the Questrade refresh token.
#
# Usage: update_token.sh <new_refresh_token>
#
# What it does:
#   1. Writes the new token to .env (QUESTRADE_REFRESH_TOKEN=...)
#   2. Deletes the stale questrade_token.json cache (forces a fresh refresh
#      on next API call, using the new value from .env)
#   3. The stock-concierge-reload.path systemd watcher will notice the .env
#      change and automatically restart stock-concierge.service.
#
# This is the ONLY correct way to install a new Questrade refresh token.
# The openclaw chat agent must shell out to this script — writing a memory
# file is not sufficient.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <new_refresh_token>" >&2
    exit 2
fi

NEW_TOKEN="$1"
ENV_FILE="/docker/openclaw-xrt9/.env"
CACHE_FILE="/docker/openclaw-xrt9/data/.openclaw/questrade_token.json"
LOCK_FILE="${CACHE_FILE}.lock"

# Basic sanity: Questrade refresh tokens are 32–34 chars, alphanumeric + -_
if ! [[ "$NEW_TOKEN" =~ ^[A-Za-z0-9_-]{28,40}$ ]]; then
    echo "ERROR: '$NEW_TOKEN' does not look like a Questrade refresh token" >&2
    exit 1
fi

if [[ ! -w "$ENV_FILE" ]]; then
    echo "ERROR: cannot write $ENV_FILE" >&2
    exit 1
fi

# Serialise against any running refresh so we don't race with it.
exec 9>"$LOCK_FILE"
flock -x -w 10 9 || { echo "ERROR: could not acquire $LOCK_FILE" >&2; exit 1; }

# 1. Update .env in-place (create backup .env.bak once per day for safety)
BACKUP="${ENV_FILE}.bak-$(date +%Y%m%d)"
[[ -f "$BACKUP" ]] || cp "$ENV_FILE" "$BACKUP"

if grep -q '^QUESTRADE_REFRESH_TOKEN=' "$ENV_FILE"; then
    sed -i "s|^QUESTRADE_REFRESH_TOKEN=.*|QUESTRADE_REFRESH_TOKEN=${NEW_TOKEN}|" "$ENV_FILE"
else
    printf 'QUESTRADE_REFRESH_TOKEN=%s\n' "$NEW_TOKEN" >> "$ENV_FILE"
fi

# 2. Wipe stale cache. Next refresh will use QUESTRADE_REFRESH_TOKEN from .env.
rm -f "$CACHE_FILE"

# 3. Touch .env to make sure the .path watcher fires even if sed kept mtime.
touch "$ENV_FILE"

echo "OK: wrote new token to $ENV_FILE, wiped $CACHE_FILE"
echo "    stock-concierge-reload.path will restart the daemon shortly."
