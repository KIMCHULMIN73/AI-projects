#!/bin/bash
# Launch the handwritten digit recognizer GUI.
# Can be run from any terminal or double-clicked from a file manager.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f ".venv/bin/python3" ]; then
    echo "Error: virtual environment not found at $SCRIPT_DIR/.venv" >&2
    exit 1
fi

if [ ! -f "digit_model.joblib" ]; then
    echo "Model not found. Training it now (this may take a few minutes)..."
    .venv/bin/python3 train_model.py
fi

exec .venv/bin/python3 digit_recognizer_gui.py
