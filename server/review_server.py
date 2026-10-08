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
from http.server import HTTPServer, SimpleHTTPRequestHandler

DEFAULT_PORT = 8080
DEFAULT_HOST = "0.0.0.0"

class DualReviewServer:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, root_dir=None, data_file=None):
        self.host = host
        self.port = port
        self.root_dir = os.path.abspath(root_dir or os.getcwd())
        self.data_file = os.path.abspath(data_file or os.path.join(self.root_dir, "data", "reviewer_feedback.json"))
        self.start_time = time.time()
        
        # Ensure directories exist
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        if not os.path.exists(self.data_file):
            self.save_feedback([])

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
    args = parser.parse_args()

    server = DualReviewServer(
        host=args.host,
        port=args.port,
        root_dir=args.root_dir,
        data_file=args.data_file
    )
    server.run()

if __name__ == "__main__":
    main()
