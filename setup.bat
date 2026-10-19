@echo off
rem One-time setup on a new PC: creates .venv, installs the libraries and downloads
rem the speech models. Double-click it, or run .\setup.bat from PowerShell.
setlocal
cd /d "%~dp0"

echo.
echo === Voice Control setup ===
echo.
echo [1/4] Looking for Python 3.10 or newer...
set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=python"
if not defined PY goto :no_python
for /f "delims=" %%v in ('%PY% --version') do echo       Found %%v

echo.
echo [2/4] Creating the Python environment (.venv)...
if exist .venv\Scripts\python.exe (
    echo       Already there.
) else (
    %PY% -m venv .venv || goto :failed
    echo       Done.
)

echo.
echo [3/4] Installing libraries (a few minutes the first time)...
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet || goto :failed
.venv\Scripts\python.exe -m pip install -r requirements.txt || goto :failed

echo.
echo [4/4] Downloading the speech models (about 520 MB the first time)...
.venv\Scripts\python.exe download_models.py || goto :failed

echo.
echo === Setup finished. Double-click start.bat to open Voice Control. ===
goto :end

:no_python
echo.
echo   Python 3.10 or newer wasn't found.
echo   Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"),
echo   then run setup.bat again.
goto :end

:failed
echo.
echo === Setup FAILED at the step above. Scroll up to see the error. ===

:end
echo.
pause
