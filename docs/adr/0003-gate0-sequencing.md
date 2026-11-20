# ADR 0003 — Gate 0 runs after the provisional architecture, before docs 03–08

**Status:** Accepted, 2026-11-20 (decided by the project owner in the Stage 3 interview)

## Context
PRD §4.1 says the spike runs before `02-architecture.md` is locked. ADR 0002 says "the first gate after the docs is a spike". Docs 03–08, and especially `04-test-integrity-spec.md`, depend on which rules survive K1–K4. Writing them before the spike risks specifying rules the spike drops.

## Decision
- `02-architecture.md` is approved as **PROVISIONAL**. Gate 0 then runs **before** docs 03–08 are written. This replaces the "after the docs" wording of ADR 0002 **for sequencing only**.
- Spike code is allowed only in `spike/`. It is throwaway and non-product, it is not the engine, and product code never imports it.
- Before any spike code runs, `spike/PREREGISTRATION.md` is committed. It records:
  - the repository selection criteria
  - the sampling method
  - the exact commit SHAs of the ~200-commit sample
  - a spike labeling rubric
  - the blinding procedure (commits are labeled before detector output is seen)
- The hosting check is **measured on the real candidate hosts and devices**, not just researched, against the thresholds pre-registered in `02-architecture.md` §12.
- After the spike, an ADR records the outcome, and `02` is locked or revised before 03–08.

## What this ADR does not change
- **No threshold changes.** K1–K4 and their fallback actions stay exactly as written in PRD §4.1 and ADR 0002.
- Spike-sample exclusion from all benchmark tiers stays as is.

## Consequences
- 03–08 are written with spike evidence in hand.
- `CLAUDE.md` gains a rule that permits throwaway code in `spike/` only. All other application code still waits for doc approval.
