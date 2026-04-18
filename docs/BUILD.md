# Build — zero → running on GX10

From an empty `~/openclaw/` to a working stack. Assumes you are logged in as `tonygale` on `gx10-087b`. For ongoing operation see [OPERATIONS.md](OPERATIONS.md).

## 0. Prerequisites (already on GX10)

Verify these before anything else:

```bash
hostname                                    # gx10-087b
uname -m                                    # aarch64
systemctl is-active ollama                  # active
curl -s http://localhost:11434/api/tags | head -c 200   # models listed
python3 --version                           # 3.11+
groups                                      # must include ollama
```

Install if missing (one-time, requires sudo password):

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip git rsync jq
# Tailscale should already be up — verify: tailscale status
```

## 1. Clone the repo

```bash
cd ~
git clone <remote-url> openclaw     # private repo; create on GitHub if not yet
cd ~/openclaw
```

If this is the first time the repo is being created (no remote yet), initialize locally:

```bash
cd ~
mkdir openclaw && cd openclaw
git init
# copy skills/, projects/, systemd/, pyproject.toml, .env.example, .gitignore from your source
git add -A && git commit -m "initial openclaw structure on GX10"
```

## 2. Python environment

```bash
cd ~/openclaw
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .                    # installs from pyproject.toml
```

Core deps (pyproject.toml): `httpx`, `supabase`, `playwright`, `python-telegram-bot`, `pandas`, `numpy`, `python-dotenv`, `cryptography`, `firecrawl-py`.

## 3. Playwright Chromium (for Questrade web orders + TradingView chart switching)

```bash
source ~/openclaw/.venv/bin/activate
playwright install chromium
playwright install-deps chromium    # may prompt for sudo
```

Verify:

```bash
python3 -c "from playwright.sync_api import sync_playwright; sync_playwright().start().chromium.launch(headless=True).close(); print('ok')"
```

## 4. Environment secrets

Copy the template and fill in real values from your password manager:

```bash
cp .env.example .env
chmod 600 .env
$EDITOR .env
```

Do **not** commit `.env`. It's in `.gitignore`.

Minimum vars that must be populated for a working stack:

- `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_PROJECT_ID`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_TRADER_BOT_TOKEN`
- `QUESTRADE_REFRESH_TOKEN`, `QUESTRADE_CONSUMER_KEY`, `QUESTRADE_WEB_USER`, `QUESTRADE_WEB_PASSWORD`
- `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`
- `FINNHUB_KEY`, `ALPHA_VANTAGE_KEY`, `TWELVE_DATA_KEY`, `FIRECRAWL_API_KEY`
- `OLLAMA_BASE_URL=http://localhost:11434` (native on GX10 — no tunnel)
- `TRADINGVIEW_WEBHOOK_SECRET`
- Live-trading gates — start with `LIVE_TRADING_ENABLED=false`, `MANUAL_TRADING_ENABLED=false`, `MANUAL_STOCK_TRADING_ENABLED=false`

**Do NOT set** `QUESTRADE_AUTH_URL` or `QUESTRADE_API_PORT_MAP`. Those were Hostinger tunnel workarounds and have no place on GX10. If the code still reads them, it's harmless (the patches are env-gated and fall back to defaults when the vars are absent).

## 5. Restore state from backup (first-time migration from VPS only)

If you're migrating from the old Hostinger VPS, rsync the runtime state **once**. Otherwise skip to step 6 — concierges will initialize a fresh DB.

```bash
mkdir -p ~/openclaw/state
rsync -avz --progress \
    srv1378550:/docker/openclaw-xrt9/data/.openclaw/questrade_token.json \
    srv1378550:/docker/openclaw-xrt9/data/.openclaw/browser/ \
    srv1378550:/docker/openclaw-xrt9/data/.openclaw/questrade_browser_state/ \
    srv1378550:/docker/openclaw-xrt9/data/skills/trading-arena/concierge/concierge_state.db \
    ~/openclaw/state/
```

Fix paths if necessary — the state dir layout on GX10 is flattened vs the VPS's `.openclaw/` nesting.

**Immediately refresh the Questrade token** to confirm it's still valid and to reset `expires_at`:

```bash
~/openclaw/.venv/bin/python ~/openclaw/skills/questrade/scripts/questrade.py portfolio | head -20
```

If that returns a portfolio JSON, you're good. If it errors with `invalid_grant` or `Bad Request`, the refresh token burned during the migration — generate a new one at questrade.com → Settings → API centre and update both `.env` and `state/questrade_token.json`.

## 6. systemd user units

User units run without sudo and start automatically with `tonygale`'s session (if lingering is enabled).

```bash
# One-time: enable lingering so services start at boot, not just at login
sudo loginctl enable-linger tonygale

# Install unit files
mkdir -p ~/.config/systemd/user
cp ~/openclaw/systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload

# Enable the ones you want
systemctl --user enable --now tv-webhook.service
systemctl --user enable --now stock-concierge.service
systemctl --user enable --now trading-concierge.service

# trading-agent and trading-arena are optional daemons — enable when ready
# (they can also run via cron; pick one pattern per skill)
```

Verify:

```bash
systemctl --user status tv-webhook stock-concierge trading-concierge
```

Each unit should show `active (running)`. Check logs with `journalctl --user -u stock-concierge -n 50`.

## 7. Crontab

The project ships `crontab.gx10` containing all scheduled entries. Install it:

```bash
# Back up any existing user crontab first
crontab -l > ~/crontab.backup.$(date +%s) 2>/dev/null || true

# Install
crontab ~/openclaw/crontab.gx10
crontab -l    # verify
```

**All times in crontab.gx10 are UTC** with explicit weekday filters for market hours (13:30–20:00 UTC = 9:30–16:00 EDT, or 14:30–21:00 UTC during EST). Accept the 1h drift across DST transitions — same compromise as the VPS wrappers.

Key entries:

```cron
# Market-hours arena scan (every 5 min)
*/5 13-20 * * 1-5  /home/tonygale/openclaw/skills/trading-arena/arena_scan.sh

# Overseer
0 13 * * 1-5       /home/tonygale/openclaw/skills/trading-arena/overseer_cron.sh game_plan
30 20 * * 1-5      /home/tonygale/openclaw/skills/trading-arena/overseer_cron.sh autopsy
0 22 * * 5         /home/tonygale/openclaw/skills/trading-arena/overseer_cron.sh super_prompt

# Medic
0 11 * * 1-5       /home/tonygale/openclaw/skills/medic/scripts/medic_cron.sh full
0 3 * * *          /home/tonygale/openclaw/skills/medic/scripts/medic_cron.sh report-only

# Procurement (every 2 days)
0 7 */2 * *        /home/tonygale/openclaw/skills/procurement/scripts/smart_crawl.sh

# Receipts
0 3 * * *          /home/tonygale/openclaw/skills/receipts/scripts/receipts_cron.sh

# Trading briefs
0 13 * * 1-5       /home/tonygale/openclaw/skills/trading/scripts/trading_premarket_cron.sh
30 20 * * 1-5      /home/tonygale/openclaw/skills/trading/scripts/trading_postmarket_cron.sh
16 * * * 1-5       /home/tonygale/openclaw/skills/trading/scripts/trading_dashboard_cron.sh
*/15 * * * *       /home/tonygale/openclaw/skills/trading/scripts/trading_news_cron.sh

# Questrade token keep-fresh (5 min before each medic)
55 2,10 * * *      /home/tonygale/openclaw/.venv/bin/python /home/tonygale/openclaw/skills/questrade/scripts/questrade.py portfolio >/dev/null 2>&1

# Watchlist + TV focus (market hours)
0 13,15,17,19 * * 1-5   /home/tonygale/openclaw/skills/trading-arena/watchlist_cron.sh
*/30 13-20 * * 1-5      /home/tonygale/openclaw/skills/trading-arena/tv_focus_cron.sh

# Concierge watchers (24/7; self-gate to time windows internally)
*/5 * * * *        /home/tonygale/openclaw/skills/trading-arena/concierge/position_watcher.py
*/30 * * * *       /home/tonygale/openclaw/skills/trading-arena/concierge/buy_watcher.py
*/30 * * * *       /home/tonygale/openclaw/skills/trading-arena/concierge/stock_buy_watcher.py
```

Each `_cron.sh` wrapper:
1. Sources `~/openclaw/.env` (so cron inherits API keys)
2. Activates `~/openclaw/.venv` (so `python3` resolves to the right interpreter)
3. Runs the actual script
4. Pipes output through the standard error-filter pattern (drops tracebacks, caps at 15 lines) before posting to Telegram
5. Exits non-zero only on genuine failure

## 8. tv-webhook public exposure

TradingView alerts POST to the webhook over the public internet. Native tv-webhook binds `127.0.0.1:8089` — you need a public URL.

Use **Tailscale Funnel** (simplest, already on Tailscale):

```bash
# One-time setup
sudo tailscale serve --bg --https=443 http://localhost:8089
sudo tailscale funnel 443 on

# Verify
tailscale funnel status
# Should show: https://gx10-087b.<tailnet>.ts.net/ → 127.0.0.1:8089
```

Then update your TradingView alert webhooks to the new `https://gx10-087b.<tailnet>.ts.net/webhook/...` URL and include `TRADINGVIEW_WEBHOOK_SECRET` as a header or payload field (check `skills/tradingview/scripts/webhook_server.py` for the exact auth mechanism).

Alternatives if Tailscale Funnel isn't an option: Cloudflare Tunnel (`cloudflared`), or a public reverse proxy with a real DNS record. Port-forward from your home router is fragile and only works if you have a static IP — not recommended.

## 9. Ollama model verification

Confirm the expected aliases exist:

```bash
ollama list | grep -E '^(quick|coder|general|gemma|nemotron)'
```

Expected:
```
quick:latest        qwen3.5:35b        23 GB
coder:latest        qwen3-coder-next   51 GB
general:latest      qwen3.5:122b       81 GB
gemma:latest        gemma4:31b         19 GB
nemotron:70b        nemotron:70b       42 GB
```

If aliases are missing, create them:

```bash
ollama cp qwen3.5:35b quick:latest
ollama cp qwen3-coder-next:latest coder:latest
ollama cp qwen3.5:122b general:latest
ollama cp gemma4:31b gemma:latest
```

Sanity-check the default model (used by most cron jobs):

```bash
time curl -s http://localhost:11434/api/generate -d '{
  "model": "quick:latest",
  "prompt": "Reply with exactly: ok",
  "think": false,
  "keep_alive": "1h",
  "stream": false
}' | jq -r '.response'
```

Should return `ok` (or close to it) in under 3 seconds. If it's 15+ seconds, `think` didn't parse — check payload.

## 10. Smoke tests

Run these in sequence. Each should complete in under 30 seconds.

```bash
# Medic report (deterministic, no LLM)
~/openclaw/.venv/bin/python ~/openclaw/skills/medic/scripts/medic.py report | tail -20

# Questrade portfolio (refreshes token if needed)
~/openclaw/.venv/bin/python ~/openclaw/skills/questrade/scripts/questrade.py portfolio | head -10

# Arena runner (one cycle)
~/openclaw/.venv/bin/python ~/openclaw/skills/trading-arena/arena_runner.py --once

# Overseer game plan (calls LLM; slower, ~10-30s)
~/openclaw/skills/trading-arena/overseer_cron.sh game_plan

# Check a Telegram post actually went out
# (look for the game_plan output in your Telegram chat 6545739863)
```

If all five pass, the stack is functional.

## 11. Cut over from the VPS

Only after steps 1–10 succeed on GX10:

1. **Freeze the VPS.** Stop all its cron jobs: `crontab -l > ~/vps-crontab.txt && crontab -r` on the VPS.
2. **Disable VPS systemd units:** `systemctl stop stock-concierge trading-concierge ollama-tunnel questrade-tunnel`.
3. **Verify no open live-trading positions** on the VPS side. If Trap Catcher or The Reverter holds anything, close it manually or let it exit naturally first.
4. **Confirm GX10 is running** — watch Telegram for 30 minutes during market hours, confirm arena buy/sell alerts fire and look sane.
5. **Keep the VPS alive for 1 week as a fallback** before cancelling. If something misbehaves on GX10, you can temporarily re-enable specific jobs on the VPS.
6. **Cancel Hostinger account** once GX10 has been stable through at least one full trading week.

## 12. Known build-time pitfalls

- **Playwright Chromium on ARM64:** works, but may require `playwright install-deps` (sudo). If `playwright install chromium` hangs, check disk space in `~/.cache/ms-playwright/`.
- **`supabase-py` version drift:** the VPS may be pinned to an older version; regenerate `requirements.txt` fresh on GX10 rather than copying verbatim.
- **Telegram long-poll conflict:** if you see `409 Conflict` on concierge startup, another process is already polling that bot token. Usually means the VPS concierge is still running. Stop it first.
- **Ollama cold-start:** first call to an unloaded model takes 10–60s. The `keep_alive: "1h"` header prevents re-warming between calls; make sure it's in every call.
- **Lingering not enabled:** if `systemctl --user` units don't start after reboot, run `loginctl enable-linger tonygale` and reboot once.

## Related docs

- [CLAUDE.md](CLAUDE.md) — entry point, quick reference
- [ARCHITECTURE.md](ARCHITECTURE.md) — what exists and why
- [OPERATIONS.md](OPERATIONS.md) — daily runbook, incident playbooks
