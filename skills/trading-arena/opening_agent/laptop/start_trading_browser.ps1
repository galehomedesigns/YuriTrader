# start_trading_browser.ps1  -  RUN THIS ON YOUR WINDOWS LAPTOP
#
# Launches a dedicated "trading" Chrome (Chrome DevTools Protocol on
# 127.0.0.1:9222, loopback only) and opens a reverse SSH tunnel to the GX10 so
# the opening agent can drive your TradingView order tickets through Questrade.
#
# Run it each morning ~30-60 min before the US open. The first time, log into
# TradingView and connect Questrade in the window that appears - that login
# persists in the dedicated trading profile, so future mornings just need this
# script (and a Questrade re-auth only if TradingView drops the broker session).
#
# Leave the window OPEN while you trade - closing it drops the tunnel.

$ErrorActionPreference = "Stop"

# --- config ---
$GX10_USER   = "tonygale"
$GX10_HOST   = "gx10-087b"     # the address your laptop uses to SSH to the GX10
$CDP_PORT    = 9222            # CDP port on THIS laptop (loopback only)
$REMOTE_PORT = 9225            # port on the GX10 that maps back to this laptop
$PROFILE_DIR = "$env:USERPROFILE\tv-trading-profile"

# --- find Chrome ---
$chrome = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LocalAppData\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { throw "Chrome not found. Edit the chrome path list in this script." }

# --- launch the trading Chrome ---
#   --remote-allow-origins=* is required so CDP accepts the tunneled connection
#   (Chrome 111+ rejects DevTools websockets from a mismatched Host otherwise).
Write-Host "Launching trading Chrome (CDP 127.0.0.1:$CDP_PORT, profile $PROFILE_DIR)..."
Start-Process $chrome -ArgumentList @(
  "--remote-debugging-port=$CDP_PORT",
  "--remote-debugging-address=127.0.0.1",
  "--remote-allow-origins=*",
  "--user-data-dir=`"$PROFILE_DIR`"",
  "--no-first-run",
  "--no-default-browser-check",
  "https://www.tradingview.com/chart/"
)

Start-Sleep -Seconds 3
try {
  $v = Invoke-RestMethod -Uri "http://127.0.0.1:$CDP_PORT/json/version" -TimeoutSec 5
  Write-Host ("  CDP up: " + $v.Browser)
} catch {
  Write-Warning "  CDP not responding yet on $CDP_PORT - give Chrome a few seconds."
}

# --- reverse tunnel: GX10:9223 -> this laptop:9222 ---
Write-Host ""
Write-Host "Opening reverse SSH tunnel to ${GX10_USER}@${GX10_HOST}  (GX10:$REMOTE_PORT -> laptop:$CDP_PORT)"
Write-Host "Keep THIS window open while trading. Press Ctrl+C to stop the tunnel."
Write-Host ""
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -R "${REMOTE_PORT}:127.0.0.1:${CDP_PORT}" "${GX10_USER}@${GX10_HOST}"
