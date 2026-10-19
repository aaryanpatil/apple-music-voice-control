@echo off
rem One-time setup on a new PC: creates .venv, installs the libraries and downloads
rem the speech models. Needs Python 3.13 or newer on PATH.
cd /d "%~dp0"

if not exist .venv (
    python -m venv .venv || exit /b 1
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt || exit /b 1
.venv\Scripts\python.exe download_models.py || exit /b 1

echo.
echo Setup done. Run start.bat to open Voice Control, or build.bat to build the exe.
