#!/usr/bin/env bash
# ==============================================================================
# Dual-Direction Review System Status Script
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PORT="${PORT:-8080}"
PID_FILE="$SCRIPT_DIR/.review_server.pid"
DATA_FILE="$SCRIPT_DIR/data/reviewer_feedback.json"

echo "=== Dual-Direction Review System Status ==="

# Check process
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Process:   RUNNING (PID: $PID)"
    else
        echo "Process:   STALE PID ($PID not running)"
    fi
else
    echo "Process:   NO LOCAL PID FILE"
fi

# Check HTTP Health
echo -n "HTTP API:  "
HEALTH_OUT=$(curl -s -m 2 "http://localhost:$PORT/api/health" 2>/dev/null || true)
if echo "$HEALTH_OUT" | grep -q '"status": "healthy"' 2>/dev/null; then
    UPTIME=$(echo "$HEALTH_OUT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('uptimeSeconds', 'unknown'))" 2>/dev/null || echo "active")
    TOTAL=$(echo "$HEALTH_OUT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('totalComments', 0))" 2>/dev/null || echo "0")
    PENDING=$(echo "$HEALTH_OUT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('pendingComments', 0))" 2>/dev/null || echo "0")
    echo "HEALTHY (Uptime: ${UPTIME}s | Comments: $TOTAL total, $PENDING pending)"
elif curl -s -m 2 "http://localhost:$PORT/api/comments" 2>/dev/null | grep -q '"comments"' 2>/dev/null; then
    COMMENTS_OUT=$(curl -s -m 2 "http://localhost:$PORT/api/comments" 2>/dev/null || echo '{"comments":[]}')
    CNT=$(echo "$COMMENTS_OUT" | python3 -c "import sys, json; print(len(json.load(sys.stdin).get('comments', [])))" 2>/dev/null || echo "0")
    echo "ONLINE (Legacy server running on port $PORT, $CNT comments loaded)"
else
    echo "UNREACHABLE on port $PORT"
fi

# Ledger status
if [ -f "$DATA_FILE" ]; then
    PENDING_CNT=$(python3 -c "import json; data=json.load(open('$DATA_FILE')); print(sum(1 for c in data if c.get('status')!='ADDRESSED'))" 2>/dev/null || echo 0)
    TOTAL_CNT=$(python3 -c "import json; data=json.load(open('$DATA_FILE')); print(len(data))" 2>/dev/null || echo 0)
    echo "Ledger:    $DATA_FILE ($TOTAL_CNT total, $PENDING_CNT pending)"
else
    echo "Ledger:    MISSING ($DATA_FILE)"
fi

echo "Dashboard: http://localhost:$PORT/dashboard/pr_review_dashboard.html"
echo "============================================"
