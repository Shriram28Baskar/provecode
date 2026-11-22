# Changelog

All notable changes to PROVECODE. Versions follow [Semantic Versioning](https://semver.org/); while the major version is 0, a minor release may change behaviour.

## 0.2.1 — 2026-11-22 (beta)

### Added
- `provecode --version`.
- **pre-commit framework hook:** `repo: https://github.com/Shriram28Baskar/provecode`, `rev: v0.2.1`, `id: provecode` runs `provecode guard --staged`. A root `pyproject.toml` builds the same package from `engine/src`, because pre-commit installs a hook repository from its root. Checked with `pre-commit try-repo`: a staged weakening is blocked (S2) and a staged honest refactor passes.

### Changed
- Packaging uses an SPDX license expression (`license = "MIT"`, setuptools ≥ 77). The old table form is deprecated, and setuptools stops building it after 2027-02-18.

### Verified
- The GitHub Action (`@v0.2.0`, no contract) on a real repository, [provecode-action-demo](https://github.com/Shriram28Baskar/provecode-action-demo/pulls): the PR that loosens a test fails the check, and an honest refactor passes it.

No rule changed. Every benchmark case result is identical to 0.2.0.

## 0.2.0 — 2026-11-22 (beta)

First release meant for use outside this repository. Real-world precision is not yet measured (see README, "Honest scope").

### Added
- **Zero-config pull-request gate.** The GitHub Action's `contract` input is optional. Without a contract, any test-weakening finding fails the check (`block-on: weakening`). `provecode check --block-on weakening` does the same from the CLI.
- **Rules (ADR 0014):**
  - pytest config in any directory (`engine/pyproject.toml`), including a closer config that shadows a parent's;
  - `filterwarnings = error` removed;
  - `assert a and b` judged per conjunct;
  - always-true asserts (`assert True`, `assert (x, "msg")`);
  - equality reduced to a `len()` check;
  - `abs(a - b) < t` and `math.isclose` read as tolerances;
  - an exception added alongside the expected one;
  - the parametrize decorator removed;
  - `pytest.importorskip` added to an existing test;
  - checks moved into a loop that never runs;
  - autouse conftest fixtures that patch the package under test;
  - `tox.ini` test commands narrowed, `-`-prefixed or removed;
  - `--cov-fail-under` lowered in CI and tox commands.
- Trusted re-run (`provecode rerun`, `check --execute`): base tests against head code in sealed containers (ADR 0013).
- Harness and test-body rules H1–H7 and T1–T5 (ADR 0012).
- `provecode guard` and `install-hooks` for pre-commit and Claude Code (ADR 0010).
- `docs/research/scripts/cheat_catalog.py`: a regression catalog of weakenings and legitimate near-misses.
- `LICENSE` (MIT).

### Changed
- `pytest.approx` and `math.isclose` at their default tolerances no longer count as a weakening of `==`. A looser tolerance still does.
- Disabling `cacheprovider`, `pastebin`, `xdist`, `sugar` or `html` with `-p no:` is no longer a plugin finding.

### Fixed (false alarms)
- A test moved unchanged to another test file is recorded as a move (R0), not a deletion (B1).
- `python -m pytest` is no longer read as a marker filter.
- Parametrize cases given `pytest.param(..., id=...)` are the same cases.
- An assert restructured into one equality (`assert (a, b) == (...)`) is not a removal.
- `# pragma: no cover` inside a string is not a coverage exclusion.
- CI and tox test commands (H5, H6), found by the real-history scan:
  - tox's `{posargs}` is read as nothing and `{posargs:default}` as its default, which is what runs when no arguments are given;
  - `coverage run -m`, `{envpython} -m`, and runner options before `-m` (`python -b -m`) name the module to run, not a marker filter;
  - values of options such as `--cov-report xml` are not test paths;
  - a test path widened to its directory is not fewer tests;
  - paths are compared only when both commands run pytest;
  - a GitHub `${{ ... }}` expression is one value and, when it selects tox envs, unknown rather than narrowed;
  - a shell comment that mentions pytest is not a test command;
  - `[testenv:lint]`, `[testenv:flake8]` and the other non-test env names already exempt in CI are exempt in `tox.ini` too.

### Fixed (performance)
- The hollowed-helper check (T2) rebuilt its table of in-file helpers once per test, so its cost grew with the square of the number of tests. On `kjd/idna`'s 1.5 MB test file (about 6,300 tests) one `provecode guard` run took about 107 s; it now takes about 5 s, most of it parsing the file. Findings are unchanged.
- The `pytest_plugins`-in-a-test-module check (H2) no longer parses a test module twice when the name does not occur in it. The search runs on NFKC-normalised text, so a look-alike spelling is still parsed and found.

## 0.1.0

Hackathon release: static rules S1–S5 and B1–B4, contracts, recorded runs, the GitHub Action, and the web demo.
