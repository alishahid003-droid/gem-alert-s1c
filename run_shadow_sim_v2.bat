@echo off
REM Shadow compound simulator v2 (20 Nov 2026): now uses a SHARED $115 synthetic equity
REM pool (Ali's real funding plan) instead of unlimited free $100 per candidate. See the
REM safety docstring at the top of shadow_compound_sim_v2.py. Writes ONLY to
REM logs\shadow_compound_sim.jsonl. Safe to run alongside the live bot.
cd /d "%~dp0"
start "GemAlert ShadowSimV2" /min cmd /c "cd /d %~dp0 && python -u shadow_compound_sim_v2.py >> logs\shadow_compound_sim_stdout.log 2>&1"
echo Started v2. Log: logs\shadow_compound_sim_stdout.log  Data: logs\shadow_compound_sim.jsonl
