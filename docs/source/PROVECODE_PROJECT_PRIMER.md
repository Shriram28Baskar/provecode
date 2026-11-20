# PROVECODE: Project Primer

**Purpose of this file:** give an AI collaborator (Claude Code) a deep, accurate understanding of what PROVECODE is, why it exists, what its code must do, and how the pieces fit, so it can help produce the PRD, architecture, design and developer documents.

**Status:** This primer is a starting understanding, not a locked specification. Challenge anything that is wrong, risky, or over-scoped. Where this file says "OPEN DECISION", ask me before assuming.

---

## 1. One-paragraph summary

PROVECODE is an independent verification layer for software changes. When a developer or an AI coding agent proposes a code change, PROVECODE does not simply ask "do the tests pass?" It asks: **was the change checked against requirements that someone independent approved, was the checking process itself left intact, and is there real, reproducible evidence for each claim?** It then returns one of three verdicts: **VERIFIED**, **FAILED**, or **INCONCLUSIVE**, each backed by an evidence trail.

For this project, the deliverable is **a properly functioning website** (portfolio piece and hackathon entry) that demonstrates this idea end to end, powered by a **real, small verification engine**. It is not meant to be a full enterprise platform.

---

## 2. The problem it solves

AI coding agents can write code and also write or edit the tests that judge that code. That creates a trust loop:

> The same actor writes the implementation, edits the tests, and reports "all tests pass."

Concrete failure modes:

1. The agent **weakens an existing test** so a broken change passes (loosened assertion, deleted test, added `skip`).
2. The agent **changes test or CI configuration** (coverage exclusions, `continue-on-error`, removed workflow step).
3. The change **breaks behavior outside the edited file**, which no existing test covers.
4. An **important requirement has no test at all**, so "tests pass" says nothing about it.
5. The agent's **natural-language explanation** sounds confident but has no evidence behind it.

Passing tests are only meaningful if the tests and the requirements they represent are trustworthy. PROVECODE targets that gap.

### Worked example (use this as the demo scenario)

Developer request to an AI agent: *"Fix the checkout discount calculation without changing the existing API or breaking other checkout functionality."*

Baseline code and test:

```python
# checkout/pricing.py (baseline)
MAX_DISCOUNT = 0.20

def apply_discount(price: float, discount: float) -> float:
    if price < 0 or discount < 0:
        raise ValueError("invalid input")
    discount = min(discount, MAX_DISCOUNT)
    return round(price * (1 - discount), 2)
```

```python
# tests/test_pricing.py (baseline)
def test_discount_is_capped_at_20_percent():
    assert apply_discount(100, 0.50) == 80.00
```

The agent's candidate change "fixes" something but also weakens the cap, and edits the test so everything still passes:

```python
# candidate
def apply_discount(price: float, discount: float) -> float:
    if price < 0 or discount < 0:
        raise ValueError("invalid input")
    return round(price * (1 - discount), 2)   # cap removed
```

```python
# candidate test (weakened)
def test_discount_is_capped_at_20_percent():
    assert apply_discount(100, 0.50) >= 0      # no longer checks the cap
```

Result: **the repository's own tests pass**, but the 20% cap guarantee is gone. PROVECODE must catch this using (a) the test-integrity analysis, which sees the assertion was weakened, and (b) a contract-owned check that independently asserts the cap and fails against the candidate.

The expected verdict is **FAILED**, with evidence linking the violated requirement to the failing check and to the weakened test.

---

## 3. Core concepts (the vocabulary of the project)

### 3.1 Baseline vs. candidate
- **Baseline:** the trusted, known version (for example, the target branch before the change).
- **Candidate:** the proposed version being evaluated (for example, the PR head).
- The system always records exact commit SHAs for both, and always keeps them separate.

### 3.2 Verification contract
A structured, versioned, **approved** description of what a change must satisfy. It is the system's source of truth for "what correct means."

A contract contains **conditions**. Each condition has: an ID, a human description, a type (functional, boundary, API-compat, protected-behavior, security, performance), a **mandatory** flag, and a link to one or more **checks** that can verify it.

Key rules:
- Contracts are **versioned, hashed, and approved by a human** (or an explicitly trusted policy). An LLM may draft conditions but never approves them.
- **The candidate change must not be able to modify the contract.** This is the central independence mechanism (see 3.4).

Illustrative contract format:

```yaml
contract:
  id: checkout-discount-fix
  version: 3
  approved_by: saish
  approved_at: 2026-11-19T10:00:00Z
  content_hash: sha256:...
conditions:
  - id: REQ-1
    description: Discount results are correct for valid inputs
    mandatory: true
    checks: [chk-discount-values]
  - id: REQ-2
    description: Discount never exceeds 20%
    mandatory: true
    protected: true
    checks: [chk-discount-cap, chk-discount-property]
  - id: REQ-3
    description: Invalid inputs raise ValueError
    mandatory: true
    checks: [chk-invalid-inputs]
  - id: REQ-4
    description: Public signature of apply_discount is unchanged
    mandatory: true
    checks: [chk-api-signature]
```

### 3.3 Checks and evidence
- A **check** is a concrete, executable or analyzable verification step (a pytest test, a Hypothesis property, a signature comparison, a static-analysis run, a test-integrity analysis).
- **Evidence** is the recorded result of running a check: outputs, exit codes, counterexamples, diffs, logs, environment info, commit SHAs.
- Every conclusion must trace: **Requirement → Condition → Check → Execution → Result → Evidence → Decision.**

### 3.4 Independence (the main idea)
Independence means three separate things, and the design must enforce all three:

1. **Contract independence:** the candidate (and the agent that produced it) cannot edit the contract. Store it where the candidate has no write access (protected path owned by reviewers, a separate repo or branch, or an external store). Pin by version and content hash.
2. **Check independence:** conditions are verified by **contract-owned checks** that PROVECODE runs against the candidate, regardless of what the candidate did to its own tests. The repo's own tests are additional evidence, not the authority.
3. **Decision independence:** verdicts come from deterministic rules over evidence, not from an LLM's opinion.

### 3.5 Test integrity (verification weakening)
Detect when the **process of verification** was weakened. Compare baseline and candidate for:

- Deleted test functions or files
- Removed or loosened assertions (for example `== 80.0` to `>= 0`, or an assert removed entirely)
- Added `skip`, `xfail`, `pytest.mark.skip`, `if False:` guards
- Changed parametrized values (removed boundary cases)
- Widened mocks or fixtures that bypass real logic
- Test and coverage configuration changes (`pytest.ini`, `pyproject.toml`, `.coveragerc`: new `omit`, lowered `fail_under`, changed `testpaths`)
- CI workflow changes (removed steps, `continue-on-error: true`, changed triggers)

Important nuance: **not every test change is malicious.** Legitimate requirements change tests. So findings are classified (for example `info`, `warning`, `blocking`) and are linked to contract conditions. A weakening that touches a protected condition and has no approved contract change is **blocking**.

### 3.6 Verdicts
- **VERIFIED:** every mandatory condition has passing, sufficient evidence within the declared scope, and no unresolved blocking integrity finding. This is **not** proof of correctness.
- **FAILED:** at least one mandatory condition has failing evidence, or a blocking integrity finding exists.
- **INCONCLUSIVE:** nothing definitively failed, but at least one mandatory condition lacks sufficient evidence (no check mapped, check skipped, setup failed, timeout, unsupported).

Precedence: **FAILED > INCONCLUSIVE > VERIFIED.** The system must **never** turn INCONCLUSIVE into VERIFIED just because no defect was found.

### 3.7 Verification gaps
A gap is a condition for which evidence is missing. The report must distinguish **"failed"** from **"never adequately evaluated."** Gap examples: mandatory condition with no check, check skipped, dependency install failed, modified function with no covering test.

---

## 4. What the code must do (engine pipeline)

The engine is a Python package with a CLI (for example `provecode verify --repo . --base <sha> --head <sha> --contract contracts/checkout.yaml`). It outputs a machine-readable JSON report and a human-readable Markdown/HTML report. The website renders these reports.

```
Git diff ──► Change analysis ──► Contract load/validate ──► Test-integrity analysis
                                                     │
                                                     ▼
                       Verification plan ──► Isolated execution ──► Evidence collection
                                                                     │
                                                                     ▼
                                          Gap analysis ──► Decision engine ──► Report
```

### 4.1 Repository and change ingestion
- Inputs: repo path or URL, baseline ref, candidate ref.
- Produce: exact SHAs, list of changed files, diff hunks, commit metadata.
- Keep baseline and candidate in **separate working copies**. Never modify the original repository.

### 4.2 Change analysis (impact)
- Use Python `ast` (Tree-sitter optional later) to identify modified functions and classes.
- Build a simple import graph and (best-effort) call graph.
- Map changed code to related test files (by imports, naming, coverage data if available).
- Output: changed symbols, likely-affected modules, related tests.
- **Honesty rule:** impact analysis is best-effort and must say so. Never claim complete coverage of affected components.

### 4.3 Contract engine
- Load, schema-validate (Pydantic), and verify integrity (hash and approval status).
- Reject an unapproved or tampered contract.
- Detect if the candidate diff touches the contract path, and flag it as blocking.
- LLM assistance (optional) may *suggest* conditions from natural-language requirements, but suggestions are drafts until approved.

### 4.4 Test-integrity engine
- Parse baseline and candidate test files with `ast` and compare per test function.
- Produce `IntegrityFinding` records: kind, file, test name, before/after snippets, severity, linked condition IDs, rationale.
- Inspect config and CI file diffs with rule-based checks.
- Must be **deterministic** and explainable. Each finding must cite concrete lines.

### 4.5 Verification planning
- From contract + impact analysis, build a plan of checks.
- Each planned check ends in one status: `executed`, `skipped` (with reason), or `unsupported`. Nothing silently disappears.

### 4.6 Isolated execution
- Run candidate code in a sandbox (Docker for MVP): no network by default, CPU/memory/time limits, read-only source mount, separate working copy.
- Treat repository code as **untrusted**. Do not describe Docker as a complete security guarantee.
- Record environment info (Python version, dependency lock, image digest).

### 4.7 Checks the MVP engine should support
1. **Contract-owned pytest checks** (primary independent evidence).
2. **Repo test suite execution** (secondary evidence).
3. **Test-integrity analysis** (section 4.4).
4. **API signature comparison** (baseline vs candidate public function signatures).
5. **Property-based checks** with Hypothesis for selected conditions (for example, "final price is never negative", "discount never exceeds cap").
6. **Static analysis** (Ruff, Mypy) as supporting evidence.

Later (not MVP): mutation testing, differential testing across wide input spaces, Semgrep/CodeQL security checks, multi-language support.

### 4.8 Evidence and traceability
Every executed check produces an `Evidence` record. The report maps each condition to its evidence and shows coverage of mandatory conditions.

### 4.9 Decision engine
Pure, deterministic function over (contract, evidence, integrity findings, gaps). Pseudocode:

```python
def decide(contract, evidence, findings, gaps):
    if any(f.severity == "blocking" for f in findings): return FAILED
    for c in contract.mandatory_conditions:
        ev = evidence_for(c)
        if any(e.result == "fail" for e in ev): return FAILED
    for c in contract.mandatory_conditions:
        if not sufficient_passing_evidence(c, evidence): return INCONCLUSIVE
    return VERIFIED
```

Each decision includes the specific reasons (condition IDs and evidence IDs) that produced it.

### 4.10 Reporting
- JSON report (canonical, machine-readable, versioned schema).
- Markdown report (for PR comments).
- The web UI renders the JSON.

---

## 5. Data model (starting sketch)

Entities the engine and website both need:

| Entity | Key fields |
|---|---|
| Repository | id, url/path, default branch |
| Run | id, repo, base_sha, head_sha, contract_version, started_at, finished_at, verdict |
| Contract / ContractVersion | id, version, hash, status (draft/approved), approver, created_at |
| Condition | id, contract_version, description, type, mandatory, protected |
| Check | id, kind (pytest/property/api/static/integrity), condition_ids, owner (contract/repo) |
| CheckExecution | id, run, check, status (executed/skipped/unsupported), started/finished, exit_code |
| Evidence | id, execution, result (pass/fail/error), output ref, counterexample, env info |
| IntegrityFinding | id, run, kind, file, before/after, severity, linked_conditions, rationale |
| Gap | id, run, condition, reason |
| Decision | run, verdict, reasons (condition IDs, evidence IDs, finding IDs) |

Reproducibility metadata on every run: SHAs, contract hash, tool versions, container image digest, timestamps.

---

## 6. The website (primary deliverable)

The site is the product the portfolio reviewer or hackathon judge sees. It must feel like a real developer tool, not a mock-up.

### 6.1 What it should communicate
1. The problem in seconds ("tests passed, but the guarantee was removed").
2. A real, interactive demonstration of PROVECODE catching it.
3. Trust: evidence you can inspect, not just a badge.

### 6.2 Candidate pages and flows
- **Landing:** problem, the checkout example as a before/after diff, call to action.
- **Run / demo:** pick a scenario (or submit a PR/commit pair), watch the pipeline stages progress.
- **Report view:** verdict banner (VERIFIED / FAILED / INCONCLUSIVE), summary, then tabs:
  - Changed files and impact
  - Contract (conditions, version, approval)
  - Test integrity findings (side-by-side diff of weakened assertions)
  - Verification plan and what executed / skipped / unsupported
  - Evidence trace (click a condition to see its checks and raw evidence)
  - Gaps
- **Benchmark page:** results of PROVECODE vs. baselines (tests only; tests + static analysis) on the labeled scenarios.
- **About / how it works / limitations:** honest scope.

### 6.3 Design principles
- Evidence first: every claim is clickable down to raw output.
- Three outcomes are visually distinct and never blurred.
- Show uncertainty and limits explicitly. INCONCLUSIVE should look like a first-class result, not an error.
- No fabricated data. If a result is a **recorded run**, label it as such and link to the real report artifact.

### 6.4 Likely stack (to validate)
Next.js + TypeScript + Tailwind on Vercel; Supabase for auth and stored reports; Recharts for charts. Engine in Python (FastAPI wrapper if needed).

**OPEN DECISION: live vs. recorded.** Should the site run verification live on submitted changes, or present real recorded runs of prepared scenarios (with optional live runs for the demo scenarios)? Live needs sandboxed execution infrastructure (Vercel itself cannot run isolated Docker jobs; options such as GitHub Actions, a small VM/worker, or a hosted sandbox service need to be researched and verified). Recorded is faster and safer. Ask me before choosing.

---

## 7. Deterministic vs. LLM-assisted (strict separation)

| Deterministic (decides outcomes) | LLM-assisted (suggests only) |
|---|---|
| Diff parsing, AST comparison | Drafting contract conditions from plain-English requirements |
| Test-integrity rules | Explaining findings in plain language |
| Check execution and evidence capture | Suggesting extra test ideas |
| Decision engine | Summarizing failures for the report |

Rules: LLMs never approve contracts, never produce the final verdict, and the system must still run fully with no LLM available.

---

## 8. Evaluation (what makes it credible)

A small, reproducible, **labeled** benchmark, for example 15-25 scenarios, each with a known expected outcome:

- Functional and boundary defects
- Regressions in unmodified code paths
- **Verification-weakening** (removed assertion, deleted test, new skip, coverage exclusion, weakened CI)
- **Legitimate changes** (correct fix, intentional API change with an approved contract update, valid test update, behavior-preserving refactor)

Compare four configurations on identical inputs:
1. Repo tests only
2. Tests + static analysis (Ruff/Mypy)
3. Tests + static analysis + change-aware selection
4. PROVECODE

Metrics: defect detection rate, weakening detection rate, **false positive rate** (legit changes wrongly flagged), **false acceptance rate** (bad changes accepted), traceability completeness, runtime overhead.

Rules: label injected defects as injected; never present modified tests as untouched history; never fabricate results. A hypothesis that is not supported by results should be reported as such.

---

## 9. Competitive context (must be verified, not assumed)

Existing tools overlap with parts of this idea. At minimum investigate and document:

- **MaruCheck:** a recent open-source tool positioned as independent QA for AI-generated code. Its public descriptions mention quality contracts, git-diff and change-impact analysis, risk-based verification, test orchestration, semantic drift detection, and CI/GitHub checks. **Read its actual repo and docs; do not rely on third-party summaries.**
- Semgrep, CodeQL, SonarQube (static and security analysis)
- Mutation testing tools (Mutmut, Stryker)
- Hypothesis (property-based testing)
- Built-in GitHub CI/code review features

The likely differentiator to test (not assume): **contract-anchored detection of verification weakening, three-valued verdicts with explicit gap analysis, and a published benchmark.** If an existing tool already does this equivalently, narrow the project or change its angle. Do not claim novelty because known techniques are combined.

---

## 10. Scope

### In scope (MVP direction)
- Python repositories only
- Git diff ingestion between two refs
- Changed-symbol detection and basic related-test mapping
- Approved, versioned YAML contracts with a protected location
- Test-integrity analysis (assertions, deletions, skips, config, CI)
- Contract-owned pytest checks + repo test run + Hypothesis properties + API signature check + Ruff/Mypy
- Isolated Docker execution
- Evidence trace, gap analysis, deterministic verdicts
- JSON + Markdown reports
- A polished website that renders real reports
- The labeled benchmark

### Explicitly out of scope
- Multi-language support
- Formal proof of correctness
- Detecting every possible regression
- Autonomous deployment
- Distributed verification infrastructure
- Replacing code review or testing frameworks
- Full enterprise features (SSO, billing, multi-tenant admin)

### Later (only after MVP)
Mutation testing, wide differential testing, Semgrep/CodeQL integration, PR bot, more languages.

---

## 11. Known risks and limitations (the product must state these honestly)

1. Incomplete requirements produce incomplete contracts.
2. Static impact analysis misses dynamic behavior.
3. Test-integrity rules can produce false positives; classification and approval flow matter.
4. Property-based testing is not exhaustive.
5. A behavioral difference is not automatically a regression.
6. Sandboxed environments differ from production.
7. Docker isolation is not a complete security boundary.
8. VERIFIED means "no mandatory condition failed within the declared scope," not "correct."
9. Other tools may already cover large parts of this.

---

## 12. How Claude Code should work with this primer

You are acting as a senior verification architect, developer-infrastructure engineer, and product engineer. Work in **gated stages**. Do not start implementation until I approve the design documents.

### Rules
- Ask me **one question at a time** when you need a decision. State your recommended default with each question.
- Never fabricate results, citations, coverage numbers, or competitor features. If you cannot verify something, say so.
- Prefer the smallest credible design. Challenge scope that does not serve a working demo and a credible benchmark.
- Keep deterministic and LLM-assisted components clearly separated.
- Record every major decision as a short ADR.

### Stages and deliverables (all in `docs/`)
1. `00-competitive-audit.md`: verified comparison against MaruCheck and the tools in section 9, with sources, plus a clear statement of the remaining gap (or a recommendation to narrow the project).
2. `01-PRD.md`: narrowed thesis, target users, demo story, features, MVP, non-goals, success criteria.
3. `02-architecture.md`: modules, data flow, deployment model (resolve the live vs. recorded decision), trust boundaries and threat model for execution.
4. `03-contract-spec.md`: contract schema, versioning, approval, protected storage, tamper detection.
5. `04-test-integrity-spec.md`: detection rules, severity classification, false-positive handling, examples.
6. `05-engine-design.md`: CLI, module interfaces, report JSON schema, decision logic.
7. `06-web-design.md`: pages, flows, component list, visual direction, how reports map to UI.
8. `07-evaluation-plan.md`: scenario catalog, baselines, metrics, falsification criteria.
9. `08-milestones.md`: thin vertical slices, each ending in something demonstrable.
10. `adr/`: one file per decision.

After each document, **stop and wait for my approval** before the next.

### First action
Read this primer, then:
1. Do the competitive audit (stage 1) using web research and the actual MaruCheck repository.
2. Report what you found and recommend whether to keep, narrow, or change the project's angle.
3. Ask me the first open question (start with live vs. recorded).

Do not write application code yet.
