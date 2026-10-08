#!/usr/bin/env python3
"""
Dual-Direction Review Server
============================
High-performance, zero-dependency Python HTTP server and REST API for real-time
human-agent code review workflows.

Supports:
- Serving the reactive review dashboard UI
- Bi-directional REST API for inline code comments and agent replies
- Atomic feedback ledger synchronization
- Healthcheck and status diagnostics
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

DEFAULT_PORT = 8080
DEFAULT_HOST = "0.0.0.0"

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

class DualReviewServer:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, root_dir=None, data_file=None, repo_dir=None, merge_base=None, config_file=None):
        self.host = host
        self.port = port
        self.root_dir = os.path.abspath(root_dir or os.getcwd())
        
        # Load configuration if available
        config_path = config_file or os.path.join(self.root_dir, "config.json")
        self.config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception as e:
                print(f"[ReviewServer WARNING] Could not parse config file: {e}", file=sys.stderr)

        data_default = self.config.get("storage", {}).get("feedback_file", "data/reviewer_feedback.json")
        self.data_file = os.path.abspath(data_file or os.path.join(self.root_dir, data_default))
        self.repo_dir = repo_dir or self.config.get("project", {}).get("working_tree_path", "/home/ahnitz/projects/claude/searchdev/pycbc-work/apogee")
        self.merge_base = merge_base or self.config.get("project", {}).get("target_merge_base", "f6eaed241")
        self.branch_map = self.config.get("branch_map", PR_BRANCH_MAP)
        self.start_time = time.time()
        
        # Ensure directories exist
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        if not os.path.exists(self.data_file):
            self.save_feedback([])

    def get_live_pr_hunks(self, pr_id):
        branch = self.branch_map.get(pr_id.upper())
        if not branch:
            return {"status": "error", "message": f"Unknown PR ID: {pr_id}"}
        
        repo_dir = self.repo_dir
        if not repo_dir or not os.path.exists(repo_dir):
            return {"status": "error", "message": f"Repo dir not found: {repo_dir}"}

        cmd_sha = ["git", "rev-parse", branch]
        p_sha = subprocess.run(cmd_sha, cwd=repo_dir, capture_output=True, text=True)
        commit_sha = p_sha.stdout.strip() if p_sha.returncode == 0 else ""

        cmd_stat = ["git", "diff", "--shortstat", f"{self.merge_base}..{branch}"]
        p_stat = subprocess.run(cmd_stat, cwd=repo_dir, capture_output=True, text=True)
        diff_stat = p_stat.stdout.strip() if p_stat.returncode == 0 else ""

        cmd_diff = ["git", "diff", "-U3", f"{self.merge_base}..{branch}"]
        p_diff = subprocess.run(cmd_diff, cwd=repo_dir, capture_output=True, text=True)
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
            
            body_lines = [l for l in lines[1:] if not (l.startswith('index ') or l.startswith('--- ') or l.startswith('+++ ') or l.startswith('new file mode'))]
            snippet = '\n'.join(body_lines[:35])
            if len(body_lines) > 35:
                snippet += f'\n... (+{len(body_lines) - 35} more lines in diff)'

            if is_new and is_test:
                summary = "Newly added unit test suite"
                rationale = "Created in response to maintainer review feedback to comprehensively test new behavior and invariant guarantees."
            elif is_test:
                summary = "Updated unit test coverage"
                rationale = "Extended unit test assertions to validate fixes requested in review."
            elif is_new:
                summary = "Newly added module"
                rationale = "Added module implementing required functionality."
            else:
                summary = "Core implementation update"
                rationale = "Implementation refactoring and fixes applied to topic branch."

            hunks.append({
                "file": file_name,
                "lines": f"{len(body_lines)} lines in diff",
                "shortSummary": summary,
                "diffSnippet": snippet,
                "rationale": rationale,
                "alternatives": f"Derived directly from git diff against upstream master merge base ({self.merge_base})."
            })

        return {
            "status": "ok",
            "prId": pr_id.upper(),
            "branch": branch,
            "commitSha": commit_sha,
            "diffStat": diff_stat,
            "hunks": hunks
        }

    def load_feedback(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[ReviewServer ERROR] Failed reading feedback file: {e}", file=sys.stderr)
                return []
        return []

    def save_feedback(self, data):
        tmp_file = f"{self.data_file}.tmp.{os.getpid()}"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_file, self.data_file)
        except Exception as e:
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
            print(f"[ReviewServer ERROR] Failed saving feedback file: {e}", file=sys.stderr)

    def create_handler(self):
        server_instance = self

        class RequestHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=server_instance.root_dir, **kwargs)

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
                # Root redirect to review dashboard
                if self.path == "/" or self.path == "":
                    dash_candidates = [
                        "pr_review_dashboard.html",
                        "dashboard/pr_review_dashboard.html",
                        "search_dev_notes/pr_review_dashboard.html"
                    ]
                    for cand in dash_candidates:
                        full_cand = os.path.join(server_instance.root_dir, cand)
                        if os.path.exists(full_cand):
                            self.send_response(302)
                            self.send_header("Location", f"/{cand}")
                            self.end_headers()
                            return

                # Health API
                if self.path == "/api/health":
                    comments = server_instance.load_feedback()
                    pending = sum(1 for c in comments if c.get("status") != "ADDRESSED")
                    self._send_json({
                        "status": "healthy",
                        "uptimeSeconds": round(time.time() - server_instance.start_time, 1),
                        "totalComments": len(comments),
                        "pendingComments": pending,
                        "dataFile": server_instance.data_file,
                        "rootDir": server_instance.root_dir
                    })
                    return

                # Comments list API
                if self.path == "/api/comments" or self.path.startswith("/api/comments?"):
                    comments = server_instance.load_feedback()
                    self._send_json({"status": "ok", "comments": comments})
                    return

                # Dynamic PR hunks endpoint
                if self.path.startswith("/api/pr/") and self.path.endswith("/hunks"):
                    parts = self.path.split("/")
                    pr_id = parts[3].upper()
                    data = server_instance.get_live_pr_hunks(pr_id)
                    self._send_json(data)
                    return

                # Default static file handler
                super().do_GET()

            def do_POST(self):
                # New comment endpoint
                if self.path == "/api/comments":
                    content_length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(content_length)
                    try:
                        data = json.loads(body.decode("utf-8"))
                    except Exception as e:
                        self._send_json({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)
                        return

                    comment_text = data.get("commentText", "").strip()
                    if not comment_text:
                        self._send_json({"status": "error", "message": "Empty commentText"}, status=400)
                        return

                    comment_id = "c_" + str(int(time.time())) + "_" + uuid.uuid4().hex[:6]
                    comment_record = {
                        "id": comment_id,
                        "prId": data.get("prId", ""),
                        "hunkIndex": data.get("hunkIndex", -1),
                        "file": data.get("file", "PR-wide"),
                        "lines": data.get("lines", "General"),
                        "commentText": comment_text,
                        "author": data.get("author", "Maintainer"),
                        "status": "PENDING_AGENT_ACTION",
                        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "timestamp": time.time(),
                        "replies": []
                    }

                    comments = server_instance.load_feedback()
                    comments.append(comment_record)
                    server_instance.save_feedback(comments)

                    print(f"[ReviewServer] New review comment on {comment_record['prId']}: {comment_text[:60]}...", flush=True)
                    self._send_json({"status": "ok", "comment": comment_record}, status=201)
                    return

                # Agent reply endpoint
                if self.path == "/api/comments/reply":
                    content_length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(content_length)
                    try:
                        data = json.loads(body.decode("utf-8"))
                    except Exception as e:
                        self._send_json({"status": "error", "message": f"Invalid JSON: {e}"}, status=400)
                        return

                    comment_id = data.get("commentId")
                    reply_text = data.get("replyText", "").strip()
                    new_status = data.get("status", "ADDRESSED")

                    if not comment_id:
                        self._send_json({"status": "error", "message": "Missing commentId"}, status=400)
                        return

                    comments = server_instance.load_feedback()
                    found = False
                    for c in comments:
                        if c.get("id") == comment_id:
                            reply_entry = {
                                "id": "r_" + str(int(time.time())) + "_" + uuid.uuid4().hex[:4],
                                "author": data.get("author", "Antigravity AI Team"),
                                "replyText": reply_text,
                                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                            }
                            if "commitSha" in data:
                                reply_entry["commitSha"] = data["commitSha"]
                            if "diffUrl" in data:
                                reply_entry["diffUrl"] = data["diffUrl"]

                            c.setdefault("replies", []).append(reply_entry)
                            c["status"] = new_status
                            c["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                            found = True
                            break

                    if found:
                        server_instance.save_feedback(comments)
                        print(f"[ReviewServer] Agent replied to comment {comment_id}: status={new_status}", flush=True)
                        self._send_json({"status": "ok", "message": "Reply saved"})
                    else:
                        self._send_json({"status": "error", "message": f"Comment ID '{comment_id}' not found"}, status=404)
                    return

                super().do_POST()

        return RequestHandler

    def run(self):
        handler_class = self.create_handler()
        server = HTTPServer((self.host, self.port), handler_class)
        print(f"===========================================================", flush=True)
        print(f"  Dual-Direction Review Server Started", flush=True)
        print(f"  Address:   http://{self.host}:{self.port}", flush=True)
        print(f"  Root Dir:  {self.root_dir}", flush=True)
        print(f"  Data File: {self.data_file}", flush=True)
        print(f"===========================================================", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down review server...", flush=True)
        finally:
            server.server_close()

def main():
    parser = argparse.ArgumentParser(description="Dual-Direction Review Server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)), help="Port to listen on")
    parser.add_argument("--host", default=os.environ.get("HOST", DEFAULT_HOST), help="Host interface to bind to")
    parser.add_argument("--root-dir", default=os.environ.get("REVIEW_ROOT_DIR"), help="Directory containing dashboard files")
    parser.add_argument("--data-file", default=os.environ.get("REVIEW_DATA_FILE"), help="Path to reviewer_feedback.json")
    parser.add_argument("--repo-dir", default=os.environ.get("REVIEW_REPO_DIR"), help="Path to git repository working tree")
    parser.add_argument("--merge-base", default=os.environ.get("REVIEW_MERGE_BASE"), help="Merge base commit SHA")
    parser.add_argument("--config", default=os.environ.get("REVIEW_CONFIG_FILE"), help="Path to config.json")
    args = parser.parse_args()

    server = DualReviewServer(
        host=args.host,
        port=args.port,
        root_dir=args.root_dir,
        data_file=args.data_file,
        repo_dir=args.repo_dir,
        merge_base=args.merge_base,
        config_file=args.config
    )
    server.run()

if __name__ == "__main__":
    main()
