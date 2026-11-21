import type { Metadata } from "next";
import { IntervalChart } from "@/components/interval-chart";
import { artifactIndex, parity, perf, tierA, tierAB, tierAU } from "@/lib/data";
import { ci, frac, pValue, pct } from "@/lib/format";

export const metadata: Metadata = { title: "Benchmark | PROVECODE" };

const CATEGORY: Record<string, string> = {
  "comparison-loosened": "Comparison loosened",
  "trivial-expected": "Expected value made trivial",
  "exception-widened": "Exception widened",
  "parametrize-reduced": "Parametrize cases removed",
  "test-deleted": "Test deleted",
  "skip-added": "Skip or guard added",
  "assertion-removed": "Assertion removed",
  other: "Not targeted by any rule",
  "vacuous-check": "Check made vacuous",
  "assertion-swallowed": "Assertion failure swallowed",
};

const PATTERN_TEXT: Record<string, string> = {
  W17: "assert A == B became assert A == A",
  W18: "the assert was wrapped in try / except AssertionError",
  W19: "assert A == B became assert A == B or True",
  U13: "self.assertEqual(A, B) became self.assertEqual(A, A)",
  U14: "the self.assert* call was wrapped in try / except AssertionError",
};

export default function BenchmarkPage() {
  const t = tierA();
  const h = t.held_out;
  const p = perf();
  const par = parity();
  const idx = artifactIndex();
  const f = t.falsification;
  const misses = Object.entries(t.all.by_pattern).filter(([, v]) => v.kind === "weakening" && v.provecode_flagged < v.n);
  const order = t.provenance.commit_order;
  const u = tierAU();
  const uh = u.held_out;
  const uMisses = Object.entries(u.all.by_pattern).filter(([, v]) => v.kind === "weakening" && v.provecode_flagged < v.n);
  const uOrder = u.provenance.commit_order;
  const ab = tierAB();
  const bh = ab.held_out;
  const bOrder = ab.provenance.commit_order;

  return (
    <div className="space-y-16 pt-12">
      <header className="space-y-4">
        <h1 className="text-[2.2rem] font-extrabold leading-tight tracking-tight">Tier A benchmark</h1>
        <p className="prose-measure text-lg text-ink-soft">{t.label}</p>
        <p className="prose-measure">
          {h.n_cases} held-out cases ({h.n_weakening} weakenings, {h.n_legit} legitimate edits), drawn from{" "}
          {t.all.n_cases} cases built by mechanically editing real test functions in {t.provenance.sources.length}{" "}
          permissively licensed repositories. The held-out split was frozen before any detector code existed, and only
          the tuning split was looked at while developing the rules.
        </p>
      </header>

      <section aria-labelledby="headline" className="space-y-6">
        <h2 id="headline" className="text-2xl font-bold tracking-tight">
          Detection on held-out cases
        </h2>
        <IntervalChart
          caption={`Held-out split, n = ${h.n_weakening} weakenings.`}
          rows={[
            { label: "All weakenings", a: h.recall.provecode, b: h.recall.baseline },
            { label: "Count-preserving", a: h.recall_count_preserving.provecode, b: h.recall_count_preserving.baseline },
            { label: "Count-reducing", a: h.recall_not_count_preserving.provecode, b: h.recall_not_count_preserving.baseline },
            { label: "Legitimate, flagged", a: h.legit_flagged.provecode, b: h.legit_flagged.baseline },
          ]}
        />
        <table className="w-full max-w-3xl text-left text-[0.95rem]">
          <thead className="text-sm text-ink-soft">
            <tr>
              <th className="py-1.5 pr-4 font-semibold">Held-out</th>
              <th className="py-1.5 pr-4 font-semibold">PROVECODE</th>
              <th className="py-1.5 pr-4 font-semibold">Baseline</th>
              <th className="py-1.5 font-semibold">Paired test</th>
            </tr>
          </thead>
          <tbody>
            {(
              [
                ["All weakenings", h.recall],
                ["Count-preserving", h.recall_count_preserving],
                ["Count-reducing", h.recall_not_count_preserving],
              ] as const
            ).map(([label, m]) => (
              <tr key={label} className="border-t border-rule align-top">
                <td className="py-2 pr-4 font-semibold">{label}</td>
                <td className="py-2 pr-4">
                  {frac(m.provecode)} ({pct(m.provecode.rate)})<br />
                  <span className="text-sm text-ink-soft">{ci(m.provecode)}</span>
                </td>
                <td className="py-2 pr-4">
                  {frac(m.baseline)} ({pct(m.baseline.rate)})<br />
                  <span className="text-sm text-ink-soft">{ci(m.baseline)}</span>
                </td>
                <td className="py-2 text-sm">
                  Only PROVECODE caught {m.paired.only_provecode}; only the baseline caught {m.paired.only_baseline}. Exact
                  McNemar {pValue(m.paired.mcnemar_exact_p)}.
                </td>
              </tr>
            ))}
            <tr className="border-t border-rule align-top">
              <td className="py-2 pr-4 font-semibold">Legitimate edits blocked</td>
              <td className="py-2 pr-4">
                {frac(h.legit_blocking.provecode)}
                <br />
                <span className="text-sm text-ink-soft">{ci(h.legit_blocking.provecode)}</span>
              </td>
              <td className="py-2 pr-4">
                {frac(h.legit_flagged.baseline)} flagged
                <br />
                <span className="text-sm text-ink-soft">the baseline has no severity</span>
              </td>
              <td className="py-2 text-sm">Only {h.n_legit} legitimate held-out cases, so this interval is wide.</td>
            </tr>
          </tbody>
        </table>
        <p className="prose-measure text-sm text-ink-soft">
          What counts as detected. PROVECODE: {t.detection_definition.provecode} Baseline:{" "}
          {t.detection_definition.baseline}
        </p>
      </section>

      <section aria-labelledby="hyp" className="space-y-4">
        <h2 id="hyp" className="text-2xl font-bold tracking-tight">
          Pre-registered hypotheses
        </h2>
        <ul className="prose-measure space-y-4">
          <li>
            <p className="font-semibold">
              F1, the core claim: {f.F1.supported ? "supported on Tier A" : "not supported on Tier A"}.
            </p>
            <p className="text-ink-soft">
              {f.F1.statement} Exact McNemar {pValue(f.F1.mcnemar_exact_p)}.
            </p>
          </li>
          <li>
            <p className="font-semibold">
              F2, parity where expected: {f.F2.baseline_outperforms ? "the baseline outperformed PROVECODE" : "parity held"}.
            </p>
            <p className="text-ink-soft">
              {f.F2.statement} Both caught {frac(h.recall_not_count_preserving.provecode)} deletions and removed asserts.
              PROVECODE claims no advantage here.
            </p>
          </li>
          <li>
            <p className="font-semibold">F3, false positives: {frac(f.F3.provecode)} legitimate held-out edits blocked.</p>
            <p className="text-ink-soft">
              {ci(f.F3.provecode)}. A real-world precision audit (Tier C) is needed before claiming a false-positive rate.
            </p>
          </li>
        </ul>
      </section>

      <section aria-labelledby="miss" className="space-y-4">
        <h2 id="miss" className="text-2xl font-bold tracking-tight">
          What it misses
        </h2>
        <p className="prose-measure">
          W17 to W19 were written on purpose to fall outside every rule, so the benchmark would show blind spots
          rather than hide them. Rules S5 and B4 were later added for them, after these results were seen, so Tier A
          recall on those patterns is no longer an unbiased estimate; Tier A-b below is their fresh test. Across all
          cases, PROVECODE still missed:
        </p>
        <ul className="prose-measure list-disc space-y-1 pl-5">
          {misses.map(([id, v]) => (
            <li key={id}>
              <strong>{id}</strong>, {PATTERN_TEXT[id] ?? CATEGORY[v.category]}: caught {v.provecode_flagged} of {v.n}{" "}
              (baseline {v.baseline_flagged} of {v.n})
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="cat" className="space-y-4">
        <h2 id="cat" className="text-2xl font-bold tracking-tight">
          By kind of weakening
        </h2>
        <p className="prose-measure text-sm text-ink-soft">
          All {t.all.n_weakening} weakening cases (tuning and held-out). Each kind has few cases, so these are
          descriptive only.
        </p>
        <IntervalChart
          caption="Per category, all splits."
          rows={Object.entries(t.all.by_category).map(([k, v]) => ({ label: CATEGORY[k] ?? k, a: v.provecode, b: v.baseline }))}
        />
      </section>

      <section aria-labelledby="tier-au" className="space-y-6">
        <h2 id="tier-au" className="text-2xl font-bold tracking-tight">
          Tier A-u: unittest-style tests
        </h2>
        <p className="prose-measure text-ink-soft">{u.label}</p>
        <p className="prose-measure">
          PROVECODE also reads <code>unittest.TestCase</code> classes, <code>self.assert*</code> checks and unittest
          skips. To measure that, a second benchmark was built the same way from {u.provenance.sources.length} other
          permissively licensed repositories: {u.all.n_cases} cases, {uh.n_cases} of them held out. Its patterns, cases
          and split were committed ({uOrder.split_frozen?.commit.slice(0, 12)}) before the detector change (
          {uOrder.detector_change?.commit.slice(0, 12)}). The pattern labels were written by the developer and await
          the owner&apos;s confirmation.
        </p>
        <IntervalChart
          caption={`Tier A-u held-out split, n = ${uh.n_weakening} weakenings.`}
          rows={[
            { label: "All weakenings", a: uh.recall.provecode, b: uh.recall.baseline },
            { label: "Count-preserving", a: uh.recall_count_preserving.provecode, b: uh.recall_count_preserving.baseline },
            { label: "Count-reducing", a: uh.recall_not_count_preserving.provecode, b: uh.recall_not_count_preserving.baseline },
            { label: "Legitimate, flagged", a: uh.legit_flagged.provecode, b: uh.legit_flagged.baseline },
          ]}
        />
        <ul className="prose-measure list-disc space-y-1 pl-5">
          <li>
            Count-preserving weakenings caught: PROVECODE {frac(uh.recall_count_preserving.provecode)}, baseline{" "}
            {frac(uh.recall_count_preserving.baseline)} (exact McNemar{" "}
            {pValue(uh.recall_count_preserving.paired.mcnemar_exact_p)}).
          </li>
          <li>
            Legitimate held-out edits blocked: {frac(uh.legit_blocking.provecode)} ({ci(uh.legit_blocking.provecode)}).
          </li>
          {u.revision_log.map((r) => (
            <li key={r.date + r.source}>
              After the Tier A-u held-out results were first seen, the engine was revised following the {r.source}:{" "}
              {r.changes.join("; ")}. Effect on Tier A-u: {r.effect_on_tier_a}.
            </li>
          ))}
          {uMisses.map(([id, v]) => (
            <li key={id}>
              Missed, all splits: <strong>{id}</strong>, {PATTERN_TEXT[id] ?? CATEGORY[v.category]}: caught{" "}
              {v.provecode_flagged} of {v.n} (baseline {v.baseline_flagged} of {v.n})
            </li>
          ))}
        </ul>
        <p className="prose-measure text-sm text-ink-soft">
          Engine source sha256 <span className="break-all font-mono">{u.provenance.engine_source_sha256}</span>;
          tierA_u.json sha256 <span className="break-all font-mono">{idx["benchmark/tierA_u.json"]?.sha256}</span>.
        </p>
      </section>

      <section aria-labelledby="tier-ab" className="space-y-6">
        <h2 id="tier-ab" className="text-2xl font-bold tracking-tight">
          Tier A-b: vacuous and swallowed checks
        </h2>
        <p className="prose-measure text-ink-soft">{ab.label}</p>
        <p className="prose-measure">
          Rules S5 (a check made vacuous: <code>A == A</code>, <code>or True</code>) and B4 (an assertion failure
          swallowed by <code>try/except</code>) were judged on a fresh benchmark: {ab.all.n_cases} cases from{" "}
          {ab.provenance.sources.length} further repositories, including legitimate near-misses such as an added
          reflexivity check, a re-raise and <code>try/finally</code>. Its cases and split were committed (
          {bOrder.split_frozen?.commit.slice(0, 12)}) before the rule code ({bOrder.detector_change?.commit.slice(0, 12)}
          ), and the rules were developed on the tuning split only.
        </p>
        <ul className="prose-measure list-disc space-y-1 pl-5">
          <li>
            Held-out weakenings caught: PROVECODE {frac(bh.recall.provecode)} ({ci(bh.recall.provecode)}), baseline{" "}
            {frac(bh.recall.baseline)}.
          </li>
          <li>Held-out legitimate edits flagged: {frac(bh.legit_flagged.provecode)} ({ci(bh.legit_flagged.provecode)}).</li>
          {ab.revision_log.map((r) => (
            <li key={r.date + r.source}>
              After the Tier A-b held-out results were first seen, the engine was revised following the {r.source}:{" "}
              {r.changes.join("; ")}. Effect on Tier A-b: {r.effect_on_tier_a}.
            </li>
          ))}
          {ab.tuning_log.map((l) => (
            <li key={l.round}>
              Tuning round {l.round}, on the tuning split only: {l.rule}: {l.change.replaceAll("`", "")}
            </li>
          ))}
          <li>
            The patterns mirror the rules&apos; stated targets, so this shows the rules work as specified on injected
            edits. It is not a real-world recall figure.
          </li>
        </ul>
      </section>

      <section aria-labelledby="perf" className="space-y-4">
        <h2 id="perf" className="text-2xl font-bold tracking-tight">
          Speed and the live path
        </h2>
        <p className="prose-measure">
          Per case, the analyzer took a median {(p.per_case.median_s * 1000).toFixed(0)} ms and at most{" "}
          {(p.per_case.max_s * 1000).toFixed(0)} ms on CPython {p.machine.python} ({p.machine.system}), with peak memory
          of at most {Math.round(p.per_case.max_peak_bytes / 1e6)} MB. At the browser&apos;s input limits, the worst
          input took {Math.max(...Object.values(p.worst_case_live_limits).map((w) => w.seconds * 1000)).toFixed(0)} ms.
          Over-limit input is refused before parsing.
        </p>
        {par && (
          <p className="prose-measure">
            The browser runs the same engine in Pyodide {par.pyodide_version} (Python {par.pyodide_python}). All{" "}
            {par.cases_compared} cases
            {par.tiers ? ` (${par.tiers.map((x) => `Tier ${x.tier}: ${x.cases_compared}`).join(", ")})` : ""} produced {par.identical ? "identical" : "different"} findings, severities and
            verdicts compared with CPython {par.cpython_reference}
            {par.identical ? "." : `: ${par.mismatches.length} differences.`}
          </p>
        )}
      </section>

      <section aria-labelledby="limits" className="space-y-4">
        <h2 id="limits" className="text-2xl font-bold tracking-tight">
          Read these numbers with care
        </h2>
        <ul className="prose-measure list-disc space-y-2 pl-5">
          <li>Cases are injected edits, not real weakening commits. Real-world recall (Tier B) and precision (Tier C) are not measured yet.</li>
          <li>The same author wrote the patterns, the cases and the detector. The patterns and split were committed before the detector, and that order is shown below.</li>
          <li>
            One tuning round, on the tuning split only: {t.tuning_log.map((l) => `${l.rule}: ${l.change.replaceAll("`", "")}`).join(" ")}
          </li>
          {t.revision_log.map((r) => (
            <li key={r.date + r.source}>
              After the held-out results were first seen, the engine was revised following an {r.source}: {r.changes.join("; ")}.
              Effect on Tier A: {r.effect_on_tier_a}.
            </li>
          ))}
          <li>The baseline is a reimplementation of published rules, not the original tools.</li>
          <li>The real-world feasibility spike (Gate 0) is paused, not finished; no result from it is claimed.</li>
        </ul>
      </section>

      <section aria-labelledby="prov" className="space-y-3">
        <h2 id="prov" className="text-2xl font-bold tracking-tight">
          Provenance
        </h2>
        <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[14rem_1fr]">
          <dt className="text-ink-soft">Regenerate</dt>
          <dd className="font-mono">{t.provenance.command}</dd>
          <dt className="text-ink-soft">Patterns and split frozen</dt>
          <dd className="break-all font-mono">
            {order.split_frozen?.commit.slice(0, 12)} at {order.split_frozen?.committed_at}
          </dd>
          <dt className="text-ink-soft">First detector code</dt>
          <dd className="break-all font-mono">
            {order.first_detector_code?.commit.slice(0, 12)} at {order.first_detector_code?.committed_at}
          </dd>
          <dt className="text-ink-soft">Engine source sha256</dt>
          <dd className="break-all font-mono">{t.provenance.engine_source_sha256}</dd>
          <dt className="text-ink-soft">tierA.json sha256</dt>
          <dd className="break-all font-mono">{idx["benchmark/tierA.json"]?.sha256}</dd>
          <dt className="text-ink-soft">Sources</dt>
          <dd>
            <ul>
              {t.provenance.sources.map((s) => (
                <li key={s.repo}>
                  <a className="link" href={`https://github.com/${s.repo}/tree/${s.sha}`}>
                    {s.repo}
                  </a>{" "}
                  <span className="font-mono text-ink-soft">{s.sha.slice(0, 10)}</span> ({s.licenses.join(" or ")})
                </li>
              ))}
            </ul>
          </dd>
        </dl>
      </section>
    </div>
  );
}
