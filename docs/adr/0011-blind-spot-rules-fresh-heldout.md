# ADR 0011 — Blind-spot rules, judged only on a fresh held-out set (Tier A-b)

**Status:** Accepted, 2026-11-21. Recommendation R3 in `docs/research/landscape-2026-11.md`, where it was drafted as "ADR 0011". The owner delegated the choice of next step.

## Context
- PROVECODE misses every vacuous or swallowed check in its benchmarks: Tier A's W17–W19 (0/18) and Tier A-u's U13–U14 (0/12). These are tautologies (`A == A`), `… or True`, and assertions wrapped in `try/except AssertionError: pass`. Each is expressible as a deterministic rule.
- Those results have been seen. A rule written now and scored on W17–W19 or U13–U14 would be scored on its own training data.
- Ruff already has rules near some of these (`PLR0124` comparison-with-itself, `SIM222` `… or True`, `S110`/`SIM105` try-except-pass). On Tier A, `PLR0124` caught 2/6 W17 with 0/30 legitimate false positives (research report §1.7). The cheaper option is to import Ruff, not to write rules.

## Decision
- **Freeze first.** A new tier, Tier A-b (`benchmark/patterns_blindspots.md`, `generate_cases_blindspots.py`, `*_blindspots.lock.json`), is generated from 7 new repositories and committed **before** any rule code. It includes legitimate near-misses designed to tempt the new rules:
  - an added reflexivity check;
  - a re-raise with context;
  - `try/finally`.
- **Develop on the tuning split only.** Rule development may look at Tier A-b's tuning cases. Its held-out cases are evaluated once, after the rule code is committed.
- **Candidate rules,** stated before development:
  - **S5, check made vacuous.** A head check whose expected side is the same expression as its subject (`assert A == A`, `assertEqual(A, A)`, `A is A`), or whose test is an `or` with a truthy constant operand (`… or True`, `assertTrue(A or True)`). It fires only when the matched base check was not already vacuous. An *added* reflexivity check is not a weakening, because nothing was taken away.
  - **B4, assertion failure swallowed.** A check that sits, in head but not in base, inside a `try` body whose handler would catch `AssertionError`, and that handler does not re-raise. The handlers that catch it are `AssertionError`, `Exception`, `BaseException`, a bare `except:`, or a tuple containing one of these. "Does not re-raise" means the handler contains no `raise` statement. `try/finally` without such a handler is fine.
- **Compared in advance** with "import Ruff instead": the existing rules plus Ruff 0.17.6's `PLR0124`, `SIM222`, `B015`, `B011`, `PT015`, `S110` and `SIM105`, counted when a code fires more often in head than in base.
- **Other tiers.** They are reported as they come out, with the change disclosed in their `revision_log` and the effect measured. Tier A and Tier A-u recall for W17–W19 and U13–U14 is then no longer an unbiased estimate, and the site says so.

## Drop criteria (from R3)
- Drop a rule if, on the Tier A-b held-out split, it flags any legitimate edit.
- Drop the custom rules and adopt the Ruff variant if the Ruff variant reaches the same held-out recall with no more false positives.

## Consequences
- The headline recall for these patterns can change only on fresh data.
- The exception-check-replaced-by-value-check gap from the pilot (`sympy__sympy-12481`) is **not** addressed. That waits for the owner's label.
