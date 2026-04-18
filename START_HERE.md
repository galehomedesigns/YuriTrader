# START HERE — OpenClaw migration to GX10

This file exists for the moment you land on GX10 and want to execute the migration. It explains what's already in place, what isn't, and what to do next. After cutover, this file becomes obsolete — delete it then.

## Where you are

```
hostname     # gx10-087b
whoami       # tonygale
pwd          # cd ~/openclaw first
```

If `hostname` doesn't return `gx10-087b`, you are on the wrong host. Stop.

## What's already on GX10 (done)

```
~/openclaw/
├── .env              ← copied from VPS, tunnel vars stripped, mode 600
├── .env.example      ← sanitized template for git
└── docs/
    ├── CLAUDE.md
    ├── ARCHITECTURE.md
    ├── BUILD.md
    └── OPERATIONS.md
```

All four docs are reachable as markdown links from each other. Read [docs/CLAUDE.md](docs/CLAUDE.md) first — it's the entry point for any future Claude session that lands here.

## What is NOT on GX10 yet (your job)

Everything in the diagram below is missing:

```
~/openclaw/
├── .venv/            ← Python virtualenv, not created
├── skills/           ← code, not migrated
├── projects/         ← agent personas, not migrated
├── state/            ← runtime state, not migrated
├── logs/             ← log dirs, not created
├── memory/           ← agent memory, not migrated
├── systemd/          ← unit files, not authored
├── crontab.gx10      ← user crontab, not authored
└── pyproject.toml    ← Python deps, not authored
```

Plus on the system side:
- No Tailscale Funnel set up for `tv-webhook`
- No systemd user units installed or enabled
- No user crontab installed
- Playwright Chromium not installed

## Execution order

Follow [docs/BUILD.md](docs/BUILD.md) start to finish. The shortcut summary, in order:

1. **Verify prereqs** — Python 3.11+, git, ollama active, Tailscale up. (BUILD §0)
2. **Get the code on GX10.** Either:
   - Clone the repo (if it's been pushed to a remote already), OR
   - Rsync `skills/`, `projects/`, and any wrapper scripts from the VPS:
     ```bash
     rsync -avz --exclude='__pycache__' --exclude='*.pyc' \
         srv1378550:/docker/openclaw-xrt9/data/skills/ \
         ~/openclaw/skills/
     rsync -avz \
         srv1378550:/docker/openclaw-xrt9/data/.openclaw/workspace/projects/ \
         ~/openclaw/projects/
     ```
3. **Create the venv and install deps** (BUILD §2). You'll need to author `pyproject.toml` from the VPS's import surface — `pip freeze` on the VPS is a starting point but cull what's not actually used.
4. **Install Playwright Chromium** (BUILD §3).
5. **`.env` is already in place** — skip BUILD §4, but verify nothing's missing:
   ```bash
   grep -E '^[A-Z_]+=' ~/openclaw/.env | wc -l   # should be ~55
   ```
6. **Migrate state from VPS** — token, browser profile, concierge SQLite (BUILD §5):
   ```bash
   rsync -avz \
       srv1378550:/docker/openclaw-xrt9/data/.openclaw/questrade_token.json \
       srv1378550:/docker/openclaw-xrt9/data/.openclaw/browser/ \
       srv1378550:/docker/openclaw-xrt9/data/.openclaw/questrade_browser_state/ \
       srv1378550:/docker/openclaw-xrt9/data/skills/trading-arena/concierge/concierge_state.db \
       ~/openclaw/state/
   ```
7. **Author systemd unit files** in `~/openclaw/systemd/` (one per daemon: `tv-webhook`, `stock-concierge`, `trading-concierge`, optionally `trading-agent`, `trading-arena`). Template in [docs/OPERATIONS.md § How to add a new daemon](docs/OPERATIONS.md#how-to-add-a-new-daemon-systemd-user-unit). Install per BUILD §6.
8. **Author `crontab.gx10`** — copy the entry list from BUILD §7, swap paths from `/docker/openclaw-xrt9/data/...` to `/home/tonygale/openclaw/...`, then `crontab ~/openclaw/crontab.gx10`.
9. **Tailscale Funnel for tv-webhook** (BUILD §8).
10. **Verify Ollama model aliases** exist (BUILD §9). Create with `ollama cp` if missing.
11. **Smoke tests** (BUILD §10) — must all pass before cutting over.
12. **Cut over from VPS** (BUILD §11) — only after smoke tests are green.

## Critical "do not skip" items during this migration

- **Path rewriting.** Every wrapper script on the VPS contains `/data/skills/...` or `/docker/openclaw-xrt9/data/...`. On GX10 these become `/home/tonygale/openclaw/...`. Grep before you start:
  ```bash
  grep -rln '/data/\|/docker/openclaw-xrt9' ~/openclaw/skills/ ~/openclaw/projects/
  ```
  Either rewrite all hits, or introduce an `OPENCLAW_ROOT` env var and reference it consistently.

- **`docker exec openclaw-xrt9-openclaw-1`** appears in many VPS wrappers. On GX10 it becomes `~/openclaw/.venv/bin/python` directly. Same grep pattern.

- **The VPS is still running.** While you migrate, the VPS is making real Telegram posts and (if live trading is on) real trades. Don't let GX10 also start firing until you cut over per BUILD §11. If both stacks run at once and both poll the same Telegram bot token, you get `409 Conflict` errors and concierge commands break.

- **Questrade refresh tokens are single-use.** If you run `questrade.py portfolio` on GX10 and it succeeds, it just burned the VPS's refresh token. The VPS will fail on its next refresh. This is fine if you're committing to the cutover — but don't toggle back and forth.

- **Live trading state.** Before stopping the VPS for cutover, check open positions:
  ```bash
  ssh srv1378550 'docker logs openclaw-xrt9-trading-agent-1 --tail 30'
  ssh srv1378550 'docker logs openclaw-xrt9-trading-arena-1 --tail 30'
  ```
  Trap Catcher and (if live) The Reverter occasionally hold positions. Let them exit naturally or close manually.

- **Don't run `general:latest` + `coder:latest` simultaneously.** 132 GB > 125 GB unified memory. It will stall. See [docs/OPERATIONS.md § Model selection](docs/OPERATIONS.md#model-selection-guide).

## When you're done

- Delete this file: `rm ~/openclaw/START_HERE.md`
- Commit `.env.example`, `pyproject.toml`, `crontab.gx10`, `systemd/`, `skills/`, `projects/`, `docs/` to git
- Confirm `.gitignore` excludes `.env`, `state/`, `logs/`, `memory/`, `.venv/`
- Push to a private remote
- [docs/CLAUDE.md](docs/CLAUDE.md) becomes the entry point for every future Claude session

## If something goes wrong mid-migration

- The VPS is still running. Worst case: stop GX10 work, the VPS keeps doing its job. No data loss.
- The hard-failure mode is breaking the Questrade refresh token chain. If that happens: regenerate at questrade.com → API centre, update both `~/openclaw/.env` AND `~/openclaw/state/questrade_token.json` on GX10, AND `/docker/openclaw-xrt9/.env` AND `/docker/openclaw-xrt9/data/.openclaw/questrade_token.json` on the VPS. Keep them in lockstep until cutover is final.

Good luck.
