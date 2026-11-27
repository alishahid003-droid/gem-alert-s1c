@echo off
REM Shadow compound simulator v3 (20 Nov 2026): adds close-time confirm-check, execution
REM delay modeling, honest stuck/illiquid tracking, and a friction stress-test report mode
REM (--report). Continues the SAME equity pool from v2 (reads logs\shadow_compound_sim.jsonl
REM on startup -- does NOT reset to $115). Writes ONLY to that same file. Safe alongside live bot.
REM
REM 27 Nov 2026: added a kill-step (mirrors start_local_mode.bat's pattern) so running this
REM script twice in a row -- or running restart_everything.bat more than once -- replaces the
REM old shadow-sim process instead of stacking a duplicate one alongside it.
cd /d "%~dp0"
echo === Stopping old shadow sim copy (if any) ===
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'shadow_compound_sim_v3' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 /nobreak > nul
start "GemAlert ShadowSimV3" /min cmd /c "cd /d %~dp0 && python -u shadow_compound_sim_v3.py >> logs\shadow_compound_sim_stdout.log 2>&1"
echo Started v3. Log: logs\shadow_compound_sim_stdout.log  Data: logs\shadow_compound_sim.jsonl
