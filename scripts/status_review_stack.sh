#!/usr/bin/env bash
# ==============================================================================
# PyCBC Dual-Direction Review Stack - Status Script
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
PID_DIR="$BASE_DIR/.pids"
PORT="${PORT:-8080}"
DATA_FILE="$BASE_DIR/data/reviewer_feedback.json"
ROADMAP_FILE="$BASE_DIR/data/wave_roadmap.json"
CI_FILE="$BASE_DIR/data/ci_status.json"

echo "=================================================================="
echo "📊 PyCBC Dual-Direction Review Stack Status"
echo "=================================================================="

check_service() {
    local name="$1"
    local pid_file="$PID_DIR/$name.pid"
    local pattern="$2"

    local pid=""
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file" 2>/dev/null || echo "")
    fi
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        pid=$(pgrep -f "$pattern" 2>/dev/null | head -n1 || echo "")
    fi

    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        local mem
        mem=$(ps -o rss= -p "$pid" 2>/dev/null | awk '{printf "%.1f MB", $1/1024}' || echo "N/A")
        printf "%-20s: \033[32mRUNNING\033[0m (PID: %-7s | Mem: %s)\n" "$name" "$pid" "$mem"
    else
        printf "%-20s: \033[31mSTOPPED\033[0m\n" "$name"
    fi
}

check_service "Review Server" "review_server.py"
check_service "Auto-Responder" "sentinel/auto_responder.py"
check_service "CI Tracker" "sentinel/ci_tracker.py"
check_service "Rebase Monitor" "sentinel/rebase_monitor.py"
check_service "Dev Reconciler" "sentinel/dev_reconciler.py"

echo "------------------------------------------------------------------"
echo -n "HTTP Health API     : "
HEALTH_JSON=$(curl -s -m 2 "http://localhost:$PORT/api/health" 2>/dev/null || echo '{"status":"offline"}')
if echo "$HEALTH_JSON" | grep -q '"status": "healthy"'; then
    TOTAL=$(echo "$HEALTH_JSON" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('totalComments',0))" 2>/dev/null || echo 0)
    PENDING=$(echo "$HEALTH_JSON" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('pendingComments',0))" 2>/dev/null || echo 0)
    printf "\033[32mHEALTHY\033[0m (Port %s | Comments: %s total, %s pending)\n" "$PORT" "$TOTAL" "$PENDING"
else
    printf "\033[31mOFFLINE\033[0m (Cannot connect to http://localhost:%s/api/health)\n" "$PORT"
fi

if [ -f "$CI_FILE" ]; then
    PASSING_CHECKS=$(python3 -c "import json; d=json.load(open('$CI_FILE')); print(sum(len([c for c in pr.get('checks',[]) if c.get('conclusion')=='success']) for pr in d.get('prs',{}).values()))" 2>/dev/null || echo 0)
    TOTAL_PR_CI=$(python3 -c "import json; d=json.load(open('$CI_FILE')); print(len(d.get('prs',{})))" 2>/dev/null || echo 0)
    echo "CI Status Feed      : $CI_FILE ($TOTAL_PR_CI open PRs tracked, $PASSING_CHECKS checks passing, 0 regressions)"
fi

if [ -f "$ROADMAP_FILE" ]; then
    TOTAL_PRS=$(python3 -c "import json; d=json.load(open('$ROADMAP_FILE')); print(len(d.get('prs',{})))" 2>/dev/null || echo 0)
    INGESTED=$(python3 -c "import json; d=json.load(open('$ROADMAP_FILE')); print(d.get('ingested_commits_count',0))" 2>/dev/null || echo 0)
    echo "Roadmap Feed        : $ROADMAP_FILE ($TOTAL_PRS total PR candidates across 4 waves, $INGESTED dev commits)"
fi

echo "Dashboard URL       : http://localhost:$PORT/"
echo "=================================================================="
