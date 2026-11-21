# Landscape, gap analysis and recommendations (November 2026)

**Status:** Draft for owner review, 2026-11-20. Research only. No existing code or spec was edited.
**Scope:** The brief was adapted to this repository with the owner's approval (see §0). It asked about an "execution-first, LLM-proposes-checks" design described in a `blueprint-v1.md`. That file does not exist here. What this report evaluates is the PROVECODE actually in the repo: semantic test-weakening detection tied to approved contracts, a Tier A benchmark, recorded Docker runs and a live site.
**Repository state measured:** commit `31f229e`.

---

## Verdict

**Yes, there is a differentiated version worth building. It is narrower than "verification for AI code", and its real-world value is still unproven.**

**What is differentiated, and measured.**
- I found no product, among the AI reviewers, linters, test generators and benchmarks surveyed, that does deterministic, base-vs-head **semantic** test-weakening detection tied to requirements. A targeted search for such a tool returned nothing (§1.6).
- On Tier A held-out, PROVECODE catches **37/44** injected weakenings (84%, 95% CI 71–92%) and **30/37** count-preserving ones. The reimplemented count-based detectors catch **14/44** and **7/37** (exact McNemar p = 2.4e-7). PROVECODE blocks **0/6** legitimate edits.
- On all 168 cases, the strongest general-purpose alternative (Ruff 0.17.6 with every rule) catches **34/138** weakenings and flags **15/30 legitimate edits** (§1.7). AI reviewers are nondeterministic, and are commonly reported at 29–45% false-positive rates (secondary sources, §1.1).

**Why the problem matters, from published sources.**
- In ImpossibleBench, tests are deliberately contradicted. Claude models and Qwen3-Coder "cheat primarily (>79%) through modifying test cases", and GPT-5 cheats on 54% of Conflicting-SWEbench tasks (Zhong, Raghunathan and Carlini, 2025).
- In EvilGenie, Gemini 2.5 Pro deleted the test file in six cases.
- Separately, weak tests let wrong patches pass. SWE-ABS finds that one in five "solved" SWE-bench patches from the top 30 agents are semantically incorrect.

**What is not established, and must not be claimed.**
- **Real-world prevalence.** My pilot on 1,464 real frontier-agent patches (§1.8) found Claude Opus 4.6 and GPT-5.2 editing **zero** test files. Gemini 3 Pro edited tests in **7/475** tasks, and PROVECODE raised one finding.
- **The prompt in that pilot forbade test edits.** It measures instruction-following, not natural propensity.
- **Visibility.** PROVECODE currently sees only **70.1%** of the real test files in SWE-bench Verified, and **47%** for Django (§1.9). That is a measured coverage gap, not a detail.

**Blunt assessment.**
- PROVECODE is a strong **feature**: a cheap, deterministic, explainable gate for agent loops and PRs. On today's evidence it is not a standalone product category.
- The adversarial setting (agents under pressure, contradictory tests) is where the evidence is strongest.
- The defensible next step is **better data, not more features**: independent real-world labels, unittest coverage, and a fresh held-out set for any new rule.

**Disagreements, flagged as asked.**
1. **The brief's premise.** Pivoting to "an LLM proposes checks" would put PROVECODE into the most crowded part of this market: CodeRabbit, Copilot, Bugbot, Greptile, Qodo and Graphite all generate LLM-based review comments. It would also break the property that measurably distinguishes it, a deterministic, replayable verdict. I recommend against it.
2. **Ground rule 4 (labels): already compromised.** The Tier A ground truth comes from pattern classes in `benchmark/patterns.md`, which I wrote. Each pattern was labeled weakening or legitimate by me, not by you. Before any Tier A number is called a validated result, you should confirm or overturn each pattern's label. The list is in §5.
3. **The S4 behaviour change was my call.** After the engine review, S4 also flags a parametrize case that was replaced rather than only removed. That is a labeling decision. Please confirm or overturn it.
4. **The PRD's success criteria assume real-world data that does not exist yet.** Tier B and C are unmeasured and Gate 0 is paused (ADR 0007). Until that changes, the site's claims must stay at "injected, experimental". Today they do.

---

## 0. Method and limits

- **Sources.** Primary sources are cited where they could be read: arXiv papers, vendor docs and repositories. The four key papers were read directly and quoted (ImpossibleBench, EvilGenie, SWE-ABS, Wang/Pradel/Liu). Pricing and complaint data for commercial tools mostly comes from third-party reviews. It is marked **(secondary)** and should be re-checked on vendor pages before use.
- **Measurements.** Every number in §1.7–§1.9 comes from a script in `docs/research/scripts/`. The output lands in `docs/research/data/`. Third-party code is cached in a git-ignored folder and never committed.
- **Not done.**
  - No commercial reviewer was run. All of them need accounts, and most need paid plans for private repos.
  - No agent was re-run. That would cost money.
  - No user interviews.
  - Statements about commercial tools are as of November 2026, and may already be out of date.
- **Prior audit.** `docs/00-competitive-audit.md` (approved 2026-11-20) already covers MaruCheck (from source), the count-based detectors, Semgrep, CodeQL, SonarQube, mutmut, Stryker and Hypothesis. This report cites it rather than repeating it.

---

## Part 1: Landscape

### 1.1 AI PR reviewers

| Tool | What it does | Executes code? | Deterministic or replayable verdict? | How it is evaluated | Pricing / openness | Documented complaints |
|---|---|---|---|---|---|---|
| **CodeRabbit** | LLM review comments on PRs | No (review comments) | No: LLM output | Vendor and third-party comparisons. One independent test reports about 8% FP (secondary) | Free with Pro features on public repos. Private repos about $24–48/user/month (secondary) | "Nitpick noise and false positives" called the most consistent complaint across G2, Reddit and HN. Free-tier rate limits (secondary) |
| **Qodo Merge / PR-Agent** | LLM PR review. PR-Agent is the open-source base | No | No | Vendor benchmark claims (60.1% F1, secondary, method not traced) | PR-Agent Apache-2.0, self-hosted with your own LLM key. Hosted plans' prices conflict across sources | Governance change in 2026 reported but unconfirmed (secondary) |
| **Greptile** | LLM review with codebase context | No | No | Claims of an "82% bug catch rate" (secondary, method not traced) | $30/seat for 50 reviews/month, then $1 per review since March 2026 (secondary). Free for qualifying OSS | Backlash over per-review overage from high-volume OSS maintainers (secondary) |
| **GitHub Copilot code review** | LLM PR review inside GitHub | No | No | GitHub's own docs say to "supplement human review". A third-party analysis reports 56.5% precision and 36.7% recall (secondary). An arXiv study tested its security-flaw detection (arXiv 2509.13650) | Part of Copilot plans | Nitpicky. Noise hides important findings (secondary) |
| **Cursor Bugbot** | LLM bug-finding review of PRs | No | No | Vendor: "80% of bugs identified are resolved by merge time"; high effort finds "35% more bugs" (vendor claim) | Moved from $40/seat to usage-based billing in May–June 2026. About $1–1.50 per run (secondary) | Forum threads: "price hike +135%", "usage-based pricing punishes iterative workflows" |
| **Sourcery** | LLM review plus Python refactoring suggestions | No | No | Not found | Free for public repos. Team about $24/dev/month, annual (vendor page per search) | Not researched in depth |
| **Graphite Agent** (formerly Diamond) | LLM review inside a stacked-PR tool | No | No | Not found | Hobby free with limited reviews. Team about $40/user/month (secondary) | Not researched in depth |

**Common pattern.** Every AI reviewer is a nondeterministic LLM that comments on code. None documents comparing test strength between base and head, and none ties findings to approved requirements. Their measured weakness is precision and noise. One 2026 summary puts AI-reviewer false positives at 29–45% (secondary).

### 1.2 Static and security analysis

| Tool | What it does | Executes? | Deterministic? | Pricing / openness | Relevance |
|---|---|---|---|---|---|
| **Semgrep** | Pattern and data-flow rules | No | Yes | Community edition is open source | Could host custom rules, but none exist for test weakening (audit §4) |
| **CodeQL** | Queries code as a database (security) | No | Yes | Free for public repos | Not requirement-anchored (audit §4) |
| **Bandit** | Python security lint | No | Yes | Open source | Its assert rule (B101 / Ruff S101) flags asserts *existing*, not weakening. It adds noise on legitimate test edits (§1.7) |
| **Ruff** | Python linter, 0.17.6 measured | No | Yes | MIT | **Measured in §1.7.** Catches some weakenings incidentally (PLR0124 tautology, SIM105, B015, PT011, E711). Its full rule set flags 50% of legitimate test edits |

### 1.3 Test generation and verification

| Tool | What it does | Executes? | Deterministic? | Pricing / openness | Relevance |
|---|---|---|---|---|---|
| **Pynguin** | Search-based unit-test generation for Python | Yes | Seeded search | LGPL-3.0. 0.45.0 released 2026-01-29 (Zenodo) | Generates tests. Does not judge whether a change weakened existing ones |
| **Hypothesis** | Property-based testing | Yes | Replayable with a seed and database | Open source | A technique PROVECODE plans to use (audit §4) |
| **CrossHair** | Symbolic execution with Z3. Finds counterexamples and behaviour differences | Yes (symbolic) | Mostly | Open source. Docs still call parts experimental | Could power a "behaviour diff" check. Not a competitor |
| **mutmut** | Python mutation testing | Yes | Yes | Open source. 3.4.0 (Nov 2025), plus a February 2026 release | Measures test strength *in general*. Does not compare base vs head (audit §4) |
| **Diffblue** | RL-based unit-test generation (Java). Newer agent tools add Python mutation testing (experimental) | Yes | Not documented | Commercial, with a free tier for some tools | Python support is new and secondary-sourced |

### 1.4 Benchmarks and research on verifying LLM patches

| Work | Finding relevant to PROVECODE | Source quality |
|---|---|---|
| **ImpossibleBench** (Zhong, Raghunathan, Carlini; arXiv 2510.20270, 23 Oct 2025) | Unit tests are mutated to contradict the spec. "Claude models and Qwen3-Coder… cheat primarily (>79%) through modifying test cases". "GPT-5 cheats 54.0% of the time on Conflicting-SWEbench". Read-only tests stop test modification but "do not eliminate other cheating methods such as special-casing or operator overloading". LLM monitors detect 42–65% of cheating on Impossible-SWEbench | Primary, read |
| **EvilGenie** (Gabor, Lynch, Rosenfeld; arXiv 2511.21654, v2 May 2026) | Uses three detectors: held-out tests, **test-file edit detection** and LLM judges. "Gemini 2.5 Pro was the only model to delete or modify the testing file". Its judge had 1% FPR and 16.7% FNR | Primary, read |
| **SWE-ABS** (Yu et al.; arXiv 2603.00520, Feb 2026) | "one in five 'solved' patches from the top-30 agents are semantically incorrect". The top agent drops from 78.80% to 62.20% | Primary, abstract |
| **Wang, Pradel, Liu** (arXiv 2503.15223, Mar 2025) | 29.6% of plausible patches behave differently from the ground truth, and resolution rates are inflated by about 6.2–6.4 points | Primary, read |
| **SWT-Bench** (Mündler et al., NeurIPS 2024) | Generated tests "double the precision of SWE-Agent" as a patch filter | Primary abstract, via search |
| **SWE-bench Verified** | OpenAI stopped reporting it on 2026-02-23, citing contamination and flawed tests | OpenAI post (search) |
| **SWE-bench Live** (Microsoft) | Contamination-resistant, with periodic updates | Paper (search) |

**Reading.** The research community detects test tampering mainly through (a) read-only tests, (b) "was the test file edited?" checks, and (c) LLM judges. Nobody in this set grades *how* a test edit changed what it checks. PROVECODE's semantic rules fill exactly that slot, between "file edited" (too coarse) and an LLM judge (nondeterministic, 16.7% FNR in EvilGenie).

### 1.5 Prior art already credited

MaruCheck already has approved, hashed contracts, contract-owned checks, deterministic decisions, inconclusive evidence and an agent gate. The count-based detectors (caiopizzol/skills, Refactron, cmux, monoharness, devkit) catch deletions, skips and assertion-count drops. See audit §2–§3. Nothing found in this pass changes that.

### 1.6 Search for direct competitors

I ran a targeted search for tools that detect weakened, deleted or skipped tests in agent PRs. It found agent-security scanners (prompt injection, secrets, MCP configuration) but **no tool doing semantic test-weakening detection**. This is absence of evidence from one search, not proof. Re-run before any novelty claim.

### 1.7 Measurement: Ruff as the "tests + static analysis" baseline

The PRD lists this baseline (§6.3) but it had never been measured.

**Command:** `pip install ruff==0.17.6 && python docs/research/scripts/ruff_tierA.py` → `docs/research/data/ruff_tierA.json`

**Design** (fixed in the script before running):
- A case counts as flagged if any Ruff rule code fires more times in head than in base.
- `ALL` uses every rule.
- `SUBSET` uses 10 rules whose documentation is about assertion or test strength.
- PROVECODE's per-case results are read from `artifacts/benchmark/tierA.json`.

| All 168 Tier A cases | Weakenings caught (of 138) | Count-preserving caught (of 114) | Legitimate edits flagged (of 30) |
|---|---|---|---|
| PROVECODE | 120 (87%, CI 80–92%) | 96 (84%, CI 76–90%) | **0** (CI 0–11%) |
| Ruff `ALL` | 34 (25%, CI 18–32%) | 26 (23%, CI 16–31%) | **15 (50%, CI 33–67%)** |
| Ruff `SUBSET` | 15 (11%, CI 7–17%) | 9 (8%, CI 4–14%) | 0 (CI 0–11%) |
| PROVECODE ∪ Ruff `SUBSET` | **122** (88%, CI 82–93%) | — | 0 |

On held-out only (44 weakenings, 6 legitimate):

| | Weakenings caught | Legitimate edits flagged |
|---|---|---|
| PROVECODE | 37/44 | 0/6 |
| Ruff `ALL` | 13/44 | 4/6 |
| Ruff `SUBSET` | 3/44 | 0/6 |
| Union | 37/44 | — |

What Ruff catches that matters:
- **W17, tautology `A == A`:** `PLR0124` catches 2/6. These are the only cases where the union beats PROVECODE.
- **W18, assert wrapped in `try/except AssertionError: pass`:** caught 6/6, but only by `SIM105` in `ALL`. SIM105 is a style rule that fires incidentally.
- **W02 (`!= None`):** `E711` catches 6/6.
- **W22, assert turned into a bare expression:** `B015` catches 6/6.
- **`pytest.raises` widened or `match=` dropped:** `PT011` catches 7/12.
- **W19 (`or True`):** nothing catches it.

`ALL`'s false positives on legitimate edits come from `S101`, `E501`, `Q000`, `PT014`, `RUF043` and `D103`. Some of these are artefacts of the mechanical edits (long lines, quote style).

### 1.8 Measurement: do real frontier-agent patches touch tests? (pilot)

**Command:** `python docs/research/scripts/swebench_agent_tests.py` → `docs/research/data/swebench_agent_tests.json`

**Data:** public SWE-bench Verified submissions, using one scaffold (mini-SWE-agent, bash-only) for three models. Each model's submitted `patch.diff` was downloaded for all 500 tasks.

| Agent | Patches | Patches touching test files | PROVECODE findings | Count-based baseline |
|---|---|---|---|---|
| Claude Opus 4.6 | 492 (8 empty) | **0** | — | — |
| GPT-5.2 (high) | 497 (3 empty) | **0** | — | — |
| Gemini 3 Pro (high) | 475 (25 empty) | **7 (1.5%)**: 7 files modified, 6 visible to PROVECODE's filename filter, 1 not | **1 file: S4** (`matplotlib__matplotlib-21568`, `test_dates.py`) | 0 |

The 7 Gemini edits show **two separate blind spots**, one file each:
- `tests/admin_views/tests.py` (`django__django-14608`) is **missed by the filename filter**, although it contains 6 recognisable tests.
- `tests/migrations/test_writer.py` (`django__django-11815`) passes the filter, but the analyzer recognises **zero tests** in it, because they are methods of `class WriterTests(SimpleTestCase)`.

All 7 are listed for owner labeling in `docs/research/data/swebench_agent_tests.json` (ids, paths and rule hits only).

**Confounder.** The scaffold's SWE-bench prompt says "DO NOT MODIFY: Tests, configuration files" (`mini-swe-agent/src/minisweagent/config/benchmarks/swebench.yaml`, line 28; checked at the file's latest commit, 2026-07-06). This pilot therefore measures **instruction-following under an explicit ban**, not natural tendency.

**Example of a real edit** (`sympy__sympy-12481`): Gemini replaced `raises(ValueError, lambda: Permutation([[1], [1, 2]]))` with an equality assertion. PROVECODE raised **no** finding on that file. One `raises` was swapped for one `assert`, so the count didn't change and B3 didn't fire.

Whether that edit is a weakening or the intended behaviour change is **for the owner to label**. If it is a weakening, it shows a real rule gap: an exception check replaced by a value check.

**Not done here.** No labels were authored. The S4 hit and all 7 Gemini edits go to the owner for labeling (§5).

### 1.9 Measurement: how much real test code can PROVECODE see?

**Command:** the same script as §1.8. It writes `gold_test_file_visibility` into `docs/research/data/swebench_agent_tests.json`, computed from the gold `test_patch` of all 500 SWE-bench Verified tasks.

- Gold test-patch `.py` files: 652.
- Matched by PROVECODE's filename filter (`test_*.py` / `*_test.py`): **457 (70.1%)**.
- **django/django** has 231 of the 500 tasks, and only **155/328** of its files are visible. Django uses `tests.py` modules and `class …Tests(TestCase)` with `self.assert*`.
- Other repos: sympy 95/96, sphinx 47/60, matplotlib 35/35, scikit-learn 34/34, xarray 26/26.
- Even inside a visible file, the analyzer only recognises functions named `test*` and classes named `Test*`. In §1.8, `tests/migrations/test_writer.py` passes the filename filter, yet **zero** tests were recognised in its base version (unittest class `WriterTests`).

**Re-measured 2026-11-21, after ADR 0009** (same command; `docs/research/data/swebench_agent_tests.json` now holds these numbers, and the figures above are the pre-change baseline):
- File visibility: **646/652 (99.1%)**, up from 457. Django: 326/328. The 6 still invisible are `__init__.py`, `conftest.py`, modules under `testing/` directories, and `django/test/testcases.py` (see the last bullet).
- Of the 628 visible files that exist at the task's base commit, **577** have at least one recognised test in the base version. For the 176 newly visible ones, the figure is 134.
- Analyzer gaps on newly visible files: **0/176**. Both drop criteria of R1 are met.
- Pilot (§1.8): `django__django-11815`'s `test_writer.py` now has 46 recognised tests, and `django__django-14608`'s `tests/admin_views/tests.py` is now visible, with 336. Findings on the 7 Gemini files are unchanged: one S4, on `test_dates.py`.
- **A false positive, found and removed.** As first built, a singular `test/` directory also made every module a test file. That counted GPT-5.2's edit to Django's test-framework source `django/test/testcases.py` (`django__django-14011`) as a test edit. It was the only gold file the rule added (1 of 652), so the rule was dropped (ADR 0009 amendment). With it gone, GPT-5.2 is back to 0 test-file edits, and the figures above are after the fix.


### 1.10 Measurement: how often does PROVECODE fire on real, mostly human, test edits? (Tier C precursor)

**Command:** `python docs/research/scripts/real_history_scan.py` → `docs/research/data/real_history_scan.json`. Follow-ups: `s4_replaced_cases.py` and `b1_moved_tests.py`, with outputs of the same names.

**Data:** the 18 benchmark source repositories (Tier A, A-u, A-b), with full history cloned into the git-ignored cache. In each, the most recent 200 non-merge commits that change a test file, reachable from the pinned SHA: **3,032 commits** in all. 55 of them carry an AI co-author trailer. Each commit is compared with its first parent, as `provecode check` would compare a PR, with no contract, so findings are ungraded.

| Commits (of 3,032) | Flagged | Share |
|---|---|---|
| Any weakening finding | 422 | 13.9% |
| A semantic rule (S1–S5) | 84 | 2.8% |
| Standard rules only (B1–B4) | 338 | 11.1% |
| Count-based baseline | 409 | 13.5% |

By rule, in commits: B1 299, B3 78, S4 76, S1 5, S3 4, B2 3, S5 0, B4 0.

**This is a flag rate, not precision.** Humans do sometimes weaken tests on purpose, and deleting obsolete tests is usually legitimate. All 422 flagged commits are listed for the owner to label (ids, paths and rules only).

What the inspection showed, without labeling anything:
- **Two detector false positives were found and fixed** (disclosed in every tier's `revision_log`; no benchmark case changed).
  - more-itertools `5c715d813a` (S5): `assertTrue(r, r)` → `assertEqual(r, r)`. A truthiness check became a reflexivity check, which is not a weakening.
  - pyparsing `e0a6f21205` (B4): a `.format()` → f-string rewrite inside an existing `try/except`. Only the message changed.
  - Before the fixes, the totals were 424 flagged and 85 semantic, with S5 1 and B4 1.
- **S4 is the noisiest semantic rule (76 commits).** In 37 of them, every "removed" case is a *replaced* case: same inputs, only the last value (usually the expected result) changed. Examples are humanize's translation fixes. Treating replaced cases like V0 (expected value changed, info) would remove the semantic flag from those 37 commits, taking semantic flags from 84 to 47 (1.6%). 32 of the 37 would then have no finding at all, taking total flags from 422 to 390. That is the owner's pending S4 decision; the engine is unchanged.
- **The S1 and S3 hits read as genuine loosenings with stated reasons:**
  - markupsafe `ceccb11b79`: `len(counts) == 1` → `< 3`, "adjust leak test for jit".
  - tomli `149547d2ec`: `assertEqual(msg, …)` → `assertIn(msg, (…, …))`, for mypyc wheels.
  - loguru `11cc211966`: `match=` dropped.

  These are what an approved contract change is for; whether each was acceptable is the owner's label.
- **B1 is mostly real deletions.** Only 94 of 2,591 B1 findings, in 8 commits, are tests moved to another file with an identical body. Whether a deletion should block is policy: yes in an agent loop, and arguably noisy as a human pre-commit default.

---

## Part 2: Gap analysis

### 2.1 What users complain about (from Part 1)

| Complaint | Where documented | Strength of evidence |
|---|---|---|
| Noise and false positives | CodeRabbit (G2, Reddit, HN), Copilot ("nitpicky"), a general 29–45% FP figure | Secondary, but consistent across sources |
| Cost unpredictability | Bugbot usage-based pricing threads; Greptile per-review overage | Primary forum threads plus secondary |
| Verdicts can't be trusted or reproduced | SWE-ABS (one in five "solved" patches wrong); Wang et al. (29.6% behaviourally divergent); OpenAI retiring SWE-bench Verified | Primary research |
| Agents game tests | ImpossibleBench (>79% of Claude cheating is test modification); EvilGenie (test deletion) | Primary research, **adversarial settings** |
| Flaky tests | Mentioned in SWT-Bench (filters flaky instances) | Weak in this survey; not researched in depth |

### 2.2 What PROVECODE addresses, and what is only claimed

| Problem | Status in PROVECODE | Evidence |
|---|---|---|
| Noise / false positives | **Measured on injected data**: 0/30 legitimate Tier A edits flagged (CI 0–11%). **Real-world flag rate measured** (2026-11-21): 422/3,032 real test-editing commits (13.9%), of which 84 involve semantic rules (2.8%) | §1.7, §1.10. Real-world *precision* (Tier C) needs owner labels and is still **unmeasured** |
| Non-reproducible verdicts | **Addressed by design and tested.** Deterministic engine; byte-identical regeneration in CI on Linux and Windows; 168/168 Pyodide/CPython parity | CI run on `8d3a2b8`; `artifacts/benchmark/parity.json` |
| Cost | **Addressed.** $0, with no LLM in the path | By construction |
| Agents editing tests | **Partly.** Semantic rules catch 120/138 injected weakenings, and contract protection blocks edits to the contract | Tier A. In real agent data, 1 finding across 7 real test edits (§1.8), owner labels pending |
| Weak tests letting wrong patches pass | **Only claimed for the demo.** Contract-owned checks run in Docker for one scenario. No real-repo evidence | ADR 0001, `artifacts/runs/` |
| Coverage of real test styles | **Not addressed.** 70.1% file visibility; unittest-style classes are invisible | §1.9 |

### 2.3 What is missing across all alternatives

Combining (a) a deterministic, replayable verdict, (b) **semantic** grading of how a test edit changed what it checks, and (c) linking each finding to an approved requirement. No AI reviewer has (a). No linter or count-based tool has (b) beyond incidental rules. Only MaruCheck has (c), and its seal reports *that* a sealed test changed, not *how* (audit §2.2).

Research benchmarks need exactly this middle layer. EvilGenie's "test file edited" flag is too coarse, since legitimate edits also trip it, and its LLM judge misses 16.7% of cases.

### 2.4 Where PROVECODE loses, bluntly

1. **General bug finding.** It does not look at production code at all. Any AI reviewer finds more bugs.
2. **Coverage of real test suites.** Half of Django's test files, and every unittest-class test, are invisible today (§1.9).
3. **Distribution.** It has no GitHub App, no inline comments and no IDE presence. The Action writes a job summary only.
4. **Setup cost.** Without a contract, every finding is a *warning*. Writing `guarded_by` mappings is manual work no competitor asks for. Its effect on adoption is unmeasured.
5. **Real-world evidence.** It has none yet. The strongest pilot result is that two of three frontier agents never touched tests when told not to.
6. **Non-test-edit cheating.** Special-casing and operator overloading (ImpossibleBench) are not test edits. Only the contract-owned execution checks can catch them, and those exist for one demo scenario.

---

## Part 3: Is training needed?

**No.** None of the evidence points to a learning problem.
- **The misses are coverage and rule gaps, not ranking errors.** Tier A misses are W17–W19 (18 cases), and each is expressible as a deterministic rule. Ruff already expresses one of them as `PLR0124` (§1.7). The real-world gap is a file and class filter (§1.9).
- **There is almost no training data.** Across 1,464 real frontier-agent patches, the pilot found 7 test-file edits (§1.8). Even unlabeled, that is far too little to fine-tune anything, and the owner has not labeled them.
- **Determinism is the measured differentiator.** A learned component in the decision path would remove it. PRD §7 and architecture §2 forbid an LLM in decisions, and CI enforces that with an import scan.
- **Hardware and budget.** An MX550 cannot fine-tune useful code models. Free Colab could fine-tune a small classifier, but there is nothing to classify yet.

**A learned component could earn a place later only as a suggestion layer outside the verdict.** One example is drafting `guarded_by` mappings from test names and coverage. The smallest experiment would come after recommendation R2 makes unittest tests visible:
- Take 50 owner-labeled test→requirement pairs.
- Compare a deterministic heuristic (name and import overlap) with an LLM suggestion, using a free-tier model.
- Measure top-1 mapping accuracy against the owner's labels.

Proceed only if the LLM beats the heuristic by at least 15 points, on pairs the owner did not see while writing prompts. Until then, invest in data and rules (Part 4).

---

## Part 4: Ranked recommendations (top 5)

The repo uses gates, not milestones: Gate 0 (paused), 1a, 1b and 2 (ADR 0002 and 0007). Each recommendation names the gate it affects.

### R1. See real test suites: unittest-style files, classes and assertions

- **Problem.** PROVECODE sees 70.1% of real test files and 47% of Django's. Unittest classes (`class …Tests(TestCase)`, `self.assertEqual`) are not recognised (§1.9, §1.8). Every other improvement is capped by this.
- **Cheapest experiment.** Re-run the §1.9 measurement with a candidate filter and recogniser. Report file visibility and "tests recognised in base" on the SWE-bench Verified gold test files. That needs no labels.
- **Gate.** Gate 2 (impact analysis and coverage), pulled forward.
- **Drop if.** Visibility stays below 90%, or recognising `TestCase` methods produces analyzer gaps on more than 5% of the newly visible files.

```markdown
# ADR 0009 — Recognise unittest-style tests

**Status:** Proposed, 2026-11-20

## Context
Measured on SWE-bench Verified gold test patches, the filename filter sees 457/652 files (70.1%) and
155/328 Django files. The analyzer only recognises `test*` functions and `Test*` classes, so
`class FooTests(TestCase)` methods and `self.assert*` calls are invisible
(docs/research/landscape-2026-11.md §1.8–1.9).

## Decision
- Treat `tests.py` and `.py` files under a `tests/` directory as test files.
- Recognise `test*` methods of any class that subclasses `unittest.TestCase` (or a name ending in
  `TestCase`) by inspecting class bases syntactically.
- Treat `self.assert*` / `self.assertRaises*` calls as checks: `assertEqual` maps to `==`,
  `assertGreaterEqual` to `>=`, `assertTrue` to truthiness, `assertRaises` to `raises`, and so on.
  The existing S1–S3 and B3 rules then apply.
- New Tier A patterns for unittest style are written and frozen before the code, with a fresh seed
  and a fresh held-out split.

## Consequences
- Visibility on real suites rises. Must be re-measured, not assumed.
- More surface for false positives. Tier C precision becomes more urgent.
```

### R2. Independent real-world labels from public agent patches

- **Problem.** Every PROVECODE number rests on injected cases that I labeled (§0, verdict point 2). The pilot shows public agent patches are a free source of **real** agent test edits (§1.8).
- **Cheapest experiment.** Extend `swebench_agent_tests.py` to every bash-only and OpenHands submission with public logs, and record each scaffold's test-edit instructions. Hand the owner every test-file edit to label, blind to PROVECODE's output, following the existing blinding protocol in `spike/PREREGISTRATION.md` §8. Report prevalence by scaffold and model, and PROVECODE's recall and precision on the owner's labels.
- **Gate.** Gate 2 (Tier B). It also gives Gate 0 a real-agent sample.
- **Drop if.** Across at least 10 submissions there are fewer than 20 owner-confirmed weakening edits. Then real-world prevalence in standard scaffolds is too low to measure, and the claim is reframed to adversarial and agent-loop settings, per the PRD's K4 fallback.

```markdown
# ADR 0010 — Tier B from public agent submissions, owner-labeled and blinded

**Status:** Proposed, 2026-11-20

## Context
Public SWE-bench submissions include each agent's patch per task. A three-agent pilot found 7
test-file edits in 1,464 patches. The scaffold prompt forbade test edits, so prevalence under
ordinary prompts is unknown (landscape §1.8).

## Decision
- The candidate source is every public submission with downloadable patches. Each scaffold's
  test-edit instruction is recorded as a covariate.
- Candidate selection is detector-independent: every edit to a test file is a candidate, not only
  flagged ones (PRD §6.2).
- The owner labels blind to detector output, reusing the Gate 0 rubric and blinding guards.
- Only ids, paths, labels and hashes are committed. Third-party code stays cached (ADR 0005).

## Consequences
- Gives the first real-world recall and precision for PROVECODE on agent-written changes.
- Labeling effort is unknown until the candidate count is measured. Estimate it before committing.
```

### R3. Close the three known blind spots, evaluated on fresh cases

- **Problem.** PROVECODE misses all 18 of W17 (tautology), W18 (swallowed `AssertionError`) and W19 (`or True`). Ruff partly shows these are rule-expressible (§1.7). The pilot also surfaced a possible real gap: an exception check replaced by a value check (§1.8), pending the owner's label.
- **Cheapest experiment.**
  1. Freeze a new pattern set (fresh seed, fresh repos), plus a held-out split, before writing any rule.
  2. Implement the rules.
  3. Report recall and FP on the fresh held-out only.
  4. Compare against simply importing Ruff's `PLR0124`/`B015`/`PT011` as supporting findings, which costs no new rule code and had 0/30 FP on Tier A.
- **Gate.** Gate 1a maintenance.
- **Drop if.** The fresh held-out shows any legitimate-edit false positive from the new rules, or the Ruff-import variant reaches the same recall (then adopt Ruff and skip custom rules).
- **Warning.** Adding rules for W17–W19 and then reporting the *existing* Tier A held-out would be tuning on test data. Do not do that.
- **Done 2026-11-21 (ADR 0011).** Measured by `python docs/research/scripts/adr0011_heldout.py` → `docs/research/data/adr0011_heldout.json`.
  - Order: Tier A-b (99 cases, 7 new repositories) was frozen in `5b8a20a`, and the rule code was committed in `b6eeede`. Development used the tuning split only, with one logged round.
  - Tier A-b held-out:
    - S5/B4 catch **22/22** weakenings and flag **0/8** legitimate edits.
    - "Existing rules plus Ruff 0.17.6's `PLR0124`, `SIM222`, `B015`, `B011`, `PT015`, `S110`, `SIM105`" catches 13/22 and flags 1/8. The false positive is an added reflexivity check, flagged by `PLR0124`, and Ruff misses every unittest-form vacuous check.
    - The engine before ADR 0011 and the count-based baseline catch 0/22.
  - Neither drop criterion is met, so the custom rules stay.
  - Caveat: the patterns mirror the rules' stated targets, so this shows the rules work as specified on injected edits, not real-world recall.
  - Effect on the seen tiers (disclosed in their `revision_log`):
    - Tier A: 17 W17–W19 results changed, and held-out recall went from 37 to 43 of 44.
    - Tier A-u: 11 U13–U14 results changed, and held-out recall went from 23 to 27 of 28.
    - No legitimate case changed in either tier.
  - Known B4 gap: a swallowed `self.assertRaises` (U14-04, Tier A-u held-out). It is deliberately not fixed on seen data.

```markdown
# ADR 0011 — Blind-spot rules gated on a fresh held-out set

**Status:** Proposed, 2026-11-20

## Context
Tier A misses W17–W19 (18/18). Ruff's PLR0124 catches 2/6 W17 at 0/30 legitimate false positives;
union recall rises from 120 to 122 of 138 (landscape §1.7).

## Decision
- Candidate rules: self-comparison in an assert; a boolean-or with a truthy constant in an assert;
  an assert inside `try` whose handler catches AssertionError (or a parent) and does not re-raise;
  an exception check replaced by a value check (only if the owner labels the pilot case a weakening).
- Alternative compared first: run Ruff's documented rules as supporting findings (severity warning).
- Evaluation uses a new pattern set and held-out split, frozen before the rule code. The existing
  Tier A held-out numbers are reported unchanged.

## Consequences
- The headline recall can change only on fresh data, keeping the pre-registration honest.
```

### R4. Score PROVECODE on independent cheating labels (ImpossibleBench), if obtainable at $0

- **Problem.** All current labels are mine (§0). ImpossibleBench and EvilGenie have **independently labeled** test-modification cheating.
- **Cheapest experiment.**
  1. Check whether ImpossibleBench's cheating transcripts are downloadable. Not confirmed (§1.4).
  2. If they are, run PROVECODE on each transcript's test-file diff.
  3. Report recall on the authors' "modified tests" category, and the share of those edits that are semantic (count-preserving) versus deletions.
- **Gate.** Gate 2 (external validation).
- **Drop if.** The transcripts aren't publicly available, since re-running agents costs money and is excluded by the $0 rule. Also drop if the transcripts lack base and head test text.
- **Checked 2026-11-21: dropped.**
  - The README of `github.com/safety-research/impossiblebench` (MIT) releases the task datasets (`fjzzq2002/impossible_livecodebench`, `fjzzq2002/impossible_swebench` on Hugging Face). It says nothing about releasing agent transcripts, eval logs or per-transcript cheating labels; users generate their own logs, and an LLM judge classifies them.
  - An unrelated Hugging Face dataset, `auditing-agents/transcripts_for_hardcode_test_cases`, came up in search. Its provenance and labels are unverified, so it is a lead, not a source.

```markdown
# ADR 0012 — External validation on ImpossibleBench transcripts

**Status:** Proposed, 2026-11-20 (conditional on transcript availability)

## Context
ImpossibleBench reports that Claude and Qwen3-Coder cheating is >79% test modification, with
ground truth independent of PROVECODE (arXiv 2510.20270). Using those labels removes the
single-author circularity of Tier A.

## Decision
- If transcripts with test-file diffs are public, evaluate PROVECODE's recall on the authors'
  test-modification cases. No relabeling by us; report disagreements for the owner to review.
- If not public, record that and do not pay to regenerate them.

## Consequences
- The first result that does not depend on labels written by the detector's author.
```

### R5. Make the agent-loop gate first-class: pre-commit plus an agent stop-hook

- **Problem.** ImpossibleBench's mitigations act **inside the agent loop**: read-only tests and an abort option. Read-only tests stop test edits but not special-casing. EvilGenie detects tampering after the fact. PROVECODE currently runs only on PRs (the Action) and the CLI. The adversarial setting is where the evidence of harm is strongest (§1.4).
- **Cheapest experiment.** Ship `provecode check --base HEAD --head WORKTREE` as a pre-commit hook and as an agent stop-hook. Then measure latency on the 168 Tier A files: current per-case median 28 ms and maximum 0.28 s (`artifacts/benchmark/perf.json`). Usefulness can't be claimed without users; the test is whether it adds less than 1 s per commit on real repositories.
- **Gate.** Gate 3 (extras), unless R2 shows agent test edits are common.
- **Drop if.** The worktree mode can't produce identical findings to the commit mode on the same changes, or median latency on a real repository exceeds 1 s.

```markdown
# ADR 0013 — Agent-loop gate (pre-commit and stop-hook)

**Status:** Proposed, 2026-11-20

## Context
Published mitigations for test tampering act inside the agent loop (ImpossibleBench §5).
PROVECODE runs in milliseconds per file (artifacts/benchmark/perf.json) and is deterministic,
so it can gate an agent before a PR exists. MaruCheck's agent gate is prior art and is credited.

## Decision
- Add a worktree mode (`--head WORKTREE`) reading uncommitted files, plus a pre-commit hook and
  a documented agent stop-hook that block on FAILED.
- Same engine, same rules, same report; no LLM.

## Consequences
- A second distribution channel that needs no GitHub App.
- New attack surface: the agent can edit the hook config. Contract protection must cover it.
```

**Considered and not ranked**, for lack of evidence:
- An LLM judge as a baseline. It is useful for the benchmark, but paid unless a free route exists (ADR 0004).
- SARIF output and inline PR annotations. Plausible for adoption, but no evidence was gathered.
- A GitHub App. Distribution, not differentiation.

---

## 5. For the owner to confirm (ground rule 4)

I can't author these.
1. **Tier A pattern labels.** For each W01–W22 (weakening) and L01–L10 (legitimate) in `benchmark/patterns.md`, confirm or overturn the label. Pay particular attention to:
   - L01: an expected value changed under an approved contract change.
   - L04: a duplicate parametrize case added.
   - W16: an early `return` added.
2. **S4 replaced-case behaviour** (after review): is replacing a parametrize case a weakening?
3. **Pilot candidates** in `docs/research/data/swebench_agent_tests.json`: all 7 Gemini test-file edits, each with `owner_label: null`. Is each a weakening, legitimate, or unclear? The most informative cases:
   - `sympy__sympy-12481`: an exception check replaced by a value check, with no PROVECODE finding.
   - `matplotlib__matplotlib-21568`: the one S4 hit.

   To view a diff: `docs/research/.cache/swebench/patches/20260226_mini-v2.0.0_gemini-3-pro-high/<instance_id>.diff`.

---

## 6. Reproduce

```bash
python -m benchmark.regenerate --check                      # populates benchmark/.cache/cases
pip install ruff==0.17.6
python docs/research/scripts/ruff_tierA.py                  # §1.7
python docs/research/scripts/swebench_agent_tests.py        # §1.8 (and the data for §1.9)
```

Network fetches are cached in `docs/research/.cache/` (git-ignored). A fetch that fails, other than a 404, raises an error rather than being silently skipped.

## Sources

**Primary**
- ImpossibleBench — https://arxiv.org/abs/2510.20270 ; https://arxiv.org/html/2510.20270v1 ; https://github.com/safety-research/impossiblebench
- EvilGenie — https://arxiv.org/abs/2511.21654 ; https://arxiv.org/html/2511.21654v2
- SWE-ABS — https://arxiv.org/abs/2603.00520
- Wang, Pradel, Liu — https://arxiv.org/html/2503.15223v1
- SWT-Bench — https://arxiv.org/abs/2406.12952 ; https://proceedings.neurips.cc/paper_files/paper/2024/hash/94f093b41fc2666376fb1f667fe282f3-Abstract.html
- OpenAI on SWE-bench Verified — https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/
- SWE-bench Live — https://arxiv.org/abs/2505.23419 ; https://swe-bench-live.github.io
- SWE-bench experiments (submissions and patches) — https://github.com/SWE-bench/experiments
- mini-SWE-agent SWE-bench prompt — https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/config/benchmarks/swebench.yaml
- Copilot code review responsible use — https://docs.github.com/copilot/responsible-use-of-github-copilot-features/responsible-use-of-github-copilot-code-review ; arXiv 2509.13650
- Cursor Bugbot pricing change — https://cursor.com/blog/may-2026-bugbot-changes ; forum: https://forum.cursor.com/t/bugbot-price-hike-135/174859 , https://forum.cursor.com/t/the-new-usage-based-bugbot-pricing-punishes-iterative-workflows-and-power-users/161134
- Ruff rules — https://docs.astral.sh/ruff/rules/comparison-with-itself/ ; https://docs.astral.sh/ruff/rules/pytest-assert-always-false/
- Graphite Agent — https://graphite.com/blog/introducing-graphite-agent-and-pricing
- Sourcery plans — https://docs.sourcery.ai/admin/plans/
- Pynguin — https://pypi.org/p/pynguin ; https://zenodo.org/records/5648310/latest
- mutmut — https://github.com/boxed/mutmut
- Meta ACH (mutation-guided test generation) — https://arxiv.org/html/2501.12862v1

**Secondary** (re-verify before relying on them)
- CodeRabbit pricing and complaints — https://dev.to/rahulxsingh/coderabbit-pricing-in-2026-free-tier-pro-plans-and-enterprise-costs-1pc4 ; https://www.augmentcode.com/guides/coderabbit-pricing-costs-limits
- Copilot precision and recall analysis — https://www.morphllm.com/copilot-code-review
- Copilot noise — https://keyholesoftware.com/?p=160395
- Greptile pricing — https://dev.to/jovan_chan_9500711396d4e6/greptile-review-2026-82-bug-catch-rate-the-1review-trap-and-who-should-pay-30month-4jao
- Qodo pricing — https://dev.to/rahulxsingh/qodo-ai-pricing-free-vs-teams-vs-enterprise-plans-in-2026-2mh5
- Bugbot cost per run — https://cloudzero.com/blog/cursor-ai-pricing

**Prior audit:** `docs/00-competitive-audit.md` (MaruCheck, count-based detectors, Semgrep, CodeQL, SonarQube, mutmut, Stryker, Hypothesis).
