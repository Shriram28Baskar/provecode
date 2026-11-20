"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Preset, Report } from "@/lib/types";
import { ReportView } from "./report-view";

const TIMEOUT_MS = 2000; // per analysis, architecture section 7.3
const MAX_BYTES = 64 * 1024;

type Status =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "running" }
  | { kind: "done"; ms: number; initMs?: number }
  | { kind: "unavailable"; message: string }
  | { kind: "timeout" };

type Pending = { id: number; resolve: (m: WorkerMsg) => void };
type WorkerMsg =
  | { type: "ready"; id: number; initMs: number }
  | { type: "result"; id: number; report: Report; ms: number }
  | { type: "error"; id: number; message: string };

function timeoutReport(base: Report): Report {
  return {
    ...base,
    mode: "live",
    outcome: "INCONCLUSIVE",
    findings: [],
    evidence: [],
    reasons: [{ kind: "gap", id: "timeout", text: `The analysis took longer than ${TIMEOUT_MS / 1000} s and was stopped.` }],
    gaps: [{ reason: "timeout", detail: `Stopped after ${TIMEOUT_MS / 1000} s; nothing was evaluated.`, file: null, condition_id: null }],
    condition_status: Object.fromEntries(Object.keys(base.condition_status).map((k) => [k, "gap"])),
  };
}

export function LiveDetector({ presets }: { presets: Preset[] }) {
  const [active, setActive] = useState(presets[0].id);
  const preset = presets.find((p) => p.id === active)!;
  const [head, setHead] = useState(preset.head_source);
  const [report, setReport] = useState<Report>(preset.report);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const worker = useRef<Worker | null>(null);
  const pending = useRef<Pending | null>(null);
  const seq = useRef(0);
  const warm = useRef(false);

  const spawn = useCallback(() => {
    worker.current?.terminate();
    const w = new Worker("/worker/analyzer.js", { type: "module" });
    w.onmessage = (ev: MessageEvent<WorkerMsg>) => {
      if (pending.current && ev.data.id === pending.current.id) {
        const p = pending.current;
        pending.current = null;
        p.resolve(ev.data);
      }
    };
    w.onerror = (ev) => {
      ev.preventDefault();
      if (pending.current) {
        const p = pending.current;
        pending.current = null;
        p.resolve({ type: "error", id: p.id, message: ev.message || "The analyzer worker could not start" });
      }
    };
    worker.current = w;
    warm.current = false;
    return w;
  }, []);

  useEffect(() => () => worker.current?.terminate(), []);

  const send = useCallback(
    (msg: object, timeoutMs: number | null): Promise<WorkerMsg | "timeout"> => {
      const w = worker.current ?? spawn();
      const id = ++seq.current;
      return new Promise((resolve) => {
        let timer: ReturnType<typeof setTimeout> | undefined;
        pending.current = {
          id,
          resolve: (m) => {
            if (timer) clearTimeout(timer);
            resolve(m);
          },
        };
        if (timeoutMs !== null) {
          timer = setTimeout(() => {
            pending.current = null;
            worker.current?.terminate(); // MDN: stops immediately, no chance to finish
            worker.current = null;
            resolve("timeout");
          }, timeoutMs);
        }
        w.postMessage({ ...msg, id });
      });
    },
    [spawn],
  );

  async function runLive(text = head) {
    if (new TextEncoder().encode(text).length > MAX_BYTES) {
      setStatus({ kind: "unavailable", message: `The test file is over ${MAX_BYTES / 1024} KiB, the live limit.` });
      return;
    }
    let initMs: number | undefined;
    if (!warm.current) {
      setStatus({ kind: "loading" });
      const r = await send({ type: "init" }, null);
      if (r === "timeout" || r.type === "error") {
        setStatus({ kind: "unavailable", message: r === "timeout" ? "Loading timed out." : r.message });
        worker.current?.terminate();
        worker.current = null;
        return;
      }
      if (r.type === "ready") initMs = r.initMs;
      warm.current = true;
    }
    setStatus({ kind: "running" });
    const r = await send(
      {
        type: "analyze",
        payload: { path: preset.path, base: preset.base_source, head: text, contract_head: preset.contract_head, contract_base: preset.contract_base },
      },
      TIMEOUT_MS,
    );
    if (r === "timeout") {
      setReport(timeoutReport(preset.report));
      setStatus({ kind: "timeout" });
      return;
    }
    if (r.type === "error") {
      setStatus({ kind: "unavailable", message: r.message });
      return;
    }
    if (r.type === "result") {
      setReport(r.report);
      setStatus({ kind: "done", ms: r.ms, initMs });
    }
  }

  function choose(id: string) {
    const p = presets.find((x) => x.id === id)!;
    setActive(id);
    setHead(p.head_source);
    setReport(p.report);
    if (status.kind !== "unavailable") setStatus({ kind: "idle" });
  }

  const statusText = (() => {
    switch (status.kind) {
      case "loading":
        return "Loading the analyzer into this browser. This happens once per visit…";
      case "running":
        return "Analyzing…";
      case "done":
        return `Analyzed in ${status.ms} ms${status.initMs ? ` after a ${(status.initMs / 1000).toFixed(1)} s first load` : ""}.`;
      case "timeout":
        return "Stopped after 2 s. The result below is a gap, not a pass.";
      case "unavailable":
        return `Live analysis unavailable: ${status.message} The precomputed result is still shown.`;
      default:
        return "Showing the result computed at build time. Edit the test or run it live.";
    }
  })();

  return (
    <div className="space-y-10">
      <fieldset className="flex flex-wrap gap-3">
        <legend className="mb-2 text-sm text-ink-soft">Choose a change to inspect</legend>
        {presets.map((p) => (
          <label
            key={p.id}
            className={`cursor-pointer rounded-md border px-4 py-3 text-left transition-colors ${
              p.id === active ? "border-ink bg-sheet" : "border-rule hover:border-ink-soft"
            }`}
          >
            <input type="radio" name="preset" value={p.id} checked={p.id === active} onChange={() => choose(p.id)} className="sr-only" />
            <span className="block font-semibold">{p.title}</span>
            <span className="block max-w-sm text-sm text-ink-soft">{p.summary}</span>
          </label>
        ))}
      </fieldset>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="min-w-0 space-y-2">
          <p className="text-sm font-semibold">Before: {preset.path} on main</p>
          <pre className="diff h-[21rem] overflow-auto rounded-md border border-rule bg-sheet p-3">{preset.base_source}</pre>
        </div>
        <div className="min-w-0 space-y-2">
          <label htmlFor="head" className="text-sm font-semibold">
            After: the candidate&apos;s version (editable)
          </label>
          <textarea
            id="head"
            value={head}
            spellCheck={false}
            onChange={(e) => setHead(e.target.value)}
            className="diff h-[21rem] w-full resize-y rounded-md border border-ink-soft bg-sheet p-3"
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={() => runLive()}
          disabled={status.kind === "loading" || status.kind === "running"}
          className="rounded-md bg-blue px-5 py-2.5 font-semibold text-sheet disabled:opacity-60"
        >
          {status.kind === "loading" ? "Loading analyzer…" : "Run it live"}
        </button>
        <button type="button" onClick={() => setHead(preset.head_source)} className="rounded-md border border-rule px-4 py-2.5">
          Reset the test
        </button>
        <p role="status" aria-live="polite" className="text-sm text-ink-soft">
          {statusText}
        </p>
      </div>

      <ReportView report={report} />
    </div>
  );
}
