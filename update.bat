@echo off
REM One command after every change: pull the latest code, restart the fast
REM watcher and the dashboard (both load the new code). Safe to run anytime.
cd /d "%~dp0"
echo === Pulling latest code ===
git pull
echo === Restarting fast watcher ===
call restart_fast_watch.bat
echo === Restarting dashboard (http://localhost:8787) ===
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'dashboard\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
start "GemAlert Dashboard" /min cmd /c "cd /d %~dp0 && python dashboard.py"
echo === Done. Dashboard: http://localhost:8787 ===
