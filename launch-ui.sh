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
# Verify Flask is available
# ----------------------------------------------------------
if ! "$PYTHON" -c "import flask" 2>/dev/null; then
    echo ""
    echo "[INFO] Flask not found — installing..."
    "$PYTHON" -m pip install flask --break-system-packages --quiet
fi

# ----------------------------------------------------------
# Add hosts entry for playwright-executor (once)
# ----------------------------------------------------------
if ! grep -q "playwright-executor" /etc/hosts 2>/dev/null; then
    echo "[INFO] Adding 'playwright-executor' to /etc/hosts (requires sudo)..."
    echo "127.0.0.1  playwright-executor" | sudo tee -a /etc/hosts > /dev/null && \
        echo "[INFO] Hosts entry added." || \
        echo "[WARN] Could not update /etc/hosts — will fall back to localhost:7777."
fi

# ----------------------------------------------------------
# Launch the web server
# ----------------------------------------------------------
cd "$EXECUTER_ROOT"

echo "[INFO] Starting server at http://playwright-executor:7777"
echo "[INFO] Press Ctrl+C to stop."
echo ""
exec "$PYTHON" server.py
