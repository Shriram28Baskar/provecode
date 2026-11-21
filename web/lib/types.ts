export type Outcome = "VERIFIED" | "FAILED" | "INCONCLUSIVE";
export type Mode = "static" | "live" | "precomputed" | "recorded";

export interface Finding {
  id: string;
  rule: string;
  title: string;
  severity: "blocking" | "warning" | "info";
  file: string;
  test: string | null;
  base_lines: [number, number] | null;
  head_lines: [number, number] | null;
  before: string | null;
  after: string | null;
  condition_ids: string[];
  rationale: string;
  detail: Record<string, unknown>;
}

export interface Evidence {
  id: string;
  check_id: string;
  condition_ids: string[];
  owner: "contract" | "repo";
  result: "pass" | "fail" | "error";
  exit_code: number | null;
  output_excerpt: string;
  output_sha256: string;
  command: string;
  duration_s: number | null;
}

export interface Gap {
  reason: string;
  detail: string;
  file: string | null;
  condition_id: string | null;
}

export interface Condition {
  id: string;
  description: string;
  type: string;
  mandatory: boolean;
  guarded_by: string[];
  checks: string[];
}

export interface Report {
  schema_version: string;
  mode: Mode;
  outcome: Outcome;
  reasons: { kind: string; id: string; text: string; condition_ids?: string[] }[];
  condition_status: Record<string, string>;
  contract: {
    id: string;
    version: number;
    content_hash: string;
    approved_by: string | null;
    approved_at: string | null;
    conditions: Condition[];
  } | null;
  contract_base: { version: number; content_hash: string } | null;
  findings: Finding[];
  evidence: Evidence[];
  gaps: Gap[];
  provenance: Record<string, unknown>;
}

export interface Preset {
  id: string;
  title: string;
  summary: string;
  path: string;
  base_source: string;
  head_source: string;
  contract_head: unknown;
  contract_base: unknown;
  report: Report;
}

export interface Interval {
  k: number;
  n: number;
  rate: number | null;
  lo: number | null;
  hi: number | null;
}

export interface Paired {
  only_provecode: number;
  only_baseline: number;
  mcnemar_exact_p: number | null;
}

export interface SplitMetrics {
  n_cases: number;
  n_weakening: number;
  n_legit: number;
  n_count_preserving: number;
  recall: { provecode: Interval; baseline: Interval; paired: Paired };
  recall_count_preserving: { provecode: Interval; baseline: Interval; paired: Paired };
  recall_not_count_preserving: { provecode: Interval; baseline: Interval; paired: Paired };
  legit_flagged: { provecode: Interval; baseline: Interval };
  legit_blocking: { provecode: Interval };
  precision_on_tierA_mix: { provecode: Interval; baseline: Interval };
  by_category: Record<string, { provecode: Interval; baseline: Interval }>;
  by_pattern: Record<string, { kind: string; category: string; n: number; provecode_flagged: number; baseline_flagged: number }>;
}

export interface TierA {
  tier: string;
  label: string;
  held_out: SplitMetrics;
  tuning: SplitMetrics;
  all: SplitMetrics;
  falsification: {
    F1: { statement: string; supported: boolean; mcnemar_exact_p: number | null };
    F2: { statement: string; baseline_outperforms: boolean };
    F3: { statement: string; provecode: Interval };
  };
  tuning_log: { round: number; rule: string; change: string; evidence: string[]; split_used: string }[];
  revision_log: { date: string; source: string; changes: string[]; effect_on_tier_a: string }[];
  baseline_rules: Record<string, string>;
  detection_definition: { provecode: string; baseline: string };
  provenance: {
    engine_version: string;
    engine_source_sha256: string;
    cases_lock_sha256: string;
    split_lock_sha256: string;
    python: string;
    sources: { repo: string; sha: string; licenses: string[] }[];
    commit_order: Record<string, { commit: string; committed_at: string } | null>;
    command: string;
  };
}

export interface Perf {
  machine: Record<string, string>;
  per_case: { n: number; median_s: number; p95_s: number; max_s: number; max_peak_bytes: number };
  worst_case_live_limits: Record<string, { bytes: number; seconds: number; peak_bytes: number; findings: number; gaps: string[] }>;
  live_limits: Record<string, number>;
}

export interface Parity {
  pyodide_version: string;
  pyodide_python: string;
  cpython_reference: string;
  cases_compared: number;
  tiers?: { tier: string; cases_compared: number }[];
  mismatches: unknown[];
  identical: boolean;
}
