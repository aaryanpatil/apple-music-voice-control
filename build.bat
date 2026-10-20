@echo off
rem Builds dist\Amphion\Amphion.exe (self-contained; no Python needed
rem to run it), then runs its self-test to check nothing was left out.
cd /d "%~dp0"

.venv\Scripts\python.exe -m PyInstaller amphion.spec --noconfirm --clean || exit /b 1

echo.
echo Self-test of the built exe:
set "RESULT=%TEMP%\amphion_selftest.json"
del "%RESULT%" 2>nul
start "" /wait "dist\Amphion\Amphion.exe" --selftest "%RESULT%"
if not exist "%RESULT%" (
    echo FAILED: the exe did not finish its self-test. See %APPDATA%\Amphion\error.log
    exit /b 1
)
type "%RESULT%"
findstr /c:"\"ok\": false" "%RESULT%" >nul && (echo. & echo FAILED: see above. & exit /b 1)
echo.
echo Build OK: dist\Amphion\Amphion.exe
