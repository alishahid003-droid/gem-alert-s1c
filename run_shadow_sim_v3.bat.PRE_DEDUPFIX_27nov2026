@echo off
REM Shadow compound simulator v3 (20 Nov 2026): adds close-time confirm-check, execution
REM delay modeling, honest stuck/illiquid tracking, and a friction stress-test report mode
REM (--report). Continues the SAME equity pool from v2 (reads logs\shadow_compound_sim.jsonl
REM on startup -- does NOT reset to $115). Writes ONLY to that same file. Safe alongside live bot.
cd /d "%~dp0"
start "GemAlert ShadowSimV3" /min cmd /c "cd /d %~dp0 && python -u shadow_compound_sim_v3.py >> logs\shadow_compound_sim_stdout.log 2>&1"
echo Started v3. Log: logs\shadow_compound_sim_stdout.log  Data: logs\shadow_compound_sim.jsonl
