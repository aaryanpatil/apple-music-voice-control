@echo off
rem One-time setup on a new PC: creates .venv, installs the libraries, downloads the
rem speech models and checks Windows lets it all run. Double-click it, or run
rem .\setup.bat from PowerShell.
setlocal
cd /d "%~dp0"

echo.
echo === Voice Control setup ===
echo.
echo [1/5] Looking for 64-bit Python 3.10 to 3.14...
rem The speech libraries only come ready-made for 64-bit Intel/AMD Windows, and
rem some (ctranslate2, winrt) not yet for Python 3.15, so try each installed
rem Python (newest first) until one fits.
set "CHECK=import sys, platform; sys.exit(not ((3, 10) <= sys.version_info[:2] <= (3, 14) and platform.machine() == 'AMD64' and sys.maxsize > 2**32))"
set "PY="
for %%v in (3.14 3.13 3.12 3.11 3.10) do if not defined PY (
    py -%%v -c "%CHECK%" >nul 2>&1 && set "PY=py -%%v"
)
if not defined PY python -c "%CHECK%" >nul 2>&1 && set "PY=python"
if not defined PY goto :no_python
for /f "delims=" %%i in ('%PY% -c "import sys, platform; print(sys.version.split()[0], platform.machine(), sys.executable)"') do echo       Using Python %%i

echo.
echo [2/5] Creating the Python environment (.venv)...
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -c "%CHECK%" >nul 2>&1 || (
        echo       The existing one was made with an unsupported Python; recreating it.
        rmdir /s /q .venv
    )
)
if exist .venv\Scripts\python.exe (
    echo       Already there.
) else (
    %PY% -m venv .venv || goto :failed
    echo       Done.
)

echo.
echo [3/5] Installing libraries (a few minutes the first time)...
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet || goto :failed
rem --prefer-binary: use ready-made packages (an older version if need be) rather
rem than compiling one, which needs build tools most PCs don't have.
.venv\Scripts\python.exe -m pip install --prefer-binary -r requirements.txt || goto :pip_failed
rem faster-whisper without its declared dependencies: they'd pull in av and
rem onnxruntime, which the app doesn't use (see whisper_loader.py). The ones it
rem does need are in requirements.txt.
.venv\Scripts\python.exe -m pip install --prefer-binary --no-deps faster-whisper || goto :pip_failed

echo.
echo [4/5] Downloading the speech models (about 520 MB the first time)...
.venv\Scripts\python.exe download_models.py || goto :failed

echo.
echo [5/5] Checking that Windows lets everything run...
.venv\Scripts\python.exe check_blocked.py || goto :blocked

echo.
echo === Setup finished. Double-click start.bat to open Voice Control. ===
goto :end

:no_python
echo.
echo   No suitable Python found. Voice Control needs 64-bit Python 3.10 to 3.14 on an
echo   Intel/AMD PC. Python 3.15 is too new: some of its libraries (Whisper's engine,
echo   the Windows media controls) don't have builds for it yet. ARM and 32-bit
echo   Python aren't supported either.
echo.
echo   Pythons on this PC:
py -0p 2>nul || echo     (the py launcher isn't installed)
echo.
echo   Install Python 3.14 alongside any newer one (they don't conflict):
echo     - if you have the Python install manager, run:  py install 3.14
echo     - or get the 3.14 "Windows installer (64-bit)" from
echo       https://www.python.org/downloads/windows/
echo   Then run setup.bat again; it picks 3.14 automatically.
goto :end

:blocked
echo.
echo   Some parts can't run on this PC. Those marked BLOCKED are being refused by
echo   Windows (Smart App Control, or a work policy): see "Troubleshooting" in
echo   README.md, and send the list above.
goto :failed

:pip_failed
echo.
echo   A library failed to install. If the error mentions "building wheel", there's no
echo   ready-made copy for this Python: install 64-bit Python 3.13 from python.org, delete
echo   the .venv folder, and run setup.bat again.
echo   If it says "an Application Control policy has blocked this file", Windows is
echo   blocking unsigned files (Smart App Control, or a work PC's policy): see
echo   "Troubleshooting" in README.md.

:failed
echo.
echo === Setup FAILED at the step above. Scroll up to see the error. ===

:end
echo.
pause
