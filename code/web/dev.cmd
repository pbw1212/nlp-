@echo off
rem ============================================================
rem  MAPAS frontend one-click launcher (Windows)
rem  Why: PowerShell's default execution policy blocks npm.ps1/pnpm.ps1.
rem  This .cmd starts a PowerShell with -ExecutionPolicy Bypass,
rem  so pnpm/npm work without changing any system setting.
rem
rem  Usage:  dev.cmd            install (first run) + start dev server
rem          dev.cmd --check    only check Node/pnpm availability
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev.ps1" %*
exit /b %ERRORLEVEL%
