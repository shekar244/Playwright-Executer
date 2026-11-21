#!/usr/bin/env bash
# ============================================================
#  Amplify QEA — macOS / Linux Launcher
#  Double-click or run:  bash amplify.sh
# ============================================================
set -euo pipefail

EXECUTER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "============================================================"
echo "  Amplify QEA"
echo "============================================================"
echo "  Launcher root : $EXECUTER_ROOT"
echo ""

# ----------------------------------------------------------
# Python environment: ./venv, created on first run
# (keeps Amplify's dependencies out of your system Python)
# ----------------------------------------------------------
VENV="$EXECUTER_ROOT/venv"

if [ ! -x "$VENV/bin/python" ]; then
    BASE_PYTHON=""
    for candidate in /opt/homebrew/bin/python3.13 /usr/local/bin/python3.13 python3.13 python3; do
        if command -v "$candidate" &>/dev/null; then
            BASE_PYTHON="$(command -v "$candidate")"
            break
        fi
    done
    if [ -z "$BASE_PYTHON" ]; then
        echo "[ERROR] Python not found. Install Python 3.11+ and retry."
        exit 1
    fi
    echo "[INFO] Creating virtualenv with $BASE_PYTHON ..."
    "$BASE_PYTHON" -m venv "$VENV"
fi

PYTHON="$VENV/bin/python"
echo "[INFO] Using venv Python: $PYTHON"

# ----------------------------------------------------------
# Install requirements.txt when anything the app imports is missing
# (first run, or after requirements.txt changes). Covers the server
# (flask, bcrypt, PyJWT, ...) and Jira Insights (streamlit, plotly, ...).
# ----------------------------------------------------------
if ! "$PYTHON" -c "import flask, bcrypt, jwt, yaml, openpyxl, streamlit, plotly, pandas, pygwalker, streamlit_sortables" 2>/dev/null; then
    echo ""
    echo "[INFO] Installing requirements (first run takes a minute)..."
    "$PYTHON" -m pip install --quiet -r "$EXECUTER_ROOT/requirements.txt"
fi

# ----------------------------------------------------------
# Add hosts entry for amplify-qea (once)
# ----------------------------------------------------------
if ! grep -q "amplify-qea" /etc/hosts 2>/dev/null; then
    echo "[INFO] Adding 'amplify-qea' to /etc/hosts (requires sudo)..."
    echo "127.0.0.1  amplify-qea" | sudo tee -a /etc/hosts > /dev/null && \
        echo "[INFO] Hosts entry added." || \
        echo "[WARN] Could not update /etc/hosts — will fall back to localhost:7777."
fi

# ----------------------------------------------------------
# Free port 7777 if already in use
# ----------------------------------------------------------
PORT=7777

_kill_port() {
    # Try both lsof syntaxes macOS uses (tcp:N and :N)
    local pids
    pids=$(lsof -ti tcp:$PORT 2>/dev/null; lsof -ti :$PORT 2>/dev/null)
    pids=$(echo "$pids" | sort -u | tr '\n' ' ')
    if [ -n "$pids" ]; then
        echo "[INFO] Port $PORT in use (PIDs: $pids) — killing..."
        # shellcheck disable=SC2086
        kill -9 $pids 2>/dev/null || true
        return 0
    fi
    return 1
}

if _kill_port; then
    # Wait until the port is actually released (up to 3 s)
    for i in 1 2 3 4 5 6; do
        sleep 0.5
        lsof -ti tcp:$PORT &>/dev/null || { echo "[INFO] Port $PORT freed."; break; }
        [ "$i" -eq 6 ] && echo "[WARN] Port $PORT may still be in use."
    done
fi

# ----------------------------------------------------------
# Launch the web server
# ----------------------------------------------------------
cd "$EXECUTER_ROOT"

echo "[INFO] Starting server at http://amplify-qea:$PORT"
echo "[INFO] Press Ctrl+C to stop."
echo ""
exec "$PYTHON" -m amplify.server
