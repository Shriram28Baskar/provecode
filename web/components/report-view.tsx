import type { Condition, Evidence, Finding, Report } from "@/lib/types";
import { Diff, SourceBadge, Stamp, sourceExplainer } from "./marks";

const SEVERITY: Record<Finding["severity"], { label: string; cls: string }> = {
  blocking: { label: "Blocking", cls: "text-failed" },
  warning: { label: "Warning", cls: "text-inconclusive" },
  info: { label: "Info", cls: "text-ink-soft" },
};

const GAP_TEXT: Record<string, string> = {
  requires_execution: "Needs a test run; this view only reads code",
  no_check: "No contract check is defined",
  input_limit: "Input is over the size or nesting limit",
  unparseable: "Could not be parsed",
  timeout: "Timed out",
  contract_invalid: "Contract missing, unapproved or altered",
  check_not_passed: "No passing check result",
  unsupported: "Check type not supported yet",
};

function shortTest(t: string | null) {
  return t ? t.split("::").slice(1).join("::") || t : "";
}

function conditionOf(report: Report, id: string): Condition | undefined {
  return report.contract?.conditions.find((c) => c.id === id);
}

export function FindingItem({ f, report }: { f: Finding; report: Report }) {
  const conds = f.condition_ids.map((id) => conditionOf(report, id)).filter(Boolean) as Condition[];
  const approved = (f.detail?.approved_contract_change as string[] | undefined) ?? [];
  return (
    <li className="grid gap-3 border-t border-rule py-5 first:border-t-0 md:grid-cols-[13rem_1fr]">
      <div className="text-sm">
        <p className={`font-bold ${SEVERITY[f.severity].cls}`}>{SEVERITY[f.severity].label}</p>
        <p className="font-semibold">
          {f.rule}: {f.title}
        </p>
        <p className="mt-1 break-all font-mono text-[0.8rem] text-ink-soft">{shortTest(f.test) || f.file}</p>
      </div>
      <div className="min-w-0 space-y-3">
        <p className="prose-measure">{f.rationale}</p>
        {(f.before || f.after) && (
          <Diff before={f.before} after={f.after} beforeLine={f.base_lines?.[0]} afterLine={f.head_lines?.[0]} label={f.file} />
        )}
        {conds.length > 0 && (
          <p className="text-sm">
            Guards{" "}
            {conds.map((c, i) => (
              <span key={c.id}>
                {i > 0 && ", "}
                <strong>{c.id}</strong> “{c.description}”{c.mandatory ? " (mandatory)" : ""}
              </span>
            ))}
            {approved.length > 0 && (
              <span className="text-verified">. Covered by the approved contract change to {approved.join(", ")}, so it does not block.</span>
            )}
          </p>
        )}
        {conds.length === 0 && f.severity !== "info" && (
          <p className="text-sm text-ink-soft">This test guards no contract condition, so the finding is advisory.</p>
        )}
      </div>
    </li>
  );
}

function EvidenceRow({ e, report }: { e: Evidence; report: Report }) {
  const tone = e.result === "pass" ? "text-verified" : e.result === "fail" ? "text-failed" : "text-inconclusive";
  return (
    <details className="group border-t border-rule py-3 first:border-t-0">
      <summary className="flex cursor-pointer flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className={`w-12 font-bold ${tone}`}>{e.result}</span>
        <span className="font-mono text-sm">{e.check_id}</span>
        <span className="text-sm text-ink-soft">
          {e.owner === "contract"
            ? `Contract check for ${e.condition_ids.map((c) => `${c} ${conditionOf(report, c)?.description ?? ""}`).join(", ")}`
            : "The repository's own tests (supporting evidence, never sufficient alone)"}
        </span>
        <span className="ml-auto text-sm text-ink-soft">
          exit {e.exit_code ?? "none"}, {e.duration_s ?? "?"} s
        </span>
      </summary>
      <div className="mt-3 space-y-2">
        <p className="font-mono text-[0.8rem] text-ink-soft">$ {e.command}</p>
        <pre className="diff max-h-80 overflow-auto rounded-md border border-rule bg-sheet p-3">{e.output_excerpt}</pre>
        <p className="break-all font-mono text-[0.75rem] text-ink-soft">full output sha256 {e.output_sha256}</p>
      </div>
    </details>
  );
}

export function ReportView({ report, heading }: { report: Report; heading?: string }) {
  const visible = report.findings;
  const mandatory = report.contract?.conditions.filter((c) => c.mandatory) ?? [];
  const p = report.provenance as Record<string, unknown>;
  return (
    <article className="space-y-10" aria-label={heading ?? "Verification report"}>
      <header className="flex flex-wrap items-start justify-between gap-6">
        <div className="space-y-3">
          <SourceBadge mode={report.mode} />
          <p className="prose-measure text-sm text-ink-soft">{sourceExplainer(report.mode)}</p>
          {heading && <h2 className="text-2xl font-bold tracking-tight">{heading}</h2>}
        </div>
        <Stamp outcome={report.outcome} />
      </header>

      <section aria-labelledby="why" className="space-y-3">
        <h3 id="why" className="text-lg font-bold">
          Why this verdict
        </h3>
        <ul className="prose-measure list-disc space-y-1 pl-5">
          {report.reasons.map((r, i) => (
            <li key={i}>{r.text}</li>
          ))}
        </ul>
      </section>

      {mandatory.length > 0 && (
        <section aria-labelledby="conds" className="space-y-3">
          <h3 id="conds" className="text-lg font-bold">
            Requirements in contract {report.contract?.id} v{report.contract?.version}
          </h3>
          <table className="w-full text-left text-[0.95rem]">
            <thead className="text-sm text-ink-soft">
              <tr>
                <th className="py-1.5 pr-4 font-semibold">Condition</th>
                <th className="py-1.5 pr-4 font-semibold">Requirement</th>
                <th className="py-1.5 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {mandatory.map((c) => {
                const s = report.condition_status[c.id] ?? "gap";
                const tone = s === "pass" ? "text-verified" : s === "fail" ? "text-failed" : "text-inconclusive";
                return (
                  <tr key={c.id} className="border-t border-rule">
                    <td className="py-2 pr-4 font-semibold">{c.id}</td>
                    <td className="py-2 pr-4">{c.description}</td>
                    <td className={`py-2 font-semibold ${tone}`}>{s === "gap" ? "not proven" : s === "pass" ? "proven" : "failed"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      <section aria-labelledby="findings" className="space-y-2">
        <h3 id="findings" className="text-lg font-bold">
          What changed in the tests ({visible.length})
        </h3>
        {visible.length === 0 ? (
          <p className="text-ink-soft">No test edits weakened a check.</p>
        ) : (
          <ul>
            {visible.map((f) => (
              <FindingItem key={f.id} f={f} report={report} />
            ))}
          </ul>
        )}
      </section>

      {report.evidence.length > 0 && (
        <section aria-labelledby="evidence" className="space-y-2">
          <h3 id="evidence" className="text-lg font-bold">
            Execution evidence ({report.evidence.length})
          </h3>
          <p className="prose-measure text-sm text-ink-soft">Open a row to see the command and its raw output.</p>
          <div>
            {report.evidence.map((e) => (
              <EvidenceRow key={e.id} e={e} report={report} />
            ))}
          </div>
        </section>
      )}

      {report.gaps.length > 0 && (
        <section aria-labelledby="gaps" className="space-y-2">
          <h3 id="gaps" className="text-lg font-bold">
            Not evaluated ({report.gaps.length})
          </h3>
          <ul className="prose-measure space-y-2">
            {Object.entries(
              report.gaps.reduce<Record<string, typeof report.gaps>>((acc, g) => {
                (acc[g.reason] ??= []).push(g);
                return acc;
              }, {}),
            ).map(([reason, gs]) => (
              <li key={reason}>
                <span className="font-semibold">{GAP_TEXT[reason] ?? reason}</span>
                {gs.some((g) => g.condition_id) && (
                  <span className="font-semibold">: {gs.map((g) => g.condition_id).filter(Boolean).join(", ")}</span>
                )}
                <span className="block text-sm text-ink-soft">{gs.map((g) => g.detail).join(" ")}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="prov" className="space-y-2">
        <h3 id="prov" className="text-lg font-bold">
          Provenance
        </h3>
        <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[12rem_1fr]">
          {report.contract && (
            <>
              <dt className="text-ink-soft">Contract hash</dt>
              <dd className="break-all font-mono">{report.contract.content_hash}</dd>
              <dt className="text-ink-soft">Approved by</dt>
              <dd>
                {report.contract.approved_by ?? "unknown"} on {report.contract.approved_at ?? "unknown"}
              </dd>
            </>
          )}
          {(["base_sha", "head_sha", "image_id", "base_image", "engine_version", "python", "generated_at"] as const).map(
            (k) =>
              p[k] ? (
                <div key={k} className="contents">
                  <dt className="text-ink-soft">{k.replace(/_/g, " ")}</dt>
                  <dd className="break-all font-mono">{String(p[k])}</dd>
                </div>
              ) : null,
          )}
        </dl>
      </section>
    </article>
  );
}
