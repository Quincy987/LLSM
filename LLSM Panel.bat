@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo [LLSM] venv not found at .venv\Scripts\pythonw.exe
    echo [LLSM] run: python -m venv .venv ^&^& .venv\Scripts\pip install -e .
    pause
    exit /b 1
)
start "LLSM Panel" ".venv\Scripts\pythonw.exe" -m llsm.panel %*
