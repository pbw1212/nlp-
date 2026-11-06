@echo off
rem ============================================================
rem  MAPAS one-click launcher (Windows)
rem  Starts: bridge (no token) -> frontend dev server -> browser
rem
rem  Usage: double-click start.cmd, or run  .\start.cmd
rem         .\start.cmd multi    start bridge in multi-agent mode
rem         .\start.cmd check    only check environment
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
exit /b %ERRORLEVEL%
