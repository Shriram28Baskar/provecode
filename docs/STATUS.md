# Status: 2026-11-22 (release 0.2.0, beta)

Where the project stands, so it can be picked up later. Numbers are from runs on the release code; the ADRs hold the details.

## Released
- **`v0.2.0`**: a tag and a GitHub pre-release on commit `45fff08`. PR #1 (`v2/phase-2-trusted-rerun` into `main`) is open, with CI green on every push. **It is not merged:** merging was not part of the release plan. Merge it when ready, so that `main` and its README match the release.
- **Install from the tag works with pip, `uv tool install` and pipx.** Each was tested in a clean environment, and the installed CLI blocked a weakening, allowed an honest change, and installed a pre-commit hook that blocked a weakening commit.
- **Phase 1** (ADR 0012) and **Phase 2** (ADR 0013) are complete. ADR 0014 adds the catalog fixes, a zero-config gate, and the fixes found during release verification.

## Verified on the release code
| Check | Result |
|---|---|
| Engine tests (local) | 656 passed, 14 skipped (Docker opt-in, Windows symlink) |
| CI engine jobs (Linux and Windows × 3.11, 3.13) | 655/654 passed, the rest skipped |
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
1. Merge PR #1.
2. Label a sample of about 10 commits per rule (H1–H7, T1–T5), then compute per-rule precision with Wilson CIs (brief Phase 6). A small labeling CLI would speed this up.
3. 0.2.1 polish: `provecode --version`; a root-level package so the pre-commit framework can use `repo: .../provecode` with `rev: v0.2.x` (today only a `repo: local` hook works, as in the README).
4. Brief Phase 3 (live web analysis of real GitHub PRs) and Phase 4 (PyPI trusted publishing, SARIF, PR comments, `provecode pr`).

## Known limits (README "Known blind spots", ADR 0014)
- The re-run overlay copies pytest config only from the root.
- nox and Makefiles are not read.
- Runtime-only tricks are missed statically: an always-true non-constant `skipif`, or `async def` with no plugin.
- A module-level `importorskip` is not flagged.
- In `rerun` runs B and C, candidate code shares a process with the base tests (`docs/04-threat-model.md`).
