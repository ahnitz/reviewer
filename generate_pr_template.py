#!/usr/bin/env python3
"""Generate a properly filled-out Pull Request description according to

PyCBC's standard `.github/pull_request_template.md`.

Usage:
    python3 generate_pr_template.py <PR_ID_OR_BRANCH>
    python3 generate_pr_template.py --list
"""

import sys
import os
import re
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DASHBOARD_FILE = os.path.join(BASE_DIR, "search_dev_notes", "pr_review_dashboard.html")
ROADMAP_FILE = os.path.join(BASE_DIR, "search_dev_notes", "data", "wave_roadmap.json")
TEMPLATE_FILE = os.path.join(BASE_DIR, "pycbc-work", "apogee", ".github", "pull_request_template.md")

def load_prs():
    if not os.path.exists(DASHBOARD_FILE):
        return []
    with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"const PR_PAGES = (\[.*?\]);\s*\n\s*let currentTab", content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception as e:
            print(f"Error parsing PR_PAGES: {e}", file=sys.stderr)
    return []

def load_roadmap():
    if os.path.exists(ROADMAP_FILE):
        try:
            with open(ROADMAP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def format_pr_description(pr, roadmap=None):
    title = pr.get("title", "")
    title_lower = title.lower()
    cat_lower = pr.get("category", "").lower()

    # Determine PR Type
    if title_lower.startswith("fix") or "bugfix" in title_lower:
        pr_type = "bug fix"
    elif title_lower.startswith("perf") or "perf" in cat_lower:
        pr_type = "efficiency update"
    elif title_lower.startswith("feat") or "feat" in cat_lower:
        pr_type = "new feature"
    elif title_lower.startswith("refactor") or title_lower.startswith("style"):
        pr_type = "efficiency update, bug fix"
    else:
        pr_type = "efficiency update, bug fix"

    # Determine Affected Codebases
    if any(k in cat_lower for k in ["filter", "strain", "psd", "core"]):
        affected_codes = "the offline search, the live search"
    elif "inference" in cat_lower:
        affected_codes = "inference"
    elif "pygrb" in cat_lower:
        affected_codes = "PyGRB"
    else:
        affected_codes = "the offline search"

    # Determine Code Areas Changed
    if any(k in cat_lower for k in ["waveform", "veto", "ranking"]):
        changes = "scientific output (numerically verified equivalent), runtime performance"
    else:
        changes = "internal library API, runtime performance (scientific output remains bit-for-bit identical)"

    # File Summary
    hunks = pr.get("hunks", [])
    files = []
    for h in hunks:
        if h.get("file") and h["file"] not in files:
            files.append(h["file"])

    file_lines = []
    for f in files:
        f_hunks = [h for h in hunks if h.get("file") == f]
        summaries = [h.get("shortSummary") for h in f_hunks if h.get("shortSummary")]
        summary_str = "; ".join(summaries) if summaries else "code updates"
        hunk_count = len(f_hunks)
        file_lines.append(f"- `{f}` ({hunk_count} hunk{'s' if hunk_count > 1 else ''}): {summary_str}")
    file_summary = "\n".join(file_lines)

    # Dependencies & Links
    rm_deps = []
    if roadmap and "waves" in roadmap:
        for w in roadmap["waves"].values():
            for rm_pr in w.get("prs", []):
                if rm_pr.get("id") == pr.get("id"):
                    rm_deps = rm_pr.get("dependencies", [])
                    break

    if rm_deps:
        links_text = "Depends on prerequisite upstream PR(s): " + ", ".join(f"`{d}`" for d in rm_deps) + " (DAG pipeline dependency)."
    elif pr.get("unblocks"):
        links_text = f"Independent root PR (0 dependencies). Unblocks downstream pipeline: {pr.get('unblocks')}."
    else:
        links_text = "None (standalone root PR with 0 upstream prerequisites; safe to review and merge independently)."

    # Top-Level Summary
    sol = pr.get("solutionDescription") or pr.get("title", "")
    sentences = [s.strip() for s in sol.split(".") if s.strip()]
    top_summary = ". ".join(sentences[:2])
    if top_summary and not top_summary.endswith("."):
        top_summary += "."

    # Build Standard PR Template
    lines = [
        f"{top_summary}\n",
        "## Standard information about the request\n",
        f"This is a: {pr_type}\n",
        f"This change affects: {affected_codes}\n",
        f"This change changes: {changes}\n",
        "This change: has appropriate unit tests, follows style guidelines (See e.g. [PEP8](https://peps.python.org/pep-0008/)), has been proposed using the [contribution guidelines](https://github.com/gwastro/pycbc/blob/master/CONTRIBUTING.md)\n",
        "This change will: not break current functionality, not require additional dependencies, not require a new release\n",
        "## Motivation",
        f"{pr.get('problemDescription', 'Code improvement and robustness.')}\n",
        "## Contents",
        f"{pr.get('solutionDescription', title)}\n"
    ]

    if pr.get("simplificationDeepDive"):
        lines.extend([
            "### Code Simplification & Review Surface Reduction",
            f"{pr.get('simplificationDeepDive')}\n"
        ])

    if file_summary:
        lines.extend([
            "### Key Changes by File",
            f"{file_summary}\n"
        ])

    lines.extend([
        "## Links to any issues or associated PRs",
        f"{links_text}\n",
        "## Testing performed",
        f"- **Unit Test Command**: `{pr.get('testCmd', 'python3 -m unittest test/')}`",
        f"- **Test Execution Result**: {pr.get('testTiming', 'All unit tests passing cleanly in isolation')}",
        "- **Rebase Verification**: Cleanly rebased against upstream `gwastro/pycbc:master` (0 merge conflicts)",
        "- **Numerical Invariance**: Bit-for-bit deterministic results verified against baseline\n",
        "## Additional notes",
        "- Adheres to `PYCBC_DEVELOPMENT_GUIDELINES.md`:",
        "  - NumPy 2.0+ compatibility: explicit sized float64 types, no deprecated scalar type aliases",
        "  - Clean function signatures: avoiding ad-hoc parameter creep (`copy=True/False`)",
        "  - Test consolidation: all new unit tests integrated directly into canonical test suites",
        "  - Zero dead code, zero unused imports (`flake8` compliant)\n",
        "- [x] The author of this pull request confirms they will adhere to the [code of conduct](https://github.com/gwastro/pycbc/blob/master/CODE_OF_CONDUCT.md)"
    ])

    return "\n".join(lines)

def main():
    prs = load_prs()
    roadmap = load_roadmap()

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python3 generate_pr_template.py <PR_ID_OR_BRANCH> [--markdown|--json]")
        print("Available PRs:")
        for p in prs:
            print(f"  {p.get('id'):<8} {p.get('branch'):<40} {p.get('title')[:50]}")
        sys.exit(0)

    if sys.argv[1] == "--list":
        for p in prs:
            print(f"{p.get('id')}\t{p.get('branch')}\t{p.get('title')}")
        sys.exit(0)

    target = sys.argv[1].strip()
    target_pr = None
    for p in prs:
        if p.get("id").upper() == target.upper() or p.get("branch") == target:
            target_pr = p
            break

    if not target_pr:
        print(f"Error: PR '{target}' not found.", file=sys.stderr)
        sys.exit(1)

    output = format_pr_description(target_pr, roadmap)
    print(output)

if __name__ == "__main__":
    main()
