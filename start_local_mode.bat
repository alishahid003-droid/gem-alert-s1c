@echo off
REM LOCAL MODE (no Upstash needed): runs everything on this PC with a local state file.
REM Starts: fast watcher, the cycle supervisor (poll-fast/slow/madeonsol), the dashboard.
REM Requires that UPSTASH_REDIS_REST_URL / _TOKEN are NOT set (or are blank) in .env.
cd /d "%~dp0"
echo === Pulling latest code ===
git pull
echo === Stopping old copies ===
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'worker_fast_watch|run_fast_watch|local_runner|dashboard\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 3 /nobreak > nul
echo === Starting fast watcher ===
start "GemAlert FastWatch" /min cmd /c "%~dp0run_fast_watch.bat"
echo === Starting cycle supervisor ===
start "GemAlert LocalRunner" /min cmd /c "cd /d %~dp0 && python -u local_runner.py >> logs\local_runner.log 2>&1"
echo === Starting dashboard (http://localhost:8787) ===
start "GemAlert Dashboard" /min cmd /c "cd /d %~dp0 && python dashboard.py"
echo === Done. State file: .gem_alert_state.json in this folder. Dashboard: http://localhost:8787 ===
