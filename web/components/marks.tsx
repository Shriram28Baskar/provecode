import type { Mode, Outcome } from "@/lib/types";

const OUTCOME_LINE: Record<Outcome, string> = {
  FAILED: "A mandatory requirement is broken or its guard was weakened",
  INCONCLUSIVE: "Nothing failed, but a mandatory requirement was not proven",
  VERIFIED: "Every mandatory requirement has passing, independent evidence",
};

export function Stamp({ outcome, land = false, note = true }: { outcome: Outcome; land?: boolean; note?: boolean }) {
  return (
    <div
      className={`stamp stamp-${outcome.toLowerCase()} ${land ? "stamp-land" : ""}`}
      role="img"
      aria-label={`Verdict: ${outcome}. ${OUTCOME_LINE[outcome]}.`}
    >
      <span className="stamp-word">{outcome}</span>
      {note && <span className="stamp-note">{OUTCOME_LINE[outcome]}</span>}
    </div>
  );
}

const SOURCE: Record<Mode, { label: string; title: string }> = {
  live: { label: "Live", title: "Computed just now in this browser. Static analysis only; your code is never run." },
  precomputed: { label: "Precomputed", title: "Same engine, computed at build time from a fixed example." },
  recorded: { label: "Recorded run", title: "A real engine run that executed checks in a Docker sandbox. Full provenance below." },
  static: { label: "Static (CLI)", title: "Static analysis from the command line." },
};

export function SourceBadge({ mode }: { mode: Mode }) {
  const s = SOURCE[mode];
  return (
    <span className={`src src-${mode}`} title={s.title}>
      {mode === "live" && <span aria-hidden className="inline-block size-1.5 rounded-full bg-current" />}
      {s.label}
    </span>
  );
}

export function sourceExplainer(mode: Mode): string {
  return SOURCE[mode].title;
}

export function Diff({
  before,
  after,
  beforeLine,
  afterLine,
  label,
}: {
  before: string | null;
  after: string | null;
  beforeLine?: number | null;
  afterLine?: number | null;
  label?: string;
}) {
  const rows: { sign: "-" | "+"; text: string; line: number | null }[] = [];
  (before ?? "").split("\n").filter((l) => before !== null && l !== "").forEach((t, i) =>
    rows.push({ sign: "-", text: t, line: beforeLine ? beforeLine + i : null }),
  );
  (after ?? "").split("\n").filter((l) => after !== null && l !== "").forEach((t, i) =>
    rows.push({ sign: "+", text: t, line: afterLine ? afterLine + i : null }),
  );
  return (
    <figure className="overflow-hidden rounded-md border border-rule bg-sheet">
      {label && <figcaption className="border-b border-rule px-3 py-1.5 font-mono text-[0.8rem] text-ink-soft">{label}</figcaption>}
      <pre className="diff overflow-x-auto py-1" aria-label="Change, removed then added lines">
        {rows.map((r, i) => (
          <div key={i} className={`flex ${r.sign === "-" ? "diff-del" : "diff-add"}`}>
            <span className="w-12 shrink-0 select-none pr-2 text-right text-ink-soft">{r.line ?? ""}</span>
            <span className="w-5 shrink-0 select-none" aria-label={r.sign === "-" ? "removed" : "added"}>
              {r.sign}
            </span>
            <code className="min-w-0 whitespace-pre-wrap break-all pr-4 sm:whitespace-pre sm:break-normal">{r.text}</code>
          </div>
        ))}
      </pre>
    </figure>
  );
}
