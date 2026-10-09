@echo off
REM Fixes the flaky DNS that is blocking api.fomoapi.io (NameResolutionError 11001).
REM Sets DNS to Cloudflare 1.1.1.1 + Google 8.8.8.8 on every connected network adapter.
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Please RIGHT-CLICK this file and choose "Run as administrator".
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix_dns.ps1"
ipconfig /flushdns
echo.
echo Testing the Fomo API host:
nslookup api.fomoapi.io 1.1.1.1
echo.
echo If you see an Address above, DNS is fixed. Now restart start_local_mode.bat.
pause
