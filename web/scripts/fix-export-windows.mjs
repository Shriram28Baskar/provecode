// Windows-only workaround: next export builds segment prefetch filenames by replacing "/" with "."
// in a path that uses "\" on Windows, so files land in folders (out/live/__next.live/__PAGE__.txt)
// instead of out/live/__next.live.__PAGE__.txt. Linux builds (Vercel) are unaffected; this is a no-op there.
import { readdirSync, renameSync, rmSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";

if (process.platform !== "win32") process.exit(0);
const out = resolve(import.meta.dirname, "..", "out");
let moved = 0;
function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (!statSync(p).isDirectory()) continue;
    if (name.startsWith("__next.")) {
      const files = [];
      const collect = (d) => readdirSync(d).forEach((n) => (statSync(join(d, n)).isDirectory() ? collect(join(d, n)) : files.push(join(d, n))));
      collect(p);
      for (const f of files) {
        renameSync(f, join(dir, name + "." + relative(p, f).split(sep).join(".")));
        moved++;
      }
      rmSync(p, { recursive: true });
    } else walk(p);
  }
}
walk(out);
console.log(`fix-export-windows: flattened ${moved} segment files`);
