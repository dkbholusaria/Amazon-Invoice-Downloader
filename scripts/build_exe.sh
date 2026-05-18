#!/bin/bash
# ============================================================
#  Build Script — Amazon Invoice Downloader (Linux / WSL)
#  Creates a standalone executable binary using PyInstaller
# ============================================================

set -e

# Automatically move to the project root folder (one level up from scripts/)
cd "$(dirname "$0")/.."

VENV_DIR=".venv"
PYTHON_BIN="$VENV_DIR/bin/python"
PYINSTALLER_BIN="$VENV_DIR/bin/pyinstaller"

echo "===================================================="
echo "  Building Amazon Invoice Downloader (WSL Linux) ..."
echo "===================================================="
echo ""

# 1. Ensure virtual environment exists
if [ ! -d "$VENV_DIR" ]; then
    echo "ERROR: Virtual environment ($VENV_DIR) not found. Please run the setup first."
    exit 1
fi

# 2. Ensure pyinstaller is installed in the virtual environment
if [ ! -f "$PYINSTALLER_BIN" ]; then
    echo "PyInstaller not found in virtual environment. Installing now..."
    "$VENV_DIR/bin/pip" install pyinstaller
fi

# 3. Execute the cross-platform python build script
"$PYTHON_BIN" scripts/build.py
