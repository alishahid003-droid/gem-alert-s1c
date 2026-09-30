# S1c PC setup -- does GO_LIVE_CHECKLIST items 1.3 and 1.4 for you, safely.
# Run it by double-clicking setup_pc.bat (not this file directly).
#
# What it does, in order, stopping on any problem:
#   1. Checks you're in the gem-alert folder and pulls the latest main.
#   2. Backs up .env (to .env.backup-<date-time>) BEFORE touching it.
#   3. Removes the old $3/$7 test lines (STAGE1_POSITION_USD,
#      STAGE2_POSITION_USD) and sets TOTAL_WALLET_USD to the amount you type.
#   4. Finds any OTHER scheduled task that already runs the MadeOnSol job
#      (e.g. the old run_madeonsol_hidden.vbs) -- two jobs would double-spend
#      the 200/day MadeOnSol budget -- and disables it after asking you.
#   5. Registers "GemAlert MadeOnSol" every 15 minutes and starts one run now.
# Nothing here touches wallet keys or turns trading on.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Step($t) { Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "    OK: $t" -ForegroundColor Green }
function Warn($t) { Write-Host "    NOTE: $t" -ForegroundColor Yellow }
function Fail($t) { Write-Host "    STOPPED: $t" -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }

# ---- 1. Right folder + latest code ----------------------------------------
Step "Checking folder and pulling the latest code"
if (-not (Test-Path "scheduler.py") -or -not (Test-Path ".git")) {
    Fail "This isn't the gem-alert-s1c folder. Put setup_pc.bat in that folder and run it there."
}
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne "main") {
    Warn "You're on branch '$branch' -- switching to main."
    git checkout main
    if ($LASTEXITCODE -ne 0) { Fail "Could not switch to main (see message above). Nothing else was changed." }
}
git pull origin main
if ($LASTEXITCODE -ne 0) { Fail "git pull failed (see message above). Nothing else was changed." }
if (-not (Test-Path "install_windows_task.bat")) { Fail "Pulled code is missing install_windows_task.bat -- tell Claude." }
Ok "code is up to date on main"

# ---- 2 + 3. .env backup and clean-up ---------------------------------------
Step "Cleaning the test settings out of .env"
if (-not (Test-Path ".env")) { Fail ".env not found in this folder -- your API keys live there. Tell Claude." }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Copy-Item ".env" ".env.backup-$stamp"
Ok "backup saved as .env.backup-$stamp"

$lines = Get-Content ".env"
$removed = $lines | Where-Object { $_ -match '^\s*(STAGE1_POSITION_USD|STAGE2_POSITION_USD|TOTAL_WALLET_USD)\s*=' }
$kept    = $lines | Where-Object { $_ -notmatch '^\s*(STAGE1_POSITION_USD|STAGE2_POSITION_USD|TOTAL_WALLET_USD)\s*=' }
if ($removed) { $removed | ForEach-Object { Ok "removed: $_" } } else { Warn "no test lines found (already clean)" }

$amount = $null
while ($null -eq $amount) {
    $raw = Read-Host "How many US dollars will be in the TRADING wallet? (just the number, e.g. 50)"
    $parsed = 0.0
    if ([double]::TryParse($raw, [ref]$parsed) -and $parsed -gt 0 -and $parsed -lt 1000000) { $amount = $parsed }
    else { Warn "please type a number greater than 0" }
}
$kept += "TOTAL_WALLET_USD=$amount"
# UTF-8 WITHOUT a byte-order mark: Windows PowerShell's "-Encoding UTF8"
# adds one, which would silently corrupt the first key in .env.
[System.IO.File]::WriteAllLines((Join-Path $PSScriptRoot ".env"), [string[]]$kept, (New-Object System.Text.UTF8Encoding($false)))
Ok "TOTAL_WALLET_USD=$amount written (position sizes are calculated from it automatically)"
Warn "Also add the same number as the GitHub Secret TOTAL_WALLET_USD (checklist item 1.5)."

# ---- 4. Duplicate scheduled tasks ------------------------------------------
Step "Looking for other scheduled tasks that already run the MadeOnSol job"
$dupes = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
    $_.TaskName -ne "GemAlert MadeOnSol" -and $_.State -ne "Disabled" -and (
        ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " "
    ) -match '(?i)madeonsol'
})
if ($dupes.Count -eq 0) {
    Ok "none found"
} else {
    foreach ($t in $dupes) {
        $act = ($t.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " ; "
        Write-Host "    Found: '$($t.TaskPath)$($t.TaskName)'  runs: $act" -ForegroundColor Yellow
    }
    $ans = Read-Host "Disable these so the job doesn't run twice (double MadeOnSol spend)? Type Y to disable"
    if ($ans -match '^[Yy]') {
        foreach ($t in $dupes) { Disable-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Out-Null; Ok "disabled $($t.TaskName) (not deleted -- re-enable any time in Task Scheduler)" }
    } else { Warn "left as-is -- the job may run twice per cycle" }
}

# ---- 5. Register + start the task ------------------------------------------
Step "Registering 'GemAlert MadeOnSol' (every 15 minutes)"
$bat = Join-Path $PSScriptRoot "run_poll_madeonsol.bat"
schtasks /create /tn "GemAlert MadeOnSol" /sc minute /mo 15 /f /tr "cmd /c `"$bat`"" | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Task registration failed -- right-click setup_pc.bat and choose 'Run as administrator'." }
Ok "registered"
schtasks /run /tn "GemAlert MadeOnSol" | Out-Null
Ok "first run started now (log: $PSScriptRoot\logs\poll_madeonsol.log)"

# ---- 6. Fast watcher (checklist 5.1) ----------------------------------------
Step "Registering 'GemAlert FastWatch' (20-second position/scalper watcher, starts at logon)"
$fw = Join-Path $PSScriptRoot "run_fast_watch.bat"
schtasks /create /tn "GemAlert FastWatch" /sc onlogon /f /tr "cmd /c `"$fw`"" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Warn "Could not register the logon task (needs 'Run as administrator'). Starting it for this session only."
} else {
    Ok "registered (starts automatically every time you log in)"
}
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'worker_fast_watch' }
if ($running) { Ok "fast watcher already running" }
else { Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$fw`"" -WindowStyle Minimized; Ok "fast watcher started now (log: $PSScriptRoot\logs\fast_watch.log)" }

Step "All done"
Write-Host "    Within ~15 minutes the dashboard System tab should show:" -ForegroundColor Green
Write-Host "      poll-madeonsol   last cycle Xm ago   local-pc" -ForegroundColor Green
Write-Host "      fast-watch       last cycle Xs ago   local-pc" -ForegroundColor Green
Write-Host "    If your dashboard is open: close it (Ctrl+C in its window), run  python dashboard.py  again."
Read-Host "Press Enter to close"
