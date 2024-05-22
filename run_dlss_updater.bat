@echo off
:: Check if running as administrator
NET SESSION >nul 2>&1
if %errorLevel% == 0 (
    echo Running with administrative privileges...
    cd /d %~dp0
    python main.py
) else (
    echo Requesting administrative privileges...
    powershell -Command "Start-Process cmd -ArgumentList '/c cd /d %~dp0 && python main.py' -Verb RunAs"
)
pause
