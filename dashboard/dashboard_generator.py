#!/usr/bin/env python3
"""
Dashboard Generator & Template Utility
======================================
Generates and updates interactive review dashboards from structured PR manifests.
Allows the Dual-Direction Review System to be repurposed for any software repository.
"""

import os
import sys
import json
import argparse

def generate_sample_manifest():
    return {
        "projectTitle": "PyCBC PR Review Dashboard",
        "mergeBase": "f6eaed241",
        "upstreamRepo": "gwastro/pycbc",
        "maintainerFork": "ahnitz/pycbc",
        "prs": [
            {
                "id": "PR-SAMPLE",
                "shortName": "Sample Optimization",
                "branch": "pr-sample-opt",
                "commitSha": "abc123def456",
                "title": "perf(core): Example optimization for review",
                "diff": "+25 / -5 (~30 lines, 1 file)",
                "diffBadge": "+25 / -5",
                "category": "Core & Optimization",
                "wave": 1,
                "testCmd": "pytest test/test_sample.py",
                "testTiming": "2 passed in 1.1s",
                "unblocks": "None (Self-contained)",
                "diffCmd": "git diff master..pr-sample-opt",
                "ghCompareUrl": "https://github.com/upstream/repo/compare/master...user:repo:pr-sample-opt",
                "ghCreatePrUrl": "https://github.com/upstream/repo/compare/master...user:repo:pr-sample-opt?expand=1",
                "ghBranchUrl": "https://github.com/user/repo/tree/pr-sample-opt",
                "ghCommitUrl": "https://github.com/user/repo/commit/abc123def456",
                "problemDescription": "Describe the architectural defect or baseline limitation.",
                "solutionDescription": "Describe the architectural solution implemented in this PR.",
                "simplificationDeepDive": "Highlight what was simplified, pruned, or optimized.",
                "hunks": [
                    {
                        "file": "path/to/module.py",
                        "lines": "lines 10-25",
                        "shortSummary": "Replace inefficient loop with vectorized calculation",
                        "diffSnippet": "-    res = [compute(x) for x in arr]\n+    res = vectorized_compute(arr)",
                        "rationale": "Vectorized computation bypasses Python bytecode dispatch overhead.",
                        "alternatives": "Considered Cython, but vectorized NumPy avoids C-extension dependencies."
                    }
                ]
            }
        ]
    }

def main():
    parser = argparse.ArgumentParser(description="Generate Review Dashboard Manifest")
    parser.add_argument("--create-sample-manifest", help="Path to write sample PR manifest JSON")
    args = parser.parse_args()

    if args.create_sample_manifest:
        manifest = generate_sample_manifest()
        with open(args.create_sample_manifest, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        print(f"Sample manifest written to {args.create_sample_manifest}")
    else:
        print("Dual Review Dashboard Generator Utility. Use --create-sample-manifest <path> to generate a starter manifest.")

if __name__ == "__main__":
    main()
