# ADR 0002 — Phased scope with validation gates

**Status:** Accepted, 2026-11-20 (decided by the project owner in the PRD interview; see `docs/01-PRD.md`)

## Context
This is the owner's main portfolio project. It is built by one person, with Claude Code doing most of the implementation. Quality and rigor matter more than breadth, and there is no fixed deadline. The audit (`docs/00-competitive-audit.md`) narrowed the thesis to semantic, count-preserving test-weakening detection for Python. That thesis is only credible if the benchmark is non-circular and reproducible.

## Decision
- **Committed scope:** the core engine plus config/CI weakening rules, delivered through gates, **not dates**. Each gate ends in something demonstrable on its own:
  - **Gate 0:** feasibility spike
  - **Gate 1a:** engine, contract, CLI, JSON report, Tier A benchmark, CI
  - **Gate 1b:** recorded Docker runs and the deployed site
  - **Gate 2:** config/CI rules, impact analysis, Hypothesis, Ruff/Mypy, Tiers B and C
  - **Gate 3:** extras, not committed
- **Phase 1 exit (1a + 1b):** deployed site, Tier A results, CI green, README, and ADRs and docs current.
- **Effort and re-planning:** each gate has a rough effort estimate (PRD §4). If a gate reaches about 2× its estimate, stop and re-plan, cutting scope rather than quality, and record the outcome in an ADR. A hackathon date, if one appears, pins Phase 1 but never lowers the quality bar.
- **Gate 0 kill/pivot criteria are written before the spike runs** (PRD §4.1):
  - **K1:** blocking precision below 50%. This is measured after tuning on the same spike sample, so it is a floor for continuing, not an estimate of product precision.
  - **K2:** rules that can't be made precise are dropped; dropping at least half of them triggers a pivot.
  - **K3:** more than 5% of real test files fail to parse or align.
  - **K4:** no real count-preserving instances found, which triggers a reframing rather than a kill.
  - The fallback actions are defined in PRD §4.1. The spike sample is excluded from all benchmark tiers.
- **Target product precision**, separate from K1: blocking precision ≥ 80% on Tier C, as a point estimate with a 95% Wilson interval, on data not used for tuning. Missing it is reported as a negative result.
- **Three-tier benchmark with pre-registration:**
  - **Tier A:** injected cases, labeled experimental, at least 10 per rule, with a 30% held-out split frozen before tuning. Held-out recall is reported pooled. Per-rule recall is descriptive only and shown with its interval, because each rule has only about 3 held-out cases. Variants come from a rule-agnostic pattern list written before the rules are finalized.
  - **Tier B:** mined real weakening commits.
  - **Tier C:** a stratified precision audit. It measures precision only.
  - The labeling protocol and the repo selection criteria are written before labeling starts.
- **Detector-independent recall:** Tier B candidates and recall estimates are sourced without using our detector. Sources are commit-message keywords or other independent means, and/or a hand-labeled random sample of *unflagged* commits. The details are in `07-evaluation-plan.md`.
- **No hand-entered results.** One command regenerates every result from pinned commits and recorded environments. CI re-runs it, and the site renders only generated artifacts with provenance.

## Consequences
- The first gate after the docs is a spike, not product code. The design (`02`+) may change based on its outcome.
- Phase 1 ships without Tier B or C. The site must label Tier A as experimental and must not present it as real-world performance.
- Benchmark work (protocol, labeling, mining) is a large share of total effort and is planned as such.
- Every threshold change after this ADR requires a new ADR made before the relevant data is seen.
