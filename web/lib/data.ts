// Build-time loaders. They read only web/generated/, which scripts/prepare.mjs fills with
// manifest-verified artifacts. A missing file fails the build instead of rendering a blank.
import "server-only";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { GuardEval, Parity, Perf, Preset, Report, TierA } from "./types";

const dir = join(process.cwd(), "generated");

function load<T>(rel: string): T {
  try {
    return JSON.parse(readFileSync(join(dir, rel), "utf8")) as T;
  } catch (err) {
    throw new Error(`Artifact ${rel} is missing or unreadable. Run \`npm run prep\`. (${String(err)})`);
  }
}

export const tierA = () => load<TierA>("benchmark/tierA.json");
export const tierAU = () => load<TierA>("benchmark/tierA_u.json");
export const perf = () => load<Perf>("benchmark/perf.json");
export const guardEval = () => load<GuardEval>("benchmark/guard.json");
export const preset = (id: "weakened" | "legit") => load<Preset>(`presets/${id}.json`);
export const run = (id: string) => load<Report>(`runs/${id}/report.json`);
export const artifactIndex = () => load<Record<string, { sha256: string; generator: string }>>("index.json");
export function parity(): Parity | null {
  try {
    return load<Parity>("benchmark/parity.json");
  } catch {
    return null;
  }
}

export const RUNS = ["checkout-weakened", "checkout-legit"] as const;
