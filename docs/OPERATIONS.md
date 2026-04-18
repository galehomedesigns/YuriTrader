# Operations — daily runbook, playbooks, and model guide

Day-to-day running of the stack. For rebuild see [BUILD.md](BUILD.md); for structural understanding see [ARCHITECTURE.md](ARCHITECTURE.md).

## Health check routine

When a user asks "are the agents working?" — run this in roughly parallel and report a consolidated summary. Don't investigate from scratch.

```bash
# 1. Host + Ollama
hostname
systemctl is-active ollama
curl -s --max-time 5 http://localhost:11434/api/tags | jq -r '.models[].name' | head -10

# 2. Long-running user services
systemctl --user status tv-webhook stock-concierge trading-concierge \
    --no-pager --lines=0

# 3. Crontab installed
crontab -l | wc -l   # should be > 10

# 4. Recent cron activity
for d in medic procurement trading-arena trading receipts; do
  echo "=== $d ==="
  tail -5 ~/openclaw/logs/$d/cron.log 2>/dev/null || echo "(no log)"
done

# 5. Medic (authoritative health report)
~/openclaw/.venv/bin/python ~/openclaw/skills/medic/scripts/medic.py report | tail -30

# 6. Arena bots (all 10 should be ACTIVE during market hours)
tail -40 ~/openclaw/logs/trading-arena/arena.log

# 7. Tailscale Funnel (if tv-webhook is expected to be reachable)
tailscale funnel status
```

What each one tells you:

| Step | Green signal | Red signal |
|---|---|---|
| 1 | Ollama active, models listed | `inactive`, empty response → Ollama down |
| 2 | `active (running)` for all three | `failed`, `inactive` → unit crashed |
| 3 | ≥ 10 entries | Empty → crontab was wiped |
| 4 | Most recent log line < 24h old | Logs stale or missing → cron not firing |
| 5 | `OK` or `WARN` final status | `FAIL` count > 1 → investigate specifics |
| 6 | All 10 bots list as `ACTIVE` in the latest cycle summary | Missing bots, error lines → arena broken |
| 7 | Funnel listening on port 443 | `Funnel off` → TV alerts won't arrive |

## Incident playbooks

### Questrade token expired

**Symptom:** medic reports `FAIL questrade.auth`, or any Questrade call returns `invalid_token`.

```bash
# Force a refresh
~/openclaw/.venv/bin/python ~/openclaw/skills/questrade/scripts/questrade.py portfolio
```

If that succeeds, the token is now fresh — done. If it fails with `invalid_grant` or `Bad Request`:

1. The refresh token itself is dead (they're single-use and burn on each refresh attempt).
2. Go to **questrade.com → My Profile → API centre → Generate New Token**.
3. Copy the new refresh token.
4. Update **both** places:
   ```bash
   $EDITOR ~/openclaw/.env                     # QUESTRADE_REFRESH_TOKEN=<new>
   $EDITOR ~/openclaw/state/questrade_token.json   # "refresh_token": "<new>"
   ```
5. Run the portfolio command again to confirm.

If `questrade.py` still fails but the token is definitely valid, check:
- `.env` has no stray `QUESTRADE_AUTH_URL` or `QUESTRADE_API_PORT_MAP` (those are VPS tunnel artifacts — must not be set here).
- Ollama isn't involved (this is pure REST).
- `curl -v https://api01.iq.questrade.com` from the host resolves DNS and completes TLS — should return a normal Questrade error page, not `Empty reply from server`.

### Medic reports FAIL

Medic's output names the failing check. Common ones:

| Failing check | First thing to try |
|---|---|
| `questrade.auth` | Token refresh (above) |
| `ollama.generation` | `systemctl restart ollama`; verify `curl http://localhost:11434/api/tags` |
| `telegram.post` | Check `TELEGRAM_BOT_TOKEN` hasn't been revoked; `curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe"` |
| `supabase.read` | Check `SUPABASE_URL` and `SUPABASE_KEY`; test with a manual query |
| `news_events.freshness` | The news scanner cron hasn't run — check `logs/trading/` |
| `social_signals.freshness` | Same — check `logs/trading/` for social_scanner runs |

### Trading arena bots not trading

**Symptom:** no arena buy/sell posts in Telegram during market hours.

Check order:

```bash
# 1. Is the arena runner firing?
tail -50 ~/openclaw/logs/trading-arena/arena.log
# Look for a recent "cycle start" line

# 2. Is the scanner returning data?
grep -E 'watchlist|no candidates' ~/openclaw/logs/trading-arena/arena.log | tail -20

# 3. Are any bots raising errors?
grep -iE 'error|exception|traceback' ~/openclaw/logs/trading-arena/arena.log | tail -20

# 4. Is Supabase reachable? (paper_trader writes here)
~/openclaw/.venv/bin/python -c "from supabase import create_client; import os; c = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY']); print(c.table('arena_trades').select('*').limit(1).execute())"
```

"No candidates" during low-volatility sessions is normal — the arena only fires when TAY conditions confluence. A 30-minute quiet stretch is not necessarily a bug.

### Emergency: kill live trading

One command closes both live-trading gates and cancels Kraken open orders:

```bash
~/openclaw/skills/trading-arena/kill_live_trading.sh
```

This sets `LIVE_TRADING_ENABLED=false` and `MANUAL_TRADING_ENABLED=false` in `.env` and calls Kraken's cancel-all endpoint. It does **not** close existing positions — if you need to flatten, use the Kraken concierge `/positions` command with the Sell Now button, or go directly to kraken.com.

**Note:** this does not touch Questrade stock trading. For manual Questrade trading, also set `MANUAL_STOCK_TRADING_ENABLED=false` in `.env` and restart the stock-concierge:

```bash
systemctl --user restart stock-concierge
```

### Ollama misbehaving

Symptoms: timeouts across all LLM-dependent jobs, model loading takes forever, or completions come back empty.

```bash
# Quick restart
sudo systemctl restart ollama

# If that doesn't help, check for stuck runners (>1400% CPU)
~/scripts/kill-runaway.sh

# Nuclear: nightly-cleanup also works as an on-demand reset
~/scripts/nightly-cleanup.sh
```

GX10 already has crons for auto-restart every 5 min and runaway-kill every 10 min, so transient issues self-heal. If problems persist > 30 min, check disk space (`~/.ollama/models/` can grow big) and unified memory usage (`nvidia-smi` or equivalent).

### tv-webhook not receiving alerts

```bash
# Is the service up?
systemctl --user status tv-webhook

# Is it listening?
ss -tlnp | grep 8089

# Is Funnel exposing it?
tailscale funnel status
# Should show https://gx10-087b.<tailnet>.ts.net/ → 127.0.0.1:8089

# Can you reach the public URL?
curl -i https://gx10-087b.<tailnet>.ts.net/health
# Should return 200 or the webhook_server's health response
```

If Funnel is off: `sudo tailscale funnel 443 on`.

If TradingView alerts show "failed" on their side, check that the alert URL still points at the current Tailscale Funnel hostname — it can change if the tailnet is renamed.

### Concierge bot not responding to /best

```bash
# Check the service
systemctl --user status stock-concierge       # or trading-concierge
journalctl --user -u stock-concierge -n 50

# Common cause: Telegram API 409 Conflict (another process polling same token)
# Fix: ensure no other instance (VPS?) is running
```

If the concierge is polling fine but commands do nothing, the advisor may be waiting on Ollama. Check `tail ~/openclaw/logs/trading-arena/advisor.log`.

## Ollama num_ctx enforcement (ollama-guard proxy)

Every LLM call on this host goes through **ollama-guard**, a transparent reverse proxy that clamps `options.num_ctx` per-model. Clients that hit `http://localhost:11434` don't need to know the proxy exists — it rewrites only the four chat/generate endpoints and passes everything else through.

**Why it exists.** OpenClaw (and similar ollama clients) read each model's self-declared max context from `/api/show`'s `context_length` — 262144 for qwen3 — and send that as `options.num_ctx` on every request. On this 125 GB unified-memory box, f16 KV at 256K on `qwen3.5:122b` tips the budget into CPU-side compute paths and the runner spirals (23 GB KV, 1900% CPU, timeouts everywhere). The proxy caps `num_ctx` at a sane per-model value. Clients still see the big "max" advertised by `/api/show`, but get served what fits.

**What's running (in-place lockdown, 2026-04-18):**

| Service | Listening on | Notes |
|---|---|---|
| `ollama.service` | `127.0.0.1:11436` | Loopback-only. Invisible over Tailscale. |
| `ollama-guard.service` | `0.0.0.0:11434` | Public port; every Tailnet client lands here. |

Drop-ins enforcing the lockdown:
- `/etc/systemd/system/ollama.service.d/bind-loopback.conf` → `OLLAMA_HOST=127.0.0.1:11436`
- `/etc/systemd/system/ollama-guard.service.d/lockdown.conf` → `LISTEN_PORT=11434`, `UPSTREAM_PORT=11436`, `AUTH_BEARER=<secret>`

Source: `/home/tonygale/ollama-guard/ollama-guard.js` (Node, zero deps). Policy: `/home/tonygale/ollama-guard/policy.json`.

**Bearer auth + model allowlist (hybrid hardening, 2026-04-18).** Non-localhost clients — primarily VPS OpenClaw over Tailscale — must supply `Authorization: Bearer $AUTH_BEARER` or get 401. They also may only request models listed in `policy.json`'s `allowed_models` array, else 404. Localhost clients bypass both (so on-host crons and interactive shells keep working unchanged against any model). Change the bearer: edit `/etc/systemd/system/ollama-guard.service.d/lockdown.conf`, `sudo systemctl daemon-reload && sudo systemctl restart ollama-guard`, then update every client's env. Change the allowlist: edit `policy.json`'s `allowed_models`, then `sudo systemctl reload ollama-guard` (SIGHUP; no restart). Deny events log as `[auth-fail]` or `[deny-model]` — grep them out of `journalctl -u ollama-guard`.

**Tune per-model caps (hot reload, no restart):**

```bash
$EDITOR /home/tonygale/ollama-guard/policy.json
sudo systemctl reload ollama-guard        # SIGHUP
journalctl -u ollama-guard --since "1 minute ago"   # expect "[policy] loaded ..."
```

**Verify it's healthy:**

```bash
curl -s http://localhost:11434/healthz | python3 -m json.tool
ss -tlnp | grep -E "1143[46]"   # node on 11434, ollama on 11436

# Clamp smoke test — asks for absurd num_ctx, expects it to be capped:
curl -s -X POST http://localhost:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"quick:latest","prompt":"ping","stream":false,"options":{"num_ctx":999999}}' >/dev/null
journalctl -u ollama-guard --since "1 minute ago" | grep clamp | tail -1
# Expect: [clamp] ... asked=999999 served=<cap> cap=<cap>
```

**Rollback the lockdown (revert to side-by-side: raw ollama on :11434, proxy on :11435):**

```bash
sudo rm /etc/systemd/system/ollama.service.d/bind-loopback.conf
sudo rm /etc/systemd/system/ollama-guard.service.d/lockdown.conf
sudo rmdir /etc/systemd/system/ollama-guard.service.d 2>/dev/null || true
sudo systemctl daemon-reload
sudo systemctl restart ollama         # back to 0.0.0.0:11434
sudo systemctl restart ollama-guard   # back to 0.0.0.0:11435
ss -tlnp | grep -E "1143[45]"         # ollama on 11434, node on 11435
```

After rollback, clamping is only applied to clients that opt in by pointing at `:11435`. `:11434` is raw ollama again — any client that forgets to opt in can bypass the caps.

**Full teardown (remove the proxy entirely):**

```bash
sudo systemctl disable --now ollama-guard
sudo rm /etc/systemd/system/ollama-guard.service
sudo rm -rf /etc/systemd/system/ollama-guard.service.d
sudo rm /etc/systemd/system/ollama.service.d/bind-loopback.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama
# Optionally: rm -rf /home/tonygale/ollama-guard
```

Upstream OpenClaw bug tracking the root cause: [openclaw/openclaw#35436](https://github.com/openclaw/openclaw/issues/35436).

## Model selection guide

| Alias | Real model | Size | When to use | Avoid when |
|---|---|---|---|---|
| `quick:latest` | qwen3.5:35b | 23 GB | **Default for every cron job.** Short agent turns, advisor, game plan, autopsy summaries, concierge chat. | Complex multi-step reasoning (use `general`) |
| `coder:latest` | qwen3-coder-next | 51 GB | Code generation, agent tool-use, script authoring | Running alongside `general` (132 GB exceeds budget) |
| `general:latest` | qwen3.5:122b | 81 GB | Weekly super_prompt, deep strategy analysis, anything that needs real reasoning | Cron jobs with < 60s budgets; running alongside `coder` |
| `nemotron:70b` | nemotron:70b | 42 GB | Experimental — evaluate against `quick` + `general` before defaulting anything to it | Until it has a track record for this workload |
| `gemma:latest` | gemma4:31b | 19 GB | **Nothing agentic.** Ignores length limits; ~10 tok/s; 120s timeouts will fail. Use only for multimodal/image tasks. | Any agent loop, any cron job |

**Two rules:**

1. **Pass `"think": false` and `"keep_alive": "1h"`** on every call to `quick`/`coder`/`general`. Thinking mode adds ~10× latency. Example:

   ```python
   httpx.post("http://localhost:11434/api/generate", json={
       "model": "quick:latest",
       "prompt": "...",
       "think": False,
       "keep_alive": "1h",
       "stream": False,
   })
   ```

   Reference implementation: `skills/trading-arena/concierge/advisor.py`.

2. **Never run `general:latest` with `coder:latest` at the same time.** 81 GB + 51 GB = 132 GB > 125 GB unified memory budget. The OS will stall or OOM. If a cron job loads `coder` and a concierge then calls `general`, one will evict the other and you'll pay the cold-load tax on the next call.

   Safe combinations:
   - `quick + coder` — 74 GB ✓
   - `quick + gemma` — 42 GB ✓
   - `quick + nemotron` — 65 GB ✓
   - `general + quick` — 104 GB (tight, works)
   - `general + coder` — 132 GB ✗ **DO NOT**

## Known staleness and false alarms

**News + social signals are stale.** At last check (2026-04-17) `news_events` and `social_signals` Supabase tables were > 180 hours old. The medic `data.freshness` check is correctly flagging this. Fix is out of scope for rebuild — the news and social scanners (`skills/trading/scripts/news_scanner.py`, `social_scanner.py`) need their API keys and scheduled job verified. Until fixed, treat those medic warnings as known-noise.

**Questrade false-alarm window.** If medic runs at exactly midnight UTC, the keep-fresh cron (55 2 UTC) hasn't yet run for the day — if the previous day's last refresh was 30+ min ago, medic will see `expires_at < now`. Harmless, self-corrects at 02:55 UTC. If you want to silence it, adjust the medic-report-only cron to run after 03:00 UTC.

## How to add a new scheduled skill

1. Create the skill dir:
   ```bash
   mkdir -p ~/openclaw/skills/<name>/{scripts,logs}
   touch ~/openclaw/skills/<name>/SKILL.md
   ```

2. Write the Python entry script at `scripts/<name>.py`. Reads env vars, does work, exits non-zero on genuine failure.

3. Write a shell wrapper at `scripts/<name>_cron.sh`, modeled on an existing one (`skills/medic/scripts/medic_cron.sh` is a good template). The wrapper must:
   - Source `~/openclaw/.env`
   - Activate `~/openclaw/.venv` (or call `.venv/bin/python` directly)
   - Run the Python script
   - Filter tracebacks (awk pattern in existing wrappers)
   - Post to Telegram if non-empty output
   - `chmod +x` the wrapper

4. Smoke-test by invoking the wrapper directly:
   ```bash
   ~/openclaw/skills/<name>/scripts/<name>_cron.sh
   ```

5. Add to `crontab.gx10`:
   ```cron
   <schedule> /home/tonygale/openclaw/skills/<name>/scripts/<name>_cron.sh
   ```

6. Reload:
   ```bash
   crontab ~/openclaw/crontab.gx10
   ```

7. Commit and push:
   ```bash
   cd ~/openclaw
   git add skills/<name>/ crontab.gx10
   git commit -m "add <name> skill with <schedule> cron"
   git push
   ```

## How to add a new daemon (systemd user unit)

Use this pattern when the skill is a long-running loop or Telegram poller, not a schedulable one-shot.

1. Write the Python script; it should run forever, log to stdout/stderr, and exit only on fatal error.

2. Write a unit file at `~/openclaw/systemd/<name>.service`:

   ```ini
   [Unit]
   Description=<Name> daemon
   After=network.target

   [Service]
   Type=simple
   EnvironmentFile=/home/tonygale/openclaw/.env
   ExecStart=/home/tonygale/openclaw/.venv/bin/python /home/tonygale/openclaw/skills/<skill>/scripts/<name>.py
   Restart=on-failure
   RestartSec=10

   [Install]
   WantedBy=default.target
   ```

3. Install and start:
   ```bash
   cp ~/openclaw/systemd/<name>.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now <name>.service
   ```

4. Verify:
   ```bash
   systemctl --user status <name>
   journalctl --user -u <name> -f
   ```

## Backup & disaster recovery

**What to back up:**

- Supabase — daily export of `arena_trades`, `tenders`, `news_events`, `social_signals`. Do this from Supabase dashboard or a scheduled job.
- `~/openclaw/.env` — keep a copy in your password manager; regenerate any API keys that were there if the file is compromised.
- `~/openclaw/state/` — snapshot weekly. Token will refresh itself, concierge DBs are rebuildable, browser profile is the one worth preserving (TradingView session).
- The git repo itself — push to a private remote.

**Disaster recovery (lose GX10 entirely):**

1. Procure a new ARM or x86 Linux host with ≥ 64 GB RAM (or a new DGX Spark).
2. Install Ollama + pull models (`quick`, `coder`, `general`, `gemma`, `nemotron:70b`). Let this run overnight — it's 200+ GB.
3. Follow [BUILD.md](BUILD.md) from step 1.
4. Restore `.env` from password manager.
5. Restore `state/` from backup (or accept a fresh start — most is rebuildable).
6. Restore Supabase from export if needed (or, if it was never lost, just reconnect — it's external).
7. Re-point Tailscale Funnel (Tailscale will issue a new hostname unless you set it explicitly).
8. Update TradingView alerts to the new Funnel URL.
9. Smoke tests from [BUILD.md § 10](BUILD.md#10-smoke-tests).

Recovery time objective with state backup: ~3 hours once Ollama models are present; ~1 day from scratch (mostly Ollama downloads).

## Pruning: things to periodically check for

- **Log directory size:** `du -sh ~/openclaw/logs/*/` — rotate or truncate if any single log exceeds 100 MB.
- **Playwright cache:** `du -sh ~/.cache/ms-playwright/` — Chromium profiles can balloon, especially Questrade's `questrade_browser_state/`.
- **Ollama model size:** `du -sh ~/.ollama/models/` — if models not in active use are taking space, `ollama rm <model>`.
- **Supabase row counts:** `arena_trades` grows ~50 rows/day during full-arena sessions. At 10k rows consider archiving older trades.
- **Stale refresh-token files:** if `.env` and `state/questrade_token.json` drift out of sync, you'll hit refresh failures. Keep them in lockstep.

## Related docs

- [CLAUDE.md](CLAUDE.md) — entry point, quick reference
- [ARCHITECTURE.md](ARCHITECTURE.md) — what exists and why
- [BUILD.md](BUILD.md) — zero → running
