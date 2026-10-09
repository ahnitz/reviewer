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
from http.server import HTTPServer, SimpleHTTPRequestHandler

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))

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
    "PR-1A": "pr-fix-core-numpy2-optparse",
    "PR-1B": "pr-fix-io-dictarray-indexing",
    "PR-1C": "pr-fix-events-numpy2-zerolag",
    "PR-1D": "pr-perf-waveform-compress",
    "PR-1E": "pr-feat-vetoes-chisq-slicing",
    "PR-1F": "pr-feat-events-eventmgr-multi",
    "PR-1G": "pr-feat-filter-dynamic-snr-renorm",
    "PR-1H": "pr-feat-inject-injfilter-optimal-snr",
    "PR-1I": "pr-feat-psd-robust-estimators",
    "PR-1J": "pr-feat-strain-regularized-inpainting",
}

PR_BRANCH_MAP = dict(CFG.get("project", {}).get("branch_map", DEFAULT_PR_BRANCH_MAP))
USER_REQUESTED_BRANCHES = list(CFG.get("project", {}).get("user_requested_branches", []))

def resolve_branch(pr_id):
    if not pr_id:
        return None
    # 1. Exact match in configured PR_BRANCH_MAP
    if pr_id.upper() in PR_BRANCH_MAP:
        return PR_BRANCH_MAP[pr_id.upper()]
    # 2. Check in USER_REQUESTED_BRANCHES
    for ub in USER_REQUESTED_BRANCHES:
        if isinstance(ub, dict):
            if ub.get("prId", "").upper() == pr_id.upper():
                return ub.get("branch")
        elif str(ub).upper() == pr_id.upper():
            return str(ub)
    # 3. Check if pr_id matches a local git branch directly
    p = subprocess.run(["git", "rev-parse", "--verify", pr_id], cwd=REPO_DIR, capture_output=True, text=True)
    if p.returncode == 0:
        return pr_id
    # 4. Check common naming variations
    for candidate in [pr_id.lower(), f"pr-{pr_id.lower()}", f"pr_{pr_id.lower()}", f"pr/{pr_id.lower()}"]:
        p = subprocess.run(["git", "rev-parse", "--verify", candidate], cwd=REPO_DIR, capture_output=True, text=True)
        if p.returncode == 0:
            return candidate
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

def get_live_pr_hunks(branch_or_id):
    branch = resolve_branch(branch_or_id) or branch_or_id
    p_check = subprocess.run(["git", "rev-parse", "--verify", branch], cwd=REPO_DIR, capture_output=True, text=True)
    if p_check.returncode != 0:
        return {"status": "error", "message": f"Unknown PR ID or branch: {branch_or_id}"}

    inv_map = {v: k for k, v in PR_BRANCH_MAP.items()}
    pr_id = inv_map.get(branch) or (branch_or_id if branch_or_id.startswith("PR-") else f"PR-{branch_or_id}").upper().replace('/', '-')
    
    cmd_sha = ["git", "rev-parse", branch]
    p_sha = subprocess.run(cmd_sha, cwd=REPO_DIR, capture_output=True, text=True)
    commit_sha = p_sha.stdout.strip() if p_sha.returncode == 0 else ""

    p_mb = subprocess.run(["git", "merge-base", f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}", branch], cwd=REPO_DIR, capture_output=True, text=True)
    if p_mb.returncode != 0 or not p_mb.stdout.strip():
        p_mb = subprocess.run(["git", "merge-base", "upstream/master", branch], cwd=REPO_DIR, capture_output=True, text=True)
    if p_mb.returncode != 0 or not p_mb.stdout.strip():
        p_mb = subprocess.run(["git", "merge-base", "master", branch], cwd=REPO_DIR, capture_output=True, text=True)
    base_commit = p_mb.stdout.strip() if p_mb.returncode == 0 and p_mb.stdout.strip() else MERGE_BASE

    cmd_stat = ["git", "diff", "--shortstat", f"{base_commit}..{branch}"]
    p_stat = subprocess.run(cmd_stat, cwd=REPO_DIR, capture_output=True, text=True)
    diff_stat = p_stat.stdout.strip() if p_stat.returncode == 0 else ""

    cmd_diff = ["git", "diff", "-U3", f"{base_commit}..{branch}"]
    p_diff = subprocess.run(cmd_diff, cwd=REPO_DIR, capture_output=True, text=True)
    if p_diff.returncode != 0:
        return {"status": "error", "message": f"Failed running git diff: {p_diff.stderr}"}

    diff_text = p_diff.stdout
    file_blocks = re.split(r'\ndiff --git a/', '\n' + diff_text)
    hunks = []

    for block in file_blocks:
        if not block.strip():
            continue
        lines = block.splitlines()
        first_line = lines[0]
        file_name = first_line.split(' b/')[0] if ' b/' in first_line else first_line.split()[0]
        
        is_new = any('new file mode' in l for l in lines[:5])
        is_test = file_name.startswith('test/') or 'test_' in file_name
        
        # Split by @@ hunk headers
        hunk_splits = re.split(r'(@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@.*)', block)
        if len(hunk_splits) <= 1:
            body_lines = [l for l in lines[1:] if not (l.startswith('index ') or l.startswith('--- ') or l.startswith('+++ ') or l.startswith('new file mode'))]
            snippet = '\n'.join(body_lines[:35])
            hunks.append({
                "file": file_name,
                "startLine": 1,
                "endLine": len(body_lines),
                "lines": f"lines 1-{len(body_lines)}",
                "shortSummary": f"File modification: {file_name}",
                "diffSnippet": snippet,
                "githubUrl": f"https://github.com/{MAINTAINER_FORK}/blob/{commit_sha or branch}/{file_name}",
                "localFileUrl": f"file://{REPO_DIR}/{file_name}",
                "vscodeUrl": f"vscode://file/{REPO_DIR}/{file_name}:1",
                "filePathLine": f"{file_name}:1",
                "rationale": f"Derived from branch {branch}.",
                "alternatives": f"Targeting upstream merge base ({base_commit[:9]})."
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
            func_ctx = m.group(5).strip()
            
            body_lines = [l for l in (header + '\n' + body).splitlines() if not (l.startswith('index ') or l.startswith('--- ') or l.startswith('+++ ') or l.startswith('new file mode'))]
            snippet = '\n'.join(body_lines[:40])
            if len(body_lines) > 40:
                snippet += f'\n... (+{len(body_lines) - 40} more lines in hunk)'

            line_str = f"lines {start_line}-{end_line}" if end_line > start_line else f"line {start_line}"
            line_hash = f"#L{start_line}-L{end_line}" if end_line > start_line else f"#L{start_line}"
            gh_url = f"https://github.com/{MAINTAINER_FORK}/blob/{commit_sha or branch}/{file_name}{line_hash}"
            local_url = f"file://{REPO_DIR}/{file_name}{line_hash}"
            vscode_url = f"vscode://file/{REPO_DIR}/{file_name}:{start_line}"
            path_line = f"{file_name}:{start_line}"

            if is_new and is_test:
                summary = f"New test suite: {file_name}"
                rationale = "Created in response to maintainer review feedback to comprehensively test invariant guarantees."
            elif is_test:
                summary = f"Unit test verification ({file_name})"
                rationale = "Extended unit test assertions to validate fixes requested in review."
            elif is_new:
                summary = f"New module: {file_name}"
                rationale = "Added module implementing required functionality."
            elif func_ctx:
                summary = f"{func_ctx}"
                rationale = f"Implementation update around {func_ctx}."
            else:
                summary = "Core implementation update"
                rationale = "Implementation refactoring and fixes applied to topic branch."

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
                "alternatives": f"Targeting upstream merge base ({base_commit[:9]})."
            })

    return {
        "status": "ok",
        "prId": pr_id.upper(),
        "branch": branch,
        "commitSha": commit_sha,
        "diffStat": diff_stat,
        "hunks": hunks
    }

class ReviewRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT_DIR, **kwargs)

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

    def do_GET(self):
        # Auto-redirect root to dashboard if index.html is absent or user requests dashboard
        if self.path in ("/", ""):
            dashboard_file = os.path.join(ROOT_DIR, "dashboard", "pr_review_dashboard.html")
            if not os.path.exists(dashboard_file):
                dashboard_file = os.path.join(ROOT_DIR, "pr_review_dashboard.html")
            if os.path.exists(dashboard_file) and not os.path.exists(os.path.join(ROOT_DIR, "index.html")):
                self.send_response(302)
                target_url = "/dashboard/pr_review_dashboard.html" if os.path.exists(os.path.join(ROOT_DIR, "dashboard", "pr_review_dashboard.html")) else "/pr_review_dashboard.html"
                self.send_header("Location", target_url)
                self.end_headers()
                return

        if self.path == "/api/health":
            comments = load_feedback()
            pending = sum(1 for c in comments if c.get("status") != "ADDRESSED")
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

        if self.path == "/api/comments" or self.path.startswith("/api/comments?"):
            comments = load_feedback()
            self._send_json({"status": "ok", "comments": comments})
            return

        if self.path == "/api/prs" or self.path.startswith("/api/prs?"):
            results = {}
            tracked = dict(PR_BRANCH_MAP)

            # Include user-requested branches explicitly
            for ub in USER_REQUESTED_BRANCHES:
                if isinstance(ub, dict):
                    pid = ub.get("prId") or ub.get("branch", "").upper().replace("/", "-")
                    bname = ub.get("branch")
                else:
                    pid = str(ub).upper().replace("/", "-")
                    bname = str(ub)
                if not pid.startswith("PR-"):
                    pid = f"PR-{pid}"
                if bname and bname not in tracked.values():
                    tracked[pid] = bname

            # Include any synthesized candidates from wave_roadmap.json if marked as TRACKED
            rm_file = find_roadmap_file()
            if os.path.exists(rm_file):
                try:
                    with open(rm_file, "r", encoding="utf-8") as f:
                        rm_data = json.load(f)
                    for pid, pdata in rm_data.get("prs", {}).items():
                        if pdata.get("status") in ("PUSHED_TO_ORIGIN", "ACTIVE", "TRACKED") and pdata.get("branch"):
                            bname = pdata["branch"]
                            if bname not in tracked.values():
                                tracked[pid] = bname
                except Exception:
                    pass

            for pid, branch in tracked.items():
                results[pid] = get_live_pr_hunks(branch)
            self._send_json({"status": "ok", "prs": results})
            return

        if self.path == "/api/roadmap" or self.path.startswith("/api/roadmap?"):
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

        if self.path == "/api/dev/status" or self.path.startswith("/api/dev/status?"):
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

        if self.path == "/api/branches" or self.path.startswith("/api/branches?"):
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

        if self.path == "/api/rebase/status" or self.path.startswith("/api/rebase/status?"):
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

        if self.path.startswith("/api/pr/") and self.path.endswith("/hunks"):
            parts = self.path.split("/")
            pr_id = parts[3].upper()
            data = get_live_pr_hunks(pr_id)
            self._send_json(data)
            return

        super().do_GET()

    def do_POST(self):
        global USER_REQUESTED_BRANCHES
        if self.path == "/api/rebase/sync":
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

        if self.path == "/api/comments":
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

        if self.path == "/api/comments/reply":
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

            comments = load_feedback()
            found = False
            for c in comments:
                if c["id"] == comment_id:
                    c.setdefault("replies", []).append({
                        "id": "r_" + str(int(time.time())) + "_" + uuid.uuid4().hex[:4],
                        "author": data.get("author", "AI Review Responder"),
                        "replyText": reply_text,
                        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    })
                    c["status"] = new_status
                    found = True
                    break

            if found:
                save_feedback(comments)
                self._send_json({"status": "ok", "message": "Reply saved"})
            else:
                self._send_json({"status": "error", "message": "Comment ID not found"}, status=404)
            return

        if self.path == "/api/prs/track":
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

        if self.path == "/api/prs/untrack":
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

        if self.path == "/api/dev/reconcile":
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

        super().do_POST()

def run(host=HOST, port=PORT):
    server = HTTPServer((host, port), ReviewRequestHandler)
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
