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
    "pr-feat-frame-gwosc-hdf": ["pytest", "test/test_gwosc_hdf.py"],
}

def load_config(config_file=DEFAULT_CONFIG_FILE):
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

class UpstreamRebaseMonitor:
    def __init__(self, repo_dir=None, upstream_remote=None, upstream_branch=None,
                 origin_remote=None, status_file=None, interval=None, auto_push=True):
        cfg = load_config()
        self.repo_dir = repo_dir or os.environ.get("REVIEWER_REPO_DIR") or cfg.get("project", {}).get("working_tree_path")
        if not self.repo_dir or not os.path.exists(self.repo_dir):
            for cand in [
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "pycbc-work", "apogee")),
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "pycbc")),
                os.getcwd()
            ]:
                if os.path.exists(os.path.join(cand, ".git")):
                    self.repo_dir = cand
                    break
        self.repo_dir = os.path.abspath(self.repo_dir or os.getcwd())

        self.upstream_remote = upstream_remote or os.environ.get("UPSTREAM_REMOTE") or cfg.get("project", {}).get("upstream_remote", "upstream")
        self.upstream_branch = upstream_branch or os.environ.get("UPSTREAM_BRANCH") or cfg.get("project", {}).get("upstream_branch", "master")
        self.origin_remote = origin_remote or os.environ.get("ORIGIN_REMOTE") or cfg.get("project", {}).get("origin_remote", "origin")
        self.interval = interval or int(os.environ.get("REBASE_INTERVAL", cfg.get("rebase", {}).get("interval_seconds", 60)))
        self.status_file = os.path.abspath(status_file or os.environ.get("REBASE_STATUS_FILE") or cfg.get("storage", {}).get("rebase_status_file", DEFAULT_STATUS_FILE))
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

    def get_worktree_map(self):
        res = self.run_cmd(["git", "worktree", "list", "--porcelain"])
        wt_map = {}
        current_wt = None
        for line in res.stdout.splitlines():
            if line.startswith("worktree "):
                current_wt = line.split(" ", 1)[1].strip()
            elif line.startswith("branch "):
                ref = line.split(" ", 1)[1].strip()
                branch = ref.replace("refs/heads/", "")
                if current_wt:
                    wt_map[branch] = current_wt
        return wt_map

    def get_tracked_branches(self):
        # Return all predefined branches plus any active pr-* branches in local git
        branches = []
        for b in DEFAULT_BRANCH_TEST_MAP.keys():
            if b not in branches:
                branches.append(b)
        res = self.run_cmd(["git", "branch", "--list", "pr-*"])
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                b = line.strip().lstrip("*+ ").strip()
                if b and b not in branches:
                    branches.append(b)
        return branches

    def run_tests_for_branch(self, branch, work_dir=None):
        test_args = DEFAULT_BRANCH_TEST_MAP.get(branch)
        if not test_args:
            # Default fallback: look for test matching branch name
            return True, "No specific unit tests registered"

        target_dir = work_dir or self.repo_dir
        # Use igwn-py311 pytest which has full LALSuite and PyCBC dependencies
        igwn_pytest = "/home/ahnitz/miniconda3/envs/igwn-py311/bin/pytest"
        venv_python = os.path.join(self.repo_dir, "..", "venv-apogee", "bin", "python")
        if os.path.exists(igwn_pytest):
            cmd = [igwn_pytest] + test_args[1:]
        elif os.path.exists(venv_python):
            cmd = [venv_python, "-m", "pytest"] + test_args[1:]
        else:
            cmd = [self.pytest_bin] + test_args[1:]

        env = os.environ.copy()
        env["PYTHONPATH"] = "."

        print(f"   Running tests in {target_dir}: {' '.join(cmd)}...", flush=True)
        t0 = time.time()
        res = subprocess.run(cmd, cwd=target_dir, env=env, capture_output=True, text=True)
        elapsed = time.time() - t0
        passed = (res.returncode == 0)
        out_summary = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else res.stderr.strip()
        msg = f"{out_summary} in {elapsed:.2f}s"
        return passed, msg

    def rebase_branch(self, branch, upstream_sha, work_dir=None):
        target_dir = work_dir or self.repo_dir
        print(f"\n⚡ Rebasing {branch} in {target_dir} onto {self.upstream_remote}/{self.upstream_branch} ({upstream_sha[:9]})...", flush=True)

        # Check commits behind upstream
        res_behind = subprocess.run(["git", "rev-list", "--count", f"{branch}..{upstream_sha}"], cwd=target_dir, capture_output=True, text=True)
        behind_count = int(res_behind.stdout.strip()) if res_behind.returncode == 0 and res_behind.stdout.strip().isdigit() else 0

        res_ahead = subprocess.run(["git", "rev-list", "--count", f"{upstream_sha}..{branch}"], cwd=target_dir, capture_output=True, text=True)
        ahead_count = int(res_ahead.stdout.strip()) if res_ahead.returncode == 0 and res_ahead.stdout.strip().isdigit() else 0

        if behind_count == 0:
            print(f"   ✓ {branch} is already up-to-date with upstream ({ahead_count} commits ahead).", flush=True)
            p_sha = subprocess.run(["git", "rev-parse", branch], cwd=target_dir, capture_output=True, text=True)
            return {
                "status": "UP_TO_DATE",
                "branch": branch,
                "commit_sha": p_sha.stdout.strip() if p_sha.returncode == 0 else "",
                "ahead": ahead_count,
                "behind": 0,
                "rebased_at": datetime.now().isoformat()
            }

        # Perform rebase
        rebase_target = f"{self.upstream_remote}/{self.upstream_branch}"
        if work_dir and os.path.exists(work_dir):
            # When inside worktree where branch is checked out, rebase current HEAD
            res_rebase = subprocess.run(["git", "rebase", rebase_target], cwd=work_dir, capture_output=True, text=True)
        else:
            # Check if repo_dir has unstaged changes
            p_dirty = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo_dir, capture_output=True, text=True)
            if p_dirty.returncode == 0 and p_dirty.stdout.strip():
                print(f"   ⚠️ Cannot rebase detached branch {branch} in main repo: working tree has local changes.", flush=True)
                return {
                    "status": "SKIPPED_DIRTY_TREE",
                    "branch": branch,
                    "error": "Main working directory has unstaged modifications; skipping rebase",
                    "timestamp": datetime.now().isoformat()
                }
            res_rebase = subprocess.run(["git", "rebase", rebase_target, branch], cwd=self.repo_dir, capture_output=True, text=True)

        if res_rebase.returncode != 0:
            print(f"   ❌ Conflict during rebase of {branch}! Aborting...", flush=True)
            subprocess.run(["git", "rebase", "--abort"], cwd=target_dir, capture_output=True, text=True)
            return {
                "status": "CONFLICT",
                "branch": branch,
                "error": res_rebase.stderr.strip() or res_rebase.stdout.strip(),
                "timestamp": datetime.now().isoformat()
            }

        new_sha = subprocess.run(["git", "rev-parse", branch], cwd=target_dir, capture_output=True, text=True).stdout.strip()
        print(f"   ✓ Rebase successful! New HEAD: {new_sha[:9]}", flush=True)

        # Run verification tests
        test_passed, test_msg = self.run_tests_for_branch(branch, work_dir=target_dir)
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
            res_push = subprocess.run(["git", "push", "-f", self.origin_remote, branch], cwd=target_dir, capture_output=True, text=True)
            push_ok = (res_push.returncode == 0)
            if push_ok:
                print(f"   ✓ Pushed to {self.origin_remote}/{branch}", flush=True)
            else:
                print(f"   [WARN] Failed pushing to {self.origin_remote}: {res_push.stderr.strip()}", flush=True)

        return {
            "status": "REBASED_AND_PUSHED" if push_ok else "REBASED_LOCAL",
            "branch": branch,
            "commit_sha": new_sha,
            "ahead": int(subprocess.run(["git", "rev-list", "--count", f"{upstream_sha}..{branch}"], cwd=target_dir, capture_output=True, text=True).stdout.strip() or 0),
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
        wt_map = self.get_worktree_map()
        rebased_any = False

        try:
            for branch in branches:
                work_dir = wt_map.get(branch)
                target_dir = work_dir or self.repo_dir
                res_behind = subprocess.run(["git", "rev-list", "--count", f"{branch}..{upstream_sha}"], cwd=target_dir, capture_output=True, text=True)
                behind = int(res_behind.stdout.strip()) if res_behind.returncode == 0 and res_behind.stdout.strip().isdigit() else 0
                
                if behind > 0 or force:
                    branch_status = self.rebase_branch(branch, upstream_sha, work_dir=work_dir)
                    status["branches"][branch] = branch_status
                    rebased_any = True
                else:
                    sha = subprocess.run(["git", "rev-parse", branch], cwd=target_dir, capture_output=True, text=True).stdout.strip()
                    ahead_str = subprocess.run(["git", "rev-list", "--count", f"{upstream_sha}..{branch}"], cwd=target_dir, capture_output=True, text=True).stdout.strip()
                    ahead = int(ahead_str) if ahead_str.isdigit() else 0
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
