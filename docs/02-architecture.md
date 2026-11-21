# 02 — Architecture

**Stage:** 3 (architecture)
**Date:** 2026-11-20
**Status:** **PROVISIONAL.** Not locked. The Gate 0 feasibility spike must run first (PRD §4.1, ADR 0003). Any item tagged **[G0]** may change based on spike measurements. The document is locked, or revised, by an ADR after the spike report.
**Inputs:** `docs/01-PRD.md` (approved), `docs/00-competitive-audit.md` (approved), ADRs 0001–0005, primer §§4–7, 11.

All platform facts are **as of November 2026** and cite a page fetched on 2026-11-20 (§15). "Not verified" means I could not confirm the fact from a primary source. Nothing in this document is a measured result. Every number is a **proposal**, a **limit** or a **pre-registered threshold**.

---

## 1. Decisions this document rests on

| Decision | Source |
|---|---|
| Live = static analysis only; execution evidence comes from recorded runs | ADR 0001 |
| Gated scope; K1–K4 written before the spike | ADR 0002 |
| Gate 0 runs right after this doc is approved as provisional, before 03–08 | ADR 0003 |
| **Zero spend:** no paid plans and no billing accounts. The live analyzer runs **in the browser (Pyodide)**, the site is a **static export on Vercel Hobby**, and **Vercel Services (Python) on Hobby** is the fallback | ADR 0004 |
| Generated artifacts committed in the repo; bulky raw data in GitHub Releases | ADR 0005 |

**Refinements to the PRD (no PRD text changed):**
- **PRD §10(b)** is answered by §11 below.
- **PRD §4.4 / §6.3(4):** LLM-written explanations and the LLM-judge baseline are **optional, and dropped unless a free route exists** (ADR 0004). They were already uncommitted Phase 3 items.

---

## 2. System overview

Three parts share one analyzer package.

```
                         ┌───────────────────────────────────────────┐
                         │  engine/  Python package `provecode`      │
                         │  ┌────────────────────────────────────┐   │
                         │  │ provecode.analyzer  (CORE)          │   │
                         │  │ stdlib only · pure · no I/O         │◄──┼── used by all three
                         │  └────────────────────────────────────┘   │
                         │  contract · decide · report · git · exec  │
                         └──────┬───────────────┬───────────────┬────┘
                                │               │               │
          ┌─────────────────────▼──┐  ┌─────────▼──────────┐  ┌─▼─────────────────────────┐
          │ (1) CLI                │  │ (2) Web (static)   │  │ (3) Recorded-run +         │
          │ provecode check <b> <h>│  │ Next.js export on  │  │ benchmark pipeline         │
          │ dev machine / CI       │  │ Vercel Hobby       │  │ GitHub Actions + Docker    │
          └────────────────────────┘  │ live: Pyodide in a │  │ → artifacts/ (PR, reviewed)│
                                      │ Web Worker         │  └─────────────┬──────────────┘
                                      └─────────▲──────────┘                │
                                                └──── build-time read ──────┘
```

**Deterministic vs LLM-assisted.** Phase 1 has **no LLM component**. Everything in the diagram is deterministic. If an LLM feature is ever added (Gate 3, and only via a free route), it lives in a **separate package** (`provecode_llm`). It may read reports but is never imported by `provecode`. A CI check enforces this: an import scan over `engine/src/provecode` fails if it finds `provecode_llm` or any LLM SDK. The decision path never consumes LLM output (primer §7, PRD §7).

---

## 3. Repository layout (monorepo)

| Path | Contents | Product code? |
|---|---|---|
| `engine/` | Python package `provecode` (`src/` layout) and its tests | Yes |
| `web/` | Next.js app, `output: 'export'`; Pyodide worker in `web/worker/` | Yes |
| `api/` | Fallback FastAPI wrapper (§10.2), **dormant** unless the fallback is activated | Yes (dormant) |
| `benchmark/` | Scenario definitions, the count-based baseline reimplementation, the regenerate command | Yes |
| `artifacts/` | Generated reports, benchmark results, manifests (ADR 0005). **Never hand-edited.** | Generated |
| `docker/` | Recorded-run image definition | Yes |
| `spike/` | Gate 0 code. **Throwaway, non-product, not the engine.** Never imported by product code | No |
| `docs/` | Design docs and ADRs | — |

---

## 4. Engine

### 4.1 Modules

| Module | Responsibility | Runs where | Dependencies |
|---|---|---|---|
| `provecode.analyzer` | **Core.** Base/head test alignment, rules S1–S5 and B1–B4 (S5, B4 from ADR 0011), `Finding` records with before/after lines | CPython (CLI, CI, fallback) **and Pyodide** | **stdlib only** (`ast`, `tokenize`, `dataclasses`, `json`, `hashlib`) |
| `provecode.limits` | Input guards: size, line count, tokenize-level nesting pre-scan (§7.3). Called before any `ast.parse` | Everywhere the core runs | stdlib only |
| `provecode.contract` | Load and validate YAML contracts, compute content hash, link findings to conditions, assign severity. Also **compiles approved contracts to canonical JSON** for the browser | Loading YAML: CLI/CI. Linking and severity over JSON: everywhere | YAML/validation library in the CLI extra only (choice in 05); the JSON path is stdlib only |
| `provecode.decide` | Pure, deterministic outcome over (contract, evidence, findings, gaps) | Everywhere | stdlib only |
| `provecode.report` | Versioned JSON report (canonical) and Markdown | Everywhere | stdlib only |
| `provecode.git` | Resolve refs to SHAs, list changed files, read base/head blobs **without** checking out or modifying the user's repo | CLI/CI only | `git` subprocess |
| `provecode.exec` | Recorded runs: contract-owned pytest checks and repo tests in Docker (Gate 1b) | CI/dev machine only | Docker |
| `provecode.cli` | `provecode check <base> <head> --contract …` | CLI | — |

**Host independence.** The core modules (`analyzer`, `limits`, `decide`, `report`, and the JSON path of `contract`) contain **no host-specific code**: no Vercel, Pyodide or FastAPI imports. The browser worker and the fallback API are thin wrappers that pass strings in and receive a report dict out. Moving hosts changes only the wrapper.

**Python version pin.** The CLI, CI and the fallback pin CPython to **the same minor and patch version as the pinned Pyodide release**, so `ast` output is identical across hosts. Pyodide 314.0.0 "Upgraded to Python 3.14.2". I did not confirm the exact version bundled in 314.0.8 (§14). Vercel's Python runtime offers 3.12, 3.13 and 3.14. **[G0]** Parity is tested (§12, P1).

### 4.2 CLI data flow

```
refs ─► git: SHAs, changed files, base/head blobs
          │
          ▼
       limits (per file) ──reject──► Gap(reason=input_limit)   (never silently passed)
          │
          ▼
       analyzer: parse base+head ─► align tests ─► rules S1–S5, B1–B4 ─► Findings
          │                               └─unalignable/unsupported─► Gap
          ▼
       contract: load, verify hash/approval ─► link findings → conditions ─► severity
          │
          ▼
       decide ─► outcome + reasons (condition / finding / evidence IDs)
          │
          ▼
       report: JSON (canonical) + Markdown, with provenance
```

Interfaces, the report JSON schema, and how static-only live results map to the three outcomes (PRD §10a) are specified in `05-engine-design.md`. Contract hashing and protection (PRD §10c) are in `03-contract-spec.md`.

---

## 5. Web app

### 5.1 Pages and data

- **Pages:** landing, live detector, recorded report view, benchmark, about / limitations / prior art (PRD §3).
- **Static export:** `next build` with `output: 'export'` emits HTML/CSS/JS. Server Components run **at build time** (Next.js static-export docs). Request-dependent features are unsupported (no rewrites, headers, Server Actions or request-reading Route Handlers in Next.js itself). Security headers are set by `vercel.json` instead (§8.1).
- **Artifacts** are read from `artifacts/` at build time and rendered statically (§6.4).

### 5.2 Live detector (primary path: in-browser)

1. **Instant presets.** The checkout weakening preset (contract v1) and the legitimate-change preset (contract v2) render **immediately** from results **precomputed at build time** by the same analyzer. They are labeled **PRECOMPUTED**.
2. **Lazy load.** Only when the reviewer clicks **Run it live** (or edits a test) does the page start a dedicated **Web Worker**. The worker loads the pinned Pyodide runtime from jsDelivr and then our pure-Python `provecode` wheel from our own origin. The loading mechanism (for example `micropip` or unpacking the archive) is **[G0]**.
3. **Analysis.** The main thread sends `{base_test, head_test, preset_id}` to the worker. The worker runs `limits`, then `analyzer`, then `contract` linking against the **fixed, bundled contract JSON** for that preset, then `decide`, and returns the report dict. Results are labeled **LIVE**.
4. **Timeout.** The main thread starts a wall-clock timer per request (§7.3). On expiry it calls `worker.terminate()`. MDN says this "immediately terminates the Worker. This does not offer the worker an opportunity to finish its operations". The page reports `timeout` as a gap and spawns a fresh worker on the next request.
5. **Contracts are not editable live.** Users can edit only the test text. The contract and its hash come from the build. This is the first approach listed in PRD §10(c).
6. **No URL-encoded user input in Phase 1.** No shareable links carry submitted code, so nobody can craft a link that runs content in another reviewer's session.

### 5.3 Result labels

Every result carries exactly one of these labels, each with a distinct visual treatment (design in 06):

| Label | Meaning |
|---|---|
| **LIVE** | Computed just now in this browser, static analysis only |
| **PRECOMPUTED** | Same analyzer, computed at build time from a fixed preset (shows build SHA) |
| **RECORDED** | A real engine run with execution evidence, from `artifacts/` (shows full provenance) |

If the live worker can't load (offline, CDN failure, unsupported browser), the page says **"Live analysis unavailable"** and keeps the PRECOMPUTED presets. It never presents those results as live.

---

## 6. Recorded-run and benchmark pipeline

### 6.1 Runner

- **GitHub Actions**, standard GitHub-hosted runners. GitHub's billing page says: "The use of standard GitHub-hosted runners is free: In public repositories." **This assumes the repo is public** (§14). On a private repo, the Free plan includes $12 of compute a month instead.
- Limits: "Each job in a workflow can run for up to 6 hours of execution time", 20 concurrent jobs on Free, and 500 MB of artifact storage on Free (shared with Packages). We don't rely on Actions artifact storage for persistence (§6.3).

### 6.2 Flow

```
workflow_dispatch / schedule (manual by default)
   │  inputs: pinned scenario manifest (repo URLs + exact SHAs, contract hashes)
   ▼
build or pull docker image ──► pin by digest
   ▼
for each scenario:
   provecode check (static)            ─┐
   provecode exec (Docker, no network,  ├─► report.json + raw logs
     CPU/mem/time limits, ro mounts)   ─┘
   ▼
benchmark regenerate (one command): PROVECODE + baselines on identical inputs
   ▼
write artifacts/<kind>/<id>/…  +  manifest.json
   ▼
open PR "artifacts: <run id>" from a bot branch   ──► owner review required
```

- **Pinned commits only.** The workflow refuses to run a scenario whose ref isn't a full 40-character SHA.
- **One command** (`benchmark regenerate`) produces every number, both locally and in CI (PRD §5.4).

### 6.3 Artifact storage (ADR 0005)

- **Committed:** report JSON, benchmark result JSON, Markdown summaries, and `manifest.json` per run.
- **Not committed:** bulky raw logs. These go into a **GitHub Release** bundle. The repo keeps a sha256 and a short summary for each one.
- **Manifest fields:**
  - `run_id`
  - CI run URL
  - workflow file SHA
  - engine version and commit
  - Docker image digest
  - Python, pytest and tool versions
  - every scenario's base/head SHAs
  - contract IDs, versions and hashes
  - timestamps
  - per-file `{path, sha256, bytes}`
  - Release asset URLs and their sha256
- **Size bound (proposed):** `artifacts/` stays **≤ 50 MB in total** and **≤ 1 MB per file**. A file over 1 MB, or a run that would push the total past 50 MB, goes to a Release bundle instead. For context, GitHub warns on files "larger than 50 MiB", blocks files "larger than 100 MiB", and recommends repositories "ideally less than 1 GB". Our bound is far below all three. Release assets have "no total size limit", though each file is capped by the plan's LFS file-size limit, which the page doesn't state.
- **Licensing:** permissive-license repos only (PRD §9). Artifacts store SHAs and the **minimal excerpts** a finding needs (the before/after lines). No vendored third-party source.

### 6.4 Provenance checks

| Check | Where | Fails when |
|---|---|---|
| Manifest integrity | Required status check **on the artifact PR itself** | Any file's sha256 or size differs from the manifest, a Release asset hash mismatches, or a ref isn't a full SHA |
| No hand-edits | Same PR check | A change under `artifacts/` is not described by a manifest in the same PR, or the PR author isn't the workflow bot |
| Determinism | Same PR check | Re-running static analysis on a sample of scenarios yields different findings (timestamps excluded) |
| Render provenance | `web` build | Any benchmark or report component is given a literal number rather than an artifact reference. Components accept only `{artifact_path, json_pointer}`, and the build resolves each one against a manifest entry |

**Branch protection on `main`:** artifact PRs need owner review, and the checks above are required. Setting this up touches repo settings, so I'll ask before changing it (CLAUDE.md).

### 6.5 Rendering

The benchmark page and the report view read only `artifacts/`. Each number shows, or links to, its provenance: run ID, CI run, SHAs, image digest, and contract hash. Tier A is labeled **experimental / injected** (ADR 0002).

---

## 7. Trust boundaries and threat model

### 7.1 Boundaries

```
[Reviewer browser]──────────── untrusted text ───────────►[Web Worker: Pyodide + provecode]
       │  ▲                                                        ▲
       │  └──── static HTML/JS (our origin, Vercel) ───────────────┤
       │                                                           │
       └──── Pyodide runtime (jsDelivr, THIRD PARTY) ──────────────┘

[GitHub Actions runner]──► [Docker container: candidate repo code = UNTRUSTED, no network]
       │
       └──► artifact PR ──► owner review ──► main ──► Vercel build

[Fallback only] Browser ──► Vercel WAF ──► Vercel Function (Python) ──► child process (parse)
```

### 7.2 Threats

| # | Asset / boundary | Threat | Mitigation | Residual |
|---|---|---|---|---|
| T1 | Live worker | Pathological input exhausts the stack, memory or CPU. Python's docs: `ast.parse` can "crash the Python interpreter with a sufficiently large/complex string due to stack depth limitations" | `limits` pre-scan (§7.3); worker isolation; wall timeout then `terminate()`; fresh worker per timeout | Only the submitter's own tab is affected. Measured in Gate 0 (P4, P6) |
| T2 | Live worker | Submitted code is imported or executed | Core never calls `eval`, `exec`, `compile` (except `ast.parse`), `__import__` or `importlib`, and never runs `ast.literal_eval` on input. Enforced by the banned-API test (§7.4) | Pyodide runs in the browser sandbox either way |
| T3 | Rendered findings | XSS through before/after snippets or file names | Render as React text nodes only. No `dangerouslySetInnerHTML`; lint rule bans it. Strict CSP (§8.1) | — |
| T4 | Third-party CDN | Compromised or altered Pyodide files | Exact version pin; SRI on the loader script; CI hash lockfile (§8.2) | Files fetched by the loader are **not SRI-covered** in the browser (§8.2). **[G0]** |
| T5 | Static host | Flood exhausts Hobby limits, so Vercel pauses the project for up to 30 days | §9 | Distributed floods can't be stopped on Hobby. Mitigated by the paused-site plan (§9.4) |
| T6 | Recorded runs | Candidate repo code is malicious (exfiltration, CI tampering) | Docker with `--network none`, CPU/memory/pids/time limits, read-only source mount, separate working copy, non-root user, no secrets in the job's environment, `permissions: contents: read` on the execution job. A separate job with write scope only opens the PR | Docker isn't a complete security boundary (primer §11.7). The runner is ephemeral |
| T7 | Artifacts | Fabricated or altered results reach the site | Bot-authored PRs only, manifest hash check on the PR, owner review, branch protection, render-provenance build check (§6.4) | Owner review is the last line |
| T8 | Contract (live) | User edits the contract to make a weakening pass | Contracts are bundled at build time and are read-only in the UI. The hash is shown | — |
| T9 | Contract (CLI/CI) | Candidate change edits its own contract | Specified in `03-contract-spec.md` (PRD §10c) | Open |
| T10 | Fallback endpoint | Abuse, crash affecting concurrent requests (Vercel: "concurrent requests can share an instance") | §7.3 child process, §9.3 | Measured in Gate 0 (V1–V6) |

### 7.3 Input limits and isolation

These are **safety parameters**, not success criteria. They may be tuned in Gate 0 from measurements and recorded in the spike report. **[G0]**

| Limit | Proposed value | Enforced by |
|---|---|---|
| Bytes per side (base test, head test) | ≤ 64 KiB (UTF-8) | `limits` (core), also the UI textarea |
| Lines per side | ≤ 2,000 | `limits` |
| Bracket nesting depth (`(` `[` `{`) | ≤ 100 | `limits` pre-scan using `tokenize`, **before** `ast.parse` |
| Indentation depth | ≤ 50 levels | `limits` pre-scan |
| Wall-clock per analysis | 2 s | Browser: main-thread timer then `worker.terminate()`. Fallback: child-process timeout |
| Memory (fallback only) | 256 MB address space | `RLIMIT_AS` on the child process **[G0: support unverified on Vercel]** |
| CPU (fallback only) | 2 s | `RLIMIT_CPU` on the child process **[G0]** |
| Request body (fallback only) | ≤ 160 KiB (2 × 64 KiB plus envelope) | API wrapper. Vercel's own limit is 4.5 MB |

Over-limit input produces a **gap** (`input_limit`), never a pass. The pre-scan limits the depth `ast.parse` will face. They don't prove `ast.parse` safe, which is why isolation is still required.

### 7.4 "No import, eval or execution" guarantee

- **Design:** the core receives source text as `str` and only ever calls `ast.parse` and `tokenize` on it.
- **Enforcement:** a CI test parses every file under `engine/src/provecode/{analyzer,limits,decide,report,contract}` and fails on:
  - calls to `eval`, `exec` or `__import__`
  - `compile` (other than via `ast.parse`)
  - `importlib`, `runpy` or `pickle`
  - `ast.literal_eval`
  - `subprocess`, `os.system` or `socket`
- **Tests:** malicious-looking inputs (for example `__import__('os').system(...)` inside a test body) are analyzed and must produce findings with no side effects.

---

## 8. Browser security and supply chain

### 8.1 Content Security Policy

CSP is set as a **response header via `vercel.json` `headers`**. Vercel says it "configures custom response headers for static files, Vercel functions, and a wildcard that matches all routes". The exact value is **[G0]**, because Pyodide's needs in a worker must be measured.

| Directive | Proposed value | Reason |
|---|---|---|
| `default-src` | `'self'` | Deny by default |
| `script-src` | `'self' https://cdn.jsdelivr.net 'wasm-unsafe-eval'` | Our bundle; the pinned Pyodide loader; WebAssembly compilation. **[G0]** whether Pyodide also needs `'unsafe-eval'`. If it does, that is allowed only in the worker's own CSP, never on pages |
| `worker-src` | `'self'` | Worker script is served from our origin. **[G0]** add `blob:` only if the loader requires it |
| `connect-src` | `'self' https://cdn.jsdelivr.net` | Fetching the Pyodide wasm/stdlib and our wheel |
| `style-src` | `'self'` | No inline styles. **[G0]** may need hashes for framework-injected styles |
| `img-src` | `'self' data:` | Static images, inline icons |
| `object-src` | `'none'` | No plugins |
| `base-uri` | `'none'` | Prevents base-tag hijacking of relative URLs |
| `form-action` | `'none'` | The site has no forms that submit anywhere |
| `frame-ancestors` | `'none'` | Prevents clickjacking. Header-only directive, which is why a static host with real headers was chosen |
| `upgrade-insecure-requests` | (set) | Defense in depth |

Additional headers: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy` denying unused features. COOP/COEP are **not** needed, because we use `terminate()` rather than a SharedArrayBuffer interrupt. **[G0]** to confirm.

### 8.2 Pyodide pinning and integrity

- **Pin:** an exact version, in a config file the worker and CI read. The latest seen is **314.0.8 (2026-11-13)**, with CDN index `https://cdn.jsdelivr.net/pyodide/v314.0.8/full/`. The pinned version is chosen in the spike.
- **What SRI covers:** only the **loader script** that our worker loads, given its `integrity` hash. **Not covered in the browser:** the `.wasm`, the stdlib zip and the lock file, which the loader fetches itself. The spike checks whether the loader accepts integrity options or a self-verifying fetch hook (**[G0]**). If it doesn't, this gap is stated on the limitations page.
- **CI check (feasible regardless):** a scheduled and per-PR job downloads every pinned Pyodide file we load and compares it to a committed `web/pyodide.lock.sha256`. A mismatch fails CI. This detects CDN drift but **does not protect a reviewer at request time**.
- **Our wheel** is served from our own origin. Its sha256 goes in the build manifest, and the worker verifies it before installing.
- **Alternative if [G0] shows unacceptable integrity gaps:** self-host the pinned Pyodide files on Vercel with full hash control. The cost is the transfer budget (§9.1), so this is not the default.

---

## 9. Abuse protection without a database

### 9.1 Verified Hobby limits (Vercel docs, November 2026)

| Resource | Hobby | Source |
|---|---|---|
| CDN requests | First 1,000,000 / month | Hobby plan page |
| Fast Data Transfer | First 100 GB / month | Hobby plan page |
| Active CPU (functions) | 4 CPU-hrs / month | Hobby plan page |
| Function invocations | First 1,000,000 / month | Hobby plan page |
| Exceeding limits | "you will have to wait until 30 days have passed before you can use the feature again" | Hobby plan page |
| WAF rate limiting | "available on all plans". Hobby: **1 rule** (within 3 custom rules), fixed window, keys IP and JA4 digest, window 10 s – 10 min, 1,000,000 allowed requests included. "Rate limit counters are tracked on a per-region basis" | WAF rate-limiting page |
| DDoS mitigation | On by default | Hobby plan page |
| Usage terms | "You shall only use the Services under a Hobby plan for your personal or non-commercial use." "Commercial" isn't defined in the terms. A non-commercial portfolio project appears to fit; this is my reading, not legal advice | Vercel Terms §4 |

### 9.2 Primary path (Pyodide)

- **No server compute** for live analysis, so the function CPU budget isn't exposed at all.
- **Pyodide's megabytes come from jsDelivr, not our origin,** so they don't count against our 100 GB. Our own payload per visit is the static site plus our wheel, measured in Gate 0.
- **The single WAF rule** is site-wide, per IP, fixed window, with a generous threshold. Proposed: 300 requests / 60 s, action 429. That is well above a human browsing the site and below a single-IP scraper. **[G0]**: the spike confirms the rule actually triggers on Hobby (V5), and **checks whether rate-limited requests still count toward CDN requests. I couldn't find this in the docs (not verified).**
- **Honest limits:**
  - A per-IP fixed window doesn't stop a distributed flood.
  - Hobby's maximum window is 10 minutes, so **daily per-IP quotas are not possible without state**, and we add no database.
  - Counters are per region.

### 9.3 Fallback path (Vercel Services), if activated

- The one WAF rule is **retargeted** to `Request Path = /api/analyze`. Proposed: 20 requests / 60 s per IP. Static pages then rely on default DDoS mitigation only.
- **In-function guards:** body-size check, then `limits`, then parse in a **child process** with `RLIMIT_AS`, `RLIMIT_CPU` and a wall timeout (§7.3). A crash kills only the child, which matters because instances may serve concurrent requests (V2).
- **Per-instance limiter:** a small in-memory token bucket per IP. It is **best effort and not global** (instances don't share state), and documented as such.
- **Budget arithmetic:** 4 CPU-hrs = 14,400 CPU-s a month. With measured worst-case CPU per accepted request *c*, an attacker needs 14,400 / *c* requests to trigger the pause. V6 (§12) requires that to be at least 50,000, which means *c* ≤ 0.288 s. The pre-scan is meant to reject expensive inputs cheaply. This is measured, not assumed.

### 9.4 Site-paused plan (known risk)

**Risk:** a flood that exhausts Hobby's 1M CDN requests or 100 GB transfer pauses the site for up to 30 days. Mitigations:
1. **Phase 1:** the README contains a **demo GIF/video** of the full PRD §3 story and links to the committed artifacts, so the work stays inspectable while the site is paused.
2. **Phase 1:** precomputed presets mean the demo needs no live compute, so if only the live path fails, the story still works.
3. **Later hardening (not Phase 1):** a GitHub Pages mirror of the same static export. GitHub Pages has a "*soft* bandwidth limit of 100 GB per month", can't set custom headers (CSP would degrade to `<meta>`, with no `frame-ancestors`), and "may stop serving" if quotas are exceeded. The mirror would carry a banner naming the canonical URL.

---

## 10. Deployment

### 10.1 Primary (Phase 1)

| Item | Setting | Pinned in |
|---|---|---|
| Host | Vercel **Hobby**, one project, Git-connected, production = `main` | `vercel.json` |
| Build | `next build` with `output: 'export'`; build step also produces the `provecode` wheel and the precomputed preset results | `web/next.config.*`, `web/package.json` |
| Node / Next.js | exact versions **[G0: chosen at spike]** | `web/package.json`, `.nvmrc` / `engines` |
| Python (CLI/CI) | CPython == Pyodide's bundled version **[G0]** | `engine/pyproject.toml` `requires-python`, `.python-version` |
| Pyodide | exact version (candidate 314.0.8) | `web/pyodide.config.json`, `web/pyodide.lock.sha256` |
| Headers | CSP and security headers (§8.1) | `vercel.json` `headers` |
| WAF | 1 rate-limit rule (§9.2) | Dashboard. The rule is **documented in repo** as `docs/ops/waf-rule.md`, since Hobby dashboard config isn't in git |
| Preview deploys | Per PR (Vercel default) | — |

### 10.2 Fallback (dormant; activated only by ADR on the §12 criteria)

```json
{
  "services": {
    "web": { "root": "web/" },
    "analyzer": { "root": "api/", "entrypoint": "main:app" }
  },
  "rewrites": [
    { "source": "/api/(.*)", "destination": { "service": "analyzer" } },
    { "source": "/(.*)",     "destination": { "service": "web" } }
  ]
}
```

- This is a sketch following Vercel's Services quick-start format. **Services is Beta** ("Services are available in Beta on all plans"), which is a platform risk: configuration and behavior may change. The config is pinned in the repo, and the Gate 0 measurement records the date.
- Services backends run as Vercel Functions on Hobby: 2 GB / 1 vCPU, 300 s max duration, 4.5 MB request body limit, 500 MB Python bundle limit.
- `api/main.py` is a thin FastAPI wrapper around `provecode` core with no logic of its own. Moving to another host later means changing only the wrapper (ADR 0004).
- With a static export in `web/`, the fallback route is the only server code. The spike confirms that the export and Services coexist (**[G0]**).

---

## 11. Hosting research: open item (b)

Constraints: static analysis only, input limits and parse timeouts, no execution, stateless, **zero spend with no billing account** (ADR 0004).

| Option | Facts (cited, Nov 2026) | Fits zero spend? | Trade-offs |
|---|---|---|---|
| **A. Pyodide in browser** (Web Worker) on a static Vercel Hobby site | CPython compiled to WebAssembly. Some stdlib modules are removed (e.g. `curses`, `tkinter`, `venv`, `resource`), and the list doesn't include `ast`. The full distribution is "200+ megabytes". **Core download size and `ast` behavior are not stated, so not verified.** Worker `terminate()` stops immediately (MDN) | **Yes** | **+** No server compute, so the CPU budget and parser DoS can't hit shared infrastructure; submitted code never leaves the browser. **−** Download and initialization cost on first live use; latency varies by device; relies on a third-party CDN; SRI covers the loader only (§8.2) |
| **B. Vercel Services, Python Function** (Hobby) | Python GA; 3.12 (default), 3.13, 3.14; FastAPI supported. Services: Beta, all plans. Hobby: 2 GB/1 vCPU, 300 s, 4.5 MB body, 4 CPU-h and 1M invocations a month, 30-day pause on exceeding. WAF: 1 rule on Hobby | **Yes** | **+** Same project and domain; real CPython; server-side limits. **−** Flood can pause the live endpoint for up to 30 days; Services is Beta; child-process and rlimit support not verified; shared-instance concurrency |
| C. Google Cloud Run | Free tier 2M requests, 180,000 vCPU-s, 360,000 GiB-s a month (request-based billing), aggregated per billing account. **A billing account is required.** That came from a third-party answer about the page, cross-checked against Google's Free Tier page summary in search results; I did not read the Google page directly | **No** (billing account) | Excluded by ADR 0004 |
| D. Fly.io | "New organizations don't have a free tier or a monthly free usage allowance." Smallest machine $2.19/month | **No** | Excluded |
| E. Render free web service | Spins down after 15 minutes idle; spin-up "takes about one minute"; 750 instance-hours a month; "Do not use them for production applications." | Yes, but the provider says not for production | Excluded: a one-minute cold start breaks the 30-second / 5-minute reviewer story (PRD §2) |

**Recommendation (accepted in ADR 0004): A as primary, B as fallback.** A removes the abuse surface that makes B fragile on Hobby, and B is zero cost if A fails the pre-registered criteria. Both are **[G0]** until measured.

---

## 12. Pre-registered Gate 0 hosting criteria

These values are **fixed when this document is approved**, before any measurement. They may be changed only by an ADR made **before** measuring (same rule as PRD §4.1). They are separate from K1–K4, which are unchanged.

**Devices.** Desktop: the owner's machine, with the model and browser recorded. Phone: a **physical mid-range Android phone**, with the model, OS and browser recorded. If none is available, the phone measurements are made on an emulated profile (DevTools CPU throttle 4× plus a named network-throttling preset), and every phone result is flagged **"not measured on real hardware"**. Network for cold-load tests: the browser's named throttling preset (recorded) with the cache disabled. Each timed metric uses **5 runs** and reports the median, plus p95 where stated.

### 12.1 Switch from Pyodide (primary) to the fallback if **any** of these holds

| ID | Criterion | Threshold |
|---|---|---|
| P1 | Analyzer output differs between Pyodide and pinned CPython on the spike corpus plus all engine fixtures (findings JSON, timestamps excluded) | **Any** mismatch that pinning can't resolve |
| P2 | Cold load-to-ready (click "Run it live" until the worker is ready), median of 5 | Phone **> 12 s**, or desktop **> 5 s** |
| P3 | Total bytes over the wire to make the worker ready (Pyodide plus our wheel) | **> 15 MB** |
| P4 | `terminate()` fails to stop a pathological-input parse | Worker still consuming CPU **> 1 s** after the timeout fires, on either device |
| P5 | Warm analysis latency for max-size in-limit input, p95 of 20 | Phone **> 1 s** |
| P6 | Tab or worker crashes from memory on any in-limit input | **Any** occurrence |
| P7 | `ast` or `tokenize` unavailable or broken in Pyodide | **Any** |

### 12.2 The fallback is viable only if **all** of these hold

| ID | Criterion | Threshold |
|---|---|---|
| V1 | A child process with a wall timeout plus memory and CPU limits works on a Vercel Python Function (Hobby) | Works, with the limits observed to take effect |
| V2 | A crashing or limit-killed parse doesn't fail other in-flight requests | 0 collateral failures in 20 concurrent mixed requests |
| V3 | Cold start (first request after ≥ 15 min idle) and warm latency for max-size input | Cold p50 **≤ 3 s**; warm p95 **≤ 1 s** |
| V4 | Max request body actually accepted | **≥ 160 KiB** |
| V5 | The single Hobby WAF rate-limit rule returns 429 when exceeded | Observed |
| V6 | Measured worst-case CPU per accepted request *c* | 14,400 / *c* **≥ 50,000** requests a month |

### 12.3 Outcomes

- **P all pass:** keep Pyodide as primary. The fallback stays dormant but measured.
- **Any P fails and all V pass:** activate the fallback by ADR.
- **Any P fails and any V fails:** **disable live analysis**. The site shows PRECOMPUTED presets only, clearly labeled, and the limitation is stated. Record by ADR and re-plan per ADR 0002. A paid option is not considered (ADR 0004).

---

## 13. Gate 0 sequencing (ADR 0003)

1. **Approve this document as PROVISIONAL**, together with ADRs 0003–0005 and the `CLAUDE.md` spike rule.
2. **Pre-registration, committed before any spike code runs:** `spike/PREREGISTRATION.md` containing:
   - repository selection criteria (stars, pytest usage, permissive license, activity)
   - the sampling method for about 200 test-modifying commits
   - **the exact commit SHAs of the sample**
   - the spike **labeling rubric**
   - the **blinding procedure**: commits are hand-labeled **before** the detector is run on them, and detector output is stored where it isn't viewed until labeling of that commit is final
   - verbatim copies of K1–K4 (PRD §4.1, unchanged) and §12 P/V thresholds
   - The commit hash of this pre-registration is cited in the spike report.
3. **Rule-feasibility spike** (K1–K4) in `spike/`. Throwaway code. The sample stays excluded from every benchmark tier.
4. **Hosting spike, measured on the real hosts and devices** (§12):
   - Pyodide on desktop and phone
   - the Vercel Services fallback on a Hobby deployment (V1–V6), so the fallback's viability is known rather than assumed
   - CSP, SRI and integrity behavior (§8)
   - whether rate-limited requests count toward CDN usage (§9.2)
5. **Spike report** with measured values against K1–K4 and P/V, then an **ADR with the outcome** (continue / narrow / pivot / stop; host decision). This document is then **locked or revised**, and only then are 03–08 written.

---

## 14. Open items and uncertainties

- **Live outcome mapping** (PRD §10a): `05-engine-design.md`.
- **Contract protection outside GitHub** (PRD §10c): `03-contract-spec.md`.
- **Not verified:** whether `ast` and `tokenize` behave identically in Pyodide (P1, P7); Pyodide core download size (P3); the exact CPython version in Pyodide 314.0.8 (the changelog shows 3.14.2 at 314.0.0).
- **Not verified:** whether the Pyodide loader supports integrity checks for the files it fetches (§8.2).
- **Not verified:** whether rate-limited requests count toward Hobby CDN usage (§9.2).
- **Not verified:** child-process and `resource` rlimit support on Vercel Python Functions (V1).
- **Cloud Run row** relies on a search-result summary and a third-party answer, not a direct read of Google's pricing page. It is excluded anyway (billing account).
- **jsDelivr** terms and availability were not reviewed. It is a third-party dependency of the live path.
- **Assumption:** the GitHub repository will be **public**, so standard Actions runners are free. If it stays private, the Free plan's $12/month of compute applies, and the budget must be checked.
- **Platform risk:** Vercel Services is Beta; Hobby is for "personal or non-commercial use".
- **Proposed numbers** in §7.3, §9 and §6.3 are design parameters (tunable in the spike). The §12 thresholds are pre-registered and fixed on approval.

---

## 15. Sources (all fetched 2026-11-20)

**Vercel**
- Python runtime: <https://vercel.com/docs/functions/runtimes/python>
- Functions limits: <https://vercel.com/docs/functions/limitations>
- WAF rate limiting: <https://vercel.com/docs/vercel-firewall/vercel-waf/rate-limiting>
- Hobby plan: <https://vercel.com/docs/plans/hobby>
- Fair use guidelines: <https://vercel.com/docs/limits/fair-use-guidelines>
- Services: <https://vercel.com/docs/services>
- Services pricing and limits: <https://vercel.com/docs/services/pricing>
- In-function concurrency for Python (changelog, 2025-01-14): <https://vercel.com/changelog/python-support-added-to-in-function-concurrency-beta>
- `vercel.json` headers: <https://vercel.com/docs/project-configuration/vercel-json>
- Terms of Service §4: <https://vercel.com/legal/terms>

**Next.js**
- Static exports: <https://nextjs.org/docs/app/guides/static-exports>

**Pyodide**
- Downloading and deploying: <https://pyodide.org/en/stable/usage/downloading-and-deploying.html>
- WASM constraints (removed stdlib modules): <https://pyodide.org/en/stable/usage/wasm-constraints.html>
- Changelog: <https://pyodide.org/en/stable/project/changelog.html>

**Python and web platform**
- Python `ast` docs (parse and literal_eval warnings): <https://docs.python.org/3/library/ast.html>
- MDN `Worker.terminate()`: <https://developer.mozilla.org/en-US/docs/Web/API/Worker/terminate>

**GitHub**
- Actions billing: <https://docs.github.com/en/billing/concepts/product-billing/github-actions>
- Actions limits: <https://docs.github.com/en/actions/reference/limits>
- Large files: <https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github>
- Pages limits: <https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits>

**Other hosts**
- Google Cloud Run pricing (via search results; not read directly): <https://cloud.google.com/run/pricing> and <https://docs.cloud.google.com/free/docs/free-cloud-features>
- Fly.io pricing: <https://docs.fly.io/about/pricing>
- Render free instances: <https://render.com/docs/free>
