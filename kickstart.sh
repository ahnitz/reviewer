#!/usr/bin/env bash
# ==============================================================================
# Dual-Direction Review System Kickstart Script
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
ROOT_DIR="$SCRIPT_DIR"
SERVER_SCRIPT="$ROOT_DIR/server/review_server.py"
RESPONDER_SCRIPT="$ROOT_DIR/sentinel/auto_responder.py"
DATA_DIR="$ROOT_DIR/data"
DATA_FILE="$DATA_DIR/reviewer_feedback.json"
SERVER_PID_FILE="$ROOT_DIR/.review_server.pid"
SERVER_LOG_FILE="$ROOT_DIR/.review_server.log"
RESPONDER_PID_FILE="$ROOT_DIR/.auto_responder.pid"
RESPONDER_LOG_FILE="$ROOT_DIR/.auto_responder.log"
REBASE_SCRIPT="$ROOT_DIR/sentinel/rebase_monitor.py"
REBASE_PID_FILE="$ROOT_DIR/.rebase_monitor.pid"
REBASE_LOG_FILE="$ROOT_DIR/.rebase_monitor.log"
RECONCILER_SCRIPT="$ROOT_DIR/sentinel/dev_reconciler.py"
RECONCILER_PID_FILE="$ROOT_DIR/.dev_reconciler.pid"
RECONCILER_LOG_FILE="$ROOT_DIR/.dev_reconciler.log"

PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"

echo "=================================================================="
echo "🚀 Kickstarting Dual-Direction Agent Review System"
echo "=================================================================="
echo "Directory: $ROOT_DIR"
echo "Port:      $PORT"
echo "Host:      $HOST"

# 1. Verify Python 3
if ! command -v python3 &>/dev/null; then
    echo "❌ Error: python3 is required but not found in PATH." >&2
    exit 1
fi
PY_VER=$(python3 -c "import sys; print('.'.join(map(str, sys.version_info[:2])))")
echo "✓ Python $PY_VER detected"

# 2. Initialize Data Storage
mkdir -p "$DATA_DIR"
if [ ! -f "$DATA_FILE" ] || [ ! -s "$DATA_FILE" ]; then
    echo "[]" > "$DATA_FILE"
    echo "✓ Initialized clean feedback ledger: $DATA_FILE"
else
    echo "✓ Existing feedback ledger found: $DATA_FILE ($(grep -c '"id":' "$DATA_FILE" 2>/dev/null || echo 0) comments)"
fi

# 3. Check / Start Review Server
SERVER_RUNNING=0
if [ -f "$SERVER_PID_FILE" ]; then
    OLD_PID=$(cat "$SERVER_PID_FILE" 2>/dev/null || echo "")
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        SERVER_RUNNING=1
    fi
fi

if curl -s -m 1 "http://localhost:$PORT/api/health" &>/dev/null || curl -s -m 1 "http://localhost:$PORT/api/comments" &>/dev/null; then
    echo "✓ Review server is already actively responding on http://localhost:$PORT"
    SERVER_RUNNING=1
fi

if [ "$SERVER_RUNNING" -eq 0 ]; then
    echo "Starting review server in background..."
    nohup python3 "$SERVER_SCRIPT" --host "$HOST" --port "$PORT" --root-dir "$ROOT_DIR" --data-file "$DATA_FILE" > "$SERVER_LOG_FILE" 2>&1 &
    NEW_PID=$!
    echo "$NEW_PID" > "$SERVER_PID_FILE"
    sleep 1

    if kill -0 "$NEW_PID" 2>/dev/null; then
        echo "✓ Server started successfully (PID: $NEW_PID)"
    else
        echo "❌ Server failed to start. Last log lines:" >&2
        tail -n 20 "$SERVER_LOG_FILE" >&2
        exit 1
    fi
fi

# 4. Check / Start Automated Responder Daemon
RESPONDER_RUNNING=0
if [ -f "$RESPONDER_PID_FILE" ]; then
    OLD_R_PID=$(cat "$RESPONDER_PID_FILE" 2>/dev/null || echo "")
    if [ -n "$OLD_R_PID" ] && kill -0 "$OLD_R_PID" 2>/dev/null; then
        RESPONDER_RUNNING=1
    fi
fi

if [ "$RESPONDER_RUNNING" -eq 0 ]; then
    echo "Starting automated review responder in background..."
    nohup python3 "$RESPONDER_SCRIPT" --api-url "http://localhost:$PORT/api/comments" --data-file "$DATA_FILE" --interval 2 > "$RESPONDER_LOG_FILE" 2>&1 &
    NEW_R_PID=$!
    echo "$NEW_R_PID" > "$RESPONDER_PID_FILE"
    sleep 1

    if kill -0 "$NEW_R_PID" 2>/dev/null; then
        echo "✓ Automated responder started successfully (PID: $NEW_R_PID)"
    else
        echo "❌ Responder failed to start. Last log lines:" >&2
        tail -n 20 "$RESPONDER_LOG_FILE" >&2
    fi
else
    echo "✓ Automated responder is already active"
fi

# 5. Check / Start Automated Upstream Rebase Monitor Daemon
REBASE_RUNNING=0
if [ -f "$REBASE_PID_FILE" ]; then
    OLD_REBASE_PID=$(cat "$REBASE_PID_FILE" 2>/dev/null || echo "")
    if [ -n "$OLD_REBASE_PID" ] && kill -0 "$OLD_REBASE_PID" 2>/dev/null; then
        REBASE_RUNNING=1
    fi
fi

if [ "$REBASE_RUNNING" -eq 0 ]; then
    echo "Starting automated rebase monitor in background..."
    nohup python3 "$REBASE_SCRIPT" --interval 60 > "$REBASE_LOG_FILE" 2>&1 &
    NEW_REBASE_PID=$!
    echo "$NEW_REBASE_PID" > "$REBASE_PID_FILE"
    sleep 1

    if kill -0 "$NEW_REBASE_PID" 2>/dev/null; then
        echo "✓ Automated rebase monitor started successfully (PID: $NEW_REBASE_PID)"
    else
        echo "❌ Rebase monitor failed to start. Last log lines:" >&2
        tail -n 20 "$REBASE_LOG_FILE" >&2
    fi
else
    echo "✓ Automated rebase monitor is already active"
fi

# 6. Check / Start Automated Dev Branch Reconciler Daemon
RECONCILER_RUNNING=0
if [ -f "$RECONCILER_PID_FILE" ]; then
    OLD_REC_PID=$(cat "$RECONCILER_PID_FILE" 2>/dev/null || echo "")
    if [ -n "$OLD_REC_PID" ] && kill -0 "$OLD_REC_PID" 2>/dev/null; then
        RECONCILER_RUNNING=1
    fi
fi

if [ "$RECONCILER_RUNNING" -eq 0 ]; then
    echo "Starting automated dev branch reconciler in background..."
    nohup python3 "$RECONCILER_SCRIPT" --watch --interval 60 > "$RECONCILER_LOG_FILE" 2>&1 &
    NEW_REC_PID=$!
    echo "$NEW_REC_PID" > "$RECONCILER_PID_FILE"
    sleep 1

    if kill -0 "$NEW_REC_PID" 2>/dev/null; then
        echo "✓ Automated dev reconciler started successfully (PID: $NEW_REC_PID)"
    else
        echo "❌ Dev reconciler failed to start. Last log lines:" >&2
        tail -n 20 "$RECONCILER_LOG_FILE" >&2
    fi
else
    echo "✓ Automated dev reconciler is already active"
fi

# 7. Output Summary and Usage Instructions
echo ""
echo "=================================================================="
echo "✨ SYSTEM IS LIVE AND OPERATIONAL"
echo "=================================================================="
echo "Dashboard UI:        http://localhost:$PORT/dashboard/pr_review_dashboard.html"
echo "Root Auto-Redirect:  http://localhost:$PORT/"
echo "REST API:            http://localhost:$PORT/api/comments"
echo "Roadmap API:         http://localhost:$PORT/api/roadmap"
echo "Dev Status API:      http://localhost:$PORT/api/dev/status"
echo "Rebase Status API:   http://localhost:$PORT/api/rebase/status"
echo "Healthcheck:         http://localhost:$PORT/api/health"
echo ""
echo "Helpful Management Commands:"
echo "  ./status.sh                                 # Check status of server, responder, rebase & dev reconciler"
echo "  ./stop.sh                                   # Stop all review services"
echo "  python3 sentinel/dev_reconciler.py --status # View live wave roadmap status"
echo "  python3 sentinel/rebase_monitor.py --once  # Run immediate upstream check & rebase"
echo "  python3 sentinel/feedback_cli.py list --pending-only"
echo "=================================================================="
