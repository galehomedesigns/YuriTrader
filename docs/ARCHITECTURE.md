# Architecture

What lives in this stack, how it fits together, and why the shape is what it is. For runbooks see [OPERATIONS.md](OPERATIONS.md). For rebuild steps see [BUILD.md](BUILD.md).

## Philosophy: native, not containerized

The previous incarnation on Hostinger VPS ran in Docker: four containers, an OpenClaw gateway image, and an agent runtime with its own cron + exec-approval gate. That architecture existed for the VPS's constraints (shared environment, amd64-only image, Hostinger's Questrade IP block requiring SSH tunnels). **None of those constraints apply on GX10.**

On GX10 everything runs as plain Python under the `tonygale` user:

- **No Docker** — GX10 is aarch64, the OpenClaw image is amd64-only, and we don't need container isolation on a single-tenant box.
- **No OpenClaw agent runtime gateway** — the gateway's main value was scheduling + channels, and we'd already moved scheduling to host cron on the VPS (2026-04-17). Channels are just Telegram REST calls.
- **No SSH tunnels** — Ollama is local; Questrade isn't IP-blocked on residential IPs.
- **One Python venv** at `~/openclaw/.venv/`.
- **systemd user units** for long-running daemons (concierges, tv-webhook).
- **User crontab** for scheduled work.
- **Ollama native** for every LLM call.

This is simpler, faster to boot, and drops four categories of tech debt at once.

## Top-level components

```
┌──────────────────────────────────────────────────────────────┐
│                        GX10 (gx10-087b)                      │
│                                                              │
│  Ollama (native) ──┬──► quick:latest  (default, 23 GB)      │
│                    ├──► coder:latest  (code tasks, 51 GB)   │
│                    ├──► general:latest (rare, 81 GB)        │
│                    └──► nemotron:70b (evaluating, 42 GB)    │
│                                                              │
│  systemd --user:                                             │
│    • stock-concierge     (Questrade HITL Telegram bot)      │
│    • trading-concierge   (Kraken HITL Telegram bot)         │
│    • tv-webhook          (TradingView alerts on :8089)      │
│    • trading-agent       (Questrade autonomous loop, opt-in)│
│    • trading-arena       (10 paper bots, market hours)      │
│                                                              │
│  crontab (user):                                             │
│    • medic, procurement, receipts, overseer, trading briefs │
│    • arena_scan, watchlist, tv_focus (market hours)         │
│                                                              │
│  State:                                                      │
│    • ~/openclaw/state/  (tokens, browser profile, SQLite)   │
│    • Supabase           (arena_trades, tenders, signals)    │
│                                                              │
│  External:                                                   │
│    • Tailscale Funnel → tv-webhook  (TradingView alerts in)│
│    • Telegram Bot API               (all outbound alerts)   │
│    • Questrade REST + Playwright    (stocks)                │
│    • Kraken REST                    (crypto)                │
│    • Firecrawl, Finnhub, Alpha Vantage, Twelve Data         │
└──────────────────────────────────────────────────────────────┘
```

## Agent projects (what "agents" means here)

The nine projects under `projects/` are **markdown persona files** that describe what a specific agent role does. They are loaded as system prompts when a skill invokes Ollama with a particular role in mind. They are not separate processes.

| Project | What it does | Sub-agent personas |
|---|---|---|
| **trading-arena** | Coordinates the 10 strategy bots + Overseer | `trading-overseer` |
| **stock-monitor** | Market monitoring, trend analysis, news/social signals, alerts | `market-monitor`, `trend-analyzer`, `news-analyzer`, `social-monitor`, `auto-trader`, `task-news`, `task-social` |
| **procurement-pipeline** | Tender intelligence: crawl → enrich → evaluate → publish | `crawler`, `enricher`, `evaluator`, `network_intelligence`, `newsletter-writer`, `social-poster` |
| **content-pipeline** | Research → write → distribute thought-leadership content | `topic-researcher`, `content-writer`, `platform-formatter` |
| **email-intelligence** | Triage inbox, extract tasks, draft responses | `email-triager`, `task-extractor`, `response-drafter` |
| **expense-pipeline** | Receipts → categorization → reports | `receipt-processor`, `expense-categorizer`, `report-generator` |
| **investor-pipeline** | Compile business data, update pitch, build demos | `data-compiler`, `deck-updater`, `demo-builder` |
| **youtube-strategy** | Channel scanning, transcript fetching, strategy extraction | `channel-scanner`, `transcript-fetcher`, `strategy-analyzer` |

Of these, **trading-arena, procurement-pipeline, expense-pipeline, youtube-strategy, and stock-monitor** have real cron-driven execution. The others exist as personas but aren't scheduled.

## Skills: the actual code

Skills live under `skills/<name>/` and follow this convention:

```
skills/<name>/
├── SKILL.md          ← human description of purpose
├── scripts/          ← executable Python + shell wrappers
│   ├── <main>.py
│   └── <name>_cron.sh   ← the thing crontab calls
└── logs/             ← output goes here (gitignored)
```

Active skills with scheduled work:

| Skill | Entry script | Schedule | Output |
|---|---|---|---|
| **medic** | `scripts/medic.py` | 7 AM ET Mon–Fri (full), 11 PM ET daily (report-only) | Telegram health report + dashboard |
| **procurement** | `scripts/crawl.py` | 3 AM ET every 2 days | Supabase `tenders` + Telegram summary |
| **trading-arena** | `arena_runner.py` | Every 5 min 9:30–16:00 ET Mon–Fri | Paper trades → Supabase + Telegram |
| **trading-arena/overseer** | `overseer/game_plan.py` | 9 AM ET Mon–Fri | Pre-market Telegram brief |
| **trading-arena/overseer** | `overseer/autopsy.py` | 4:30 PM ET Mon–Fri | Daily trade review |
| **trading-arena/overseer** | `overseer/super_prompt.py` | Friday 6 PM ET | Weekly bot improvement suggestions |
| **trading** | `trading_news_cron.sh` | Every 15 min 24/7 | Silent unless alerts fire |
| **trading** | `trading_premarket_cron.sh` | 9 AM ET Mon–Fri | Portfolio + market brief |
| **trading** | `trading_postmarket_cron.sh` | 4:30 PM ET Mon–Fri | Portfolio + trade history |
| **trading** | `dashboard_gen.py` | Noon ET Mon–Fri | Static dashboard regen (no Telegram) |
| **receipts** | `receipts_cron.sh` | 11 PM ET daily | Silent unless receipts processed |
| **questrade** | `questrade.py portfolio` | 2:55 & 10:55 UTC daily | Keeps OAuth token fresh |

## The Trading Arena — 10 bots + TAY framework

Ten strategy bots run every 5 minutes during market hours. Each follows the **TAY framework**: a trade fires only when **all three** conditions pass:

- **T**rend — market is in a regime the strategy can exploit
- **A**rea of value — price is at a level the strategy cares about
- **Y**es trigger — an entry signal confirms

This is strict confluence over single-signal fire. Logged per-trade in Supabase so the Overseer can rank which T/A/Y components win.

| Bot | Strategy | Trend | Area | Trigger | Notes |
|---|---|---|---|---|---|
| **Momentum Hunter** | Momentum breakout | Price > EMA50 + ADX > 20 | +1% intraday move | Vol > 2× + RSI 50–75 + MACD↑ | TAY v1 |
| **Trend Rider v2** | Pullback following | 200 MA filter + EMA21 > EMA50 | S/R + 50 EMA confluence | Hammer or Bullish Engulfing | TAY v2 (2026-04-12), uses ATR stops |
| **The Reverter v2** | Mean reversion | Range-bound (ADX < 20) | Horizontal support OR BB lower | Bullish Engulfing or Hammer | **Only live-trading-eligible bot** (Kraken crypto) |
| **Nano Sniper** | EMA scalping | EMA hierarchy 8>21>50>200 | Above VWAP | Vol > 1.5× + RSI 45–75 + MACD↑ | TAY v1 |
| **Squeeze Breaker v2** | Bollinger squeeze breakout | BB bandwidth < 0.03 | Horizontal resistance OR BB upper | Vol surge + MACD bullish | Best trend component historically |
| **Flag Rider** | Flag pattern | +2% impulse + vol | 5-bar tight consolidation | Breakout above flag + VWAP + MACD↑ | TAY v1 |
| **Trap Catcher** | False-breakout reversal | Weakening trend (ADX < 30) | RSI reverting from extreme | Vol declining + MACD fading | Occasionally holds 1–2 positions |
| **Volume Whisperer** | VWAP + OBV flow | OBV up + above EMA21 | Above VWAP | Vol > 1.5× + RSI 40–70 + MACD↑ | TAY v1 |
| **Correlation Hunter** | Pairs trading | Custom correlation logic (not TAY) | Z-score > 2, correlation divergence | — | Historical P&L leader, stocks only |
| **News Sniper** | Sentiment scalping | >3% intraday move | Vol > 2× institutional | RSI 30–70 + VWAP + MACD | TAY v1 |

Framework strategies (v1 vs v2): v2 bots were upgraded on 2026-04-12 to add candlestick patterns and horizontal S/R based on analysis of 96 YouTube trading strategies extracted by the `youtube-strategy` pipeline. Top predictors across those strategies: horizontal S/R (47), moving averages (35), candlestick patterns (31), ATR stops (23).

### Arena operational parameters

- **Starting balance:** $1,000 per bot (paper)
- **Max per trade:** $50 (5% of starting balance)
- **Max concurrent per bot:** 3 positions
- **Per-bot daily loss limit:** -$30
- **Global daily loss limit:** -$500
- **Watchlist:** Top 20 movers refreshed every 2h by `|change%| × log10(volume_usd)` — no hardcoded always-on symbols
- **TradingView chart:** auto-switches every 30 min to the #1 opportunity via Chrome DevTools Protocol

### Overseer

A meta-agent persona at `projects/trading-arena/sub-agents/trading-overseer.md` that runs three scheduled jobs:

- **game_plan (9 AM ET)** — assigns tickers to bots for the day
- **autopsy (4:30 PM ET)** — reviews every trade, ranks TAY components by win rate
- **super_prompt (Friday 6 PM ET)** — weekly improvement prompts per bot

## Concierge pattern — human in the loop for live money

The arena is paper-only. Real money flows through two **concierge** daemons that wait for commands from Telegram, propose trades, and require Tony's button-tap to execute.

| Concierge | Exchange | Bot handle | systemd unit | State DB |
|---|---|---|---|---|
| **stock-concierge** | Questrade (stocks) | `@MyProjectWorldbot` | `stock-concierge.service` | `state/stock_concierge.db` |
| **trading-concierge** | Kraken (crypto) | `@YuriTrade24Bot` | `trading-concierge.service` | `state/concierge_state.db` |

Commands: `/best` (rank opportunities), `/positions`, `/balance`, `/history`, `/kill`.

Execution flow: `/best` → advisor ranks candidates by number of arena bots firing → button choices [$10/$25/$50/Skip] → execute real order → position-watcher cron monitors → exit signal posts button [Sell Now / Hold 30m].

The two bots **must be separate** — Telegram long-poll conflicts if two services poll updates with the same token.

**Live-money limits (much stricter than arena):**
- Manual stock (Questrade): $25 max/trade, $50 max exposure, -$10 daily loss
- Manual crypto (Kraken): $5 max/trade, $10 max exposure, -$3 daily loss
- Auto live (arena, the-reverter only): $5 max/trade, $10 exposure, -$3 daily loss — disabled by default

Kill switch: `skills/trading-arena/kill_live_trading.sh` — closes both live-trading gates and cancels open Kraken orders.

## Why we keep scheduled work out of any agent runtime

**Rule: scheduled Python scripts run from user crontab, not from an agent runtime.**

History: the VPS's OpenClaw agent runtime had an exec-approval gate that denied `python3 /data/skills/...` regardless of session target. Config flags, allowlist entries, and `exec-approvals.json` edits did not bypass it — the gate was a separate matcher. Workaround: move scheduling to host cron.

On GX10 there is no such gate, but the lesson holds: **scheduled work has no business sitting behind an LLM agent's tool-call path.** Cron calls Python directly, Python does the work deterministically, and the LLM is invoked only where it adds value (advisor, autopsy, super_prompt, etc.). This keeps:

- Latency low (no agent planning loop for routine jobs)
- Failure modes predictable (cron either runs or doesn't)
- Debugging scoped (a broken script is a broken script, not "the agent decided not to do it")

The crontab is the source of truth for "what runs and when." The skills have `<name>_cron.sh` wrappers that load `.env`, call Python, and post to Telegram. The LLM is a tool called by Python — not the scheduler, not the dispatcher.

## State model

Three tiers:

**1. Local state (`~/openclaw/state/`)** — gitignored, rsync-migratable
- `questrade_token.json` — OAuth token (access + refresh + api_server assignment). Must stay fresh.
- `stock_concierge.db` / `concierge_state.db` — SQLite, tracks one-time-use button callbacks
- `browser/openclaw/` — Playwright Chromium profile with TradingView login session
- `questrade_browser_state/` — separate Playwright profile for Questrade web order execution
- `telegram_offset.json` — Telegram long-poll offset per bot

**2. Supabase (external)** — source of truth for shared/historical data
- `arena_trades` — every paper trade with full TAY component breakdown
- `tenders` — procurement crawler output
- `news_events`, `social_signals` — trading signals (both stale >180h at last check — see [OPERATIONS.md § Known staleness](OPERATIONS.md#known-staleness))

**3. Secrets (`.env`)** — gitignored, restored from password manager on rebuild
- API keys (Questrade, Kraken, OpenAI, Gemini, Finnhub, Alpha Vantage, Firecrawl, Twelve Data, Supabase)
- Telegram bot tokens (`TELEGRAM_BOT_TOKEN` main, `TELEGRAM_TRADER_BOT_TOKEN` trader)
- Live-trading gate flags (default conservative; see `.env.example` for safe defaults)

## Telegram channel map

One chat ID (`6545739863`), three bots posting there:

| Bot | Posts | Triggered by |
|---|---|---|
| `@MyProjectWorldbot` (`TELEGRAM_BOT_TOKEN`) | Medic reports, trading briefs, arena buy/sell, procurement summary, receipts, overseer output | cron + stock concierge |
| `@YuriTrade24Bot` (`TELEGRAM_TRADER_BOT_TOKEN`) | Kraken concierge opportunities + position updates | trading-concierge service |
| TradingView webhook (via main bot token) | Pine strategy alerts | tv-webhook service |

## Lessons baked in

Rather than a standalone GOTCHAS.md that would rot, the hard-won lessons from the VPS era are folded into the relevant docs:

- **"Don't use gemma for agent loops"** → [OPERATIONS.md § Model selection](OPERATIONS.md#model-selection-guide)
- **"Pass `think: false` and `keep_alive` for qwen3.5"** → [OPERATIONS.md § Model selection](OPERATIONS.md#model-selection-guide)
- **"Questrade refresh tokens burn on use"** → [OPERATIONS.md § Questrade token expired](OPERATIONS.md#questrade-token-expired)
- **"Never run general + coder simultaneously"** → [OPERATIONS.md § Model selection](OPERATIONS.md#model-selection-guide)
- **"Live trading has two gates"** → [OPERATIONS.md § Kill live trading](OPERATIONS.md#emergency-kill-live-trading)
- **"Separate Telegram bots for separate concierges"** → this file, [Concierge pattern](#concierge-pattern--human-in-the-loop-for-live-money)

## What got deleted in the GX10 rebuild (and won't come back)

These are Hostinger-VPS artifacts with no place on GX10. If you see a PR or code change bringing any of them back, push back.

- `ollama-tunnel.service` — Ollama is local here
- `questrade-tunnel.service` + the 12 `-L` forwards — Questrade is not IP-blocked on residential IPs
- `QUESTRADE_AUTH_URL` env var — no tunnel port to rewrite to
- `QUESTRADE_API_PORT_MAP` env var — no tunnel ports
- `_api_base_url(token)` helpers in `questrade.py` and `questrade_client.py` — the rewriting they did is unneeded
- `extra_hosts` in docker-compose — no docker, no compose
- UFW bridge rules — no docker bridge
- OpenClaw exec-approval config (`tools.exec.ask`, `tools.elevated.enabled`) — no OpenClaw gateway
- `docker exec openclaw-xrt9-openclaw-1 ...` in any cron wrapper — direct Python now

## Related docs

- [CLAUDE.md](CLAUDE.md) — entry point, quick reference
- [BUILD.md](BUILD.md) — zero → running
- [OPERATIONS.md](OPERATIONS.md) — runbooks and playbooks
