@echo off
REM Registers (or re-registers) the Windows Task Scheduler job that runs
REM run_poll_madeonsol.bat every 15 minutes, starting now, hidden, whether
REM or not a console window is open. Double-click once. Safe to re-run: /f
REM replaces an existing task of the same name.
REM
REM Why this exists (Sept 30 2026): the MadeOnSol-dependent layers only run
REM on your PC (MadeOnSol blocks GitHub's shared IPs), and there was no way
REM to tell whether run_madeonsol_hidden.vbs was ever registered on a timer.
REM Every cycle now writes a heartbeat -- the dashboard's System tab shows
REM "poll-madeonsol: last cycle Xm ago (local-pc)" once this is working.
cd /d "%~dp0"
schtasks /create /tn "GemAlert MadeOnSol" /sc minute /mo 15 /f ^
  /tr "cmd /c \"%~dp0run_poll_madeonsol.bat\""
if %errorlevel%==0 (
  echo.
  echo Registered "GemAlert MadeOnSol" -- runs every 15 minutes.
  echo Check it:   schtasks /query /tn "GemAlert MadeOnSol"
  echo Logs:       %~dp0logs\poll_madeonsol.log
  echo Remove it:  schtasks /delete /tn "GemAlert MadeOnSol" /f
) else (
  echo.
  echo Registration FAILED -- try right-click, "Run as administrator".
)
pause
