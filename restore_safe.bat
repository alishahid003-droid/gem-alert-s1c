@echo off
cd /d "%~dp0"
copy /Y .env.SAFE_BACKUP_20261014 .env
copy /Y executor\paper_ledger.py.SAFE_BACKUP_20261014 executor\paper_ledger.py
echo.
echo RESTORED settings AND exit rules to the safe version. Now restart: taskkill /F /IM python.exe  then  start_local_mode.bat
pause
