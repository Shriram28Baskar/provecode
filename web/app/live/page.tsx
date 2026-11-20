import type { Metadata } from "next";
import { LiveDetector } from "@/components/live-detector";
import { preset } from "@/lib/data";

export const metadata: Metadata = { title: "Try it | PROVECODE" };

export default function LivePage() {
  const presets = [preset("weakened"), preset("legit")];
  return (
    <div className="space-y-10 pt-12">
      <header className="space-y-4">
        <h1 className="text-[2.2rem] font-extrabold leading-tight tracking-tight">Inspect a test change</h1>
        <p className="prose-measure text-lg text-ink-soft">
          Pick a change, edit the candidate&apos;s test if you like, and run the analyzer in your browser. It only reads
          the code. Nothing you type is imported or executed, and nothing leaves this page.
        </p>
        <p className="prose-measure text-sm text-ink-soft">
          Because nothing runs here, requirements that need execution show as not proven. A live result can be FAILED or
          INCONCLUSIVE, but never VERIFIED. The recorded runs supply the execution evidence.
        </p>
      </header>
      <LiveDetector presets={presets} />
    </div>
  );
}
