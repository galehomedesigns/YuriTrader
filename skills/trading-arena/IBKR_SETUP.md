# IBKR Setup — Replacing Questrade for live orders

Stack:
- **IB Gateway** runs in a Docker container (`infra/ib-gateway/docker-compose.yml`),
  holds your IBKR session, exposes the API on `127.0.0.1:4002` (paper) and `:4001` (live).
- **ibkr_executor.py** (`shared/ibkr_executor.py`) talks to Gateway over localhost using `ib_async`.
- **Selector**: `STOCK_BROKER=ibkr` (vs `questrade`) in `.env` chooses which executor `stock_concierge` uses.

There is no API key. Auth = your IBKR login held by the Gateway container.

---

## Step 1 — (Skip) Enabling API

IB Gateway **accepts API connections by default** — there is no toggle to flip in Client Portal. (The "Enable API" setting people reference applies to the TWS desktop app, not Gateway, and we're using Gateway via Docker.) Your IBKR username + password is the only credential needed.

## Step 2 — Create a paper trading account (recommended)

Paper trading is separate from your live account and must be created once. Skip this step if you want to go straight to live.

1. Sign in at https://www.interactivebrokers.ca/sso/Login
2. **Settings → Account Settings → scroll to "Paper Trading Account" → click "Create" or "Enable"**
3. IBKR generates a **fresh paper username** (e.g. `kbqrog347` — unrelated to your live username) and a **paper account ID** with a `DUP#######` prefix.
4. Set a **paper password** during creation. If you missed that step, use the "Forgot Password" link at the IBKR login with the paper username — IBKR will email a reset.
5. On the paper trading screen, leave "Share real-time market data subscriptions" **Yes** (so once you subscribe to any live data, paper gets it too).

## Step 3 — Drop credentials into `.env`

Edit `/home/tonygale/openclaw/.env` (already has stubs ready):

```bash
IBKR_TRADING_MODE=paper
IBKR_USERID_PAPER=<your paper username>
IBKR_PASSWORD_PAPER=<your paper password>
IBKR_ACCOUNT_ID=DU#######        # optional; auto-detected from Gateway if blank
IBKR_VNC_PASSWORD=<any password>  # for debugging Gateway GUI via VNC
# Leave IBKR_USERID / IBKR_PASSWORD blank until you're ready for live.
IBKR_ALLOW_TRADING=false          # gate 1 — keep OFF until paper trades verified
```

## Step 4 — Bring up IB Gateway

```bash
cd /home/tonygale/openclaw/infra/ib-gateway
docker compose up -d
docker logs -f ib-gateway
```

You'll see Gateway boot, then attempt login. **Within ~60s an IBKR push notification will arrive on your phone** (IBKR Mobile → IB Key) asking to approve the login. Approve it.

When logs show `Login has completed` (or similar), Gateway is ready.

## Step 5 — Smoke test from Python

```bash
cd /home/tonygale/openclaw
source .env
.venv/bin/python skills/trading-arena/smoke_ibkr.py --symbol AAPL
```

Expected output: connection success, managed accounts list with your `DU#######`, account summary with `NetLiquidation` ≈ paper starting balance, AAPL quote.

If you see `bid=None ask=None last=None` for the quote, that's fine — it means you don't have a real-time market data subscription, but **orders still work** (they just route at market without local quote display).

## Step 6 — Test a paper order via the executor

```bash
cd /home/tonygale/openclaw
source .env
.venv/bin/python -c "
from skills.trading-arena.shared.ibkr_executor import IBKRExecutor
import os
# Open both gates just for this test
os.environ['IBKR_ALLOW_TRADING'] = 'true'
os.environ['MANUAL_STOCK_TRADING_ENABLED'] = 'true'
ex = IBKRExecutor()
print(ex.get_balance())
print(ex.execute_manual_trade('AAPL', 'buy', 1))
print(ex.get_positions())
"
```

If a position appears with `qty=1.0 AAPL`, end-to-end paper trading works.

## Step 7 — Wire into stock_concierge

`stock_concierge.py` currently imports `QuestradeExecutor` directly. To switch:

- Add a tiny factory `shared/stock_broker.py` that reads `STOCK_BROKER` and returns either `QuestradeExecutor()` or `IBKRExecutor()`.
- Replace the 5-ish `from shared.questrade_executor import QuestradeExecutor` lines with `from shared.stock_broker import get_executor`.
- Replace `QuestradeExecutor()` with `get_executor()`.

Keep both executors in the repo — Questrade can still be used for portfolio/quote reads if you want. Only ordering moves to IBKR.

## Step 8 — Go live

Once paper has been running cleanly for a session:

1. Fund the **live** account at IBKR — CAD via your bank's bill-payment ("Interactive Brokers Canada", IBKR account number as bill ref). 1-2 business days.
2. Update `.env`:
   ```bash
   IBKR_TRADING_MODE=live
   IBKR_PORT=4001
   IBKR_USERID=<live username>
   IBKR_PASSWORD=<live password>
   ```
3. Restart Gateway: `docker compose -f infra/ib-gateway/docker-compose.yml restart`
4. Approve the new login push on IBKR Mobile.
5. Re-run smoke test against port 4001.
6. Flip `IBKR_ALLOW_TRADING=true` only when you're ready for real orders.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `IB Gateway not reachable at 127.0.0.1:4002` | Container not up, or still logging in | `docker logs --tail 50 ib-gateway`; wait for "Login has completed"; check 2FA push wasn't ignored |
| Gateway logs say "Second Factor Authentication failed" | 2FA push wasn't approved within ~3 min | Restart container; approve push faster |
| `Gateway returned no managed accounts` | Login partially failed | Same — restart and re-approve 2FA |
| `Symbol 'XYZ' not recognized` | Wrong primary exchange or symbol | Most US stocks use `SMART` / `USD` — `ibkr_executor._stock()` defaults match; if it's TSX, would need `currency='CAD'` and possibly explicit exchange |
| Quote bid/ask/last all None | No market data subscription | Either subscribe at IBKR (~$10/mo US, ~$4/mo TSX) or accept that pre-trade price comes from your existing TradingView/Questrade quote source |
| Need to inspect the running Gateway GUI | VNC into the container | `vncviewer localhost:5900` (password = `IBKR_VNC_PASSWORD` from .env) |

## Daily restart note

IBKR enforces a session restart every ~24h. The compose file sets `AUTO_RESTART_TIME: "07:00 AM"` (in `TZ`) — Gateway will log out and log back in around 03:00 ET. The 2FA push will fire again at that time, so keep IBKR Mobile installed and notifications enabled. (Some users configure 2FA bypass for a paired device — see IBKR docs if the daily push gets annoying.)
