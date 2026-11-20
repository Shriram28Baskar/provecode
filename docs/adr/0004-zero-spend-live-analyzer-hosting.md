# ADR 0004 — Zero spend; live analyzer in the browser (Pyodide), Vercel Services as fallback

**Status:** Accepted, 2026-11-20 (decided by the project owner in the Stage 3 interview). Host choice is provisional until measured in Gate 0 (`02-architecture.md` §12).

## Context
PRD §10(b) left open where the live static analyzer runs. Research, with sources in `02-architecture.md` §11 and §15, found:
- On Vercel Hobby, a function has 4 Active-CPU hours a month, and exceeding usage limits pauses the feature for up to 30 days.
- Hobby has a single WAF rate-limit rule.
- Python's docs warn that `ast.parse` can crash the interpreter on sufficiently complex input.

A flood could therefore take a server-side live endpoint offline for a month. Cloud Run requires a billing account, and Fly.io has no free tier.

## Decision
- **Hard constraint:** the project spends no money. No paid plans and no billing accounts.
- **Primary live path:** the analyzer runs **in the reviewer's browser via Pyodide**, in a Web Worker. It is lazy-loaded when the reviewer clicks "Run it live", and the presets are precomputed so they show instantly.
- **Site:** static Next.js export on **Vercel Hobby**, with CSP and security headers set via `vercel.json`.
- **Fallback:** a thin FastAPI wrapper on **Vercel Services (Python), Hobby**. It is dormant, and activated only by ADR on the pre-registered criteria.
- **One analyzer package** (`provecode` core, stdlib only, no host-specific code) is shared by the CLI, CI, the browser and the fallback. CPython is pinned to Pyodide's Python version.
- **Pyodide is pinned to an exact version**, with SRI where supported and a CI hash lockfile. The document states exactly what isn't covered.
- **Paid-only features** (LLM explanations, the LLM-judge baseline) are optional, and dropped unless a free route exists.
- **Rejected:** Cloud Run (needs a billing account), Fly.io (no free tier), Render free (one-minute cold start; the provider says not for production), Vercel Pro (paid).

## Consequences
- The live path needs no server compute, so Hobby's function budget isn't exposed. The remaining risks are CDN request/transfer floods and the third-party CDN.
- The spike must measure Pyodide (size, init time, `ast` parity, latency, memory, worker termination) on desktop and a mid-range phone. It also measures the fallback on a real Hobby deployment.
- If both paths fail the criteria, live analysis is disabled and the site shows labeled precomputed presets only. No paid option is considered.
- A known risk: a Hobby pause of up to 30 days. Mitigated by a README demo GIF/video now and a GitHub Pages mirror later.
