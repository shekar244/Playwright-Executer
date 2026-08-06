#!/usr/bin/env bash
# ============================================================
#  Playwright Test Executor — macOS / Linux Launcher
#  Double-click or run:  bash launch-ui.sh
# ============================================================
set -euo pipefail

# Resolve the directory containing this script (Playwright-Executer root)
EXECUTER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "============================================================"
echo "  Playwright Test Executor"
echo "============================================================"
echo "  Launcher root : $EXECUTER_ROOT"
echo ""

# ----------------------------------------------------------
# Locate Python
# ----------------------------------------------------------
PYTHON=""

# 1. Executer-project venv
if [ -f "$EXECUTER_ROOT/venv/bin/python" ]; then
    PYTHON="$EXECUTER_ROOT/venv/bin/python"
    echo "[INFO] Using Executer venv Python: $PYTHON"

# 2. Framework venv (sibling folder)
elif [ -f "$EXECUTER_ROOT/../p13n-marketing-experiences-qa-automation/venv/bin/python" ]; then
    PYTHON="$(cd "$EXECUTER_ROOT/../p13n-marketing-experiences-qa-automation/venv/bin" && pwd)/python"
    echo "[INFO] Using framework venv Python: $PYTHON"

# 3. System python3
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
    echo "[INFO] Using system python3: $(command -v python3)"

# 4. System python
elif command -v python &>/dev/null; then
    PYTHON="python"
    echo "[INFO] Using system python: $(command -v python)"

else
    echo "[ERROR] Python not found."
    echo "        Install Python 3.9+ and ensure it is on your PATH, then retry."
    exit 1
fi

# ----------------------------------------------------------
# Verify tkinter is available (common omission on Linux)
# ----------------------------------------------------------
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
    echo ""
    echo "[ERROR] tkinter is not available in: $PYTHON"
    echo ""
    echo "  On Ubuntu/Debian : sudo apt-get install python3-tk"
    echo "  On Fedora/RHEL   : sudo dnf install python3-tkinter"
    echo "  On macOS (brew)  : brew install python-tk"
    echo ""
    exit 1
fi

# ----------------------------------------------------------
# Launch the UI from the Executer root
# ----------------------------------------------------------
cd "$EXECUTER_ROOT"

echo "[INFO] Starting UI ..."
echo ""
exec "$PYTHON" -m ui_launcher
