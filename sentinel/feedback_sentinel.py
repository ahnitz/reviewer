#!/usr/bin/env python3
"""
Feedback Sentinel Daemon
========================
Monitors the review feedback ledger for incoming maintainer comments requiring agent action.
Can run as a continuous background daemon or one-shot cron worker.
"""

import os
import sys
import json
import time
import argparse
import subprocess
import urllib.request
import urllib.error

DEFAULT_DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "reviewer_feedback.json")
DEFAULT_API_URL = "http://localhost:8080/api/comments"

class FeedbackSentinel:
    def __init__(self, data_file=None, api_url=DEFAULT_API_URL, repo_dir=None, interval=5):
        self.data_file = os.path.abspath(data_file or DEFAULT_DATA_FILE)
        self.api_url = api_url
        self.repo_dir = os.path.abspath(repo_dir) if repo_dir else None
        self.interval = interval
        self.seen_comment_ids = set()

    def get_comments_from_api(self):
        try:
            req = urllib.request.Request(self.api_url, headers={"User-Agent": "FeedbackSentinel/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("comments", [])
        except Exception:
            return None

    def get_comments_from_file(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[Sentinel ERROR] Failed reading {self.data_file}: {e}", file=sys.stderr)
        return []

    def get_comments(self):
        # Prefer local file if available, otherwise fallback to API
        if os.path.exists(self.data_file):
            return self.get_comments_from_file()
        api_res = self.get_comments_from_api()
        if api_res is not None:
            return api_res
        return []

    def post_reply_to_api(self, comment_id, reply_text, status="ADDRESSED", commit_sha=None):
        payload = {
            "commentId": comment_id,
            "replyText": reply_text,
            "status": status,
            "author": "Antigravity AI Responder"
        }
        if commit_sha:
            payload["commitSha"] = commit_sha

        body = json.dumps(payload).encode("utf-8")
        reply_url = self.api_url.replace("/comments", "/comments/reply")
        try:
            req = urllib.request.Request(
                reply_url,
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": "FeedbackSentinel/1.0"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status in (200, 201)
        except Exception as e:
            print(f"[Sentinel ERROR] Failed sending reply via API: {e}", file=sys.stderr)
            return False

    def check_pending(self):
        comments = self.get_comments()
        pending = [c for c in comments if c.get("status") == "PENDING_AGENT_ACTION"]
        
        new_pending = []
        for c in pending:
            cid = c.get("id")
            if cid not in self.seen_comment_ids:
                self.seen_comment_ids.add(cid)
                new_pending.append(c)

        return pending, new_pending

    def print_comment_alert(self, c):
        print("\n" + "="*70, flush=True)
        print(f"🚨 [NEW MAINTAINER FEEDBACK] ID: {c.get('id')} | PR: {c.get('prId')}", flush=True)
        print(f"   Target: {c.get('file')} ({c.get('lines')})", flush=True)
        print(f"   Author: {c.get('author')} at {c.get('createdAt')}", flush=True)
        print(f"   Comment: \"{c.get('commentText')}\"", flush=True)
        print("="*70 + "\n", flush=True)

    def run_daemon(self):
        print(f"[Sentinel] Monitoring for maintainer feedback every {self.interval}s...", flush=True)
        print(f"   Ledger file: {self.data_file}", flush=True)
        print(f"   API target:  {self.api_url}", flush=True)
        
        try:
            while True:
                pending, new_pending = self.check_pending()
                for c in new_pending:
                    self.print_comment_alert(c)
                time.sleep(self.interval)
        except KeyboardInterrupt:
            print("\n[Sentinel] Stopped by user.", flush=True)

    def run_once(self):
        pending, _ = self.check_pending()
        if pending:
            print(f"[Sentinel] Found {len(pending)} pending comment(s):")
            for c in pending:
                print(f" - [{c.get('prId')}] {c.get('id')}: {c.get('commentText')[:60]}...")
            return len(pending)
        else:
            print("[Sentinel] 0 pending comments. All feedback addressed.")
            return 0

def main():
    parser = argparse.ArgumentParser(description="Review Feedback Sentinel")
    parser.add_argument("--data-file", default=os.environ.get("REVIEW_DATA_FILE"), help="Path to feedback JSON")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="URL of comments API")
    parser.add_argument("--repo-dir", default=os.environ.get("PROJECT_REPO_DIR"), help="Git working directory")
    parser.add_argument("--interval", type=int, default=5, help="Polling interval in seconds")
    parser.add_argument("--check-once", action="store_true", help="Run a single check and exit")
    args = parser.parse_args()

    sentinel = FeedbackSentinel(
        data_file=args.data_file,
        api_url=args.api_url,
        repo_dir=args.repo_dir,
        interval=args.interval
    )

    if args.check_once:
        sys.exit(sentinel.run_once())
    else:
        sentinel.run_daemon()

if __name__ == "__main__":
    main()
