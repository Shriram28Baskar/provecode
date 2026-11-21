# Tier A-b pattern list: the known blind spots (frozen before any rule code)

**Status:** Frozen in the commit that adds this file. That commit comes **before** any engine code for ADR 0011, and the commit order is recorded in `tierA_b.json` provenance.
**Authority:** ADR 0011, from recommendation R3 in `docs/research/landscape-2026-11.md`. The method is the same as `benchmark/patterns.md` and `benchmark/patterns_unittest.md`.
**Why a new tier:** Tier A's W17–W19 and Tier A-u's U13–U14 are the blind spots these rules target. Their results were already seen, so judging new rules on them would be tuning on test data. Those tiers keep reporting what they report, and the rules are judged on this tier's held-out split only.
**Labels:** each pattern's ground-truth class was written by the developer. The owner should confirm them, as for Tier A and Tier A-u.

Each pattern is one mechanical edit to one real test, made at a pinned SHA in `sources_blindspots.lock.json`, a set of 7 repositories not used by Tier A, Tier A-u or the Gate 0 spike pool. Every other byte of the file stays identical.
- **pytest-style patterns** edit an `assert` statement in a `test*` function or a method of a `Test*` class.
- **unittest-style patterns** edit a `self.assert*` call in a `test*` method of a `TestCase`-style class.

**Count-preserving (`cp`).** The number of test functions and the number of checks (`assert` statements plus `self.assert*` calls) are unchanged, counted statically.

## Weakening patterns

| ID | Style | Category | cp | Edit (base → head) | Eligible site |
|---|---|---|---|---|---|
| X01 | pytest | vacuous-check | yes | `assert A == B` → `assert A == A` | single `==` assert on its own line(s) |
| X02 | unittest | vacuous-check | yes | `self.assertEqual(A, B)` → `self.assertEqual(A, A)` | `assertEqual` statement with exactly 2 positional args |
| X03 | pytest | vacuous-check | yes | `assert A == B` → `assert A == B or True` | as X01 |
| X04 | unittest | vacuous-check | yes | `self.assertTrue(A)` → `self.assertTrue(A or True)` (A parenthesised if it is a conditional, lambda or `:=`) | `assertTrue` with 1 arg |
| X05 | pytest | assertion-swallowed | yes | assert wrapped in `try: … except AssertionError: pass` | single-line assert on its own line |
| X06 | unittest | assertion-swallowed | yes | call wrapped in `try: … except AssertionError: pass` | single-line `self.assert*` statement |
| X07 | pytest | assertion-swallowed | yes | assert wrapped in `try: … except Exception: pass` | as X05 |
| X08 | unittest | assertion-swallowed | yes | call wrapped in `try: … except: pass` (bare) | as X06 |

## Legitimate patterns

| ID | Style | cp | Edit (base → head) | Eligible site |
|---|---|---|---|---|
| Y01 | pytest | no | `assert A == A` (a reflexivity check) **added** on the line before `assert A == B` | as X01 |
| Y02 | unittest | no | `self.assertEqual(A, A)` **added** on the line before `self.assertEqual(A, B)` | as X02 |
| Y03 | pytest | yes | assert wrapped in `try: … except AssertionError as exc: raise AssertionError(f"context: {exc}") from exc` | as X05 |
| Y04 | unittest | yes | call wrapped in `try: … except AssertionError: raise` | as X06 |
| Y05 | pytest | yes | assert wrapped in `try: … finally: pass` | as X05 |
| Y06 | unittest | yes | a message added: `self.assertEqual(A, B, msg="check")` | as X02 |
| Y07 | pytest | yes | operands swapped: `assert B == A` | as X01 |

## Volumes, selection, contracts and split (frozen)

- **Volumes.** Each weakening pattern gets `n = 8` and each legitimate pattern `n = 5`: 64 weakenings and 35 legitimate edits if there is no shortfall. Shortfalls are logged, not padded.
- **Selection.** This is identical to Tier A: sites sorted by `(file, line, test)`, shuffled per repo with `random.Random("20261121-TAB-<pattern>-<repo>")`, then taken round-robin across repos in lock order, with each test used at most once per pattern.
- **Concentration, disclosed.** Only `python-jsonschema/jsonschema` and `hukkin/tomli` have unittest-style tests among the 7 sources, and jsonschema has most of them. unittest-style results therefore mostly reflect one repository.
- **Test identity.** `path::name` for functions, `path::Class::method` for methods.
- **Contracts.** Condition `C1` is mandatory and guards the mutated test; no pattern changes the contract.
- **Split.** The case IDs are sorted, shuffled with `random.Random("20261121-TAB")`, and the first `round(0.3 × N)` become held-out.
- **Bytes.** Cases are built from raw git objects (`git cat-file`), not the working tree.
- **Comparison fixed in advance.** On the held-out split, PROVECODE with the ADR 0011 rules is compared with:
  - PROVECODE without them, which is the engine at the freeze commit;
  - the count-based baseline;
  - "import Ruff instead": PROVECODE's existing rules plus Ruff 0.17.6's `PLR0124` (comparison with itself), `SIM222` (`… or True`), `B015`, `B011`, `PT015`, `S110` (try-except-pass) and `SIM105` (suppressible exception). A case counts for this variant when one of those codes fires more often in head than in base.
