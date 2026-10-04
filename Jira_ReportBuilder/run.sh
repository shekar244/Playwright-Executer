#!/usr/bin/env bash
# ============================================================
#  Jira Report Builder — macOS / Linux launcher
#  Run:  ./run.sh            (PORT=8600 ./run.sh to change port)
#  Virtual environment: VENV_DIR if set, else .venv or venv in this folder,
#  else .venv or venv in the parent folder; otherwise ./venv is created.
#  Missing requirements are installed automatically.
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"
PORT="${PORT:-8501}"

# Pick the virtual environment
VENV="${VENV_DIR:-}"
if [ -z "$VENV" ]; then
    for candidate in .venv venv ../.venv ../venv; do
        if [ -x "$candidate/bin/python" ]; then VENV="$candidate"; break; fi
    done
fi
VENV="${VENV:-venv}"

if [ ! -x "$VENV/bin/python" ]; then
    PYTHON=""
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" &>/dev/null && \
           "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            PYTHON="$(command -v "$candidate")"
            break
        fi
    done
    if [ -z "$PYTHON" ]; then
        echo "[ERROR] Python 3.10+ not found. Install it (e.g. brew install python@3.13) and retry."
        exit 1
    fi
    echo "[INFO] Creating virtual environment in $VENV with $PYTHON ..."
    "$PYTHON" -m venv "$VENV"
fi
PY="$VENV/bin/python"
echo "[INFO] Using virtual environment: $VENV"

if ! "$PY" -c "import streamlit, pygwalker, plotly, pandas, openpyxl, streamlit_sortables" 2>/dev/null; then
    echo "[INFO] Installing missing requirements into $VENV ..."
    # venvs created by tools such as uv have no pip — bootstrap it first
    "$PY" -m pip --version &>/dev/null || "$PY" -m ensurepip --upgrade &>/dev/null || true
    if ! "$PY" -m pip install -r requirements.txt; then
        echo ""
        echo "[ERROR] Could not install the requirements into $VENV."
        echo "        On a company network, point pip at your package mirror once, e.g."
        echo "          $PY -m pip config set global.index-url https://YOUR-MIRROR/api/pypi/pypi/simple"
        echo "        then run ./run.sh again — or install manually: $PY -m pip install -r requirements.txt"
        exit 1
    fi
fi

# Free the port if an earlier instance (or Amplify's embedded Jira Insights) still holds it.
_listeners() { (lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true) | sort -u | tr '\n' ' '; }
if command -v lsof &>/dev/null; then
    PIDS="$(_listeners)"
    if [ -n "${PIDS// /}" ]; then
        echo "[INFO] Port $PORT in use (PID ${PIDS% }) — stopping it ..."
        # shellcheck disable=SC2086
        kill $PIDS 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            [ -z "$(_listeners | tr -d ' ')" ] && break
            sleep 0.5
        done
        PIDS="$(_listeners)"
        if [ -n "${PIDS// /}" ]; then
            # shellcheck disable=SC2086
            kill -9 $PIDS 2>/dev/null || true
            sleep 0.5
        fi
    fi
fi

echo "[INFO] Jira Report Builder → http://localhost:$PORT   (Ctrl+C to stop)"
exec "$PY" -m streamlit run app.py --server.port "$PORT"
