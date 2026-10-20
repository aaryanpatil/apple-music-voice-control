@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo Amphion isn't set up on this PC yet: run setup.bat first.
    echo.
    pause
    exit /b 1
)
start "" .venv\Scripts\pythonw.exe app.py
