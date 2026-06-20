# install_autostart.ps1  -  RUN THIS ONCE ON YOUR WINDOWS LAPTOP
#
# Registers a Task Scheduler job that runs start_trading_browser.ps1 automatically
# every time you log in to Windows. This is what makes the Opening-Power tunnel
# "pick up" on its own after a reboot / Windows reload - without it, the laptop
# Chrome + reverse SSH tunnel only come up when you run the script by hand, and a
# Windows reinstall silently loses any auto-start that lived only on the machine.
#
# Run it from an ordinary (non-admin) PowerShell - the task runs as YOU at logon,
# in your desktop session, which is what the GUI Chrome + saved TradingView
# profile need.
#
#   powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1
#
# Re-run any time to update the task. Use -Uninstall to remove it.
#
# PREREQUISITE (the usual reason auto-start silently fails):
#   The tunnel SSHes to the GX10 unattended, so key-based (passwordless) auth must
#   work. Verify BEFORE relying on this:
#       ssh -o BatchMode=yes tonygale@gx10-087b true
#   No output + exit 0 = good. "Permission denied" = set up an SSH key first, or
#   the task will launch Chrome but never establish the tunnel.

param(
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$TaskName = "OpeningPowerTradingBrowser"
$ScriptPath = Join-Path $PSScriptRoot "start_trading_browser.ps1"

if ($Uninstall) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
  } else {
    Write-Host "No scheduled task '$TaskName' found - nothing to remove."
  }
  return
}

if (-not (Test-Path $ScriptPath)) {
  throw "start_trading_browser.ps1 not found next to this installer ($ScriptPath). Keep both files in the same folder."
}

Write-Host "Registering logon auto-start for: $ScriptPath"

# Run start_trading_browser.ps1 minimized so the tunnel window is out of the way
# but still visible/closable (closing it drops the tunnel, by design).
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-ExecutionPolicy Bypass -WindowStyle Minimized -File `"$ScriptPath`""

# At logon of the current user, with a short delay so network + desktop are ready.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT30S"

# Survive flaky startup conditions: only run when the network is up, never let
# Windows stop it for "idle"/battery reasons, and restart it if it dies.
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -DontStopOnIdleEnd `
  -StartWhenAvailable `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Hours 0)   # 0 = no time limit; tunnel runs all day

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName `
  -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
  -Description "Opening-Power: launch trading Chrome (CDP 9222) + reverse SSH tunnel to GX10 at logon." `
  -Force | Out-Null

Write-Host "Done. '$TaskName' will run at every logon."
Write-Host ""
Write-Host "Test it now without rebooting:"
Write-Host "    Start-ScheduledTask -TaskName $TaskName"
Write-Host "Then on the GX10 confirm the tunnel is up:"
Write-Host "    curl -s http://127.0.0.1:9225/json/version"
