#!/usr/bin/env bash
# ==============================================================================
# Dual-Direction Review System Stop Script
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PID_FILE="$SCRIPT_DIR/.review_server.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found ($PID_FILE). Searching for running review_server.py..."
    PIDS=$(pgrep -f "server/review_server.py" || true)
    if [ -n "$PIDS" ]; then
        echo "Terminating review server PID(s): $PIDS"
        kill -15 $PIDS 2>/dev/null || true
        echo "✓ Stopped."
    else
        echo "No review server processes found."
    fi
    exit 0
fi

PID=$(cat "$PID_FILE")
if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    echo "Stopping review server (PID: $PID)..."
    kill -15 "$PID" 2>/dev/null || true
    sleep 1
    if kill -0 "$PID" 2>/dev/null; then
        echo "Process still alive, forcing termination..."
        kill -9 "$PID" 2>/dev/null || true
    fi
    echo "✓ Review server stopped."
else
    echo "Review server PID $PID was not running."
fi

rm -f "$PID_FILE"
