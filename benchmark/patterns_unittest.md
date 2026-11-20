# Tier A-u pattern list: unittest-style tests (frozen before the detector change)

**Status:** Frozen in the commit that adds this file. That commit comes **before** any change to `engine/` for ADR 0009, and the commit order is recorded in `tierA_u.json` provenance.
**Authority:** ADR 0009. The method is the same as `benchmark/patterns.md`.
**Labels:** each pattern's ground-truth class was written by the developer. The owner should confirm them (`docs/research/landscape-2026-11.md` §5).

Each pattern is one mechanical edit to one real test **method of a `unittest.TestCase`-style class**, made at a pinned SHA in `sources_unittest.lock.json`. Every other byte of the file stays identical.

**Categories.** The `category` column uses the primer §3.5 taxonomy. U13 and U14 are `other`: real weakenings that no current rule targets. U13 and U14 are unittest forms of Tier A's W17 and W18.

**Count-preserving (`cp`).** The number of test methods and the number of assertion calls (`self.assert*` plus `assert` statements) are unchanged, counted statically.

## Weakening patterns

| ID | Category | cp | Edit (base → head) | Eligible site |
|---|---|---|---|---|
| U01 | comparison-loosened | yes | `self.assertEqual(A, B)` → `self.assertGreaterEqual(A, B)` | `assertEqual` call statement with exactly 2 positional args |
| U02 | trivial-expected | yes | `self.assertEqual(A, <lit>)` → `self.assertIsNotNone(A)` | as U01, with the 2nd arg a constant (not None or bool) |
| U03 | trivial-expected | yes | `self.assertEqual(A, <lit>)` → `self.assertTrue(A)` | as U02, with lit truthy |
| U04 | trivial-expected | yes | `self.assertEqual(A, <lit>)` → `self.assertIsInstance(A, <type(lit)>)` | as U02 |
| U05 | comparison-loosened | yes | `self.assertEqual(A, <num>)` → `self.assertAlmostEqual(A, <num>, delta=1000)` | as U02, with lit an int or float |
| U06 | exception-widened | yes | `assertRaises(X, ...)` → `assertRaises(Exception, ...)`, in call or `with` form | X is a bare name other than `Exception` / `BaseException` |
| U07 | exception-widened | yes | `assertRaisesRegex(X, msg, ...)` → `assertRaises(X, ...)` (message check dropped) | `assertRaisesRegex` with at least 2 positional args |
| U08 | test-deleted | no | the test method is deleted | its class has at least 2 test methods |
| U09 | skip-added | yes | `@unittest.skip("flaky")` added to the method | the module has `import unittest` |
| U10 | skip-added | yes | `self.skipTest("flaky")` inserted as the first statement | any test method |
| U11 | assertion-removed | no | one `self.assert*` call statement deleted | method with at least 2 such statements, each on its own lines |
| U12 | skip-added | yes | `return` inserted as the first statement | any test method |
| U13 | other | yes | `self.assertEqual(A, B)` → `self.assertEqual(A, A)` (tautology) | as U01 |
| U14 | other | yes | an assertion call wrapped in `try: … except AssertionError: pass` | `self.assert*` call statement on one line |
| U15 | assertion-removed | no | `self.assertEqual(A, B)` → expression statement `A == B` | as U01 |
| U16 | skip-added | yes | `@unittest.skip("flaky")` added to the **class** | the module has `import unittest`; the class has at least 1 test method |

## Legitimate patterns

| ID | Contract change | Edit (base → head) | Eligible site |
|---|---|---|---|
| V01 | **yes** (v1 → v2, C1 updated) | expected constant changed: number `n` → `n + 1`, str `s` → `s + "!"` | as U02 |
| V02 | no | test method renamed (`test_x` → `test_x_renamed`), body identical | any test method |
| V03 | no | `self.assertIsNotNone(A)` added before `self.assertEqual(A, B)` | as U01 |
| V04 | no | new test method added: a copy named `<name>_extra` | any test method |
| V05 | no | subject extracted: `result = A`, then `self.assertEqual(result, B)` | as U01, with A a call; `result` unused in the method |
| V06 | no | a comment line added above an assertion call | single-line `self.assert*` call statement |
| V07 | no | `assertRaises(X, ...)` → `assertRaisesRegex(X, ".*", ...)` (a message check added) | `assertRaises` with first arg a bare name |
| V08 | no | a message added: `self.assertEqual(A, B)` → `self.assertEqual(A, B, msg="check")` | as U01, with no msg |
| V09 | no | arguments swapped: `self.assertEqual(B, A)` | as U01 |
| V10 | no | `self.assertTrue(A)` → `self.assertIs(A, True)` (stricter) | `assertTrue` call with 1 arg |

## Volumes, selection, contracts and split (frozen)

- **Weakening volumes.**
  - Each weakening pattern gets `n = 6`, except U08, which gets `n = 12`.
  - The resulting category totals are: comparison 12, trivial 18, exception 12, deleted 12, skip 24, assertion-removed 12, other 12.
- **Legitimate volumes.** Each legitimate pattern gets `n = 3`.
- **Shortfalls** are logged, not padded.
- **Selection.** This is identical to Tier A: sites sorted by `(repo, file, line, test)`, shuffled per repo with `random.Random("20261120-TAU-<pattern>-<repo>")`, then taken round-robin across repos in lock order, with each test method used at most once per pattern.
- **Test identity.** `path::Class::method`.
- **Contracts.** Condition `C1` is mandatory and guards the mutated test. For V01 only, the head contract is v2.
- **Split.** The case IDs are sorted, shuffled with `random.Random("20261120-TAU")`, and the first `round(0.3 × N)` become held-out.
- **Bytes.** Cases are built from raw git objects (`git cat-file`), not the working tree, so they are byte-identical on every platform.
