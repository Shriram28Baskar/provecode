# ADR 0006 — Gate 0 sample design and stratum clarifications

**Status:** Accepted, 2026-11-20 (decided by the project owner in the Gate 0 pre-registration interview; see `spike/PREREGISTRATION.md`)

## Context
PRD §4.1 calls for a spike sample of about 200 real test-modifying commits, and defines K1–K4 against "the spike sample". If prevalence is low, a purely random sample may contain no count-preserving weakening at all. K1 also needs enough flagged units to give a meaningful precision. PRD §4.1 excludes the sampled commits from the benchmark, but says nothing about the repos they come from. If the detector is tuned on a repo's test style and later measured on the same repo, precision comes out inflated.

## Decision
- **Three sets of commits, all drawn by a seeded procedure:**
  - **Random stratum:** 200 commits. This is the primary sample.
  - **Keyword stratum:** 45 commits whose messages match a frozen, hash-locked regex. It is supplementary and always reported separately.
  - **K1 reserve:** 100 random commits, drawn in Step B by the same script as the sample, and SHA-listed in commit 2, then kept sealed. It is opened only if K1 is short: fewer than 30 non-U flagged units, or fewer than 15 distinct commits. **The reserve is the only remedy when K1 is short.** If K1 is still short after the reserve, it is reported as unevaluable. The keyword stratum never fills the gap.
- **Which stratum decides each K:**

  | K | Decided on |
  |---|---|
  | K1 | The random stratum, combined with the reserve if the reserve was triggered |
  | K2 | The random stratum |
  | K3 | The random stratum |
  | K4 | The random stratum, where "count-preserving instances" means units labeled WP |

  The keyword stratum never decides any K. K1 may use the reserve; K2, K3 and K4 never do. Reserve K3 results are descriptive only. Tuning may use the random and keyword strata, but never the reserve.
- **R9:** spike repos are excluded from **every** benchmark tier (A, B, C), not only the sampled commits. This is stricter than PRD §4.1.
- All of this was decided, **committed and pushed before Step B runs (see the commit that adds this ADR)**. `spike/PREREGISTRATION.md` is the authoritative detail: seeds, allocations, the regex and its digest, the rubric, the blinding guards and the K4 interpretation table.

## What this ADR does not change
- **The text and thresholds of K1–K4 are unchanged** (PRD §4.1, ADR 0002). This ADR only says which data each K is decided on.
- The hosting thresholds in `docs/02-architecture.md` §12 are unchanged.

## Consequences
- Labeling effort rises by about 22% (45 more commits), or by about 40% more if the reserve is opened (estimates, not measurements). The estimates are in `spike/PREREGISTRATION.md` §14.
- A zero in the random stratum can be told apart from "doesn't exist" by using the keyword stratum, without biasing any K decision.
- Future benchmark tiers must draw from repos other than the spike repos.
