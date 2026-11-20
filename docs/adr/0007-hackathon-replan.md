# ADR 0007 — Hackathon re-plan: pause Gate 0, one build spec, 2-day Phase 1

**Status:** Accepted, 2026-11-20 (owner approved the re-plan and build spec: "Proceed with what you think would benefit this project")

## Context
The owner has a hackathon in 2 days. Under the current plan, that's out of reach:
- Gate 0 needs an estimated 20–45 hours of hand-labeling, plus a 7-day relabel gap that the tooling enforces (`spike/PREREGISTRATION.md` §14, §9.2).
- Phase 1 is estimated at 8–12 weeks (PRD §4).
- Docs 03–08 are sequenced after Gate 0 (ADR 0003).

ADR 0002 already allows a hackathon date to pin Phase 1: "unpolished features are cut. The quality bar is never cut." The owner wants a project that is both a solid hackathon entry and a portfolio piece, with real validation and real benchmark numbers.

## Decision
1. **Gate 0 is paused, not failed or abandoned.**
   - The pre-registration, sample, packets and labeling tools stay exactly as committed.
   - No K1–K4 result is claimed.
   - The site and README say the real-world feasibility spike is pending.
   - The spike can resume later and still be valid. §8.1 step 6 already allows detector work in parallel with labeling, provided it uses only synthetic fixtures and the checkout example.
2. **The product engine never reads the spike repos or the sample.** R9 still applies, so Tier A uses other sources.
3. **Docs 03–08 are replaced for Phase 1 by one document, `docs/03-build-spec.md`.**
   - It covers the contract, the integrity rules, the engine, the web app, evaluation and milestones.
   - Once the owner approves it, it authorizes product code under `engine/`, `benchmark/`, `web/` and `artifacts/`.
   - The full 03–08 set can be split out from it later.
4. **Phase 1 is pinned to the hackathon date.** It covers:
   - the engine: rules S1–S4 and B1–B3, the contract, `decide`, the report and the CLI
   - Tier A, labeled experimental, with the count-based baseline
   - one recorded run in Docker
   - the static site with the live Pyodide detector
5. **Cut, and labeled "not yet" on the site:**
   - Tiers B and C
   - the hosting measurements (§12 P/V)
   - the dormant fallback API
   - config and CI rules
   - impact analysis, Hypothesis, and Ruff/Mypy as evidence
6. **Unchanged:**
   - the thesis
   - K1–K4 and their thresholds
   - the ≥80% Tier C precision target (still a target, not a claim)
   - no hand-entered results
   - no LLM in the decision path
   - zero spend

## Consequences
- The site can claim only Tier A numbers. They are injected, experimental and come with intervals, and nothing on the site may present them as real-world performance.
- The same author writes the Tier A pattern list, the cases and the detector, which risks circularity. To mitigate it:
  - The pattern list and the frozen held-out split are committed **before** any detector code.
  - The commit order is cited on the benchmark page as evidence.
  - The limitation is stated on the site.
- Pyodide hosting hasn't been measured against §12. If the worker fails to load, the site falls back to the precomputed presets (architecture §5.3).
- Until Gate 0 resumes and an ADR records its outcome, `02-architecture.md` stays provisional.
