@echo off
REM Double-click me. Runs setup_pc.ps1 (checklist items 1.3 + 1.4) --
REM see that file's header for exactly what it does.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_pc.ps1"
