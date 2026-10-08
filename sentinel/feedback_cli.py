#!/usr/bin/env python3
"""
Feedback CLI
============
Command-line utility for developers and agents to query, inspect, add, and reply
to review feedback.
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.error

DEFAULT_API_URL = "http://localhost:8080/api/comments"

def fetch_comments(api_url):
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "FeedbackCLI/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("comments", [])
    except Exception as e:
        print(f"Error connecting to review server at {api_url}: {e}", file=sys.stderr)
        sys.exit(1)

def cmd_list(args):
    comments = fetch_comments(args.api_url)
    if args.pr:
        comments = [c for c in comments if c.get("prId") == args.pr]
    if args.pending_only:
        comments = [c for c in comments if c.get("status") != "ADDRESSED"]

    if not comments:
        print("No matching comments found.")
        return

    print(f"\n{'ID':<20} {'PR':<8} {'STATUS':<24} {'FILE & LINES':<30} {'AUTHOR':<15}")
    print("-" * 100)
    for c in comments:
        cid = c.get("id", "")
        pr = c.get("prId", "")
        st = c.get("status", "PENDING_AGENT_ACTION")
        target = f"{c.get('file', '')}:{c.get('lines', '')}"[:29]
        auth = c.get("author", "Maintainer")[:14]
        print(f"{cid:<20} {pr:<8} {st:<24} {target:<30} {auth:<15}")
        print(f"  > Text: {c.get('commentText', '').strip()}")
        replies = c.get("replies", [])
        if replies:
            for r in replies:
                print(f"    - Reply by {r.get('author')}: {r.get('replyText', '').strip()}")
        print()

def cmd_reply(args):
    reply_url = args.api_url.replace("/comments", "/comments/reply")
    payload = {
        "commentId": args.comment_id,
        "replyText": args.message,
        "status": args.status,
        "author": args.author
    }
    if args.commit:
        payload["commitSha"] = args.commit

    body = json.dumps(payload).encode("utf-8")
    try:
        req = urllib.request.Request(
            reply_url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "FeedbackCLI/1.0"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            print(f"Success: {res_data.get('message', 'Reply posted')}")
    except Exception as e:
        print(f"Error posting reply: {e}", file=sys.stderr)
        sys.exit(1)

def cmd_add(args):
    payload = {
        "prId": args.pr,
        "hunkIndex": args.hunk,
        "file": args.file or "General",
        "lines": args.lines or "General",
        "commentText": args.message,
        "author": args.author
    }
    body = json.dumps(payload).encode("utf-8")
    try:
        req = urllib.request.Request(
            args.api_url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "FeedbackCLI/1.0"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            cid = res_data.get("comment", {}).get("id")
            print(f"Success: Comment created with ID {cid}")
    except Exception as e:
        print(f"Error adding comment: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Review Feedback CLI")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="URL of comments API")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser("list", help="List review comments")
    p_list.add_argument("--pr", help="Filter by PR ID (e.g. PR-1A)")
    p_list.add_argument("--pending-only", action="store_true", help="Show only unresolved comments")
    p_list.set_defaults(func=cmd_list)

    # reply
    p_reply = subparsers.add_parser("reply", help="Reply to a comment")
    p_reply.add_argument("comment_id", help="Comment ID to reply to")
    p_reply.add_argument("--message", "-m", required=True, help="Reply explanation text")
    p_reply.add_argument("--status", default="ADDRESSED", help="New status (default: ADDRESSED)")
    p_reply.add_argument("--author", default="Antigravity AI Responder", help="Author name")
    p_reply.add_argument("--commit", help="Associated commit SHA")
    p_reply.set_defaults(func=cmd_reply)

    # add
    p_add = subparsers.add_parser("add", help="Add a new comment")
    p_add.add_argument("--pr", required=True, help="PR ID")
    p_add.add_argument("--message", "-m", required=True, help="Comment text")
    p_add.add_argument("--hunk", type=int, default=-1, help="Hunk index (-1 for PR-level)")
    p_add.add_argument("--file", help="File name")
    p_add.add_argument("--lines", help="Lines description")
    p_add.add_argument("--author", default="Maintainer", help="Author name")
    p_add.set_defaults(func=cmd_add)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
