#!/usr/bin/env bash
# ==============================================================================
# PyCBC Dual-Direction Review Stack - Stop Script
# ==============================================================================
# Gracefully stops all review stack processes:
#   1. review_server.py
#   2. auto_responder.py
#   3. ci_tracker.py
#   4. rebase_monitor.py
#   5. dev_reconciler.py
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
PID_DIR="$BASE_DIR/.pids"

echo "=================================================================="
echo "🛑 Stopping PyCBC Dual-Direction Review Stack"
echo "=================================================================="

stop_process() {
    local name="$1"
    local pid_file="$PID_DIR/$name.pid"
    local pattern="$2"

    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file" 2>/dev/null || echo "")
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo -n "Stopping $name (PID: $pid)... "
            kill -15 "$pid" 2>/dev/null || true
            for _ in {1..10}; do
                if ! kill -0 "$pid" 2>/dev/null; then
                    break
                fi
                sleep 0.2
            done
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
            fi
            echo "stopped."
        fi
        rm -f "$pid_file"
    fi

    # Fallback pattern sweep
    local stray_pids
    stray_pids=$(pgrep -f "$pattern" 2>/dev/null || true)
    if [ -n "$stray_pids" ]; then
        echo -n "Cleaning stray $name processes ($stray_pids)... "
        kill -15 $stray_pids 2>/dev/null || true
        sleep 0.5
        kill -9 $stray_pids 2>/dev/null || true
        echo "done."
    fi
}

stop_process "auto_responder" "sentinel/auto_responder.py"
stop_process "ci_tracker" "sentinel/ci_tracker.py"
stop_process "rebase_monitor" "sentinel/rebase_monitor.py"
stop_process "dev_reconciler" "sentinel/dev_reconciler.py"
stop_process "review_server" "review_server.py"

# Also clean legacy PID files if present
rm -f "$BASE_DIR/.review_server.pid" "$BASE_DIR/.auto_responder.pid" "$BASE_DIR/.rebase_monitor.pid" "$BASE_DIR/.dev_reconciler.pid"

echo "✓ All review stack processes stopped."
echo "=================================================================="
