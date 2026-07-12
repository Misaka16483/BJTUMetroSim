@echo off
cd /d "%~dp0"
echo [backend] Activating virtual environment...
call .venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo [backend] ERROR: Failed to activate .venv. Run 'uv venv .venv --python 3.11 && uv pip install numpy flask' first.
    pause
    exit /b 1
)
set PYTHONPATH=%CD%
echo [backend] Starting API server on http://127.0.0.1:8000...
python app\api_server.py
if %errorlevel% neq 0 (
    pause
)
