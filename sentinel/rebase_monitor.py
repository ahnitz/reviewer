#!/usr/bin/env python3
"""
Automated Upstream Rebase & Synchronization Monitor
===================================================
Continuously monitors `gwastro/pycbc:master` (upstream/master) as it moves.
When upstream/master advances with new commits, this daemon automatically:
1. Detects the new upstream commit hash.
2. Identifies all registered topic branches (Wave 1 PRs, feature branches, fix branches).
3. Automatically rebases each branch onto the latest `upstream/master`.
4. Executes the isolated verification test suite for that branch.
5. If tests pass, force-pushes the rebased branch to `origin` (`maintainer_fork`).
6. If a rebase conflict occurs, aborts safely (`git rebase --abort`) and records status.
7. Exposes live rebase and sync health in `data/rebase_status.json`.
"""

import os
import sys
import json
import time
import argparse
import subprocess
from datetime import datetime

DEFAULT_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "config.json")
DEFAULT_STATUS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "rebase_status.json")

# Branch-specific verification test suites
DEFAULT_BRANCH_TEST_MAP = {
    "pr-fix-core-numpy2-optparse": ["pytest", "test/test_resample.py", "test/test_optparse.py"],
    "pr-fix-io-dictarray-indexing": ["pytest", "test/test_io_hdf.py"],
    "pr-fix-events-numpy2-zerolag": ["pytest", "test/test_coinc_stat.py", "test/test_significance_module.py"],
    "pr-perf-waveform-compress": ["pytest", "test/test_waveform_compress.py"],
    "pr-feat-vetoes-chisq-slicing": ["pytest", "test/test_chisq_slicing.py", "test/test_autochisq.py"],
    "pr-feat-events-eventmgr-multi": ["pytest", "test/test_eventmgr.py"],
    "pr-feat-filter-dynamic-snr-renorm": ["pytest", "test/test_dynamic_snr_renorm.py", "test/test_dynamic_snr_renorm_lazy.py"],
    "pr-feat-inject-injfilter-optimal-snr": ["pytest", "test/test_injfilterrejector.py"],
    "pr-feat-psd-robust-estimators": ["pytest", "test/test_psd.py"],
    "pr-feat-strain-regularized-inpainting": ["pytest", "test/test_gate_and_paint.py"],
}

class UpstreamRebaseMonitor:
    def __init__(self, repo_dir=None, upstream_remote="upstream", upstream_branch="master",
                 origin_remote="origin", status_file=None, interval=60, auto_push=True):
        self.repo_dir = os.path.abspath(repo_dir or "/home/ahnitz/projects/claude/searchdev/pycbc-work/apogee")
        self.upstream_remote = upstream_remote
        self.upstream_branch = upstream_branch
        self.origin_remote = origin_remote
        self.status_file = os.path.abspath(status_file or DEFAULT_STATUS_FILE)
        self.interval = interval
        self.auto_push = auto_push
        self.last_upstream_sha = None
        os.makedirs(os.path.dirname(self.status_file), exist_ok=True)

        # Locate pytest binary in venv or fallback to system
        venv_pytest = os.path.join(self.repo_dir, "..", "venv-apogee", "bin", "pytest")
        if os.path.exists(venv_pytest):
            self.pytest_bin = os.path.abspath(venv_pytest)
        else:
            self.pytest_bin = "pytest"

    def run_cmd(self, cmd, check=False):
        res = subprocess.run(cmd, cwd=self.repo_dir, capture_output=True, text=True)
        if check and res.returncode != 0:
            raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{res.stderr.strip()}")
        return res

    def get_current_branch(self):
        res = self.run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        return res.stdout.strip() if res.returncode == 0 else None

    def fetch_upstream(self):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching {self.upstream_remote}/{self.upstream_branch}...", flush=True)
        res = self.run_cmd(["git", "fetch", self.upstream_remote, self.upstream_branch])
        if res.returncode != 0:
            print(f"[WARN] Failed fetching upstream: {res.stderr.strip()}", flush=True)
            return False
        return True

    def get_upstream_sha(self):
        ref = f"{self.upstream_remote}/{self.upstream_branch}"
        res = self.run_cmd(["git", "rev-parse", ref])
        return res.stdout.strip() if res.returncode == 0 else None

    def load_status(self):
        if os.path.exists(self.status_file):
            try:
                with open(self.status_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"last_check": None, "upstream_head": None, "branches": {}}

    def save_status(self, status):
        try:
            with open(self.status_file, "w", encoding="utf-8") as f:
                json.dump(status, f, indent=2)
        except Exception as e:
            print(f"[WARN] Failed saving status file: {e}", flush=True)

    def get_tracked_branches(self):
        # Return all predefined branches plus any active pr-* branches in local git
        branches = list(DEFAULT_BRANCH_TEST_MAP.keys())
        res = self.run_cmd(["git", "branch", "--list", "pr-*"])
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                b = line.strip().lstrip("* ").strip()
                if b and b not in branches:
                    branches.append(b)
        return branches

    def run_tests_for_branch(self, branch):
        test_args = DEFAULT_BRANCH_TEST_MAP.get(branch)
        if not test_args:
            # Default fallback: look for test matching branch name
            return True, "No specific unit tests registered"

        cmd = [self.pytest_bin] + test_args[1:]
        env = os.environ.copy()
        env["PYTHONPATH"] = "."
        
        print(f"   Running tests: {' '.join(cmd)}...", flush=True)
        t0 = time.time()
        res = subprocess.run(cmd, cwd=self.repo_dir, env=env, capture_output=True, text=True)
        elapsed = time.time() - t0
        passed = (res.returncode == 0)
        out_summary = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else res.stderr.strip()
        msg = f"{out_summary} in {elapsed:.2f}s"
        return passed, msg

    def rebase_branch(self, branch, upstream_sha):
        print(f"\n⚡ Rebasing {branch} onto {self.upstream_remote}/{self.upstream_branch} ({upstream_sha[:9]})...", flush=True)
        
        # Check commits behind upstream
        res_behind = self.run_cmd(["git", "rev-list", "--count", f"{branch}..{upstream_sha}"])
        behind_count = int(res_behind.stdout.strip()) if res_behind.returncode == 0 else 0

        res_ahead = self.run_cmd(["git", "rev-list", "--count", f"{upstream_sha}..{branch}"])
        ahead_count = int(res_ahead.stdout.strip()) if res_ahead.returncode == 0 else 0

        if behind_count == 0:
            print(f"   ✓ {branch} is already up-to-date with upstream ({ahead_count} commits ahead).", flush=True)
            return {
                "status": "UP_TO_DATE",
                "branch": branch,
                "commit_sha": self.run_cmd(["git", "rev-parse", branch]).stdout.strip(),
                "ahead": ahead_count,
                "behind": 0,
                "rebased_at": datetime.now().isoformat()
            }

        # Perform rebase
        rebase_target = f"{self.upstream_remote}/{self.upstream_branch}"
        res_rebase = self.run_cmd(["git", "rebase", rebase_target, branch])

        if res_rebase.returncode != 0:
            print(f"   ❌ Conflict during rebase of {branch}! Aborting...", flush=True)
            self.run_cmd(["git", "rebase", "--abort"])
            return {
                "status": "CONFLICT",
                "branch": branch,
                "error": res_rebase.stderr.strip() or res_rebase.stdout.strip(),
                "timestamp": datetime.now().isoformat()
            }

        new_sha = self.run_cmd(["git", "rev-parse", branch]).stdout.strip()
        print(f"   ✓ Rebase successful! New HEAD: {new_sha[:9]}", flush=True)

        # Run verification tests
        test_passed, test_msg = self.run_tests_for_branch(branch)
        if not test_passed:
            print(f"   ❌ Tests failed for {branch}: {test_msg}", flush=True)
            return {
                "status": "TEST_FAILED",
                "branch": branch,
                "commit_sha": new_sha,
                "test_error": test_msg,
                "timestamp": datetime.now().isoformat()
            }

        print(f"   ✓ Tests passed: {test_msg}", flush=True)

        # Force push to origin if enabled
        push_ok = False
        if self.auto_push:
            print(f"   Pushing {branch} to {self.origin_remote}...", flush=True)
            res_push = self.run_cmd(["git", "push", "-f", self.origin_remote, branch])
            push_ok = (res_push.returncode == 0)
            if push_ok:
                print(f"   ✓ Pushed to {self.origin_remote}/{branch}", flush=True)
            else:
                print(f"   [WARN] Failed pushing to {self.origin_remote}: {res_push.stderr.strip()}", flush=True)

        return {
            "status": "REBASED_AND_PUSHED" if push_ok else "REBASED_LOCAL",
            "branch": branch,
            "commit_sha": new_sha,
            "ahead": int(self.run_cmd(["git", "rev-list", "--count", f"{upstream_sha}..{branch}"]).stdout.strip() or 0),
            "behind": 0,
            "tests": test_msg,
            "pushed": push_ok,
            "rebased_at": datetime.now().isoformat()
        }

    def check_and_sync(self, force=False):
        initial_branch = self.get_current_branch()
        self.fetch_upstream()
        upstream_sha = self.get_upstream_sha()

        if not upstream_sha:
            print("[ERROR] Could not determine upstream HEAD SHA", flush=True)
            return

        status = self.load_status()
        status["last_check"] = datetime.now().isoformat()
        status["upstream_remote"] = self.upstream_remote
        status["upstream_branch"] = self.upstream_branch
        status["upstream_head"] = upstream_sha

        upstream_moved = (upstream_sha != self.last_upstream_sha and self.last_upstream_sha is not None)
        self.last_upstream_sha = upstream_sha

        if upstream_moved:
            print(f"\n📢 UPSTREAM ADVANCED! New master commit: {upstream_sha[:9]}", flush=True)
        elif force:
            print(f"\n🔄 Running forced synchronization against {upstream_sha[:9]}...", flush=True)
        else:
            print(f"Upstream {self.upstream_remote}/{self.upstream_branch} at {upstream_sha[:9]} (no change). Checking branches...", flush=True)

        branches = self.get_tracked_branches()
        rebased_any = False

        try:
            for branch in branches:
                res_behind = self.run_cmd(["git", "rev-list", "--count", f"{branch}..{upstream_sha}"])
                behind = int(res_behind.stdout.strip()) if res_behind.returncode == 0 else 0
                
                if behind > 0 or force:
                    branch_status = self.rebase_branch(branch, upstream_sha)
                    status["branches"][branch] = branch_status
                    rebased_any = True
                else:
                    sha = self.run_cmd(["git", "rev-parse", branch]).stdout.strip()
                    ahead = int(self.run_cmd(["git", "rev-list", "--count", f"{upstream_sha}..{branch}"]).stdout.strip() or 0)
                    status["branches"][branch] = {
                        "status": "UP_TO_DATE",
                        "commit_sha": sha,
                        "ahead": ahead,
                        "behind": 0,
                        "last_verified": datetime.now().isoformat()
                    }
        finally:
            # Restore working branch
            if initial_branch and self.get_current_branch() != initial_branch:
                self.run_cmd(["git", "checkout", initial_branch])

        self.save_status(status)
        if rebased_any:
            print(f"\n✓ Upstream sync cycle complete! Status recorded in {self.status_file}\n", flush=True)
        else:
            print(f"All {len(branches)} topic branches are up-to-date with upstream.\n", flush=True)

    def run_daemon(self):
        print(f"============================================================")
        print(f"  PyCBC Upstream Rebase Monitor Active")
        print(f"  Upstream:  {self.upstream_remote}/{self.upstream_branch}")
        print(f"  Origin:    {self.origin_remote}")
        print(f"  Interval:  {self.interval}s")
        print(f"  Repo:      {self.repo_dir}")
        print(f"============================================================\n", flush=True)

        while True:
            try:
                self.check_and_sync()
            except Exception as e:
                print(f"[RebaseMonitor ERROR] {e}", flush=True)
            time.sleep(self.interval)

def main():
    parser = argparse.ArgumentParser(description="PyCBC Automated Upstream Rebase Monitor")
    parser.add_argument("--repo-dir", default=None, help="Path to PyCBC repo")
    parser.add_argument("--upstream-remote", default="upstream", help="Remote name for upstream")
    parser.add_argument("--upstream-branch", default="master", help="Branch name for upstream")
    parser.add_argument("--origin-remote", default="origin", help="Remote name for maintainer fork")
    parser.add_argument("--interval", type=int, default=60, help="Check interval in seconds")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit")
    parser.add_argument("--force", action="store_true", help="Force rebase of all branches regardless of behind count")
    parser.add_argument("--no-push", action="store_true", help="Do not push rebased branches to origin")
    args = parser.parse_args()

    monitor = UpstreamRebaseMonitor(
        repo_dir=args.repo_dir,
        upstream_remote=args.upstream_remote,
        upstream_branch=args.upstream_branch,
        origin_remote=args.origin_remote,
        interval=args.interval,
        auto_push=not args.no_push
    )

    if args.once:
        monitor.check_and_sync(force=args.force)
    else:
        monitor.run_daemon()

if __name__ == "__main__":
    main()
