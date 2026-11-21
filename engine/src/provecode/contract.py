"""Contracts: validation, canonical hashing, approval, linking and severity.

Spec: docs/03-build-spec.md section 3. The JSON path (validate, hash, link,
grade) is stdlib only and runs in the browser. YAML loading needs the `cli`
extra (PyYAML) and is imported lazily.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .model import (ALWAYS_BLOCKING, BLOCKING, CONTRACT_BLOCKING, CONTRACT_TOUCHED, INFO, INFO_RULES, NEVER_BLOCKING,
                    T1, WARNING, WEAKENING_RULES, Finding)

CONDITION_TYPES = {"functional", "boundary", "api-compat", "protected-behavior", "security", "performance"}
CHECK_KINDS = {"pytest", "property", "api", "static"}


class ContractError(Exception):
    """The contract is malformed, unapproved or tampered. Callers record a gap."""


@dataclass
class Condition:
    id: str
    description: str
    type: str
    mandatory: bool
    guarded_by: list[str]
    checks: list[str]


@dataclass
class Contract:
    id: str
    version: int
    conditions: list[Condition]
    checks: list[dict]
    content_hash: str
    approval: dict = field(default_factory=dict)

    @property
    def mandatory(self) -> list[Condition]:
        return [c for c in self.conditions if c.mandatory]

    def condition(self, cid: str) -> Condition | None:
        return next((c for c in self.conditions if c.id == cid), None)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "content_hash": self.content_hash,
            "approved_by": self.approval.get("approved_by"),
            "approved_at": self.approval.get("approved_at"),
            "conditions": [
                {"id": c.id, "description": c.description, "type": c.type, "mandatory": c.mandatory,
                 "guarded_by": list(c.guarded_by), "checks": list(c.checks)}
                for c in self.conditions
            ],
        }


def hashed_content(doc: dict) -> dict:
    return {"contract": doc.get("contract"), "conditions": doc.get("conditions"), "checks": doc.get("checks", [])}


def canonical_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def content_hash(doc: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(hashed_content(doc))).hexdigest()


def _req(cond: bool, msg: str, errors: list[str]) -> None:
    if not cond:
        errors.append(msg)


def validate(doc: dict) -> None:
    errors: list[str] = []
    if not isinstance(doc, dict):
        raise ContractError("contract document must be a mapping")
    meta = doc.get("contract")
    _req(isinstance(meta, dict), "missing 'contract' mapping", errors)
    if isinstance(meta, dict):
        _req(isinstance(meta.get("id"), str) and meta["id"], "contract.id must be a non-empty string", errors)
        _req(isinstance(meta.get("version"), int) and not isinstance(meta.get("version"), bool)
             and meta["version"] >= 1, "contract.version must be an integer >= 1", errors)
    checks = doc.get("checks", [])
    _req(isinstance(checks, list), "'checks' must be a list", errors)
    check_ids = set()
    for i, ch in enumerate(checks if isinstance(checks, list) else []):
        if not isinstance(ch, dict) or not isinstance(ch.get("id"), str):
            errors.append(f"checks[{i}] needs a string id")
            continue
        _req(ch["id"] not in check_ids, f"duplicate check id {ch['id']}", errors)
        check_ids.add(ch["id"])
        _req(ch.get("kind") in CHECK_KINDS, f"check {ch['id']}: kind must be one of {sorted(CHECK_KINDS)}", errors)
        _req(isinstance(ch.get("path"), str), f"check {ch['id']}: path must be a string", errors)
    conds = doc.get("conditions")
    _req(isinstance(conds, list) and conds, "'conditions' must be a non-empty list", errors)
    seen = set()
    for i, c in enumerate(conds if isinstance(conds, list) else []):
        if not isinstance(c, dict) or not isinstance(c.get("id"), str):
            errors.append(f"conditions[{i}] needs a string id")
            continue
        cid = c["id"]
        _req(cid not in seen, f"duplicate condition id {cid}", errors)
        seen.add(cid)
        _req(isinstance(c.get("description"), str) and c["description"], f"{cid}: description required", errors)
        _req(c.get("type") in CONDITION_TYPES, f"{cid}: type must be one of {sorted(CONDITION_TYPES)}", errors)
        _req(isinstance(c.get("mandatory"), bool), f"{cid}: mandatory must be true or false", errors)
        gb = c.get("guarded_by", [])
        _req(isinstance(gb, list) and all(isinstance(x, str) for x in gb), f"{cid}: guarded_by must be a list of test ids", errors)
        cc = c.get("checks", [])
        _req(isinstance(cc, list) and all(isinstance(x, str) for x in cc), f"{cid}: checks must be a list of check ids", errors)
        for ref in cc if isinstance(cc, list) else []:
            _req(ref in check_ids, f"{cid}: unknown check {ref}", errors)
    if errors:
        raise ContractError("; ".join(errors))


def load(doc: dict, *, require_approval: bool = True) -> Contract:
    """Validate, recompute the hash and verify the approval record."""
    validate(doc)
    h = content_hash(doc)
    approval = doc.get("approval") or {}
    if require_approval:
        if not approval.get("approved_by") or not approval.get("approved_at"):
            raise ContractError("contract is not approved (missing approved_by / approved_at)")
        if approval.get("content_hash") != h:
            raise ContractError(
                f"contract content does not match its approval (approved {approval.get('content_hash')}, actual {h})"
            )
    conds = [
        Condition(c["id"], c["description"], c["type"], c["mandatory"], list(c.get("guarded_by", [])),
                  list(c.get("checks", [])))
        for c in doc["conditions"]
    ]
    return Contract(doc["contract"]["id"], doc["contract"]["version"], conds, list(doc.get("checks", [])), h, dict(approval))


def approve(doc: dict, approved_by: str, approved_at: str) -> dict:
    """Return a copy of doc with an approval record over its current content hash."""
    validate(doc)
    out = dict(doc)
    out["approval"] = {"approved_by": approved_by, "approved_at": approved_at, "content_hash": content_hash(doc)}
    return out


def load_yaml(path: str | Path) -> dict:
    try:
        import yaml  # CLI extra only
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ContractError("reading YAML contracts needs the 'cli' extra: pip install provecode[cli]") from exc
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(f"cannot read contract {path}: {exc}") from exc
    return _plain(doc)


def _plain(obj):
    """YAML turns unquoted timestamps into date objects; keep everything JSON-representable."""
    import datetime as _dt

    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    return obj


def parse_text(text: str, name: str) -> dict:
    """A contract from text, e.g. read from a git blob. JSON if name ends in .json, else YAML."""
    if name.endswith(".json"):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ContractError(f"cannot read contract {name}: {exc}") from exc
    try:
        import yaml  # CLI extra only
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ContractError("reading YAML contracts needs the 'cli' extra: pip install provecode[cli]") from exc
    try:
        return _plain(yaml.safe_load(text))
    except yaml.YAMLError as exc:
        raise ContractError(f"cannot read contract {name}: {exc}") from exc


def read_any(path: str | Path) -> dict:
    p = Path(path)
    if p.suffix == ".json":
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"cannot read contract {path}: {exc}") from exc
    return load_yaml(p)


def compile_json(doc: dict) -> bytes:
    """Canonical JSON (with approval and hash) for the browser bundle."""
    c = load(doc)
    out = dict(hashed_content(doc))
    out["approval"] = c.approval
    return json.dumps(out, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


def approved_changes(base: Contract | None, head: Contract) -> set[str]:
    """Condition IDs that carry an approved change in base -> head (spec section 3)."""
    if base is None or head.version <= base.version:
        return set()
    out = set()
    for c in head.conditions:
        b = base.condition(c.id)
        if b is None or b.description != c.description or sorted(b.guarded_by) != sorted(c.guarded_by):
            out.add(c.id)
    return out


def ungraded_severity(f: Finding) -> str:
    """Severity with no valid contract (ADR 0010, amended by ADR 0012): everything is a warning
    except a contract edit and the always-blocking harness rules (H1, H3)."""
    if f.rule == CONTRACT_TOUCHED or (f.rule in ALWAYS_BLOCKING and not f.detail.get("advisory")):
        return BLOCKING
    if f.rule == T1 and f.detail.get("both_changed"):
        return INFO  # inputs and expectation changed together: only notable under a contract
    return WARNING


def link_and_grade(findings: list[Finding], head: Contract, changed: set[str]) -> None:
    """Assign condition_ids and severity in place (spec section 4, first match wins)."""
    guards: dict[str, list[str]] = {}
    for c in head.conditions:
        for t in c.guarded_by:
            guards.setdefault(t, []).append(c.id)
    for f in findings:
        keys = [f.test] if f.test else []
        if f.detail.get("renamed_from"):
            keys.append(f.detail["renamed_from"])
        cids = sorted({cid for k in keys for cid in guards.get(k, [])})
        f.condition_ids = cids
        mandatory = [cid for cid in cids if head.condition(cid).mandatory]
        if f.rule == CONTRACT_TOUCHED or (f.rule in ALWAYS_BLOCKING and not f.detail.get("advisory")):
            f.severity = BLOCKING
        elif f.rule in CONTRACT_BLOCKING or f.rule in ALWAYS_BLOCKING:
            f.severity = BLOCKING  # repository-wide harness weakening under an adopted contract (ADR 0012)
        elif f.rule == T1 and f.detail.get("both_changed") and not mandatory:
            f.severity = INFO
        elif mandatory and all(cid in changed for cid in mandatory):
            f.severity = INFO
            f.detail["approved_contract_change"] = sorted(mandatory)
        elif f.rule in INFO_RULES:
            f.severity = INFO
        elif mandatory and f.rule in WEAKENING_RULES and f.rule not in NEVER_BLOCKING:
            f.severity = BLOCKING
        else:
            f.severity = WARNING
