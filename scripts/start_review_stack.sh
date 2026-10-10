#!/usr/bin/env bash
# ==============================================================================
# PyCBC Dual-Direction Review Stack - Start Script
# ==============================================================================
# Starts all 5 core review services:
#   1. review_server.py    (HTTP dashboard, REST API on port 8080)
#   2. auto_responder.py   (Immediate review comments auto-responder SLA)
#   3. ci_tracker.py       (GitHub Actions CI status and matrix tracker)
#   4. rebase_monitor.py   (Continuous upstream master rebase sentinel)
#   5. dev_reconciler.py   (Development branch continuous ingestion & roadmap)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"

PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"

PID_DIR="$BASE_DIR/.pids"
LOG_DIR="$BASE_DIR/.logs"
DATA_DIR="$BASE_DIR/data"
mkdir -p "$PID_DIR" "$LOG_DIR" "$DATA_DIR"

SERVER_SCRIPT="$BASE_DIR/server/review_server.py"
RESPONDER_SCRIPT="$BASE_DIR/sentinel/auto_responder.py"
CI_TRACKER_SCRIPT="$BASE_DIR/sentinel/ci_tracker.py"
REBASE_SCRIPT="$BASE_DIR/sentinel/rebase_monitor.py"
RECONCILER_SCRIPT="$BASE_DIR/sentinel/dev_reconciler.py"

echo "=================================================================="
echo "🚀 Starting PyCBC Dual-Direction Review Stack"
echo "=================================================================="
echo "Base Directory: $BASE_DIR"
echo "Port:           $PORT"
echo "Host:           $HOST"

# 1. Verify Python 3
if ! command -v python3 &>/dev/null; then
    echo "❌ Error: python3 is required but not found in PATH." >&2
    exit 1
fi
echo "✓ Python: $(python3 --version 2>&1)"

# 2. Initialize Data Files
FEEDBACK_FILE="$DATA_DIR/reviewer_feedback.json"
if [ ! -f "$FEEDBACK_FILE" ] || [ ! -s "$FEEDBACK_FILE" ]; then
    echo "[]" > "$FEEDBACK_FILE"
    echo "✓ Initialized feedback ledger: $FEEDBACK_FILE"
else
    echo "✓ Existing feedback ledger: $FEEDBACK_FILE"
fi

# Helper function to check if PID is alive
is_running() {
    local pid_file="$1"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file" 2>/dev/null || echo "")
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

# 3. Start Review Server
if is_running "$PID_DIR/review_server.pid"; then
    echo "✓ Review Server is already running (PID: $(cat "$PID_DIR/review_server.pid"))"
elif curl -s -m 1 "http://localhost:$PORT/api/health" &>/dev/null; then
    echo "✓ Review Server is already actively responding on port $PORT"
else
    echo "Starting Review Server on http://$HOST:$PORT..."
    nohup python3 "$SERVER_SCRIPT" --host "$HOST" --port "$PORT" > "$LOG_DIR/review_server.log" 2>&1 &
    SERVER_PID=$!
    echo "$SERVER_PID" > "$PID_DIR/review_server.pid"
    sleep 1
    if kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "✓ Review Server started (PID: $SERVER_PID)"
    else
        echo "❌ Review Server failed to start. Last log lines:" >&2
        tail -n 20 "$LOG_DIR/review_server.log" >&2
        exit 1
    fi
fi

# 4. Start Auto-Responder Daemon
if is_running "$PID_DIR/auto_responder.pid"; then
    echo "✓ Auto-Responder is already running (PID: $(cat "$PID_DIR/auto_responder.pid"))"
else
    echo "Starting Auto-Responder daemon (interval: 2s)..."
    nohup python3 "$RESPONDER_SCRIPT" --api-url "http://localhost:$PORT/api/comments" --interval 2 > "$LOG_DIR/auto_responder.log" 2>&1 &
    R_PID=$!
    echo "$R_PID" > "$PID_DIR/auto_responder.pid"
    sleep 1
    if kill -0 "$R_PID" 2>/dev/null; then
        echo "✓ Auto-Responder started (PID: $R_PID)"
    else
        echo "⚠️ Auto-Responder failed to start. Check $LOG_DIR/auto_responder.log"
    fi
fi

# 5. Start CI Failure Tracker Daemon
if is_running "$PID_DIR/ci_tracker.pid"; then
    echo "✓ CI Tracker is already running (PID: $(cat "$PID_DIR/ci_tracker.pid"))"
else
    echo "Starting CI Tracker daemon (watch: 60s)..."
    nohup python3 "$CI_TRACKER_SCRIPT" --watch --interval 60 > "$LOG_DIR/ci_tracker.log" 2>&1 &
    CI_PID=$!
    echo "$CI_PID" > "$PID_DIR/ci_tracker.pid"
    sleep 1
    if kill -0 "$CI_PID" 2>/dev/null; then
        echo "✓ CI Tracker started (PID: $CI_PID)"
    else
        echo "⚠️ CI Tracker failed to start. Check $LOG_DIR/ci_tracker.log"
    fi
fi

# 6. Start Upstream Rebase Monitor Daemon
if is_running "$PID_DIR/rebase_monitor.pid"; then
    echo "✓ Rebase Monitor is already running (PID: $(cat "$PID_DIR/rebase_monitor.pid"))"
else
    echo "Starting Rebase Monitor daemon (interval: 60s)..."
    nohup python3 "$REBASE_SCRIPT" --interval 60 > "$LOG_DIR/rebase_monitor.log" 2>&1 &
    REBASE_PID=$!
    echo "$REBASE_PID" > "$PID_DIR/rebase_monitor.pid"
    sleep 1
    if kill -0 "$REBASE_PID" 2>/dev/null; then
        echo "✓ Rebase Monitor started (PID: $REBASE_PID)"
    else
        echo "⚠️ Rebase Monitor failed to start. Check $LOG_DIR/rebase_monitor.log"
    fi
fi

# 7. Start Dev Branch Reconciler Daemon
if is_running "$PID_DIR/dev_reconciler.pid"; then
    echo "✓ Dev Reconciler is already running (PID: $(cat "$PID_DIR/dev_reconciler.pid"))"
else
    echo "Starting Dev Reconciler daemon (watch: 60s)..."
    nohup python3 "$RECONCILER_SCRIPT" --watch --interval 60 > "$LOG_DIR/dev_reconciler.log" 2>&1 &
    REC_PID=$!
    echo "$REC_PID" > "$PID_DIR/dev_reconciler.pid"
    sleep 1
    if kill -0 "$REC_PID" 2>/dev/null; then
        echo "✓ Dev Reconciler started (PID: $REC_PID)"
    else
        echo "⚠️ Dev Reconciler failed to start. Check $LOG_DIR/dev_reconciler.log"
    fi
fi

# 8. Verify Stack Health
echo ""
echo "Verifying stack health via HTTP..."
HEALTH_JSON=$(curl -s -m 3 "http://localhost:$PORT/api/health" 2>/dev/null || echo '{"status":"offline"}')
if echo "$HEALTH_JSON" | grep -q '"status": "healthy"'; then
    echo "✅ Review Stack is 100% HEALTHY and ONLINE"
else
    echo "⚠️ Stack started, but /api/health returned non-standard payload: $HEALTH_JSON"
fi

echo ""
echo "=================================================================="
echo "✨ REVIEW STACK IS OPERATIONAL"
echo "=================================================================="
echo "Dashboard UI:        http://localhost:$PORT/"
echo "Comments API:        http://localhost:$PORT/api/comments"
echo "CI Status API:       http://localhost:$PORT/api/ci/status"
echo "Rebase Status API:   http://localhost:$PORT/api/rebase/status"
echo "Dev Status API:      http://localhost:$PORT/api/dev/status"
echo "Roadmap API:         http://localhost:$PORT/api/roadmap"
echo "Healthcheck API:     http://localhost:$PORT/api/health"
echo "=================================================================="
