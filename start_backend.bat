@echo off
cd /d "%~dp0"
set PYTHONPATH=%CD%
set "PYTHON_EXE=.venv\Scripts\python.exe"
"%PYTHON_EXE%" -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo [backend] WARNING: Project .venv is unavailable; using Python from PATH.
    set "PYTHON_EXE=python"
)
"%PYTHON_EXE%" -c "import flask, numpy" >nul 2>&1
if errorlevel 1 (
    echo [backend] ERROR: Selected Python is missing required packages.
    echo [backend] Install them with: python -m pip install flask numpy
    pause
    exit /b 1
)
echo [backend] Starting API server on http://127.0.0.1:8000...
"%PYTHON_EXE%" -m app.api_server
if %errorlevel% neq 0 (
    pause
)
