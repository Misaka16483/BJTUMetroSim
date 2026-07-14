#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

PYTHON=".venv/Scripts/python.exe"
if [ ! -f "$PYTHON" ]; then
    echo "[backend] ERROR: .venv not found. Run: uv venv .venv --python 3.11 && uv pip install numpy flask"
    exit 1
fi

echo "[backend] Using $("$PYTHON" --version) from .venv"
# WSL 下需将路径转为 Windows 格式供 Windows Python 使用
if command -v wslpath &>/dev/null; then
    export PYTHONPATH="$(wslpath -w "$PWD")"
else
    export PYTHONPATH="$PWD"
fi
echo "[backend] Starting API server on http://127.0.0.1:8000..."
exec "$PYTHON" app/api_server.py
