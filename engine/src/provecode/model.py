"""Plain data records shared by the core modules (stdlib only)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Rule identifiers (docs/03-build-spec.md section 4).
S1 = "S1"  # comparison weakened
S2 = "S2"  # expected value replaced by a trivial bound
S3 = "S3"  # exception widened
S4 = "S4"  # parametrize cases removed
S5 = "S5"  # check made vacuous (ADR 0011)
B1 = "B1"  # test deleted
B2 = "B2"  # skip / xfail / guard added
B3 = "B3"  # assertion removed
B4 = "B4"  # assertion failure swallowed (ADR 0011)
V0 = "V0"  # expected value changed, strength unchanged (info only)
R0 = "R0"  # test renamed with identical body (info only)
CONTRACT_TOUCHED = "CONTRACT_TOUCHED"

WEAKENING_RULES = frozenset({S1, S2, S3, S4, S5, B1, B2, B3, B4})
INFO_RULES = frozenset({V0, R0})

RULE_TITLES = {
    S1: "Comparison weakened",
    S2: "Expected value replaced by a trivial bound",
    S3: "Exception widened",
    S4: "Parametrize cases removed",
    S5: "Check made vacuous",
    B1: "Test deleted",
    B2: "Skip, xfail or guard added",
    B3: "Assertion removed",
    B4: "Assertion failure swallowed",
    V0: "Expected value changed (strength unchanged)",
    R0: "Test renamed (body unchanged)",
    CONTRACT_TOUCHED: "Candidate modified the contract",
}

BLOCKING = "blocking"
WARNING = "warning"
INFO = "info"


@dataclass
class Finding:
    rule: str
    file: str
    test: str | None
    rationale: str
    base_lines: tuple[int, int] | None = None
    head_lines: tuple[int, int] | None = None
    before: str | None = None
    after: str | None = None
    detail: dict = field(default_factory=dict)
    # Assigned by provecode.contract.link_and_grade:
    id: str = ""
    severity: str = ""
    condition_ids: list[str] = field(default_factory=list)

    def sort_key(self) -> tuple:
        line = (self.base_lines or self.head_lines or (0, 0))[0]
        return (self.file, self.test or "", line, self.rule)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["title"] = RULE_TITLES.get(self.rule, self.rule)
        d["base_lines"] = list(self.base_lines) if self.base_lines else None
        d["head_lines"] = list(self.head_lines) if self.head_lines else None
        return d


@dataclass
class Gap:
    reason: str  # input_limit | unparseable | requires_execution | no_check | timeout | contract_invalid | ...
    detail: str
    file: str | None = None
    condition_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FileResult:
    file: str
    findings: list[Finding] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    tests_base: list[str] = field(default_factory=list)
    tests_head: list[str] = field(default_factory=list)
