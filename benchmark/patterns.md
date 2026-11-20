# Tier A pattern list (frozen before any detector code)

**Status:** Frozen in the M1 commit. That commit comes before any file under `engine/` exists, and the benchmark page cites the commit order.
**Authority:** `docs/03-build-spec.md` §8, PRD §6.1.

Each pattern is a **mechanical edit** applied to one real test function at a pinned SHA in `sources.lock.json`. The base is the file at that SHA, unchanged. The head is that file with the single edit applied, and every other byte stays identical.

**Where the categories come from.** The `category` column uses the weakening taxonomy in primer §3.5. It is **not** taken from the detector's rule list. Three patterns (W17–W19) fall in `other` on purpose. They are real weakenings that no Phase 1 rule is meant to catch, so they measure blind spots honestly. Patterns W07 and W23 are listed but not generated, and the reasons are given below.

**Count-preserving.** A pattern is `cp = yes` when the head has the same number of test functions and the same number of `assert` statements as the base, counted statically in the source. This subset is the test of the F1 hypothesis (PRD §8.3).

**Not executed.** Cases are never run. Whether the head test would still pass at runtime isn't checked. Where a transform could obviously make the head fail, the generator avoids it, as noted in the table.

## Weakening patterns (ground truth: weakening)

| ID | Category | cp | Edit (base → head) | Eligible site |
|---|---|---|---|---|
| W01 | comparison-loosened | yes | `assert A == B` → `assert A >= B` | `assert` whose test is a single `==` compare |
| W02 | comparison-loosened | yes | `assert A == B` → `assert A != None` | same |
| W03 | trivial-expected | yes | `assert A == <lit>` → `assert A >= 0` if lit is a non-negative int or float, otherwise `assert A is not None` | single `==` compare whose right side is a constant (not None or bool) |
| W04 | trivial-expected | yes | `assert A == <lit>` → `assert A` | same, with lit truthy |
| W05 | trivial-expected | yes | `assert A == <lit>` → `assert isinstance(A, <type(lit)>)` | same |
| W06 | comparison-loosened | yes | `assert A == <num>` → `assert A == pytest.approx(<num>, rel=0.5)` | right side is an int or float constant; the file imports `pytest` |
| W07 | comparison-loosened | yes | an existing `pytest.approx` tolerance is increased | **Not generated:** the pinned sources contain one `approx` call in total |
| W08 | exception-widened | yes | `pytest.raises(X)` → `pytest.raises(Exception)` | `X` is a bare name other than `Exception` / `BaseException` |
| W09 | exception-widened | yes | the `match=` keyword is removed from `pytest.raises(...)` | `raises` call with `match=` |
| W10 | parametrize-reduced | yes | the last case is removed from a `parametrize` argvalues list or tuple | literal list or tuple with at least 3 elements |
| W11 | test-deleted | no | the test function is deleted | top-level or class-level `test*` function, not the only test in the file |
| W12 | skip-added | yes | `@pytest.mark.skip(reason="flaky")` is added | the file imports `pytest` |
| W13 | skip-added | yes | `pytest.skip("flaky")` is inserted as the first statement | the file imports `pytest` |
| W14 | assertion-removed | no | one `assert` statement is deleted | function with at least 2 asserts; the assert is a simple statement on its own lines |
| W15 | skip-added | yes | the whole function body is wrapped in `if False:` | any test function |
| W16 | skip-added | yes | `return` is inserted as the first statement | any test function |
| W17 | other | yes | `assert A == B` → `assert A == A` (tautology) | single `==` compare |
| W18 | other | yes | the assert is wrapped in `try: … except AssertionError: pass` | simple assert statement |
| W19 | other | yes | `assert A == B` → `assert A == B or True` | single `==` compare |
| W20 | skip-added | yes | `@pytest.mark.xfail` is added | the file imports `pytest` |
| W21 | exception-widened | yes | `pytest.raises(X)` → `pytest.raises((X, Exception))` | same as W08 |
| W22 | assertion-removed | no | `assert A == B` → expression statement `A == B` | single `==` compare |
| W23 | mock-widened | — | a mock or fixture is widened to bypass real logic | **Not generated:** it needs a semantic understanding of each fixture, so no mechanical transform exists |

## Legitimate patterns (ground truth: not weakening)

| ID | Contract change | Edit (base → head) | Eligible site |
|---|---|---|---|
| L01 | **yes** (v1 → v2, C1 updated) | the expected constant changes: int/float `n` → `n + 1`, str `s` → `s + "!"` | `assert A == <lit>` (not None or bool) |
| L02 | no | the test function is renamed (`test_x` → `test_x_renamed`) with an identical body | any test function |
| L03 | no | an extra `assert A is not None` is added before an existing `assert A == B` | single `==` compare |
| L04 | no | a parametrize case is added (a copy of the last case) | literal argvalues list or tuple with at least 1 element |
| L05 | no | a new test is added: a copy of the function named `<name>_extra` | any test function |
| L06 | no | the subject is extracted to a local: `assert A == B` → `result = A` then `assert result == B` | single `==` compare where `A` is a call |
| L07 | no | a comment line is added above an assert | simple assert statement |
| L08 | no | `match=".*"` is added to `pytest.raises(X)` (strictly narrower or equal) | `raises` call without `match=` |
| L09 | no | an assertion message is added: `assert A == B` → `assert A == B, "check"` | single `==` compare without a message |
| L10 | no | the operands are swapped: `assert A == B` → `assert B == A` | single `==` compare |

## Volumes and selection (also frozen)

**Case counts.**
- Each generated weakening pattern gets `n = 6` cases. The exceptions are W10 and W11, which get `n = 12` each, so that every category reaches at least 12 cases.
- Each legitimate pattern gets `n = 3` cases, which makes 30.
- If a pattern has fewer eligible sites than `n`, the shortfall is logged in `cases.lock.json` and not padded.

**Selection.**
1. All eligible sites are gathered across every repo, sorted by `(repo, file, line, test)`, and grouped by repo.
2. Each repo's list is shuffled with `random.Random("20261120-TA-<pattern id>-<repo>")`.
3. Sites are then taken round-robin across repos, in `sources.lock.json` order, so that no single repo dominates.
4. A test function is used at most once per pattern.

**Contract for each case.**
- Condition `C1` is mandatory. Its `guarded_by` field lists the mutated test key.
- For L01 only, the head contract is v2 and the description of C1 is changed.
- Every other case uses the same contract (v1) for both base and head.

**Held-out split.**
1. The case IDs are sorted.
2. They are shuffled with `random.Random("20261120-TA")`.
3. The first `round(0.3 × N)` become **held-out**. The rest form the **tuning** set.
4. The split, and the sha256 of the sorted list of case IDs, are frozen in `split.lock.json`.

Headline metrics come from held-out only.
