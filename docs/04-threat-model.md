# 04 — Threat model

**Status:** Draft, 2026-11-21, written with ADR 0012 (harness rules) and ADR 0013 (trusted re-run, isolation). It supersedes nothing; `02-architecture.md` §7 still covers the live browser path.

This document covers each surface of PROVECODE:
- what the **candidate** (the change being judged, and whoever or whatever wrote it, including a coding agent) controls;
- what PROVECODE trusts;
- the bypasses that are known and not closed;
- what Docker does not protect against.

## 1. Assets and the question PROVECODE answers

The asset is the **verdict**: "did this change weaken its own verification, and does it still pass the verification trusted before it?"

An attacker wins by producing **no blocking finding and a passing run** for a change that breaks a guarded behaviour. Availability attacks (making PROVECODE crash or time out) are a lesser goal. Every crash or limit becomes a gap, and gaps give INCONCLUSIVE, never VERIFIED.

## 2. Trust boundaries

| Component | Trusted? | Why |
|---|---|---|
| The base commit (its code, tests, harness, config, dependency list) | **Trusted** | It is the reviewed state the change is judged against. Its dependencies are installed into the wheelhouse |
| The approved contract, read from the base commit or outside the candidate | **Trusted** | Hash- and approval-checked; an edit to a protected path is blocking |
| PROVECODE's engine, its reporter plugin and its runner scripts | **Trusted** | They come from the installed package, never from the candidate tree |
| The head commit (code, tests, conftest, config, CI, dependency changes) | **Untrusted** | Written by the party being judged |
| The candidate's process at run time (runs B and C) | **Untrusted** | It imports head code |
| The isolated child process (`provecode.isolate`) | **Untrusted** | It imports head code, but cannot reach the check that judges it |
| The browser input (pasted code, fetched code) | **Untrusted** | It is parsed only, never executed (architecture §7.4) |

## 3. What the candidate controls, and what stops it

| The candidate can… | Static answer (any surface) | Executed answer (rerun) |
|---|---|---|
| Weaken an assertion, delete a test, add a skip | S1–S5, B1–B4, T1–T5 | Run B uses the base tests, so the weakened test is not what runs |
| Plant a `conftest.py` that forces outcomes or drops tests (BenchJack) | H1, blocking without a contract | The overlay deletes head-only conftests and restores base ones |
| Load a plugin (`pytest_plugins`, `-p`, a `pytest11` entry point) | H2 | Plugin autoload is disabled; only plugins declared by the **base** dependency set are passed with `-p` |
| Add `.pth` / `sitecustomize.py`, or a module shadowing the stdlib, pytest or the package | H3, blocking | The overlay deletes them |
| Narrow selection in pytest config (`-k`, `--deselect`, `testpaths`, a new higher-precedence config file) | H4 | The overlay restores the base config. X1 reports base tests that B no longer collects |
| Weaken CI (`\|\| true`, `continue-on-error`, `if: false`, step removed, `paths-ignore`) | H5 | Not applicable: rerun does not use the candidate's CI |
| Lower coverage requirements | H6 | Not applicable (coverage is not run) |
| Remove or pin down a test dependency | H7 (warning) | Dependencies come from the **base** lockfile or requirements |
| Monkeypatch the function under test inside its own test | T3 (warning) | The base test, without the patch, runs in B |
| Break the implementation while tests "pass" (special-casing, `__eq__` overloads) | **Not detectable statically** | Run B fails if the base tests exercised the behaviour |
| Tamper with pytest **at import time** of head code (patch `_pytest.reports`) | Not detectable statically | **Not prevented in runs B and C** (see §4.1). Prevented for contract-owned checks through `provecode.isolate` |
| Edit the contract | CONTRACT_TOUCHED, blocking | The contract is read from base |
| Edit the agent-loop hook settings | **Not prevented** (ADR 0010) | The PR check is the enforcement point |

## 4. Known bypasses (open)

### 4.1 In-process tampering in runs B and C
In runs B and C, the head code and pytest share one Python process, because the tests must import the code. Deliberately malicious head code can patch pytest's reporting at import time and forge run B's outcome, exactly as a conftest could.
- **The defence for what matters most** is a contract-owned check that calls the candidate through `provecode.isolate`, so the judging process never imports candidate code.
- `engine/tests/test_isolate.py` demonstrates both halves. The forged pass works without isolation and fails with it.

### 4.2 Weaknesses the base tests never checked
If the base tests do not exercise a behaviour, breaking it passes run B. No rule detects a weakening of a guarantee that was never tested. This is why the contract exists.

### 4.3 Coverage of the static rules
- The static rules read the files listed in ADR 0012.
- They do not read CI systems other than GitHub Actions, `tox.ini` `commands`, `noxfile.py`, Makefiles, or a test step that runs through a script (`run: scripts/test`).
- T2 resolves helpers within one file only.
- See the README's "Known blind spots".

### 4.4 Dependency confusion via the base
The wheelhouse installs what the **base** declares, from the package index at the time of the run. A compromised upstream package affects the base as much as the head. PROVECODE does not defend against supply-chain attacks on the base's dependencies.

### 4.5 Local hooks
An agent with a shell can edit `.claude/settings.json`, the session record or the hook itself (ADR 0010).

## 5. What Docker does and does not protect against

**Does (as configured):**
- no network during test runs;
- a read-only root filesystem and read-only source mounts;
- a non-root user with all capabilities dropped and `no-new-privileges`;
- memory, CPU, process-count and wall-clock limits;
- a fresh container per run.

**Does not:**
- **Kernel or runtime vulnerabilities.** Containers share the host kernel. A container escape is out of scope; run PROVECODE's executed mode on a machine you would let run the candidate's code anyway, such as a disposable CI runner.
- **The wheelhouse step has network access.** It sees only the base tree and the base dependency list, and never candidate code. Build backends declared by the base still run there.
- **Side channels and resource exhaustion** within the limits (for example filling the tmpfs to make a run fail). These turn a run into an error, so INCONCLUSIVE, not a forged pass.
- **Docker Desktop on Windows and macOS** runs containers in a VM with different file-sharing semantics. Measured results in this repository come from Docker Desktop on Windows 11 unless stated otherwise.

## 6. The browser (unchanged; architecture §7–§8)

Code in the browser is parsed by Pyodide in a Web Worker and never imported or executed. The CSP allows scripts and connections only from the site itself. Phase 3 (fetching from GitHub) must re-open this section before it ships: `connect-src` will need `api.github.com` and `raw.githubusercontent.com`, and tokens must stay in memory.
