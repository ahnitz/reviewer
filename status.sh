#!/usr/bin/env bash
# ==============================================================================
# Dual-Direction Review System Status Script
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PORT="${PORT:-8080}"
SERVER_PID_FILE="$SCRIPT_DIR/.review_server.pid"
RESPONDER_PID_FILE="$SCRIPT_DIR/.auto_responder.pid"
DATA_FILE="$SCRIPT_DIR/data/reviewer_feedback.json"

echo "=== Dual-Direction Review System Status ==="

# Check server process
if [ -f "$SERVER_PID_FILE" ]; then
    PID=$(cat "$SERVER_PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Review Server:  RUNNING (PID: $PID)"
    else
        echo "Review Server:  STALE PID ($PID not running)"
    fi
elif pgrep -f "review_server.py" >/dev/null 2>&1; then
    echo "Review Server:  RUNNING (PID: $(pgrep -f "review_server.py" | head -n1))"
else
    echo "Review Server:  STOPPED"
fi

# Check auto-responder process
if [ -f "$RESPONDER_PID_FILE" ]; then
    RPID=$(cat "$RESPONDER_PID_FILE")
    if kill -0 "$RPID" 2>/dev/null; then
        echo "Auto-Responder: RUNNING (PID: $RPID)"
    else
        echo "Auto-Responder: STALE PID ($RPID not running)"
    fi
elif pgrep -f "sentinel/auto_responder.py" >/dev/null 2>&1; then
    echo "Auto-Responder: RUNNING (PID: $(pgrep -f "sentinel/auto_responder.py" | head -n1))"
else
    echo "Auto-Responder: STOPPED"
fi

# Check HTTP Health
echo -n "HTTP API:       "
HEALTH_OUT=$(curl -s -m 2 "http://localhost:$PORT/api/health" 2>/dev/null || true)
if echo "$HEALTH_OUT" | grep -q '"status": "healthy"' 2>/dev/null; then
    UPTIME=$(echo "$HEALTH_OUT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('uptimeSeconds', 'unknown'))" 2>/dev/null || echo "active")
    TOTAL=$(echo "$HEALTH_OUT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('totalComments', 0))" 2>/dev/null || echo "0")
    PENDING=$(echo "$HEALTH_OUT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('pendingComments', 0))" 2>/dev/null || echo "0")
    echo "HEALTHY (Uptime: ${UPTIME}s | Comments: $TOTAL total, $PENDING pending)"
elif curl -s -m 2 "http://localhost:$PORT/api/comments" 2>/dev/null | grep -q '"comments"' 2>/dev/null; then
    COMMENTS_OUT=$(curl -s -m 2 "http://localhost:$PORT/api/comments" 2>/dev/null || echo '{"comments":[]}')
    CNT=$(echo "$COMMENTS_OUT" | python3 -c "import sys, json; print(len(json.load(sys.stdin).get('comments', [])))" 2>/dev/null || echo "0")
    echo "ONLINE (Port $PORT active, $CNT comments in ledger)"
else
    echo "UNREACHABLE on port $PORT"
fi

# Ledger status
if [ -f "$DATA_FILE" ]; then
    PENDING_CNT=$(python3 -c "import json; data=json.load(open('$DATA_FILE')); print(sum(1 for c in data if c.get('status')!='ADDRESSED'))" 2>/dev/null || echo 0)
    TOTAL_CNT=$(python3 -c "import json; data=json.load(open('$DATA_FILE')); print(len(data))" 2>/dev/null || echo 0)
    echo "Ledger:         $DATA_FILE ($TOTAL_CNT total, $PENDING_CNT pending)"
else
    echo "Ledger:         MISSING ($DATA_FILE)"
fi

echo "Dashboard:      http://localhost:$PORT/dashboard/pr_review_dashboard.html"
echo "============================================"
