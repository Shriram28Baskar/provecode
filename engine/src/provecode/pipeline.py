"""The one analysis entry point shared by the CLI, the benchmark and the browser.

Stdlib only and host independent: callers pass source strings and contract
dicts in and receive a report dict out.
"""

from __future__ import annotations

import platform

from . import __version__
from . import analyzer, contract as contract_mod, decide as decide_mod, harness, report as report_mod
from .changeset import Changeset
from .decide import Evidence
from .limits import Limits
from .model import CONTRACT_TOUCHED, FileResult, Finding, Gap


def run(
    files: list[tuple[str, str | None, str | None]] | Changeset,
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
    """Analyze a change against an approved contract.

    `files` is either a Changeset (test files plus the harness, config, CI and source files the
    H rules read; ADR 0012) or the original list of (path, base_source, head_source[, old_path])
    test-file tuples, which is analyzed exactly as before.
    """
    limits = limits or Limits.cli()
    changeset = files if isinstance(files, Changeset) else None
    if changeset is not None:
        files = changeset.test_entries()
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
    for entry in sorted(files, key=lambda x: x[0]):
        # (path, base, head) or (path, base, head, old_path) for a rename.
        path, b, h = entry[0], entry[1], entry[2]
        old = entry[3] if len(entry) > 3 else None
        if old and old != path and analyzer.is_test_path(old):
            # Keep the base's test ids so contract guarded_by links still resolve. If the new name
            # would no longer be collected by default runners (or is not test code at all), the
            # tests stop running: treat the file as deleted (B1).
            keep = analyzer.is_test_path(path) and not (
                analyzer.is_collected_name(old) and not analyzer.is_collected_name(path))
            r = analyzer.analyze_file(old, b, h if keep else None, limits)
            for f in r.findings:
                f.detail["renamed_to"] = path
                if not keep and f.rule == "B1":
                    f.rationale = (f"The test file was renamed to {path}, which pytest and unittest "
                                   "discovery do not collect by default.")
            r.file = f"{old} -> {path}"
        elif analyzer.is_test_path(path):
            r = analyzer.analyze_file(path, b, h, limits)
        else:
            continue
        results.append(r)
        findings.extend(r.findings)
        gaps.extend(r.gaps)
    if changeset is not None:
        h_findings, h_gaps, analyzed = harness.analyze(changeset, limits)
        findings.extend(h_findings)
        gaps.extend(h_gaps)
        seen = {r.file for r in results}
        results.extend(FileResult(p) for p in analyzed if p not in seen)
        results.sort(key=lambda r: r.file)
    for p in sorted(touched_protected or []):
        findings.append(Finding(CONTRACT_TOUCHED, p, None,
                                "The candidate change modifies a protected contract path; the contract must not be "
                                "editable by the change it judges.", detail={"path": p}))
    if head is not None:
        contract_mod.link_and_grade(findings, head, contract_mod.approved_changes(base, head))
    else:
        for f in findings:
            f.severity = contract_mod.ungraded_severity(f)
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
