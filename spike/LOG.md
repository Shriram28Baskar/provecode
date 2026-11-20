# Gate 0 log

Running log for the Gate 0 spike. It is governed by `spike/PREREGISTRATION.md`. Values in this file are recorded facts. Nothing here changes a pre-registered threshold.

**Gate 0 start date:** 2026-11-20 (the day of the Step B sampling run).

## 1. Commits

| What | Commit |
|---|---|
| Pre-registration (commit 1) | `f44e44a3ea88d14743d45eed9b615712b0a8d025` |
| Line endings pinned under `spike/` before label freeze (`.gitattributes`: `spike/** text eol=lf`) | `9abfe8f60e3308aed45191de572e102bf5ac5c32` |
| Sample run record, SHAs and snapshot (commit 2) | `095fffa7d3609426bc2324f1ef20b3bcac8564ab` |

## 2. Tool versions

| Tool | Version |
|---|---|
| gh | 2.105.0 (2026-11-17) |
| Python | 3.11.3 (CPython, 64-bit) |
| git | 2.51.0.windows.1 |

These are the versions recorded by the sampling script in `spike/build_sample.log`.

## 3. Step B run record

- Started 2026-11-20T04:15:18Z, finished 04:49:57Z (UTC). Exit code 0. The script's self-check passed. No aborted runs.
- Command: `python -I -B spike/sampling/build_sample.py`, run by the owner in plain PowerShell, not through Claude Code.
- Script SHA-256: `463fccc40a1c70734e4659c35d904b6bedb15e79c7e6bb4f9cf23f89a4352fd7`. The script was not edited after the run.
- Selected repos: mitmproxy/mitmproxy (W), plotly/dash (D), realiti4/claude-swap (T). The full record is in section 13 of `spike/PREREGISTRATION.md`.
- Sample: 200 random, 45 keyword, 100 reserve. No shortfall.
- Clones were made under the system temp folder and a stable copy was made outside the repo. Each clone is at its recorded HEAD SHA. The locations are not recorded here.
- **Reserve:** the SHA list was hashed but never opened. First-opened date: not yet opened.

## 4. Desktop device record

| Field | Value |
|---|---|
| Make / model | HP Laptop 15-dw4xxx (model name masked by Windows). Product number 715X5EA#ABV. The exact model name was not confirmed |
| Type | Laptop |
| CPU | 12th Gen Intel Core i5-1235U, 10 cores, 12 logical processors |
| RAM | 7.7 GB usable as reported by Windows. The installed amount was not checked separately |
| Windows | Windows 11 Home, version 10.0.26200, build 26200 |
| Browser installed: Chrome | 158.0.8127.36 (recorded 2026-11-20) |
| Browser installed: Edge | 157.0.4341.60 (recorded 2026-11-20) |
| Browser for measurements | Chrome. Confirm before the first hosting measurement |

Browser versions are re-recorded at every measurement run, and any change is logged. Power state (plugged in, power mode) is recorded at each measurement run.

## 5. Phone device record

Not yet filled. To be recorded before the first hosting measurement: make / model, SoC, RAM, Android version, browser and version, and the M1 to M4 evidence (launch listing URL and fetch date), as required by section 10 of `spike/PREREGISTRATION.md`.

## 6. Events still to be logged

- Label-freeze date and SHA.
- detector-v0 and detector-v1 SHAs.
- Reserve-freeze date and SHA, if the reserve is used.
- First detector run date.
- Date the owner first opened `spike/detector_out/` (self-reported).
- Self-consistency subset SHAs, before each relabel starts.

## 7. Disclosures for the spike report

Interpretations made at implementation time, after commit 1:

1. 500 MB means 500,000,000 bytes.
2. Bucket membership also counts the topics field, so cross-bucket exclusion is wider than appearance in search results alone.
3. The owner and the `pytest-` prefix are matched case-insensitively.
4. Only the attribute form `pytest.raises` / `pytest.warns` counts at R2, so `from pytest import raises` is undercounted.
5. R2 unparseable counts depend on the Python version (3.11.3), so files with newer syntax count as unparseable at R2. This is not K3.
6. Section 13 records the random and keyword SHAs by file and hash (`spike/sample_random_shas.txt`, `spike/sample_keyword_shas.txt`) and does not inline them. The reserve list is kept as a separate sealed list.

Observations from the run:

7. frappe/frappe and whyisdifficult/jiratui were rejected at R2, and the log shows their R2 numbers but no explicit "fail R2" line. frappe's pytest share was 0.136, below the 70% rule. jiratui had a pytest share of 1.0 and no detected pytest configuration. That reason is inferred, not verified. Selection was left as drawn, because no manual skips are allowed.
8. realiti4/claude-swap was selected by the seeded draw. Results are to be reported per repo as well as pooled.
9. The header status line of `spike/PREREGISTRATION.md` still reads DRAFT (Step A). Section 13 was filled after the run in commit 2. The pre-registration is cited at commit `f44e44a`.
10. After the run, a case-insensitive text search for the substring "name" was run over the spike outputs, intended as a username check. Its output listed repo names, topic strings and bot-author-name counters. Whether any content from the snapshot appeared in that output was not verified. A search for the real account name returned no matches. The reserve list was not opened.
