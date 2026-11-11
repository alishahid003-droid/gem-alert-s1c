@echo off
REM Stops all GemAlert local-mode processes cleanly in one shot.
REM Kills the whole window tree for each GemAlert-titled window (fast watcher,
REM cycle supervisor/local_runner, dashboard) so the auto-restart supervisor
REM inside local_runner.py cannot respawn a killed child - the parent dies too.
REM Safe: only matches windows titled "GemAlert*", started by start_local_mode.bat.
REM Does NOT touch any other cmd/python windows you have open (other ventures, etc).
echo === Stopping GemAlert local-mode processes ===
taskkill /F /FI "WINDOWTITLE eq GemAlert*" /T >nul 2>&1
timeout /t 2 /nobreak > nul
echo === Verifying ===
tasklist /v | findstr /i "python.exe gemalert"
if errorlevel 1 (
    echo === Done. No GemAlert processes remain. ===
) else (
    echo === WARNING: something still showing above - check manually before restarting. ===
)
