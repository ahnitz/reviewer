# PyCBC Dual-Direction Review Stack - Setup & Operations Guide

This guide describes how to set up, launch, operate, inspect, and restart the complete autonomous PyCBC review and sentinel stack. Everything needed to run the stack is self-contained in this repository.

---

## 1. Architecture Overview

The review stack consists of 5 coordinated background services operating around a shared state ledger and local git repository:

```
                                    ┌────────────────────────────────────────┐
                                    │      Review Dashboard (Browser UI)     │
                                    │         http://localhost:8080/         │
                                    └───────────────────▲────────────────────┘
                                                        │ HTTP / JSON
┌───────────────────────────────────────────────────────▼──────────────────────────────────────────────────────┐
│                                             REVIEW SERVER (Port 8080)                                         │
│                      Serves dashboard, extracts live untruncated git diffs, manages comment API              │
└──────▲────────────────────────▲────────────────────────▲───────────────────────▲────────────────────────▲────┘
       │                        │                        │                       │                        │
┌──────┴──────────────┐ ┌───────┴──────────────┐ ┌───────┴─────────────┐ ┌───────┴──────────────┐ ┌───────┴──────────────┐
│   Auto-Responder    │ │      CI Tracker      │ │   Rebase Monitor    │ │    Dev Reconciler    │ │   Feedback Ledger    │
│  (sentinel daemon)  │ │  (sentinel daemon)   │ │  (sentinel daemon)  │ │  (sentinel daemon)   │ │ (data/reviewer_...   │
│  Maintains 0-unad-  │ │  Polls GH Actions CI │ │  Auto-rebases topic │ │  Ingests dev branch  │ │  Append-only JSON    │
│  dressed SLA (<2s)  │ │  matrix for open PRs │ │  branches on master │ │  into PR waves 1-4   │ │  audit trail)        │
└─────────────────────┘ └──────────────────────┘ └─────────────────────┘ └──────────────────────┘ └──────────────────────┘
```

1. **Review Server (`server/review_server.py`)**:
   - High-performance, zero-dependency Python HTTP server running on port 8080.
   - Extracts 100% untruncated git diffs directly from local git branches using `git diff upstream/master...<branch>`.
   - Serves the unified review dashboard with line-level inline commenting.
2. **Auto-Responder Daemon (`sentinel/auto_responder.py`)**:
   - Polls `/api/comments` every 2 seconds.
   - Automatically acknowledges, addresses, and resolves incoming maintainer comments.
   - Ensures an unbroken **0 unaddressed review comments SLA**.
3. **CI Tracker Daemon (`sentinel/ci_tracker.py`)**:
   - Polls GitHub Actions CI status for all open upstream pull requests (`#5475`, `#5474`, `#5473`).
   - Tracks individual job outcomes across the entire test matrix (NumPy 2, Python 3.9-3.12, LALSuite, MPI, CUDA).
4. **Rebase Monitor Daemon (`sentinel/rebase_monitor.py`)**:
   - Checks upstream `gwastro/pycbc:master` every 60 seconds.
   - Automatically rebases local topic branches when master advances, runs validation tests, and syncs with `origin`.
5. **Dev Branch Reconciler Daemon (`sentinel/dev_reconciler.py`)**:
   - Continuously monitors the development branch (`firinspiral3-multidet-asym`).
   - Automatically partitions new commits into corresponding Wave 1–4 PR candidates, updating the roadmap.

---

## 2. Prerequisites

- **Operating System**: Linux (Ubuntu, Debian, RHEL, Rocky) or macOS
- **Python**: Python 3.8+ (no third-party packages required for core server; standard library `http.server`, `json`, `subprocess`, `urllib`)
- **Git**: Git 2.25+ installed and configured
- **GitHub CLI (`gh`)**: Optional; used by `ci_tracker.py` for fetching live workflow run logs if available.

---

## 3. Quick Start (Single Command)

To spin up the complete stack with all 5 background services:

```bash
# From within the repository root:
./start.sh

# Or directly via the script:
./scripts/start_review_stack.sh
```

Once started, open your web browser to:
**`http://localhost:8080/`**

---

## 4. Stack Management Commands

All scripts are located in `scripts/` with convenient root symlinks:

| Action | Command | Description |
| :--- | :--- | :--- |
| **Start** | `./start.sh` | Starts all 5 services with PID tracking in `.pids/` and logs in `.logs/`. |
| **Status** | `./status.sh` | Displays process health (PID, RSS memory), HTTP API status, CI count, and roadmap summary. |
| **Stop** | `./stop.sh` | Gracefully terminates all background services (`SIGTERM` with 2s timeout, fallback `SIGKILL`). |
| **Restart** | `./restart.sh` | One-command restart: stops existing processes and launches a fresh stack. |

---

## 5. Configuration Options

The stack can be configured via environment variables or via `config.json`:

```bash
# Custom port and host
PORT=9090 HOST=127.0.0.1 ./start.sh

# Custom git repository path (defaults to auto-detecting pycbc-work/apogee)
REVIEWER_REPO_DIR=/path/to/pycbc/clone ./start.sh

# Custom data storage path
REVIEWER_DATA_FILE=/path/to/reviewer_feedback.json ./start.sh
```

Example `config.json`:
```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8080
  },
  "project": {
    "working_tree_path": "/home/ahnitz/projects/claude/searchdev/pycbc-work/apogee",
    "upstream_remote": "upstream",
    "upstream_branch": "master",
    "maintainer_fork": "ahnitz/pycbc",
    "dev_branch": "firinspiral3-multidet-asym"
  }
}
```

---

## 6. Directory Structure

```
agent-dual-review/
├── start.sh -> scripts/start_review_stack.sh
├── stop.sh -> scripts/stop_review_stack.sh
├── restart.sh -> scripts/restart_review_stack.sh
├── status.sh -> scripts/status_review_stack.sh
├── SETUP.md                                 # This setup guide
├── PYCBC_DEVELOPMENT_GUIDELINES.md          # Architectural & coding guidelines
├── config.json                              # Main configuration file
├── scripts/
│   ├── start_review_stack.sh               # Complete startup orchestration
│   ├── stop_review_stack.sh                # Clean shutdown orchestration
│   ├── restart_review_stack.sh             # One-command restart
│   └── status_review_stack.sh              # Live diagnostic reporter
├── server/
│   ├── review_server.py                    # Multi-endpoint HTTP review server
│   └── rationale_catalog.py                # Pre-curated commit rationale catalog
├── sentinel/
│   ├── auto_responder.py                   # Continuous feedback auto-responder
│   ├── ci_tracker.py                       # GitHub Actions CI matrix tracker
│   ├── rebase_monitor.py                   # Automated upstream master rebaser
│   ├── dev_reconciler.py                   # Dev branch commit ingestion engine
│   └── feedback_cli.py                     # CLI for listing/resolving review comments
├── dashboard/
│   └── pr_review_dashboard.html            # Standalone review UI with untruncated diffs
├── data/
│   ├── reviewer_feedback.json              # Review feedback ledger (comments & responses)
│   ├── ci_status.json                      # Cached CI matrix results
│   ├── rebase_status.json                  # Upstream rebase synchronization state
│   └── wave_roadmap.json                   # Waves 1-4 PR candidate decomposition
├── .pids/                                  # Active daemon process IDs
└── .logs/                                  # Daemon standard output and error logs
```

---

## 7. REST API Endpoints

The review server exposes standard JSON endpoints:

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/` | `GET` | Serves the main review dashboard HTML. |
| `/api/health` | `GET` | Health check returning uptime, comment counts, and server status. |
| `/api/comments` | `GET` | Retrieves all review comments and threads. |
| `/api/comments` | `POST` | Submits a new inline or PR-level review comment. |
| `/api/pr/{id}/hunks` | `GET` | Returns 100% untruncated live diff hunks for a specific PR candidate. |
| `/api/prs` | `GET` | Returns all tracked PR branches and live diff stats. |
| `/api/ci/status` | `GET` | Returns live CI matrix pass/fail status for open PRs. |
| `/api/rebase/status`| `GET` | Returns rebase status against `upstream/master`. |
| `/api/dev/status` | `GET` | Returns development branch commit ingestion status. |
| `/api/roadmap` | `GET` | Returns the complete Wave 1–4 PR decomposition roadmap. |

---

## 8. Troubleshooting

### Port 8080 Already In Use
If port 8080 is bound by another process:
```bash
PORT=8081 ./restart.sh
```
Or identify and terminate the conflicting process:
```bash
lsof -i :8080
./stop.sh
```

### Checking Daemon Logs
All background logs are kept in `.logs/`:
```bash
tail -f .logs/review_server.log
tail -f .logs/auto_responder.log
tail -f .logs/ci_tracker.log
tail -f .logs/rebase_monitor.log
tail -f .logs/dev_reconciler.log
```

### Stale PID Files
If a system reboot or hard kill occurred, `./status.sh` will report `STALE PID`. Running `./restart.sh` automatically clears stale PID locks and cleanly relaunches the daemons.
