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
  const a = tierA();
  const t = a.held_out;
  // The release tag comes from the engine version the benchmark artifacts record, never typed by hand.
  const tag = `v${a.provenance.engine_version}`;
  const install = `git+https://github.com/Shriram28Baskar/provecode@${tag}#subdirectory=engine`;

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

      <section aria-labelledby="use" className="space-y-6">
        <h2 id="use" className="text-2xl font-bold tracking-tight">
          Use it on your own code
        </h2>
        <p className="prose-measure text-ink-soft">
          Beta, {tag}. It works with no configuration on Python projects that use pytest or unittest: any finding that
          weakens a test blocks. Real-world precision is not yet measured, so expect some false alarms.
        </p>
        <div className="grid gap-8 md:grid-cols-2">
          <div className="min-w-0 space-y-3 md:col-span-2">
            <h3 className="text-lg font-bold">On your machine</h3>
            <Code
              label="Install, then check uncommitted changes"
              text={`pipx install "provecode[cli] @ ${install}"\nprovecode guard`}
            />
            <p className="text-sm text-ink-soft">
              <code className="whitespace-nowrap font-mono">uv tool install</code> and <code className="whitespace-nowrap font-mono">pip install</code> take
              the same argument.
            </p>
          </div>
          <div className="min-w-0 space-y-3">
            <h3 className="text-lg font-bold">On pull requests</h3>
            <Code
              label=".github/workflows/provecode.yml"
              text={`on: pull_request\npermissions:\n  contents: read\njobs:\n  provecode:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n        with:\n          fetch-depth: 0\n      - uses: Shriram28Baskar/provecode@${tag}`}
            />
            <p className="text-sm text-ink-soft">
              <a className="link" href="https://github.com/Shriram28Baskar/provecode-action-demo/pulls">
                See it on a real repository
              </a>
              : the pull request that loosens a test fails the check, and an honest refactor passes it.
            </p>
          </div>
          <div className="min-w-0 space-y-3">
            <h3 className="text-lg font-bold">Before each commit</h3>
            <Code
              label=".pre-commit-config.yaml"
              text={`repos:\n  - repo: https://github.com/Shriram28Baskar/provecode\n    rev: ${tag}\n    hooks:\n      - id: provecode`}
            />
            <p className="text-sm text-ink-soft">
              For coding agents: <code className="whitespace-nowrap font-mono">provecode install-hooks --claude</code> stops Claude Code
              from finishing with weakened tests.
            </p>
          </div>
        </div>
        <p className="text-sm">
          <a className="link" href="https://github.com/Shriram28Baskar/provecode#use-it-in-your-repository-5-minutes-no-contract-needed">
            Full setup, contracts and the trusted re-run
          </a>{" "}
          ·{" "}
          <a className="link" href={`https://github.com/Shriram28Baskar/provecode/releases/tag/${tag}`}>
            Release notes for {tag}
          </a>
        </p>
      </section>
    </div>
  );
}

function Code({ label, text }: { label: string; text: string }) {
  return (
    <figure className="min-w-0 rounded-md border border-rule bg-sheet">
      <figcaption className="border-b border-rule px-3 py-1.5 font-mono text-[0.8rem] text-ink-soft">{label}</figcaption>
      <pre className="overflow-x-auto px-3 py-2 font-mono text-[0.8rem] leading-relaxed">
        <code>{text}</code>
      </pre>
    </figure>
  );
}
