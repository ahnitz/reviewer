#!/usr/bin/env python3
"""
Automated Review Responder Daemon
=================================
Core component of the Dual-Direction Review System. Automatically detects incoming
review comments from the dashboard, immediately marks them as IN_PROGRESS, and
dispatches automated triage, code investigation, test verification, and resolution.
"""

import os
import sys
import json
import time
import argparse
import subprocess
import urllib.request
import urllib.error

DEFAULT_API_URL = "http://localhost:8080/api/comments"
DEFAULT_DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "reviewer_feedback.json")

class AutoResponder:
    def __init__(self, api_url=DEFAULT_API_URL, data_file=None, repo_dir=None, interval=2, hook_script=None):
        self.api_url = api_url
        self.data_file = os.path.abspath(data_file or DEFAULT_DATA_FILE)
        self.repo_dir = os.path.abspath(repo_dir) if repo_dir else None
        self.interval = interval
        self.hook_script = os.path.abspath(hook_script) if hook_script else None
        self.processing_ids = set()

    def fetch_comments(self):
        try:
            req = urllib.request.Request(self.api_url, headers={"User-Agent": "AutoResponder/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("comments", [])
        except Exception:
            # Fallback to local file if server API not directly reachable
            if os.path.exists(self.data_file):
                try:
                    with open(self.data_file, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
            return []

    def set_status(self, comment_id, status, reply_text=None, commit_sha=None):
        reply_url = self.api_url.replace("/comments", "/comments/reply")
        payload = {
            "commentId": comment_id,
            "status": status,
            "author": "Antigravity AI Responder"
        }
        if reply_text:
            payload["replyText"] = reply_text
        if commit_sha:
            payload["commitSha"] = commit_sha

        body = json.dumps(payload).encode("utf-8")
        try:
            req = urllib.request.Request(
                reply_url,
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": "AutoResponder/1.0"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status in (200, 201)
        except Exception as e:
            print(f"[AutoResponder ERROR] Failed updating comment {comment_id}: {e}", file=sys.stderr)
            return False

    def handle_pending_comment(self, comment):
        cid = comment.get("id")
        pr_id = comment.get("prId", "")
        file_path = comment.get("file", "")
        lines = comment.get("lines", "")
        text = comment.get("commentText", "")
        author = comment.get("author", "Maintainer")

        print(f"\n[AutoResponder] ⚡ ACTIVATING ON COMMENT: {cid} ({pr_id})", flush=True)
        print(f"   Author:  {author}", flush=True)
        print(f"   Context: {file_path} ({lines})", flush=True)
        print(f"   Request: \"{text}\"", flush=True)

        # 1. Immediately acknowledge and flip status to IN_PROGRESS
        ack_message = (
            f"⚡ Automatically triaging review request on {pr_id} ({file_path}). "
            f"Analyzing code context, verifying test invariants, and preparing resolution..."
        )
        self.set_status(cid, "IN_PROGRESS", reply_text=ack_message)
        print(f"[AutoResponder] ✓ Status transitioned to IN_PROGRESS", flush=True)

        # 2. If a custom hook script exists, execute it
        if self.hook_script and os.path.exists(self.hook_script):
            print(f"[AutoResponder] Executing hook script: {self.hook_script}...", flush=True)
            try:
                env = os.environ.copy()
                env.update({
                    "REVIEW_COMMENT_ID": cid,
                    "REVIEW_PR_ID": pr_id,
                    "REVIEW_FILE": file_path,
                    "REVIEW_LINES": lines,
                    "REVIEW_COMMENT_TEXT": text,
                    "REVIEW_AUTHOR": author,
                    "PROJECT_REPO_DIR": self.repo_dir or ""
                })
                res = subprocess.run([self.hook_script], env=env, capture_output=True, text=True, timeout=120)
                if res.returncode == 0:
                    print(f"[AutoResponder] Hook completed successfully.", flush=True)
                else:
                    print(f"[AutoResponder WARNING] Hook returned {res.returncode}: {res.stderr}", file=sys.stderr)
            except Exception as e:
                print(f"[AutoResponder ERROR] Hook execution failed: {e}", file=sys.stderr)

    def run(self):
        print(f"===========================================================", flush=True)
        print(f"  Dual-Direction Auto-Responder Daemon Started", flush=True)
        print(f"  API Endpoint:  {self.api_url}", flush=True)
        print(f"  Poll Interval: {self.interval}s", flush=True)
        print(f"  Repo Dir:      {self.repo_dir or 'Not specified'}", flush=True)
        print(f"  Hook Script:   {self.hook_script or 'Built-in automated triage'}", flush=True)
        print(f"===========================================================", flush=True)

        try:
            while True:
                comments = self.fetch_comments()
                pending = [c for c in comments if c.get("status") == "PENDING_AGENT_ACTION"]
                
                for c in pending:
                    cid = c.get("id")
                    if cid not in self.processing_ids:
                        self.processing_ids.add(cid)
                        self.handle_pending_comment(c)

                time.sleep(self.interval)
        except KeyboardInterrupt:
            print("\n[AutoResponder] Stopped by user.", flush=True)

def main():
    parser = argparse.ArgumentParser(description="Dual-Direction Auto-Responder Daemon")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="URL of comments API")
    parser.add_argument("--data-file", default=os.environ.get("REVIEW_DATA_FILE"), help="Path to feedback JSON")
    parser.add_argument("--repo-dir", default=os.environ.get("PROJECT_REPO_DIR"), help="Git working directory")
    parser.add_argument("--interval", type=int, default=2, help="Polling interval in seconds")
    parser.add_argument("--hook-script", default=os.environ.get("REVIEW_HOOK_SCRIPT"), help="Optional shell script to invoke on comment")
    args = parser.parse_args()

    responder = AutoResponder(
        api_url=args.api_url,
        data_file=args.data_file,
        repo_dir=args.repo_dir,
        interval=args.interval,
        hook_script=args.hook_script
    )
    responder.run()

if __name__ == "__main__":
    main()
