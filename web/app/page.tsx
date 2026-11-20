import Link from "next/link";
import { Diff, SourceBadge, Stamp } from "@/components/marks";
import { preset, run, tierA } from "@/lib/data";
import { ci, frac, pct } from "@/lib/format";

export default function Home() {
  const weakened = preset("weakened");
  const finding = weakened.report.findings.find((f) => f.severity === "blocking")!;
  const recorded = run("checkout-weakened");
  const repoTests = recorded.evidence.find((e) => e.owner === "repo")!;
  const capCheck = recorded.evidence.find((e) => e.check_id === "chk-cap")!;
  const t = tierA().held_out;

  return (
    <div className="space-y-24 pt-14">
      <section className="grid items-start gap-10 lg:grid-cols-[1.1fr_0.9fr]" aria-labelledby="hero">
        <div className="min-w-0 space-y-6">
          <h1 id="hero" className="max-w-[16ch] text-[2.3rem] font-extrabold leading-[1.05] tracking-tight sm:text-[3.4rem]">
            Tests passed. The 20% cap is gone.
          </h1>
          <p className="prose-measure text-lg text-ink-soft">
            An AI agent was asked to fix a discount bug. It removed the discount cap, then loosened the one test that
            guarded it. The repository&apos;s own tests stayed green. PROVECODE reads the test change itself, links it
            to the requirement it protected, and runs the reviewers&apos; own checks against the result.
          </p>
          <div className="flex flex-wrap gap-4 pt-2">
            <Link href="/live/" className="rounded-md bg-blue px-5 py-3 font-semibold text-sheet">
              Run it live
            </Link>
            <Link href="/runs/checkout-weakened/" className="rounded-md border border-ink px-5 py-3 font-semibold">
              See the full recorded run
            </Link>
          </div>
        </div>

        <div className="relative min-w-0 space-y-4 pt-2">
          <Diff
            before={finding.before}
            after={finding.after}
            beforeLine={finding.base_lines?.[0]}
            afterLine={finding.head_lines?.[0]}
            label={`${weakened.path}, test_discount_is_capped`}
          />
          <div className="flex justify-end pr-2">
            <Stamp outcome={weakened.report.outcome} land />
          </div>
          <p className="text-sm text-ink-soft">
            One assertion before, one after. A detector that counts assertions sees no change. This one is a{" "}
            <strong className="text-ink">{finding.rule}</strong> finding: {finding.rationale.toLowerCase()}
          </p>
        </div>
      </section>

      <section aria-labelledby="evidence" className="space-y-8">
        <h2 id="evidence" className="text-2xl font-bold tracking-tight">
          Two independent pieces of evidence, both on the same requirement
        </h2>
        <div className="grid gap-10 md:grid-cols-2">
          <div className="space-y-3">
            <SourceBadge mode="precomputed" />
            <h3 className="text-lg font-bold">The test was weakened</h3>
            <p className="prose-measure">
              Static analysis compares the test before and after. The equality to 80.00 became a bound that any price
              satisfies. The test guards contract condition <strong>{finding.condition_ids.join(", ")}</strong>, “
              {weakened.report.contract?.conditions.find((c) => c.id === finding.condition_ids[0])?.description}”, so the
              finding is blocking.
            </p>
          </div>
          <div className="space-y-3">
            <SourceBadge mode="recorded" />
            <h3 className="text-lg font-bold">The reviewers&apos; check fails</h3>
            <p className="prose-measure">
              In a sandbox with no network, the repository&apos;s own tests <strong className="text-verified">{repoTests.result}</strong>,
              while the contract-owned check <code className="font-mono">{capCheck.check_id}</code>{" "}
              <strong className="text-failed">{capCheck.result}s</strong>. The candidate cannot edit that check, because
              it lives in the approved contract, outside the change.
            </p>
          </div>
        </div>
      </section>

      <section aria-labelledby="bench" className="space-y-6">
        <h2 id="bench" className="text-2xl font-bold tracking-tight">
          Measured, not claimed
        </h2>
        <p className="prose-measure text-ink-soft">
          On {t.n_weakening} held-out injected weakenings in six real open-source repositories, compared with a
          reimplementation of the count-based detectors that already exist.
        </p>
        <dl className="grid gap-8 sm:grid-cols-3">
          <div>
            <dt className="text-sm text-ink-soft">Count-preserving weakenings caught</dt>
            <dd className="text-3xl font-extrabold">
              {pct(t.recall_count_preserving.provecode.rate)}
              <span className="ml-2 text-base font-semibold text-ink-soft">vs {pct(t.recall_count_preserving.baseline.rate)}</span>
            </dd>
            <dd className="text-sm text-ink-soft">
              {frac(t.recall_count_preserving.provecode)}, {ci(t.recall_count_preserving.provecode)}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-ink-soft">All weakenings caught</dt>
            <dd className="text-3xl font-extrabold">
              {pct(t.recall.provecode.rate)}
              <span className="ml-2 text-base font-semibold text-ink-soft">vs {pct(t.recall.baseline.rate)}</span>
            </dd>
            <dd className="text-sm text-ink-soft">
              {frac(t.recall.provecode)}, {ci(t.recall.provecode)}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-ink-soft">Legitimate changes blocked</dt>
            <dd className="text-3xl font-extrabold">{frac(t.legit_blocking.provecode)}</dd>
            <dd className="text-sm text-ink-soft">{ci(t.legit_blocking.provecode)}. Few cases, so the range is wide.</dd>
          </div>
        </dl>
        <p className="text-sm">
          These are injected cases, labeled experimental, not real-world performance.{" "}
          <Link href="/benchmark/" className="link">
            Read the full benchmark, including what it misses
          </Link>
          .
        </p>
      </section>
    </div>
  );
}
