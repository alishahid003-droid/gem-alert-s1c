@echo off
REM One --poll-madeonsol cycle (Layer 1 deployer alerts, Layer 8 deep scoring,
REM Layer 2+9 Fomo wallet activity, Layer 13 Fomo copy-trading) on this PC.
REM Registered to run every 15 minutes by install_windows_task.bat -- you
REM don't normally run this by hand. Output goes to logs\poll_madeonsol.log
REM so you can see what each cycle did.
cd /d "%~dp0"
if not exist logs mkdir logs
echo ==== %date% %time% ==== >> logs\poll_madeonsol.log
REM -u = unbuffered, so the log shows each step live instead of only when the run ends
python -u scheduler.py --poll-madeonsol >> logs\poll_madeonsol.log 2>&1
echo ==== finished %date% %time% (exit code %errorlevel%) ==== >> logs\poll_madeonsol.log
