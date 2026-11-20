import type { Interval } from "@/lib/types";
import { pct } from "@/lib/format";

export interface Row {
  label: string;
  a: Interval; // PROVECODE
  b?: Interval; // baseline
}

// Dot-and-whisker chart of proportions with 95% Wilson intervals. Hand-drawn SVG, no chart library.
export function IntervalChart({ rows, caption }: { rows: Row[]; caption: string }) {
  const W = 640;
  const left = 190;
  const right = 24;
  const rowH = 46;
  const H = rows.length * rowH + 34;
  const x = (v: number) => left + v * (W - left - right);
  return (
    <figure className="space-y-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-3xl" role="img" aria-label={caption}>
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={6} y2={H - 26} stroke="var(--rule)" strokeWidth={1} />
            <text x={x(t)} y={H - 8} textAnchor="middle" fontSize={12} fill="var(--ink-soft)">
              {pct(t)}
            </text>
          </g>
        ))}
        {rows.map((r, i) => {
          const y = 22 + i * rowH;
          const series: [Interval | undefined, string, number, string][] = [
            [r.a, "var(--blue)", -7, "PROVECODE"],
            [r.b, "var(--ink-soft)", 8, "Count-based baseline"],
          ];
          return (
            <g key={r.label}>
              <text x={0} y={y + 4} fontSize={13} fill="var(--ink)">
                {r.label}
              </text>
              {series.map(([iv, color, dy, who]) =>
                iv && iv.rate !== null && iv.lo !== null && iv.hi !== null ? (
                  <g key={who}>
                    <title>{`${who}: ${iv.k} of ${iv.n} (${pct(iv.rate)}), 95% CI ${pct(iv.lo)} to ${pct(iv.hi)}`}</title>
                    <line x1={x(iv.lo)} x2={x(iv.hi)} y1={y + dy} y2={y + dy} stroke={color} strokeWidth={2.5} strokeLinecap="round" />
                    <circle cx={x(iv.rate)} cy={y + dy} r={5} fill={who === "PROVECODE" ? color : "var(--paper)"} stroke={color} strokeWidth={2} />
                  </g>
                ) : null,
              )}
            </g>
          );
        })}
      </svg>
      <figcaption className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-ink-soft">
        <span>
          <svg width="14" height="14" aria-hidden className="mr-1.5 inline">
            <circle cx="7" cy="7" r="5" fill="var(--blue)" />
          </svg>
          PROVECODE
        </span>
        <span>
          <svg width="14" height="14" aria-hidden className="mr-1.5 inline">
            <circle cx="7" cy="7" r="5" fill="var(--paper)" stroke="var(--ink-soft)" strokeWidth="2" />
          </svg>
          Count-based baseline (reimplementation)
        </span>
        <span>Lines show 95% Wilson intervals. {caption}</span>
      </figcaption>
    </figure>
  );
}
