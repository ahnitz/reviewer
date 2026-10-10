# PyCBC Development & Code Architecture Guidelines

This document captures architectural principles, coding conventions, numerical stability rules, and pull-request guidelines inferred from PyCBC development, maintainer code reviews, and CI testing across upstream `gwastro/pycbc`.

---

## 1. Core Architecture & Philosophy

1. **Modular, Decoupled Subsystems**:
   - PyCBC components are divided into decoupled packages: `types` (time/frequency series), `filter` (matched filtering, resampling), `psd` (spectral estimation), `waveform` (approximant generation, compression), `events` (triggers, clustering, coincidences), and `workflow` (Pegasus/Condor DAG pipelines).
   - Avoid cross-subsystem coupling. A module in `pycbc.filter` should never depend on `pycbc.workflow`.
2. **Minimizing External & LALSuite Dependencies (per `AGENTS.md`)**:
   - Prefer native NumPy and SciPy implementations over LALSuite wrappers whenever possible.
   - Do not introduce new external package dependencies unless strictly necessary and discussed with maintainers.
   - Pure Python / NumPy implementations are preferred over Cython/C unless inner-loop microsecond performance benchmarks demonstrate necessity.
3. **Reproducibility & Determinism**:
   - All signal processing algorithms must produce bit-for-bit deterministic results across platforms (x86_64, ARM64/Apple Silicon) under identical seeds and inputs.

---

## 2. NumPy 2 Compatibility & Array Semantics

PyCBC supports both modern NumPy 2.0+ and legacy NumPy 1.x environments. Follow these strict rules:

1. **Avoid Deprecated Scalar Type Aliases**:
   - Never use `numpy.float_`, `numpy.int_`, `numpy.bool_`, `numpy.complex_`, or `numpy.longfloat`.
   - Use standard Python built-ins (`float`, `int`, `bool`, `complex`) or explicit sized dtypes (`numpy.float64`, `numpy.int64`, `numpy.complex128`).
2. **Standardize on `numpy.float64` for Time & Timestamps**:
   - Do not use `numpy.longdouble` for GPS times, timeslide offsets, or coincidence clustering.
   - On x86, `longdouble` is 80-bit; on ARM/M1/Graviton, it is 128-bit; on Windows, it is 64-bit. This disparity causes subtle precision mismatches and NaN comparisons. Standardizing on `float64` guarantees uniform 53-bit mantissa precision (~15-17 decimal digits, sub-microsecond GPS resolution) across all architectures.
3. **Explicit Memory Copy vs View Guarantees**:
   - When a function returns a modified or filtered version of an input series, do not return the input reference even if no modification was needed.
   - Example: In `resample_to_delta_t(timeseries, delta_t, copy=True)`, if the sample rate already matches, return `timeseries.copy()` by default. Returning the original reference causes silent in-place mutation aliasing bugs when callers modify the result. Allow opt-in `copy=False` for zero-copy performance in hot loops.
   - Avoid arithmetic copies like `timeseries * 1`; use `timeseries.copy()` explicitly.
4. **Defensive Slice Bounds & Clamping**:
   - In windowing and slicing (e.g., Q-transform `qseries`), never assume window indices will be within `[0, len)`. Near $f=0$ or large Q, `start` can become negative.
   - Under NumPy 2.0, negative slice indices wrap to the end of the array, silently corrupting the frequency window.
   - Always clamp bounds explicitly:
     ```python
     f_start = max(0, start)
     f_end = min(f_len, end)
     ```
     and zero-pad into a pre-allocated array of zeros.

---

## 3. Command-Line & Configuration Parsing

1. **Safe Arbitrary-Precision Integer Conversion (`pycbc.types.optparse`)**:
   - CLI options that parse integer values (such as `--batch-size` or `--sample-rate`) frequently receive scientific notation strings from workflow generators (e.g., `'1e6'`).
   - Standard `int('1e6')` fails with `ValueError: invalid literal for int() with base 10`.
   - Naive `int(float(v))` silently truncates fractional numbers (`int(float('2048.5')) == 2048`) and silently loses precision for large integers $\ge 2^{53}$ (e.g., bitmask flags or large GPS nanoseconds).
   - Use the standard safe converter:
     ```python
     def to_int(val):
         try:
             return int(val)
         except ValueError:
             f = float(val)
             if f.is_integer():
                 return int(f)
             raise ValueError(f"Value '{val}' cannot be parsed as an integer without loss.")
     ```
2. **HPC Environment Variable Safety in Config Parsing**:
   - `pycbc.types.config.InterpolatingConfigParser` interpolates environment variables.
   - HPC cluster environments (Slurm, HTCondor, LMOD) often export environment keys containing `%` or `$` symbols (e.g., `BASH_FUNC_ml%%`).
   - ConfigParser interpolation crashes on unescaped `%` in values or keys.
   - Filter `os.environ` to include only keys matching `key.isidentifier()` and containing no `%` or `$`.

---

## 4. Strain Conditioning, Gating & Inpainting

1. **Adaptive Tikhonov Ridge Regularization on Cholesky Inpainting**:
   - In strain gating (`pycbc.strain.gate`), the Toeplitz autocovariance matrix can become ill-conditioned when the detector PSD has high dynamic range or steep low-frequency roll-offs.
   - Standard `scipy.linalg.cho_solve` will throw `LinAlgError: matrix is not positive definite`, terminating long analysis jobs on severe glitch bursts.
   - Guard Cholesky factorisation with adaptive Tikhonov regularization:
     ```python
     try:
         c, low = scipy.linalg.cho_factor(toeplitz_matrix)
     except scipy.linalg.LinAlgError:
         toeplitz_matrix[np.diag_indices_from(toeplitz_matrix)] += ridge * toeplitz_matrix[0, 0]
         c, low = scipy.linalg.cho_factor(toeplitz_matrix)
     ```
     This stabilizes the decomposition while preserving continuous phase derivatives across the gate boundary.
2. **Strain Memory Deallocation in Long Filtering Jobs**:
   - Once Fourier-transformed segments (`fseries`) and PSDs are generated in executables like `pycbc_inspiral`, the raw time-domain strain is no longer needed during the filtering loop.
   - Deallocate the heavy strain buffer:
     ```python
     gwstrain._data = numpy.array([], dtype=gwstrain.dtype)
     ```
     This frees hundreds of megabytes per process and prevents Linux Copy-on-Write (COW) page duplication across multiprocessing workers, while keeping `gwstrain.epoch` and metadata intact.

---

## 5. Injections & Multi-Detector Safety

1. **Immutable Injections Across Detectors**:
   - In multi-detector workflows (e.g. H1, L1, V1), the exact same injection parameters and waveform objects are processed for each detector sequentially.
   - Windowing, tapering, or applying antenna pattern factors to `inj.waveform` must NEVER modify the underlying waveform array in-place.
   - Always copy before modifying:
     ```python
     inj_waveform = inj.waveform.copy()
     ```
   - In-place mutation causes downstream detectors to see an already-tapered or phase-shifted waveform, distorting recovery SNR and coincidence statistics.
2. **Sub-Threshold Injection Pre-filtering**:
   - In injection recovery campaigns with $10^5+$ simulated signals, filtering quiet signals that have zero chance of detection wastes millions of CPU-seconds.
   - Use optimal SNR pre-filtering (`--injection-filter-rejector-optimal-snr-threshold`): calculate $\sigma = \text{inner\_product}(h, h)^{1/2}$ in microseconds, skipping full multi-rate matched filtering for sub-threshold injections.

---

## 6. PSD Estimation & Spectral Lines

1. **Robust Estimators Beyond Mean / Median**:
   - Standard Welch mean is corrupted by non-stationary glitches.
   - Median Welch has high estimator variance (~1.44x penalty in degrees of freedom) and bi-modal bias near spectral lines.
   - Prefer alpha-trimmed Welch periodograms (`estimate_psd_trimmed_welch`), trimming the top and bottom quantiles (e.g. 10%) to discard glitches while retaining near-mean statistical efficiency.
2. **Multitaper PSD Estimation**:
   - For high-resolution estimation around narrow violin lines, use Slepian DPSS sequences via pure `scipy.signal.windows.dpss`.
   - Avoid calling external LAL routines for spectral windowing.

---

## 7. Matched Filtering & Waveform Compression

1. **Dynamic SNR Renormalization**:
   - During glitch bursts, stationary PSDs underestimate noise variance, generating cascades of spurious triggers.
   - Implement rolling variance envelopes using $O(1)$ cumulative sums (`DynamicSNRRenormFactor`).
   - Use a central hollow notch (exclusion zone) around the candidate trigger: variance is estimated from surrounding sidebands, strictly preserving real astrophysical signals while suppressing non-stationary noise.
2. **Waveform Compression & Spline Knot Placement**:
   - In greedy spline compression (`pycbc.waveform.compress`), knot spacing should naturally accommodate merger-ringdown dynamics.
   - High-mass binaries exhibit second-derivative sign changes over 1-2 samples at 4096 Hz.
   - Do not artificially clamp knot spacing with `abs(idx - existing) <= 2`. Use `abs(idx - existing) <= 0` (deduplication only) to allow dense knot placement across steep mergers, guaranteeing $< 10^{-4}$ interpolation mismatch.

---

## 8. Pull Request & Review Hygiene

1. **Atomic, Single-Topic PRs**:
   - Each PR must focus on a single self-contained subsystem (bugfix, performance optimization, or feature).
   - Never combine unrelated changes. A bugfix in `optparse.py` must not be bundled with a new filter engine.
2. **Zero Dead Code & Zero Unused Imports**:
   - All code must pass `flake8` without warnings.
   - Eliminate unused imports (`F401`), unused variables, and abandoned experimental flags before submitting.
3. **Minimal Diff Footprint**:
   - Avoid reformatting lines outside the direct scope of the change.
   - Do not perform whitespace refactoring or re-indentation on untouched legacy functions.
4. **Mandatory Unit Tests**:
   - Every PR must include standalone unit tests in `test/test_*.py`.
   - Tests must run independently via `pytest test/test_<module>.py` and pass against upstream `master`.
5. **Clear Commit & PR Messages**:
   - Use conventional commit prefixes: `fix(...)`, `feat(...)`, `perf(...)`, `refactor(...)`, `test(...)`.
   - State clearly:
     1. The problem or motivation
     2. The solution and implementation rationale
     3. Alternative approaches considered and why they were rejected
     4. Runnable test commands and timing results


---

## 9. Dependency DAG & Continuous PR Unlocking vs Waterfall Waves

When refactoring or introducing large feature sets (such as multi-detector search or strain conditioning), PR decomposition must **never** be treated as a series of rigid waterfall batches ("waves"). Instead:

1. **Model as a Directed Acyclic Graph (DAG)**:
   - PR candidates are nodes in a dependency DAG.
   - Root nodes with zero dependencies (e.g. bugfixes, independent signal-processing helpers, standalone data structures) can and should be opened and reviewed concurrently in parallel.
2. **Dynamic Per-PR Unlocking**:
   - A downstream PR is blocked **only** by its specific parent PRs, not by an entire "wave" or batch of unrelated PRs.
   - *Concrete Example*: If PR B (`pr-feat-frame-gwosc-hdf-strain`) depends solely on PR A (`pr-fix-core-numpy2-optparse`, #5475), PR B is unlocked and ready for submission the exact moment PR A merges into `gwastro/pycbc:master`. It never waits for unrelated PRs (like PR-1J inpainting or PR-1G SNR renormalization) to merge.
   - PRs move forward as a continuous pipeline: each merge unblocks its direct descendants immediately.
3. **Automated Cascading Rebases**:
   - Maintainer bots and sentinel monitors continuously track the upstream merge base (`git merge-base upstream/master <branch>`).
   - When any parent branch merges into `upstream/master`, the sentinel immediately rebases the child branch onto the new master HEAD, validates tests in isolation, and promotes the child to `READY_TO_OPEN`.
