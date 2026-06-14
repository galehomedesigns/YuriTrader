# TradingView → Questrade CDP Trading — Runbook

## How it's wired (mental model)
- **GX10** (always-on server): runs the opening agent, cron, `advisory_monitor.py`, and the order **queue runner**. Also runs `tv-chrome.service` (a headless Chrome, logged OUT/Guest, only for chart-switching).
- **Laptop** (Windows): runs the **dedicated trading Chrome** (logged into TradingView + Questrade) with remote-control on, plus a **reverse SSH tunnel** so the GX10 can drive it.
- **Tunnel:** GX10 port **9225** → laptop Chrome port **9222**.
- **Golden rule:** only **ONE** TradingView session at a time → the **trading Chrome must be the sole login**.

---

## A. EVERY TRADING DAY (startup, ~before 9:25 ET)
- On the laptop, open **one** PowerShell window.
- Run: `powershell -ExecutionPolicy Bypass -File $HOME\start_trading_browser.ps1`
- Wait for `CDP up: Chrome/...`; it then sits on the tunnel line — **leave this window open all session**.
- In the Chrome window that opens: confirm **logged into TradingView** + **Questrade connected** (Trading panel shows your account).
- Make sure TradingView is **signed out** in your everyday browser and phone app (else they kick the trading Chrome).
- On the GX10, verify the link + sync the cookie: `cd ~/openclaw && node skills/trading-arena/opening_agent/tv_session_sync.js --port 9225`
  - Expect: `OK - logged in as tonygale; .env sessionid synced ...`
- That's it — at 9:32 the confirmations will pop up; review each and click **Send Order**.

---

## A2. DURING THE SESSION — order lifecycle & exits (when armed)
When `OPENING_TV_AUTO_STAGE=true`, the agent stages each action for your one-click
confirmation. You always click; it never sends.
- **~9:32 — entries:** for each stock that passed the first-bar rule, a confirmation
  pops up: `Buy N SYM @ <stop> STOP` **+ a Stop loss row**. Click **Send Order**. The
  next one appears ~1-2s after each. (qty = floor((budget ÷ matches) ÷ entry).)
- **9:32-9:50 — stop-moves (trailing):** when the coach says "move stop up," a
  **Modify Order** dialog opens with the new stop price. Its **Confirm** button is
  **grayed for a second or two while TradingView validates**, then enables — **wait
  for it to light up, then click Confirm.** This reprices the stop in place (no gap).
- **9:50 — cutoff close:** for each position you actually hold (cross-checked against
  the real Questrade positions, sized to held shares), a `Sell N SYM @ <px> LIMIT`
  confirmation pops up (a "marketable limit" priced through the market so it fills
  like a market order but works on order-type-restricted securities). Click **Send
  Order** on each to flatten.
- **Adds / take-profits:** Telegram coaching only — you place those manually.
- If a stock is too thin/volatile, an attached stop or order may be **rejected**
  ("both marketable" / "change to limit"); the queue logs it and moves on.

## B. IF THE LAPTOP REBOOTS / CRASHES
- Reboot it, log in.
- Open one PowerShell → run `powershell -ExecutionPolicy Bypass -File $HOME\start_trading_browser.ps1`
- The trading Chrome relaunches with its saved profile (usually still logged in). If it shows logged out / "another session": log into TradingView again + reconnect Questrade.
- On the GX10: `node skills/trading-arena/opening_agent/tv_session_sync.js --port 9225` (re-verifies + re-syncs the cookie).
- Done — nothing on the GX10 needs restarting.

---

## C. IF THE GX10 REBOOTS
- `tv-chrome.service` auto-starts on boot (it's enabled) and cron jobs persist — nothing to do there.
- The **tunnel is initiated by the laptop**, so it dropped when the GX10 went down. On the laptop: press **Ctrl+C** in the tunnel PowerShell (if still open), then re-run `start_trading_browser.ps1` to re-establish it.
- On the GX10, confirm: `curl -s http://127.0.0.1:9225/json/version` → should print a `Chrome/...` version.
- Then: `node skills/trading-arena/opening_agent/tv_session_sync.js --port 9225`
- (Optional sanity) `systemctl status tv-chrome.service` → should be `active (running)`.

---

## D. FIRST-TIME / FROM-SCRATCH SETUP
**GX10 side (one-time, already done — only if rebuilding):**
- Repo at `/home/tonygale/openclaw`; `.env` present.
- `tv-chrome.service` installed + enabled (system service, runs as `tonygale`).
- `.env` contains: `TRADINGVIEW_SESSIONID=...`, `OPENING_TV_CDP_PORT=9225`, and (to arm) `OPENING_TV_AUTO_STAGE=true`, `OPENING_TRADE_BUDGET_USD=<amount>`.
- cron runs `run_opening_scan.py` (pre-market) and `advisory_monitor.py` (9:32 ET).

**Laptop side (one-time):**
- Install Google Chrome.
- Confirm SSH to the GX10 works: `ssh tonygale@gx10-087b "echo ok"` (Tailscale).
- Download the launch script:
  `scp tonygale@gx10-087b:/home/tonygale/openclaw/skills/trading-arena/opening_agent/laptop/start_trading_browser.ps1 $HOME\start_trading_browser.ps1`
- Verify it: `Select-String -Path $HOME\start_trading_browser.ps1 -Pattern "REMOTE_PORT ="` → should show `9225`.
- Run it: `powershell -ExecutionPolicy Bypass -File $HOME\start_trading_browser.ps1`
- In the new Chrome (a fresh `tv-trading-profile`, logged out): **log into TradingView** and **connect Questrade** in the Trading panel. (Persists in this profile after.)

**Link + cookie (one-time + whenever it expires):**
- On the GX10: `node skills/trading-arena/opening_agent/tv_session_sync.js --port 9225`

**Arm it (only when ready to trade live):**
- In `.env`: `OPENING_TV_AUTO_STAGE=true` and `OPENING_TRADE_BUDGET_USD=<your $ budget>`.
- Ensure USD buying power exists in Questrade (convert CAD→USD; it's a cash account, needs settled USD for US stocks).

---

## E. TROUBLESHOOTING
- **"Another session / disconnected" on the trading Chrome** → something else is logged into the same TradingView account. Sign out of TV in your everyday browser + phone. (GX10 browser is already Guest.)
- **GX10 can't reach the browser** (`tv_session_sync.js` says NOT reachable, or `curl ...9225/json/version` fails) → the tunnel/Chrome is down. Re-run `start_trading_browser.ps1` on the laptop.
- **Tunnel won't bind / "remote port forwarding failed"** → an old tunnel is stuck. Close ALL PowerShell windows on the laptop, wait ~30s, re-run the script. (The script self-cleans dead tunnels now.)
- **Watchlist sync says `login_required`** → the `.env` cookie expired. Re-run `tv_session_sync.js --port 9225` (pulls a fresh one from the trading Chrome).
- **Order rejected "both marketable"** → the stock is too thin/volatile and the stop is too close to market; that name just isn't usable for an attached bracket. The queue logs it and moves on.
- **Stop-move "no confirmation" / dialog vanished** → the Modify dialog's Confirm is **grayed for ~1-2s while it validates**, then enables. Wait for it to light up, then click. If you don't click, it auto-dismisses and the stop is unchanged (your original stop still protects you).
- **Close rejected "change to limit"** → handled automatically (closes use a marketable limit), but if you ever close manually, use a limit for OTC/restricted names, not market.
- **Re-arm safety / pause trading** → set `OPENING_TV_AUTO_STAGE=false` in `.env` (staging stops; advisory still coaches via Telegram).

---

## F. QUICK REFERENCE
- GX10 host: `gx10-087b` (Tailscale). Repo: `/home/tonygale/openclaw`.
- Laptop script: `%USERPROFILE%\start_trading_browser.ps1`. Trading profile: `%USERPROFILE%\tv-trading-profile`.
- Ports: laptop Chrome CDP **9222**; tunnel lands on GX10 **9225**.
- Key scripts (GX10, `skills/trading-arena/opening_agent/`): `tv_session_sync.js` (verify+cookie), `tv_order_queue.js` (the 9:32 stager), `tv_order.js` (single test), `advisory_monitor.py` (9:32 driver).
- Manual single-order test: `node skills/trading-arena/opening_agent/tv_order.js --port 9225 --side buy --type stop --price <above> --stop <below> --qty 1 --expect-symbol <TICKER>` (chart must be on that ticker; click Cancel).
- Reminder: YOU run all order/CDP commands on the GX10 by hand (they touch the live broker). Every order is your **Send Order** click.
