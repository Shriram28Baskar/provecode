import type { Metadata } from "next";
import Link from "next/link";
import { Stamp } from "@/components/marks";

export const metadata: Metadata = { title: "How it works | PROVECODE" };

const STEPS = [
  ["Read the change", "Git gives the exact base and head commits. Only test files are compared, and the repository is never checked out or modified."],
  ["Compare each test", "Python's own parser reads both versions. Tests are matched by name, renames are recognised, and each assertion is compared by what it checks and how strictly."],
  ["Link to the contract", "Every finding is tied to the approved requirement its test guards. Severity comes from that requirement: weakening a guard of a mandatory condition is blocking."],
  ["Run the reviewers' checks", "In recorded runs, contract-owned checks run against the candidate in a Docker sandbox with no network. The repository's own tests run too, as supporting evidence only."],
  ["Decide by rule", "A fixed function turns findings, evidence and gaps into one verdict. No language model takes part, and missing evidence never becomes a pass."],
] as const;

const RULES = [
  ["S1", "Comparison weakened", "== became >=, <, != and similar, or a pytest.approx tolerance grew"],
  ["S2", "Expected value made trivial", "== 80.00 became >= 0, is not None, a bare truthiness or isinstance check"],
  ["S3", "Exception widened", "pytest.raises(ValueError) became Exception or an ancestor, or match= was dropped"],
  ["S4", "Parametrize cases removed", "boundary cases quietly dropped from @pytest.mark.parametrize"],
  ["B1", "Test deleted", "a test function or file removed, and not just renamed"],
  ["B2", "Skip or guard added", "skip or xfail markers, pytest.skip(), an early return or if False: before the checks"],
  ["B3", "Assertion removed", "fewer checks than before, with the removed ones unexplained"],
] as const;

export default function AboutPage() {
  return (
    <div className="space-y-16 pt-12">
      <header className="space-y-4">
        <h1 className="text-[2.2rem] font-extrabold leading-tight tracking-tight">How PROVECODE works</h1>
        <p className="prose-measure text-lg text-ink-soft">
          When the same agent writes the code and edits the tests that judge it, “all tests pass” stops meaning much.
          PROVECODE detects test edits that make checks weaker, including the ones that keep the number of assertions
          the same, and ties each one to the approved requirement it was guarding.
        </p>
      </header>

      <section aria-labelledby="steps" className="space-y-5">
        <h2 id="steps" className="text-2xl font-bold tracking-tight">
          From a change to a verdict
        </h2>
        <ol className="prose-measure space-y-4">
          {STEPS.map(([title, text], i) => (
            <li key={title} className="grid grid-cols-[2rem_1fr] gap-2">
              <span className="font-extrabold text-ink-soft">{i + 1}</span>
              <div>
                <p className="font-semibold">{title}</p>
                <p className="text-ink-soft">{text}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="verdicts" className="space-y-6">
        <h2 id="verdicts" className="text-2xl font-bold tracking-tight">
          Three verdicts, kept apart
        </h2>
        <div className="flex flex-wrap gap-8">
          <Stamp outcome="FAILED" />
          <Stamp outcome="INCONCLUSIVE" />
          <Stamp outcome="VERIFIED" />
        </div>
        <p className="prose-measure">
          FAILED outranks INCONCLUSIVE, which outranks VERIFIED. VERIFIED means no mandatory condition failed within the
          declared scope. It is not a proof of correctness. The live detector only reads code, so its results are FAILED
          or INCONCLUSIVE, never VERIFIED.
        </p>
      </section>

      <section aria-labelledby="rules" className="space-y-4">
        <h2 id="rules" className="text-2xl font-bold tracking-tight">
          The rules
        </h2>
        <table className="w-full max-w-4xl text-left text-[0.95rem]">
          <tbody>
            {RULES.map(([id, name, ex]) => (
              <tr key={id} className="border-t border-rule align-top">
                <td className="py-2 pr-4 font-mono font-semibold">{id}</td>
                <td className="py-2 pr-4 font-semibold">{name}</td>
                <td className="py-2 text-ink-soft">{ex}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="prose-measure text-sm text-ink-soft">
          S1 to S4 are the contribution. B1 to B3 are standard checks that existing tools already make; they are included
          for completeness and credited below.
        </p>
      </section>

      <section aria-labelledby="use" className="space-y-4">
        <h2 id="use" className="text-2xl font-bold tracking-tight">
          Use it on your own pull requests
        </h2>
        <p className="prose-measure">
          The same engine runs from the command line on any machine with Python 3.11 or later, and as a GitHub Action.
          The Action reads the contract from the pull request&apos;s base commit, so the change under review cannot edit
          the requirements it is judged by. A pull request that tries is blocked.
        </p>
        <pre className="diff overflow-x-auto rounded-md border border-rule bg-sheet p-4">{`- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- uses: Shriram28Baskar/provecode@main
  with:
    contract: contracts/checkout.yaml`}</pre>
        <p className="prose-measure text-sm text-ink-soft">
          From a terminal: <code className="font-mono">provecode check --base main --head my-branch --contract contracts/checkout.yaml</code>.
          Exit code 1 means FAILED, 2 INCONCLUSIVE, 0 VERIFIED.
        </p>
      </section>

      <section aria-labelledby="prior" className="space-y-4">
        <h2 id="prior" className="text-2xl font-bold tracking-tight">
          Prior art
        </h2>
        <ul className="prose-measure list-disc space-y-2 pl-5">
          <li>
            Human-approved, hash-versioned contracts kept outside the code, contract-owned checks, deterministic
            decisions, explicit inconclusive evidence and an agent gate all exist in{" "}
            <strong>MaruCheck</strong>. Its seal detects that a sealed contract test changed, not how; it does not
            cover ordinary tests and has no Python adapter.
          </li>
          <li>
            Deleted tests, new skip markers and assertion-count drops are detected by caiopizzol/skills PR #99, Refactron
            PR #169, cmux PR #34485, monoharness PR #246 and devkit PR #358. Their authors note that count-preserving
            edits are a gap. The benchmark baseline reimplements their published rules.
          </li>
          <li>Mutation testing (mutmut, Stryker) measures test strength indirectly.</li>
          <li>Protected contract paths rely on GitHub CODEOWNERS and branch protection.</li>
        </ul>
        <p className="prose-measure text-sm text-ink-soft">Statements about other tools are as of November 2026.</p>
      </section>

      <section aria-labelledby="not" className="space-y-4">
        <h2 id="not" className="text-2xl font-bold tracking-tight">
          What it does not claim, and its limits
        </h2>
        <ul className="prose-measure list-disc space-y-2 pl-5">
          <li>It is not the first independent verifier of AI-written code, and it does not prove correctness.</li>
          <li>It does not catch every weakening. Tautologies, swallowed assertions and widened mocks are known blind spots.</li>
          <li>Python and pytest only. Benchmark numbers are from injected cases; real-world measurement is pending.</li>
          <li>Docker narrows what untrusted code can do but is not a complete security boundary.</li>
          <li>
            In recorded runs, a contract check counts as passed only with a test report showing tests that actually ran
            and passed, never on an exit code alone, and the candidate cannot replace the test runner. Code the candidate
            supplies still runs inside the same process as the check, so a deliberately malicious change could forge a
            report. Defending against that needs the check and the code under test in separate processes.
          </li>
          <li>
            The browser runtime (Pyodide 314.0.8) is served from this site, not a third-party CDN. Its loader is checked
            against a pinned SHA-384 and the engine files against SHA-256 before use. The other runtime files come from
            the same deployment and are hashed at build time, but the page does not re-check them as they load.
          </li>
          <li>
            The site&apos;s security policy allows inline scripts, which the static Next.js export needs, and WebAssembly
            compilation, which Pyodide needs. It allows no other origins.
          </li>
          <li>A test that guards no contract condition can only produce advisory findings. Coverage depends on the contract.</li>
        </ul>
      </section>

      <p>
        <Link href="/live/" className="link">
          Try the detector on the checkout change
        </Link>
      </p>
    </div>
  );
}
