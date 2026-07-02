#!/bin/bash
# Serve and launch the browser version of the handwritten digit recognizer.
# Can be run from any terminal or double-clicked from a file manager.
#
# Must be served over http:// (not opened as a file:// URL) — browsers
# block fetch("weights.json") from a file:// page.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT=8000
URL="http://localhost:$PORT/"

python3 -m http.server "$PORT" &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null' EXIT

sleep 1
xdg-open "$URL" 2>/dev/null || echo "Open $URL in your browser"

wait "$SERVER_PID"
