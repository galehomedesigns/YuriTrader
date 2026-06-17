# TradingView/Questrade Opening Trader — OPERATOR GUIDE (no Claude Code required)

This is the complete, self-contained guide to running the morning opening-trade
system **without Claude Code**. A local LLM can read this and walk the operator
through everything. Nothing here depends on Claude — the GX10 does the work on
cron, and the operator does the laptop startup + clicks the confirmations.

---

## 0. TL;DR — does Claude Code need to be running? NO.
- The **GX10 server** runs the scans + the 9:32 trade staging automatically via
  **cron**. It does not need Claude.
- The **operator** (you) does exactly two manual things each morning:
  1. Start the laptop trading browser + tunnel (one command).
  2. Click **Send Order / Confirm** on each order ticket that pops up.
- Everything else (scan, classify, size, stage entries/stops/close) is automatic.
- If the auto-staging ever fails, there is a **manual fallback** (§6): the system
  still texts you exactly what to do, and you place the orders by hand.

---

## 1. The pieces
- **GX10** (always-on Linux server, host `gx10-087b`, repo `/home/tonygale/openclaw`):
  runs cron jobs + the order "queue runner" that drives the laptop browser.
- **Laptop** (Windows): a dedicated **trading Chrome** logged into TradingView +
  Questrade, plus a **reverse SSH tunnel** to the GX10.
- **Tunnel:** GX10 port **9225** → laptop Chrome port **9222**.
- **Golden rule:** ONE TradingView session at a time → the trading Chrome must be
  the **only** TradingView login (sign out everywhere else).

---

## 2. DAILY STARTUP (operator, ~before 9:25 ET)
On the **laptop**, open **PowerShell** and run (you're already in your home folder):
```
powershell -ExecutionPolicy Bypass -File start_trading_browser.ps1
```
- A Chrome window opens to a TradingView chart; PowerShell prints `CDP up: Chrome/...`
  then sits on the tunnel line. **Leave that window open all session.**
- In Chrome: confirm **logged into TradingView** + **Questrade connected**.
- Sign **out** of TradingView in your everyday browser and phone app.

**Verify the link (optional, run on the GX10 over SSH):**
```
cd ~/openclaw && node skills/trading-arena/opening_agent/tv_session_sync.js --port 9225
```
Expect: `OK - logged in as tonygale; .env sessionid synced ...`

That's the whole startup. The cron handles the rest.

---

## 3. WHAT HAPPENS DURING THE SESSION (when armed)
Armed = `OPENING_TV_AUTO_STAGE=true` and `OPENING_TRADE_BUDGET_USD=<amt>` in
`/home/tonygale/openclaw/.env`. Every action is staged for your one-click confirm;
**nothing ever auto-sends.**
- **Pre-market (7:00–9:25 ET):** Telegram "Opening Power — Top N" + your TradingView
  watchlist updates. No orders.
- **9:32 ET — entries:** for each stock that passed the first-bar rule, a ticket pops
  up: `Buy N SYM @ <stop> STOP` **+ a Stop loss row**. Click **Send Order**. Next one
  appears ~1–2s later. Qty = floor((budget ÷ #matches) ÷ entry price).
- **9:32–9:50 — stop-moves:** when coached "move stop up," a **Modify Order** dialog
  opens with the new stop. Its **Confirm** button is **grayed ~1–2s while it validates,
  then enables** — wait for it to light up, then click Confirm.
- **9:50 ET — cutoff close:** for each position you actually hold, a `Sell N SYM @ <px>
  LIMIT` ticket pops up (a marketable limit). Click **Send Order** to flatten.
- Adds / take-profits = Telegram coaching only (place manually if you want them).

---

## 4. ARM / DISARM (GX10 `.env`)
- **Arm:** `OPENING_TV_AUTO_STAGE=true`, `OPENING_TRADE_BUDGET_USD=500` (or your number),
  `OPENING_TV_CDP_PORT=9225`.
- **Pause/disarm:** set `OPENING_TV_AUTO_STAGE=false`. Staging stops; you still get
  the Telegram coaching. (Cron re-reads `.env` each run — no restart needed.)
- US-stock fills need **USD buying power** — convert CAD→USD in Questrade (cash account
  needs settled USD). Until then, orders size but reject/skip for affordability.

---

## 5. TROUBLESHOOTING (decision tree)
- **Telegram says 🚨 BROKER LINK DOWN** (or pre-open check flags "Questrade broker link
  DOWN") → the Questrade↔TradingView connection dropped; orders can't route and
  auto-staging is paused. **Re-link:** on the laptop trading tab, open the bottom
  **Trading Panel → broker dropdown → reconnect Questrade** (re-enter the login if
  prompted); keep that tab the **sole** TradingView session. You'll get a ✅ "Broker
  link restored" text once it's back, and staging resumes. Coaching keeps running
  throughout, so you can place orders by hand in the meantime (§6). Check it yourself
  any time: `node tv_broker_health.js --port 9225` (→ `{"connected":true,...}`).
- **Trading Chrome says "another session / disconnected"** → something else is logged
  into the same TV account. Sign out of TV in your everyday browser + phone. GX10
  browser stays Guest. (This is a common cause of the broker link dropping.)
- **GX10 can't reach the browser** (`tv_session_sync.js` says NOT reachable; or
  `curl -s http://127.0.0.1:9225/json/version` fails) → tunnel/Chrome down. Re-run
  `start_trading_browser.ps1` on the laptop.
- **Tunnel won't bind / "remote port forwarding failed"** → old tunnel stuck. Close ALL
  laptop PowerShell windows, wait ~30s, re-run the script.
- **Watchlist sync says `login_required`** → `.env` cookie expired. Re-run
  `tv_session_sync.js --port 9225` (pulls a fresh one from the trading Chrome).
- **Stop-move: "no confirmation" / dialog vanished** → the Modify Confirm is grayed for
  ~1–2s, then enables. Wait, then click. If you don't click, it auto-dismisses and the
  stop is unchanged (original stop still protects you).
- **Order rejected "both marketable"** → thin/volatile stock; not usable for an attached
  bracket. It's logged and skipped — trade it manually if you want it.
- **Close rejected "change to limit"** → handled automatically (closes use a marketable
  limit). If closing manually, use a limit (not market) for OTC names.
- **Nothing pops up at 9:32** → either no stocks passed (you'll get a Telegram "nothing
  qualifies"), or the tunnel is down (you'd get Telegram coaching but no tickets — fix
  the tunnel, §2), or it's not armed (`OPENING_TV_AUTO_STAGE`).

---

## 6. MANUAL FALLBACK (if auto-staging fails entirely)
The Telegram coaching ALWAYS runs (it's independent of the CDP staging). So even with
the laptop/tunnel/staging completely broken, you can trade by hand:
1. Watch Telegram from the **YuriStocks** bot at 9:32 ET.
2. For each `🎯 ... passed the 2-min test`: in TradingView, set a **Buy STOP** at the
   stated entry price, with a **Stop loss** at the stated stop, and confirm.
3. As it coaches "move stop up / add / take profit / CLOSE," do those manually in the
   panel.
4. At the 20-min mark it texts `CLOSE your position now` — flatten manually.
This is the original manual mode; the automation just does the clicking-prep for you.

---

## 7. COMMAND + FILE REFERENCE
- Host: `gx10-087b` (Tailscale). Repo: `/home/tonygale/openclaw`.
- Laptop script: `%USERPROFILE%\start_trading_browser.ps1`. Profile: `%USERPROFILE%\tv-trading-profile`.
- Ports: laptop Chrome CDP **9222** → tunnel lands on GX10 **9225**.
- Config: `/home/tonygale/openclaw/.env` → `OPENING_TV_AUTO_STAGE`, `OPENING_TRADE_BUDGET_USD`,
  `OPENING_TV_CDP_PORT`, `TRADINGVIEW_SESSIONID`.
- GX10 scripts (`skills/trading-arena/opening_agent/`):
  - `tv_session_sync.js` — verify reachable+logged-in, refresh cookie. `node tv_session_sync.js --port 9225`
  - `tv_positions.js` — list real Questrade positions (JSON). `node tv_positions.js --port 9225`
  - `tv_order.js` — stage ONE order (test/manual): `node tv_order.js --port 9225 --side buy --type stop --price <p> --stop <s> --qty <n> --expect-symbol <TICKER>` (chart must be on that ticker; you confirm/Cancel).
  - `tv_order_queue.js` — stage a list from a JSON file (entries/closes/modify-stops). Used by the agent.
  - `advisory_monitor.py` — the 9:32 driver (cron). `run_opening_scan.py` — pre-market scan (cron).
- Cron (GX10, `crontab -l`): `run_opening_scan.py` at pre-market times, `advisory_monitor.py` at 9:32 ET (server-local 07:32 if GX10 is MDT). Mon–Fri.
- systemd: `tv-chrome.service` (GX10 headless Guest Chrome for chart/watchlist) — `systemctl status tv-chrome.service`.
- Cancel a stray order / close a position: TradingView Orders/Positions tab → the per-row Cancel/Close button (you confirm).

---

## 8. SAFETY INVARIANTS (never violate)
- Nothing auto-sends — a human clicks Send Order / Confirm on every order.
- Only ONE TradingView session (the trading Chrome).
- Closes are cross-checked against real held shares (no naked sells) and scoped to
  today's traded symbols.
- The attached protective stop rests at the broker the whole time the position is open.
- To stop everything instantly: `OPENING_TV_AUTO_STAGE=false` in `.env`, or just close
  the laptop tunnel (staging can't reach the browser → nothing stages).
