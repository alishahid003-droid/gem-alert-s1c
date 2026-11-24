# Single status check (24 Nov 2026, Ali: "I need one command that tells me
# plainly what's running"). Read-only -- never starts, stops, or touches
# anything, only reports. Uses Get-CimInstance CommandLine matching, the
# same convention as stop_local_mode.bat, so this detects the real process
# even if its window was renamed/minimized/hidden, not just a window-title
# guess. A real .ps1 file instead of inline cmd -Command text so there is no
# nested-quote/line-continuation fragility to break silently later.

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "================================================================"
Write-Host "  GEM ALERT STATUS CHECK"
Write-Host "================================================================"
Write-Host ""

$procs = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match 'local_runner|worker_fast_watch|run_fast_watch|dashboard\.py|shadow_compound_sim_v3'
}

function Show-Proc($label, $pattern) {
    $p = $procs | Where-Object { $_.CommandLine -match $pattern } | Select-Object -First 1
    if ($p) {
        Write-Host ("  [RUNNING]  {0}  (PID {1})" -f $label, $p.ProcessId) -ForegroundColor Green
    } else {
        Write-Host ("  [STOPPED]  {0}" -f $label) -ForegroundColor Red
    }
}

Show-Proc "Real system - cycle supervisor (local_runner.py)" "local_runner"
Show-Proc "Real system - fast watcher"                        "worker_fast_watch|run_fast_watch"
Show-Proc "Real system - dashboard (localhost:8787)"           "dashboard\.py"
Show-Proc "Shadow sim v3 (paper trading)"                      "shadow_compound_sim_v3"

Write-Host ""
Write-Host "----------------------------------------------------------------"
Write-Host "  LAST ACTIVITY (how fresh is each log)"
Write-Host "----------------------------------------------------------------"

function Show-Age($path, $label) {
    if (Test-Path $path) {
        $mins = [math]::Round(((Get-Date) - (Get-Item $path).LastWriteTime).TotalMinutes, 1)
        if ($mins -gt 60) { $color = "Red" } elseif ($mins -gt 20) { $color = "Yellow" } else { $color = "Green" }
        Write-Host ("  {0}: last updated {1} min ago" -f $label, $mins) -ForegroundColor $color
    } else {
        Write-Host ("  {0}: file not found yet" -f $label) -ForegroundColor DarkGray
    }
}

Show-Age "logs\candidate_eval.jsonl"      "Coin scanning feed    "
Show-Age "logs\local_poll-fast.log"       "Poll-fast cycle log   "
Show-Age "logs\shadow_compound_sim.jsonl" "Shadow sim trade log  "

Write-Host ""
Write-Host "================================================================"
Write-Host "  RED = needs attention."
Write-Host "  Real system stopped, or a real-system log silent over an hour"
Write-Host "    -> run start_local_mode.bat"
Write-Host "  Shadow sim stopped, or its log silent over an hour"
Write-Host "    -> run run_shadow_sim_v3.bat"
Write-Host "================================================================"
