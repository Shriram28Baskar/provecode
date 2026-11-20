// PROVECODE live analyzer worker (architecture section 5.2). Module worker.
// 1. Loads the pinned Pyodide runtime from this site's own origin (ADR 0008), after checking
//    the loader against its pinned SHA-384.
// 2. Fetches the stdlib-only engine core from this origin and checks each file's SHA-256.
// 3. Runs limits -> analyzer -> contract -> decide -> report on the test text it is sent.
// Submitted text is only parsed (ast.parse / tokenize); it is never imported or executed.
// The other runtime files are served from the same deployment and hashed at build time in
// /pyodide/files.json; the loader fetches them itself, so they are not re-checked here.

const PYODIDE_VERSION = "314.0.8";
const INDEX_URL = new URL(`/pyodide/v${PYODIDE_VERSION}/`, self.location.origin).href;
const LOADER_SHA384 = "551f9e629f0b64a1a1850ecdaa5dd338d248322cccd15471cfcee4e95ec8cae40712589c8f2ed42e690eb81d8b151ddb";

let ready = null;

async function digest(algo, buf) {
  const h = await crypto.subtle.digest(algo, buf);
  return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

const progress = (stage) => self.postMessage({ type: "progress", stage });

async function init() {
  const t0 = performance.now();
  progress("Downloading and checking the Python runtime loader");
  const res = await fetch(`${INDEX_URL}pyodide.mjs`, { cache: "force-cache" });
  if (!res.ok) throw new Error(`Pyodide loader download failed (HTTP ${res.status})`);
  if ((await digest("SHA-384", await res.arrayBuffer())) !== LOADER_SHA384) {
    throw new Error("Pyodide loader failed its integrity check, so it was not run");
  }
  // Same URL, now in the HTTP cache; a different response would come from our own deployment.
  const { loadPyodide } = await import(`${INDEX_URL}pyodide.mjs`);
  progress("Starting Python in WebAssembly");
  const py = await loadPyodide({ indexURL: INDEX_URL });
  progress("Loading and checking the PROVECODE engine");

  const listing = await (await fetch("/py/files.json", { cache: "no-cache" })).json();
  py.FS.mkdirTree("/engine/provecode");
  for (const [name, sha] of Object.entries(listing.files)) {
    const buf = await (await fetch(`/py/provecode/${name}`, { cache: "no-cache" })).arrayBuffer();
    if ((await digest("SHA-256", buf)) !== sha) throw new Error(`Engine file ${name} failed its integrity check`);
    py.FS.writeFile(`/engine/provecode/${name}`, new Uint8Array(buf));
  }
  py.runPython(`
import sys, json, platform
sys.path.insert(0, "/engine")
from provecode import pipeline
from provecode.limits import Limits
def _analyze(payload):
    p = json.loads(payload)
    r = pipeline.run([(p["path"], p["base"], p["head"])], p["contract_head"], p["contract_base"],
                     mode="live", limits=Limits.live(),
                     provenance={"runtime": "Pyodide ${PYODIDE_VERSION} (Python " + platform.python_version() + ")",
                                 "base": "preset baseline", "head": "edited in this browser"})
    return json.dumps(r)
`);
  return { py, analyze: py.globals.get("_analyze"), initMs: Math.round(performance.now() - t0) };
}

self.onmessage = async (ev) => {
  const msg = ev.data;
  try {
    if (!ready) ready = init();
    const { analyze, initMs } = await ready;
    if (msg.type === "init") {
      self.postMessage({ type: "ready", id: msg.id, initMs });
      return;
    }
    const t0 = performance.now();
    const report = JSON.parse(analyze(JSON.stringify(msg.payload)));
    self.postMessage({ type: "result", id: msg.id, report, ms: Math.round(performance.now() - t0) });
  } catch (err) {
    ready = null;
    self.postMessage({ type: "error", id: msg.id, message: String(err && err.message ? err.message : err) });
  }
};
