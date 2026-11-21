# 03 — Phase 1 Build Spec (hackathon)

**Status:** Accepted, 2026-11-20 (owner approval, ADR 0007)
**Authority:** ADR 0007. For Phase 1 this replaces docs 03–08.
**Inputs:**
- `docs/01-PRD.md` (approved)
- `docs/02-architecture.md` (provisional)
- ADRs 0001–0007

Nothing here is a measured result. Wherever a section of 01 or 02 already covers something, this spec cites it and doesn't restate it.

---

## 1. What ships

A public static site plus an open-source engine. Together they show the PRD §3 story end to end:
- **Live detector:** the checkout weakening is caught in the browser, the legitimate 25% change isn't blocked, and the reviewer can edit the test and re-run.
- **Recorded full run:** a Docker run that ends FAILED, with an evidence trace and provenance.
- **Benchmark page:** generated Tier A numbers with intervals, compared against a count-based baseline.

**Non-claims shown on the site:**
- The real-world feasibility spike (Gate 0) is pending.
- Tier A is injected and experimental.
- No Tier B or C results exist yet.
- VERIFIED is not proof of correctness.

## 2. Repository layout

Follows architecture §3. Nothing is added under `api/`.

```
engine/      src/provecode/{limits,analyzer,contract,decide,report,git,exec,cli}.py + tests/
benchmark/   patterns.md, split.lock.json, sources.lock.json, cases/, baseline_count.py, regenerate.py
demo/        checkout scenario repo built by script (baseline, candidate-weakened, candidate-legit)
docker/      Dockerfile for recorded runs (base image pinned by digest)
artifacts/   generated only: runs/*/report.json, benchmark/tierA.json, manifest.json
web/         Next.js (output: 'export'), Tailwind; web/worker/ Pyodide worker
.github/workflows/ci.yml
```

Dependencies:
- **Engine core:** standard library only.
- **CLI extra:** `PyYAML`, for loading contracts.
- **Dev:** `pytest`.
- **Web:** `next`, `react` and `tailwindcss`. Charts are built by hand in SVG, with no chart library.

## 3. Contract (replaces 03-contract-spec)

```yaml
contract: { id: checkout-pricing, version: 1 }
approval: { approved_by: <owner handle>, approved_at: 2026-11-20T00:00:00Z }
conditions:
  - id: C1
    description: Discount is capped at 20%
    type: boundary            # functional|boundary|api-compat|protected-behavior
    mandatory: true
    guarded_by:               # repo tests that guard this condition (link target for findings)
      - tests/test_pricing.py::test_discount_is_capped_at_20_percent
    checks: [chk-cap]         # contract-owned checks (execution; recorded runs only)
checks:
  - { id: chk-cap, kind: pytest, path: contract_checks/test_cap.py }
```

**Hash.**
- `content_hash` is the sha256 of the canonical JSON of `{contract, conditions, checks}`, with sorted keys, no whitespace and UTF-8 encoding. The `approval` block isn't part of the hash.
- The approval record stores the hash it approved.
- **Load fails** if the stored approved hash differs from the recomputed hash, or if approval is missing. Load failure is a gap, not a pass.
- `compile` writes the canonical JSON that the browser uses.

**Protection.**
- If the candidate diff touches the contract path or `contract_checks/`, the engine raises the finding `CONTRACT_TOUCHED`, which is **blocking**.
- On GitHub the protection itself comes from CODEOWNERS plus branch protection (PRD §4.2). The engine detects tampering; it doesn't prevent it.

**Approved contract change.**
- A base→head range "carries an approved change to Cx" when both of these hold:
  - the head contract version is greater than the base contract version
  - Cx's description, or the tests it is guarded by, differ between the two versions
- In the demo this is v1 → v2, where the cap changes to 25%. The new contract is approved out of band, meaning it is loaded from the trusted contract store and not from the candidate.

## 4. Integrity rules (replaces 04-test-integrity-spec)

**Alignment.**
- Tests are keyed by `file::Class::func` (`Class` only when the test is inside a class).
- A base test whose key is missing in head counts as B1, **unless** a head test has an identical normalized body. In that case it is a rename and gets an info finding.
- If a file doesn't parse, or goes over the limits in §7, it becomes a gap (`unparseable` / `input_limit`). It is never treated as passing.

Each rule compares one aligned (base, head) test. Every finding cites its rule, file, test key, the base and head line numbers, and the before and after source lines.

| Rule | Triggers when (base → head) |
|---|---|
| **S1 comparison weakened** | In aligned asserts that compare the same subject expression, the operator moves down the strength order `== / is` → `<= / >= / < / >` → `!=`. Also fires when a `pytest.approx` `rel` or `abs` literal increases, or when an exact `==` becomes `approx`. |
| **S2 trivial bound** | `== <literal>` becomes a trivially satisfiable check: `>= 0`, `> 0`, `>= <smaller literal>`, `is not None`, `!= None`, bare truthiness (`assert x`), `isinstance(...)` only, or `len(x) >= 0`. S2 takes precedence over S1 on the same assert. |
| **S3 exception widened** | `pytest.raises(X)` becomes a builtin ancestor of X, or `Exception` / `BaseException`. Also fires when a `match=` is removed, or when tuple members are replaced by an ancestor. |
| **S4 parametrize cases removed** | The set of `ast.unparse` forms of `argvalues` in head is a strict subset of the base set. The finding lists the removed cases. |
| **B1 deleted** | A test function, or a whole test file, is deleted and isn't a rename. |
| **B2 skip added** | Any of these is added: an unconditional `skip` or `xfail` mark (`skipif(True)` counts), `pytest.skip()` as the first statement, or an `if False:` / `return` guard placed before the asserts. |
| **B3 assertion removed** | The aligned test has fewer `assert` / `pytest.raises` statements, and the removed ones aren't explained by S1–S3. |
| **V0 value changed** (info only) | The expected literal changed and comparison strength stayed the same, for example `== 80.00` → `== 75.00`. |

**Severity.** First match wins.
1. `CONTRACT_TOUCHED` → **blocking**.
2. The finding is on a test that guards a mandatory condition, and the range carries an approved change to that condition → **info**. The finding also links the change.
3. An S or B finding on a test that guards a mandatory condition → **blocking**.
4. An S or B finding on any other test → **warning**.
5. V0 → **info**.

**False-positive handling.** Only a contract-guarded test can produce a blocking finding. Everything else is advisory. Legitimate edits go through an approved contract change (§3), never through a suppression comment written into the candidate.

## 5. Decision and report (replaces 05-engine-design)

**Outcome.** This is a pure function of (contract, evidence, findings, gaps), following primer §4.9 with the precedence FAILED > INCONCLUSIVE > VERIFIED.
- **FAILED:** there is a blocking finding, or any evidence for a mandatory condition has result `fail`.
- **INCONCLUSIVE:** a mandatory condition lacks passing evidence. This covers live mode, where an execution check is planned but not executed, so the gap reason is `requires_execution`.
- **VERIFIED:** every mandatory condition has passing evidence, there are no blocking findings, and there are no gaps on mandatory conditions.

Static integrity analysis on its own **never** counts as passing evidence for a condition. So live results can be only FAILED or INCONCLUSIVE, as ADR 0001 requires.

**Expected outcomes for the demo:**

| Scenario | Live | Recorded |
|---|---|---|
| Weakened | FAILED | FAILED |
| Legitimate (v2) | INCONCLUSIVE | VERIFIED |

**Report JSON**, `schema_version: "1"`:
- `mode`: live / precomputed / recorded
- `outcome`, plus `reasons[]` (each with kind, id and text)
- `contract{id, version, content_hash, approved_by}`
- `findings[]{id, rule, severity, file, test, base_lines, head_lines, before, after, condition_ids, rationale}`
- `evidence[]{id, check_id, condition_ids, result, exit_code, output_excerpt, output_sha256}`
- `gaps[]{condition_id, reason}`
- `provenance{base_sha, head_sha, engine_version, python, image_digest?, generated_at}`

There is also a Markdown rendering for PR comments.

**CLI.**
- `provecode check --repo . --base <ref> --head <ref> --contract <path> [--json out]` does static analysis only.
- `provecode record ...` adds the Docker execution step.
- Exit codes: 0 = VERIFIED, 1 = FAILED, 2 = INCONCLUSIVE, 3 = usage or internal error.
- `provecode.git` reads blobs through `git show <sha>:<path>` and never checks out or modifies the user's repo.

**Recorded run** (`provecode.exec`):
- Docker with a `python:3.11-slim` image pinned by digest.
- Run settings: `--network none`, the source mounted read-only, `--memory 512m`, `--cpus 1`, a 120 s timeout, and a fresh container.
- Inside, it runs the contract-owned checks and the repo's own tests against head.
- It writes a report and manifest to `artifacts/runs/<scenario>/`, containing the image digest, the SHAs and the output hashes.
- The limitations page states that Docker isn't a full security boundary.

## 6. Web (replaces 06-web-design)

These pages follow PRD §3 and architecture §5:

| Page | What it shows |
|---|---|
| `/` | Landing, with the hero diff and the "Run it live" button |
| `/live` | Precomputed presets, and the LIVE result after the reviewer runs or edits |
| `/runs/checkout` | The RECORDED report, with tabs for outcome, findings (before and after side by side), evidence trace, gaps and provenance |
| `/benchmark` | Tier A results |
| `/about` | Thesis, prior art (MaruCheck credited), non-claims and limitations |

**Labels.**
- Results are tagged LIVE, PRECOMPUTED or RECORDED, and each tag has its own colour and badge.
- The three outcomes are visually distinct. INCONCLUSIVE is styled as a first-class result, not as an error.

**Live worker.**
- Pyodide is pinned to an exact version I can verify at build time, and loaded from jsDelivr with SRI on the loader.
- The engine wheel is served from our own origin, and its sha256 is checked before install.
- A 2 s timer runs on the main thread. On timeout it calls `worker.terminate()`, records a `timeout` gap, and starts a fresh worker for the next request.
- If the worker can't load, the page says "Live analysis unavailable" and keeps the presets.
- Only the test text can be edited. The contract is bundled with the build.

**Security headers and build checks.**
- A CSP and other security headers are set in `vercel.json`, following architecture §8.1.
- The build **fails** if any number on the page doesn't come from `artifacts/manifest.json`.

## 7. Safety (architecture §7.3–7.4, adopted as is)

**Limits:**
- 64 KiB and 2,000 lines per side
- bracket nesting ≤ 100 and indentation ≤ 50, checked with a `tokenize` pre-scan before `ast.parse`
- 2 s wall clock per analysis

**No-exec guarantee.**
- A CI test scans the core modules and fails if it finds `eval`, `exec`, `compile`, `__import__`, `importlib`, `runpy`, `pickle`, `literal_eval`, `subprocess`, `os.system` or `socket`.
- A second CI scan fails if the engine imports any LLM SDK or anything from `spike/`.

## 8. Evaluation: Tier A (replaces 07-evaluation-plan)

**Order (each step is its own commit, and the commit order is cited on the benchmark page):**
1. `benchmark/patterns.md` is written: a rule-agnostic list of weakening patterns and legitimate-edit patterns, drawn from the primer §3.5 and PRD S/B examples plus extra patterns that no rule targets.
2. `benchmark/sources.lock.json` is written: 4–6 permissively licensed (MIT/BSD/Apache) pytest repos, **not** spike repos (R9). Each entry records its pinned SHA and its license, which I verify from the license file at that SHA.
3. The cases are generated, and `split.lock.json` freezes the held-out 30%. The split is a seeded shuffle, `random.Random("20261120-TA")`, and its hash is recorded.
4. **Only after these three steps** is the detector code written.

**Cases.**
- A generator applies one pattern to one real test function at a pinned SHA, which gives a (base, head) pair.
- No third-party code is committed. The repos are fetched at regenerate time (ADR 0005).
- Targets, from PRD §6.1:
  - ≥ 10 weakening cases per rule, about 70 in all, with the count-preserving cases tagged
  - about 20 legitimate cases
- Each case gets a synthetic contract in which the mutated test guards a mandatory condition. That way blocking severity is exercised on every case.
- Half of the legitimate cases carry an approved contract change.

**Baseline.**
- `baseline_count.py` reimplements the publicly documented count-based rules: test deletion, new skip/xfail, removed assertions and a drop in net assertion count.
- Each rule cites its source PR from PRD §6.3.
- It is labeled "reimplementation" everywhere it appears.

**Metrics (generated, each with a 95% Wilson interval):**
- held-out pooled recall, for PROVECODE and for the baseline
- recall on the **count-preserving subset**. This is the F1 hypothesis from PRD §8.3, and the page shows plainly if it isn't supported.
- per-rule recall, which is descriptive only
- the rate at which legitimate cases are flagged as blocking (F3)
- runtime and peak memory per case
- worst-case behaviour on pathological inputs (deep nesting, maximum size)
- every result shown both for the tuning split and for held-out

**One command.** `python -m benchmark.regenerate` writes `artifacts/benchmark/tierA.json` and the manifest. CI runs it again and fails if the output differs from what is committed.

**Parity check.** CI also runs the held-out cases through the engine inside Pyodide, using Node's `pyodide` package, and diffs the findings against CPython. Any difference is shown on the site.

**Disclosed limitations:**
- the same author wrote the patterns, the cases and the detector
- cases are injected, not real weakening
- there are about 20 legitimate cases, so the false-positive interval is wide
- there is a single labeler

## 9. Milestones (replaces 08)

Each milestone ends in something demonstrable, and each is a separate commit. I ask you before any push or deploy.

| # | Day | Deliverable | Done when |
|---|---|---|---|
| M1 | 1 | `patterns.md`, `sources.lock.json`, generated cases, frozen split | Committed **before** any detector code |
| M2 | 1 | `limits` + `analyzer` (S1–S4, B1–B3, V0), with tests for every rule: true positive, legitimate negative, and malformed or pathological input | pytest passes |
| M3 | 1 | `contract`, `decide`, `report`, `git`, `cli`, and the `demo/` checkout repo | `provecode check` gives FAILED for the weakened change and INCONCLUSIVE for the legitimate one, and both JSON reports are written |
| M4 | 1 | Baseline, `regenerate`, CI workflow (tests, no-exec scan, regenerate diff) | `artifacts/benchmark/tierA.json` is generated, and CI passes locally via `act` or on push |
| M5 | 2 | `exec` + Docker recorded run | `artifacts/runs/checkout/` holds a FAILED report with evidence and the image digest, and the legitimate run is VERIFIED |
| M6 | 2 | Web: every page rendering artifacts, and precomputed presets | `next build` passes, and the build fails if a number is missing |
| M7 | 2 | Pyodide worker and parity test | The live result matches the precomputed one on both presets, and the timeout path works |
| M8 | 2 | README, demo GIF, Vercel deploy | A public URL where the §3 story works. Deploying needs your approval |

**If time runs short, cut in this order:** M7 parity in CI (run it by hand instead), then the GIF, then the per-rule chart. The engine tests, the honest labels and the generated-only numbers are never cut.

## 10. Assumptions

- The GitHub repo becomes public before deploy, so Actions minutes are free (architecture §14).
- The contract `approved_by` field uses the owner's GitHub handle.
- The Python engine targets 3.11 and later. Pyodide's Python version (3.13 or 3.14) differs from the local 3.11.3, and the parity check (§8) is how that risk is detected.

---

## 11. As built (2026-11-20)

These are the places where the implementation departs from, or adds to, the sections above. Each one is listed so the docs stay current (ADR 0002).

**Integrity rules**
- **One tuning round.** It ran on the tuning split only, and is logged in `tierA.json` under `tuning_log`. **B2** now counts a top-level `return`, `pytest.skip()` or `if False:` as a guard whenever any statements follow it, not only when visible asserts follow. The reason is that many real tests check through helper functions.
- **R0, test renamed (info).** This was added to make the "unless renamed" clause of B1 concrete.
- **Severity order.** The approved-contract-change rule is applied before the info rules, so a V0 finding is also linked to the approved change.

**Limits and report modes**
- **Two limit profiles.** `Limits.live()` uses the §7 values: 64 KiB, 2,000 lines, nesting 100, indentation 50. `Limits.cli()` raises size to 2 MiB and 50,000 lines, with the same nesting bounds, because real test files in the benchmark exceed 64 KiB.
- **Report mode `static`.** This mode is added for CLI runs. The site still shows only LIVE, PRECOMPUTED and RECORDED.

**Recorded runs**
- The sandbox also uses `--read-only`, `--cap-drop ALL`, `no-new-privileges`, a non-root user and `--pids-limit 128`.
- The repository's tests are recorded as evidence with `owner: repo` and never satisfy a condition on their own.

**Pyodide**
- **Self-hosted (ADR 0008).** The runtime is served from the site, not from jsDelivr. Pyodide 314.0.8 bundles Python 3.14.2.
- **Parity.** `benchmark/pyodide_parity.mjs` runs all 168 Tier A cases, not only the held-out ones.

**Artifacts and CI**
- **Generated outputs.** `regenerate` also writes the live presets (`artifacts/presets/`) and the README results block, and `--check` verifies all of them.
- **CI.** The Pyodide parity check and a Chrome end-to-end run of the demo story (`web/scripts/e2e.mjs`) are added to CI.

**After the engine code review** (also recorded in `tierA.json` under `revision_log`; no Tier A result changed)
- **S4** now fires whenever any base parametrize case is removed. Previously it fired only when the head cases were a strict subset of the base cases, so dropping hard cases while adding a trivial one escaped.
- **B2** also covers:
  - class decorators and class-level `pytestmark`
  - `from pytest import mark`
  - `pytest.xfail()` calls

  It no longer treats non-pytest `.skip()` calls as guards.
- **S1** covers positional `approx` tolerances and non-numeric `approx`.
- **S3** flags a `match=` loosened to a match-anything pattern.
- **Robustness:**
  - An AST deeper than 120 levels is reported as an `input_limit` gap.
  - Line splitting matches the tokenizer.
  - Files with a UTF-8 BOM are analyzed.
  - Renamed test files keep their contract links. A file renamed to a non-test name is treated as deleted (B1).
- **Recorded runs:**
  - Checks run through `python -I`, with the candidate tree appended to `sys.path`.
  - A check counts as **pass** only with exit 0 **and** a JUnit report showing at least one test that ran and passed.
  - The remaining risk of forgery from inside the same process is stated on the site.
- **CLI:**
  - Any internal error exits with 3.
  - Git failures are never treated as an absent file.
  - JSON contracts stay JSON when approved.

**unittest-style tests (ADR 0009, 2026-11-21; recorded in `tierA.json` under `revision_log`; no Tier A result changed)**
- **Test files.** `test_*.py`, `*_test.py`, `tests.py`, and any `.py` file under a `tests/` or `test/` directory. `conftest.py` and `__init__.py` are excluded.
- **Test classes.** `Test*` classes, plus any class with a base whose final name ends in `TestCase` (`TestCase`, `SimpleTestCase`, `test.TransactionTestCase`, ...). Bases are judged by name only, without import resolution. ADR 0009 also mentions bases ending in `Tests`; that part was **not** built, to keep the false-positive surface small.
- **Checks.** `self.assert*` calls are mapped onto the existing check model: `assertEqual`→`==`, `assertGreaterEqual`→`>=`, `assertIs`→`is`, `assertIsNotNone`→not-None, `assertTrue`→truthy, `assertIsInstance`→isinstance, `assertAlmostEqual`→approx (`delta`, or `places` with a default of 7). Project helpers such as `assertMarkdownRenders` count only for B3: their strength is unknown, so argument changes are not judged.
- **S3.** `self.assertRaises*` and `self.assertWarns*` are exception checks. The regex argument of `assertRaisesRegex` counts as `match=`.
- **B2.** `@unittest.skip`, `@expectedFailure`, `@skipIf(<truthy constant>)`, `@skipUnless(<falsy constant>)` (as `unittest.X` or imported from `unittest`), and a leading `self.skipTest()`.
- **Renames.** A rename from a name default runners collect (`test*.py`, `*_test.py`) to one they don't is B1, even inside `tests/`.
- **Tier A-u.** A second injected benchmark (`benchmark/patterns_unittest.md`, `generate_cases_unittest.py`, `*_unittest.lock.json`) was frozen in `bb6f3d5`, before the detector change. `regenerate` writes `artifacts/benchmark/tierA_u.json`, with the same metrics, baseline and held-out split, and with `commit_order.detector_change` in provenance. Pyodide parity covers both tiers.
