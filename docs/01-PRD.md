# 01 — Product Requirements Document

**Stage:** 2 (PRD)
**Date:** 2026-11-20
**Status:** Approved, 2026-11-20
**Inputs:** `docs/00-competitive-audit.md` (approved), `docs/adr/0001-hybrid-live-static-recorded-execution.md`, `docs/adr/0002-phased-scope-and-validation-gates.md`, primer §§1–2, 6–11.

All claims about other tools are **as of November 2026** and come from the audit. Section references (e.g. "audit §2.2") point there. Nothing in this document is a measured result. Every number is a **target** or a **threshold**.

---

## 1. Thesis

> PROVECODE detects **semantic** test weakening in Python changes, including the count-preserving cases that assertion-counting detectors miss. It ties every finding to an approved contract condition, and backs the claim with a public, labeled, reproducible benchmark.

**Our contribution is semantic meaning.** It answers two questions about each change to a test:

1. **How did the test change?** For example, an equality became a lower bound, an expected value became a trivially true bound, an exception type was widened, or boundary cases were removed. Each finding cites the before and after lines.
2. **Does that change weaken a protected requirement?** The finding is linked to the approved contract condition that the test guarded. Its severity comes from that condition, not only from the edit.

The checkout example is the canonical case. `assert apply_discount(100, 0.50) == 80.00` becomes `>= 0`. There is one assertion before and one after, so a counting detector sees no change. The 20% cap guarantee is gone anyway.

### 1.1 Prior art (cited, not claimed)

As of November 2026, the following already exist. PROVECODE uses or follows these ideas and does **not** claim them:

| Idea | Prior art | Audit ref |
|---|---|---|
| Human-approved, hash-versioned requirement contracts kept outside the code under test | MaruCheck Quality Contracts (`maru contract approve`, SHA-256 version hash, `.history/` snapshots) | §2 |
| Signed, base-anchored tamper detection for contracts, contract tests and root runner config | MaruCheck seal (`packages/seal`, Ed25519, public key from the trusted base commit, replay rejection). Read in source at commit `06f0522`; not run | §2.2 |
| Contract-owned checks run against the candidate | MaruCheck contract tests | §2, §5 |
| Deterministic, LLM-free decisions | MaruCheck ("Severity is deterministic and does not use an LLM") | §2 |
| Explicit inconclusive evidence; "missing evidence is never converted into a pass" | MaruCheck evidence model | §2 |
| Gate that blocks an AI agent from finishing | MaruCheck agent gate (Claude Code Stop hook) | §2 |
| Detecting deleted tests, new skip/xfail markers and assertion-count drops | caiopizzol/skills PR #99, Refactron PR #169, cmux PR #34485, monoharness PR #246, devkit PR #358 | §3 |
| Mutation testing as an indirect test-strength signal | mutmut, Stryker, MaruCheck `maru mutate` (TS only) | §4 |
| Protecting a path with mandatory owner review | GitHub CODEOWNERS + branch protection | §4 |

MaruCheck's seal detects **that** a sealed contract test changed. It does not detect **how** it changed, and it does not cover ordinary repository tests (audit §2.2). MaruCheck has no Python or pytest adapter (audit §2.2, Q2). The count-based detectors acknowledge count-preserving evasions as a gap (audit §3). That is the space PROVECODE occupies.

### 1.2 What we do not claim

- We are not the first to verify AI-written code independently, to use requirement contracts, or to use inconclusive outcomes.
- PROVECODE does not prove correctness. A clean result means "no mandatory condition failed and no blocking weakening was found, within the declared scope."
- We do not claim to detect every weakening. The benchmark measures what is caught and what is missed.

### 1.3 Supporting design choices (not the lead)

- Runs end in one of three outcomes: VERIFIED, FAILED or INCONCLUSIVE. This is a small departure from MaruCheck's inconclusive evidence plus binary gate (audit §6.6), and is presented as a design choice.
- Decisions are deterministic. LLMs never approve contracts or produce outcomes, and the system runs fully without one (primer §7).

---

## 2. Users and what success looks like

**Primary audience:** hackathon judges and technical portfolio reviewers, such as engineers and hiring managers. They have about 3–5 minutes and may check our claims.

**Secondary audience:** Python developers who might try the CLI on their own repository. This is welcome, but the product is not designed around them.

**Success for a reviewer:**

1. **Within 30 seconds** they understand the problem: "tests passed, but the guarantee was removed."
2. **Within 5 minutes**, unaided, they have:
   - run the live detector on the checkout example and seen the `==` → `>=` change flagged, with lines and the linked condition;
   - seen a legitimate test change that is *not* blocked;
   - opened a recorded full run;
   - found the benchmark and the limitations / prior-art page.
3. They leave thinking the claims are **narrow, honest and checkable**. Every result links to a generated artifact, and MaruCheck is credited prominently.

This is the owner's main portfolio project. **Quality matters more than breadth** (§5).

---

## 3. Demo story

The site follows ADR 0001: **live** results come from static analysis only, and **recorded** results come from real engine runs on prepared scenarios. The two are visually distinct everywhere.

1. **Landing.**
   - Headline: "Tests passed. The 20% cap is gone."
   - A before/after diff of `tests/test_pricing.py`, with the baseline assertion as the `-` line and the weakened assertion as the `+` line:
     ```diff
     - assert apply_discount(100, 0.50) == 80.00
     + assert apply_discount(100, 0.50) >= 0
     ```
   - One call to action: **Run it live**.
2. **Live detector (static, no code execution).**
   - The checkout scenario is preloaded.
   - The result shows a **blocking** finding, "assertion weakened: equality replaced by lower bound", citing `tests/test_pricing.py` before and after lines. It is linked to contract condition **C1, "discount is capped at 20%"**, with the contract version and hash shown.
   - The reviewer can **edit the test** in the page and re-run. The analysis is static only and nothing submitted is imported or executed.
   - Because execution-dependent conditions can't be checked live, the live result shows them as gaps (ADR 0001).
3. **Legitimate change (not blocked).**
   - A second preset: the business approved raising the cap to 25%. This is contract **v2**, with C1 changed to "capped at 25%" and a new approval and hash.
   - The test changes from `== 80.00` to `== 75.00`.
   - The detector reports the edit (the expected value changed, and the comparison strength is unchanged), links it to the approved contract change, and does **not** mark it blocking. This shows the detector doesn't flag every edit.
4. **Recorded full run.** "See the full run" opens a report labeled **RECORDED**, with a distinct visual treatment. It shows:
   - the contract-owned check for C1 failing against the candidate;
   - the repository's own tests passing;
   - the weakening finding;
   - outcome **FAILED**, with an evidence trace (condition → check → raw output);
   - provenance: base and head SHAs, contract hash, tool versions, timestamp, and a link to the raw report artifact.
5. **Benchmark page.** Generated results per tier and per rule, with confidence intervals, baselines (including where the count-based baseline does well), negative results and provenance.
6. **About / limitations / prior art.** The thesis, §1.1 prior art with MaruCheck credited, §1.2 non-claims, and the §9 risks written for users.

---

## 4. Features by gate

The work is delivered in gates, not dates (ADR 0002). **Each gate ends in something demonstrable on its own.** Effort estimates are rough guesses for one person working with Claude Code. They are not commitments.

| Gate | Content | Demonstrable at exit | Rough effort |
|---|---|---|---|
| **0** | Feasibility spike on real diffs | A spike report with measured numbers against the kill/pivot criteria (§4.1) | ~1 week |
| **1a** | Engine, contract, CLI, JSON report, Tier A benchmark, CI | `provecode check <base> <head>` on the checkout repo; the Tier A suite regenerated by one command in green CI | ~4–6 weeks |
| **1b** | Recorded Docker runs and deployed site | A public URL that walks through the full §3 demo story | ~4–6 weeks |
| **2** | Config/CI rules, impact analysis, Hypothesis, Ruff/Mypy, Tiers B and C | An updated site and benchmark with Tier B and C results | ~8–12 weeks |
| **3** | Extras | Per item | Not estimated |

**Re-plan trigger.** If a gate reaches about 2× its estimate, stop and re-plan, cutting scope and never the quality bar. Record the outcome in an ADR.

**Hackathon pinning.** If a specific event comes up, Phase 1 (1a + 1b) is pinned to its date and unpolished features are cut. The quality bar is never cut.

### 4.1 Gate 0: feasibility spike and kill/pivot criteria

The spike runs before `02-architecture.md` is locked. These criteria are written **now, before the spike runs**. Final values may be adjusted only by recording the change in an ADR before the spike starts.

- **Spike sample:** about 200 real test-modifying commits from 2–3 candidate Python repositories. The sample is **excluded from every benchmark tier**, to prevent leakage.
- **K1, too noisy:** precision of blocking-severity semantic findings is below **50%** after one tuning round. K1 is measured **after tuning on the same spike sample**, so it is optimistically biased. It is a **floor for continuing**, not an estimate of product precision. The product target is separate (§6.4).
- **K2, can't be made precise:** for a given rule, hand review shows it can't distinguish weakening from legitimate change, for example most of its flags are refactors or intended updates. That rule is dropped. If **at least half** of the semantic rules are dropped, this triggers a pivot.
- **K3, parsing infeasible:** more than **5%** of real test files fail to parse, or base and head tests can't be aligned.
- **K4, weak real-world signal:** zero count-preserving weakening instances are found in the spike sample. This is **not a kill by itself**. It changes the framing.

**If Gate 0 fails:**
- (a) Narrow to the rules that passed.
- (b) On K4, reframe the claim around AI-agent settings (injected and agent-generated scenarios), and state the low observed real-world prevalence as a limitation.
- (c) If K1 or K3 holds broadly, stop and publish the spike as a negative result, then reassess the thesis with the owner before writing more docs.

The outcome is recorded in an ADR either way.

### 4.2 Phase 1 (Gates 1a + 1b): MVP

**Engine (1a)**
- Python and pytest only. The engine takes git diff input between two refs.
- **Semantic weakening rules**, the core contribution. These are AST-based and compare base and head versions of the same test:
  - **S1 Comparison weakened:** for example `==` becomes `>=`, `<=`, `!=`, or a looser `pytest.approx` tolerance.
  - **S2 Expected value replaced by a trivial bound:** for example `== 80.00` becomes `>= 0`, `is not None`, or a bare truthiness check.
  - **S3 Exception widened:** for example `pytest.raises(ValueError)` becomes `pytest.raises(Exception)`, or a `match=` is removed.
  - **S4 Parametrize cases removed:** boundary or other cases are dropped from `pytest.mark.parametrize`.
- **Basic rules**, which are commodity and included for completeness. These are credited as prior art (audit §3):
  - **B1** test function or test file deleted
  - **B2** unconditional skip or xfail added
  - **B3** assertion removed
- **Minimal YAML contract.** Each condition lists the tests that guard it. Each version has an approval record and a content hash. The design follows MaruCheck's contract approach (§1.1). The full spec is in `03-contract-spec.md`.
- **Finding → condition → severity.** A weakening finding on a test that guards a mandatory condition is **blocking**. Other findings are **warning** or **info**. A test edit backed by an approved contract change in the same range is not blocking. The exact rules are in `04-test-integrity-spec.md`.
- **CLI and versioned JSON report.** The report contains findings with before and after lines, condition links, the outcome, and provenance.
- **Contract protection.** For GitHub repositories, contract protection relies on CODEOWNERS plus branch protection (audit §4). The mechanism outside GitHub is open item §10(c). MaruCheck's seal is cited as prior art and not rebuilt.
- **Engine tests** in CI, covering each rule: true positive, legitimate-change negative, and malformed or pathological input.

**Benchmark (1a):** Tier A (§6.1), regenerated by one command, re-run in CI.

**Recorded runs (1b):**
- Contract-owned pytest checks and repository tests run in Docker, locally or in CI, never on the hosting platform (ADR 0001).
- Each run produces a report artifact with full provenance.

**Site (1b):**
- Pages: landing, live detector, recorded report view, benchmark, about / limitations / prior art.
- Live and recorded results are visually distinct.
- The site renders **only generated artifacts**, never hand-entered numbers.

### 4.3 Phase 2 (Gate 2)

- Config and CI weakening rules, which **classify** whether a change weakens verification instead of flagging every edit:
  - `pyproject.toml`, `pytest.ini`, `.coveragerc`: `omit`, `fail_under`, `testpaths`, `addopts`
  - GitHub workflows: `continue-on-error`, removed test steps
  - This is narrower than first stated, because MaruCheck already flags any change to sealed root JS runner config (audit §6.4).
- Change-impact analysis (changed symbols → related tests).
- Hypothesis property checks and Ruff/Mypy in recorded runs.
- Benchmark Tiers B and C (§6.1), and the Ruff/Mypy baseline.

### 4.4 Phase 3 (Gate 3), not committed

Possible additions:
- mock-widening rule
- submitting a public PR or commit URL to the live detector
- LLM-written plain-language explanations, kept separate and never deciding anything
- LLM-judge baseline
- mutation testing
- PR bot

---

## 5. Quality bar

These are requirements, not aspirations. A feature that can't meet them is cut, not shipped rough.

1. **Deployed and usable.** A public URL where a first-time reviewer completes the §3 story in **under 5 minutes** without help.
2. **Fewer features, finished well.** The cut rule: if a feature can't be tested, documented and visually polished within its gate, it moves to a later gate.
3. **Engineering signals:**
   - engine test suite in CI, with CI green on `main`
   - a README that explains the thesis, quickstart and reproduction
   - architecture docs (`02`–`08`) and ADRs kept current
4. **Reproducible benchmark.** One command regenerates every result from pinned commits and recorded environments. CI re-runs it. Site numbers come only from generated artifacts with provenance. **No hand-entered results anywhere.**
5. **Honest claims.**
   - the thesis stays narrow
   - MaruCheck and other prior art are credited (§1.1)
   - limits are stated on the site
   - competitor claims are date-stamped
   - negative results are published
6. **Measured scalability only.** Throughput, memory use and worst-case input behavior of the analyzer are measured and reported. Nothing unmeasured is claimed.
7. **Safe live path** (ADR 0001):
   - input size limits
   - parse timeouts
   - protection against pathological nesting
   - no import, eval or execution of submitted code

---

## 6. Evaluation requirements

The detailed protocol is in `07-evaluation-plan.md`. The PRD fixes the following requirements.

### 6.1 Three tiers

| Tier | What | Measures | Targets | Gate |
|---|---|---|---|---|
| **A** | Injected weakening and legitimate cases on real, permissively licensed (MIT/BSD/Apache) pytest repositories. **Labeled experimental / injected.** | Recall and precision per rule on known cases | 4–6 repos. **≥10 weakening cases per rule** (about 70 for the 7 Phase 1 rules), plus about 20 legitimate edits. If a rule can't reach 10, report pooled recall and mark that rule's recall as *indicative only*. | 1a |
| **B** | Real historical test-weakening commits mined from OSS Python repos, with licenses checked | Recall on real-world weakening | Target **≥30**; report the actual n found, even if lower | 2 |
| **C** | False-positive audit: about **3,000** real test-modifying commits scanned; **300** flagged findings hand-reviewed, **stratified by rule** | **Precision only.** Recall comes from Tiers A and B | Overall and per-rule precision with Wilson intervals. Per-rule intervals are wider, and this is stated | 2 |

- **Tier A variants** are built from a **rule-agnostic list of weakening patterns, written before the rules are finalized**, so the benchmark is not shaped around the detector.
- **Held-out split:** 30% of Tier A is frozen before any tuning and used only for the reported numbers.
- **Held-out recall is reported pooled.** With about 70 weakening cases and a 30% split, each rule has only about 3 held-out cases. Per-rule recall is **descriptive only**, and is always shown with its interval.
- **Legitimate cases in Tier A are few.** About 20 legitimate cases give a wide interval on the false-positive rate. **Tier C is the stronger evidence for false positives.**

### 6.2 Detector-independent recall

Recall must not be sourced through our own detector, otherwise it is circular. Tier B candidates and recall estimates come from detector-independent means:
- candidates found by commit-message keywords or other means that don't depend on the detector, then hand-labeled; and/or
- a **random sample of unflagged** test-modifying commits, hand-labeled to estimate misses.

### 6.3 Baselines (identical inputs)

1. **Repository tests only.**
2. **Tests + Ruff/Mypy** (Phase 2).
3. **Count-based detector, reimplemented.** It is a faithful reimplementation of the **publicly documented rules** of the audit §3 detectors: test deletion, new skip/xfail, removed assertions, and net assertion-count drop, as described in caiopizzol/skills PR #99, Refactron PR #169, cmux PR #34485 and monoharness PR #246.
   - It is **labeled everywhere as a reimplementation, not the original tool**.
   - Each implemented rule cites the PR text it comes from.
   - Results must **report where it does well**. On deletions, skips and count drops it is expected to match PROVECODE, and the page says so.
4. **LLM judge** (stretch goal, Phase 3). It is reported as a baseline only and never used in PROVECODE's decisions.

### 6.4 Metrics and targets

- Per-rule precision, recall and F1, with confidence intervals (Wilson for proportions).
- False-positive rate on legitimate changes, and false-acceptance rate on weakening changes.
- **Target product precision:** blocking-severity precision **≥ 80%** on Tier C, as a point estimate reported with a 95% Wilson interval, on data not used for tuning. This is a **target, not a claim**, and it is separate from the K1 continuation floor (§4.1). Missing it is reported as a negative result.
- Analyzer runtime and memory per commit, and worst-case input behavior (§5.6).

### 6.5 Labeling and selection protocol

These are written **before labeling starts**:
- **Labeling rubric.**
- **Blinded review:** detector output is hidden while labeling.
- **Self-consistency:** a random subset is re-labeled later and agreement is reported.
- **Second labeler:** used for a subset if one is available, with agreement reported. Otherwise the single-labeler limitation is stated (§9).
- **Repository selection** uses pre-registered criteria (stars, pytest usage, license). Every commit SHA is recorded.

---

## 7. Non-goals

From primer §10:
- multi-language support
- formal proof of correctness
- detecting every possible regression
- autonomous deployment
- distributed verification infrastructure
- replacing code review or test frameworks
- enterprise features (SSO, billing, multi-tenant admin)

Added by this PRD:
- **No live code execution** on the hosted site (ADR 0001).
- **No auth and no database in Phase 1.** The site renders static generated artifacts and the live detector is stateless.
- **No novelty claims** for contracts, sealing, inconclusive outcomes, deterministic gates or independent verification (§1.2).
- **No LLM in any decision path.**
- **No rebuild of MaruCheck's seal.** Contract protection uses CODEOWNERS on GitHub. The mechanism outside GitHub is open item §10(c).
- **No JS/TS support.** MaruCheck covers that ecosystem.

---

## 8. Success criteria

### 8.1 Gate exit criteria

- **Gate 0:** spike report published with measured values against K1–K4 and a continue/narrow/pivot/stop decision recorded in an ADR.
- **Gate 1a:**
  - CLI produces a JSON report for the checkout scenario with the S1/S2 finding linked to C1
  - all Phase 1 rules have tests
  - the Tier A suite and its count-based baseline regenerate from one command in green CI
- **Gate 1b:**
  - public URL live and recorded runs published with provenance
  - the full §3 story works end to end
  - the live/recorded distinction is visible on every result
- **Phase 1 exit (1a + 1b):**
  - deployed site
  - Tier A results published
  - CI green
  - README complete
  - **ADRs and docs current**
- **Gate 2:**
  - Tier B and C results published, including negative results
  - config/CI rules covered by tests and benchmark cases

### 8.2 Product criteria (targets, to be measured)

- **Time to understand:** at least 3 people unfamiliar with the project each complete the §3 story unaided in under 5 minutes, timed. Results are reported as observed.
- **Live analyzer latency** is measured and published for inputs up to the size limit. Target values are set in `02-architecture.md` once hosting is chosen (§10b).
- **Blocking precision** target as in §6.4.

### 8.3 Falsification (pre-registered form)

The exact thresholds and statistical tests are fixed in `07-evaluation-plan.md` **before tuning**. The forms are:

- **F1, core hypothesis.** On count-preserving weakening cases in the Tier A held-out split (and in Tier B once available), PROVECODE's recall should exceed the count-based reimplementation's. If it doesn't, the hypothesis is **not supported**, and the site says so.
- **F2, parity where expected.** On deletions, skips and count drops, we expect parity with the count-based baseline and claim no advantage. If the baseline outperforms us, we report it.
- **F3, false positives.** If the share of legitimate Tier A changes flagged as blocking exceeds the pre-registered threshold, we report it as a failure of the severity design. With about 20 legitimate Tier A cases, this rate has a wide interval, so Tier C precision is the stronger false-positive evidence.
- **F4, precision target.** Missing the §6.4 target is reported as a negative result.

---

## 9. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **Single labeler** | Label bias, and no inter-rater agreement | Blinded review, self-consistency re-labeling, a second labeler for a subset if available, limitation stated |
| **Too few real weakening commits for Tier B** | Real-world recall can't be estimated well | Report the actual n; Gate 0 K4 reframing; Tier A stays labeled experimental |
| **False positives on legitimate refactors and intended updates** | Reviewer distrust; low precision | Legitimate-change cases in every tier; contract-change linkage; severity design; Tier C audit |
| **AST rule brittleness** (helpers, custom assertions, fixtures, dynamic tests) | Missed weakenings, or failed base/head alignment | Gate 0 K3; unsupported patterns reported as gaps, never silently passed |
| **Parser DoS on the live path** | Outage or resource exhaustion | Size limits, timeouts and nesting guards (ADR 0001); worst-case input measured |
| **Live vs. recorded confusion** | A recorded result read as live undermines trust | A distinct RECORDED visual treatment and provenance on every recorded result |
| **Scope creep vs. polish** | An unfinished-looking portfolio piece | Cut rule (§5.2), gate exits, the 2× re-plan trigger |
| **Benchmark overfitting** | Inflated numbers | Frozen held-out split, rule-agnostic pattern list, spike sample excluded, pre-registration |
| **Circular recall** | Recall overstated | Detector-independent sourcing (§6.2) |
| **Competitors add semantic detection** | The gap narrows | Competitor claims date-stamped "as of November 2026"; the thesis stands on benchmark evidence, not on being first |
| **Licensing of mined data** | Can't publish the benchmark | Permissive licenses only (pre-registered); store SHAs and minimal excerpts |
| **AI review bots not audited** (Copilot code review, CodeRabbit, Qodo) | They may informally flag weakening | Stated as an audit gap (audit §8); LLM-judge baseline as a stretch goal |
| **Contract protection outside GitHub** | A candidate change edits its own contract | Open item §10(c), resolved in `03-contract-spec.md` |
| **Effort estimates are guesses** | Gates slip | Gates, not dates; the 2× re-plan trigger |

---

## 10. Open items for later documents

- **(a) Live outcomes.** How static-only live results map to VERIFIED / FAILED / INCONCLUSIVE. ADR 0001 says execution-dependent conditions show as gaps, so the outcome is INCONCLUSIVE; how a blocking weakening finding combines with those gaps is open. Decided in `05-engine-design.md`.
- **(b) Where the live Python analyzer runs.** The frontend is planned as Next.js, with no database in Phase 1. Current hosting options will be **researched and verified in `02-architecture.md`, not assumed**. Constraints:
  - static analysis only
  - input size limits and parse timeouts
  - no execution
  - stateless
- **(c) Contract protection outside GitHub.** CODEOWNERS only works inside a GitHub repository. The live demo and the local CLI don't have it. **Requirement:** the contract a check uses must come from a trusted source that the candidate change can't modify. Approaches to evaluate in `03-contract-spec.md` (not decided):
  - in the live demo, contracts are fixed server-side and can't be edited by users;
  - in the CLI, the contract is read from the base ref plus a hash, citing MaruCheck's base-anchored seal as prior art.
- **(d) Final Gate 0 thresholds.** K1–K4 values are proposed above. Any change must be made by ADR before the spike runs.
