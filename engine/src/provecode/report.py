"""Versioned JSON report (canonical) and its Markdown rendering. Stdlib only."""

from __future__ import annotations

import json
from typing import Any

from .contract import Contract
from .decide import Decision, Evidence
from .model import RULE_TITLES, FileResult, Finding, Gap

SCHEMA_VERSION = "2"  # ADR 0012/0013: H, T and X rules, execution evidence; engine/schema/report.v2.json
MODES = ("static", "live", "precomputed", "recorded")


def build(
    *,
    mode: str,
    contract: Contract | None,
    contract_base: Contract | None,
    decision: Decision,
    findings: list[Finding],
    evidence: list[Evidence],
    gaps: list[Gap],
    files: list[FileResult],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "outcome": decision.outcome,
        "reasons": decision.reasons,
        "condition_status": decision.condition_status,
        "contract": contract.summary() if contract else None,
        "contract_base": {"version": contract_base.version, "content_hash": contract_base.content_hash}
        if contract_base else None,
        "summary": {
            "files_analyzed": len(files),
            "findings_by_severity": dict(sorted(counts.items())),
            "gaps": len(gaps),
            "evidence": len(evidence),
        },
        "findings": [f.to_dict() for f in findings],
        "evidence": [e.to_dict() for e in evidence],
        "gaps": [g.to_dict() for g in gaps],
        "files": [{"file": r.file, "tests_base": len(r.tests_base), "tests_head": len(r.tests_head)} for r in files],
        "provenance": provenance,
    }


def dumps(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


_ICON = {"VERIFIED": "VERIFIED", "FAILED": "FAILED", "INCONCLUSIVE": "INCONCLUSIVE"}


def to_markdown(r: dict[str, Any]) -> str:
    out = [f"## PROVECODE: **{r['outcome']}** ({r['mode']})", ""]
    c = r.get("contract")
    if c:
        out.append(f"Contract `{c['id']}` v{c['version']} `{c['content_hash'][:19]}…`, approved by "
                   f"{c.get('approved_by') or 'n/a'}.")
        out.append("")
    if r["reasons"]:
        out.append("**Why**")
        out += [f"- {x['text']}" for x in r["reasons"]]
        out.append("")
    shown = [f for f in r["findings"] if f["severity"] != "info"] or r["findings"]
    if shown:
        out += ["| Severity | Rule | Test | Conditions |", "|---|---|---|---|"]
        for f in shown:
            out.append(f"| {f['severity']} | {f['rule']} {RULE_TITLES.get(f['rule'], '')} | `{f['test'] or f['file']}` "
                       f"| {', '.join(f['condition_ids']) or '-'} |")
        out.append("")
        for f in shown:
            if f.get("before") or f.get("after"):
                out += [f"`{f['test'] or f['file']}`: {f['rationale']}", "```diff"]
                out += [f"- {line}" for line in (f.get("before") or "").splitlines()]
                out += [f"+ {line}" for line in (f.get("after") or "").splitlines()]
                out += ["```", ""]
    if r["gaps"]:
        out.append("**Gaps (not evaluated)**")
        out += [f"- `{g['reason']}`: {g['detail']}" for g in r["gaps"]]
        out.append("")
    p = r["provenance"]
    out.append(f"<sub>base `{str(p.get('base_sha', '-'))[:12]}` · head `{str(p.get('head_sha', '-'))[:12]}` · "
               f"provecode {p.get('engine_version')} · Python {p.get('python')}</sub>")
    return "\n".join(out) + "\n"
