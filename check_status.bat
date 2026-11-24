@echo off
REM Single status check (24 Nov 2026, Ali: "I need one command that tells me
REM plainly what's running"). Just double-click this file. Read-only --
REM never starts, stops, or touches anything. The real work is in
REM check_status.ps1 (a real PowerShell script, not inline text here, so
REM there's no fragile quoting to silently break later).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_status.ps1"
echo.
pause
