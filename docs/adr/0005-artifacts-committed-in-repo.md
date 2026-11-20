# ADR 0005 — Generated artifacts committed in the repo; bulky raw data in GitHub Releases

**Status:** Accepted, 2026-11-20 (decided by the project owner in the Stage 3 interview)

## Context
The site renders only generated artifacts with provenance (PRD §5.4), and there is no database in Phase 1. Recorded runs and benchmark results need a durable, reviewable home.

## Decision
- **Committed:** report JSON, benchmark result JSON, Markdown summaries and per-run `manifest.json` files, under `artifacts/`.
- **Not committed:** bulky raw logs. These go into GitHub Release bundles, and the repo keeps a sha256 and a summary for each.
- **Size bound:** `artifacts/` ≤ 50 MB total and ≤ 1 MB per file. Exceeding either moves the data to a Release.
- **Licensing:** no mined third-party code beyond the minimal excerpts and SHAs allowed by the PRD.
- **Generation:** a GitHub Actions workflow, from **pinned commits only**, opens an artifact PR.
- **Review:** artifact PRs require branch protection and owner review. The manifest and hash check runs **on the PR itself**.
- The site build fails if a rendered number doesn't resolve to a manifest entry.

## Consequences
- Git history is part of the provenance, and every result change is a reviewable diff.
- Branch protection must be configured on GitHub. This changes repo settings, so I'll ask before doing it.
- The spike's outputs stay in `spike/` and never enter `artifacts/`.
