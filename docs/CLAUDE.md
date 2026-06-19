# OpenClaw on GX10 — Claude entry point

You are on **`gx10-087b`** (ARM64, NVIDIA GPU, 125 GB unified memory, Tailscale `100.84.217.85`). This is **the** host — no VPS, no tunnels, no Docker. Everything runs natively as `tonygale`-owned Python + systemd user units + user crontab.

If `hostname` doesn't return `gx10-087b`, stop and reorient. These docs assume you are on GX10.

> **Session briefing hook:** a `SessionStart` hook in `.claude/settings.local.json` (machine-local, gitignored) runs [`skills/trading-arena/opening_agent/session_briefing.sh`](../skills/trading-arena/opening_agent/session_briefing.sh) to print the Opening-Power focus + live tunnel/broker/market status at the start of each session.

## What this stack is (in one paragraph)

A personal agent stack that trades (paper arena of 10 strategy bots + live Kraken concierge + Questrade concierge), monitors health, crawls public-procurement tenders, processes receipts, and reports to Telegram. Most scheduled work runs from the user crontab as plain Python, reading/writing Supabase for shared state. Ollama runs locally on this host and serves every LLM call.

## Directory map

```
~/openclaw/
├── docs/                   ← you are here
│   ├── CLAUDE.md
│   ├── ARCHITECTURE.md     ← what exists and why
│   ├── BUILD.md            ← zero → running
│   └── OPERATIONS.md       ← daily runbook + incident playbooks
├── skills/                 ← code (git-tracked)
│   ├── medic/              ← health check → Telegram
│   ├── trading/            ← market data, alerts, dashboards, Questrade auto-trader
│   ├── trading-arena/      ← 10 paper-trading bots + overseer + concierges
│   ├── procurement/        ← tender crawler
│   ├── receipts/           ← receipt processor
│   ├── questrade/          ← Questrade API client
│   ├── tradingview/        ← webhook receiver
│   └── ...
├── projects/               ← agent markdown personas (git-tracked)
├── state/                  ← runtime state (gitignored, rsync'd from VPS on first build)
│   ├── questrade_token.json
│   ├── concierge_state.db
│   └── browser/            ← Playwright Chromium profile
├── logs/                   ← per-skill log dirs (gitignored)
├── memory/                 ← agent memory (gitignored)
├── systemd/                ← unit files, installed to ~/.config/systemd/user/
├── crontab.gx10            ← the user crontab to install
├── .env                    ← secrets (gitignored)
├── .env.example            ← committed
└── pyproject.toml          ← Python deps
```

## Commands you'll use constantly

```bash
# Health snapshot — run first when asked "is anything broken?"
~/openclaw/skills/medic/scripts/medic.py report

# Run any skill manually
~/openclaw/.venv/bin/python ~/openclaw/skills/trading-arena/arena_runner.py

# Ollama sanity
curl -s http://localhost:11434/api/tags | python3 -m json.tool | head -40

# User systemd units (concierges, tv-webhook)
systemctl --user status stock-concierge trading-concierge tv-webhook

# User cron
crontab -l

# Live-trading kill switch
~/openclaw/skills/trading-arena/kill_live_trading.sh
```

## Top-10 gotchas (read before touching anything)

1. **Default model is `quick:latest` (qwen3.5:35b).** Don't change this for short agent turns. `gemma:latest` is confirmed too slow and ignores length limits (it'll burn your 120s timeout). See [OPERATIONS.md § Model selection](OPERATIONS.md#model-selection-guide).

2. **Always pass `"think": false` and `"keep_alive": "1h"` to qwen3.5 calls.** Thinking mode is ~10× latency for short prompts. Any new code that talks to Ollama and skips this will look broken but actually just be slow. Reference: `skills/trading-arena/concierge/advisor.py`.

3. **Never run `general:latest` + `coder:latest` at the same time.** 81 GB + 51 GB = 132 GB, exceeds the 125 GB unified memory budget. Will stall. `quick + coder` (74 GB) and `quick + gemma` (42 GB) are safe.

4. **Live trading has two gates.** `KRAKEN_ALLOW_TRADING=true` AND `LIVE_TRADING_ENABLED=true`. If either is false, trades route to paper. Only `the-reverter` is in `LIVE_TRADING_BOTS` by default. Kill switch: `skills/trading-arena/kill_live_trading.sh`.

5. **Questrade refresh tokens are single-use.** Each refresh burns the previous token. If a refresh fails mid-way, the old token is already dead — go to questrade.com → Settings → API centre → generate new, update `.env` **and** `state/questrade_token.json`.

6. **The `openclaw` Docker container from the old VPS does not exist here.** Don't `docker exec openclaw-xrt9-openclaw-1 ...`. That was the bypass pattern for the VPS exec-approval gate. On GX10 everything runs as native Python; cron calls `~/openclaw/.venv/bin/python ...` directly.

7. **Trading containers don't exist either.** `trading-agent` (Questrade continuous loop) and `trading-arena` (10 bots) are now `systemd --user` units running the same Python scripts.

8. **`arena_runner.py` uses Supabase for all trade state.** Losing the local SQLite concierge DB is survivable; losing the Supabase `arena_trades` table is not. Back it up.

9. **TradingView webhook must be publicly reachable.** It runs on `127.0.0.1:8089`. Public exposure is via **Tailscale Funnel** (see [BUILD.md § tv-webhook public reach](BUILD.md#tv-webhook-public-exposure)). If alerts stop firing and the service is up, suspect Funnel.

10. **Don't run `general + coder` simultaneously** — repeated intentionally because you will forget.

## When a user says "check the agents"

Don't investigate from scratch. See [OPERATIONS.md § Health check routine](OPERATIONS.md#health-check-routine). Cadence:

1. Ollama: `systemctl is-active ollama` + `curl http://localhost:11434/api/tags`
2. systemd user units: `systemctl --user status stock-concierge trading-concierge tv-webhook`
3. Cron last-runs: `tail -20 ~/openclaw/logs/*/cron.log`
4. Medic: `~/openclaw/skills/medic/scripts/medic.py report`
5. Arena bots: `tail -30 ~/openclaw/logs/trading-arena/arena.log` — all 10 should show `ACTIVE`

## Don't

- Don't claim you can't see the agents. You are on the host that runs them. `hostname` = `gx10-087b`.
- Don't add the old Hostinger tunnel workarounds back. `QUESTRADE_AUTH_URL`, `QUESTRADE_API_PORT_MAP`, and `_api_base_url` rewriters were IP-block workarounds that **do not apply here**. If you see them referenced in code, delete them.
- Don't restart concierge services casually — they hold Telegram long-poll offsets and may have open positions. Check state first.
- Don't confuse "OpenClaw" (this stack) with Claude Code features. This has its own cron, its own agent personas in `projects/`, and its own Telegram channels. Unrelated to Claude Code's tooling.

## Related docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — the 9 projects, 10 trading bots, TAY framework, state model, and why it's shaped this way
- [BUILD.md](BUILD.md) — zero → running on a fresh GX10
- [OPERATIONS.md](OPERATIONS.md) — daily runbook, incident playbooks, model selection, how to add a new skill
