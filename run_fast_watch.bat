@echo off
REM Fast watcher (checklist 5.1): manages positions, scalper, paper trades
REM every 20 s and the revival watch every minute. Started at logon by the
REM "GemAlert FastWatch" task (setup_pc.bat). Restarts itself if it exits.
cd /d "%~dp0"
if not exist logs mkdir logs
:loop
echo ==== fast-watch start %date% %time% ==== >> logs\fast_watch.log
python -u worker_fast_watch.py >> logs\fast_watch.log 2>&1
echo ==== fast-watch exited %date% %time% (code %errorlevel%), restarting in 15s ==== >> logs\fast_watch.log
timeout /t 15 /nobreak > nul
goto loop
