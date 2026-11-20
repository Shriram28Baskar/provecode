# ADR 0009 — Recognise unittest-style tests

**Status:** Accepted, 2026-11-20. The owner delegated the choice of next step ("proceed with the decision that would help us achieve the main objective"). This was recommendation R1 in `docs/research/landscape-2026-11.md`.

## Context
Measured on the SWE-bench Verified gold test patches (`docs/research/data/swebench_agent_tests.json`, `gold_test_file_visibility`):
- The filename filter (`test_*.py` / `*_test.py`) sees 457 of 652 test files (70.1%).
- For Django, which accounts for 231 of the 500 tasks, it sees 155 of 328.

The analyzer recognises only `test*` functions and methods of `Test*` classes. Methods of `class FooTests(TestCase)` are invisible, and so are `self.assert*` checks. In the real-agent pilot, one of the 7 test files a Gemini agent edited was missed by the filename filter. In another, zero tests were recognised: `WriterTests(SimpleTestCase)`.

## Decision
- **Test files.** Also treat `tests.py` files, and any `.py` file under a `tests/` or `test/` directory, as test files. `conftest.py` and `__init__.py` are not test files.
- **Test classes.** Also recognise `test*` methods of classes whose bases syntactically name a `*TestCase` or `*TestCase`-like base: any base whose final name ends in `TestCase` or `Tests`, or is `TestCase`.
- **unittest checks.** `self.assert*` calls are checks. Each is mapped onto the existing model so that S1–S3 and B3 apply unchanged.
  - Equality and comparison: `assertEqual` → `==`, `assertGreaterEqual` → `>=`, `assertNotEqual` → `!=`, `assertTrue(x)` → truthiness, `assertIsNotNone(x)` → not-None, `assertIsInstance(x, T)` → isinstance.
  - `assertAlmostEqual` → `approx` (with `places`/`delta` as tolerance).
  - `assertRaises(X)` and `assertRaisesRegex(X, msg)` → raises (`msg` treated like `match=`).
- **unittest skips.** `@unittest.skip`, `@skipIf(True)` and `self.skipTest()` before the body count as B2 skips and guards.
- **Benchmark order.** A fresh unittest-style benchmark (Tier A-u) is generated from new, permissively licensed repos that are not Gate 0 spike repos. Its patterns, cases and held-out split are committed **before** the detector changes. The existing Tier A numbers are regenerated and must not change for any existing case.

## Consequences
- Visibility on real suites is re-measured against the drop criteria in the research report:
  - at least 90% file visibility on the SWE-bench gold test files
  - analyzer gaps on at most 5% of newly visible files
- More code is analyzed, so more false-positive surface. Tier C precision becomes more urgent.
- Tier A-u labels are pattern classes written by the developer, like Tier A's. The owner should confirm them (research report §5).
