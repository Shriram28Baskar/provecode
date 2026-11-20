"""The one analysis entry point shared by the CLI, the benchmark and the browser.

Stdlib only and host independent: callers pass source strings and contract
dicts in and receive a report dict out.
"""

from __future__ import annotations

import platform

from . import __version__
from . import analyzer, contract as contract_mod, decide as decide_mod, report as report_mod
from .decide import Evidence
from .limits import Limits
from .model import CONTRACT_TOUCHED, Finding, Gap


def run(
    files: list[tuple[str, str | None, str | None]],
    contract_head: dict | None,
    contract_base: dict | None = None,
    *,
    mode: str = "static",
    limits: Limits | None = None,
    touched_protected: list[str] | None = None,
    evidence: list[Evidence] | None = None,
    executed: bool = False,
    extra_gaps: list[Gap] | None = None,
    provenance: dict | None = None,
) -> dict:
    """Analyze (path, base_source, head_source) test files against an approved contract."""
    limits = limits or Limits.cli()
    gaps: list[Gap] = list(extra_gaps or [])
    head = base = None
    try:
        head = contract_mod.load(contract_head) if contract_head is not None else None
        if head is None:
            gaps.append(Gap("contract_invalid", "No contract was supplied."))
    except contract_mod.ContractError as exc:
        gaps.append(Gap("contract_invalid", str(exc)))
    if contract_base is not None and head is not None:
        try:
            base = contract_mod.load(contract_base)
        except contract_mod.ContractError as exc:
            gaps.append(Gap("contract_invalid", f"base contract: {exc}"))

    results = []
    findings: list[Finding] = []
    for path, b, h in sorted(files, key=lambda x: x[0]):
        if not analyzer.is_test_path(path):
            continue
        r = analyzer.analyze_file(path, b, h, limits)
        results.append(r)
        findings.extend(r.findings)
        gaps.extend(r.gaps)
    for p in sorted(touched_protected or []):
        findings.append(Finding(CONTRACT_TOUCHED, p, None,
                                "The candidate change modifies a protected contract path; the contract must not be "
                                "editable by the change it judges.", detail={"path": p}))
    if head is not None:
        contract_mod.link_and_grade(findings, head, contract_mod.approved_changes(base, head))
    else:
        for f in findings:
            f.severity = "blocking" if f.rule == CONTRACT_TOUCHED else "warning"
    findings.sort(key=Finding.sort_key)
    for i, f in enumerate(findings, start=1):
        f.id = f"F{i}"

    decision, derived = decide_mod.decide(head, findings, evidence or [], gaps, executed)
    prov = {"engine_version": __version__, "python": platform.python_version(),
            "limits": limits.to_dict(), **(provenance or {})}
    return report_mod.build(
        mode=mode, contract=head, contract_base=base, decision=decision, findings=findings,
        evidence=evidence or [], gaps=gaps + derived, files=results, provenance=prov,
    )
