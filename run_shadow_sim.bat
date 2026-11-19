@echo off
REM Shadow compound simulator (19 Nov 2026, Ali's explicit ask): continuous, short-interval
REM simulation of compound's real exit ladder against real live price data for every real
REM compound-qualifying robinhood_chain/base candidate -- fills the gap winners_tracker.py's
REM +3h/+24h/+72h coarse revisit cadence leaves. NEVER touches real trading state -- see the
REM safety docstring at the top of shadow_compound_sim.py. Writes ONLY to
REM logs\shadow_compound_sim.jsonl. Safe to run alongside the live bot.
cd /d "%~dp0"
start "GemAlert ShadowSim" /min cmd /c "cd /d %~dp0 && python -u shadow_compound_sim.py >> logs\shadow_compound_sim_stdout.log 2>&1"
echo Started. Log: logs\shadow_compound_sim_stdout.log  Data: logs\shadow_compound_sim.jsonl
