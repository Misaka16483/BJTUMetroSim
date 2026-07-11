#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

PYTHON=".venv/Scripts/python.exe"
if [ ! -f "$PYTHON" ]; then
    echo "[backend] ERROR: .venv not found. Run: uv venv .venv --python 3.11 && uv pip install numpy flask"
    exit 1
fi

echo "[backend] Using $("$PYTHON" --version) from .venv"
export PYTHONPATH="$PWD"
echo "[backend] Starting API server on http://127.0.0.1:8000..."
exec "$PYTHON" app/api_server.py
