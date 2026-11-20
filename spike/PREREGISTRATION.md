# Gate 0 Pre-registration

**Status:** DRAFT (Step A). There are no commit SHAs yet; they are added in Step B.
**Date:** 2026-11-20
**Governs:** the Gate 0 rule-feasibility spike (PRD §4.1) and the hosting spike (`docs/02-architecture.md` §12).
**Authority:** ADR 0002 and ADR 0003. This document does **not** change any threshold. K1–K4 (§11) and P1–P7 / V1–V6 (§12) are verbatim copies of the approved docs. §5 only clarifies which sample stratum each K is decided on.

---

## 0. How this document is committed

| Step | What happens | Who |
|---|---|---|
| Commit 1 | This document: procedure, seeds, frozen topic lists, frozen keyword regex and its SHA-256 digest. There are **no** pool results and **no** SHAs | Owner |
| Step B | One sampling script builds the pool snapshot, walks the candidate repos, draws the sample and the reserve, and writes `spike/pool_snapshot.json`. Clones go under `%TEMP%`, outside the repo | Drafter runs it; owner reviews |
| Commit 2 | Snapshot plus the exact SHAs, added to §13 | Owner |
| Step C | `spike/LOG.md` with the Gate 0 start date | Owner commits |

No detector or prototype code is written or run before commits 1 and 2 and `LOG.md` are committed. The commit hash of this pre-registration is cited in the spike report.

**What ran in Step A.** Four commands ran, and nothing else:

1. **The hashing command.** It read the six canonical lines (§4.4) from this document and wrote them to a scratch file outside the repo. It inspected the file's bytes (first three, last one, CR and LF counts) and computed `Get-FileHash -Algorithm SHA256`. This ran in Step A so that the regex is hash-locked in commit 1, before anyone sees the pool.
2. **One approved re-run of that command,** after the digest was inserted. It confirmed that the digest in §4.4 matches.
3. **A read-only text comparison** of the verbatim blocks (§11, §12) against their source docs. Both are identical.
4. **One `git status --short`**, which is read-only.

No sampling, detector or prototype code ran.

---

## 1. Repository selection

### 1.1 Criteria

A repo is selected only if it passes **all** of R1–R9.

| # | Criterion | Rule |
|---|---|---|
| R1 | License | GitHub's license endpoint (`GET /repos/{owner}/{repo}/license`) returns `license.spdx_id` ∈ {`MIT`, `BSD-2-Clause`, `BSD-3-Clause`, `Apache-2.0`}. The value is recorded per repo |
| R2 | pytest usage | §1.2 |
| R3 | Stars | `stargazers_count` ≥ 1,000 |
| R4 | Recent activity | ≥ 1 commit on the default branch since 2026-08-22T00:00:00Z (90 days before 2026-11-20). Checked with `GET /repos/{owner}/{repo}/commits?sha=<default_branch>&since=2026-08-22T00:00:00Z&per_page=1`, using GitHub's `since` semantics |
| R5 | Population and size | ≥ 300 qualifying test-modifying commits (§2) in the window (§2.1). The `.git` directory after cloning is ≤ 500 MB |
| R6 | Window | §2.1: committer timestamp, UTC, all non-merge commits reachable from the default-branch HEAD |
| R7 | Not test tooling | The repo is dropped if its `topics` include any of `pytest`, `testing`, `test-framework` or `unittest`, or if its name starts with `pytest-` |
| R8 | Diversity | A seeded random draw from domain buckets (§1.3). No test code is inspected before the draw. At most one repo per owner |
| R9 | Leakage | Spike repos are excluded from **every** benchmark tier (A, B, C), not just the sampled commits |

### 1.2 R2 method

- **Where it's counted:** the default-branch HEAD of the fresh clone. The SHA is recorded.
- **Test files:** the basename matches `test_*.py` or `*_test.py`. Files under any of these path components are excluded: `vendor/`, `third_party/`, `_vendor/`, `site-packages/`, `docs/`, `examples/`.
- **Counting:** each file is decoded as UTF-8 with `errors="replace"`, the BOM is stripped, and the file is parsed with `ast.parse`. Nothing is imported or executed.
  - **pytest-style sites:** `assert` statements; calls to `pytest.raises`; calls to `pytest.warns`. `pytest.approx` is **not** counted.
  - **unittest-style sites:** method calls on `self` whose name starts with `assert` or `fail`.
  - **Helper-wrapped assertions are not counted.** An assertion inside a helper counts only where the `assert` statement or call actually appears.
- **Unparseable files** are excluded from the R2 count, and their number is reported. This is not K3; K3 is measured on the sample (§2.4).
- **Pass condition:**
  - pytest-style sites ÷ (pytest-style + unittest-style) ≥ **70%**, and
  - pytest is a configured runner: any of `[tool.pytest.ini_options]` in `pyproject.toml`, a `pytest.ini` file, `[tool:pytest]` in `setup.cfg`, `[pytest]` in `tox.ini`, or `pytest` appearing in a `.github/workflows/*.yml` file.
- **The 70% threshold is a judgment call.** It's not a published standard. It allows for repos that are mid-migration from unittest, while making sure most assertions are syntax S1–S4 target.
- **Limitation:** R2 is measured at HEAD, but the sampling window spans 5 years. A repo may have been more unittest-heavy earlier in the window.

### 1.3 R8: buckets, pool and draw

**Buckets.** Frozen as of this document. A repo matches a bucket if its GitHub `topics` include any topic in that bucket's list.

| Bucket | Topics |
|---|---|
| W: web / HTTP | `web-framework`, `http`, `http-client`, `asgi`, `wsgi`, `rest-api` |
| D: data / scientific | `data-science`, `dataframe`, `scientific-computing`, `numpy`, `data-analysis` |
| T: dev tools / CLI | `cli`, `command-line-tool`, `linter`, `formatter`, `developer-tools`, `static-analysis` |

**Pool construction (Step B).**

- **Queries:** for each topic, `GET /search/repositories` with `q=topic:<topic> language:Python stars:>=1000 archived:false fork:false`, `sort=stars`, `order=desc` and `per_page=100`. Results are paginated until exhausted or until the API's 1,000-result limit.
- **Recorded per query:** the query string, the query date and time (UTC), `total_count`, and every returned `full_name` with its `stargazers_count`, `topics` and `default_branch`.
- **Assigning repos to buckets:** results are deduplicated within each bucket. A repo that appears in more than one bucket is **excluded** from all buckets.
- **Filters:** R3 and R7 are applied to the pool before the draw.
- **Snapshot:** everything goes to `spike/pool_snapshot.json`.

**Draw.**

1. For each bucket, sort the remaining repos by `full_name`, exactly as returned by the API.
2. Shuffle with a separate generator per bucket: `random.Random("20261120-W")`, `random.Random("20261120-D")` and `random.Random("20261120-T")`, using `Random.shuffle`.
   - These are `str` seeds under Python's default version-2 seeding. In CPython's `random.py` implementation, a `str` seed is extended with its SHA-512 digest. That is an implementation detail, not a documented guarantee, so reproducing the draw requires the **recorded Python version**.
3. The shuffled order of each bucket is written to the snapshot.

**Walk (Step B).**

- Each bucket is walked in its shuffled order. For each repo, the checks run in order **R1 → R4 → R2 → R5**, and the log records the first check the repo fails.
  - R1 and R4 are gh api GET calls.
  - R2 and R5 need a clone.
- A repo whose owner matches an already-selected repo's owner is skipped, and the skip is logged.
- The first repo that passes all checks is selected for that bucket.
- **Clone failures and clone timeouts** are logged and skipped. **No other manual skips are allowed.**
- A bucket that runs out of repos is reported as **unfilled**. The spike then runs with 2 repos, which is within PRD §4.1's "2–3".

**Limitations.**

- Search results are capped at 1,000 per query. The recorded `total_count` shows whether truncation occurred.
- Topic tagging is uneven. A repo with no matching topic never enters the pool.
- Search results drift over time, so the snapshot, not a re-run of the search, is the record.

---

## 2. Test-modifying commit

### 2.1 History and window (R6)

- **Commits walked:** all non-merge commits reachable from the default-branch HEAD recorded at clone time (`git rev-list --no-merges <HEAD>`).
- **Window:** the script keeps commits whose **committer timestamp** (`%ct`) falls in [2021-11-20T00:00:00Z, 2026-11-20T00:00:00Z), in UTC. Counts walked and kept are logged per repo.
- Each commit is diffed against its single parent. Root commits are excluded.

### 2.2 Git settings

- **git version:** recorded in the snapshot. Reproduction requires the same version.
- **Diff:** `git -c core.quotepath=false diff-tree -r --no-commit-id -M50% --diff-filter=MDR --name-status <parent> <sha>`.

### 2.3 Test files and qualifying commits

- **Test file:**
  - The basename matches `test_*.py` or `*_test.py`.
  - The basename doesn't match `snap_*.py`.
  - The path has none of these components: `vendor/`, `third_party/`, `_vendor/`, `site-packages/`, `docs/`, `examples/`, `__snapshots__/`, `snapshots/`.
  - `conftest.py` is not a test file.
- **Renames:** for an `R` entry, **both** the old and the new path must be test files.
- **Qualifying test file:** a test file with status `M`, `D`, or `R` with similarity < 100%. An added (`A`) file never qualifies.
- **Qualifying commit:** at least one qualifying test file remains after the exclusions in §2.4.

### 2.4 Exclusions (frozen lists, all counts logged)

1. **Merge and root commits.**
2. **Bots.** This applies to the commit's **author name and author email**. The commit is excluded if any of these holds:
   - the name ends with `[bot]`
   - the email ends with `[bot]@users.noreply.github.com`
   - the name contains (case-insensitive) `dependabot`, `renovate`, `pre-commit-ci`, `github-actions` or `allcontributors`

   Every excluded author name is logged.
3. **Snapshot-only commits.** These are excluded through the path rules in §2.3: if a commit's only test-file changes are snapshot files, it doesn't qualify.
4. **Generated files.** A qualifying test file is ignored if, after decoding as UTF-8 with `errors="replace"` and stripping a BOM, `@generated`, `auto-generated` or `autogenerated` appears (case-insensitive) in the first 5 lines of its base or head version.
5. **Not excluded:** reverts, formatting-only commits and large commits.
6. **Parse failures are never excluded.** A qualifying test file that fails `ast.parse` stays in the sample and counts toward K3.

**K3 denominator (defined now).** The set of qualifying `M`, `D` and `R` test files in the final random stratum.

- A `D` file fails K3 if its base version doesn't parse.
- An `M` or `R` file fails K3 if either version doesn't parse, or if base and head tests can't be aligned.
- Excluded and unparseable files are logged per commit.

### 2.5 D-only commits

A **D-only** commit is one where every qualifying test file has status `D`.

- **Cap:** per repo and per stratum. ⌊10% × allocation⌋ in the random stratum and in the reserve; **1** in the keyword stratum.
- **Enforcement:** the commit list is sorted by full SHA, shuffled, and taken in order. Once a repo's quota is full, further D-only commits are skipped, and the draw continues until the allocation is filled. Every skipped commit is logged.
- **Reporting:** the D-only share of each stratum is reported.

### 2.6 Limitations and descriptive counts

- **Mid-PR and fixup commits are included.** Commits made in the middle of a PR, or `fixup!` commits, may show intermediate states that the final PR doesn't contain. The product evaluates whole base..head ranges, not single commits.
- **Descriptive only, no effect on selection:** per repo, the number of qualifying commits that change an assert line in a modified file. An assert line is a `+` or `-` diff line in an `M` or `R` test file whose stripped content starts with `assert`.

---

## 3. Sampling

### 3.1 Random stratum (primary): 200 commits

- **Allocation:** 67 / 67 / 66 to the repos selected for buckets W, D, T, in that order. With 2 repos, 100 / 100.
- **Draw, per repo:**
  1. Sort the qualifying commits by full SHA.
  2. Shuffle with `random.Random("20261120-<full_name>-R")`, where `<full_name>` is exactly as returned by the GitHub API.
  3. Take commits in that order, applying the D-only cap (§2.5).
- **Shortfall:** if a repo has fewer qualifying commits than its allocation, take all of them. The shortfall is **not** redistributed.
- The shuffled order is written to the snapshot.

### 3.2 Keyword stratum (supplementary): 45 commits

- **Allocation:** 15 per repo. With 2 repos, 23 / 22.
- **Eligible:** qualifying commits **not** in the random stratum whose message matches `KEYWORD_RE` (§4).
- **Draw:** sort by SHA, shuffle with `random.Random("20261120-<full_name>-K")`, take in order. D-only cap of 1.
- **Shortfall:** if a repo has fewer than 15 eligible commits, take all of them. No redistribution.
- The shuffled order is written to the snapshot.

### 3.3 K1 reserve batch: 100 commits (pre-drawn, sealed)

- **Drawn in Step B** by the same script and procedure. Seed `random.Random("20261120-<full_name>-R2")`, from qualifying commits not in the random or keyword strata.
- **Allocation:** 34 / 33 / 33 in W, D, T order. With 2 repos, 50 / 50. D-only cap ⌊10% × allocation⌋.
- **Shortfall:** a repo with fewer eligible commits than its allocation contributes all of them. No redistribution.
- The shuffled reserve order is written to the snapshot. The SHAs are committed in commit 2 as a **separate list**.
- **Cap:** one reserve batch only, so at most 300 random commits.
- When it's opened and how it's used are defined in §7.4.

---

## 4. Keyword regex (frozen)

### 4.1 Implementation form

```python
import re

KEYWORD_RE = re.compile(
    r"\b(?:"
    r"flak(?:y|iness)"
    r"|loosen\w*"
    r"|relax\w*"
    r"|toleran(?:ce|ces|t)"
    r"|weaken\w*"
    r"|xfail\w*"
    r"|skip\w*"
    r"|disabl(?:e|es|ed|ing)\s+tests?"
    r"|fix(?:es|ed|ing)?\s+tests?"
    r"|make\s+tests?\s+pass"
    r"|less\s+strict"
    r"|approx\w*"
    r")\b",
    re.IGNORECASE,
)
CI_SKIP_RE = re.compile(r"\[(?:skip ci|ci skip)\]", re.IGNORECASE)
```

This is the only keyword regex for the spike.

### 4.2 What it runs on

1. Get the full commit message with `git log -1 --format=%B <sha>`.
2. Decode it as UTF-8 with `errors="replace"`. Messages that needed replacement are **counted, not excluded**.
3. Replace every `CI_SKIP_RE` match with a single space.
4. The commit is eligible if `KEYWORD_RE.search(text)` returns a match.

There is no other preprocessing.

- **Message only.** No diff or code is searched.
- **Identifiers aren't matched.** `\w` includes `_`, so `\b` doesn't split inside identifiers. `\bskip` therefore doesn't match inside `test_skip_foo`.
- **Reporting:** match counts per keyword alternative, per repo.

### 4.3 Accepted noise

`approx\w*` matches "approximately". `toleran(?:ce|ces|t)` matches unrelated tolerance wording. `skip\w*` matches phrases like "skipped CI cache". These are accepted trade-offs: they add noise to a supplementary stratum, which is better than missing the common inflected forms.

### 4.4 Canonical form and digest

The hash input is the following six lines: LF (`0x0A`) line endings only, UTF-8, **no BOM**, **no trailing newline**. Each pattern line is exactly the `.pattern` string of the compiled regex.

<!-- CANONICAL-REGEX-BEGIN -->
```text
KEYWORD_RE
\b(?:flak(?:y|iness)|loosen\w*|relax\w*|toleran(?:ce|ces|t)|weaken\w*|xfail\w*|skip\w*|disabl(?:e|es|ed|ing)\s+tests?|fix(?:es|ed|ing)?\s+tests?|make\s+tests?\s+pass|less\s+strict|approx\w*)\b
flags=re.IGNORECASE
CI_SKIP_RE
\[(?:skip ci|ci skip)\]
flags=re.IGNORECASE
```
<!-- CANONICAL-REGEX-END -->

**SHA-256:** `9dd17bc922488091a9a18eea83b1dd417ffd72f5338fb7395cf6da3330cb5cdc` (278 bytes)

**To reproduce:**

1. Write the six lines, joined by `\n` only (no `\r`), to a file as UTF-8 without a BOM and with no final newline.
2. Check that the first byte is `0x4B` (`K`) and the last byte is `0x45` (`E`).
3. Compute SHA-256.

Any change to this section requires a new hash and a repeat of the byte inspection.

### 4.5 Step B enforcement

The hash check is the **first statement** the sampling script runs, before the seeds, topic lists or any network or git call are used. The script:

1. rebuilds the six lines from `KEYWORD_RE.pattern` and `CI_SKIP_RE.pattern`
2. asserts `(compiled.flags & ~re.UNICODE) == re.IGNORECASE` for both regexes. `str` patterns carry `re.UNICODE` automatically, which is why it's masked out.
3. hashes the six lines and **aborts on any mismatch** with the digest above.

---

## 5. Clarifications to PRD §4.1

These clarify how K1–K4, which are unchanged, apply to a two-stratum sample. No threshold is changed.

1. **"Spike sample" for the decisions** means the **random stratum** (§3.1). If the reserve is triggered, it means the random stratum plus the reserve for K1 only (§7.4).
2. **Tuning** may use both the random and keyword strata. The reserve is never used for tuning. **K1 is computed only on the random stratum.**
3. **Which data each K uses:**

   | K | Decided on | Also reported |
   |---|---|---|
   | K1 | Random stratum (+ reserve if triggered, §7.4) | Keyword stratum, separately |
   | K2 | Random stratum | Keyword stratum, separately |
   | K3 | Random stratum's K3 denominator (§2.4) | Keyword stratum; reserve, descriptive only |
   | K4 | Random stratum | Keyword stratum, as in the table below |

4. **K4's "count-preserving weakening instances"** means **WP units** (§6) in the random stratum.

   | WP in random | WP in keyword | K4 |
   |---|---|---|
   | ≥ 1 | any | Not triggered |
   | 0 | ≥ 1 | Triggered as written. The report notes that instances exist in the wild but weren't seen at random-sample prevalence, which supports reframing (b) |
   | 0 | 0 | Triggered |

5. **Power note.** Zero WP commits in 200 random commits gives an exact one-sided Clopper–Pearson 95% upper bound on prevalence of 1 − 0.05^(1/200) ≈ **1.49%**. The report states that bound and names the method. All other proportions use 95% Wilson intervals.
6. **Prevalence scope.** Prevalence is estimated across the selected repos at equal weight. It is **not** a population-wide estimate. The Wilson interval assumes independent commits. Prevalence is also reported per repo.

---

## 6. Labeling rubric

Labels judge **test strength, not commit correctness**. `justified` is the only intent field.

### 6.1 Unit of labeling

- A **unit** is a test function or method, identified by its qualified name (e.g. `TestCart.test_cap`), that exists in the **base** version of a qualifying test file and is either **changed** in the head version (body, decorators or signature) or **deleted**.
- Every test in a `D` file is a deleted unit. `R` files are paired by their rename. Tests added in the head version are not units.
- **Listing:** the labeler (the owner) lists the units by hand, in file order, from `git show -W` output, and stores the list in `spike/labels/unit_lists/`. No alignment code runs before labeling, because alignment is part of what K3 tests.

### 6.2 What the labeler sees

- the full commit message
- `git show -W` output for the qualifying test files
- the list of non-test files changed
- the repo at that commit, opened only when needed

**Logged:** every helper file and line consulted outside the diff, and which commits needed repo browsing.

**No detector output** is visible, and **no detector is run**, until a commit's labels are final (§8).

### 6.3 Definition

A unit is **weakened** if, for the same code under test, the head test passes some behavior that the base test would have failed. This is judged from the test alone; the change to the code under test is ignored.

### 6.4 Primary labels

**The count-based triggers** are exactly the four rules in PRD §6.3(3):

1. test deletion
2. new skip/xfail
3. removed assertion
4. net assertion-count drop

| Label | Rule | Examples |
|---|---|---|
| **WP**: weakened, count-preserving | Weakened, and **none** of the four triggers fires in the unit | `== 80.00` → `>= 0`; `== x` → `is not None`; `raises(ValueError)` → `raises(Exception)`; `match=` removed; `approx(rel=1e-6)` → `approx(rel=1e-2)`; **a parametrize case removed**; an assert wrapped in a new `if` guard; a `try/except` added that swallows the failure |
| **WC**: weakened, count-changing | Weakened, and **at least one** trigger fires | test deleted; `@pytest.mark.skip` added; assert removed; 3 asserts become 1; **one assert removed and another added**, because the removed-assertion trigger fired |
| **N**: not weakened | Everything else | rename or reformat; equivalent rewrite; strengthening; expected value changed at the same strength (`== 80` → `== 75`); a test split into two with all asserts kept |
| **U/timebox** | Timebox reached before this unit was labeled | — |
| **U/unclear** | Evidence is visible but ambiguous | a tolerance change whose direction depends on units the test doesn't state |
| **U/unresolvable** | Depends on code that can't be resolved | an assert replaced by a helper whose definition can't be followed; dynamic or generated tests |

### 6.5 Decision rules

1. **Mixed changes.** If a unit is strengthened in one place and weakened in another, it's weakened.
2. **WP vs WC.** WP vs WC is decided **only** by the four triggers, worked out by hand from the diff.
3. **Helpers.** An assert moved into a helper is N if the helper is at least as strong, WP/WC if weaker, and U/unresolvable if it can't be determined.
4. **Value changes.** An expected value changed at the same comparison strength is **N**, even though it could hide a bug. This matches the PRD §3 legitimate-change example.
   - The frozen sub-flag **`value_to_trivial`** is Y when the new expected value is a literal in {`0`, `0.0`, `None`, `True`, `False`, `""`, `[]`, `{}`, `()`}. **It doesn't change the label.**
5. **Timebox.** 15 minutes per commit. Units not reached are labeled U/timebox. Minutes are recorded per commit.
6. **Bulk labels.** If a commit has more than 20 units and one mechanical transformation covers all of them, one label may be applied to all, flagged `bulk`. Any unit that doesn't fit is labeled on its own. **Bulk-labeled units count individually in every denominator.**

### 6.6 Fields

- **`spike/labels/units.csv`**, one row per unit:
  - `sha`, `stratum`, `repo`
  - `base_path`, `head_path`, `qualified_name`
  - `label` (WP, WC, N, U/timebox, U/unclear or U/unresolvable)
  - `subtype`, `justified` (Y/N), `value_to_trivial` (Y/N)
  - `before_lines`, `after_lines`, `bulk` (Y/N), `notes`
- **`subtype`** (frozen list): comparison loosened, trivial bound, exception widened, match removed, tolerance loosened, cases removed, assert removed, skip/xfail added, guard/swallow added, test deleted, other.
- **`spike/labels/commits.csv`**, one row per commit:
  - `sha`, `stratum`, `repo`
  - `first_pass_date`, recorded automatically by the labeling helper
  - `minutes`, `browsed` (Y/N), `helpers_consulted` (`file:line` list)
  - `unit_count`, `notes`

### 6.7 U handling

- U units are left out of the primary denominators, and their counts by sub-label are always reported.
- If **U exceeds 10% of a repo's units**, K1 is reported as **unevaluable for that repo**.
- **Intervals** are reported at both unit and commit level.

---

## 7. K1 measurement in the spike (severity proxy)

### 7.1 Proxy

Real repos have no approved contracts, so "blocking" can't be computed from a contract link the way the product does. In the spike, **every unit is treated as guarding a mandatory condition with no approved contract change**. A blocking-severity semantic finding is therefore **any S1–S4 finding**. B1–B3 findings are excluded from K1.

**Limitation.** The spike doesn't test severity assignment or contract linkage. Product blocking precision may differ, and the Tier C target (PRD §6.4) is measured separately.

### 7.2 Matching findings to labels

- A **flagged unit** is (commit, base or head path, qualified name) with at least one S1–S4 finding.
  - Labeled WP or WC → **TP**
  - Labeled N → **FP**
  - Labeled U (any sub-label) → excluded from the primary analysis. Bounds are reported in §7.3.
  - Not in the unit list (a new test, or a listing miss) → **FP**, logged as `unlisted`. The unlisted-FP count is reported separately.
- Rename-caused mismatches are logged separately.

### 7.3 K1 computation

- **Primary K1:** **unit-level** precision on the random stratum, using evaluable repos only (§6.7). `justified=Y` weakenings count as TP, because in the product, an approved contract change would excuse them.
- **Decision gate:** K1 is decided only if there are **≥ 30 non-U flagged units** coming from **≥ 15 distinct commits**, across evaluable repos. If not, the reserve procedure in §7.4 applies. If K1 is still short after that, it's reported as **unevaluable**. With fewer than 2 evaluable repos, K1 is unevaluable.
- **Trigger:** K1's verbatim threshold, a point estimate below 50% (§11). A 95% Wilson interval is always reported.
  - If the point estimate is below 50%, the outcome ADR also states whether the **Wilson upper bound** is below 50%.
- **Reported, not deciding:**
  - finding-level and commit-level precision
  - the **strict variant**, where `justified=Y` counts as FP
  - U-as-FP and U-as-TP bounds
  - pre-tuning precision (detector-v0)
  - keyword-stratum precision
- **Same-sentence rule:** if primary K1 is ≥ 50% and the strict variant is < 50%, the report states both in the same sentence.
- **Label reliability caveat:** see §9.
- **Tuning round:** one pass of rule changes from detector-v0 to detector-v1, with the full diff recorded. K1 uses detector-v1.

### 7.4 Reserve trigger

- **Trigger:** after the post-tuning run on the random stratum, fewer than 30 non-U flagged units, or fewer than 15 distinct commits.
- **Before the trigger fires:** the labeler doesn't open reserve commits. The date they're first opened is logged in `spike/LOG.md`.
- **Once it fires:**
  1. The reserve is labeled blind, into `spike/labels_reserve/`.
  2. It's frozen with its own tag (§8).
  3. Only then is the frozen detector-v1 run on it. There is no further tuning.
- **Reporting:** K1 is reported three ways: original 200, reserve only, and combined 300. **Combined decides only if the reserve was triggered**, and the 30 / 15 thresholds are checked on the combined set.
- K2–K4 stay decided on the original 200. Reserve K3 is **descriptive only** and doesn't enter the K3 decision.

### 7.5 Corrections after freeze

- **The errata rule governs.** Frozen label rows are never edited.
- Corrections go only into a dated, published `spike/ERRATA.md`.
- **Nothing derived from corrected labels feeds K1–K4.**

---

## 8. Blinding procedure

### 8.1 Order

1. Commits 1 and 2 and `spike/LOG.md` are committed.
2. **Packets** are rendered for each commit: `git show -W` for the qualifying test files, the message, and the non-test file list. The packet renderer is non-detector spike code and **imports nothing from the detector**.
3. The owner labels all 200 random and 45 keyword commits.
4. The self-consistency relabel runs (§9).
5. **Label freeze:** the label files and a SHA-256 manifest of them are committed, and an annotated tag `labels-freeze` is created and pushed by the owner.
6. The detector is developed **only** on synthetic fixtures and the checkout example. It never touches the spike repos or the sample.
   - Frozen as annotated tag `detector-v0`, created and pushed by the owner.
   - This work may run in parallel with steps 3–4.
7. detector-v0 runs on the sample, producing the pre-tuning numbers.
8. One tuning round produces annotated tag `detector-v1`.
9. detector-v1 runs, producing K1–K4. The reserve follows §7.4 if triggered, with its own freeze tag `labels-reserve-freeze`.

Detector output goes to `spike/detector_out/`. The owner doesn't open it until the labels it depends on are frozen.

### 8.2 Mechanical guards

These are the only guards.

**The detector-run script aborts unless all of these hold:**

1. **Tags.** The required annotated tags exist: `labels-freeze`, plus `detector-v0` or `detector-v1`, plus `labels-reserve-freeze` when the reserve is in scope. The SHAs are read from the tags, not typed in.
2. **Label files unchanged.** `git diff --quiet labels-freeze HEAD -- spike/labels` succeeds. For the reserve: `git diff --quiet labels-reserve-freeze HEAD -- spike/labels_reserve`.
3. **No untracked files** under `spike/labels` (or `spike/labels_reserve`).
4. **Manifest matches.** Every label file's SHA-256 matches the manifest committed at the freeze.
5. **Every sampled SHA** has a final commit row and its unit rows.
6. **Detector source.** The detector source is exactly the tagged version (no diff against the tag and no untracked files in the detector directory). The tag name and SHA are recorded in every output file.
7. **Pre-registered scope.** Any repo or SHA outside the pre-registered lists (§13) is refused.

**Other tools:**

8. **Relabel tool.** It refuses to relabel a commit before `first_pass_date + 7 days`.
9. **Packet renderer.** It imports nothing from the detector.

### 8.3 Logged in `spike/LOG.md`

- the label-freeze date and SHA
- the detector-v0 and detector-v1 SHAs
- the reserve-freeze date and SHA, if used
- the first detector run date
- the date the owner first opened `spike/detector_out/`. **This date is self-reported.**

---

## 9. Self-consistency check

### 9.1 Subsets

- **Main subset:** 25 commits, 10% of 245 rounded. Drawn after the first pass of all 245 is complete: sort the SHAs, shuffle with `random.Random("20261120-SC")`, take the first 25. The stratum mix is reported.
- **Weakening subset:** 10 commits, or all if fewer, from commits where the first pass found at least one WP or WC unit, excluding the main subset. Same procedure, seed `random.Random("20261120-SC-W")`.
- **Reserve subset:** 10 commits, seed `random.Random("20261120-SC-R2")`, drawn **only if the reserve is opened**. The main subset is never extended.
- **Logged:** every subset's SHAs go in `spike/LOG.md` before its relabel starts.

### 9.2 Procedure

- **Gap:** each commit is relabeled at least 7 days after its own `first_pass_date`. The relabel tool enforces this (§8.2, guard 8).
- **Method:** the units are **re-listed** from `git show -W`, then every field is relabeled. First-pass rows aren't opened during the relabel.
- **Output:** `spike/labels/relabel/`. Minutes are recorded.
- **No replacement:** no adjudicated label replaces either pass.

### 9.3 Reported

- **Headline:** raw agreement.
- **Also reported:**
  - unit-list agreement: |listed in both passes| ÷ |listed in either|
  - on units listed in both passes: 4-way agreement (WP / WC / N / U\*, where U\* merges the three U sub-labels) and binary agreement (weakened vs not, U excluded), each with raw agreement and Cohen's κ with a 95% CI
  - WP-vs-WC agreement on units both passes called weakened
- **Instability:** κ is flagged unstable when any category has fewer than 5 units.
- **Disagreement lists are published,** with both labels.
- **Reliability rule:** if **binary agreement is below 80%**, the report states that the labels are unreliable, and K1 carries that caveat. **K1's trigger doesn't change.**

---

## 10. Device record (hosting spike)

The devices follow `docs/02-architecture.md` §12 (verbatim in §12 below). A physical Android phone is used.

### 10.1 Fields

**Not yet filled. Recorded in `spike/LOG.md` before the first hosting measurement.**

| Device | Field | Value |
|---|---|---|
| Desktop | Make / model | `<not yet filled>` |
| Desktop | CPU | `<not yet filled>` |
| Desktop | RAM | `<not yet filled>` |
| Desktop | Windows version and build | `<not yet filled>` |
| Desktop | Browser and version | `<not yet filled>` |
| Phone | Make / model | `<not yet filled>` |
| Phone | SoC | `<not yet filled>` |
| Phone | RAM | `<not yet filled>` |
| Phone | Android version | `<not yet filled>` |
| Phone | Browser and version | `<not yet filled>` |
| Phone | M1–M4 evidence (launch listing URL and fetch date) | `<not yet filled>` |

- **Browser versions** are re-recorded at every measurement run, and any change is logged.
- **Fallback:** if the phone becomes unusable, that run uses the emulated profile in §12 (DevTools CPU throttle 4× plus a named network-throttling preset, recorded). Every result from that run is flagged **"not measured on real hardware"**.

### 10.2 Mid-range rule

This rule is a **proxy** for mid-range performance. The SoC is recorded either way. A phone qualifies only if all four hold:

- **M1:** Android, released 2022-01-01 or later.
- **M2:** launch price between **₹12,000 and ₹40,000 (INR)**, taken from the maker's or a major retailer's launch listing. The URL and fetch date are recorded. The INR band is the criterion itself, with no currency conversion.
- **M3:** not in the maker's top line, per this frozen list:
  - Samsung: Galaxy S, Galaxy Z
  - Google: Pixel numbered models other than "a", Pixel Pro, Pixel Fold
  - OnePlus: numbered flagships, OnePlus Open
  - Xiaomi: numbered flagships, Mix
  - vivo: X series
  - OPPO: Find series
  - Motorola: Edge+ / Edge Ultra, Razr+
  - realme: GT Pro
  - iQOO: numbered flagships

  This list was written from general knowledge of product lines and was not checked against the makers' sites.
- **M4:** RAM ≥ 4 GB.

A phone that fails any of M1–M4 has every result flagged **"phone not mid-range per pre-registered rule"**.

---

## 11. Verbatim: K1–K4 (PRD §4.1)

Copied unchanged from `docs/01-PRD.md` §4.1. Section references inside this block (e.g. "§6.4") refer to the PRD.

<!-- VERBATIM-K-BEGIN -->
- **K1, too noisy:** precision of blocking-severity semantic findings is below **50%** after one tuning round. K1 is measured **after tuning on the same spike sample**, so it is optimistically biased. It is a **floor for continuing**, not an estimate of product precision. The product target is separate (§6.4).
- **K2, can't be made precise:** for a given rule, hand review shows it can't distinguish weakening from legitimate change, for example most of its flags are refactors or intended updates. That rule is dropped. If **at least half** of the semantic rules are dropped, this triggers a pivot.
- **K3, parsing infeasible:** more than **5%** of real test files fail to parse, or base and head tests can't be aligned.
- **K4, weak real-world signal:** zero count-preserving weakening instances are found in the spike sample. This is **not a kill by itself**. It changes the framing.
<!-- VERBATIM-K-END -->

---

## 12. Verbatim: hosting criteria (architecture §12)

Copied unchanged from `docs/02-architecture.md` §12. Section references inside this block (e.g. "§7.3") refer to that document.

<!-- VERBATIM-12-BEGIN -->
## 12. Pre-registered Gate 0 hosting criteria

These values are **fixed when this document is approved**, before any measurement. They may be changed only by an ADR made **before** measuring (same rule as PRD §4.1). They are separate from K1–K4, which are unchanged.

**Devices.** Desktop: the owner's machine, with the model and browser recorded. Phone: a **physical mid-range Android phone**, with the model, OS and browser recorded. If none is available, the phone measurements are made on an emulated profile (DevTools CPU throttle 4× plus a named network-throttling preset), and every phone result is flagged **"not measured on real hardware"**. Network for cold-load tests: the browser's named throttling preset (recorded) with the cache disabled. Each timed metric uses **5 runs** and reports the median, plus p95 where stated.

### 12.1 Switch from Pyodide (primary) to the fallback if **any** of these holds

| ID | Criterion | Threshold |
|---|---|---|
| P1 | Analyzer output differs between Pyodide and pinned CPython on the spike corpus plus all engine fixtures (findings JSON, timestamps excluded) | **Any** mismatch that pinning can't resolve |
| P2 | Cold load-to-ready (click "Run it live" until the worker is ready), median of 5 | Phone **> 12 s**, or desktop **> 5 s** |
| P3 | Total bytes over the wire to make the worker ready (Pyodide plus our wheel) | **> 15 MB** |
| P4 | `terminate()` fails to stop a pathological-input parse | Worker still consuming CPU **> 1 s** after the timeout fires, on either device |
| P5 | Warm analysis latency for max-size in-limit input, p95 of 20 | Phone **> 1 s** |
| P6 | Tab or worker crashes from memory on any in-limit input | **Any** occurrence |
| P7 | `ast` or `tokenize` unavailable or broken in Pyodide | **Any** |

### 12.2 The fallback is viable only if **all** of these hold

| ID | Criterion | Threshold |
|---|---|---|
| V1 | A child process with a wall timeout plus memory and CPU limits works on a Vercel Python Function (Hobby) | Works, with the limits observed to take effect |
| V2 | A crashing or limit-killed parse doesn't fail other in-flight requests | 0 collateral failures in 20 concurrent mixed requests |
| V3 | Cold start (first request after ≥ 15 min idle) and warm latency for max-size input | Cold p50 **≤ 3 s**; warm p95 **≤ 1 s** |
| V4 | Max request body actually accepted | **≥ 160 KiB** |
| V5 | The single Hobby WAF rate-limit rule returns 429 when exceeded | Observed |
| V6 | Measured worst-case CPU per accepted request *c* | 14,400 / *c* **≥ 50,000** requests a month |

### 12.3 Outcomes

- **P all pass:** keep Pyodide as primary. The fallback stays dormant but measured.
- **Any P fails and all V pass:** activate the fallback by ADR.
- **Any P fails and any V fails:** **disable live analysis**. The site shows PRECOMPUTED presets only, clearly labeled, and the limitation is stated. Record by ADR and re-plan per ADR 0002. A paid option is not considered (ADR 0004).
<!-- VERBATIM-12-END -->

---

## 13. Sample SHAs

**Status:** filled in after the Step B run on 2026-11-20. The sampling script was run once by the owner in plain PowerShell as `python -I -B spike/sampling/build_sample.py`. Exit code 0. The script's own log ends with "self-check passed; run finished". No aborted runs. The full record is `spike/build_sample.log`.

### 13.1 Run record

| Field | Value |
|---|---|
| Run started (UTC) | 2026-11-20T04:15:18Z |
| Pre-registration commit | `f44e44a3ea88d14743d45eed9b615712b0a8d025` |
| Repo HEAD when the run started | `9abfe8f60e3308aed45191de572e102bf5ac5c32` |
| Script | `spike/sampling/build_sample.py`, SHA-256 `463fccc40a1c70734e4659c35d904b6bedb15e79c7e6bb4f9cf23f89a4352fd7` |
| Regex digest (verified by the script before any network call) | `9dd17bc922488091a9a18eea83b1dd417ffd72f5338fb7395cf6da3330cb5cdc` |
| Python | 3.11.3 |
| git | 2.51.0.windows.1 |
| gh | 2.105.0 |

### 13.2 Selected repos

| Bucket | Repo | HEAD SHA | License (R1) | R2: pytest / unittest / share / parsed / unparseable | R5: qualifying commits / .git bytes |
|---|---|---|---|---|---|
| W | mitmproxy/mitmproxy | `c491986978a6280c29977482cb18698d6523a662` | MIT | 4699 / 20 / 0.9957618139436322 / 184 / 0 | 419 / 75007690 |
| D | plotly/dash | `823ce3a73a8831c779ff6176c278e1cbf0f61f07` | MIT | 3560 / 44 / 0.9877913429522752 / 312 / 0 | 825 / 241489944 |
| T | realiti4/claude-swap | `ee79e705cf6e1ad3f70621b88b80983a99bb2001` | MIT | 7320 / 1 / 0.9998634066384374 / 55 / 0 | 429 / 5619456 |

All three buckets were filled. There was no clone timeout and there were no manual skips.

### 13.3 Sample sizes drawn

| Repo | Random | Keyword | Reserve (sealed) |
|---|---|---|---|
| mitmproxy/mitmproxy | 67 | 15 | 34 |
| plotly/dash | 67 | 15 | 33 |
| realiti4/claude-swap | 66 | 15 | 33 |
| **Total** | **200** | **45** | **100** |

No shortfall in any repo or stratum.

### 13.4 Committed files and SHA-256

| File | Contents | SHA-256 |
|---|---|---|
| `spike/sample_random_shas.txt` | 200 SHAs, random stratum | `dec6fbf2439e7ed4c77e5765a462612255a1c5ad691de4319adc86532fa05dfc` |
| `spike/sample_keyword_shas.txt` | 45 SHAs, keyword stratum | `7f290cdf1b2fd411238658631d6bd0f14e33b196bccfcfae55ed8cbdec5e4cb1` |
| `spike/sample_reserve_shas.txt` | 100 SHAs, K1 reserve, **sealed** (§7.4) | `4d28d8f13233acd4fdef541a5aca916407e2d993c622212a7da743a72ea0c06a` |
| `spike/pool_snapshot.json` | Pool, queries, shuffled orders, walk results | `26c5809ad01e67bf1f043db5f619c15d188e061d57de5a0a65e953245570b4ab` |
| `spike/qualifying_mitmproxy__mitmproxy.json` | 419 qualifying commits | `39ddc1938cf25e8f73512f63be07d638d15f7a3863252be63336d97ce9c5bda1` |
| `spike/qualifying_plotly__dash.json` | 825 qualifying commits | `bdd9122136470bdf1485cc6bfa9bc83f9e9c77ee883b1e5d00d0bdd01ed5b7af` |
| `spike/qualifying_realiti4__claude-swap.json` | 429 qualifying commits | `415847807fc24a8c1ca66a05fe9dda202a878309c3b36c60c8f87e2cf37280d6` |

### 13.5 Notes

- **Where the SHAs are.** The random and keyword SHAs are recorded in the two list files above, not inlined in this section, to avoid transcription errors. The list files are committed in the same commit as this section, and their hashes are recorded here. This is an implementation-time interpretation of section 13's "SHAs per repo".
- **Reserve.** The reserve list is a separate sealed list. Section 7.4 and section 8 govern its use.
- **Clones.** The three clones are kept outside the repo, at their HEAD SHAs above. Their location is not recorded here.

---

## 14. Labeling effort estimate

These are guesses. The recorded minutes per commit will replace them in the report.

| Item | Commits | min / commit (low / mid / high) | Hours (low / mid / high) |
|---|---|---|---|
| Calibration on rubric examples (not sample commits) | — | — | 1 / 2 / 3 |
| First pass, random + keyword | 245 | 4 / 6 / 9 | 16 / 24.5 / 37 |
| Relabel, main + weakening subsets | 35 | 4 / 6 / 9 | 2.3 / 3.5 / 5.3 |
| **Total without the reserve** | | | **≈ 20 / 30 / 45** |
| Reserve first pass (if triggered) | 100 | 4 / 6 / 9 | 6.7 / 10 / 15 |
| Reserve relabel (if triggered) | 10 | 4 / 6 / 9 | 0.7 / 1 / 1.5 |
| **Total with the reserve** | | | **≈ 27 / 41 / 61** |

- The 15-minute timebox caps any single commit at 0.25 h.
- The 7-day relabel gap adds at least 7 calendar days after the first pass.
- **Labeling plus the 7-day gap will probably exceed the Gate 0 estimate** of ~1 week (PRD §4). Whether a re-plan follows is decided under ADR 0002. This document doesn't claim it happens automatically.

---

## 15. Parameters chosen by the drafter, accepted by the owner

- Self-consistency κ CI: commit-cluster bootstrap, **2,000 resamples**, seed `random.Random("20261120-KB")`.
- R4 cutoff timestamp `2026-08-22T00:00:00Z` (computed as 90 days before 2026-11-20).
- The `value_to_trivial` literal set (§6.5, rule 4).
- The CSV field lists (§6.6).
- The tag names `labels-freeze`, `detector-v0`, `detector-v1` and `labels-reserve-freeze`.
- The search parameters `order=desc` and `per_page=100`.
- **The clone timeout: 20 minutes per repo.** A timeout is logged and skipped (§1.3).
- The R5 size check uses the on-disk size of `.git` after cloning.

---

## 16. Known gaps

1. **M3 top-line list for unlisted makers.** If the phone's maker isn't in §10.2 M3, that maker's top-line list is written in `spike/LOG.md` and committed **before** the phone is identified for measurement.
2. **Device values are placeholders** (§10.1). They are not yet filled, and are recorded in `spike/LOG.md` before the first hosting measurement.
3. **Single labeler.** No second labeler is planned. Self-consistency (§9) is the only reliability check, and the limitation is stated in the report (PRD §6.5, §9).
4. **Proxy severity.** §7.1 explains why K1 doesn't test the product's severity design.
5. **Prevalence scope.** Prevalence covers 2–3 repos at equal weight, not the Python ecosystem (§5.6).
6. **Pool coverage.** Topic tagging is uneven, and search results are capped at 1,000 per query (§1.3).
