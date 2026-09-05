#!/usr/bin/env bash
# ============================================================
#  Playwright Test Executor — macOS / Linux Launcher
#  Double-click or run:  bash launch-ui.sh
# ============================================================
set -euo pipefail

EXECUTER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "============================================================"
echo "  Playwright Test Executor"
echo "============================================================"
echo "  Launcher root : $EXECUTER_ROOT"
echo ""

# ----------------------------------------------------------
# Locate Python (prefer Homebrew 3.13 which has Flask)
# ----------------------------------------------------------
PYTHON=""

if [ -f "$EXECUTER_ROOT/venv/bin/python" ]; then
    PYTHON="$EXECUTER_ROOT/venv/bin/python"
    echo "[INFO] Using Executer venv Python: $PYTHON"
elif command -v /opt/homebrew/bin/python3.13 &>/dev/null; then
    PYTHON="/opt/homebrew/bin/python3.13"
    echo "[INFO] Using Homebrew python3.13: $PYTHON"
elif command -v /usr/local/bin/python3.13 &>/dev/null; then
    PYTHON="/usr/local/bin/python3.13"
    echo "[INFO] Using python3.13: $PYTHON"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
    echo "[INFO] Using system python3: $(command -v python3)"
else
    echo "[ERROR] Python not found. Install Python 3.11+ and retry."
    exit 1
fi

# ----------------------------------------------------------
# Verify required packages are available
# ----------------------------------------------------------
if ! "$PYTHON" -c "import flask" 2>/dev/null; then
    echo ""
    echo "[INFO] Flask not found — installing..."
    "$PYTHON" -m pip install flask --break-system-packages --quiet
fi

if ! "$PYTHON" -c "import openpyxl" 2>/dev/null; then
    echo ""
    echo "[INFO] openpyxl not found — installing (needed for Excel support)..."
    "$PYTHON" -m pip install openpyxl --break-system-packages --quiet
fi

if ! "$PYTHON" -c "import yaml" 2>/dev/null; then
    echo ""
    echo "[INFO] pyyaml not found — installing (needed for YAML support)..."
    "$PYTHON" -m pip install pyyaml --break-system-packages --quiet
fi

# ----------------------------------------------------------
# Add hosts entry for amplyf-qea (once)
# ----------------------------------------------------------
if ! grep -q "amplyf-qea" /etc/hosts 2>/dev/null; then
    echo "[INFO] Adding 'amplyf-qea' to /etc/hosts (requires sudo)..."
    echo "127.0.0.1  amplyf-qea" | sudo tee -a /etc/hosts > /dev/null && \
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

echo "[INFO] Starting server at http://amplyf-qea:$PORT"
echo "[INFO] Press Ctrl+C to stop."
echo ""
exec "$PYTHON" server.py
