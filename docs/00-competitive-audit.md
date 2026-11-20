# 00 — Competitive Audit

**Stage:** 1 (competitive audit)
**Date:** 2026-11-20
**Status:** Approved, 2026-11-20

---

## 1. Method and limits

- **MaruCheck.** I read the README and five documentation guides in the actual repository: contracts, evidence/findings, agent gate, test execution, and mutation verification. I also read the README of the companion study repository. **I did not read MaruCheck's source code and did not run it.** Every MaruCheck claim below therefore reflects what its docs say on the audit date, not observed behavior. "Not documented" means I found nothing in those pages. It does not mean the feature is absent from the code.
- **Third-party summaries excluded.** Pages on aiindigo.com and stork.ai describe MaruCheck. They look auto-generated, contradict each other (for example "open source" vs. "freemium"), and are not used as evidence.
- **Other tools.** I used official documentation pages. SonarSource's page is a marketing page and is labeled as such.
- **Small test-weakening detectors** were found through web search. They are mostly single pull requests in small repositories. I read the PR pages, not the code.
- **Not covered.** I did not evaluate GitHub Copilot code review, Qodo, CodeRabbit, or other AI review bots in depth. That gap is listed in §8.

---

## 2. MaruCheck

Repository: <https://github.com/Kidus-M/MaruCheck>. License: MIT. At the time of reading it had about 8 stars, 1 fork and 184 commits.

| Capability | What the docs say | Source |
|---|---|---|
| Quality Contracts | YAML files in `.maru/contracts/`, described as "a human-owned, versioned file outside the code under test". It parses a restricted subset of YAML that rejects anchors, aliases, tags and duplicate keys. | [README](https://github.com/Kidus-M/MaruCheck), [phase-2-quality-contracts.md](https://github.com/Kidus-M/MaruCheck/blob/main/docs/guides/phase-2-quality-contracts.md) |
| Contract drafting | `maru contract create --from requirements.md`. "Draft creation and validation are deterministic and local." Drafts stay in `draft` until approved. | phase-2 guide |
| Approval and versioning | `maru contract approve <id> --by <approver>` records the approver, a timestamp and a SHA-256 version hash, and writes a snapshot to `.maru/contracts/.history/<id>/<hash>.yml`. "An approved version cannot be approved again." | phase-2 guide |
| Contract change classification | `maru contract diff` classifies changes as *semantic* (behavior, requirements, invariants, evidence policy, quality checks) or *mechanical*. | phase-2 guide |
| Contract tamper detection | **Docs:** the README describes Swarm mode as being on a branch and "not yet released to npm": an Ed25519 seal "held outside the repository" that "refuses to run if a sealed contract, contract test or gate setting has changed". **Source:** the seal is merged to `main` (the `swarm-sprint` branch tree is identical to `main`), and the CLI exposes `maru seal keygen/create/verify`. See §2.2. Whether it is published to npm was not checked. | README; source §2.2 |
| Evidence report | Versioned `report.json` (schema v1) containing evidence, requirement mappings, findings, gate, summary and artifacts. | [phase-7-evidence-and-findings.md](https://github.com/Kidus-M/MaruCheck/blob/main/docs/guides/phase-7-evidence-and-findings.md) |
| Check outcome states | `passed`, `failed`, `inconclusive` (error, skipped, manual, unavailable). A requirement with no result gets explicit `planning-gap` evidence. "Missing evidence is never converted into a pass." | phase-7 guide |
| Overall verdict | Binary gate: `passed` / `blocked`. Inconclusive evidence becomes `execution-error` or `verification-gap` findings with severity. | phase-7 guide |
| Determinism | "Severity is deterministic and does not use an LLM." Risk scoring "does not use an LLM for scoring." | phase-7 guide, README |
| Mutation verification | `maru mutate --diff` mutates changed `.ts` / `.tsx` production files in an isolated temporary worktree. A surviving mutant reports weak verification and blocks. | [phase-13-mutation-verification.md](https://github.com/Kidus-M/MaruCheck/blob/main/docs/guides/phase-13-mutation-verification.md) |
| Agent gate | A Claude Code Stop hook refuses to let the agent finish while the gate is blocked, and tells the agent: "Do not edit or re-approve the contract to make this pass." | [agent-gate.md](https://github.com/Kidus-M/MaruCheck/blob/main/docs/guides/agent-gate.md) |
| Detection of test / assertion / skip / CI / coverage-config edits | **Docs:** not documented. **Source:** no semantic detection. There is hash-based change detection for sealed files only (§2.2). | agent-gate guide; source §2.2 |
| Test runners | **Docs:** Vitest, Jest, Playwright, Cypress, axe, Semgrep, Gitleaks. No Python, pytest or `.py` mention in the README or the test-execution guide. **Source:** confirmed, no Python adapter (§2.2). | README, [phase-6-test-execution.md](https://github.com/Kidus-M/MaruCheck/blob/main/docs/guides/phase-6-test-execution.md); source §2.2 |
| Execution isolation | **Docs:** local execution, no container or network restriction described. **Source:** consistent. Only mutation runs use a temporary git worktree (`packages/mutation/src/worktree.ts`). | phase-6 guide; source |
| Other features | Semantic drift checks, QA memory, GitHub PR verification, MCP server, Challenger agent. | README |

### 2.1 MaruCheck's own study

Repository: <https://github.com/Kidus-M/marucheck-swarm-study>, written for the Apart Research Swarm Alignment Hackathon.

- Splitting an attack across six PRs cut mean LLM-monitor suspicion from 93 to 45. The most suspicious PR in each chain still averaged 88.
- A per-PR LLM monitor caught all 97 attack chains at a 5% honest false-positive budget.
- The contract oracle (MaruCheck swarm mode) ignored splitting and test tampering *when its tests exercised the behavior*. It **missed every narrow-trigger and every self-disabling attack**.
- The benchmark repositories are synthetic. Generated attack fragments and chains are withheld. Only aggregate results are published.

### 2.2 Source-tree findings (read source, not run)

I shallow-cloned `main` at commit `06f0522fd733395ece621dc4377049bde38c634c` (2026-11-09) into a scratch directory. I read the code but **did not build or execute it**. The repository has 214 tracked files and is a TypeScript/Node monorepo under `packages/`. Its latest tag is `v0.6.1`, although `packages/cli/package.json` says `0.1.0`.

**Q1. Does it detect edits to existing tests, assertions, skips, or coverage/CI config?**

There is no semantic detection. The source has three related mechanisms:

1. **Hash seal over protected files** (`packages/seal/src/index.ts`).
   - `collectProtectedFiles()` SHA-256-hashes:
     - `.maru/maru.yml`
     - every `.maru/contracts/*.y(a)ml`
     - root-level runner configs matching `^(vitest|vite|jest)\.(config|workspace|setup)\.[cm]?[jt]s$`
     - files matching `*.contract.(test|spec).[cm]?[jt]sx?`
     - the `jest`, `scripts` and `vitest` fields of `package.json`
   - `verifySeal()` takes the public key from the trusted base commit only, rejects a replayed older seal by its sequence number, and reports "Sealed file changed / missing / not sealed".
   - In `packages/evidence/src/index.ts`, a strict base-range run (`--strict --base`) checks the seal *before* executing anything (throws `SealBrokenError`). It checks again *after* execution and adds any issues to the gate's blocking reasons.
   - **Limits:**
     - Detection is all-or-nothing hash equality. Any edit to a sealed file is reported the same way, with no notion of weakening vs. strengthening.
     - It covers only contract tests (`*.contract.test.*`) and root runner config. **Ordinary repo tests, `.github/workflows/*`, and coverage settings outside those root config files are not covered.**
     - It is opt-in. It requires a `.maru/seal.pub` at the base commit and strict mode, and otherwise returns no issues.
2. **Path classification** (`packages/git/src/classification.ts`). Each changed path gets a label such as `test`, `configuration` or `business-logic` from path regexes. It doesn't parse file contents.
3. **Risk rule** (`packages/risk/src/index.ts`, around line 375). A `tests-unchanged` reason is raised when production code changed but no test file changed. This is the opposite of weakening detection.

Searches of non-doc source for `weaken`, `xfail`, `.skip(`, `assertion` and `continue-on-error` found no weakening logic. The only hits were MaruCheck's own tests and CI.

The only code parser in use is the TypeScript compiler API in `packages/mutation/src/transforms.ts`, which mutates **production** `.ts`/`.tsx` code. No base-vs-head comparison of test files exists.

**Q2. Is there any Python or pytest adapter, even experimental?**

**No.**
- There are no `.py` files in the repository.
- No non-doc file mentions `pytest`, `python` or `unittest`.
- The adapter type in `packages/planner/src/index.ts` (lines 22–30) is exactly `axe | cypress | gitleaks | jest | manual-review | playwright | semgrep | unavailable | vitest`.
- `.py` appears only in a path-classification regex (`SOURCE_EXTENSION` in `classification.ts`), so Python files are labeled `business-logic` for risk scoring but never executed or analyzed.
- The `swarm-sprint` branch has a tree identical to `main`, so there is no Python work there either.

**Gate (source).** The run status in `packages/execution/src/index.ts` is `failed | error | incomplete | passed`. The report gate in `packages/evidence/src/index.ts` (around line 431) is binary: `blocked` if any reason exists, otherwise `passed`. This confirms the docs.

### 2.3 What this means for PROVECODE

MaruCheck already implements most of what the primer treated as PROVECODE's differentiator:

- human-approved, hash-versioned contracts kept outside the code under test
- **signed, base-anchored tamper detection for contracts, contract tests and root runner config** (source-confirmed, on `main`)
- deterministic, LLM-free decisions
- an explicit "inconclusive" evidence state and "never convert missing evidence into a pass"
- a gate that blocks an AI agent

PROVECODE must not claim any of these as novel. The protected-contract design in `03-contract-spec.md` should cite MaruCheck's seal as prior art.

---

## 3. Small test-weakening detectors

Several recent, small implementations detect weakened tests in diffs. None is an established product.

| Project / PR | What it detects | Notes | Source |
|---|---|---|---|
| caiopizzol/skills PR #99 (merged 2026-09-28) | Python: deleted test files; unconditional `skip`/`xfail` (blocking); conditional skips (reported); added bare `return`; removed `assert`, `self.assert*`, mock `.assert_*`, `pytest.raises`/`warns` (counted); `__test__ = False`, `collect_ignore` | Does not document comparison-level loosening analysis. | <https://github.com/caiopizzol/skills/pull/99> |
| Refactron PR #169 (merged 2026-09-27) | Net drop in assertion count, removed or renamed tests, new `skip`/`xfail` | Downgrades a SAFE verdict to **UNPROVEN** and "can never grant SAFE". Described as heuristic. Lists **count-preserving evasions** as a known gap. | <https://github.com/Refactron-ai/refactron/pull/169> |
| manaflow-ai/cmux PR #34485 | Deleted test files, reduced assertion counts, new skip markers, removed test definitions | Runs as warnings or strict failures in CI. | <https://github.com/manaflow-ai/cmux/pull/34485> |
| tinhtute/monoharness PR #246 | Deleted test files, skip markers, trivial assertions, net removal of assertion lines | Overridable with an `allow-test-weakening` label. | <https://github.com/tinhtute/monoharness/pull/246> |
| AbysmalBiscuit/devkit PR #358 | Base-vs-HEAD test file comparison; `.skipIf(`, `.todo(`, `.only(`; tautological assertions | Standalone CLI. | <https://github.com/AbysmalBiscuit/devkit/pull/358> |
| clay-good/OpenLore PR #634 | Diffs that weaken tests and checks | I read only the title from search results. | <https://github.com/clay-good/OpenLore/pull/634> |
| davdittrich/review-code PR #39 | Restores agent edits that weaken tests | States that it does not see a weaker assertion that replaces a stronger one at the same count. | <https://github.com/davdittrich/review-code/pull/39> |

Research on the same problem:

- **EvilGenie** (arXiv 2511.21654) combines held-out tests, test-file edit detection, LLM judges and human review. It measures reward hacking in Codex, Claude Code and Gemini CLI. <https://arxiv.org/html/2511.21654v1>
- **FrogNano** (arXiv 2609.07925) defines patch-level reward-hacking rules, including newly added skip/xfail markers and patches that change only tests. <https://arxiv.org/pdf/2609.07925>
- **hacktrace** (arXiv 2610.03055) is a learned detector trained on annotated coding trajectories. <https://arxiv.org/pdf/2610.03055>

I have only read search-result summaries of these papers, not the full texts.

**What this means for PROVECODE.** Deletion, skip-marker and assertion-count heuristics are a commodity. The **count-preserving semantic weakening** case is a blind spot that the tools themselves acknowledge. The primer's demo (`assert apply_discount(100, 0.50) == 80.00` becoming `>= 0`) is exactly that case: one assertion before, one assertion after.

---

## 4. Other tools

| Tool | What it is | Relevance | Source |
|---|---|---|---|
| Semgrep | Rules that "encapsulate pattern matching logic and data flow analysis" over code | Finds code patterns. Has no notion of approved requirements or of base-vs-head test strength. Could host custom rules, but none for this purpose were found. | <https://docs.semgrep.dev/writing-rules/overview> |
| CodeQL | "A language and toolchain for code analysis" that queries code as a database for vulnerability variants | Security and static analysis. Not requirement-anchored. | <https://codeql.github.com/docs/codeql-overview/about-codeql/> |
| SonarQube (AI Code Assurance) | Quality gates with pass/fail conditions on coverage, issues, duplication and AI-code metrics | The marketing page does not mention weakened or deleted tests or requirement contracts. A coverage gate might catch some deletions indirectly. | <https://www.sonarsource.com/sem/solutions/ai-code-quality/> (marketing) |
| mutmut | "A mutation testing system for Python". Re-tests only mutants in changed functions between runs. Requires fork support (WSL on Windows). | Measures test strength in general. Does not compare base vs. head tests or link to requirements. | <https://github.com/boxed/mutmut> |
| Stryker | Mutation testing for JS/TS, C# and Scala | Same as mutmut; no Python. | <https://stryker-mutator.io/docs/> |
| Hypothesis | "The property-based testing library for Python" | A technique PROVECODE uses, not a competitor. | <https://hypothesis.readthedocs.io/en/latest/> |
| GitHub CODEOWNERS + branch protection | "Require review from Code Owners" makes owner approval mandatory for changes to given paths | The native way to protect a contract path. PROVECODE should rely on it rather than reinvent it. | <https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners> |

---

## 5. Feature matrix

Legend: **Yes** = documented. **Partial** = documented in limited form. **No** = documented as out of scope, or clearly outside the tool's category. **ND** = not documented in the sources read. **Planned** = a PROVECODE intention, not yet built. Cells marked **(src)** come from reading MaruCheck's source (§2.2). All other MaruCheck cells come from its docs.

| Capability | PROVECODE (planned) | MaruCheck | Count-based detectors (§3) | Semgrep / CodeQL | SonarQube | mutmut / Stryker |
|---|---|---|---|---|---|---|
| Human-approved, hash-versioned contracts | Planned | **Yes** | No | No | No | No |
| Contract tamper detection | Planned | **Yes (src)**: signed, base-anchored hash seal, opt-in, strict mode | No | No | No | No |
| Contract-owned checks run against candidate | Planned | Yes (contract tests; hash-sealed (src)) | No | No | No | No |
| Python / pytest | Planned | **No (src)** | Partial (some Python) | Yes (analysis only) | Yes (analysis only) | mutmut: Yes; Stryker: No |
| Deleted test / new skip detection | Planned | Partial (src): any change to a sealed contract test is reported. **No** for ordinary repo tests | **Yes** | ND | ND | No |
| **Semantic assertion loosening at equal count** (e.g. `==` → `>=`) | Planned | **No (src)**: hash equality only, no parsing of tests | **No** (admitted gap) | ND | ND | Indirect* |
| Test/coverage/CI config weakening rules | Planned | Partial (src): any change to sealed root runner config or `package.json` test fields is reported. CI workflows not covered. No weakening classification | Partial | ND | Partial (coverage gate) | No |
| Findings linked to contract conditions | Planned | Partial (requirement mappings for evidence) | No | No | No | No |
| Explicit inconclusive / gap handling | Planned | **Yes** (evidence level; binary gate) | Partial (Refactron UNPROVEN) | No | No | Partial (inconclusive mutants) |
| Three-valued run-level verdict | Planned | No (gate is passed/blocked) | Partial (Refactron) | No | No | No |
| Deterministic decision, no LLM | Planned | **Yes** | Yes | Yes | ND | Yes |
| Mutation testing | Deferred | Yes (TS only) | No | No | No | **Yes** |
| Sandboxed execution | Planned | No (src): local; mutation uses a temp worktree | No | n/a | n/a | Partial (temp worktree / copies) |
| Published labeled benchmark | Planned | Partial (attack study vs. LLM monitors) | No | No | No | No |
| Web report UI | Planned | Partial (hosted report upload guide exists, not read) | No | Yes (vendor UIs) | Yes | Yes (HTML reports) |

\* Mutation testing can expose a weak assertion indirectly, because a mutant survives. It does not report *which* test edit weakened verification, or link it to a requirement.

---

## 6. The gap that remains

Stated narrowly, the gap is:

1. **Python.** The leading contract-based tool has no Python or pytest adapter. This is confirmed from its source (§2.2).
2. **Semantic, AST-level test-weakening analysis** that catches count-preserving loosening. MaruCheck's seal detects *that* a sealed contract test changed, but not *how*. It does not look at ordinary repo tests at all (source, §2.2). Examples of the loosening in question:
   - a comparison operator weakened (`==` → `>=`, `<`)
   - an expected constant replaced by a trivially true bound
   - `pytest.raises(ValueError)` widened to `pytest.raises(Exception)`
   - `pytest.mark.parametrize` boundary cases removed
   - a mock widened to bypass real logic

   Each finding should cite concrete before/after lines. Existing count-based tools say they miss this.
3. **Linking each weakening finding to a protected contract condition**, so the severity (`info` / `warning` / `blocking`) depends on what requirement the test guarded, not just on the edit.
4. **Rules that classify test and CI configuration weakening** (`omit`, `fail_under`, `testpaths`, `continue-on-error`, removed workflow steps). This gap is narrower than first stated. MaruCheck's seal already reports *any* change to root Vitest/Jest config and `package.json` test fields (source, §2.2). What remains is:
   - Python config files (`pytest.ini`, `pyproject.toml`, `.coveragerc`)
   - CI workflow files, which the seal does not cover
   - classifying *whether* a config change weakens verification, instead of flagging every edit equally
5. **A published, labeled, reproducible benchmark** of weakening scenarios. It should include legitimate test changes, to measure false positives, and should compare against (a) repo tests only, (b) a count-based detector, and (c) a contract-only oracle.
6. **A three-valued, run-level verdict.** This is a *small* difference from MaruCheck's inconclusive evidence plus binary gate, and should be presented as a design choice, not a novelty.

**Not a gap** (credit prior art instead): approved hashed contracts, contract-owned checks, signed base-anchored sealing of contracts and contract tests (MaruCheck `packages/seal`), deterministic decisions, "missing evidence is never a pass", mutation testing, and agent stop hooks.

---

## 7. Recommendation: **narrow**

**Narrowed thesis:**

> PROVECODE detects *semantic* test weakening in Python changes, including the count-preserving cases that assertion-counting detectors miss. It ties every finding to an approved contract condition, and backs the claim with a public, labeled benchmark.

**Why narrow rather than keep:**

- Keeping the original framing would mean presenting MaruCheck's documented features as PROVECODE's contribution. That is not credible to a reviewer who checks.
- The narrowed angle is specific, demonstrable in one screen (the checkout demo), and falsifiable by the benchmark.

**Why narrow rather than change:**

- The core demo scenario is exactly the documented blind spot, so the existing primer work transfers almost entirely.

**Scope consequences (to carry into the PRD):**

- **Keep, as supporting structure:** contracts, contract-owned checks, three-valued verdict, evidence trace, gap analysis. Credit MaruCheck explicitly on the About/Limitations page.
- **Promote to core:** the AST test-integrity engine and the benchmark.
- **Defer:** mutation testing, LLM-assisted contract drafting, PR bot.
- **Add a benchmark baseline:** a count-based weakening detector, reimplemented from the publicly documented rules and labeled as a reimplementation, not as the original tool.
- **Do not claim** to be first at independent verification of AI code, contracts, or inconclusive verdicts.

---

## 8. Open uncertainties

- **Resolved:** I read MaruCheck's source tree at commit `06f0522fd733395ece621dc4377049bde38c634c` (`main`, 2026-11-09) without building or running it. It has no semantic test-weakening detection and no Python or pytest adapter. See §2.2.
- The seal is merged to `main` and exposed via `maru seal`. Whether it is published to npm was not checked.
- AI PR-review bots (Copilot code review, CodeRabbit, Qodo, etc.) were not audited. They are LLM-based, so they would not satisfy the deterministic-decision requirement, but some may flag test weakening informally.
- The small detectors in §3 were assessed from their PR descriptions, not their code.
- The research papers were assessed from search summaries, not full texts.
