#!/usr/bin/env python3
"""
Custom review server for PyCBC PR Review Dashboard.
Serves static project files and handles real-time two-way code-review comments API.
"""

import os
import sys
import json
import time
import uuid
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8080
ROOT_DIR = "/home/ahnitz/projects/claude/searchdev"
FEEDBACK_FILE = os.path.join(ROOT_DIR, "search_dev_notes", "reviewer_feedback.json")
TEAMWORK_FEEDBACK_FILE = os.path.join(ROOT_DIR, ".agents", "teamwork", "REVIEWER_FEEDBACK.json")

os.makedirs(os.path.dirname(FEEDBACK_FILE), exist_ok=True)
os.makedirs(os.path.dirname(TEAMWORK_FEEDBACK_FILE), exist_ok=True)

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

import subprocess
import re

PR_BRANCH_MAP = {
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
REPO_DIR = "/home/ahnitz/projects/claude/searchdev/pycbc-work/apogee"
MERGE_BASE = "f6eaed241"

def get_live_pr_hunks(pr_id):
    branch = PR_BRANCH_MAP.get(pr_id.upper())
    if not branch:
        return {"status": "error", "message": f"Unknown PR ID: {pr_id}"}
    
    cmd_sha = ["git", "rev-parse", branch]
    p_sha = subprocess.run(cmd_sha, cwd=REPO_DIR, capture_output=True, text=True)
    commit_sha = p_sha.stdout.strip() if p_sha.returncode == 0 else ""

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
                "githubUrl": f"https://github.com/ahnitz/pycbc/blob/{commit_sha or branch}/{file_name}",
                "localFileUrl": f"file://{REPO_DIR}/{file_name}",
                "rationale": f"Derived from branch {branch}.",
                "alternatives": f"Merge base {MERGE_BASE}."
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
            gh_url = f"https://github.com/ahnitz/pycbc/blob/{commit_sha or branch}/{file_name}{line_hash}"
            local_url = f"file://{REPO_DIR}/{file_name}{line_hash}"

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
        if self.path == "/api/health":
            comments = load_feedback()
            pending = sum(1 for c in comments if c.get("status") != "ADDRESSED")
            self._send_json({
                "status": "healthy",
                "totalComments": len(comments),
                "pendingComments": pending,
                "dataFile": FEEDBACK_FILE,
                "rootDir": ROOT_DIR
            })
            return

        if self.path == "/api/comments" or self.path.startswith("/api/comments?"):
            comments = load_feedback()
            self._send_json({"status": "ok", "comments": comments})
            return

        if self.path.startswith("/api/pr/") and self.path.endswith("/hunks"):
            parts = self.path.split("/")
            pr_id = parts[3].upper()
            data = get_live_pr_hunks(pr_id)
            self._send_json(data)
            return

        super().do_GET()

    def do_POST(self):
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
                "author": data.get("author", "Maintainer (Alex)"),
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
                        "author": data.get("author", "Antigravity AI Team"),
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

        super().do_POST()

def run():
    server = HTTPServer(("0.0.0.0", PORT), ReviewRequestHandler)
    print(f"Review server running on http://0.0.0.0:{PORT} (Serving {ROOT_DIR})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    run()
