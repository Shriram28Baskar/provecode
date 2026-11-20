"""Deterministic outcome over (contract, findings, evidence, gaps).

Precedence FAILED > INCONCLUSIVE > VERIFIED (primer section 3.6). Static
integrity findings never count as passing evidence for a condition, so a
static-only run can be FAILED or INCONCLUSIVE but never VERIFIED.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .contract import Contract
from .model import BLOCKING, Finding, Gap

VERIFIED = "VERIFIED"
FAILED = "FAILED"
INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class Evidence:
    id: str
    check_id: str
    condition_ids: list[str]
    owner: str  # "contract" (independent) or "repo" (supporting)
    result: str  # pass | fail | error
    exit_code: int | None = None
    output_excerpt: str = ""
    output_sha256: str = ""
    command: str = ""
    duration_s: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    outcome: str
    reasons: list[dict] = field(default_factory=list)
    condition_status: dict[str, str] = field(default_factory=dict)


def decide(
    contract: Contract | None,
    findings: list[Finding],
    evidence: list[Evidence],
    gaps: list[Gap],
    executed: bool,
) -> tuple[Decision, list[Gap]]:
    """Return the decision and the condition-level gaps it derived."""
    derived: list[Gap] = []
    if contract is None:
        # FAILED still outranks INCONCLUSIVE: a candidate that touched the contract is blocked
        # even when no valid contract could be loaded.
        blocking = [f for f in findings if f.severity == BLOCKING]
        if blocking:
            return Decision(FAILED, [{"kind": "finding", "id": f.id, "text": f"{f.rule} on {f.test or f.file}: "
                                      f"{f.rationale}", "condition_ids": []} for f in blocking]), derived
        reasons = [{"kind": "gap", "id": g.reason, "text": g.detail} for g in gaps if g.condition_id is None]
        return Decision(INCONCLUSIVE, reasons or [{"kind": "gap", "id": "contract_invalid",
                                                    "text": "No valid approved contract."}]), derived

    failed_reasons: list[dict] = []
    for f in findings:
        if f.severity == BLOCKING:
            failed_reasons.append({"kind": "finding", "id": f.id,
                                   "text": f"{f.rule} on {f.test or f.file}: {f.rationale}",
                                   "condition_ids": f.condition_ids})
    status: dict[str, str] = {}
    for c in contract.mandatory:
        ev = [e for e in evidence if c.id in e.condition_ids and e.owner == "contract"]
        if any(e.result == "fail" for e in ev):
            status[c.id] = "fail"
            for e in ev:
                if e.result == "fail":
                    failed_reasons.append({"kind": "evidence", "id": e.id,
                                           "text": f"Contract check {e.check_id} failed for {c.id}.",
                                           "condition_ids": [c.id]})
        elif not c.checks:
            status[c.id] = "gap"
            derived.append(Gap("no_check", f"{c.id} has no contract-owned check.", condition_id=c.id))
        elif not executed:
            status[c.id] = "gap"
            derived.append(Gap("requires_execution",
                               f"{c.id} is verified by execution ({', '.join(c.checks)}); static analysis cannot "
                               "provide passing evidence.", condition_id=c.id))
        elif ev and all(e.result == "pass" for e in ev) and {e.check_id for e in ev} >= set(c.checks):
            status[c.id] = "pass"
        else:
            status[c.id] = "gap"
            missing = sorted(set(c.checks) - {e.check_id for e in ev if e.result == "pass"})
            derived.append(Gap("check_not_passed", f"{c.id}: no passing result for {', '.join(missing)}.",
                               condition_id=c.id))
        # File-level gaps (unparseable, input limit) on a guarded test file.
        for g in gaps:
            if g.file and any(t.split("::")[0] == g.file for t in c.guarded_by) and status[c.id] != "fail":
                status[c.id] = "gap"
                derived.append(Gap(g.reason, f"{c.id}: guarded test file {g.file} could not be analyzed ({g.detail}).",
                                   file=g.file, condition_id=c.id))
    if failed_reasons:
        return Decision(FAILED, failed_reasons, status), derived
    gap_reasons = [{"kind": "gap", "id": g.reason, "text": g.detail, "condition_ids": [g.condition_id]}
                   for g in derived]
    if any(s != "pass" for s in status.values()):
        return Decision(INCONCLUSIVE, gap_reasons, status), derived
    return Decision(VERIFIED, [{"kind": "verified", "id": c.id,
                                "text": f"{c.id}: all contract-owned checks passed; no blocking findings."}
                               for c in contract.mandatory], status), derived
