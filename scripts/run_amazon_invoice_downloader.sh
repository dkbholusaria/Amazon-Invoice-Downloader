#!/bin/bash
# ============================================================
#  Run Script — Amazon Invoice Downloader (Linux / WSL)
# ============================================================

# Automatically move to the project root folder (one level up from scripts/)
cd "$(dirname "$0")/.."

PYTHON_BIN=".venv/bin/python"
SCRIPT_FILE="amazon_download_complete_documented.py"

# Default settings (feel free to customize)
DEST_PATH=""  # Leave empty to open the GUI, or provide a path (e.g. "/home/deepak/downloads") for CLI mode
PERIOD="last-month"
HEADED=0      # Set to 1 to show the browser window, 0 to run in background

echo "===================================================="
echo "  Starting Amazon Invoice Downloader (WSL Linux) ..."
echo "===================================================="
echo ""

# Run check
# Force single-threaded BLAS to avoid WSL thread deadlocks
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1

CMD="\"$PYTHON_BIN\" \"$SCRIPT_FILE\""

if [ -n "$DEST_PATH" ]; then
    CMD="$CMD --dest \"$DEST_PATH\" --period \"$PERIOD\""
fi

if [ "$HEADED" -eq 1 ]; then
    CMD="$CMD --headed"
fi

echo "Executing: $CMD"
echo ""
eval "$CMD"
