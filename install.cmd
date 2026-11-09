@echo off
rem Per-user LLSM install. Needs no administrator rights and can be run again.
rem Exit codes: 0 ok, 2 no Python 3.11+, 3 pip failed, 4 SCIA 24.0 not found.
setlocal
cd /d "%~dp0"

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [LLSM] Python 3.11 or newer not found. Install it from python.org and run this again.
    exit /b 2
)
echo [LLSM] Python found: %PY%

if exist ".venv\Scripts\python.exe" (
    echo [LLSM] Virtual environment already there.
) else (
    echo [LLSM] Creating the virtual environment.
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [LLSM] Could not create the virtual environment.
        exit /b 3
    )
)

echo [LLSM] Installing packages, this can take a few minutes.
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e .
if errorlevel 1 (
    echo [LLSM] pip failed; trying once more.
    ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e .
    if errorlevel 1 (
        echo [LLSM] pip failed twice. See the messages above.
        exit /b 3
    )
)
echo [LLSM] Packages installed.

reg query "HKCU\Software\SCIA\ESA\24.0\Admin\Dir" /v SYSTEM >nul 2>&1
if errorlevel 1 (
    echo [LLSM] SCIA Engineer 24.0 not found for this Windows user. Start SCIA once, or install it.
    exit /b 4
)
echo [LLSM] SCIA Engineer 24.0 found.

echo.
".venv\Scripts\llsm.exe" diagnose
exit /b 0
