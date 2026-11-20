\# PROVECODE



Read docs/source/PROVECODE\_PROJECT\_PRIMER.md before doing anything.



\## Rules

\- Work in gated stages. Do not write application code until I approve the design docs.
- Exception (ADR 0003): Gate 0 spike code may be written only in `spike/`. It is throwaway, non-product, not the engine, and must never be imported by product code. Commit `spike/PREREGISTRATION.md` before running any spike code.

\- Ask one question at a time, with your recommended default.

\- Never fabricate results, citations, coverage numbers, or competitor features.

\- If you can't verify something, say so.

\- Keep deterministic components and LLM-assisted components separate.

\- Record major decisions as short ADRs in docs/adr/.

\- After finishing each document, stop and wait for my approval.

\- Never push, force-push, rename branches, or change remotes without asking me first.

