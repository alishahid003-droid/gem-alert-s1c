@echo off
REM Restarts the fast watcher cleanly (e.g. after git pull loads new code).
REM Stops EVERY running copy first -- a second copy can't write the shared log
REM ("The process cannot access the file because it is being used by another
REM process") -- then starts exactly one, minimized, like the Startup launcher.
cd /d "%~dp0"
echo Stopping any running fast watcher...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'worker_fast_watch|run_fast_watch' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('  stopped ' + $_.Name + ' ' + $_.ProcessId) }"
timeout /t 3 /nobreak > nul
echo Starting one fast watcher (minimized window "GemAlert FastWatch")...
start "GemAlert FastWatch" /min cmd /c "%~dp0run_fast_watch.bat"
timeout /t 5 /nobreak > nul
powershell -NoProfile -Command "$n = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'worker_fast_watch' }).Count; if ($n -eq 1) { Write-Host 'OK: exactly one fast watcher running.' } else { Write-Host ('WARNING: ' + $n + ' fast watcher(s) running') }"
