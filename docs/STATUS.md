# Status: 2026-11-22 (release 0.2.1, beta)

Where the project stands, so it can be picked up later. Numbers are from runs on the release code; the ADRs hold the details.

## Released
- **`v0.2.1`** (current) and **`v0.2.0`**: tags and GitHub pre-releases, both on `main`. `main` was fast-forwarded to the 0.2.0 release, so PR #1 shows as merged and every commit SHA, including the tag's, is unchanged. 0.2.1 followed through PR #2.
- **0.2.1 adds** `provecode --version`, a pre-commit framework hook (`repo: https://github.com/Shriram28Baskar/provecode`, `rev: v0.2.1`, `id: provecode`) and an SPDX license expression. No rule changed.
- **Install from the tag works with pip, `uv tool install` and pipx.** Each was tested in a clean environment. The installed CLI blocked a weakening, allowed an honest change, and installed a pre-commit hook that blocked a weakening commit. The pre-commit framework hook was checked with `pre-commit try-repo`, and again from GitHub with `rev: v0.2.1` in a real `.pre-commit-config.yaml`: the weakening commit was blocked (S2) and the honest commit went through. `pip install ...@v0.2.1` gives `provecode --version` = `provecode 0.2.1`.
- **The GitHub Action was run on a real repository:** [provecode-action-demo](https://github.com/Shriram28Baskar/provecode-action-demo/pulls). The weakening PR fails the check, and the honest refactor passes it. The repository's own tests pass on both.
- **Phase 1** (ADR 0012) and **Phase 2** (ADR 0013) are complete. ADR 0014 adds the catalog fixes, a zero-config gate, and the fixes found during release verification.

## Verified on the release code
| Check | Result |
|---|---|
| Engine tests (local, 0.2.1) | 660 passed, 14 skipped (Docker opt-in, Windows symlink) |
| CI engine jobs (Linux and Windows × 3.11, 3.13), 0.2.1 | 659 passed on Linux, 658 on Windows, of 674; the rest skipped; 0 failed |
| Docker tests (`PROVECODE_DOCKER=1`) | 15 passed |
| `mypy --strict`, `ruff check src` | clean |
| Benchmark held-out recall | A 97.7%, A-u 96.4%, A-b 100%, H 93.6% (44/47); legitimate edits blocked 0 in every tier |
| `regenerate --check`, Pyodide parity | byte-for-byte; 642/642, 0 mismatches (locally and in CI) |
| Site build, lint, Chrome e2e | pass (locally and in CI) |
| `guard_eval` | 399/399 identical to commit mode; median 0.785 s (ADR 0010 budget 1 s) |
| Trusted re-run, 7 real repositories | all VERIFIED (prettytable after one `deps_failed` network attempt, kept) |
| Cheat catalog | 58/58 and 22/24 blocked; 0/19 and 0/16 false alarms |

## Waiting on the owner
- **Labels: 179 real commits** in `docs/research/data/real_history_scan_v2.json` (`owner_label: null`). They are needed for a real-world precision figure. The older backlog is still there too: 422 v1 flags and 7 real agent edits. Claude has labeled nothing.
- **Decisions left open in ADR 0014:** whether `[testenv:check]` and `tox -e mypy-test` count as test envs.
- **Untracked files from before 2026-11-22, not touched:** `PROVECODE_V2_BUILD_BRIEF.md`, `spike/labeling/relabel_tool.py`, `spike/labeling/test_relabel_tool.py`.

## Next, in order of value
1. Label a sample: `python docs/research/scripts/label.py --sample 10` (75 commits, about 10 per rule), then `--summary` for per-rule precision with Wilson 95% intervals (brief Phase 6). Labels go to `docs/research/labels/real_history_scan_v2.jsonl`; the tool resumes where you stopped.
2. Brief Phase 3 (live web analysis of real GitHub PRs) and Phase 4 (PyPI trusted publishing, SARIF, PR comments, `provecode pr`).

## Known limits (README "Known blind spots", ADR 0014)
- The re-run overlay copies pytest config only from the root.
- nox and Makefiles are not read.
- Runtime-only tricks are missed statically: an always-true non-constant `skipif`, or `async def` with no plugin.
- A module-level `importorskip` is not flagged.
- In `rerun` runs B and C, candidate code shares a process with the base tests (`docs/04-threat-model.md`).
