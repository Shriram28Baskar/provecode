import type { Interval } from "./types";

export const pct = (x: number | null | undefined, digits = 0) =>
  x === null || x === undefined ? "n/a" : `${(x * 100).toFixed(digits)}%`;

export const frac = (i: Interval) => `${i.k} of ${i.n}`;

export const ci = (i: Interval) => (i.lo === null || i.hi === null ? "no data" : `95% CI ${pct(i.lo)} to ${pct(i.hi)}`);

export function pValue(p: number | null): string {
  if (p === null) return "not testable (no disagreements)";
  if (p < 0.001) return `p = ${p.toExponential(1)}`;
  return `p = ${p.toFixed(3)}`;
}
