---
name: docker-restart
description: Restart the OpenClaw container. Clears stale lock files and sends a restart signal via Docker socket. Triggers on requests to restart, reboot, or reset OpenClaw/Yuri.
---

# Docker Restart Skill

Restarts the OpenClaw container from within itself using the Docker socket.

## Usage

```bash
bash {baseDir}/scripts/restart.sh
```

## What It Does

1. Clears stale `.lock` files in `/data/.openclaw/`
2. Sends a restart signal to the container via Docker socket
3. The container restarts cleanly (connection drops momentarily)

## When to Use

- Tony says "restart" or "reboot"
- Session file locked errors
- OpenClaw becomes unresponsive
- After configuration changes that need a restart

## Important

- The restart will disconnect the current conversation
- Telegram/WhatsApp channels will reconnect automatically after ~20 seconds
- Warn Tony that the connection will drop before running the restart
