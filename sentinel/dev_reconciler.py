#!/usr/bin/env python3
"""
Automated Development Branch Ingestion & Wave Reconciler
=========================================================
Continuously tracks the designated development branch (e.g. `firinspiral3-multidet-asym`).
As new commits are added to the development branch, this reconciler:
1. Detects new commits between the last ingested SHA and the dev branch HEAD.
2. Evaluates the files and functional changes introduced:
   - Active PR Updates: If a commit touches files owned by an active/open PR branch,
     it assesses whether the change should be added to that existing PR and proposes/stages it.
   - Future Wave Updates: If a commit touches files planned for future wave PRs (Waves 2-4),
     it automatically updates the future wave's specifications, file lists, and diff stats.
   - New PR Candidate Generation: If a commit introduces a new standalone feature or bugfix
     not mapped to any existing wave, it automatically synthesizes a new PR candidate,
     determines its dependency DAG, and registers it in the future wave queue.
3. Keeps machine-readable (`data/wave_roadmap.json`) and human-readable roadmap specifications
   constantly up to date.
"""

import os
import sys
import json
import time
import argparse
import subprocess
from datetime import datetime

DEFAULT_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "config.json")
DEFAULT_ROADMAP_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "wave_roadmap.json")

# Initial canonical subsystem mapping for known files in PyCBC decomposition
INITIAL_PR_REGISTRY = {
    # WAVE 1: ACTIVE IMMEDIATE REVIEW QUEUE
    "PR-1A": {
        "wave": 1,
        "branch": "pr-fix-core-numpy2-optparse",
        "title": "fix(core): numpy2 compatibility, robust to_int parser, bounds checks, resample copy",
        "status": "PUSHED_TO_ORIGIN",
        "dependencies": [],
        "files": [
            "pycbc/types/config.py",
            "pycbc/types/optparse.py",
            "pycbc/filter/resample.py",
            "pycbc/filter/qtransform.py",
            "pycbc/results/render.py",
            "test/test_optparse.py",
            "test/test_qtransform.py",
            "test/test_resample.py"
        ],
        "test_cmd": "pytest test/test_optparse.py test/test_resample.py test/test_qtransform.py"
    },
    "PR-1B": {
        "wave": 1,
        "branch": "pr-fix-io-dictarray-indexing",
        "title": "fix(io): DictArray in-place indexing and SingleDetTriggers boolean masking",
        "status": "PUSHED_TO_ORIGIN",
        "dependencies": [],
        "files": ["pycbc/io/hdf.py", "test/test_io_hdf.py"],
        "test_cmd": "pytest test/test_io_hdf.py"
    },
    "PR-1C": {
        "wave": 1,
        "branch": "pr-fix-events-numpy2-zerolag",
        "title": "fix(events): NumPy 2 compatibility, empty event handling, and robust significance",
        "status": "PUSHED_TO_ORIGIN",
        "dependencies": [],
        "files": [
            "pycbc/events/coinc.py",
            "pycbc/events/significance.py",
            "pycbc/events/threshold.py",
            "test/test_coinc_stat.py",
            "test/test_significance_module.py"
        ],
        "test_cmd": "pytest test/test_coinc_stat.py test/test_significance_module.py"
    },
    "PR-1D": {
        "wave": 1,
        "branch": "pr-perf-waveform-compress",
        "title": "perf(waveform): Refine greedy sample point selection in waveform compression",
        "status": "PUSHED_TO_ORIGIN",
        "dependencies": [],
        "files": ["pycbc/waveform/compress.py", "test/test_waveform_compress.py"],
        "test_cmd": "pytest test/test_waveform_compress.py"
    },
    "PR-1E": {
        "wave": 1,
        "branch": "pr-feat-vetoes-chisq-slicing",
        "title": "feat(vetoes): Memory-efficient bin SNR slicing and numerical stability in power chisq",
        "status": "PUSHED_TO_ORIGIN",
        "dependencies": [],
        "files": [
            "pycbc/vetoes/chisq.py",
            "pycbc/vetoes/sgchisq.py",
            "test/test_chisq_slicing.py",
            "test/test_autochisq.py"
        ],
        "test_cmd": "pytest test/test_chisq_slicing.py test/test_autochisq.py"
    },
    "PR-1F": {
        "wave": 1,
        "branch": "pr-feat-events-eventmgr-multi",
        "title": "feat(events): Multi-detector HDF5 output schema and strain buffer deallocation",
        "status": "PUSHED_TO_ORIGIN",
        "dependencies": [],
        "files": ["pycbc/events/eventmgr.py", "test/test_eventmgr.py", "test/test_threshold.py"],
        "test_cmd": "pytest test/test_eventmgr.py"
    },
    "PR-1G": {
        "wave": 1,
        "branch": "pr-feat-filter-dynamic-snr-renorm",
        "title": "feat(filter): Dynamic SNR renormalization via hollow rolling window variance envelope",
        "status": "PUSHED_TO_ORIGIN",
        "dependencies": [],
        "files": [
            "pycbc/filter/matched_filter_ratio.py",
            "test/test_dynamic_snr_renorm.py",
            "test/test_dynamic_snr_renorm_lazy.py"
        ],
        "test_cmd": "pytest test/test_dynamic_snr_renorm.py test/test_dynamic_snr_renorm_lazy.py"
    },
    "PR-1H": {
        "wave": 1,
        "branch": "pr-feat-inject-injfilter-optimal-snr",
        "title": "feat(inject): Pre-filtering of sub-threshold injections via optimal SNR cut",
        "status": "PUSHED_TO_ORIGIN",
        "dependencies": [],
        "files": ["pycbc/inject/injfilterrejector.py", "test/test_injfilterrejector.py"],
        "test_cmd": "pytest test/test_injfilterrejector.py"
    },
    "PR-1I": {
        "wave": 1,
        "branch": "pr-feat-psd-robust-estimators",
        "title": "feat(psd): Robust trimmed-mean Welch and DPSS multitaper PSD estimators",
        "status": "PUSHED_TO_ORIGIN",
        "dependencies": [],
        "files": ["pycbc/psd/estimate.py", "test/test_psd.py"],
        "test_cmd": "pytest test/test_psd.py"
    },
    "PR-1J": {
        "wave": 1,
        "branch": "pr-feat-strain-regularized-inpainting",
        "title": "feat(strain): Regularized Cholesky inpainting and numerical stability for strain gating",
        "status": "PUSHED_TO_ORIGIN",
        "dependencies": [],
        "files": ["pycbc/strain/gate.py", "pycbc/strain/strain.py", "test/test_gate_and_paint.py", "test/test_infmodel.py"],
        "test_cmd": "pytest test/test_gate_and_paint.py"
    },

    # WAVE 2: PRECONDITIONING, MODELS & COINCIDENCE
    "PR-2A": {
        "wave": 2,
        "branch": "pr-feat-psd-compact-instantaneous-model",
        "title": "feat(psd): Compact instantaneous PSD model representation and serialization",
        "status": "QUEUED",
        "dependencies": ["PR-1I"],
        "files": ["pycbc/psd/model.py", "pycbc/psd/read.py", "pycbc/psd/analytical.py"],
        "test_cmd": "pytest test/test_psd.py"
    },
    "PR-2B": {
        "wave": 2,
        "branch": "pr-feat-filter-continuous-overwhitening",
        "title": "feat(strain): Continuous in-situ overwhitening and Toeplitz inpainting",
        "status": "QUEUED",
        "dependencies": ["PR-1J", "PR-2A"],
        "files": ["pycbc/filter/overwhiten.py", "bin/pycbc_overwhiten", "test/test_overwhiten.py"],
        "test_cmd": "pytest test/test_overwhiten.py"
    },
    "PR-2C": {
        "wave": 2,
        "branch": "pr-perf-coinc-statmap-memory-livetime",
        "title": "perf(coinc): Memory-bounded statmap combination and multi-detector livetime accounting",
        "status": "QUEUED",
        "dependencies": ["PR-1B", "PR-1C"],
        "files": ["bin/all_sky_search/pycbc_add_statmap", "bin/all_sky_search/pycbc_combine_statmap"],
        "test_cmd": "pytest test/test_coinc_stat.py"
    },
    "PR-2D": {
        "wave": 2,
        "branch": "pr-feat-events-single-coinc-downweight",
        "title": "feat(events): Smooth sub-threshold Rayleigh statistic and singles downweighting",
        "status": "QUEUED",
        "dependencies": ["PR-1B", "PR-1C"],
        "files": [
            "pycbc/events/stat.py",
            "pycbc/events/ranking.py",
            "bin/all_sky_search/pycbc_fit_sngls_split_binned",
            "bin/all_sky_search/pycbc_sngls_findtrigs",
            "test/test_ranking.py"
        ],
        "test_cmd": "pytest test/test_ranking.py"
    },
    "PR-2E": {
        "wave": 2,
        "branch": "pr-tool-inject-optimal-mf-snr",
        "title": "tool(inject): Dedicated optimal matched-filter SNR evaluator with glitch autogating",
        "status": "QUEUED",
        "dependencies": ["PR-1H"],
        "files": ["bin/pycbc_optimal_mf_snr"],
        "test_cmd": "pytest test/test_injfilterrejector.py"
    },

    # WAVE 3: BANK STRUCTURES, FILTER ENGINE & WORKFLOW
    "PR-3A": {
        "wave": 3,
        "branch": "pr-feat-waveform-ratio-filter-bank",
        "title": "feat(waveform): RatioFilterBank data structure and hierarchical grouping",
        "status": "QUEUED",
        "dependencies": ["PR-1D", "PR-1H"],
        "files": ["pycbc/waveform/bank.py", "test/test_bank.py"],
        "test_cmd": "pytest test/test_bank.py"
    },
    "PR-3B": {
        "wave": 3,
        "branch": "pr-feat-filter-matched-filter-ratio-control",
        "title": "feat(filter): MatchedFilterRatioControl execution engine and ratio candidate extraction",
        "status": "QUEUED",
        "dependencies": ["PR-1G"],
        "files": ["pycbc/filter/matched_filter_ratio.py"],
        "test_cmd": "pytest test/test_dynamic_snr_renorm.py"
    },
    "PR-3C": {
        "wave": 3,
        "branch": "pr-tool-waveform-pycbc-fir-bank",
        "title": "tool(waveform): Streamlined 3-level FIR filter bank generator (pycbc_fir_bank)",
        "status": "QUEUED",
        "dependencies": ["PR-1D", "PR-3A"],
        "files": ["bin/pycbc_fir_bank"],
        "test_cmd": "pytest test/test_bank.py"
    },
    "PR-3D": {
        "wave": 3,
        "branch": "pr-feat-workflow-multidet-inspiral-dag",
        "title": "feat(workflow): Multi-detector hierarchical inspiral DAG orchestration and segment planning",
        "status": "QUEUED",
        "dependencies": ["PR-1F", "PR-2C"],
        "files": [
            "pycbc/workflow/matchedfilter.py",
            "pycbc/workflow/coincidence.py",
            "pycbc/workflow/injection.py",
            "pycbc/workflow/segment.py"
        ],
        "test_cmd": "pytest test/test_workflow.py"
    },

    # WAVE 4: INSPIRAL SEARCH EXECUTABLE & DIAGNOSTICS
    "PR-4A": {
        "wave": 4,
        "branch": "pr-feat-minifollowups-diagnostics-plotting",
        "title": "feat(diagnostics): Minifollowup visual diagnostics, background IFAR normalization, and found/missed pages",
        "status": "QUEUED",
        "dependencies": ["PR-1F", "PR-2C", "PR-3D"],
        "files": [
            "bin/minifollowups/pycbc_sngl_minifollowups",
            "bin/minifollowups/pycbc_injection_minifollowups",
            "bin/minifollowups/pycbc_foreground_minifollowups",
            "bin/plotting/pycbc_page_ifar",
            "bin/plotting/pycbc_page_sensitivity",
            "bin/plotting/pycbc_page_foundmissed",
            "bin/plotting/pycbc_plot_singles_vs_params"
        ],
        "test_cmd": "pytest test/test_results.py"
    },
    "PR-4B": {
        "wave": 4,
        "branch": "pr-feat-inspiral-pycbc-inspiral-fir",
        "title": "feat(inspiral): Hierarchical FIR multi-detector matched-filter search executable (pycbc_inspiral_fir)",
        "status": "QUEUED",
        "dependencies": ["PR-1E", "PR-1F", "PR-3A", "PR-3B", "PR-3C"],
        "files": ["bin/pycbc_inspiral_fir"],
        "test_cmd": "pytest test/test_chisq_slicing.py test/test_dynamic_snr_renorm.py"
    }
}


def load_config(config_file=DEFAULT_CONFIG_FILE):
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


class DevBranchReconciler:
    def __init__(self, repo_dir=None, dev_branch=None, upstream_remote=None,
                 upstream_branch=None, roadmap_file=None):
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

        self.dev_branch = dev_branch or os.environ.get("DEV_BRANCH") or cfg.get("project", {}).get("dev_branch", "firinspiral3-multidet-asym")
        self.upstream_remote = upstream_remote or cfg.get("project", {}).get("upstream_remote", "upstream")
        self.upstream_branch = upstream_branch or cfg.get("project", {}).get("upstream_branch", "master")
        self.roadmap_file = os.path.abspath(roadmap_file or cfg.get("storage", {}).get("roadmap_file", DEFAULT_ROADMAP_FILE))
        os.makedirs(os.path.dirname(self.roadmap_file), exist_ok=True)

        self.state = self.load_state()

    def run_cmd(self, cmd, check=False):
        res = subprocess.run(cmd, cwd=self.repo_dir, capture_output=True, text=True)
        if check and res.returncode != 0:
            raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{res.stderr.strip()}")
        return res

    def load_state(self):
        if os.path.exists(self.roadmap_file):
            try:
                with open(self.roadmap_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "version": "1.0",
            "last_updated": None,
            "dev_branch": self.dev_branch,
            "upstream_base": None,
            "last_ingested_commit": None,
            "ingested_commits_count": 0,
            "prs": json.loads(json.dumps(INITIAL_PR_REGISTRY)),
            "ingestion_log": []
        }

    def save_state(self):
        self.state["last_updated"] = datetime.now().isoformat()
        try:
            with open(self.roadmap_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            print(f"[WARN] Failed to save roadmap state: {e}", file=sys.stderr)

    def get_merge_base(self):
        ref = f"{self.upstream_remote}/{self.upstream_branch}"
        res = self.run_cmd(["git", "merge-base", ref, self.dev_branch])
        if res.returncode != 0 or not res.stdout.strip():
            res = self.run_cmd(["git", "merge-base", "upstream/master", self.dev_branch])
        if res.returncode != 0 or not res.stdout.strip():
            res = self.run_cmd(["git", "merge-base", "master", self.dev_branch])
        return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else "f6eaed241"

    def get_dev_head(self):
        res = self.run_cmd(["git", "rev-parse", self.dev_branch])
        return res.stdout.strip() if res.returncode == 0 else None

    def get_commit_files(self, commit_sha):
        res = self.run_cmd(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha])
        if res.returncode == 0:
            return [line.strip() for line in res.stdout.splitlines() if line.strip()]
        return []

    def get_commit_stat(self, commit_sha):
        res = self.run_cmd(["git", "show", "--shortstat", "--oneline", commit_sha])
        lines = res.stdout.strip().splitlines()
        stat_line = lines[-1] if len(lines) > 1 else ""
        return stat_line

    def find_matching_pr(self, file_path):
        """Finds which PR candidate in the registry owns or is best suited for this file."""
        # 1. Exact file match in registered PRs
        for pr_id, pr_data in self.state["prs"].items():
            if file_path in pr_data.get("files", []):
                return pr_id

        # 2. Test file stem matching (e.g. test/test_gwosc_hdf.py -> matches PR with gwosc_hdf.py)
        if file_path.startswith("test/"):
            test_base = os.path.basename(file_path).replace("test_", "").replace(".py", "")
            for pr_id, pr_data in self.state["prs"].items():
                for pf in pr_data.get("files", []):
                    if test_base in pf:
                        return pr_id

        # 3. Subsystem prefix matching (excluding generic test/ directory)
        f_dir = os.path.dirname(file_path)
        if f_dir and f_dir != "test":
            for pr_id, pr_data in self.state["prs"].items():
                for pf in pr_data.get("files", []):
                    p_dir = os.path.dirname(pf)
                    if p_dir and p_dir == f_dir:
                        return pr_id

        return None

    def synthesize_new_pr_candidate(self, file_path, commit_sha, commit_msg):
        """Synthesizes a new PR candidate when an unmapped file/feature arrives on dev branch."""
        # Detect domain
        if "gwosc_hdf" in file_path or "frame" in file_path:
            pr_id = "PR-2F"
            wave = 2
            branch = "pr-feat-frame-gwosc-hdf-strain"
            title = "feat(frame): support reading strain from GWOSC HDF5 files and caches"
            test_cmd = "pytest test/test_gwosc_hdf.py"
        elif file_path.startswith("test/"):
            pr_id = f"PR-FIX-TEST-{int(time.time())%1000}"
            wave = 2
            branch = f"pr-fix-test-{os.path.basename(file_path).replace('.py', '')}"
            title = f"test: add unit test coverage for {os.path.basename(file_path)}"
            test_cmd = f"pytest {file_path}"
        else:
            base_name = os.path.basename(file_path).split('.')[0]
            pr_id = f"PR-FEAT-{base_name.upper()[:12]}"
            wave = 2
            branch = f"pr-feat-{base_name.lower()}"
            title = f"feat({os.path.dirname(file_path).replace('/', '-') or 'core'}): {commit_msg[:60]}"
            test_cmd = "pytest"

        return {
            "pr_id": pr_id,
            "wave": wave,
            "branch": branch,
            "title": title,
            "status": "CANDIDATE_SYNTHESIZED",
            "dependencies": [],
            "files": [file_path],
            "test_cmd": test_cmd,
            "synthesized_from_commit": commit_sha
        }

    def reconcile(self):
        """Scans the development branch commits and reconciles with PRs and Waves."""
        base_commit = self.get_merge_base()
        head_commit = self.get_dev_head()
        if not head_commit:
            return {"status": "error", "message": f"Dev branch {self.dev_branch} not found in {self.repo_dir}"}

        self.state["upstream_base"] = base_commit
        last_ingested = self.state.get("last_ingested_commit")

        # Determine commit range
        if last_ingested:
            rev_range = f"{last_ingested}..{self.dev_branch}"
        else:
            rev_range = f"{base_commit}..{self.dev_branch}"

        res = self.run_cmd(["git", "log", "--reverse", "--format=%H %s", rev_range])
        new_commits = []
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if line.strip():
                    sha, _, msg = line.strip().partition(" ")
                    new_commits.append((sha, msg))

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Reconciling {self.dev_branch} ({len(new_commits)} commits in {rev_range})")

        reconciled_events = []

        for sha, msg in new_commits:
            files = self.get_commit_files(sha)
            stat = self.get_commit_stat(sha)
            
            for f in files:
                matched_pr = self.find_matching_pr(f)
                if matched_pr:
                    pr_info = self.state["prs"][matched_pr]
                    wave = pr_info.get("wave", 1)
                    if wave == 1:
                        action_type = "ACTIVE_PR_UPDATE"
                        note = f"Commit {sha[:9]} touches active PR {matched_pr} file '{f}'. Evaluate for staging/cherry-pick."
                    else:
                        action_type = "FUTURE_WAVE_UPDATE"
                        note = f"Commit {sha[:9]} absorbed into future Wave {wave} PR {matched_pr} ('{f}')."
                        if f not in pr_info["files"]:
                            pr_info["files"].append(f)
                    
                    event = {
                        "commit_sha": sha,
                        "commit_msg": msg,
                        "file": f,
                        "action": action_type,
                        "pr_id": matched_pr,
                        "wave": wave,
                        "note": note,
                        "timestamp": datetime.now().isoformat()
                    }
                    reconciled_events.append(event)
                    self.state["ingestion_log"].append(event)
                else:
                    # New unmapped file -> Synthesize or group into new PR candidate
                    new_cand = self.synthesize_new_pr_candidate(f, sha, msg)
                    new_pr_id = new_cand["pr_id"]
                    if new_pr_id not in self.state["prs"]:
                        self.state["prs"][new_pr_id] = new_cand
                        event = {
                            "commit_sha": sha,
                            "commit_msg": msg,
                            "file": f,
                            "action": "NEW_PR_CANDIDATE_SYNTHESIZED",
                            "pr_id": new_pr_id,
                            "wave": new_cand["wave"],
                            "note": f"New standalone capability in {f} synthesized into {new_pr_id} ({new_cand['title']})",
                            "timestamp": datetime.now().isoformat()
                        }
                        reconciled_events.append(event)
                        self.state["ingestion_log"].append(event)
                    else:
                        if f not in self.state["prs"][new_pr_id]["files"]:
                            self.state["prs"][new_pr_id]["files"].append(f)

        self.state["last_ingested_commit"] = head_commit
        self.state["ingested_commits_count"] = self.state.get("ingested_commits_count", 0) + len(new_commits)
        self.save_state()

        return {
            "status": "ok",
            "dev_branch": self.dev_branch,
            "dev_head": head_commit,
            "base_commit": base_commit,
            "new_commits_scanned": len(new_commits),
            "events_count": len(reconciled_events),
            "events": reconciled_events[-20:] # return last 20 events
        }

    def print_status(self):
        state = self.state
        print("==================================================================")
        print("📊 DEVELOPMENT BRANCH & WAVE ROADMAP STATUS")
        print("==================================================================")
        print(f"Target Dev Branch:   {self.dev_branch}")
        print(f"Working Tree:        {self.repo_dir}")
        print(f"Upstream Base:       {state.get('upstream_base')}")
        print(f"Last Ingested SHA:   {state.get('last_ingested_commit')}")
        print(f"Total PRs Tracked:   {len(state.get('prs', {}))}")
        print("------------------------------------------------------------------")
        
        # Group by wave
        waves = {}
        for pid, pdata in state.get("prs", {}).items():
            w = pdata.get("wave", 1)
            waves.setdefault(w, []).append((pid, pdata))

        for w in sorted(waves.keys()):
            items = waves[w]
            print(f"\n🌊 WAVE {w} ({len(items)} PRs):")
            for pid, pdata in items:
                branch = pdata.get("branch", "unknown")
                status = pdata.get("status", "QUEUED")
                files_cnt = len(pdata.get("files", []))
                print(f"   {pid:8s} [{status:22s}] ({branch}): {files_cnt} files -> {pdata.get('title')[:60]}")

        print("==================================================================")


def main():
    parser = argparse.ArgumentParser(description="PyCBC Dev Branch Ingestion & Wave Reconciler")
    parser.add_argument("--repo-dir", help="Path to PyCBC git repository")
    parser.add_argument("--dev-branch", help="Target development branch")
    parser.add_argument("--reconcile", action="store_true", help="Run immediate reconciliation against dev branch")
    parser.add_argument("--status", action="store_true", help="Print current wave roadmap status")
    parser.add_argument("--watch", action="store_true", help="Run continuous watcher daemon")
    parser.add_argument("--interval", type=int, default=60, help="Watch interval in seconds")
    args = parser.parse_args()

    reconciler = DevBranchReconciler(repo_dir=args.repo_dir, dev_branch=args.dev_branch)

    if args.status:
        reconciler.print_status()
        return

    if args.reconcile or not args.watch:
        res = reconciler.reconcile()
        print(json.dumps(res, indent=2))
        return

    if args.watch:
        print(f"Starting continuous dev branch reconciler on {reconciler.dev_branch} (interval: {args.interval}s)...")
        while True:
            try:
                res = reconciler.reconcile()
                if res.get("events_count", 0) > 0:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Reconciled {res['events_count']} events across PRs.")
            except Exception as e:
                print(f"[{datetime.now().strftime('%H:%M:%S')} ERROR] Reconcile failed: {e}", file=sys.stderr)
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
