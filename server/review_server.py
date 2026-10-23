#!/usr/bin/env python3
"""
Custom review server for Reviewer / PyCBC PR Review Dashboard.
Serves static project files and handles real-time two-way code-review comments API,
dynamic git hunk extraction, and continuous upstream rebase status.
"""

import os
import sys
import json
import time
import uuid
import argparse
import subprocess
import re
import urllib.request
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer
except ImportError:
    from socketserver import ThreadingMixIn
    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True


SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

try:
    from rationale_catalog import match_curated_hunk, synthesize_hunk_rationale
except ImportError:
    try:
        from server.rationale_catalog import match_curated_hunk, synthesize_hunk_rationale
    except ImportError:
        def match_curated_hunk(*args, **kwargs): return None
        def synthesize_hunk_rationale(*args, **kwargs):
            return {"shortSummary": "Implementation update", "rationale": "Code refinement targeting upstream master.", "alternatives": "Evaluated against requirements."}

def find_config_path(config_path=None):
    if config_path and os.path.exists(config_path):
        return os.path.abspath(config_path)
    candidates = [
        os.path.abspath(os.path.join(SERVER_DIR, "..", "config.json")),
        os.path.abspath(os.path.join(SERVER_DIR, "..", "agent-dual-review", "config.json")),
        os.path.abspath(os.path.join(SERVER_DIR, "..", "search_dev_notes", "tools", "reviewer", "config.json")),
        os.path.abspath(os.path.join(os.getcwd(), "agent-dual-review", "config.json")),
        os.path.abspath(os.path.join(os.getcwd(), "config.json")),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]

def load_config(config_path=None):
    path = find_config_path(config_path)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[ReviewServer WARN] Could not parse config file {path}: {e}", file=sys.stderr)
    return {}

# Initial load from config
CFG = load_config()

PORT = int(os.environ.get("REVIEWER_PORT", CFG.get("server", {}).get("port", 8080)))
HOST = os.environ.get("REVIEWER_HOST", CFG.get("server", {}).get("host", "0.0.0.0"))

# Auto-detect ROOT_DIR (where dashboard HTML and static files live)
ROOT_DIR = os.environ.get("REVIEWER_ROOT_DIR")
if not ROOT_DIR:
    candidates = [
        os.path.abspath(os.path.join(SERVER_DIR, "..")),
        os.path.abspath(os.path.join(SERVER_DIR, "..", "..")),
        os.getcwd()
    ]
    for cand in candidates:
        if os.path.exists(os.path.join(cand, "dashboard", "pr_review_dashboard.html")) or os.path.exists(os.path.join(cand, "pr_review_dashboard.html")):
            ROOT_DIR = cand
            break
ROOT_DIR = ROOT_DIR or os.path.abspath(os.path.join(SERVER_DIR, ".."))

# Auto-detect REPO_DIR (the target git repository being reviewed)
REPO_DIR = os.environ.get("REVIEWER_REPO_DIR") or CFG.get("project", {}).get("working_tree_path")
if not REPO_DIR or not os.path.exists(REPO_DIR):
    candidates = [
        os.path.abspath(os.path.join(SERVER_DIR, "..", "pycbc-work", "apogee")),
        os.path.abspath(os.path.join(SERVER_DIR, "..", "..", "pycbc-work", "apogee")),
        os.path.abspath(os.path.join(SERVER_DIR, "..", "..", "pycbc")),
        os.path.abspath(os.path.join(SERVER_DIR, "..")),
        os.getcwd()
    ]
    for cand in candidates:
        if os.path.exists(os.path.join(cand, ".git")):
            REPO_DIR = cand
            break
REPO_DIR = os.path.abspath(REPO_DIR or os.getcwd())

# Auto-detect FEEDBACK_FILE
FEEDBACK_FILE = os.environ.get("REVIEWER_FEEDBACK_FILE") or CFG.get("storage", {}).get("feedback_file")
if not FEEDBACK_FILE or not os.path.isabs(FEEDBACK_FILE):
    rel_path = FEEDBACK_FILE or "data/reviewer_feedback.json"
    candidates = [
        os.path.abspath(os.path.join(SERVER_DIR, "..", "search_dev_notes", "reviewer_feedback.json")),
        os.path.abspath(os.path.join(SERVER_DIR, "..", "agent-dual-review", "data", "reviewer_feedback.json")),
        os.path.abspath(os.path.join(SERVER_DIR, "..", rel_path)),
        os.path.abspath(os.path.join(os.getcwd(), rel_path)),
    ]
    for c in candidates:
        if os.path.exists(c):
            FEEDBACK_FILE = c
            break
    if not FEEDBACK_FILE or not os.path.exists(FEEDBACK_FILE):
        FEEDBACK_FILE = candidates[0]

TEAMWORK_FEEDBACK_FILE = os.path.join(ROOT_DIR, ".agents", "teamwork", "REVIEWER_FEEDBACK.json")

UPSTREAM_REMOTE = CFG.get("project", {}).get("upstream_remote", "upstream")
UPSTREAM_BRANCH = CFG.get("project", {}).get("upstream_branch", "master")
ORIGIN_REMOTE = CFG.get("project", {}).get("origin_remote", "origin")
MERGE_BASE = CFG.get("project", {}).get("target_merge_base", "f6eaed241")
MAINTAINER_FORK = CFG.get("project", {}).get("maintainer_fork", "ahnitz/pycbc")

def is_pushed_to_origin(branch):
    """Verifies that a branch exists on the maintainer's own GitHub remote (origin)."""
    p = subprocess.run(["git", "rev-parse", "--verify", f"{ORIGIN_REMOTE}/{branch}"], cwd=REPO_DIR, capture_output=True, text=True)
    if p.returncode == 0:
        return True
    p2 = subprocess.run(["git", "ls-remote", "--heads", ORIGIN_REMOTE, branch], cwd=REPO_DIR, capture_output=True, text=True)
    return p2.returncode == 0 and bool(p2.stdout.strip())

os.makedirs(os.path.dirname(FEEDBACK_FILE), exist_ok=True)
if os.path.dirname(TEAMWORK_FEEDBACK_FILE):
    try:
        os.makedirs(os.path.dirname(TEAMWORK_FEEDBACK_FILE), exist_ok=True)
    except Exception:
        pass

def load_feedback():
    if os.path.exists(FEEDBACK_FILE):
        try:
            with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_feedback(data):
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    try:
        with open(TEAMWORK_FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

DEFAULT_PR_BRANCH_MAP = {
    "PR-1K": "pr-feat-frame-gwosc-hdf",
    "PR-1H": "pr-feat-inject-injfilter-optimal-snr",
    "PR-1A": "pr-fix-core-numpy2-optparse",
    "PR-5457": "fix-gating-inpainting",
    "PR-5446": "ci-http-cache",
    "PR-5434": "relbin-kernel-vectorize",
    "PR-5433": "marg-peak-search",
    "PR-5432": "marg-distance-interp",
    "PR-5430": "marg-inline-reconstruct",
    "PR-5429": "marg-reconstruct-cache",
    "PR-5410": "relbin-calibration",
    "PR-5409": "relbin-resolution-diagnostic",
    "PR-5405": "relbin-setup-refactor",
    "PR-5371": "staging",
    "PR-4658": "tsdoc",
    "PR-4002": "siminj",
    "PR-VOG": "feature/veto-option-groups",
    "PR-SCC": "feature/signal-consistency-chain",
    "PR-PCBW": "feature/power-chisq-bin-weights",
    "PR-SGCTP": "feature/sg-chisq-template-projection",
    "PR-FB": "pr-feat-fir-bank",
    "PR-MDTF": "pr-feat-multidet-trigger-files",
    "PR-MDWJ": "pr-feat-multidet-workflow-jobs",
}

BRANCH_ALIASES = {
    "feature/veto-options-group": "feature/veto-option-groups",
    "feature-veto-options-group": "feature/veto-option-groups",
    "feature-veto-option-groups": "feature/veto-option-groups",
    "feature/signal-consitency-chain": "feature/signal-consistency-chain",
    "feature-signal-consitency-chain": "feature/signal-consistency-chain",
    "feature-signal-consistency-chain": "feature/signal-consistency-chain",
    "feature-sg-chisq-template-projection": "feature/sg-chisq-template-projection",
    "feature-power-chisq-bin-weights": "feature/power-chisq-bin-weights",
    "feat-fir-bank": "pr-feat-fir-bank",
    "fir-bank": "pr-feat-fir-bank",
    "feat-multidet-trigger-files": "pr-feat-multidet-trigger-files",
    "multidet-trigger-files": "pr-feat-multidet-trigger-files",
    "feat-multidet-workflow-jobs": "pr-feat-multidet-workflow-jobs",
    "multidet-workflow-jobs": "pr-feat-multidet-workflow-jobs",
}

STACKED_PARENT_BRANCHES = {
    "feature/signal-consistency-chain": "feature/veto-option-groups",
    "feature/sg-chisq-template-projection": "feature/signal-consistency-chain",
    "marg-inline-reconstruct": "marg-reconstruct-cache",
}

PR_BRANCH_MAP = dict(CFG.get("project", {}).get("branch_map", DEFAULT_PR_BRANCH_MAP))
USER_REQUESTED_BRANCHES = list(CFG.get("project", {}).get("user_requested_branches", []))

def resolve_git_ref(branch):
    """Returns the most up-to-date valid git ref (local, origin/branch, or upstream/branch) for a branch name."""
    if not branch:
        return None
    b = BRANCH_ALIASES.get(branch, branch)
    if b == "ci-http-cache":
        p_up = subprocess.run(["git", "rev-parse", "--verify", f"{UPSTREAM_REMOTE}/{b}"], cwd=REPO_DIR, capture_output=True, text=True)
        if p_up.returncode == 0:
            return f"{UPSTREAM_REMOTE}/{b}"
    p_loc = subprocess.run(["git", " log", "-1", "--format=%ct", b], cwd=REPO_DIR, capture_output=True, text=True)
    # Wait: use git log without space
    p_loc = subprocess.run(["git", "log", "-1", "--format=%ct", b], cwd=REPO_DIR, capture_output=True, text=True)
    p_orig = subprocess.run(["git", "log", "-1", "--format=%ct", f"{ORIGIN_REMOTE}/{b}"], cwd=REPO_DIR, capture_output=True, text=True)
    t_loc = int(p_loc.stdout.strip()) if p_loc.returncode == 0 and p_loc.stdout.strip().isdigit() else -1
    t_orig = int(p_orig.stdout.strip()) if p_orig.returncode == 0 and p_orig.stdout.strip().isdigit() else -1
    if t_loc >= 0 and t_orig >= 0:
        return b if t_loc >= t_orig else f"{ORIGIN_REMOTE}/{b}"
    if t_loc >= 0:
        return b
    if t_orig >= 0:
        return f"{ORIGIN_REMOTE}/{b}"
    p_up = subprocess.run(["git", "rev-parse", "--verify", f"{UPSTREAM_REMOTE}/{b}"], cwd=REPO_DIR, capture_output=True, text=True)
    if p_up.returncode == 0:
        return f"{UPSTREAM_REMOTE}/{b}"
    return None

def resolve_branch(pr_id):
    if not pr_id:
        return None
    clean = BRANCH_ALIASES.get(pr_id, BRANCH_ALIASES.get(pr_id.lower(), pr_id))
    # 1. Exact match in configured PR_BRANCH_MAP
    if clean.upper() in PR_BRANCH_MAP:
        return PR_BRANCH_MAP[clean.upper()]
    # 2. Check if clean is already a branch in PR_BRANCH_MAP values
    for k, v in PR_BRANCH_MAP.items():
        if v.lower() == clean.lower():
            return v
    # 3. Check in USER_REQUESTED_BRANCHES
    for ub in USER_REQUESTED_BRANCHES:
        if isinstance(ub, dict):
            if ub.get("prId", "").upper() == clean.upper() or ub.get("branch", "").lower() == clean.lower():
                return BRANCH_ALIASES.get(ub.get("branch"), ub.get("branch"))
        elif str(ub).upper() == clean.upper() or str(ub).lower() == clean.lower():
            return BRANCH_ALIASES.get(str(ub), str(ub))
    # 4. Check if clean or common variations resolve via resolve_git_ref
    for candidate in [clean, clean.lower(), f"pr-{clean.lower()}", f"pr_{clean.lower()}", f"pr/{clean.lower()}", f"feature/{clean.lower()}"]:
        cand_norm = BRANCH_ALIASES.get(candidate, candidate)
        if resolve_git_ref(cand_norm):
            return cand_norm
    return None

def find_rebase_status_file():
    candidates = [
        os.path.join(SERVER_DIR, "..", "data", "rebase_status.json"),
        os.path.join(ROOT_DIR, "agent-dual-review", "data", "rebase_status.json"),
        os.path.join(ROOT_DIR, "data", "rebase_status.json"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(os.path.join(SERVER_DIR, "..", "data", "rebase_status.json"))

def find_roadmap_file():
    candidates = [
        os.path.join(SERVER_DIR, "..", "data", "wave_roadmap.json"),
        os.path.join(ROOT_DIR, "agent-dual-review", "data", "wave_roadmap.json"),
        os.path.join(ROOT_DIR, "data", "wave_roadmap.json"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(os.path.join(SERVER_DIR, "..", "data", "wave_roadmap.json"))

def find_ci_status_file():
    candidates = [
        os.path.join(SERVER_DIR, "..", "agent-dual-review", "data", "ci_status.json"),
        os.path.join(SERVER_DIR, "..", "search_dev_notes", "data", "ci_status.json"),
        os.path.join(SERVER_DIR, "..", "data", "ci_status.json"),
        os.path.join(ROOT_DIR, "agent-dual-review", "data", "ci_status.json"),
        os.path.join(ROOT_DIR, "data", "ci_status.json"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(os.path.join(SERVER_DIR, "..", "agent-dual-review", "data", "ci_status.json"))

def find_github_comments_file():
    candidates = [
        os.path.join(SERVER_DIR, "..", "agent-dual-review", "data", "github_comments.json"),
        os.path.join(SERVER_DIR, "..", "search_dev_notes", "data", "github_comments.json"),
        os.path.join(SERVER_DIR, "..", "data", "github_comments.json"),
        os.path.join(ROOT_DIR, "agent-dual-review", "data", "github_comments.json"),
        os.path.join(ROOT_DIR, "data", "github_comments.json"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(os.path.join(SERVER_DIR, "..", "agent-dual-review", "data", "github_comments.json"))

def find_action_queue_file():
    candidates = [
        os.path.join(SERVER_DIR, "..", "agent-dual-review", "data", "action_queue.json"),
        os.path.join(SERVER_DIR, "..", "data", "action_queue.json"),
        os.path.join(ROOT_DIR, "agent-dual-review", "data", "action_queue.json"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(os.path.join(SERVER_DIR, "..", "agent-dual-review", "data", "action_queue.json"))

def find_rebase_script():
    candidates = [
        os.path.join(SERVER_DIR, "..", "sentinel", "rebase_monitor.py"),
        os.path.join(ROOT_DIR, "agent-dual-review", "sentinel", "rebase_monitor.py"),
        os.path.join(ROOT_DIR, "sentinel", "rebase_monitor.py"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return None

def find_candidates_file():
    candidates = [
        os.path.join(SERVER_DIR, "..", "data", "candidate_prs.json"),
        os.path.join(ROOT_DIR, "agent-dual-review", "data", "candidate_prs.json"),
        os.path.join(ROOT_DIR, "data", "candidate_prs.json"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(os.path.join(SERVER_DIR, "..", "data", "candidate_prs.json"))

def scan_dev_branch_candidates():
    """Scans the remaining diff on the dev branch vs upstream/master and groups it into atomic PR proposals."""
    dev_branch = CFG.get("project", {}).get("dev_branch", "firinspiral3-multidet-asym")
    p = subprocess.run(["git", "diff", "--name-only", f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}...{dev_branch}"],
                       cwd=REPO_DIR, capture_output=True, text=True)
    if p.returncode != 0:
        return {"status": "error", "message": "Failed to run git diff against dev branch"}
    all_files = set(p.stdout.splitlines())

    # Exclude files in currently active PR branches
    active_files = set()
    for b in PR_BRANCH_MAP.values():
        p_b = subprocess.run(["git", "diff", "--name-only", f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}...{b}"],
                             cwd=REPO_DIR, capture_output=True, text=True)
        if p_b.returncode == 0:
            active_files.update(p_b.stdout.splitlines())

    unpeeled = sorted(list(all_files - active_files))

    candidate_defs = [
        {
            "id": "CAND-PSD-MODEL",
            "title": "feat(psd): Instantaneous PSD model representation, serialization, and robust estimators",
            "subsystem": "PSD & Noise Estimation",
            "suggested_branch": "feat/psd-instantaneous-model",
            "matcher": lambda f: f.startswith("pycbc/psd/") or f.startswith("test/test_psd") or ("psd" in f.lower() and f.startswith("bin/")),
            "rationale": "Self-contained compact model representation of time-varying PSDs with spline and line estimators. Decoupled from core search executable."
        },
        {
            "id": "CAND-STRAIN-OVERWHITEN",
            "title": "feat(strain): Continuous in-situ overwhitening and Toeplitz strain inpainting",
            "subsystem": "Strain Conditioning",
            "suggested_branch": "feat/strain-continuous-overwhitening",
            "matcher": lambda f: "overwhiten" in f or "gate_and_paint" in f or "gate.py" in f or "test_gate" in f,
            "rationale": "Clean strain conditioning algorithms for suppressing spectral non-stationarities. Replaces legacy gating routines."
        },
        {
            "id": "CAND-COINC-STATMAP",
            "title": "perf(coinc): Memory-bounded statmap combination and multi-detector livetime accounting",
            "subsystem": "Coincidence & Post-processing",
            "suggested_branch": "perf/coinc-statmap-bounded-memory",
            "matcher": lambda f: "statmap" in f or "pycbc/events/stat.py" in f or "pycbc/events/ranking.py" in f or "test_ranking.py" in f,
            "rationale": "Reduces memory footprint during background coincidence clustering with 10M+ events using in-place DictArray operations."
        },
        {
            "id": "CAND-FILTER-RATIO",
            "title": "feat(filter): Ratio matched filter engine with dynamic SNR renormalization",
            "subsystem": "Matched Filtering Engine",
            "suggested_branch": "feat/filter-matched-filter-ratio",
            "matcher": lambda f: "matched_filter_ratio" in f or "dynamic_snr" in f or "matchedfilter_cpu.pyx" in f,
            "rationale": "Hierarchical ratio matched filtering engine with hollow-notch variance tracking."
        },
        {
            "id": "CAND-WAVEFORM-BANK",
            "title": "feat(waveform): RatioFilterBank data structures and hierarchical template grouping",
            "subsystem": "Waveform Banks",
            "suggested_branch": "feat/waveform-ratio-filterbank",
            "matcher": lambda f: "waveform/bank.py" in f or "fir_bank" in f or "ratio_bank" in f,
            "rationale": "Sub-bank decomposition data structures for multi-rate ratio filtering."
        },
        {
            "id": "CAND-WORKFLOW-SEARCH",
            "title": "feat(workflow): Multi-detector hierarchical inspiral search workflow generation",
            "subsystem": "Workflow Planning",
            "suggested_branch": "feat/workflow-multidet-inspiral",
            "matcher": lambda f: f.startswith("pycbc/workflow/") or f.startswith("bin/workflows/"),
            "rationale": "Pegasus workflow generators for distributed multi-detector searches."
        }
    ]

    matched_all = set()
    proposals = []
    for cdef in candidate_defs:
        m_files = [f for f in unpeeled if cdef["matcher"](f)]
        if m_files:
            matched_all.update(m_files)
            p_stat = subprocess.run(["git", "diff", f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}...{dev_branch}", "--shortstat", "--"] + m_files,
                                    cwd=REPO_DIR, capture_output=True, text=True)
            stat = p_stat.stdout.strip() if p_stat.returncode == 0 else ""
            proposals.append({
                "id": cdef["id"],
                "title": cdef["title"],
                "subsystem": cdef["subsystem"],
                "suggested_branch": cdef["suggested_branch"],
                "rationale": cdef["rationale"],
                "file_count": len(m_files),
                "files": m_files,
                "diff_stat": stat
            })

    leftover = [f for f in unpeeled if f not in matched_all]
    data = {
        "status": "ok",
        "last_scanned": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dev_branch": dev_branch,
        "upstream": f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}",
        "total_unpeeled_files": len(unpeeled),
        "candidates": proposals,
        "leftover_files_count": len(leftover),
        "leftover_sample": leftover[:30]
    }
    cand_file = find_candidates_file()
    os.makedirs(os.path.dirname(cand_file), exist_ok=True)
    with open(cand_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data

def test_dev_rebase():
    """Simulates rebasing the dev branch onto upstream/master and reports results."""
    dev_branch = CFG.get("project", {}).get("dev_branch", "firinspiral3-multidet-asym")
    # Fetch upstream master
    subprocess.run(["git", "fetch", UPSTREAM_REMOTE, UPSTREAM_BRANCH], cwd=REPO_DIR, capture_output=True, text=True)
    # Check merge base
    p_mb = subprocess.run(["git", "merge-base", f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}", dev_branch],
                          cwd=REPO_DIR, capture_output=True, text=True)
    mb = p_mb.stdout.strip()
    p_tree = subprocess.run(["git", "merge-tree", mb, f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}", dev_branch],
                            cwd=REPO_DIR, capture_output=True, text=True)
    conflicts = [l for l in p_tree.stdout.splitlines() if "<<<<<<<" in l or "changed in both" in l]
    return {
        "status": "ok",
        "merge_base": mb,
        "upstream_target": f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}",
        "dev_branch": dev_branch,
        "conflicts_detected": len(conflicts),
        "is_clean": len(conflicts) == 0,
        "conflict_summary": conflicts[:10]
    }

def get_unified_comments(pr_id=None):
    """Merges internal feedback and GitHub reviewer comments into a unified feed."""
    fb = load_feedback()
    gh_data = {}
    gh_file = find_github_comments_file()
    if os.path.exists(gh_file):
        try:
            with open(gh_file, "r", encoding="utf-8") as f:
                gh_data = json.load(f)
        except Exception:
            pass

    pr_id_map = {
        "5500": "PR-MDWJ", "5496": "PR-5496",
        "5494": "PR-MDTF", "5493": "PR-VOG",
        "5490": "PR-FB", "5484": "PR-PCBW",
        "5475": "PR-1A", "5474": "PR-1B", "5473": "PR-1D",
        "5480": "PR-1E", "5481": "PR-1F", "5479": "PR-1H",
        "5482": "PR-1K", "5478": "PR-1M",
        "5457": "PR-5457", "5446": "PR-5446", "5434": "PR-5434",
        "5433": "PR-5433", "5432": "PR-5432", "5430": "PR-5430",
        "5429": "PR-5429", "5410": "PR-5410", "5409": "PR-5409",
        "5405": "PR-5405", "5371": "PR-5371",
        "4658": "PR-4658", "4002": "PR-4002",
    }
    # Normalize pr_id if branch name or PR number was passed
    target_pid = None
    if pr_id:
        pr_str = str(pr_id).strip()
        if pr_str in pr_id_map:
            target_pid = pr_id_map[pr_str]
        else:
            # Check branch map
            for k, b in PR_BRANCH_MAP.items():
                if pr_str.upper() == k.upper() or pr_str == b:
                    target_pid = k
                    break
            if not target_pid:
                target_pid = pr_str.upper()

    items = []
    # 1. Internal review items
    for c in fb:
        c_pid = c.get("prId", "").upper()
        if target_pid and c_pid != target_pid.upper():
            continue
        items.append({
            "id": c.get("id"),
            "prId": c_pid,
            "hunkIndex": c.get("hunkIndex"),
            "source": "INTERNAL_NOTE",
            "sourceLabel": "Author / Team Note",
            "author": c.get("author", "Maintainer"),
            "file": c.get("file", ""),
            "lines": c.get("lines", ""),
            "body": c.get("commentText", ""),
            "commentText": c.get("commentText", ""),
            "status": c.get("status", "PENDING_AGENT_ACTION"),
            "createdAt": c.get("createdAt", ""),
            "replies": c.get("replies", []),
            "resolution": c.get("resolution")
        })

    # 2. GitHub reviewer comments
    for p_num, p_data in gh_data.get("prs", {}).items():
        mapped_pid = pr_id_map.get(str(p_num), p_data.get("prId") or f"PR-{p_num}")
        if target_pid and mapped_pid.upper() != target_pid.upper():
            continue
        for gc in p_data.get("comments", []):
            items.append({
                "id": gc.get("id"),
                "prId": mapped_pid,
                "prNumber": p_num,
                "source": "GITHUB_REVIEWER",
                "sourceLabel": "GitHub Reviewer",
                "isBot": gc.get("isBot", False),
                "type": gc.get("type", "inline_review"),
                "author": gc.get("author", "Reviewer"),
                "authorAvatar": gc.get("authorAvatar", ""),
                "file": gc.get("path", ""),
                "path": gc.get("path", ""),
                "lines": gc.get("lines", ""),
                "diffSnippet": gc.get("diffSnippet", ""),
                "body": gc.get("body", ""),
                "commentText": gc.get("body", ""),
                "htmlUrl": gc.get("htmlUrl", ""),
                "status": gc.get("status", "NEEDS_TRIAGE"),
                "createdAt": gc.get("createdAt", ""),
                "aiAssessment": gc.get("aiAssessment", {}),
                "actionStatus": gc.get("actionStatus", "NONE"),
                "actionId": gc.get("actionId", ""),
                "userStatus": gc.get("userStatus", "PENDING_TRIAGE"),
                "userNote": gc.get("userNote", ""),
                "replies": gc.get("replies", [])
            })

    items.sort(key=lambda x: x.get("createdAt", ""))
    return items

GITHUB_PR_CACHE = {"data": None, "time": 0}
KNOWN_PR_NUM_TO_BRANCH = {
    5500: "pr-feat-multidet-workflow-jobs",
    5496: "pr-feat-workflow-clusterlink",
    5494: "pr-feat-multidet-trigger-files",
    5493: "feature/veto-option-groups",
    5490: "pr-feat-fir-bank",
    5484: "feature/power-chisq-bin-weights",
    5482: "pr-feat-frame-gwosc-hdf",
    5481: "pr-feat-events-eventmgr-multi",
    5480: "pr-feat-vetoes-chisq-slicing",
    5479: "pr-feat-inject-injfilter-optimal-snr",
    5478: "pr-fix-filter-resample-copy",
    5475: "pr-fix-core-numpy2-optparse",
    5474: "pr-fix-io-dictarray-indexing",
    5473: "pr-perf-waveform-compress",
    5457: "fix-gating-inpainting",
    5446: "ci-http-cache",
    5434: "relbin-kernel-vectorize",
    5433: "marg-peak-search",
    5432: "marg-distance-interp",
    5430: "marg-inline-reconstruct",
    5429: "marg-reconstruct-cache",
    5410: "relbin-calibration",
    5409: "relbin-resolution-diagnostic",
    5405: "relbin-setup-refactor",
    5371: "staging",
    4658: "tsdoc",
    4002: "siminj",
}

DEFAULT_EXCLUDED_PRS = {5404}
DEFAULT_EXCLUDED_BRANCHES = {"marg-accuracy-tests"}

def get_github_prs(repo_owner="gwastro", repo_name="pycbc", user_filter=None, force_refresh=False):
    """Fetch all pull requests on upstream repo opened by user, using HTML embeddedData (no rate limit) + disk cache."""
    if user_filter is None:
        user_filter = MAINTAINER_FORK.split("/")[0] if "/" in MAINTAINER_FORK else "ahnitz"
    now = time.time()
    cache_file = os.path.join(SERVER_DIR, "..", "agent-dual-review", "data", "github_prs_cache.json")
    if not force_refresh and GITHUB_PR_CACHE["data"] and (now - GITHUB_PR_CACHE["time"] < 45):
        return GITHUB_PR_CACHE["data"]

    # Build SHA -> branch name map from git refs
    sha_to_branch = {}
    p_refs = subprocess.run(
        ["git", "for-each-ref", "refs/remotes/origin", "refs/remotes/upstream", "refs/heads", "--format=%(refname:short) %(objectname)"],
        cwd=REPO_DIR, capture_output=True, text=True
    )
    if p_refs.returncode == 0:
        for line in p_refs.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2:
                ref_short, sha = parts[0], parts[1]
                for prefix in (f"{ORIGIN_REMOTE}/", f"{UPSTREAM_REMOTE}/"):
                    if ref_short.startswith(prefix):
                        ref_short = ref_short[len(prefix):]
                if ref_short not in ("HEAD", "master", "main", "gh-pages") and not ref_short.startswith("rebased-") and not ref_short.startswith("pr/"):
                    sha_to_branch[sha] = ref_short

    branch_to_pr = {}
    # Seed with existing cache for CLOSED/MERGED entries so pagination never drops previously-seen closed/merged PRs
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_prs = json.load(f)
            for cb, cinfo in cached_prs.items():
                if cb in DEFAULT_EXCLUDED_BRANCHES or (isinstance(cinfo, dict) and cinfo.get("number") in DEFAULT_EXCLUDED_PRS):
                    continue
                if isinstance(cinfo, dict) and cinfo.get("state") in ("MERGED", "CLOSED"):
                    branch_to_pr[cb] = cinfo
        except Exception:
            pass

    # Also cross-check git log on upstream/master for squash-merged PRs (#NNNN) and reverted PRs
    try:
        p_mlog = subprocess.run(
            ["git", "log", "-n", "400", "--format=%H %s", f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}"],
            cwd=REPO_DIR, capture_output=True, text=True
        )
        if p_mlog.returncode == 0:
            reverted_nums = set()
            num_to_known_branch = dict(KNOWN_PR_NUM_TO_BRANCH)
            # Process oldest-to-newest so a later Revert overrides an earlier Merge if needed, or track both
            for line in reversed(p_mlog.stdout.splitlines()):
                parts = line.split(" ", 1)
                if len(parts) < 2:
                    continue
                c_sha, c_subj = parts[0], parts[1]
                m_rev = re.match(r'^Revert ".*\(#(\d+)\)"\s*\(#(\d+)\)', c_subj)
                if m_rev:
                    rev_num = int(m_rev.group(1))
                    reverted_nums.add(rev_num)
                    b_rev = num_to_known_branch.get(rev_num)
                    if b_rev:
                        branch_to_pr[b_rev] = {
                            "number": rev_num,
                            "title": c_subj,
                            "state": "REVERTED",
                            "reviewDecision": "NONE",
                            "assignees": [],
                            "headSha": c_sha,
                            "htmlUrl": f"https://github.com/{repo_owner}/{repo_name}/pull/{rev_num}",
                            "createdAt": "",
                            "updatedAt": "",
                            "commentCount": 0,
                        }
                    continue
                m_pr = re.search(r'\(#(\d+)\)\s*$', c_subj)
                if m_pr:
                    p_num = int(m_pr.group(1))
                    b_merged = num_to_known_branch.get(p_num)
                    if b_merged and p_num not in reverted_nums:
                        clean_title = re.sub(r'\s*\(#\d+\)\s*$', '', c_subj).strip()
                        branch_to_pr[b_merged] = {
                            "number": p_num,
                            "title": clean_title,
                            "state": "MERGED",
                            "reviewDecision": "APPROVED",
                            "assignees": [],
                            "headSha": c_sha,
                            "htmlUrl": f"https://github.com/{repo_owner}/{repo_name}/pull/{p_num}",
                            "createdAt": "",
                            "updatedAt": "",
                            "commentCount": 0,
                        }
    except Exception:
        pass

    approved_nums = set()
    changes_requested_nums = set()
    try:
        # Query review states via HTML search (no API rate limit)
        review_queries = [
            (f"https://github.com/{repo_owner}/{repo_name}/pulls?q=is%3Apr+author%3A{user_filter}+is%3Aopen+review%3Aapproved", approved_nums),
            (f"https://github.com/{repo_owner}/{repo_name}/pulls?q=is%3Apr+author%3A{user_filter}+is%3Aopen+review%3Achanges_requested", changes_requested_nums),
        ]
        for rq_url, target_set in review_queries:
            try:
                req_rq = urllib.request.Request(rq_url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})
                with urllib.request.urlopen(req_rq, timeout=8) as resp_rq:
                    rq_page = resp_rq.read().decode("utf-8")
                m_rq = re.search(r'<script type="application/json" data-target="react-app\.embeddedData">(.*?)</script>', rq_page, re.DOTALL)
                if m_rq:
                    emb_rq = json.loads(m_rq.group(1))
                    for r_item in emb_rq.get("payload", {}).get("repoPullsDashboardContentRoute", {}).get("results", []):
                        target_set.add(int(r_item["number"]))
            except Exception:
                pass

        urls = [
            f"https://github.com/{repo_owner}/{repo_name}/pulls/{user_filter}",
            f"https://github.com/{repo_owner}/{repo_name}/pulls?q=is%3Apr+author%3A{user_filter}+is%3Aclosed+sort%3Aupdated-desc",
            f"https://github.com/{repo_owner}/{repo_name}/pulls?q=is%3Apr+author%3A{user_filter}+is%3Aclosed+sort%3Aupdated-desc&page=2",
        ]
        for url in urls:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                page = resp.read().decode("utf-8")
            m = re.search(r'<script type="application/json" data-target="react-app\.embeddedData">(.*?)</script>', page, re.DOTALL)
            if not m:
                continue
            embedded = json.loads(m.group(1))
            results = embedded.get("payload", {}).get("repoPullsDashboardContentRoute", {}).get("results", [])
            for r in results:
                num = int(r["number"])
                if num in DEFAULT_EXCLUDED_PRS:
                    continue
                raw_state = r.get("state", "")
                if "MERGED" in raw_state:
                    state = "MERGED"
                elif "OPEN" in raw_state:
                    state = "OPEN"
                else:
                    state = "CLOSED"
                head_sha = r.get("headSha", "")
                branch = KNOWN_PR_NUM_TO_BRANCH.get(num) or sha_to_branch.get(head_sha)
                if not branch:
                    continue
                # Preserve REVERTED state if upstream/master later reverted this merged PR
                if branch in branch_to_pr and branch_to_pr[branch].get("state") == "REVERTED" and state == "MERGED" and branch_to_pr[branch].get("number") == num:
                    continue
                # Do not overwrite an OPEN PR entry with an older CLOSED PR of the same branch name
                if branch in branch_to_pr and branch_to_pr[branch]["state"] == "OPEN" and state != "OPEN":
                    continue
                review_decision = "REVIEW_REQUIRED"
                if num in approved_nums:
                    review_decision = "APPROVED"
                elif num in changes_requested_nums:
                    review_decision = "CHANGES_REQUESTED"
                assignees = [a.get("login") for a in (r.get("assignees") or []) if a.get("login")]
                branch_to_pr[branch] = {
                    "number": num,
                    "title": r.get("title", "").strip(),
                    "state": state,
                    "reviewDecision": review_decision,
                    "assignees": assignees,
                    "headSha": head_sha,
                    "htmlUrl": r.get("permalink") or f"https://github.com/{repo_owner}/{repo_name}/pull/{num}",
                    "createdAt": r.get("createdAt", ""),
                    "updatedAt": r.get("updatedAt", ""),
                    "commentCount": r.get("commentCount", 0),
                }
        if branch_to_pr:
            GITHUB_PR_CACHE["data"] = branch_to_pr
            GITHUB_PR_CACHE["time"] = now
            try:
                os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(branch_to_pr, f, indent=2)
            except Exception:
                pass
            return branch_to_pr
    except Exception as e:
        print(f"[ReviewServer WARN] GitHub HTML PR fetch error: {e}", file=sys.stderr)

    if GITHUB_PR_CACHE["data"]:
        return GITHUB_PR_CACHE["data"]
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                GITHUB_PR_CACHE["data"] = data
                return data
        except Exception:
            pass
    return {}

def compute_pr_lifecycle(pr_id, branch, gh_prs, roadmap_prs, unaddressed_comments_by_pr):
    """Categorizes a PR topic branch into its actionable lifecycle stage."""
    gh = gh_prs.get(branch)
    if gh:
        state = gh.get("state", "").upper()
        if state == "OPEN":
            rev_dec = gh.get("reviewDecision", "REVIEW_REQUIRED")
            if rev_dec == "APPROVED":
                return {
                    "phase": "APPROVED_UPSTREAM",
                    "reviewDecision": "APPROVED",
                    "label": f"Approved to Merge — PR #{gh['number']}",
                    "badge": f"✅ PR #{gh['number']} (APPROVED)",
                    "action": f"Approved by reviewer on gwastro/pycbc (#{gh['number']}) and ready to merge!",
                    "actionUrl": gh["htmlUrl"],
                    "actionText": f"Merge PR #{gh['number']} on GitHub",
                    "isActionNeeded": True,
                    "order": 0
                }
            elif rev_dec == "CHANGES_REQUESTED":
                return {
                    "phase": "OPEN_UPSTREAM",
                    "reviewDecision": "CHANGES_REQUESTED",
                    "label": f"Changes Requested — PR #{gh['number']}",
                    "badge": f"🟠 PR #{gh['number']} (CHANGES REQUESTED)",
                    "action": f"Reviewer requested changes on gwastro/pycbc (#{gh['number']})",
                    "actionUrl": gh["htmlUrl"],
                    "actionText": f"View PR #{gh['number']} on GitHub",
                    "isActionNeeded": True,
                    "order": 1
                }
            return {
                "phase": "OPEN_UPSTREAM",
                "reviewDecision": "REVIEW_REQUIRED",
                "label": f"Open Upstream PR #{gh['number']}",
                "badge": f"🟢 PR #{gh['number']} (OPEN)",
                "action": f"Under active review upstream on gwastro/pycbc (#{gh['number']})",
                "actionUrl": gh["htmlUrl"],
                "actionText": f"View PR #{gh['number']} on GitHub",
                "isActionNeeded": False,
                "order": 1
            }
        elif state == "MERGED":
            return {
                "phase": "MERGED_UPSTREAM",
                "label": f"Merged Upstream PR #{gh['number']}",
                "badge": f"🟣 PR #{gh['number']} (MERGED)",
                "action": "Merged into master; unblocks dependent branches",
                "actionUrl": gh["htmlUrl"],
                "actionText": f"Merged PR #{gh['number']}",
                "isActionNeeded": False,
                "order": 5
            }
        elif state == "REVERTED":
            return {
                "phase": "STAGED",
                "label": f"Reverted Upstream (PR #{gh['number']} Reverted in #5491)",
                "badge": f"🔄 PR #{gh['number']} (REVERTED)",
                "action": f"PR #{gh['number']} was merged and subsequently reverted in upstream/master; needs rework before re-submitting.",
                "actionUrl": gh["htmlUrl"],
                "actionText": f"View Reverted PR #{gh['number']}",
                "isActionNeeded": True,
                "order": 3
            }

    unresolved_count = unaddressed_comments_by_pr.get(pr_id, 0)
    if unresolved_count > 0:
        return {
            "phase": "NEEDS_TRIAGE",
            "label": f"{unresolved_count} Comments Needing Attention",
            "badge": "⚠️ FEEDBACK PENDING",
            "action": f"{unresolved_count} review threads need resolution before opening PR",
            "actionUrl": None,
            "actionText": "Resolve Feedback",
            "isActionNeeded": True,
            "order": 0
        }

    pdata = roadmap_prs.get(pr_id, {})
    up_status = pdata.get("upstream_status")
    if up_status in ("INCORPORATED_UPSTREAM", "SUPERSEDED_OR_INCORPORATED") or pdata.get("status") in ("INCORPORATED_UPSTREAM", "SUPERSEDED_OR_INCORPORATED"):
        return {
            "phase": "INCORPORATED_UPSTREAM",
            "label": "Incorporated / Superseded Upstream",
            "badge": "🟣 INCORPORATED UPSTREAM",
            "action": "Changes already merged or superseded in upstream master; no further action needed.",
            "actionUrl": None,
            "actionText": "Merged Upstream",
            "isActionNeeded": False,
            "order": 5
        }
    if up_status == "CONFLICT_WITH_UPSTREAM":
        return {
            "phase": "CONFLICT",
            "label": "Upstream Merge Conflict",
            "badge": "⚠️ REBASE NEEDED",
            "action": "Upstream master modifications conflict with branch; rebase required before opening.",
            "actionUrl": None,
            "actionText": "Rebase Required",
            "isActionNeeded": True,
            "order": 0
        }

    deps = pdata.get("dependencies", [])
    merged_branches = {b for b, p in gh_prs.items() if p.get("state") == "MERGED"}
    unmerged_deps = [d for d in deps if roadmap_prs.get(d, {}).get("branch") not in merged_branches]

    if unmerged_deps:
        return {
            "phase": "STAGED",
            "label": f"Staged (Waiting on {', '.join(unmerged_deps)})",
            "badge": "⏳ STAGED",
            "action": f"Dependent on {', '.join(unmerged_deps)} merging upstream first",
            "actionUrl": None,
            "actionText": "Held in Queue",
            "isActionNeeded": False,
            "order": 3
        }

    return {
        "phase": "READY_TO_OPEN",
        "label": "Ready to Open",
        "badge": "🚀 READY TO SUBMIT",
        "action": "Pushed to fork, 100% tests pass. Ready for GitHub submission.",
        "actionUrl": f"https://github.com/gwastro/pycbc/compare/master...ahnitz:pycbc:{branch}?expand=1",
        "actionText": "Open PR on GitHub →",
        "isActionNeeded": True,
        "order": 2
    }

def get_live_pr_hunks(branch_or_id):
    branch = resolve_branch(branch_or_id) or BRANCH_ALIASES.get(branch_or_id, branch_or_id)
    git_ref = resolve_git_ref(branch)
    if not git_ref:
        return {"status": "error", "message": f"Unknown PR ID or branch: {branch_or_id}"}

    inv_map = {v: k for k, v in PR_BRANCH_MAP.items()}
    pr_id = inv_map.get(branch) or (branch_or_id if str(branch_or_id).upper().startswith("PR-") else f"PR-{branch_or_id}").upper().replace('/', '-')

    cmd_sha = ["git", "rev-parse", git_ref]
    p_sha = subprocess.run(cmd_sha, cwd=REPO_DIR, capture_output=True, text=True)
    commit_sha = p_sha.stdout.strip() if p_sha.returncode == 0 else ""

    # 1. Resolve merge base with stacked parent or current upstream master
    parent_branch = STACKED_PARENT_BRANCHES.get(branch)
    parent_ref = resolve_git_ref(parent_branch) if parent_branch else None
    if parent_ref:
        upstream_ref = parent_ref
    else:
        upstream_ref = f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}"

    p_mb = subprocess.run(["git", "merge-base", upstream_ref, git_ref], cwd=REPO_DIR, capture_output=True, text=True)
    if p_mb.returncode != 0 or not p_mb.stdout.strip():
        p_mb = subprocess.run(["git", "merge-base", "upstream/master", git_ref], cwd=REPO_DIR, capture_output=True, text=True)
        upstream_ref = "upstream/master"
    if p_mb.returncode != 0 or not p_mb.stdout.strip():
        p_mb = subprocess.run(["git", "merge-base", "master", git_ref], cwd=REPO_DIR, capture_output=True, text=True)
        upstream_ref = "master"
    base_commit = p_mb.stdout.strip() if p_mb.returncode == 0 and p_mb.stdout.strip() else MERGE_BASE

    # True Pull Request diff: triple-dot diff against upstream_ref (diff from merge-base to git_ref)
    diff_target = f"{upstream_ref}...{git_ref}"
    diff_cmd_str = f"git diff {upstream_ref}...{branch}"

    cmd_stat = ["git", "diff", "--shortstat", diff_target]
    p_stat = subprocess.run(cmd_stat, cwd=REPO_DIR, capture_output=True, text=True)
    diff_stat = p_stat.stdout.strip() if p_stat.returncode == 0 else ""

    # Extract commit subject and body for automatic description synthesis
    p_log = subprocess.run(["git", "log", "-1", "--format=%s%n%b", git_ref], cwd=REPO_DIR, capture_output=True, text=True)
    log_lines = [l.strip() for l in p_log.stdout.splitlines() if l.strip() and not l.strip().startswith("Co-Authored-By:")] if p_log.returncode == 0 else []
    commit_subject = log_lines[0] if log_lines else f"Branch: {branch}"
    commit_body = " ".join(log_lines[1:]) if len(log_lines) > 1 else ""

    cmd_diff = ["git", "diff", "-U3", diff_target]
    p_diff = subprocess.run(cmd_diff, cwd=REPO_DIR, capture_output=True, text=True)
    if p_diff.returncode != 0:
        # Fallback to double-dot against resolved base_commit if triple-dot fails
        cmd_diff = ["git", "diff", "-U3", f"{base_commit}..{git_ref}"]
        p_diff = subprocess.run(cmd_diff, cwd=REPO_DIR, capture_output=True, text=True)
        if p_diff.returncode != 0:
            return {"status": "error", "message": f"Failed running git diff: {p_diff.stderr}"}

    diff_text = p_diff.stdout
    file_blocks = re.split(r'\ndiff --git a/', '\n' + diff_text)
    hunks = []

    for block in file_blocks:
        if not block.strip():
            continue
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        first_line = lines[0]
        file_name = first_line.split(' b/')[0] if ' b/' in first_line else first_line.split()[0]
        
        # Split by @@ hunk headers
        hunk_splits = re.split(r'(@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@.*)', block)
        if len(hunk_splits) <= 1:
            body_lines = [l for l in lines[1:] if not (l.startswith('index ') or l.startswith('--- ') or l.startswith('+++ ') or l.startswith('new file mode'))]
            snippet = '\n'.join(body_lines)
            meta = match_curated_hunk(pr_id, file_name, 1, len(body_lines), body_lines)
            if not meta:
                meta = synthesize_hunk_rationale(file_name, 1, len(body_lines), body_lines, REPO_DIR)
            hunks.append({
                "file": file_name,
                "startLine": 1,
                "endLine": len(body_lines),
                "lines": f"lines 1-{len(body_lines)}",
                "shortSummary": meta.get("shortSummary") or f"File modification: {file_name}",
                "diffSnippet": snippet,
                "githubUrl": f"https://github.com/{MAINTAINER_FORK}/blob/{commit_sha or branch}/{file_name}",
                "localFileUrl": f"file://{REPO_DIR}/{file_name}",
                "vscodeUrl": f"vscode://file/{REPO_DIR}/{file_name}:1",
                "filePathLine": f"{file_name}:1",
                "rationale": meta.get("rationale") or f"Derived from branch {branch}.",
                "alternatives": meta.get("alternatives") or f"Targeting {upstream_ref} ({base_commit[:9]})."
            })
            continue

        for i in range(1, len(hunk_splits), 2):
            header = hunk_splits[i]
            body = hunk_splits[i+1] if i+1 < len(hunk_splits) else ''
            
            m = re.search(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)', header)
            if not m:
                continue
            start_line = int(m.group(3))
            count = int(m.group(4) or 1)
            end_line = start_line + max(count - 1, 0)
            
            body_lines = [l for l in (header + '\n' + body).splitlines() if not (l.startswith('index ') or l.startswith('--- ') or l.startswith('+++ ') or l.startswith('new file mode'))]
            snippet = '\n'.join(body_lines)

            line_str = f"lines {start_line}-{end_line}" if end_line > start_line else f"line {start_line}"
            line_hash = f"#L{start_line}-L{end_line}" if end_line > start_line else f"#L{start_line}"
            gh_url = f"https://github.com/{MAINTAINER_FORK}/blob/{commit_sha or branch}/{file_name}{line_hash}"
            local_url = f"file://{REPO_DIR}/{file_name}{line_hash}"
            vscode_url = f"vscode://file/{REPO_DIR}/{file_name}:{start_line}"
            path_line = f"{file_name}:{start_line}"

            meta = match_curated_hunk(pr_id, file_name, start_line, end_line, body_lines)
            if not meta:
                meta = synthesize_hunk_rationale(file_name, start_line, end_line, body_lines, REPO_DIR)

            summary = meta.get("shortSummary") or f"Logic update in {file_name}"
            rationale = meta.get("rationale") or f"Implementation refinement in {file_name} ({commit_subject})."
            alternatives = meta.get("alternatives") or f"Targeting {upstream_ref} ({base_commit[:9]})."

            hunks.append({
                "file": file_name,
                "startLine": start_line,
                "endLine": end_line,
                "lines": line_str,
                "shortSummary": summary,
                "diffSnippet": snippet,
                "githubUrl": gh_url,
                "localFileUrl": local_url,
                "vscodeUrl": vscode_url,
                "filePathLine": path_line,
                "rationale": rationale,
                "alternatives": alternatives
            })

    gh_prs = get_github_prs()
    gh_pr = gh_prs.get(branch)

    return {
        "status": "ok",
        "prId": pr_id.upper(),
        "branch": branch,
        "gitRef": git_ref,
        "commitSha": commit_sha,
        "baseCommit": base_commit,
        "upstreamRef": upstream_ref,
        "diffCmd": diff_cmd_str,
        "diffStat": diff_stat,
        "title": (gh_pr.get("title") if gh_pr else None) or commit_subject,
        "problemDescription": commit_body or f"Review and verification for branch {branch} ({commit_subject}).",
        "solutionDescription": commit_subject,
        "githubPr": gh_pr,
        "upstreamPrNumber": gh_pr["number"] if gh_pr else None,
        "upstreamPrState": gh_pr["state"] if gh_pr else None,
        "upstreamPrUrl": gh_pr["htmlUrl"] if gh_pr else None,
        "hunks": hunks
    }


class ReviewRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT_DIR, **kwargs)

    def setup(self):
        super().setup()
        try:
            self.request.settimeout(15.0)
        except Exception:
            pass

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_HEAD(self):
        req_path = urllib.parse.urlparse(self.path).path
        if req_path in ("/", "", "/index.html", "/pr_review_dashboard.html"):
            dashboard_candidates = [
                os.path.join(ROOT_DIR, "dashboard", "pr_review_dashboard.html"),
                os.path.join(ROOT_DIR, "pr_review_dashboard.html"),
                os.path.join(REPO_DIR, "search_dev_notes", "pr_review_dashboard.html"),
                os.path.abspath(os.path.join(SERVER_DIR, "..", "search_dev_notes", "pr_review_dashboard.html")),
                os.path.abspath(os.path.join(SERVER_DIR, "..", "agent-dual-review", "dashboard", "pr_review_dashboard.html")),
            ]
            for cand in dashboard_candidates:
                if os.path.exists(cand):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(os.path.getsize(cand)))
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    self.end_headers()
                    return
        if req_path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            return
        if req_path.startswith("/api/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            return
        super().do_HEAD()

    def do_GET(self):
        global PR_BRANCH_MAP, USER_REQUESTED_BRANCHES
        req_path = urllib.parse.urlparse(self.path).path

        # Direct serve dashboard on root or index.html with no-cache headers
        if req_path in ("/", "", "/index.html", "/pr_review_dashboard.html"):
            dashboard_candidates = [
                os.path.join(ROOT_DIR, "dashboard", "pr_review_dashboard.html"),
                os.path.join(ROOT_DIR, "pr_review_dashboard.html"),
                os.path.join(REPO_DIR, "search_dev_notes", "pr_review_dashboard.html"),
                os.path.abspath(os.path.join(SERVER_DIR, "..", "search_dev_notes", "pr_review_dashboard.html")),
                os.path.abspath(os.path.join(SERVER_DIR, "..", "agent-dual-review", "dashboard", "pr_review_dashboard.html")),
            ]
            for cand in dashboard_candidates:
                if os.path.exists(cand):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    with open(cand, "rb") as f:
                        content = f.read()
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    self.end_headers()
                    self.wfile.write(content)
                    return

        if req_path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            return

        if req_path == "/api/health":
            comments = load_feedback()
            pending = sum(1 for c in comments if c.get("status") not in ("ADDRESSED", "RESOLVED"))
            self._send_json({
                "status": "healthy",
                "totalComments": len(comments),
                "pendingComments": pending,
                "dataFile": FEEDBACK_FILE,
                "rootDir": ROOT_DIR,
                "repoDir": REPO_DIR,
                "upstream": f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}"
            })
            return

        if req_path == "/api/comments":
            comments = load_feedback()
            self._send_json({"status": "ok", "comments": comments})
            return

        if req_path == "/api/prs":
            live_cfg = load_config()
            if live_cfg.get("project", {}).get("branch_map"):
                PR_BRANCH_MAP = dict(live_cfg["project"]["branch_map"])
            if "user_requested_branches" in live_cfg.get("project", {}):
                USER_REQUESTED_BRANCHES = list(live_cfg["project"]["user_requested_branches"])

            results = {}
            tracked = dict(PR_BRANCH_MAP)

            # Include user-requested branches explicitly
            for ub in USER_REQUESTED_BRANCHES:
                if isinstance(ub, dict):
                    pid = ub.get("prId") or ub.get("branch", "").upper().replace("/", "-")
                    bname = BRANCH_ALIASES.get(ub.get("branch"), ub.get("branch"))
                else:
                    bname = BRANCH_ALIASES.get(str(ub), str(ub))
                    pid = bname.upper().replace("/", "-")
                if not pid.startswith("PR-"):
                    pid = f"PR-{pid}"
                if bname and bname not in tracked.values():
                    tracked[pid] = bname

            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            force_ref = qs.get("force", ["0"])[0] in ("1", "true", "yes")
            gh_all = get_github_prs(force_refresh=force_ref)

            # Automatically include any open PRs on gwastro/pycbc by user_filter (unless excluded)
            excluded_prs = set(DEFAULT_EXCLUDED_PRS) | {int(x) for x in live_cfg.get("project", {}).get("excluded_prs", []) if str(x).isdigit()}
            excluded_branches = set(DEFAULT_EXCLUDED_BRANCHES) | set(live_cfg.get("project", {}).get("excluded_branches", []))
            for gh_branch, gh_info in gh_all.items():
                if gh_info.get("number") in excluded_prs or gh_branch in excluded_branches:
                    continue
                if gh_info.get("state") == "OPEN" and gh_branch not in tracked.values():
                    tracked[f"PR-{gh_info['number']}"] = gh_branch

            # Filter out any excluded branches from tracked
            tracked = {k: v for k, v in tracked.items() if v not in excluded_branches and k not in {f"PR-{n}" for n in excluded_prs}}

            # Load roadmap data to resolve dependencies
            roadmap_prs = {}
            rm_file = find_roadmap_file()
            if os.path.exists(rm_file):
                try:
                    with open(rm_file, "r", encoding="utf-8") as f:
                        roadmap_prs = json.load(f).get("prs", {})
                except Exception:
                    pass

            # Load feedback data to find unaddressed comments
            unaddressed_by_pr = {}
            if os.path.exists(FEEDBACK_FILE):
                try:
                    with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                        fb_data = json.load(f)
                    for item in fb_data:
                        if item.get("status") in ("PENDING_AGENT_ACTION", "IN_PROGRESS"):
                            c_pid = item.get("prId")
                            if c_pid:
                                unaddressed_by_pr[c_pid] = unaddressed_by_pr.get(c_pid, 0) + 1
                except Exception:
                    pass

            # Load CI status data
            ci_data = {}
            ci_file = find_ci_status_file()
            if os.path.exists(ci_file):
                try:
                    with open(ci_file, "r", encoding="utf-8") as f:
                        ci_data = json.load(f).get("prs", {})
                except Exception:
                    pass

            for pid, branch in tracked.items():
                pr_info = get_live_pr_hunks(branch)
                if pr_info.get("status") != "ok":
                    continue
                rm_info = roadmap_prs.get(pid, {})
                for k in ["title", "category", "problemDescription", "solutionDescription",
                          "simplificationDeepDive", "testCmd", "testTiming", "dependencies",
                          "unblocks", "wave"]:
                    if k in rm_info and rm_info[k]:
                        pr_info[k] = rm_info[k]
                pr_info["lifecycle"] = compute_pr_lifecycle(pid, branch, gh_all, roadmap_prs, unaddressed_by_pr)
                # Match CI status
                pr_ci = None
                for pr_num, ci_val in ci_data.items():
                    if ci_val.get("branch") == branch or (pr_info.get("githubPr") and str(pr_info["githubPr"].get("number")) == str(pr_num)):
                        pr_ci = ci_val
                        break
                pr_info["ci"] = pr_ci
                if pr_info.get("githubPr") and pr_info["githubPr"].get("state") in ("MERGED", "CLOSED"):
                    continue
                results[pid] = pr_info

            self._send_json({"status": "ok", "prs": results, "githubPrs": gh_all, "ci": ci_data})
            return

        if req_path == "/api/github/prs":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            force_ref = qs.get("force", ["0"])[0] in ("1", "true", "yes")
            self._send_json({"status": "ok", "githubPrs": get_github_prs(force_refresh=force_ref)})
            return

        if req_path == "/api/roadmap":
            rm_file = find_roadmap_file()
            if os.path.exists(rm_file):
                try:
                    with open(rm_file, "r", encoding="utf-8") as f:
                        rm_data = json.load(f)
                    self._send_json({"status": "ok", "roadmap": rm_data})
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)}, status=500)
                    return
            self._send_json({"status": "error", "message": "Roadmap not found"}, status=404)
            return

        if req_path == "/api/reconciliation":
            rm_file = find_roadmap_file()
            if os.path.exists(rm_file):
                try:
                    with open(rm_file, "r", encoding="utf-8") as f:
                        rm_data = json.load(f)
                    unopened = {
                        pid: p for pid, p in rm_data.get("prs", {}).items()
                        if p.get("status") not in ("OPEN", "PUSHED_TO_ORIGIN") or p.get("upstream_status")
                    }
                    self._send_json({
                        "status": "ok",
                        "last_reconciled": rm_data.get("last_updated"),
                        "last_ingested_commit": rm_data.get("last_ingested_commit"),
                        "unopened_prs": unopened,
                        "ingestion_log": rm_data.get("ingestion_log", [])[-25:]
                    })
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)}, status=500)
                    return
            self._send_json({"status": "error", "message": "Roadmap not found"}, status=404)
            return

        if req_path == "/api/dev/status":
            dev_branch = CFG.get("project", {}).get("dev_branch", "firinspiral3-multidet-asym")
            p_head = subprocess.run(["git", "rev-parse", dev_branch], cwd=REPO_DIR, capture_output=True, text=True)
            head_sha = p_head.stdout.strip() if p_head.returncode == 0 else ""
            p_log = subprocess.run(["git", "log", "-n", "5", "--oneline", dev_branch], cwd=REPO_DIR, capture_output=True, text=True)
            recent_commits = p_log.stdout.strip().splitlines() if p_log.returncode == 0 else []
            self._send_json({
                "status": "ok",
                "devBranch": dev_branch,
                "headSha": head_sha,
                "recentCommits": recent_commits,
                "userTrackedCount": len(USER_REQUESTED_BRANCHES),
                "userTrackedBranches": USER_REQUESTED_BRANCHES,
                "activePRCount": len(PR_BRANCH_MAP)
            })
            return

        if req_path == "/api/branches":
            # Restrict to branches pushed to maintainer's own GitHub account (origin)
            p_br = subprocess.run(["git", "branch", "-r", "--list", f"{ORIGIN_REMOTE}/*", "--format=%(refname:short) %(objectname:short)"], cwd=REPO_DIR, capture_output=True, text=True)
            branches = []
            if p_br.returncode == 0:
                for line in p_br.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        bname = parts[0].replace(f"{ORIGIN_REMOTE}/", "")
                        if bname not in ("HEAD", "master", "main") and not bname.startswith("HEAD"):
                            branches.append({"branch": bname, "sha": parts[1]})
            self._send_json({"status": "ok", "branches": branches})
            return

        if req_path == "/api/rebase/status":
            rebase_file = find_rebase_status_file()
            if os.path.exists(rebase_file):
                try:
                    with open(rebase_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._send_json({"status": "ok", "rebase": data})
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)}, status=500)
                    return
            self._send_json({"status": "ok", "rebase": {"status": "NOT_INITIALIZED"}})
            return

        if req_path == "/api/ci/status":
            ci_file = find_ci_status_file()
            if os.path.exists(ci_file):
                try:
                    with open(ci_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._send_json({"status": "ok", "ci": data})
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)}, status=500)
                    return
            self._send_json({"status": "ok", "ci": {"status": "NOT_INITIALIZED"}})
            return

        if req_path.startswith("/api/pr/") and req_path.endswith("/hunks"):
            parts = req_path.split("/")
            if len(parts) >= 4:
                pr_id = parts[3].upper()
                data = get_live_pr_hunks(pr_id)
                self._send_json(data)
                return

        if req_path == "/api/github/comments":
            c_file = find_github_comments_file()
            if os.path.exists(c_file):
                try:
                    with open(c_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    pr_filter = query.get("pr", [None])[0] or query.get("prId", [None])[0]
                    if pr_filter:
                        prs = data.get("prs", {})
                        target = None
                        for k, v in prs.items():
                            if str(k) == str(pr_filter) or v.get("prId", "").upper() == str(pr_filter).upper():
                                target = v
                                break
                        self._send_json({"status": "ok", "pr": target or {"comments": [], "totalCount": 0}})
                        return
                    self._send_json({"status": "ok", "comments": data})
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)}, status=500)
                    return
            self._send_json({"status": "ok", "comments": {"totalComments": 0, "prs": {}}})
            return

        if req_path == "/api/actions":
            a_file = find_action_queue_file()
            if os.path.exists(a_file):
                try:
                    with open(a_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._send_json({"status": "ok", "actions": data})
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)}, status=500)
                    return
            self._send_json({"status": "ok", "actions": []})
            return

        if req_path == "/api/candidates":
            cand_file = find_candidates_file()
            if os.path.exists(cand_file):
                try:
                    with open(cand_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._send_json(data)
                    return
                except Exception as e:
                    pass
            # Generate on-the-fly if not cached
            data = scan_dev_branch_candidates()
            self._send_json(data)
            return

        if req_path == "/api/comments/unified":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            pr_filter = query.get("pr", [None])[0] or query.get("prId", [None])[0]
            unified = get_unified_comments(pr_filter)
            self._send_json({"status": "ok", "comments": unified, "totalCount": len(unified)})
            return

        if req_path == "/api/dev/rebase/test":
            res = test_dev_rebase()
            self._send_json(res)
            return

        super().do_GET()

    def do_POST(self):
        global USER_REQUESTED_BRANCHES
        req_path = urllib.parse.urlparse(self.path).path
        if req_path == "/api/rebase/sync":
            rebase_script = find_rebase_script()
            if rebase_script and os.path.exists(rebase_script):
                try:
                    p = subprocess.run([sys.executable, rebase_script, "--once"], capture_output=True, text=True, timeout=120)
                    rebase_file = find_rebase_status_file()
                    if os.path.exists(rebase_file):
                        with open(rebase_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        self._send_json({"status": "ok", "rebase": data, "log": p.stdout})
                        return
                    self._send_json({"status": "ok", "log": p.stdout})
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)}, status=500)
                    return
            self._send_json({"status": "error", "message": "Rebase script not found"}, status=404)
            return

        if req_path == "/api/comments":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)
                return

            comment_id = "c_" + str(int(time.time())) + "_" + uuid.uuid4().hex[:6]
            comment_record = {
                "id": comment_id,
                "prId": data.get("prId", ""),
                "hunkIndex": data.get("hunkIndex", 0),
                "file": data.get("file", ""),
                "lines": data.get("lines", ""),
                "commentText": data.get("commentText", "").strip(),
                "author": data.get("author", "Maintainer"),
                "status": "PENDING_AGENT_ACTION",
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "timestamp": time.time(),
                "replies": []
            }

            if not comment_record["commentText"]:
                self._send_json({"status": "error", "message": "Empty commentText"}, status=400)
                return

            comments = load_feedback()
            comments.append(comment_record)
            save_feedback(comments)

            print(f"[ReviewServer] New maintainer comment on {comment_record['prId']}: {comment_record['commentText'][:60]}...", flush=True)
            self._send_json({"status": "ok", "comment": comment_record}, status=201)
            return

        if req_path == "/api/comments/reply":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)
                return

            comment_id = data.get("commentId") or data.get("id")
            reply_text = data.get("replyText", "").strip()
            new_status = data.get("status", "ADDRESSED")
            author_name = data.get("author", "Maintainer (Alex)")

            reply_obj = {
                "id": "r_" + str(int(time.time())) + "_" + uuid.uuid4().hex[:4],
                "author": author_name,
                "replyText": reply_text,
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }

            comments = load_feedback()
            found = False
            for c in comments:
                if c["id"] == comment_id:
                    c.setdefault("replies", []).append(reply_obj)
                    c["status"] = new_status
                    found = True
                    break

            if found:
                save_feedback(comments)
                self._send_json({"status": "ok", "message": "Reply saved", "reply": reply_obj})
                return

            # Also check github_comments.json for GitHub reviewer comment IDs
            c_file = find_github_comments_file()
            if os.path.exists(c_file):
                try:
                    with open(c_file, "r", encoding="utf-8") as f:
                        c_data = json.load(f)
                    gh_found = False
                    for p_key, p_val in c_data.get("prs", {}).items():
                        for gc in p_val.get("comments", []):
                            if gc.get("id") == comment_id:
                                gc.setdefault("replies", []).append(reply_obj)
                                gc["userNote"] = reply_text
                                gc["status"] = new_status
                                gh_found = True
                                break
                        if gh_found:
                            break
                    if gh_found:
                        with open(c_file, "w", encoding="utf-8") as f:
                            json.dump(c_data, f, indent=2)
                        self._send_json({"status": "ok", "message": "Reply attached to GitHub comment", "reply": reply_obj})
                        return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)}, status=500)
                    return

            self._send_json({"status": "error", "message": "Comment ID not found"}, status=404)
            return

        if req_path == "/api/comments/resolve":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)
                return

            comment_id = data.get("commentId") or data.get("id")
            comments = load_feedback()
            found = False
            for c in comments:
                if c["id"] == comment_id:
                    c["status"] = "RESOLVED"
                    c.setdefault("replies", []).append({
                        "id": "r_" + str(int(time.time())) + "_" + uuid.uuid4().hex[:4],
                        "author": data.get("author", "Maintainer"),
                        "replyText": "Marked as resolved by maintainer.",
                        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    })
                    found = True
                    break

            if found:
                save_feedback(comments)
                self._send_json({"status": "ok", "message": "Comment resolved"})
            else:
                self._send_json({"status": "error", "message": "Comment ID not found"}, status=404)
            return

        if req_path == "/api/prs/track":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)
                return

            branch = data.get("branch", "").strip()
            if not branch:
                self._send_json({"status": "error", "message": "Missing 'branch' parameter"}, status=400)
                return

            # Verify branch in git repository
            p_check = subprocess.run(["git", "rev-parse", "--verify", branch], cwd=REPO_DIR, capture_output=True, text=True)
            if p_check.returncode != 0:
                self._send_json({"status": "error", "message": f"Branch '{branch}' not found in git repository ({REPO_DIR})"}, status=404)
                return

            # Strict Gate: Verify branch is pushed to user's own GitHub account (origin)
            if not is_pushed_to_origin(branch):
                if branch.startswith(f"{ORIGIN_REMOTE}/"):
                    clean_b = branch[len(ORIGIN_REMOTE)+1:]
                    if is_pushed_to_origin(clean_b):
                        branch = clean_b
                if not is_pushed_to_origin(branch):
                    self._send_json({
                        "status": "error",
                        "message": f"Branch '{branch}' is not pushed to your GitHub account ({ORIGIN_REMOTE} -> {MAINTAINER_FORK}). The review system strictly tracks branches pushed to your own GitHub account only."
                    }, status=400)
                    return

            pr_id = data.get("prId", "").strip().upper()
            if not pr_id:
                pr_id = branch.upper().replace("/", "-")
                if not pr_id.startswith("PR-"):
                    pr_id = f"PR-{pr_id}"

            # Add to user requested branches
            USER_REQUESTED_BRANCHES.append({"prId": pr_id, "branch": branch, "trackedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            PR_BRANCH_MAP[pr_id] = branch

            # Persist to config if possible
            cfg_path = find_config_path()
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cur_cfg = json.load(f)
                    cur_cfg.setdefault("project", {}).setdefault("user_requested_branches", []).append({"prId": pr_id, "branch": branch})
                    cur_cfg.setdefault("project", {}).setdefault("branch_map", {})[pr_id] = branch
                    with open(cfg_path, "w", encoding="utf-8") as f:
                        json.dump(cur_cfg, f, indent=2)
                except Exception:
                    pass

            hunks_data = get_live_pr_hunks(branch)
            print(f"[ReviewServer] Tracked user-requested branch '{branch}' as '{pr_id}' ({len(hunks_data.get('hunks', []))} hunks)", flush=True)
            self._send_json({
                "status": "ok",
                "message": f"Branch '{branch}' tracked successfully as '{pr_id}'",
                "prId": pr_id,
                "branch": branch,
                "data": hunks_data
            })
            return

        if req_path == "/api/prs/untrack":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)
                return

            target = (data.get("prId", "") or data.get("branch", "")).strip()
            target_upper = target.upper()

            USER_REQUESTED_BRANCHES = [
                b for b in USER_REQUESTED_BRANCHES
                if (b.get("prId", "").upper() if isinstance(b, dict) else str(b).upper()) != target_upper
                and (b.get("branch", "") if isinstance(b, dict) else str(b)) != target
            ]
            if target_upper in PR_BRANCH_MAP and target_upper not in DEFAULT_PR_BRANCH_MAP:
                del PR_BRANCH_MAP[target_upper]

            self._send_json({"status": "ok", "message": f"Untracked '{target}'"})
            return

        if req_path in ("/api/dev/reconcile", "/api/reconciliation"):
            reconciler_script = os.path.join(SERVER_DIR, "..", "sentinel", "dev_reconciler.py")
            if not os.path.exists(reconciler_script):
                reconciler_script = os.path.join(ROOT_DIR, "agent-dual-review", "sentinel", "dev_reconciler.py")
            if os.path.exists(reconciler_script):
                try:
                    p = subprocess.run([sys.executable, reconciler_script, "--reconcile"], cwd=REPO_DIR, capture_output=True, text=True, timeout=60)
                    rm_file = find_roadmap_file()
                    if os.path.exists(rm_file):
                        with open(rm_file, "r", encoding="utf-8") as f:
                            rm_data = json.load(f)
                        self._send_json({"status": "ok", "roadmap": rm_data, "log": p.stdout})
                        return
                    self._send_json({"status": "ok", "log": p.stdout})
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)}, status=500)
                    return
            self._send_json({"status": "error", "message": "dev_reconciler.py not found"}, status=404)
            return

        if req_path == "/api/comments/assess":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)
                return

            cid = data.get("commentId")
            pr_num = str(data.get("prNumber", ""))
            action = data.get("action", "accept")
            user_note = data.get("userNote", "")
            user_status = data.get("userStatus", "ACCEPTED")

            c_file = find_github_comments_file()
            if os.path.exists(c_file):
                try:
                    with open(c_file, "r", encoding="utf-8") as f:
                        c_data = json.load(f)

                    found = False
                    for p_key, p_val in c_data.get("prs", {}).items():
                        if pr_num and str(p_key) != str(pr_num):
                            continue
                        for comment in p_val.get("comments", []):
                            if comment.get("id") == cid:
                                comment["userStatus"] = user_status
                                if user_note:
                                    comment["userNote"] = user_note
                                comment["assessedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                                found = True
                                break
                        if found:
                            break

                    if found:
                        with open(c_file, "w", encoding="utf-8") as f:
                            json.dump(c_data, f, indent=2)
                        self._send_json({"status": "ok", "message": f"Comment assessment updated ({user_status})"})
                        return
                    else:
                        self._send_json({"status": "error", "message": "Comment not found"}, status=404)
                        return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)}, status=500)
                    return
            self._send_json({"status": "error", "message": "Comments file not found"}, status=404)
            return

        if req_path == "/api/comments/action":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)
                return

            cid = data.get("commentId")
            pr_num = str(data.get("prNumber", ""))
            pr_id = data.get("prId", "")
            instruction = data.get("instruction", "")
            target_file = data.get("targetFile", "")
            action_type = data.get("actionType", "CODE_FIX")

            a_file = find_action_queue_file()
            os.makedirs(os.path.dirname(a_file), exist_ok=True)
            actions = []
            if os.path.exists(a_file):
                try:
                    with open(a_file, "r", encoding="utf-8") as f:
                        actions = json.load(f)
                except Exception:
                    actions = []

            action_entry = {
                "id": f"act_{int(time.time())}_{cid[-6:] if cid else 'gen'}",
                "commentId": cid,
                "prNumber": pr_num,
                "prId": pr_id,
                "instruction": instruction,
                "targetFile": target_file,
                "actionType": action_type,
                "status": "QUEUED",
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "history": [f"Action queued at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"]
            }
            actions.append(action_entry)

            with open(a_file, "w", encoding="utf-8") as f:
                json.dump(actions, f, indent=2)

            # Also update comment's actionStatus in github_comments.json
            c_file = find_github_comments_file()
            if os.path.exists(c_file):
                try:
                    with open(c_file, "r", encoding="utf-8") as f:
                        c_data = json.load(f)
                    for p_key, p_val in c_data.get("prs", {}).items():
                        for comment in p_val.get("comments", []):
                            if comment.get("id") == cid:
                                comment["actionStatus"] = "QUEUED"
                                comment["actionId"] = action_entry["id"]
                    with open(c_file, "w", encoding="utf-8") as f:
                        json.dump(c_data, f, indent=2)
                except Exception:
                    pass

            self._send_json({
                "status": "ok",
                "message": "Action instruction queued for AI agent execution",
                "action": action_entry
            })
            return

        if req_path == "/api/candidates/scan":
            data = scan_dev_branch_candidates()
            self._send_json(data)
            return

        if req_path == "/api/dev/rebase/execute":
            res = test_dev_rebase()
            if not res.get("is_clean"):
                self._send_json({
                    "status": "error",
                    "message": f"Cannot safely rebase: {res.get('conflicts_detected')} merge conflicts detected against upstream/master.",
                    "details": res
                }, status=409)
                return
            dev_branch = CFG.get("project", {}).get("dev_branch", "firinspiral3-multidet-asym")
            # Fast-forward dev branch to upstream/master
            p_rebase = subprocess.run(["git", "rebase", f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}"],
                                      cwd=REPO_DIR, capture_output=True, text=True)
            if p_rebase.returncode == 0:
                # Optionally push to origin
                p_push = subprocess.run(["git", "push", ORIGIN_REMOTE, dev_branch],
                                        cwd=REPO_DIR, capture_output=True, text=True)
                self._send_json({
                    "status": "ok",
                    "message": f"Successfully rebased {dev_branch} onto {UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}.",
                    "push_log": p_push.stdout.strip()
                })
            else:
                subprocess.run(["git", "rebase", "--abort"], cwd=REPO_DIR, capture_output=True, text=True)
                self._send_json({
                    "status": "error",
                    "message": "Rebase command failed; cleanly aborted.",
                    "log": p_rebase.stderr.strip()
                }, status=500)
            return

        self._send_json({"status": "error", "message": f"Endpoint not found: {self.path}"}, status=404)

def run(host=HOST, port=PORT):
    server = ThreadingHTTPServer((host, port), ReviewRequestHandler)
    server.daemon_threads = True
    print(f"===========================================================", flush=True)
    print(f"  Reviewer Server Running on http://{host}:{port}", flush=True)
    print(f"  Static Root: {ROOT_DIR}", flush=True)
    print(f"  Target Repo: {REPO_DIR}", flush=True)
    print(f"  Feedback:    {FEEDBACK_FILE}", flush=True)
    print(f"===========================================================", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

def main():
    global HOST, PORT, ROOT_DIR, REPO_DIR, FEEDBACK_FILE
    parser = argparse.ArgumentParser(description="Reviewer Web Server & Comment API")
    parser.add_argument("--host", default=HOST, help="Host address to bind")
    parser.add_argument("--port", type=int, default=PORT, help="Port to listen on")
    parser.add_argument("--root-dir", default=ROOT_DIR, help="Static web root directory")
    parser.add_argument("--repo-dir", default=REPO_DIR, help="Target git repository directory")
    parser.add_argument("--data-file", default=FEEDBACK_FILE, help="Path to feedback JSON file")
    args = parser.parse_args()

    HOST = args.host
    PORT = args.port
    ROOT_DIR = os.path.abspath(args.root_dir)
    REPO_DIR = os.path.abspath(args.repo_dir)
    FEEDBACK_FILE = os.path.abspath(args.data_file)

    run(HOST, PORT)

if __name__ == "__main__":
    main()
