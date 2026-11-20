# ADR 0008 — Self-host the pinned Pyodide runtime

**Status:** Accepted, 2026-11-20. This was an implementation decision within the owner's "proceed" mandate (ADR 0007), made on measured evidence. It amends ADR 0004's live-path loading only.

## Context
ADR 0004 and architecture §8.2 load Pyodide from jsDelivr, and list self-hosting as the alternative if integrity gaps or delivery are unacceptable.

I measured both problems on 2026-11-20, in headless Chrome on the owner's network. The test used the pinned `314.0.8` files:
- **Delivery.** With jsDelivr, the first live analysis completed once in 6.7 s and then stalled for more than 45 s on a later run. In that later run, `python_stdlib.zip` (2.5 MB) took 40 s. A direct `curl` of `pyodide.asm.wasm` (9.6 MB) timed out after 170 s, at 6.7 MB.
- **Integrity.** The loader can be checked against SRI, but the `.wasm`, the stdlib and the lock file are fetched by the loader itself, so they can't be checked in the browser.

A slow third-party CDN on venue Wi-Fi is a demo-day risk.

## Decision
- **Where the files come from.** At build time, `web/scripts/prepare.mjs` copies the runtime files from the npm package `pyodide`, which is pinned exactly to `314.0.8`. The build fails on any other version. The files are `pyodide.mjs`, `pyodide.asm.mjs`, `pyodide.asm.wasm`, `python_stdlib.zip` and `pyodide-lock.json`. They go to `/pyodide/v314.0.8/`, and their sha256 values are recorded in `/pyodide/files.json`.
- **Checking the loader.** The worker checks `pyodide.mjs` against a pinned SHA-384 before importing it. I verified that this hash is byte-identical in the npm package and on jsDelivr.
- **CSP.** The policy drops `https://cdn.jsdelivr.net` and `blob:`. Scripts and connections are `'self'` only, plus `'wasm-unsafe-eval'`.

## Evidence
- The npm and jsDelivr copies are byte-identical for `pyodide.mjs`, `pyodide.asm.mjs`, `python_stdlib.zip` and `pyodide-lock.json`.
- I couldn't compare `pyodide.asm.wasm`, because the CDN download timed out. That comparison is still open.
- Self-hosted, the first live analysis loaded in 4.1 s and ran in 15 ms. All checks in `web/scripts/e2e.mjs` passed under the production CSP.

## Consequences
- **Bandwidth.** Each first live load costs about 13.5 MB of the site's own Vercel transfer budget. The compressed size isn't measured. The runtime loads only after "Run it live" is clicked, and the precomputed presets need none of it.
- **Trust.** jsDelivr is no longer a dependency of the live path. What remains is trust in npm and our own deployment.
- **Checking at load time.** Runtime files other than the loader aren't re-checked as they load. They come from the same deployment and are hashed at build. This is stated on the How it works page.
