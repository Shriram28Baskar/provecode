// Build-time guard (ADR 0005, build spec section 6).
// 1. Copies the artifacts the site renders into web/generated/, refusing any file whose
//    sha256 does not match artifacts/manifest.json. Pages read only these copies, so every
//    rendered number resolves to a manifest entry; a missing or altered artifact fails the build.
// 2. Publishes the stdlib-only engine core to public/py/ with per-file sha256 for the worker.
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const web = resolve(here, "..");
const root = resolve(web, "..");
const artifacts = join(root, "artifacts");

const REQUIRED = [
  "benchmark/tierA.json",
  "benchmark/tierA_u.json",
  "benchmark/perf.json",
  "presets/weakened.json",
  "presets/legit.json",
  "runs/checkout-weakened/report.json",
  "runs/checkout-legit/report.json",
];
const OPTIONAL = ["benchmark/parity.json"];
// The browser gets only the host-independent core (architecture section 4.1): no cli, git or exec.
const CORE = ["__init__.py", "model.py", "limits.py", "analyzer.py", "contract.py", "decide.py", "report.py", "pipeline.py"];

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");
const fail = (msg) => {
  console.error(`prepare: ${msg}`);
  process.exit(1);
};

const manifest = JSON.parse(readFileSync(join(artifacts, "manifest.json"), "utf8"));
const byPath = new Map(manifest.artifacts.map((a) => [a.path, a]));
const out = join(web, "generated");
rmSync(out, { recursive: true, force: true });

const copied = {};
for (const rel of [...REQUIRED, ...OPTIONAL]) {
  const entry = byPath.get(rel);
  if (!entry) {
    if (OPTIONAL.includes(rel)) continue;
    fail(`${rel} is not in artifacts/manifest.json`);
  }
  const buf = readFileSync(join(artifacts, rel));
  if (sha256(buf) !== entry.sha256) fail(`${rel}: sha256 does not match the manifest`);
  const dest = join(out, rel);
  mkdirSync(dirname(dest), { recursive: true });
  writeFileSync(dest, buf);
  copied[rel] = { sha256: entry.sha256, generator: entry.generator, deterministic: entry.deterministic };
}
writeFileSync(join(out, "index.json"), JSON.stringify(copied, null, 2));

const pyOut = join(web, "public", "py", "provecode");
rmSync(join(web, "public", "py"), { recursive: true, force: true });
mkdirSync(pyOut, { recursive: true });
const files = {};
for (const name of CORE) {
  const src = join(root, "engine", "src", "provecode", name);
  copyFileSync(src, join(pyOut, name));
  files[name] = sha256(readFileSync(src));
}
writeFileSync(join(web, "public", "py", "files.json"), JSON.stringify({ package: "provecode", files }, null, 2));
// 3. Self-host the pinned Pyodide runtime (ADR 0008) from the exact-pinned npm package.
const PYODIDE_VERSION = "314.0.8";
const pyoPkg = join(web, "node_modules", "pyodide");
const pyoVersion = JSON.parse(readFileSync(join(pyoPkg, "package.json"), "utf8")).version;
if (pyoVersion !== PYODIDE_VERSION) fail(`pyodide ${pyoVersion} installed, ${PYODIDE_VERSION} pinned`);
const RUNTIME = ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"];
const pyoOut = join(web, "public", "pyodide", `v${PYODIDE_VERSION}`);
rmSync(join(web, "public", "pyodide"), { recursive: true, force: true });
mkdirSync(pyoOut, { recursive: true });
const runtime = {};
for (const name of RUNTIME) {
  copyFileSync(join(pyoPkg, name), join(pyoOut, name));
  runtime[name] = sha256(readFileSync(join(pyoPkg, name)));
}
writeFileSync(join(web, "public", "pyodide", "files.json"), JSON.stringify({ version: PYODIDE_VERSION, files: runtime }, null, 2));
console.log(`prepare: ${Object.keys(copied).length} artifacts verified, ${CORE.length} engine files and ${RUNTIME.length} Pyodide files published`);
