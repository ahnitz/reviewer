#!/usr/bin/env bash
# ==============================================================================
# PyCBC Dual-Direction Review Stack - Restart Script
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
"$SCRIPT_DIR/stop_review_stack.sh"
sleep 1
"$SCRIPT_DIR/start_review_stack.sh"
