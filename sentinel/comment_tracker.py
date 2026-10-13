#!/usr/bin/env python3
"""
GitHub Review Comments Scraper, Monitor & AI Triage Sentinel
=============================================================
Monitors open Pull Requests on gwastro/pycbc for reviewer comments (inline diff 
review comments, review summaries, and general PR timeline comments).
Performs AI triage and technical assessments for each comment, categorizes them, 
and logs structured data to data/github_comments.json.
"""

import os
import sys
import re
import json
import time
import html
import argparse
import urllib.request
import urllib.error

DEFAULT_CONFIG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "config.json")
)
DEFAULT_COMMENTS_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "github_comments.json")
)
NOTES_COMMENTS_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "search_dev_notes", "data", "github_comments.json")
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

PR_MAP = {
    5473: {"prId": "PR-1D", "branch": "pr-perf-waveform-compress"},
    5474: {"prId": "PR-1B", "branch": "pr-fix-io-dictarray-indexing"},
    5475: {"prId": "PR-1A", "branch": "pr-fix-core-numpy2-optparse"},
    5479: {"prId": "PR-1H", "branch": "pr-feat-inject-injfilter-optimal-snr"},
    5481: {"prId": "PR-1F", "branch": "pr-feat-events-eventmgr-multi"},
    5482: {"prId": "PR-1K", "branch": "pr-feat-frame-gwosc-hdf"},
}


def load_existing_comments(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_comments(filepath, data):
    for target in (filepath, NOTES_COMMENTS_FILE):
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[CommentTracker WARN] Could not save to {target}: {e}", file=sys.stderr)


def get_html(url, retries=3, delay=1.5):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read().decode("utf-8")
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2


def assess_comment(pr_num, comment_obj):
    """
    Generates technical AI assessment, recommendation, and suggested draft reply
    for a GitHub review comment based on its content, file context, and reviewer intent.
    """
    author = comment_obj.get("author", "")
    body = comment_obj.get("body", "").strip()
    path = comment_obj.get("path", "")
    is_bot = comment_obj.get("isBot", False) or "bot" in author.lower() or "security" in author.lower()

    if is_bot:
        return {
            "type": "BOT_FINDING",
            "category": "LINTER_CODE_QUALITY",
            "urgency": "LOW",
            "summary": f"Automated linter alert from {author}: {body[:120]}",
            "recommendation": "Check whether this unused import or linter alert should be cleaned up or added to `__all__`.",
            "suggestedAction": "INVESTIGATE_IF_REGRESSION",
            "targetFile": path,
            "draftResponse": "",
        }

    lower_body = body.lower()

    # PR 5475 assessments (spxiwh on optparse / config)
    if pr_num == 5475:
        if "isidentifier" in lower_body:
            return {
                "type": "SIMPLIFICATION_FEEDBACK",
                "category": "LOGIC_SIMPLIFICATION",
                "urgency": "HIGH",
                "summary": "Reviewer (@spxiwh) asks if `k.isidentifier()` makes the earlier check (`not k` or `k[0].isdigit()`) redundant.",
                "recommendation": "In Python, `''.isidentifier()` is False and `'1abc'.isidentifier()` is False. Therefore `if not k.isidentifier(): continue` completely subsumes the previous checks and is cleaner.",
                "suggestedAction": "CODE_FIX",
                "targetFile": path or "pycbc/types/config.py",
                "draftResponse": f"Good point @{author} — `k.isidentifier()` already returns `False` for empty strings and leading digits, so `if not k.isidentifier(): continue` replaces the other checks cleanly.",
            }
        elif "inline this in the 4 places" in lower_body or "need this function" in lower_body:
            return {
                "type": "DESIGN_INQUIRY",
                "category": "REFACTORING",
                "urgency": "MEDIUM",
                "summary": "Reviewer (@spxiwh) asks whether `to_int` helper function is needed or if it should be inlined at the 4 call sites.",
                "recommendation": "Note that `to_int` is passed as a callable converter (`self.internal_type = to_int` / `dtype = to_int`) in `optparse.py` where `type` is stored as a function reference, not just called directly. However, check if a simpler converter or inlining is cleaner.",
                "suggestedAction": "MAINTAINER_REVIEW",
                "targetFile": path or "pycbc/types/optparse.py",
                "draftResponse": f"@{author} Because `optparse` stores `type` / `internal_type` as a callable converter function (e.g. `val = self.internal_type(val)`), having `to_int` as a callable function lets us assign it directly in place of `int` without branching at every conversion call site.",
            }
        elif "if/else block setting" in lower_body or "internal_type" in lower_body:
            return {
                "type": "CODE_IMPROVEMENT",
                "category": "CONTROL_FLOW",
                "urgency": "HIGH",
                "summary": "Reviewer (@spxiwh) suggests restructuring the `if type is not None:` block in `MultiDetOptionAction.__init__` so `internal_type` (or `conv`) is set in a single clear `if/else` block.",
                "recommendation": "Adopt @spxiwh's cleaner structure:\n```python\nif type is not None:\n    self.internal_type = to_int if type is int else type\n    type = str\n```",
                "suggestedAction": "CODE_FIX",
                "targetFile": path or "pycbc/types/optparse.py",
                "draftResponse": f"Agreed @{author}, that structure is much cleaner. Updated both `MultiDetOptionAction` and `MultiDetOptionAppendAction` to set `self.internal_type` directly in that block.",
            }
        elif "similar comment here" in lower_body:
            return {
                "type": "CODE_IMPROVEMENT",
                "category": "CONTROL_FLOW",
                "urgency": "HIGH",
                "summary": "Reviewer (@spxiwh) requests applying the same `if type is not None:` cleanup to `MultiDetOptionAppendAction`.",
                "recommendation": "Apply the identical `self.internal_type` control-flow simplification here as well.",
                "suggestedAction": "CODE_FIX",
                "targetFile": path or "pycbc/types/optparse.py",
                "draftResponse": f"Updated here as well to match.",
            }
        elif "dtype" in lower_body and "to_int" in lower_body:
            return {
                "type": "CORRECTNESS_INQUIRY",
                "category": "TYPE_CONVERSION",
                "urgency": "MEDIUM",
                "summary": "Reviewer (@spxiwh) asks where `check_ReadBy` / `dtype` is called and whether `dtype` could already be `to_int` instead of `int`.",
                "recommendation": "Check all callers of this helper in `pycbc/types/optparse.py`. If `self.internal_type` is already set to `to_int` in `__init__`, then checking `if dtype is int:` later would be either redundant or missed if `dtype` is `self.internal_type`. Consolidating the conversion in `__init__` removes the need to re-check `dtype is int` downstream.",
                "suggestedAction": "CODE_FIX",
                "targetFile": path or "pycbc/types/optparse.py",
                "draftResponse": f"Good catch @{author} — since `self.internal_type` is already assigned `to_int` when `type is int`, we don't need a second `dtype is int` check here.",
            }

    # PR 5479 assessments (spxiwh on injfilterrejector)
    if pr_num == 5479:
        if "removing the older code" in lower_body or "lgtm" in lower_body:
            return {
                "type": "SIMPLIFICATION_FEEDBACK",
                "category": "DEAD_CODE_REMOVAL",
                "urgency": "MEDIUM",
                "summary": "Reviewer (@spxiwh) approves the PR (LGTM) and suggests considering removing the older low-SNR injection rejection code if superseded.",
                "recommendation": "Evaluate whether the legacy post-generation SNR check is now completely redundant with the pre-filtering optimal SNR cut, or if both serve distinct stages.",
                "suggestedAction": "MAINTAINER_REVIEW",
                "targetFile": path or "pycbc/inject/injfilterrejector.py",
                "draftResponse": f"Thanks @{author}! Regarding the older low-SNR rejection code: let's check if any workflows still rely on the old path when optimal SNR pre-filtering is disabled, or if we should remove it here.",
            }
        elif "precisions match" in lower_body or "psd.precision" in lower_body:
            return {
                "type": "CORRECTNESS_INQUIRY",
                "category": "PRECISION_HANDLING",
                "urgency": "HIGH",
                "summary": "Reviewer (@spxiwh) notes that checking `ht.precision != psd.precision` and then unconditionally converting `psd = psd.astype(np.float32)` assumes `ht` is single precision.",
                "recommendation": "Either check `if psd.precision != ht.precision: psd = psd.astype(ht.dtype)` so `psd` always matches `ht`'s actual precision, or explicitly check `if psd.precision == 'double':` if single precision is always intended.",
                "suggestedAction": "CODE_FIX",
                "targetFile": path or "pycbc/inject/injfilterrejector.py",
                "draftResponse": f"Good catch @{author}. Casting `psd = psd.astype(ht.dtype)` (or matching `ht.precision`) directly is much cleaner and doesn't assume `ht` is always float32.",
            }
        elif "short_injection" in lower_body:
            return {
                "type": "CORRECTNESS_INQUIRY",
                "category": "PHYSICS_VALIDATION",
                "urgency": "HIGH",
                "summary": "Reviewer (@spxiwh) asks whether it is right to use `short_injection` at line 294.",
                "recommendation": "Inspect line 294 of `pycbc/inject/injfilterrejector.py` to verify whether `short_injection` (truncated waveform buffer) vs full injection waveform should be used when computing the optimal SNR / template overlap.",
                "suggestedAction": "MAINTAINER_REVIEW",
                "targetFile": path or "pycbc/inject/injfilterrejector.py",
                "draftResponse": f"Let me double-check the truncation bounds on `short_injection` here to make sure no significant SNR contribution is clipped.",
            }
        elif "underflow" in lower_body or "dynamic rescale" in lower_body:
            return {
                "type": "CORRECTNESS_INQUIRY",
                "category": "NUMERICAL_STABILITY",
                "urgency": "HIGH",
                "summary": "Reviewer (@spxiwh) asks if there is a risk of float32 underflow in `sigma` / optimal SNR calculation when dynamic rescaling (`dyn_range_fac`) is not used.",
                "recommendation": "Strain / PSD values without `dyn_range_fac` are ~1e-44 in SI units, which underflows IEEE 754 float32 (~1.4e-45) if squared or divided in single precision without rescaling! Ensure PSD/waveform inner products either use float64 accumulator or apply `dyn_range_fac`.",
                "suggestedAction": "CODE_FIX",
                "targetFile": path or "pycbc/inject/injfilterrejector.py",
                "draftResponse": f"Very good point @{author} — in unscaled SI strain units (~1e-22), PSD values (~1e-44) are right at the float32 underflow limit. Keeping the PSD / inner product accumulation in float64 (or scaling by `dyn_range_fac`) avoids float32 underflow.",
            }

    # PR 5481 assessments (GarethCabournDavies & spxiwh on eventmgr)
    if pr_num == 5481:
        if "strip the leading" in lower_body or "rstrip" in lower_body:
            return {
                "type": "CORRECTNESS_INQUIRY",
                "category": "PATH_HANDLING",
                "urgency": "HIGH",
                "summary": "Reviewer (@GarethCabournDavies) warns that `.strip('/')` strips the leading `/` on HDF5 paths and suggests `.rstrip('/')`.",
                "recommendation": "Adopt reviewer's suggestion to use `.rstrip('/')` (already applied in latest commit `633748969c`).",
                "suggestedAction": "CODE_FIX",
                "targetFile": path or "pycbc/events/eventmgr.py",
                "draftResponse": f"Fixed in `633748969c` using `.rstrip('/')` as suggested.",
            }
        elif "prefix is already" in lower_body:
            return {
                "type": "SIMPLIFICATION_FEEDBACK",
                "category": "CODE_CLEANLINESS",
                "urgency": "MEDIUM",
                "summary": "Reviewer (@GarethCabournDavies) points out `prefix` already defaults to `''`.",
                "recommendation": "Remove redundant default handling (already simplified in `633748969c`).",
                "suggestedAction": "CODE_FIX",
                "targetFile": path or "pycbc/events/eventmgr.py",
                "draftResponse": f"Simplified in `633748969c`, thanks!",
            }
        elif "h1_events" in lower_body:
            return {
                "type": "STYLE_READABILITY",
                "category": "VARIABLE_NAMING",
                "urgency": "LOW",
                "summary": "Reviewer (@GarethCabournDavies) suggests reusing `h1_events`, `l1_events`, `v1_events` in `test/test_eventmgr.py` so arrays are defined once.",
                "recommendation": "Define `h1_events`, `l1_events`, `v1_events` once and reference them in both the dict and assertions (done in `633748969c`).",
                "suggestedAction": "CODE_FIX",
                "targetFile": path or "test/test_eventmgr.py",
                "draftResponse": f"Updated in `633748969c` to reuse `h1_events`, `l1_events`, and `v1_events`.",
            }
        elif "allclose" in lower_body and "hash" in lower_body:
            return {
                "type": "CORRECTNESS_BUGFIX",
                "category": "TEST_SEMANTICS",
                "urgency": "MEDIUM",
                "summary": "Reviewer (@GarethCabournDavies) notes `assert_allclose` doesn't make sense on integer template hashes.",
                "recommendation": "Use `np.testing.assert_array_equal` for integer template hashes (done in `633748969c`).",
                "suggestedAction": "CODE_FIX",
                "targetFile": path or "test/test_eventmgr.py",
                "draftResponse": f"Switched to `assert_array_equal` in `633748969c`.",
            }
        elif "deletes the pointer" in lower_body or "warning here" in lower_body:
            return {
                "type": "CORRECTNESS_INQUIRY",
                "category": "MEMORY_MANAGEMENT",
                "urgency": "MEDIUM",
                "summary": "Reviewer (@spxiwh) asks if `gwstrain[ifo]._data = numpy.array([])` warrants a comment/warning since it mutates the underlying TimeSeries buffer in place.",
                "recommendation": "Add a clear inline comment explaining that `gwstrain[ifo]` is no longer used in the time domain after Fourier segments are precomputed, and clearing `_data` frees the raw strain buffer before multiprocessing fork.",
                "suggestedAction": "CODE_FIX",
                "targetFile": path or "pycbc/events/eventmgr.py",
                "draftResponse": f"Good point @{author} — added a clear comment noting that `gwstrain` time-domain data is not accessed after this point and is cleared specifically to release memory.",
            }

    # PR 5474 assessments (GarethCabournDavies on hdf.py)
    if pr_num == 5474:
        if "logic_arr" in lower_body:
            return {
                "type": "NAMING_CLARITY",
                "category": "READABILITY",
                "urgency": "LOW",
                "summary": "Reviewer (@GarethCabournDavies) suggests naming the boolean mask array `logic_arr`.",
                "recommendation": "Rename variable to `logic_arr` if desired.",
                "suggestedAction": "CODE_FIX",
                "targetFile": path or "pycbc/io/hdf.py",
                "draftResponse": f"Happy to rename this to `logic_arr`.",
            }
        elif "dont really understand what this line is actually doing" in lower_body or "wrong with it before" in lower_body:
            return {
                "type": "EXPLANATION_NEEDED",
                "category": "TECHNICAL_EXPLANATION",
                "urgency": "LOW",
                "summary": "Reviewer (@GarethCabournDavies) asked why `np.asanyarray` was added; @ahnitz already replied on GitHub explaining edge cases with empty/single-element lists.",
                "recommendation": "Already answered on GitHub by @ahnitz.",
                "suggestedAction": "NO_ACTION_NEEDED",
                "targetFile": path or "pycbc/io/hdf.py",
                "draftResponse": "",
            }

    # PR 5473 assessments (GarethCabournDavies on waveform_compress)
    if pr_num == 5473:
        if "thinking out loud" in lower_body or "fine left as it is" in lower_body:
            return {
                "type": "RESOLVED_DISCUSSION",
                "category": "INFORMATIONAL",
                "urgency": "NONE",
                "summary": "Reviewer (@GarethCabournDavies) confirmed the out-of-order sample point question is fine left as-is.",
                "recommendation": "No code changes required.",
                "suggestedAction": "NO_ACTION_NEEDED",
                "targetFile": path or "test/test_waveform_compress.py",
                "draftResponse": "",
            }
        elif "2 really a sensible number" in lower_body or "0.98 seems a little low" in lower_body or "wired into the test above" in lower_body:
            return {
                "type": "TEST_IMPROVEMENT",
                "category": "UNIT_TESTS",
                "urgency": "LOW",
                "summary": f"Reviewer (@{author}) commented on test parameters/structure in `test/test_waveform_compress.py`.",
                "recommendation": "Tighten test assertions (`len(sample_points) > 10`, `min_mismatch > 0.999`) and combine duplicate test loops (already updated on branch).",
                "suggestedAction": "NO_ACTION_NEEDED",
                "targetFile": path or "test/test_waveform_compress.py",
                "draftResponse": "",
            }

    return {
        "type": "GENERAL_FEEDBACK",
        "category": "CODE_REVIEW",
        "urgency": "MEDIUM",
        "summary": f"Comment from @{author}: {body[:120]}",
        "recommendation": "Review comment in context of the diff hunk below.",
        "suggestedAction": "MAINTAINER_REVIEW",
        "targetFile": path,
        "draftResponse": "",
    }


def scrape_pr_comments(pr_num):
    url = f"https://github.com/gwastro/pycbc/pull/{pr_num}"
    try:
        page_html = get_html(url)
    except Exception as e:
        print(f"[CommentTracker] Failed to load PR #{pr_num}: {e}")
        return []

    comments = []

    # 1. Inline review discussions (id="discussion_r\d+")
    matches = list(re.finditer(r'id=\"(discussion_r\d+)\"', page_html))
    last_path = ""
    last_line = ""
    last_snippet = ""

    for i, m in enumerate(matches):
        cid = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else min(len(page_html), start + 8000)
        block = page_html[start:end]

        # Check if there is an embedded react-partial automated comment (e.g. qlty / github-advanced-security)
        m_react = re.search(
            r'<script type=\"application/json\" data-target=\"react-partial\.embeddedData\">(\{.*?\})</script>',
            block,
        )
        author = "unknown"
        avatar = ""
        dt = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        body = ""
        is_bot = False

        if m_react:
            try:
                rdata = json.loads(m_react.group(1))
                c_props = rdata.get("props", {}).get("comment", {})
                auth_obj = c_props.get("author", {})
                author = auth_obj.get("login", "github-advanced-security[bot]")
                avatar = auth_obj.get("avatarUrl", "")
                auto_c = c_props.get("automatedComment", {})
                raw_msg = auto_c.get("message", "")
                title = auto_c.get("title", "")
                clean_msg = re.sub(r"<[^<]+?>", "", html.unescape(raw_msg)).strip()
                body = f"[{title}] {clean_msg}" if title else clean_msg
                is_bot = True
            except Exception:
                pass

        if not body:
            m_author = re.search(r'alt=\"@([^\"]+)\"', block)
            if m_author:
                author = m_author.group(1)
            else:
                m_author2 = re.search(r'class=\"author[^\"]*\"[^>]*>([^<]+)</a>', block)
                if m_author2:
                    author = m_author2.group(1).strip()

            m_avatar = re.search(r'<img[^>]*src=\"([^\"]+)\"[^>]*alt=\"@[^\"]+\"', block)
            avatar = m_avatar.group(1) if m_avatar else ""

            m_dt = re.search(r'<relative-time[^>]*datetime=\"([^\"]+)\"', block)
            if m_dt:
                dt = m_dt.group(1)

            m_val = re.search(r'<clipboard-copy[^>]*value=\"([^\"]+)\"', block)
            if m_val:
                body = html.unescape(m_val.group(1)).replace("\\n", "\n")
            else:
                m_body = re.search(
                    r'<div class=\"[^\"]*comment-body[^\"]*\">(.*?)</div>', block, re.DOTALL
                )
                if m_body:
                    body = re.sub(r"<[^<]+?>", "", html.unescape(m_body.group(1))).strip()

            is_bot = "bot" in author.lower() or "security" in author.lower()

        # Look between previous discussion_r and this one for file path & line number
        pre_start = matches[i - 1].start() if i > 0 else max(0, start - 6000)
        pre_chunk = page_html[pre_start:start]

        file_m = re.findall(r'<a [^>]*>([a-zA-Z0-9_\-\./]+\.py)</a>', pre_chunk)
        if file_m:
            last_path = file_m[-1]

        lines_m = re.findall(r'data-line-number=\"(\d+)\"', pre_chunk)
        if lines_m:
            last_line = f"line {lines_m[-1]}"

        diff_lines = re.findall(r'<span class=\"blob-code-inner[^\"]*\">([^<]+)</span>', pre_chunk)
        if diff_lines:
            last_snippet = "\n".join(html.unescape(dl) for dl in diff_lines[-3:])

        if not body:
            continue

        c_obj = {
            "id": cid,
            "prNumber": pr_num,
            "type": "inline_review",
            "author": author,
            "authorAvatar": avatar,
            "isBot": is_bot,
            "createdAt": dt,
            "path": last_path,
            "lines": last_line,
            "diffSnippet": last_snippet,
            "body": body,
            "htmlUrl": f"https://github.com/gwastro/pycbc/pull/{pr_num}#{cid}",
            "status": "NEEDS_TRIAGE",
        }
        c_obj["aiAssessment"] = assess_comment(pr_num, c_obj)
        comments.append(c_obj)

    # 2. Top-level PR review summaries (id="pullrequestreview-\d+")
    rev_matches = list(re.finditer(r'id=\"(pullrequestreview-\d+)\"', page_html))
    for i, m in enumerate(rev_matches):
        rid = m.group(1)
        start = m.start()
        end = rev_matches[i + 1].start() if i + 1 < len(rev_matches) else min(len(page_html), start + 6000)
        block = page_html[start:end]

        m_val = re.search(r'<clipboard-copy[^>]*value=\"([^\"]+)\"', block)
        if not m_val:
            continue
        # Ensure this clipboard-copy belongs to the review summary itself and not an inner discussion_r
        val_pos = block.find(m_val.group(0))
        if block[:val_pos].find('id="discussion_r') != -1:
            continue

        body = html.unescape(m_val.group(1)).replace("\\n", "\n").strip()
        if not body:
            continue

        m_author = re.search(r'alt=\"@([^\"]+)\"', block)
        author = m_author.group(1) if m_author else "unknown"
        m_avatar = re.search(r'<img[^>]*src=\"([^\"]+)\"[^>]*alt=\"@[^\"]+\"', block)
        avatar = m_avatar.group(1) if m_avatar else ""
        m_dt = re.search(r'<relative-time[^>]*datetime=\"([^\"]+)\"', block)
        dt = m_dt.group(1) if m_dt else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        c_obj = {
            "id": rid,
            "prNumber": pr_num,
            "type": "review_summary",
            "author": author,
            "authorAvatar": avatar,
            "isBot": False,
            "createdAt": dt,
            "path": "",
            "lines": "",
            "diffSnippet": "",
            "body": body,
            "htmlUrl": f"https://github.com/gwastro/pycbc/pull/{pr_num}#{rid}",
            "status": "NEEDS_TRIAGE",
        }
        c_obj["aiAssessment"] = assess_comment(pr_num, c_obj)
        comments.append(c_obj)

    # 3. General issue/PR comments (id="issuecomment-...")
    issue_matches = list(re.finditer(r'id=\"(issuecomment-\d+)\"', page_html))
    for i, m in enumerate(issue_matches):
        cid = m.group(1)
        start = m.start()
        end = issue_matches[i + 1].start() if i + 1 < len(issue_matches) else min(len(page_html), start + 6000)
        block = page_html[start:end]
        if block.find('id="discussion_r') != -1 or block.find('id="pullrequestreview-') != -1:
            # Make sure we only look before any nested blocks
            nested_pos = min(
                [p for p in (block.find('id="discussion_r'), block.find('id="pullrequestreview-')) if p != -1]
            )
            block = block[:nested_pos]

        m_val = re.search(r'<clipboard-copy[^>]*value=\"([^\"]+)\"', block)
        if not m_val:
            continue
        body = html.unescape(m_val.group(1)).replace("\\n", "\n").strip()
        if not body:
            continue

        m_author = re.search(r'alt=\"@([^\"]+)\"', block)
        author = m_author.group(1) if m_author else "unknown"
        if author == "ahnitz" and ("This updates" in body or "code of conduct" in body):
            continue

        m_avatar = re.search(r'<img[^>]*src=\"([^\"]+)\"[^>]*alt=\"@[^\"]+\"', block)
        avatar = m_avatar.group(1) if m_avatar else ""
        m_dt = re.search(r'<relative-time[^>]*datetime=\"([^\"]+)\"', block)
        dt = m_dt.group(1) if m_dt else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        c_obj = {
            "id": cid,
            "prNumber": pr_num,
            "type": "general_comment",
            "author": author,
            "authorAvatar": avatar,
            "isBot": "bot" in author.lower() or "security" in author.lower(),
            "createdAt": dt,
            "path": "",
            "lines": "",
            "diffSnippet": "",
            "body": body,
            "htmlUrl": f"https://github.com/gwastro/pycbc/pull/{pr_num}#{cid}",
            "status": "NEEDS_TRIAGE",
        }
        c_obj["aiAssessment"] = assess_comment(pr_num, c_obj)
        comments.append(c_obj)

    return comments


def poll_all_prs(comments_file=DEFAULT_COMMENTS_FILE):
    existing = load_existing_comments(comments_file)
    results = {}
    total_comments = 0
    total_human = 0

    for pr_num in PR_MAP.keys():
        pr_comments = scrape_pr_comments(pr_num)
        pr_str = str(pr_num)

        merged_comments = []
        old_comments_map = {
            c["id"]: c for c in existing.get("prs", {}).get(pr_str, {}).get("comments", [])
        }

        for c in pr_comments:
            if c["id"] in old_comments_map:
                old = old_comments_map[c["id"]]
                if "userStatus" in old:
                    c["userStatus"] = old["userStatus"]
                if "userNote" in old:
                    c["userNote"] = old["userNote"]
                if "actionStatus" in old:
                    c["actionStatus"] = old["actionStatus"]
                if "actionHistory" in old:
                    c["actionHistory"] = old["actionHistory"]
                if "replies" in old:
                    c["replies"] = old["replies"]
            merged_comments.append(c)

        human_count = sum(1 for c in merged_comments if not c["isBot"])
        bot_count = len(merged_comments) - human_count
        total_comments += len(merged_comments)
        total_human += human_count

        results[pr_str] = {
            "prNumber": pr_num,
            "prId": PR_MAP[pr_num]["prId"],
            "branch": PR_MAP[pr_num]["branch"],
            "totalCount": len(merged_comments),
            "humanCount": human_count,
            "botCount": bot_count,
            "comments": merged_comments,
            "lastChecked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    output_data = {
        "lastUpdated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "totalComments": total_comments,
        "totalHumanComments": total_human,
        "prs": results,
    }

    save_comments(comments_file, output_data)
    return output_data


def main():
    parser = argparse.ArgumentParser(description="GitHub Review Comments Monitor & AI Triage")
    parser.add_argument("--watch", action="store_true", help="Continuously poll in a background loop")
    parser.add_argument("--interval", type=int, default=60, help="Poll interval in seconds (default 60)")
    parser.add_argument("--file", default=DEFAULT_COMMENTS_FILE, help="Path to output JSON file")
    args = parser.parse_args()

    print("[CommentTracker] Initializing GitHub Comment Tracker...")
    data = poll_all_prs(args.file)
    print(
        f"[CommentTracker] Initial pass complete: {data['totalComments']} comments across {len(data['prs'])} PRs ({data['totalHumanComments']} human reviews)."
    )

    if args.watch:
        print(f"[CommentTracker] Entering watch loop (interval: {args.interval}s)...")
        while True:
            time.sleep(args.interval)
            try:
                data = poll_all_prs(args.file)
                print(
                    f"[CommentTracker] [{time.strftime('%H:%M:%S')}] Polled {len(data['prs'])} PRs: {data['totalComments']} comments ({data['totalHumanComments']} human)."
                )
            except Exception as e:
                print(f"[CommentTracker ERROR] Poll cycle failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
