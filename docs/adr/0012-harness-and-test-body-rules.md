# ADR 0012 — Harness and test-body integrity rules (H1–H7, T1–T5) over a host-independent Changeset

**Status:** Accepted, 2026-11-21. Phase 1 of `PROVECODE_V2_BUILD_BRIEF.md`. The owner reviewed the proposal and then delegated the decision while away ("Start proceeding further with what you would think would be the best option … Don't ask for my permissions"). Every choice below that the brief left open is marked **(decided under delegation)**, so the owner can overturn it.

## Context
- The seven cheapest cheats in brief §0.3 were run against the engine at `70f2dce`. All seven produce **zero findings**. Three of them (a planted `conftest.py`, `addopts = -k …`, `pytest || true` in CI) never reach the analyzer, because collection keeps only test files (`files_analyzed=0`).
- The planted-conftest cheat is the published SWE-bench exploit: a `conftest.py` whose `pytest_runtest_makereport` hook marks every result as passed. BenchJack reports it scores 100% on SWE-bench Verified (arXiv 2605.12673; Berkeley RDI blog). Read from the search result summaries only, not the full paper.
- pytest 9.2's own hookspec (`_pytest.hookspec`, read from the installed package) defines 54 hooks. They differ in what they can do: some decide outcomes, some decide collection, and some only change what is printed.
- GitHub Actions has supported YAML anchors and aliases since 2025-09-18 (GitHub changelog). Third-party write-ups say merge keys are not supported. A real workflow can therefore use `&`/`*`, which a stdlib-only subset parser must handle or refuse.

## Decision

### 1. A host-independent Changeset
- `provecode.changeset`: `FileChange(path, old_path, status, base_text, head_text)` and `Changeset(files, base_sha, head_sha, base_paths)`. `base_paths` (every path at base, optional) is used only by the shadow-module check.
- `classify(path)` returns one of: `test`, `conftest`, `harness` (`*.pth`, `sitecustomize.py`, `usercustomize.py`), `config` (`pytest.ini`, `.pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`, `.coveragerc`, requirements files), `ci` (`.github/workflows/*.yml|yaml`), `source` (other `.py`), or `other`.
- `pipeline.run` accepts either the existing list of `(path, base, head[, old])` tuples or a `Changeset`. The tuple path is unchanged, so every existing tier result must regenerate byte-for-byte, apart from the engine-source hash in provenance.
- The git collectors (`check`, `guard`) fetch contents for test, conftest, harness, config and CI files. Source `.py` files are fetched only for H6's pragma check.

### 2. Rules
All parsing is `ast`, `tomllib`, `configparser`, `shlex`, or the YAML subset below. Nothing is imported or executed.

| ID | Detects | Default severity |
|---|---|---|
| **H1** | A `conftest.py` that adds or changes: an **outcome** hook (`pytest_runtest_makereport`, `_call`, `_protocol`, `_setup`, `_teardown`, `pytest_pyfunc_call`, `pytest_runtestloop`, `pytest_report_teststatus`, `pytest_runtest_logreport`, `pytest_sessionfinish`, `pytest_report_to/from_serializable`, `pytest_fixture_setup`, `pytest_cmdline_main`); a **collection** hook (`pytest_collection_modifyitems`, `pytest_ignore_collect`, `pytest_pycollect_makeitem`, `pytest_pycollect_makemodule`, `pytest_collect_file`, `pytest_collect_directory`, `pytest_collection`, `pytest_make_collect_report`, `pytest_generate_tests`, `pytest_markeval_namespace`); `collect_ignore` / `collect_ignore_glob`; a process exit (`os._exit`, `sys.exit`); module-level tampering with `pytest` / `_pytest` attributes; or a `pytest_configure` / `pytest_cmdline_parse` / `pytest_load_initial_conftests` that touches `config.option`, `config.args`, `config.pluginmanager` or `config.inicfg`. Display-only hooks, fixtures and marker registration are **not** findings | **blocking**, with or without a contract |
| **H2** | Plugin loading changed: `pytest_plugins` added or extended; `-p NAME` / `-p no:NAME` added to `addopts`; a `pytest11` entry point added | warning; blocking with a contract |
| **H3** | `*.pth`, `sitecustomize.py` or `usercustomize.py` added or changed; an added module or package whose name shadows the stdlib (`sys.stdlib_module_names`), a test dependency (`pytest`, `_pytest`, `pluggy`, `unittest`, `hypothesis`, `mock`, `coverage`, …) or a top-level package of the repository (needs `base_paths`), placed at the repository root, in `src/`, or in a test directory | **blocking**, with or without a contract |
| **H4** | Test selection narrowed in pytest config: `addopts` gains `-k`, `-m`, `--deselect`, `--ignore`, `--ignore-glob`, `--collect-only`/`--co`, `--lf`, `--sw` or `--continue-on-collection-errors`, or loses `--doctest-modules`/`--doctest-glob`; `testpaths` loses an entry or is introduced; `python_files`/`python_classes`/`python_functions` lose a pattern (against pytest's defaults when unset); `norecursedirs` gains a pattern; `xfail_strict` turned off | warning; blocking with a contract |
| **H5** | CI weakened in `.github/workflows/*.yml`: a test step removed (or a workflow holding one deleted); `\|\| true`, `\|\| :` or `\|\| exit 0` after a test command; `continue-on-error` added to a test step or its job; a constant-false `if:` on a test step or job; the test command narrowed (new `-k`/`-m`/`--deselect`/`--ignore` or path arguments); `paths-ignore` gaining a pattern that covers Python source; `pull_request`/`push` triggers removed. "Test command" means `pytest`, `py.test`, `python -m pytest`, `python -m unittest`, `tox`, `nox`, `hatch test`/`hatch run …test…`, `make test`, `coverage run` (with `uv run`/`poetry run`/`pdm run`/`uvx` prefixes) | warning; blocking with a contract |
| **H6** | Coverage weakened: `fail_under` / `--cov-fail-under` lowered or removed; `omit` gains a pattern that is not test-only; `exclude_lines` / `exclude_also` gains a pattern; `# pragma: no cover` added to a non-trivial source line (trivial: `if __name__ == "__main__":`, `if TYPE_CHECKING:`, `raise NotImplementedError`, `pass`, `...`, `except ImportError:`, `def __repr__`, `@overload`, `@abstractmethod`) | warning; blocking with a contract |
| **H7** | Test dependencies: `pytest` or a `pytest-*` plugin, `hypothesis` or `coverage` removed, or pinned below its base major version, in `pyproject.toml` (`project.dependencies`, `optional-dependencies`, `dependency-groups`), `requirements*.txt`/`*.in`, or `setup.cfg` `extras_require` | warning, always |
| **T1** | Test inputs changed while the asserted value stayed the same. Either the check's subject is a call to the same callee with different literal arguments and the same expected value, or a local fed by such a call changed while every check on it is unchanged. Also flags input and expected changed together, in a test guarded by a mandatory condition. A pure expected-value change stays V0 | warning, capped (never blocking) until its real-world flag rate is reviewed |
| **T2** | A check delegated to an in-file helper (a module-level function or a method of the same class) whose body is trivially true: no `assert`, `raise` or call, and only constant returns. Also flags an existing helper hollowed out, for every test that calls it. A call to an imported or unresolved helper replacing removed checks is recorded as an `unresolved_helper` gap (UNKNOWN), never as a pass | graded like S/B (blocking via a mandatory contract link) |
| **T3** | A `monkeypatch.setattr`, `mock.patch`, `mocker.patch` or `patch.object` added to a test whose target resolves, through the file's imports, to the same symbol that the test's checks call | warning, capped |
| **T4** | A loop iterable emptied (`[]`, `()`, `{}`, `set()`, `range(0)`, `list()`, `x[:0]`, …) or a literal loop iterable losing elements, when the loop body holds checks; a parametrize argvalues list replaced by an empty constructor | graded like S/B |
| **T5** | An exception check (`pytest.raises`, `assertRaises*`) replaced by a value check, with no change in total check count | warning, capped |

- **Severity for H rules is repository-wide (decided under delegation).** An H finding is not tied to one test, so it cannot be linked to a `guarded_by` entry. H1 and H3 always block: they change what pytest reports or what code it imports, and no legitimate change needs them silently. H2 and H4–H6 block only when the repository has adopted an approved contract. H7 never blocks.
- This changes ADR 0010's "no contract ⇒ every finding is a warning". In static mode, `check` can now return FAILED without a contract, but only for H1 or H3.
- **Not built:** the brief's H4 clause "`-x` combined with a reorder". `-x` stops at the first failure and still reports it, so it hides nothing.
- **T5 and ADR 0011.** ADR 0011 left the exception-to-value gap waiting for the owner's label on `sympy__sympy-12481`. The brief now asks for T5. It ships capped at warning, and the sympy edit is **not** counted as a true positive anywhere unless the owner labels it.

### 3. H5 YAML: a stdlib, fail-closed subset parser in the core (decided under delegation)
- `provecode.yamlsubset` parses:
  - block mappings and sequences;
  - plain, single- and double-quoted scalars (including multi-line plain scalars);
  - literal and folded block scalars with chomping indicators;
  - flow sequences and mappings;
  - comments, `---`, anchors and aliases.
- Every scalar stays a string, as with PyYAML's `BaseLoader`, so `on:` never becomes `True`.
- Anything else (tags, merge keys `<<`, complex keys, multiple documents, directives) raises an error. That error becomes an `unparseable` gap for the file, **never a guess**. The browser therefore gets H5 too.
- **Verification:** a dev-only differential test against `yaml.load(…, Loader=yaml.BaseLoader)` runs on every distinct `.github/workflows/*` blob in the history of the 18 benchmark repositories and the 9 Tier H repositories.
- **Drop criterion:** if fewer than 95% of those blobs give an identical tree, or any blob gives a *different* tree (rather than a refusal), H5 becomes CLI-only with PyYAML and reports a "not available in the browser" gap.

### 4. Tier H, frozen before any rule code
- `benchmark/patterns_harness.md`, `generate_cases_harness.py`, `sources_harness.lock.json`, `cases_harness.lock.json` and `split_harness.lock.json` are committed **before** `changeset.py`, `harness.py`, `yamlsubset.py` or any T-rule code.
- The commit order is recorded in `tierH.json` provenance.
- **Sources:** 9 repositories, chosen by fixed criteria before any case was generated:
  - an MIT, BSD-3-Clause or Apache-2.0 license, verified at the pinned SHA;
  - at least one `conftest.py`;
  - a pytest configuration file;
  - a GitHub workflow that runs the tests;
  - not used by Tiers A, A-u or A-b, and not in the Gate 0 spike pool (R9).
- **Patterns:** weakening patterns cover H1–H7 and T1–T5. Near-misses that tempt each rule include a fixture added to `conftest.py`, marker registration in `pytest_configure`, a `testpaths` entry added, a CI matrix entry added, `continue-on-error` and `|| true` on non-test steps, `fail_under` raised, test-only `omit`, a pragma on `if __name__ == "__main__":`, a plugin added, an expected-value-only change, a collaborator monkeypatched, a loop extended, a helper that does assert, and `match=` added.
- **Development:** rules may be developed on the tuning split only. The held-out split is evaluated once, after the rule code is committed.
- **Baselines:**
  - the count-based baseline;
  - a "harness file touched" baseline (flags any change to a non-test harness, config or CI file), the coarse detector used in research (EvilGenie-style);
  - the engine at the freeze commit.
- **Metrics:** recall and legitimate-edit flag rate, with Wilson intervals and exact McNemar p-values, through `benchmark.regenerate`. H and T families are also reported separately.
- **Caveat, stated with every number:** the patterns mirror the rules' stated targets, as in Tier A-b. The results show the rules work as specified on injected edits. They are not real-world recall.

## Definition of done
- `engine/tests/test_v2_probes.py`: each of the seven §0.3 probes yields its expected finding (T1, T2, T3, T4, H1, H4, H5).
- Every rule has true-positive, near-miss and regression tests.
- `python -m benchmark.regenerate --check`: Tiers A, A-u and A-b reproduce with **no case result changed** (any change is disclosed in that tier's `revision_log`), and Tier H is generated.
- `npm run parity`: 100% on all tiers, including Tier H.
- `docs/research/scripts/real_history_scan.py` is rerun. The flag rate is reported per rule, and the new flags are listed for the owner to label. Nothing is labeled by Claude.

## Consequences
- The engine now reads config, CI and coverage files. The false-positive surface is larger, and Tier C precision still is not measured.
- H1 and H3 give FAILED without a contract. A repository that legitimately edits an outcome hook must approve that through a contract change or accept the block.
- The YAML parser is new code on an untrusted-input path. It runs under the same size limits as the analyzer, and it is fuzzed by the differential corpus.
- **Code locations:** ADR 0007's allowance already covers `engine/`, `benchmark/`, `web/` and `artifacts/`. Nothing in this phase is written outside them except `docs/`.

## Outcome (2026-11-21, measured)

- **Commit order:**
  - ADR `049b8c6`;
  - Tier H frozen `9eca74a` (patterns, generator, 243 cases, split), before any rule code;
  - rule code `2454eda`;
  - Tier H runner `b483912`.
  
  Development used the tuning split only, with one logged round (`tierH.json` `tuning_log`):
  - **H6:** a pragma on an aliased `TYPE_CHECKING` is trivial.
  - **H7:** "pinned down" compares upper bounds.
  - **H3:** a shadowing module inside a test package is an advisory warning. The pattern labels HS02-02 and HS02-03 weakenings; the owner should confirm that label.
- **Tier H held-out** (73 cases, evaluated once after `2454eda`):
  - PROVECODE catches **47/47** weakenings (95% CI 92–100%) and flags **0/26** legitimate near-misses (CI 0–13%).
  - The engine at the freeze commit catches 0/47.
  - "Harness file touched" catches 37/47 and flags 19/26 legitimate edits.
  - The count-based baseline catches 0/47.
  - Exact McNemar against the freeze engine: p = 1.4e-14.
  - **Caveat:** the patterns mirror the rules' stated targets.
- **Existing tiers:** all 399 case results in Tiers A, A-u and A-b are unchanged; only the engine-source hash in provenance changed.
- **Pyodide parity:** 642/642 (A 168, A-u 132, A-b 99, H 243), 0 mismatches.
- **H5 YAML decision held:** on 3,494 real workflow blobs from 27 repositories' histories, the subset parser is identical to PyYAML BaseLoader on 100%, with 0 refusals and 0 misparses. It also rejects all 13 blobs PyYAML rejects (`docs/research/data/yaml_subset_differential.json`).
- **The brief's §8 assumption "pytest settings live in `[tool.pytest.ini_options]`" is incomplete.**
  - 3 of the 9 Tier H repositories (anyio, stamina, cattrs) use pytest 9's native `[tool.pytest]` table.
  - pytest 9.2 also reads `pytest.toml` / `.pytest.toml`.
  - H4 and the Phase 2 overlay handle both tables and all seven config files.
- **A cheat the brief did not list:** adding an empty, higher-precedence `pytest.ini` silently drops every setting in `pyproject.toml` / `tox.ini` / `setup.cfg`. H4 reports it (Tier H pattern HP05).
- **Real-history flag rate** (`docs/research/scripts/real_history_scan_v2.py` → `real_history_scan_v2.json`; engine `b660dbd`, which is Phase 1 plus the Phase 2 T3 import fix; 18 benchmark repositories; no contract; a flag means a weakening finding of severity warning or blocking). **This is a flag rate, not precision.**
  - **Population T** (v1's selection, 3,032 test-editing commits):
    - 502 flagged (16.6%), against 422 (13.9%) for v1's rules;
    - 106 with an H or T finding;
    - commits by new rule: T1 27, H6 42, H4 14, H5 12, T4 10, T5 6, H1 4, H7 4, H3 1.
  - **Population H** (2,815 commits touching conftest, config, CI, requirements or harness files):
    - 197 flagged (7.0%);
    - 143 with an H or T finding;
    - commits by rule: H5 55, H6 40, H4 25, H7 12, H1 10, T1 5, H2 2, T4 2, H3 1, T5 1.
  - **210 commits with an H or T finding are listed for the owner to label** (`owner_label: null`). Claude labeled nothing.
  - **Likely false-positive classes seen while reading the output, for the owner to confirm or overturn. No rule was changed because of them:**
    - a `tox -e mypy` / `-e lint` job counted as a test job (H5);
    - `continue-on-error: ${{ matrix.experimental }}` for allowed-failure matrix entries (H5);
    - a regex exclude for `...` (`\.{3}`) and `omit **/__main__.py` (H6);
    - `testpaths` introduced during config cleanups (H4);
    - T5 and T1 firing on a generated test file in a Unicode data update (kjd/idna `9a4bc59e9d`, `007bcb6836`), where the specification itself changed.

## Amendment, 2026-11-22: precision fixes from real-history review (disclosed in `tierH.json` `revision_log`)

- **H5:** a tox/nox call is a test command unless every selected env or session is a non-test one (lint, typing, docs, docset, build, …). Names containing "test" (`docs-doctests`) still count.
- **H4:** with the repository's file list, `testpaths`, `python_files` and `norecursedirs` changes are reported only when pytest would stop collecting a test file it collected before, and those files are named. Without a file list the rule judges by value, as before.
- **H5:** PYTEST_ADDOPTS narrowing is read from workflow, job and step `env`, not only step `env`.
- **H6:** regex spellings of an ellipsis, and omitting `__main__.py` or version files, are boilerplate.
- **Parsing:** analyzed source is parsed with Python's warnings silenced. A test (`test_robustness.py`) showed that `-W error` turned a parseable file with an invalid escape into a false gap.
- **Effect:**
  - 8 Tier H case results changed, all workflow cases whose step runs `tox -e docs`, `tox run -e typing`, `tox -e lint` or `tox run -e docset`. Their "weakening" labels are disputed and listed for the owner.
  - Held-out recall against the frozen labels: 44/47. Legitimate edits flagged: 0/26.
  - **Owner decision (2026-11-22):** the 8 labels stay frozen as "weakening". The headline figure is 44/47, and the 8 cases are reported as a disclosed disagreement between the frozen labels and the corrected H5 rule.
  - No other tier changed.
- **Engineering bar:**
  - `mypy --strict` clean on `engine/src/provecode`;
  - ruff clean (rules in `engine/pyproject.toml`);
  - a corpus test runs every `.py`, config and workflow file of the 27 pinned repositories through the engine;
  - a seeded fuzz test mutates real files 1,500 times. No crashes.
- **Real-history flag rate after these fixes** (same scan, engine `f7f87ed`; a flag rate, not precision):
  - Population T: 495 of 3,032 flagged (16.3%), 97 with an H or T finding (was 502 and 106).
  - Population H: 168 of 2,815 (6.0%), 112 with an H or T finding (was 197 and 143). By rule: H4 25 → 12, H5 55 → 44, H6 40 → 31.
  - **175 commits** are listed for the owner to label (was 210).
