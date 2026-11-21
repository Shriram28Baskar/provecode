# ADR 0013 — Trusted re-run (runs A/B/C) with the base branch's tests as the contract, and process isolation for contract checks

**Status:** Accepted, 2026-11-21. Phase 2 of `PROVECODE_V2_BUILD_BRIEF.md`. The owner delegated decisions while away. Choices the brief left open, or that depart from it, are marked **(decided under delegation)** so the owner can overturn them. The brief's own "ask the owner first" item, renaming VERIFIED to PASSED, is **not** done; it waits for the owner.

## Context
- Static rules see what a change did to the tests and the harness. They cannot see a broken implementation that the PR's own, possibly weakened, tests still pass.
- The brief's zero-config contract: run the **base branch's** tests and harness against the **head** code, in a sealed container.
- In recorded runs, candidate code runs in the same Python process as the contract-owned checks. A malicious candidate could patch pytest in memory and forge the result (README, "Honest scope").
- **A design flaw in the brief's R2, found while writing this ADR.** If a test is unchanged in head, runs B (head code, base tests) and C (head code, head tests) execute the same test on the same code, so they agree. "C passes and B fails" can therefore only happen on tests the PR edited or deleted, or when the harness differs. A single R2 → FAILED would fail every legitimate behaviour change that updates an expected value. R2 is split by cause below.

## Decision

### 1. Run B's tree (`provecode.overlay`)
- Start from the exported head tree (`git archive`).
- Copy from base: every test file, every `conftest.py`, the root pytest and coverage config files, and every non-Python file under a test directory.
- `pyproject.toml` keeps head's content, with its `[tool.pytest]` / `[tool.pytest.ini_options]` tables replaced by base's. **(decided under delegation)** This is done textually, because the stdlib cannot write TOML. The merged file must parse, and must give pytest exactly base's settings; otherwise the run is INCONCLUSIVE (`overlay_failed`). Both table forms are handled because 3 of the 9 Tier H repositories use pytest 9's native `[tool.pytest]` (ADR 0012).
- Deleted, and recorded as evidence:
  - import-time harness files and shadowing modules (the H3 targets);
  - **(decided under delegation, stricter than the brief)** every test-side file that exists **only in head**: new test modules, new conftests, new files in test directories. pytest imports every test module during collection, before any test runs. A new `tests/test_zzz.py` could therefore patch the package at import and make the base tests pass. B means head code and base tests, nothing else.

### 2. Runs
| Run | Code | Tests and harness | Purpose |
|---|---|---|---|
| A (×2) | base | base | Reference |
| B (×2) | head | base (overlay) | The trusted result |
| C | head | head | What ordinary CI would say |

- **Dependencies come from the base tree only:**
  - `[build-system].requires`, `[project].dependencies`;
  - test/dev optional dependencies and dependency groups (with `include-group`);
  - test/dev/ci requirement files (with `-r`);
  - `pytest`.
- **(decided under delegation)** The wheelhouse is built by default on the **host** with `pip download --only-binary=:all:` for the container's Linux platform and Python version. It downloads wheels and runs no package code.
  - Measured reason: from inside Docker Desktop on this network, downloads from `files.pythonhosted.org` timed out (`ReadTimeoutError` after 35 s), while the host fetched the same set in 6.7 s.
  - `--wheelhouse container` keeps the brief's networked-container option.
  - **Limit:** the host's pip evaluates environment markers, so a Linux-only dependency can be missed. The offline install then fails and the run is INCONCLUSIVE, never a silent pass.
- **Every test container:**
  - `--network none`, a read-only root, a non-root user, all capabilities dropped, `no-new-privileges`;
  - 1 GB memory, 1 CPU, 256 processes, a timeout;
  - a venv created in tmpfs, the wheelhouse installed offline, the tree installed with `--no-deps --no-build-isolation`;
  - `python -I -m pytest` with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.
- **Plugins.** The only plugins loaded with `-p` are:
  - the `pytest11` entry points declared by the **base** dependency set, discovered in a sealed container before any tree is installed;
  - the base project's own declared `pytest11` plugins;
  - PROVECODE's trusted reporter plugin, which records collected node IDs and per-phase outcomes.
- A and B run twice. Disagreement means `flaky`: the test is excluded, never FAILED.
- **(decided under delegation)** Base tests that already fail with the base code in the sealed environment (for example, tests that need the network) are **excluded and listed as a gap** (`baseline_failing`). The brief made the whole result INCONCLUSIVE in that case, which would make most real repositories unjudgeable. INCONCLUSIVE (`baseline_broken`) remains for an A that produces no usable run (setup failed, collection error, no tests).

### 3. Verdict: the brief's R2, split by cause (decided under delegation)
For every non-flaky base test that passes in A and fails in both B runs:

| Rule | Cause (static facts from the same Changeset) | Severity |
|---|---|---|
| **X1** | collected in A but not collected in B (head code broke collection) | blocking |
| **X2** | the test is unchanged in head and passes in C: only the head's harness differs | blocking |
| **X3** | the test was deleted in head: the behaviour it checked no longer holds | blocking, unless its condition carries an approved contract change |
| **X4** | the test was edited in head and the edit has a weakening finding (S/B/T) | blocking |
| **X5** | the test was edited in head with no weakening finding: the expectation changed | warning; blocking when it guards a mandatory condition with no approved change |
| **X6** | the test fails in C too: an ordinary regression | blocking |

X1 is the brief's R1, renamed. "R" clashes with rule R0 and with the research report's R1–R5.

### 4. Zero-config contract
With no contract, `provecode rerun` and `provecode check --execute` judge against an **implicit contract**:
- one mandatory condition, `BASE-TESTS`: "the base branch's tests pass against the head code in a sealed run";
- verified by run B's evidence (`E-B`, owner `contract`).

VERIFIED then means: no blocking finding, and every judged base test passed in B. A contract-owned check still needs `provecode record`.

### 5. Process isolation for contract-owned checks (`provecode.isolate`)
- `isolate.call("pkg.mod:func", *args, **kwargs)` runs the call in a child `python -I` process that has the candidate tree on `sys.path`. The parent and child speak JSON lines.
- The child keeps a private duplicate of stdout for the protocol and points file descriptor 1 at stderr. Anything the candidate prints, even with `os.write(1, …)`, cannot reach the protocol.
- Built-in exceptions are re-raised with their own type. Anything that is not a JSON value, a crash, a timeout or a malformed answer is an `IsolationError`, never a pass.
- **Measured** on this machine: child start-up 0.54 s, then 0.20 ms per call.
- **Limits:** side effects in the candidate are not observable. This is process separation, not a sandbox; the container is the sandbox.

## Evidence (2026-11-21)
- **`demo/build_probes.py`:** a real repository whose candidate branches each remove the 20% cap and apply one brief §0.3 cheat. `provecode check --execute` in real Docker runs gives:

  | Branch | A / B / C | Outcome | Blocking findings |
  |---|---|---|---|
  | cheat-conftest | pass / fail / pass | FAILED | H1, X2 |
  | cheat-input-change | pass / fail / pass | FAILED | X4 |
  | cheat-helper | pass / fail / pass | FAILED | X4 |
  | cheat-monkeypatch | pass / fail / pass | FAILED | X4 |
  | cheat-deselect | pass / fail / pass | FAILED | H4, X2 |
  | honest-fix (a refactor that keeps the cap) | pass / pass / pass | VERIFIED | none |

  The monkeypatch probe first showed X5, not X4. T3 resolved only module-level imports, and the probe imports inside the test. That static gap was fixed with a regression test (`test_regression_t3_resolves_imports_inside_the_test_function`). It was found by an executed probe, not by benchmark data.
- **`engine/tests/test_isolate.py`:** a candidate that patches `_pytest.reports.TestReport` at import time makes a failing contract check **pass** when imported directly (the attack works). The same check **fails** under `provecode.isolate`.
- **Real repositories:** `docs/research/scripts/rerun_real_repos.py` → `docs/research/data/rerun_real_repos.json` (results in the Phase 2 report).

## Not done here
- The VERIFIED → PASSED rename (the owner decides).
- Lockfile-exact dependency installs (`uv.lock`, `poetry.lock`).
- Running contract-owned checks inside `rerun`.
