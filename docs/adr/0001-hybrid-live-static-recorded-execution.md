# ADR 0001 — Hybrid demo: live static analysis, recorded execution runs

**Status:** Accepted (decided by project owner, 2026-11-20)

## Context
The primer left open whether the website runs verification live on submitted changes or presents recorded runs (primer §6.4). Running untrusted repository code live needs sandboxed execution infrastructure, which Vercel does not provide. The competitive audit (`docs/00-competitive-audit.md`) narrowed the project's core to semantic test-weakening detection. That analysis is static (AST comparison) and does not need to execute code.

## Decision
- **Live:** the test-weakening detector runs live on the site. It performs static analysis only (parsing and comparison), and **never executes submitted code**.
- **Recorded:** execution-based evidence (contract-owned pytest checks, repo test runs, Hypothesis properties, Ruff/Mypy) comes from real engine runs on prepared scenarios. The site presents these recorded runs.
- Every recorded result is **clearly labeled as recorded** and links to its real report artifact, with SHAs, contract hash, tool versions and timestamps.

## Consequences
- The live demo needs no Docker sandbox in hosting, which removes the largest infrastructure risk.
- The live path still handles untrusted input. The architecture must define:
  - input size limits
  - parse timeouts
  - protection against pathological input, since deeply nested code can exhaust Python's parser recursion
  - a guarantee that no import or eval of submitted code happens
- The UI must make the live/recorded distinction visually explicit, so a recorded result is never presented as a live one.
- Verdicts on live submissions can only rely on static evidence. Conditions that need execution must show as gaps, giving **INCONCLUSIVE** rather than VERIFIED.
- Docker isolation is still needed locally or in CI, to produce the recorded runs.
