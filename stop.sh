#!/usr/bin/env bash
# ==============================================================================
# Dual-Direction Review System Stop Script
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
SERVER_PID_FILE="$SCRIPT_DIR/.review_server.pid"
RESPONDER_PID_FILE="$SCRIPT_DIR/.auto_responder.pid"

# Stop Auto-Responder
if [ -f "$RESPONDER_PID_FILE" ]; then
    R_PID=$(cat "$RESPONDER_PID_FILE")
    if [ -n "$R_PID" ] && kill -0 "$R_PID" 2>/dev/null; then
        echo "Stopping automated responder (PID: $R_PID)..."
        kill -15 "$R_PID" 2>/dev/null || true
    fi
    rm -f "$RESPONDER_PID_FILE"
fi
PIDS=$(pgrep -f "sentinel/auto_responder.py" || true)
if [ -n "$PIDS" ]; then
    kill -15 $PIDS 2>/dev/null || true
fi
echo "✓ Auto-responder stopped."

# Stop Review Server
if [ -f "$SERVER_PID_FILE" ]; then
    PID=$(cat "$SERVER_PID_FILE")
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "Stopping review server (PID: $PID)..."
        kill -15 "$PID" 2>/dev/null || true
        sleep 1
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null || true
        fi
    fi
    rm -f "$SERVER_PID_FILE"
fi
PIDS=$(pgrep -f "server/review_server.py" || true)
if [ -n "$PIDS" ]; then
    kill -15 $PIDS 2>/dev/null || true
fi
echo "✓ Review server stopped."
