# PyCBC Code Review Rules, Design Principles & Conventions

*Extracted and codified from maintainer review feedback on the 21-PR Decomposition Tree (Waves 1–4).*

---

## Executive Summary

This document establishes the governing engineering standards, numerical safety invariants, and architectural conventions for all Pull Requests in the PyCBC tree. These principles were systematically inferred from maintainer review comments and ensure that every PR is robust, high-performance, review-proof, and seamless to merge into `gwastro/pycbc:master`.

---

## Table of Contents
1. [Rule 1: Integer Precision & Strict String Coercion](#rule-1-integer-precision--strict-string-coercion)
2. [Rule 2: Explicit Mutation Semantics & Copy Isolation](#rule-2-explicit-mutation-semantics--copy-isolation)
3. [Rule 3: Standard Scientific Python Ecosystem Conventions](#rule-3-standard-scientific-python-ecosystem-conventions)
4. [Rule 4: Sparse vs. Dense Data Representation in Pipeline Filtering](#rule-4-sparse-vs-dense-data-representation-in-pipeline-filtering)
5. [Rule 5: Extended Precision for Large-Offset Astronomical Time Arithmetic](#rule-5-extended-precision-for-large-offset-astronomical-time-arithmetic)
6. [Rule 6: Discrete Grid Resolution & Interval Singularity Guards](#rule-6-discrete-grid-resolution--interval-singularity-guards)
7. [Rule 7: Proactive Unit Test Coverage for Core Algorithmic Components](#rule-7-proactive-unit-test-coverage-for-core-algorithmic-components)
8. [Rule 8: Early Slicing & Transient Memory Spikes in Precomputed Kernels](#rule-8-early-slicing--transient-memory-spikes-in-precomputed-kernels)
9. [Rule 9: Upstream Root-Cause Resolution vs. Inner-Loop Bandaids](#rule-9-upstream-root-cause-resolution-vs-inner-loop-bandaids)
10. [Rule 10: Canonical Reference Code Isolation](#rule-10-canonical-reference-code-isolation)
11. [Rule 11: Domain-Accurate Parameter Naming in Storage Hierarchies](#rule-11-domain-accurate-parameter-naming-in-storage-hierarchies)
12. [Rule 12: Avoid Namespace Redundancy / Stuttering in Function and Class Names](#rule-12-avoid-namespace-redundancy--stuttering-in-function-and-class-names)
13. [Rule 13: Upstream PR Auditing & Rebase Synchronization](#rule-13-upstream-pr-auditing--rebase-synchronization)
14. [Rule 14: Continuous Upstream Rebase Tracking & Multi-Branch Synchronization](#rule-14-continuous-upstream-rebase-tracking--multi-branch-synchronization)
15. [Rule 15: Comprehensive Review Hunk Visibility, Dynamic Test Discovery & Context Linking](#rule-15-comprehensive-review-hunk-visibility-dynamic-test-discovery--context-linking)
16. [Rule 16: Mandatory Architectural Rationale, Problem Statement & Alternatives for Every Code Modification & Refactoring](#rule-16-mandatory-architectural-rationale-problem-statement--alternatives-for-every-code-modification--refactoring)
17. [Rule 17: Strict Triple-Dot Diff Semantics Against Upstream Master & Stable Comment Anchoring](#rule-17-strict-triple-dot-diff-semantics-against-upstream-master--stable-comment-anchoring)

---

## Rule 1: Integer Precision & Strict String Coercion

### Core Rationale
When parsing command-line options or configuration files, naive conversion patterns like `int(float(value))` are hazardous:
1. **Silent Truncation**: Fractional inputs (`"2048.5"`) are silently truncated to `2048`, hiding configuration errors.
2. **Precision Loss**: Standard IEEE 754 `float64` only has 53 bits of precision. Large integer values $\ge 2^{53} \approx 9 \times 10^{15}$ (such as GPS nanosecond timestamps, high-entropy random seeds, or 64-bit hash keys) lose their lowest bits when passed through `float()`.

### Implementation Standard
Always use a safe two-stage conversion that preserves arbitrary-precision integers and verifies whole-number values:

```python
def to_int(value):
    """Safely convert a string, int, or float to an integer.
    
    Preserves exact arbitrary-precision integers (>= 2^53), permits exact
    whole-number floats/scientific notation ('1e3', 2048.0), and strictly
    raises ValueError on fractional numbers ('2048.5').
    """
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    try:
        f = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"Cannot parse integer from {value!r}")
    if not f.is_integer():
        raise ValueError(f"Non-integer value {value!r} cannot be safely converted to int")
    return int(f)
```

---

## Rule 2: Explicit Mutation Semantics & Copy Isolation

### Core Rationale
Data structures in PyCBC (`TimeSeries`, `FrequencySeries`, `DictArray`, `StatmapData`) are often passed through multiple analysis modules. Methods that silently mutate inputs in-place cause subtle aliasing bugs in downstream callers. Conversely, forcing deep copies everywhere introduces significant memory overhead in high-throughput inner loops.

### Implementation Standard
1. **Default to Safe Copies**: All transformation or selection methods must default to returning a new instance (`copy=True` or `inplace=False`).
2. **Explicit Opt-in**: Provide an explicit keyword argument (`copy=False` or `inplace=True`) for performance-critical inner loops where the caller explicitly requests zero-allocation mutation.
3. **No Arithmetic Hacks**: Never use expressions like `timeseries * 1` to create copies. Always use explicit `.copy()`.

```python
# GOOD: Safe default with explicit in-place opt-in
def resample_to_delta_t(timeseries, delta_t, method='ldas', copy=True):
    if timeseries.delta_t == delta_t:
        return timeseries.copy() if copy else timeseries
    # ...
```

---

## Rule 3: Standard Scientific Python Ecosystem Conventions

### Core Rationale
PyCBC interfaces directly with NumPy, SciPy, Pandas, and Astropy. Non-standard naming conventions (such as `in_place` instead of `inplace`) increase cognitive friction and lead to API inconsistencies.

### Implementation Standard
1. **Standardize on Ecosystem Precedents**: Use `inplace` without underscores across all classes and functions (matching Pandas, SciPy, and PyCBC's internal `pycbc/fft` modules).
2. **Maintain Deprecated Aliases**: When renaming or standardizing existing parameter names, preserve the legacy variant as a non-breaking alias (`in_place=None`):

```python
def select(self, idx, inplace=False, in_place=None):
    if in_place is not None:
        inplace = in_place
    # ...
```

---

## Rule 4: Sparse vs. Dense Data Representation in Pipeline Filtering

### Core Rationale
In gravitational-wave search pipelines, trigger density varies wildly between background noise and astrophysical events. In sparse trigger regimes (e.g., 20 loud triggers surviving out of $10^6$ time steps), passing or converting to dense boolean arrays allocates multi-megabyte buffers and forces full memory scans.

### Implementation Standard
1. Masking methods (`apply_mask`) must natively support **both** dense boolean masks and sparse integer index arrays without forcing conversions.
2. In-place slicing of index arrays must preserve the caller's data structure:
   - If `mask` is a list of integer indices: `self.mask = list(numpy.array(self.mask)[logic_mask])`.
   - If `mask` is a dense boolean array: `self.mask[self.mask] = logic_mask`.
3. Guard against empty lists (`len(logic_mask) > 0`) to allow zero-allocation no-ops on empty triggers.

---

## Rule 5: Extended Precision for Large-Offset Astronomical Time Arithmetic

### Core Rationale
In coincidence analysis across multi-detector networks, artificial time slides are applied to measure background false-alarm rates. Timestamps are shifted by:
$$\text{time}_{\text{slide}} = \text{time} + \text{span} \times \text{tslide}$$
When $\text{tslide} \sim 10^3$ and $\text{span} \sim 10^7$ s, the offset scales to $\sim 10^{10}$ s. Under standard IEEE 754 `float64` (53 mantissa bits):
$$\text{ulp}(10^{10}) = 2^{-53} \times 10^{10} \approx 1.11 \times 10^{-6}\text{ s} = 1.11\,\mu\text{s}$$
This $1\,\mu\text{s}$ quantization noise introduces artificial jitter into coincidence clustering windows that operate at sub-millisecond tolerances.

### Implementation Standard
Always use extended precision (`numpy.longdouble`) for time-slide offsets and coincidence clustering calculations:
- On x86_64, `numpy.longdouble` uses 80-bit x87 extended precision (64-bit significand, 19 decimal digits), reducing quantization noise to $< 1\text{ ns}$.
- On ARM64 / PowerPC, `numpy.longdouble` provides 128-bit quadruple precision.

```python
# GOOD: Extended precision for large time slides
tslide = numpy.array(tslide, dtype=numpy.longdouble)
time = numpy.array(time, dtype=numpy.longdouble) + span * tslide
```

---

## Rule 6: Discrete Grid Resolution & Interval Singularity Guards

### Core Rationale
In numerical algorithms operating on discrete grids (such as greedy waveform compression or adaptive frequency sampling), frequency intervals have a fundamental resolution limit $\Delta f$. When an interval narrows to $\le 1$ sample point:
- The inner product over that slice collapses to 0 or 1 point.
- Normalization vectors (`sigma = sqrt(inner(v, v))`) evaluate to 0.0.
- Normalization calculations (`norm = 1 / (sig1 * sig2)`) blow up to `inf` or `NaN`, causing infinite loops or crashes in downstream optimizers.

### Implementation Standard
1. **Defensive Slice Guards**: Any function computing mismatches or overlaps across frequency/time slices must guard against collapsed intervals:
   ```python
   def _vecdiff(htilde, hinterp, fmin, fmax, psd=None):
       kmin = int(fmin / htilde.delta_f)
       kmax = int(fmax / htilde.delta_f)
       if kmax <= kmin + 1:
           return 0.0  # Slices <= 1 bin cannot be subdivided or evaluated
       return 1.0 - abs(overlap_cplx(...))
   ```
2. **Subdivision Guards**: Proposal loops must check that the interval width exceeds 1 bin before attempting to place midpoints:
   ```python
   if sample_index[i+1] - sample_index[i] <= 1:
       continue
   ```
3. **Exact Integer Midpoints**: Use integer floor division `(idx1 + idx2) // 2` to eliminate floating-point drift and guarantee midpoints lie strictly inside open intervals.
4. **Early Loop Termination**: If all candidate intervals with errors are at fundamental grid resolution, break gracefully instead of looping indefinitely.

---

## Rule 7: Proactive Unit Test Coverage for Core Algorithmic Components

### Core Rationale
Maintainers require proof that refactored or new algorithms remain mathematically accurate, physically representative, and robust against boundary conditions. Unit tests must run fast ($< 2$ seconds per module) while exercising genuine scientific regimes.

### Implementation Standard
Every PR touching numerical methods must include tests covering:
1. **Physical Parameter Space**: Test at least two distinct astrophysical regimes:
   - Low-mass / long-duration regime: BNS system (e.g. `TaylorF2`, $1.4 + 1.4 M_\odot$, $f_\text{low} = 30$ Hz).
   - High-mass / short-duration regime: BBH system (e.g. `IMRPhenomD`, $30.0 + 30.0 M_\odot$, $f_\text{low} = 20$ Hz).
2. **Algorithmic Variations**: Exercise all user-facing options (e.g., `'inline_linear'`, `'inline_quadratic'`, `'inline_cubic'`, `'linear'`, `'cubic'`).
3. **Boundary Conditions**: Explicitly test degenerate cases (adjacent sample points, empty inputs, non-subdivisible bins).
4. **NumPy 2 Cleanliness**: Ensure zero `DeprecationWarning` or `copy=False` warnings by using `.numpy()` on PyCBC custom array types.

---

## Rule 8: Early Slicing & Transient Memory Spikes in Precomputed Kernels

### Core Rationale
In search executables (`pycbc_inspiral_fir`, `pycbc_single_template`), inner-loop kernels process data segments with $N = 2^{21}\text{--}2^{22}$ samples (1M–4M samples, 500–2000 seconds). When computing auxiliary statistics (such as 16 $\chi^2$ frequency bins), generating full-length `TimeSeries` for each bin causes massive transient memory spikes ($> 300\text{ MB}$) and wastes tens of millions of FLOPs on unanalyzed data.

### Implementation Standard
1. **Kernel-Level Slicing (`return_bins_slice`)**: Provide optional slice parameters directly at the calculation kernel:
   ```python
   def power_chisq_from_precomputed(corr, snr, snr_norm, bins,
                                     return_bins=False, return_bins_slice=None):
       # ...
       if return_bins:
           if return_bins_slice is not None:
               # Slice before scalar multiplication: allocates KB instead of MB
               bin_snrs.append((q[return_bins_slice] * (snr_norm * num_bins ** 0.5)).numpy())
           else:
               bin_snrs.append(TimeSeries(q * snr_norm * num_bins ** 0.5, ...))
   ```
2. **Benchmark Verification**:
   - **Peak Memory**: Reduces memory from **344 MB to 33 MB (10.2x reduction)** per template.
   - **FLOPs & Cache**: Eliminates $3.3 \times 10^7$ redundant multiplications, saving 20–25% overall runtime.
   - **Backwards Compatibility**: Defaults to `None` so existing callers retain full `TimeSeries` outputs.

---

## Rule 9: Upstream Root-Cause Resolution vs. Inner-Loop Bandaids

### Core Rationale
Defensive guards placed inside high-throughput inner execution loops (such as skipping $k_\text{max} \le k_\text{min}$ inside FFT loops) often treat the symptom of degenerate data rather than the root cause. If an algorithm generates invalid parameters (such as duplicate $\chi^2$ bin edges), masking the defect downstream corrupts statistical assumptions (e.g. altering test degrees of freedom) and hides upstream bugs.

### Implementation Standard
1. **No Silent Inner-Loop Bandaids**: Do not place silent workarounds inside core execution kernels.
2. **Validate at Data Generation**: Validate, deduplicate, or clamp parameters upstream where they are created (e.g. in `power_chisq_bins`), preserving clean execution semantics in downstream consumers.

---

## Rule 10: Canonical Reference Code Isolation

### Core Rationale
Flagship production executables (such as `bin/pycbc_inspiral`) represent the trusted, stable baseline for the collaboration and astrophysical observational runs. Modifying canonical reference scripts to add experimental memory management or branching logic introduces regression risk into standard analyses.

### Implementation Standard
1. **Keep Canonical Executables Static**: Treat `bin/pycbc_inspiral` as largely static reference code.
2. **Dedicated Modular Executables**: Implement architectural innovations (such as hierarchical FIR filtering, ratio template searches, or custom GPU pipelines) in dedicated executables (e.g. `bin/pycbc_inspiral_fir`).
3. **Library-Level Reusability**: Factor shared functionality into general-purpose library modules (`pycbc/events/eventmgr.py`) rather than mutating reference binaries.

---

## Rule 11: Domain-Accurate Parameter Naming in Storage Hierarchies

### Core Rationale
In HDF5 storage structures, scoping datasets under a detector prefix (`/H1` or `/L1`) is fundamentally an HDF5 group path, not a string prefix. In addition, forcing callers to choose low-level file modes when a safe, universal default exists adds unnecessary cognitive overhead.

### Implementation Standard
1. **Accurate Domain Terminology**: Name HDF5 group parameters `group`, not `prefix`.
2. **Universal Defaults**: Use `mode='a'` as the default in file writing helpers (`H5FileSyntSugar`), which universally handles both initial creation and multi-detector appending without overwriting sibling detector groups.
3. **Deprecate Non-Breakingly**: Always retain legacy parameter names (`prefix=None`) as backwards-compatibility aliases.

---

## Rule 12: Avoid Namespace Redundancy / Stuttering in Function and Class Names

### Core Rationale
When a function or class lives inside a descriptive module or package (such as `pycbc.psd.estimate`), repeating the module or package name in the function identifier (e.g. `estimate_psd_trimmed_welch`) introduces redundant "stuttering" (`pycbc.psd.estimate.estimate_psd_trimmed_welch`). The module and package already establish clear context. Functions should be concisely named (e.g. `welch`, `trimmed_welch`, `multitaper`), matching existing conventions within the file.

### Implementation Standard
1. **Omit Redundant Prefixes**: Strip module/package prefixes from function names inside specialized subpackages (use `trimmed_welch`, not `estimate_psd_trimmed_welch`; use `multitaper`, not `estimate_psd_multitaper`).
2. **Harmonize with Existing Conventions**: Match the established naming style in the target module (e.g. `pycbc.psd.estimate.welch`).
3. **Preserve Backwards Compatibility**: Always retain legacy verbose names as aliases (`estimate_psd_trimmed_welch = trimmed_welch`) so external scripts and downstream pipelines do not break.
4. **Clean Subpackage Exports**: Export both concise names and compatibility aliases in `pycbc/<subpackage>/__init__.py`.

---

## Rule 13: Upstream PR Auditing & Rebase Synchronization

### Core Rationale
Before proposing a new topic PR or branching a feature, audit active PRs and commits on `gwastro/pycbc` (`upstream/master` and `upstream/pr/*`). If an upstream PR already addresses the same core issue (e.g., PR #5457 for regularized Cholesky inpainting and gating numerical stability), branching a new duplicate PR (e.g. PR-1J) fragments reviewer feedback, duplicates review effort, and risks conflicting solutions. Furthermore, feature branches must always be rebased against current `upstream/master` to avoid file collisions (e.g. PR-1G collision on `matched_filter_ratio.py`).

### Implementation Standard
1. **Audit Upstream PRs First**: Run `git fetch upstream` and check existing branches and PRs (`upstream/pr/*`) before decomposing new topic branches.
2. **Adopt Canonical Upstream PRs**: If an upstream PR covers the required feature, recommend adopting/merging the upstream PR directly rather than creating a competing duplicate. Align local topic branch pointers to the upstream PR commit.
3. **Continuous Rebase onto Master**: Always rebase topic branches onto latest `upstream/master` prior to maintainer review, eliminating accidental recreation of files merged upstream.
4. **Modular Isolation for Subsystem Extensions**: When extending or refactoring subsystems, place new algorithmic logic in dedicated modules (e.g., `pycbc/filter/dynamic_snr_renorm.py`) and use backwards-compatible re-exports, leaving existing master engines intact.

---

## Rule 14: Continuous Upstream Rebase Tracking & Multi-Branch Synchronization

### Core Rationale
In an active open-source project like PyCBC, `gwastro/pycbc:master` advances asynchronously as peer pull requests merge. PR topic branches cannot remain anchored to stale historical merge bases (e.g. `f6eaed241`). If branches sit un-rebased:
1. Reviewers cannot evaluate code against current master semantics.
2. File collisions occur when files modified or added in upstream PRs are touched.
3. CI workflows on upstream will test against outdated base commits.
4. When `gwastro/master` moves, all branches across the repository tree (not just currently open PRs) must be kept cleanly rebased with 0 commits behind master.

### Implementation Standard
1. **Automated Rebase Sentinel**: Run `sentinel/rebase_monitor.py` as a standing daemon or triggerable endpoint (`POST /api/rebase/sync`).
2. **Periodic Upstream Tracking**: Periodically execute `git fetch upstream master` to detect new upstream commit hashes.
3. **Automated Non-Destructive Rebase**: When upstream advances:
   - For every topic branch (`pr-*`, `feat/*`, `fix/*`), calculate commits behind upstream (`git rev-list --count <branch>..<upstream_sha>`).
   - If behind > 0, rebase the branch onto `upstream/master`.
4. **Isolated Test Verification**: Immediately after rebase, run the isolated test suite for that branch (e.g. `pytest test/test_*.py` with `PYTHONPATH=.`).
5. **Verified Force-Push**: Only if all tests pass, force-push the rebased branch to `origin` (`git push -f origin <branch>`).
6. **Graceful Conflict Abort**: If a rebase conflict occurs, immediately abort (`git rebase --abort`), record `CONFLICT` in `data/rebase_status.json`, and alert maintainers/agents without leaving the repository in a detached or dirty state.

---

## Rule 15: Comprehensive Review Hunk Visibility, Dynamic Test Discovery & Context Linking

### Core Rationale
A code review dashboard is only effective if 100% of code modifications are reviewable:
1. **Zero Omission**: Newly created unit tests, auxiliary fixtures, helper modules, and config files created in response to feedback must appear as first-class, commentable hunks on the review dashboard.
2. **Surrounding Context Access**: Reviewers evaluating a 30-line hunk often need to inspect surrounding classes, caller signatures, and file scope. Hunk cards must provide direct, unambiguous links to the file at those lines.
3. **Dynamic Discovery**: As agents amend branches in response to review comments, new commits, modified lines, and new test files must appear automatically in the reviewer UI without requiring manual dashboard code edits or full page reloads.

### Implementation Standard
1. **Dynamic Git Diff Ingestion**: The review server (`/api/prs` and `/api/pr/{id}/hunks`) must dynamically compute git diffs against the true merge base (`git merge-base upstream/master <branch>`), parsing every file block and hunk header (`@@ -X,Y +A,B @@`).
2. **Quad-Action Hunk Links**: Every hunk header must render 4 distinct links/actions:
   - **GitHub Link**: `https://github.com/{maintainer_fork}/blob/{commitSha}/{file}#L{start}-L{end}` (opens exact lines in fork).
   - **Local File Link**: `file://{REPO_DIR}/{file}#L{start}-L{end}` (opens local file directly in browser).
   - **IDE Deep Link**: `vscode://file/{REPO_DIR}/{file}:{start}` (opens file and cursor position in editor).
   - **Copy Path:Line**: Copies `{file}:{start}` to clipboard for instant CLI navigation.
3. **Eager Pre-fetching & Polling**: The dashboard must eagerly load live hunks for all PRs on startup (`syncAllPRHunks()`) and poll the server periodically to reflect new changes seamlessly.
4. **Universal Commentability**: Every hunk—whether a 1-line bugfix, a refactored DSP kernel, or a 300-line new test file—must include an inline review comment form dispatching directly to the agent feedback ledger.

---

## Rule 16: Mandatory Architectural Rationale, Problem Statement & Alternatives for Every Code Modification & Refactoring

### Core Rationale
Whenever an AI agent decomposes PRs, refactors existing code, addresses maintainer review feedback, or creates a topic branch:
1. **Zero Generic Placeholders**: It is strictly forbidden to emit robotic, vague placeholder strings like `"Implementation update around class EventManager(object):"` or `"Implementation update around class InterpolatingConfigParser(DeepCopyableConfigParser):"` or `"Refactored code"`.
2. **Cognitive Burden on Reviewers**: Maintainers evaluating code changes require the exact technical justification for every hunk. Code without explicit logic and reasoning forces reviewers to reverse-engineer intent, slowing reviews and hiding subtle regressions.
3. **Mandatory Triad**: Every hunk presented in the review system must explicitly answer three questions:
   - **Problem**: What concrete bug, numerical issue, performance bottleneck, cluster incompatibility, or design limitation is being solved?
   - **Logic & Rationale**: Why was this specific implementation, algorithm, or data structure chosen? How does it resolve the problem while preserving invariants?
   - **Alternatives Considered & Rejected**: What alternative designs were evaluated (e.g. why an external library, different data format, or naive approach was rejected) and why?

### Implementation Standard
1. **Catalog Registration**: Whenever an AI agent modifies or refactors code, it must register the hunk in the authoritative rationale catalog (`server/rationale_catalog.py`).
2. **Granular Hunk Rules**: When refactoring a class or function (such as `EventManager` or `InterpolatingConfigParser`), individual hunks must have distinct, granular entries matched by unique symbol patterns (e.g., `isidentifier` vs `def getint`, or `mgr.ifo` vs `f.close()`), guaranteeing that each hunk displays its own tailored explanation rather than a generic class-level summary.
3. **Intelligent Fallback Engine**: If an uncurated hunk is encountered, the review server's synthesis engine (`synthesize_hunk_rationale`) must analyze the diff's AST, modified expressions, and enclosing context to produce specific technical problem/logic/alternative descriptions, and is strictly prohibited from emitting generic `"Implementation update around..."` strings.

---

## Rule 17: Strict Triple-Dot Diff Semantics Against Upstream Master & Stable Comment Anchoring

### Core Rationale
When branches rebase onto `upstream/master`, the review dashboard must display ONLY the real differences introduced by the branch relative to current `upstream/master` (`git diff upstream/master...<branch>`).
1. **No Stale Merge Bases**: Branches must never be evaluated against obsolete historical merge bases (e.g. `f6eaed241`), which display phantom diffs of code that has already been merged upstream or refactored.
2. **Stable Comment-to-Hunk Anchoring**: Inline review comments submitted by human maintainers must remain pinned to their exact file and line context even after branch rebases or when line numbers shift.

### Implementation Standard
1. **Triple-Dot Diff Semantics**: All diff calculations in the review server and dashboard must use:
   ```bash
   git diff -U3 upstream/master...<branch>
   git diff --shortstat upstream/master...<branch>
   ```
   This computes the symmetric difference from the merge base of `upstream/master` and `<branch>` to the tip of `<branch>`, strictly exposing branch-specific modifications.
2. **File and Line Comment Pinning**:
   - A comment must NEVER drift across files. Comment-to-hunk matching strictly enforces `comment.file === hunk.file`.
   - Comments are matched to hunks by line interval overlap `[comment.startLine, comment.endLine] \cap [hunk.startLine, hunk.endLine]`.
   - If lines shift during a rebase, the comment is pinned to the nearest hunk in that exact file based on line distance, preserving full conversational context without cross-file drift.

---

## Rule 18: Upstream PR Merge Tracking, Lifecycle State Machine & Downstream Dependency Cascade (Protocol B)

### Core Rationale
When PRs are opened upstream on `gwastro/pycbc:master` (e.g. PR #5473, #5474, #5475, #5476), the review system must actively track them across their full lifecycle:
`STAGED_DEPENDENT` $\rightarrow$ `READY_TO_OPEN` $\rightarrow$ `OPEN_UPSTREAM` $\rightarrow$ `MERGED`.
When an upstream PR merges into `upstream/master`, it has immediate ripple effects across the entire dependency graph:
1. **Sibling Topic Rebasing**: All active open and ready branches on `origin` must be rebased onto the new `upstream/master` HEAD with 0 commits behind, verifying test invariants.
2. **Downstream Unblocking (Protocol B)**: Downstream dependent PRs (e.g. Wave 2/3 branches depending on Wave 1 PRs) are unblocked once their prerequisite merges.
3. **Merge Base Advancement**: The developer branch merge base (`upstream_base`) advances to the new master HEAD, shrinking overall diff volume and recalibrating future wave candidates.

### Implementation Standard
1. **Continuous Upstream Polling**: `sentinel/rebase_monitor.py` polls `upstream/master` every 60s (`git fetch upstream master`).
2. **Merge Detection**: When `upstream_head` advances, the monitor cross-references merge commit SHAs and GitHub PR API states (`/repos/gwastro/pycbc/pulls?state=closed`) to identify which PR merged.
3. **Lifecycle Transition to `MERGED`**:
   - The merged PR is marked `MERGED` in `data/wave_roadmap.json`.
   - The PR card moves to the `MERGED` section on the dashboard.
4. **Sibling Branch Rebase & Test Execution**:
   - Every active branch on `origin` is rebased onto the new `upstream/master`.
   - Automated tests (`DEFAULT_BRANCH_TEST_MAP`) execute immediately with `PYTHONPATH=.`.
   - On green tests, force-push to `origin`.
   - On conflict, abort gracefully (`git rebase --abort`) and record `CONFLICT` in `data/rebase_status.json`.
5. **Downstream Dependency Promotion (Protocol B)**:
   - `sentinel/dev_reconciler.py` evaluates the dependency DAG in `data/wave_roadmap.json`.
   - Any PR previously marked `STAGED_DEPENDENT` whose prerequisites are all `MERGED` is promoted to `READY_TO_OPEN`.
   - The branch is created or rebased on the new master base, verified with tests, registered in `server/rationale_catalog.py`, and pushed to `origin`.
6. **Dashboard Lifecycle Alignment**:
   - The dashboard dynamically displays PR cards in prioritized swimlanes:
     `NEEDS_ATTENTION` (unaddressed review comments or rebase conflicts) $\rightarrow$
     `OPEN_UPSTREAM` (open on `gwastro/pycbc`) $\rightarrow$
     `READY_TO_OPEN` (rebased, 100% tests pass, 0 pending comments) $\rightarrow$
     `STAGED_DEPENDENT` (waiting for upstream prerequisite) $\rightarrow$
     `MERGED`.

---

## Rule 19: Physical Signal Duration Containment & Fast Unified Test Setup (`setUpClass`)

### Core Rationale
In frequency-domain gravitational wave tests (such as waveform compression, matched filtering, PSD estimation, and inpainting):
1. **Time-Domain Aliasing Prevention**: A frequency series with sample spacing $\Delta f$ corresponds to a periodic time-domain duration $T = 1 / \Delta f$. If the physical signal duration from $f_{\text{lower}}$ exceeds $T$ ($T_{\text{signal}} > 1 / \Delta f$), the waveform wraps around in time, inducing severe aliasing distortion and corrupting compression or filtering tests.
2. **Redundant Waveform Generation Elimination**: Frequency-domain waveform generation (`get_fd_waveform`) can take hundreds of milliseconds per call. Generating waveforms inside individual test methods balloons test runtimes.

### Implementation Standard
1. **Physical Containment Invariant**: Always ensure:
   $$\Delta f \le \frac{1}{T_{\text{signal}}(m_1, m_2, f_{\text{lower}})}$$
   - For BNS ($1.4 + 1.4\,M_\odot$, $f_{\text{low}} = 30\,\text{Hz}$): duration is $\sim 58.9\,\text{s}$, requiring $\Delta f \le 1/64 = 0.015625\,\text{Hz}$ ($T = 64\,\text{s}$).
   - For BBH ($30 + 30\,M_\odot$, $f_{\text{low}} = 20\,\text{Hz}$): duration is $\sim 1.1\,\text{s}$, requiring $\Delta f \le 1/4 = 0.25\,\text{Hz}$ ($T = 4\,\text{s}$).
2. **Unified `setUpClass` Initialization**: Pre-generate test waveforms once per test class in `@classmethod def setUpClass(cls)`, storing `cls.hp_bns` and `cls.hp_bbh`. Test methods must reuse these shared fixtures, keeping execution times under 3 seconds.
3. **Sanitized Sample Points**: Any utility accepting external sample frequencies (such as `compress_waveform`) must defensively sort and deduplicate inputs (`numpy.sort(numpy.unique(numpy.asarray(points, dtype=float)))`) to prevent out-of-order indexing failures.
4. **Explicit Exception Chaining**: Always use `raise ... from err` (to preserve traceback) or `raise ... from None` (to cleanly reject invalid values) per PEP 3134.

---

*Document maintained autonomously by the Antigravity Dual-Review Sentinel system.*


