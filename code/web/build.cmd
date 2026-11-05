@echo off
rem ============================================================
rem  MAPAS frontend build script (Windows)
rem  Usage:  build.cmd    = typecheck + production build
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build.ps1" %*
exit /b %ERRORLEVEL%
