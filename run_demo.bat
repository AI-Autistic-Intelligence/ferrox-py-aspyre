@echo off
setlocal enabledelayedexpansion

:: ==============================================================================
:: ferrox-py-aspyre: Windows Turnkey Execution Harness
:: Evaluators: Arnout Ulenberg & Tjitze Weststrate (Aspyre B.V.)
:: ==============================================================================

:: Detect Python executable
set "PYTHON_EXE="

if exist "..\ferrox-py\venv\Scripts\python.exe" (
    set "PYTHON_EXE=..\ferrox-py\venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PYTHON_EXE=venv\Scripts\python.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_EXE=python"
    ) else (
        echo [ERROR] Python was not found in PATH or standard virtualenv locations.
        echo Please ensure Python 3.10+ is installed.
        exit /b 1
    )
)

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=demo"

if "%TARGET%"=="clean" (
    echo [CLEAN] Removing cache directories and temporary packages...
    if exist ".pytest_cache" rmdir /s /q ".pytest_cache"
    if exist "ferrox-py-aspyre-enterprise.zip" del /f /q "ferrox-py-aspyre-enterprise.zip"
    for /d /r %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
    echo [CLEAN] Completed.
    exit /b 0
)

"%PYTHON_EXE%" scripts\run_demo.py %TARGET%
exit /b %errorlevel%
