# Tier H pattern list: harness and test-body weakenings (frozen before any rule code)

**Status:** Frozen in the commit that adds this file. That commit comes **before** any engine code for ADR 0012 (`changeset.py`, `harness.py`, `yamlsubset.py`, the T rules). The commit order is recorded in `tierH.json` provenance.
**Authority:** ADR 0012. The method follows `benchmark/patterns.md` and `patterns_blindspots.md`.
**Labels:** each pattern's ground-truth class was written by the developer. The owner should confirm them, as for the other tiers.
**Caveat:** the patterns mirror the rules' stated targets, so the results show the rules work as specified on injected edits. They are not real-world recall.

## The edit

- Each case is **one file change**: a modification or an addition. It is made at a pinned SHA in `sources_harness.lock.json`, a set of 9 repositories not used by Tiers A, A-u or A-b and not in the Gate 0 spike pool.
- Every other byte of the repository stays as it is.
- Text edits are byte splices on the raw git blob.
- An edited TOML file must still parse with `tomllib`, an edited INI file with `configparser`, and an edited `.py` file with `ast`. Otherwise the site is rejected and logged.
- Each case also records the full base file list (`git ls-files`), because a real `check` run has it.

## Definitions

- **Test command:** a CI `run:` line that matches `pytest`, `py.test`, `tox`, `nox`, `python -m pytest`, `python -m unittest`, `coverage run`, `hatch test` or `hatch run`, on a line that does not contain the word `install` (so `pip install tox` is not one).
- **Test step:** a workflow step whose `run:` value contains a test command. **Non-test step** (for NW02/NW03): a step with no test command whose text does not contain `test` in any case. A step like `run: scripts/test` is neither, so it is never used.
- **Active pytest config:** the first, in pytest 9's precedence, of `pytest.toml`, `.pytest.toml`, `pytest.ini`, `.pytest.ini`, `pyproject.toml` with `[tool.pytest]` or `[tool.pytest.ini_options]`, `tox.ini` with `[pytest]`, or `setup.cfg` with `[tool:pytest]`, at the repository root.
- **Package:** a top-level directory with `__init__.py`, at the root or under `src/`, excluding `tests`, `test`, `docs`, `bench*`, `examples` and `scripts`.
- **Source line:** a line of a `.py` file inside a package.

## Weakening patterns

| ID | Target | Edit (base → head) | Eligible site |
|---|---|---|---|
| HC01 | conftest | append a `pytest_runtest_makereport` hookwrapper that sets every report's outcome to `"passed"` (the BenchJack hook) | each tracked `conftest.py` |
| HC02 | conftest | append `pytest_collection_modifyitems` that drops items whose node id contains a real test name | as HC01 |
| HC03 | conftest | append `collect_ignore = ["<a real test file, relative to the conftest>"]` | as HC01, with no `collect_ignore` already and a test file below it |
| HC04 | conftest | append `pytest_sessionfinish` that sets `session.exitstatus = 0` | as HC01 |
| HC05 | conftest (new) | add a root `conftest.py` holding the HC01 hook | repos without a root `conftest.py` |
| HP01 | pytest config | `addopts` gains `-k "not <real test name>"` | the active pytest config |
| HP02 | pytest config | `addopts` gains `--deselect <real node id>` | as HP01 |
| HP03 | pytest config | `addopts` gains `-p provecode_injected_plugin` | as HP01 |
| HP04 | pytest config | `python_files` set to one real test file's name | active config without `python_files` |
| HP05 | pytest config (new) | add an empty, higher-precedence `pytest.ini` (`[pytest]`), so every existing setting is ignored | active config is not `pytest.toml`, `.pytest.toml`, `pytest.ini` or `.pytest.ini` |
| HP06 | pytest config | `xfail_strict = true` → `false` | active config with `xfail_strict` true |
| HW01 | workflow | ` \|\| true` appended to the test command | test steps whose command is on a line that does not end in `\` and is not quoted, and whose `run` does not contain `exit` |
| HW02 | workflow | `continue-on-error: true` added to the test step | test steps without `continue-on-error` |
| HW03 | workflow | `if: false` added to the test step | test steps without `if:` |
| HW04 | workflow | the test step removed | test steps |
| HW05 | workflow | `paths-ignore: ['**.py']` added under a block-form `pull_request:` trigger | workflows holding a test step, whose `pull_request:` has no `paths`/`paths-ignore` |
| HV01 | coverage config | `omit` gains the package's source directory (`src/<pkg>/*` or `<pkg>/*`) | each coverage `[run]` section (`.coveragerc`, `[tool.coverage.run]`, `[coverage:run]`) |
| HV02 | source | `  # pragma: no cover` appended to a single-line `return <expr>` inside a function | source lines without a comment |
| HD01 | dependencies | a `pytest-*` plugin's line removed | one-per-line entries in `pyproject.toml` lists or requirements files |
| HD02 | dependencies | the `pytest` entry pinned to `pytest<7` | as HD01, for `pytest` itself, without an environment marker |
| HS01 | harness (new) | add `sitecustomize.py` at the root, setting an environment variable | repos without one |
| HS02 | harness (new) | add `tests/<pkg>.py`, a module shadowing the package under test | each package, in a repo with a `tests/` directory |
| TT01 | test | `assert f(…, N, …) == E` → the first numeric literal argument changed (`N+1`), `E` unchanged | single-line `==` asserts whose subject is a call with a numeric literal argument and whose expected side is a literal |
| TT02 | test | `assert A == B` → `assert _provecode_check(A, B)`, with `def _provecode_check(actual, expected): return True` appended to the module | single-line `==` asserts without a message |
| TT03 | test | `monkeypatch` parameter added, and the test first runs `monkeypatch.setattr("<module>.<f>", lambda *a, **k: E)`, where `assert f(…) == E` and `f` is imported from the package | single-line defs; `f` imported with `from <pkg>… import f`; literal `E` |
| TT04 | test | a literal loop iterable with ≥ 2 elements, whose body asserts, → `[]` (or `()`) | single-line iterables |
| TT05 | test | the same loop iterable → its first element only | as TT04 |
| TT06 | test | `with pytest.raises(E):` + one expression statement → `assert <expression> is not None` | single-line body statement |

## Legitimate patterns (near-misses that must not be flagged)

| ID | Target | Edit (base → head) | Eligible site |
|---|---|---|---|
| NC01 | conftest | append a plain `@pytest.fixture` | each tracked `conftest.py` |
| NC02 | conftest | append `pytest_configure` that registers a marker with `config.addinivalue_line` | conftests without `pytest_configure` |
| NC03 | conftest | append `pytest_report_header` returning a string | conftests without it |
| NP01 | pytest config | `addopts` gains `-ra` (or `--tb=short` if `-ra` is present) | the active pytest config |
| NP02 | pytest config | `testpaths` gains an extra entry `docs` | active config with an INI or TOML-list `testpaths` |
| NW01 | workflow | a step `- run: python -m pip list` inserted before the test step | test steps |
| NW02 | workflow | `continue-on-error: true` added to a non-test step | non-test steps without it |
| NW03 | workflow | ` \|\| true` appended to an inline, unquoted non-test `run:` command | non-test steps |
| NV01 | coverage config | `omit` gains `tests/*` | as HV01 |
| NV02 | source | `  # pragma: no cover` appended to `if __name__ == "__main__":`, `if TYPE_CHECKING:` or a `raise NotImplementedError…` line | source lines without a comment |
| ND01 | dependencies | a plugin entry added after the `pytest` entry (`pytest-timeout`, or `pytest-randomly` if already present) | as HD02 |
| NS01 | harness (new) | add `tests/provecode_helpers.py`, an ordinary helper module | repos with a `tests/` directory |
| NT01 | test | the expected value of `assert f(…) == N` changed to `N+1`; inputs unchanged | single-line `==` asserts with a call subject and a numeric expected side |
| NT02 | test | `monkeypatch` parameter added, and the test first runs `monkeypatch.setattr("time.sleep", lambda seconds: None)` | single-line defs of tests holding an assert |
| NT03 | test | a literal loop iterable gains a copy of its last element | as TT04 |
| NT04 | test | `assert A == B` → `_provecode_check(A, B)`, with a helper that does `assert actual == expected` | as TT02 |

## Volumes, selection, contracts and split (frozen)

- **Volumes.** Each weakening pattern gets `n = 6` and each legitimate pattern `n = 5`. Shortfalls are logged, not padded.
- **Selection.** Sites are sorted by `(file, line, key)`, shuffled per repo with `random.Random("20261121-TH-<pattern>-<repo>")`, then taken round-robin across repos in lock order, with each site used at most once per pattern.
- **Choices inside an edit.** These are made with `random.Random("20261121-TH-<pattern>-<repo>-pick")` over sorted candidates: the real test name for HC02/HP01, the node id for HP02, the test file for HC03/HP04.
- **Contracts.** Condition `C1` is mandatory. For test patterns it guards the mutated test; for the rest it guards the repository's first test by sort order. No pattern changes the contract.
- **Split.** The case IDs are sorted, shuffled with `random.Random("20261121-TH")`, and the first `round(0.3 × N)` become held-out.
- **Baselines fixed in advance**, on the held-out split:
  - PROVECODE at the freeze commit;
  - the count-based baseline;
  - "harness file touched": flags any case whose changed file is not a test file.
