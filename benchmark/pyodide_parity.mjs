// Pyodide parity (build spec section 8): run every Tier A case through the engine inside
// Pyodide (the same runtime the browser uses) and diff against the CPython results in
// artifacts/benchmark/tierA.json. Writes artifacts/benchmark/parity.json.
//
//   node benchmark/pyodide_parity.mjs     (needs web/node_modules and benchmark/.cache/cases)
import { createRequire } from "node:module";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(root, "web", "package.json"));
const { loadPyodide, version } = require("pyodide");

const tier = JSON.parse(readFileSync(join(root, "artifacts/benchmark/tierA.json"), "utf8"));
const casesDir = join(root, "benchmark/.cache/cases");
const py = await loadPyodide();
py.FS.mkdirTree("/engine/provecode");
for (const f of readdirSync(join(root, "engine/src/provecode"))) {
  if (f.endsWith(".py") && !["cli.py", "git.py", "exec.py"].includes(f)) {
    py.FS.writeFile(`/engine/provecode/${f}`, readFileSync(join(root, "engine/src/provecode", f)));
  }
}
py.runPython(`
import sys, json, platform
sys.path.insert(0, "/engine")
from provecode import pipeline, contract
from provecode.limits import Limits
from provecode.model import WEAKENING_RULES
def _approve(d):
    return contract.approve(d, "tierA-harness (out of band)", "2026-11-20T00:00:00Z")
def run_case(meta_json, base, head):
    m = json.loads(meta_json)
    r = pipeline.run([(m["file"], base, head)], _approve(m["contract_head"]), _approve(m["contract_base"]),
                     mode="static", limits=Limits.cli())
    weak = [f for f in r["findings"] if f["rule"] in WEAKENING_RULES and f["severity"] != "info"]
    return json.dumps({"flagged": bool(weak), "rules": sorted({f["rule"] for f in r["findings"]}),
                       "outcome": r["outcome"], "blocking": any(f["severity"] == "blocking" for f in r["findings"])})
`);
const runCase = py.globals.get("run_case");
const mismatches = [];
for (const row of tier.cases) {
  const d = join(casesDir, row.id);
  const meta = readFileSync(join(d, "meta.json"), "utf8");
  const got = JSON.parse(runCase(meta, readFileSync(join(d, "base.py"), "utf8"), readFileSync(join(d, "head.py"), "utf8")));
  const want = row.provecode;
  for (const k of ["flagged", "rules", "outcome", "blocking"]) {
    if (JSON.stringify(got[k]) !== JSON.stringify(want[k])) mismatches.push({ id: row.id, field: k, cpython: want[k], pyodide: got[k] });
  }
}
const out = {
  schema_version: "1",
  pyodide_version: version,
  pyodide_python: py.runPython("platform.python_version()"),
  cpython_reference: tier.provenance.python,
  cases_compared: tier.cases.length,
  fields_compared: ["flagged", "rules", "outcome", "blocking"],
  mismatches,
  identical: mismatches.length === 0,
  command: "node benchmark/pyodide_parity.mjs",
};
writeFileSync(join(root, "artifacts/benchmark/parity.json"), JSON.stringify(out, null, 2) + "\n");
console.log(`pyodide ${version} (Python ${out.pyodide_python}): ${tier.cases.length} cases, ${mismatches.length} mismatches`);
process.exit(mismatches.length ? 1 : 0);
