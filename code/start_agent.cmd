@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo [Startup failed] Python was not found in PATH.
    pause
    exit /b 1
)

python code\scripts\personal_agent.py
set "MAPAS_EXIT_CODE=%ERRORLEVEL%"

if not "%MAPAS_EXIT_CODE%"=="0" (
    echo.
    echo MAPAS exited with code %MAPAS_EXIT_CODE%.
    pause
)

exit /b %MAPAS_EXIT_CODE%
