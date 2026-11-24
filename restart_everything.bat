@echo off
REM ONE script that does the whole restart sequence Ali was doing by hand
REM across multiple windows (24 Nov 2026: "running multiple instances is so
REM confusing... which one is done, which one is not done"). Double-click
REM this ONE file, wait for it to finish, done -- no other window, no manual
REM git pull/stop/start steps, no judgment calls about what's already done.
REM
REM Order: pull latest code -> stop everything (real system + shadow sim,
REM using the same "GemAlert*" window-title convention stop_local_mode.bat
REM already uses) -> start the real system (start_local_mode.bat, which
REM does its own git pull + fast watcher + cycle supervisor + dashboard) ->
REM start the shadow sim -> show the status check so you SEE the result
REM instead of having to guess.
cd /d "%~dp0"

echo ================================================================
echo   STEP 1 of 4: Pulling latest code
echo ================================================================
git pull

echo.
echo ================================================================
echo   STEP 2 of 4: Stopping everything (real system + shadow sim)
echo ================================================================
taskkill /F /FI "WINDOWTITLE eq GemAlert*" /T >nul 2>&1
timeout /t 3 /nobreak > nul
echo Done.

echo.
echo ================================================================
echo   STEP 3 of 4: Starting the real system
echo ================================================================
call start_local_mode.bat

echo.
echo ================================================================
echo   STEP 4 of 4: Starting the shadow sim (paper trading)
echo ================================================================
call run_shadow_sim_v3.bat

echo.
echo Waiting 10 seconds for everything to come up before checking status...
timeout /t 10 /nobreak > nul

echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_status.ps1"

echo.
echo ================================================================
echo   ALL DONE. Everything above should say RUNNING in green.
echo   If anything says STOPPED in red, tell Claude exactly what it says.
echo ================================================================
pause
