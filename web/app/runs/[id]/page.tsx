import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ReportView } from "@/components/report-view";
import { RUNS, run } from "@/lib/data";

const TITLES: Record<string, { title: string; blurb: string }> = {
  "checkout-weakened": {
    title: "Recorded run: cap removed, test weakened",
    blurb: "Contract v1 says the discount is capped at 20%. The candidate removed the cap and loosened the test.",
  },
  "checkout-legit": {
    title: "Recorded run: cap raised to 25% under contract v2",
    blurb: "The business approved contract v2 first. The candidate raised the cap and updated the test to match.",
  },
};

export function generateStaticParams() {
  return RUNS.map((id) => ({ id }));
}

export const dynamicParams = false;

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  return { title: `${TITLES[id]?.title ?? "Run"} | PROVECODE` };
}

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!(RUNS as readonly string[]).includes(id)) notFound();
  const report = run(id);
  const other = RUNS.find((r) => r !== id)!;
  return (
    <div className="space-y-10 pt-12">
      <header className="space-y-3">
        <h1 className="text-[2.2rem] font-extrabold leading-tight tracking-tight">{TITLES[id].title}</h1>
        <p className="prose-measure text-lg text-ink-soft">{TITLES[id].blurb}</p>
        <p className="text-sm">
          <Link href={`/runs/${other}/`} className="link">
            Compare with: {TITLES[other].title.replace("Recorded run: ", "")}
          </Link>
        </p>
      </header>
      <ReportView report={report} />
    </div>
  );
}
