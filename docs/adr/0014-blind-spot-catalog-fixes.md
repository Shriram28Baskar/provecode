# ADR 0014 — Fixes from a test-weakening catalog: subdirectory configs, compound asserts, moved tests, tox, and fewer false alarms

**Status:** Accepted, 2026-11-22. The owner asked for PROVECODE to be made useful for everyday engineering and approved working on the fixes. Policy choices are marked **(policy)** so the owner can overturn them.

## Context
- PROVECODE was installed on its own repository with the Claude Code hooks. It caught a deleted assert, an added skip, a stubbed body, a skip-all conftest and a weakening committed mid-session.
- Two blind spots showed up straight away:
  - pytest config in a subdirectory (`engine/pyproject.toml`) was never read;
  - `assert a and b == X` → `assert a and b is not None` was not seen.
- `docs/research/scripts/cheat_catalog.py` was written to look for more, and runs every case through the engine as `provecode guard` would.
  - **Round 1** (58 weakenings, 19 legitimate near-misses) was written first and the engine was fixed against it. Its final 58/58 and 0/19 are **tuned**, not a measurement.
  - **Round 2** (24 weakenings, 16 near-misses) was written after the round-1 fixes and run once. Its as-found result is kept in `docs/research/data/cheat_catalog_round2_asfound.json`: **16/24 weakenings blocked, 5/16 legitimate edits blocked.** Round 2 was then fixed too, so its final numbers are also tuned.
  - Both rounds were written by the same author as the rules. The catalog finds blind spots; it does not estimate recall or precision on real changes.
- Two catalog cases were wrong, not the engine:
  - `@pytest.mark.flaky(reruns=5)` was reclassified as legitimate: a deterministic regression fails every rerun.
  - A contrived `__import__` mock was rewritten as a real `@mock.patch`, which T3 already catches.

## Decision

### Coverage added
| Rule | Now caught |
|---|---|
| H4 | pytest config in any directory. Per directory that holds a changed config file, the config that governs it (the nearest one, in that directory or above) is compared at base and head. That includes a new, closer `pytest.ini` that shadows a parent's settings. The collector now loads the unchanged configs of that directory and its ancestors. |
| H4 | `filterwarnings` losing an `error` / `error::Category` entry. Adding `ignore::` entries is not flagged. |
| S1–S5, B3 | `assert a and b` is judged as `assert a` plus `assert b`. A conjunct dropped is B3; a conjunct loosened is S1/S2. |
| S5 | A check replaced by one that is always true: `assert True`, a truthy constant, or a non-empty tuple or list (`assert (x == 1, "msg")`). The base check paired with it must have a subject the head checks nowhere else. |
| S2 | Equality to a literal collection or string replaced by a check on `len()` of the same subject. |
| S1 | `abs(a - b) < t` and `math.isclose(a, b, ...)` are read as approx with those tolerances. So `== 80` → `abs(x - 80) < 50` is a relaxation, and widening `t` is too. |
| S3 | An exception added alongside the expected one (`ValueError` → `(ValueError, TypeError)`). Rule: something new is accepted while everything the base accepted still is. A swap is still not flagged. |
| S4 | The parametrize decorator removed, so the test runs once, unless the head loops over a literal with at least as many cases. |
| B2 | `pytest.importorskip(...)` newly added inside an existing test. `for _ in []:`, `range(0)`, `while False:` wrapping the checks. A loop that existed before and was emptied stays T4. |
| H1 | A new or changed `autouse=True` conftest fixture that patches the project's own packages (`monkeypatch.setattr`, `mock.patch`, `mocker.patch`, `patch.object`). Patching the environment, the stdlib or third-party modules is not flagged. |
| H5 | `tox.ini` `[testenv*]` test commands: narrowed selection, a leading `-` (tox ignores the exit status), or the test command removed. |
| H6 | `--cov-fail-under` lowered or removed in a CI test command or a tox command, not only in `addopts`. |

### False alarms removed
- **R0 instead of B1:** a test moved, body unchanged (decorators and class skip marks included), to another test file in the same change. The destination must be collected by name and not skipped at module level. Each copy explains one deletion. Earlier research (`b1_moved_tests.py`) measured 94 such B1 findings in 8 real commits.
- **`python -m pytest`:** the interpreter's `-m` is no longer read as pytest's `-m` marker filter.
- **`pytest.param(..., id=...)`:** a case given an id is the same case. A param with `marks=` is not.
- **Restructured asserts:** a check whose subject is still checked inside a new head check was restructured, not removed (`assert a and b == 1` → `assert (a, b) == (True, 1)`).
- **Plugin disables:** `-p no:cacheprovider`, `no:pastebin`, `no:xdist`, `no:sugar` and `no:html` are not H2. **(policy)** `no:randomly`, `no:warnings` and `no:cov` still are.
- **`# pragma: no cover` inside a string:** H6 counts only real comment tokens (via `tokenize`), falling back to the text match when a file does not tokenize. Found live: the Claude Code Stop hook blocked this session over the catalog script, whose probe P01 holds that text in a string.
- **Default approx:** `== x` → `pytest.approx(x)` at default tolerance (rel ≤ 1e-6, abs ≤ 1e-12), or `math.isclose` at its defaults, is no longer S1. **(policy)** A looser or non-literal tolerance still is. No frozen benchmark case has a default-tolerance approx: the generator's pattern uses `rel=0.5`.

### Known limits, not fixed
- **Needs runtime knowledge:** a `skipif` whose condition is always true but not a constant (`sys.version_info < (9,)`), and a test turned `async def` with no async plugin installed. The trusted re-run covers both.
- **Module-level `pytest.importorskip`** is not flagged: it is the usual way to handle an optional dependency.
- **Trusted re-run overlay:** `overlay.py` still copies only root config files, and merges pytest tables only for the root `pyproject.toml`. A weakened subdirectory config therefore still applies in run B. Static H4 reports the edit.
- **nox and Makefiles** are not read.

## Consequences
- Rule behaviour changed, so every benchmark tier must be regenerated and diffed against the frozen labels. Any change in a frozen case result is disclosed with the regeneration, as in ADR 0012's amendment.
- The catalog is kept as a regression check: `python docs/research/scripts/cheat_catalog.py` exits 1 if any case is wrong.
