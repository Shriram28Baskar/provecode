"""Contract integrity, severity grading, the decision function and the report shape."""

from __future__ import annotations

import copy

import pytest

from provecode import contract as C
from provecode import pipeline
from provecode.decide import Evidence

P = "tests/test_pricing.py"
BASE = "def test_cap():\n    assert apply_discount(100, 0.5) == 80.0\n\ndef test_other():\n    assert f() == 1\n"
WEAK = BASE.replace("== 80.0", ">= 0")
LEGIT = BASE.replace("== 80.0", "== 75.0")


def doc(version=1, desc="Discount is capped at 20%", checks=True):
    d = {
        "contract": {"id": "checkout", "version": version},
        "conditions": [{
            "id": "C1", "description": desc, "type": "boundary", "mandatory": True,
            "guarded_by": [f"{P}::test_cap"], "checks": ["chk-cap"] if checks else [],
        }],
        "checks": [{"id": "chk-cap", "kind": "pytest", "path": "t.py::test_cap"}] if checks else [],
    }
    return C.approve(d, "owner", "2026-11-20T00:00:00Z")


# ---------------------------------------------------------------- contract integrity


def test_hash_is_stable_and_ignores_key_order_and_approval():
    a = doc()
    b = copy.deepcopy(a)
    b["conditions"][0] = dict(reversed(list(b["conditions"][0].items())))
    b["approval"]["approved_by"] = "someone-else"
    assert C.content_hash(a) == C.content_hash(b)


def test_tampered_contract_is_rejected():
    d = doc()
    d["conditions"][0]["description"] = "Discount is capped at 90%"
    with pytest.raises(C.ContractError, match="does not match its approval"):
        C.load(d)


def test_unapproved_contract_is_rejected():
    d = doc()
    del d["approval"]
    with pytest.raises(C.ContractError, match="not approved"):
        C.load(d)


@pytest.mark.parametrize("mutate, msg", [
    (lambda d: d["conditions"][0].update(type="vibes"), "type must be"),
    (lambda d: d["conditions"][0].update(checks=["nope"]), "unknown check"),
    (lambda d: d["conditions"].append(dict(d["conditions"][0])), "duplicate condition"),
    (lambda d: d["contract"].update(version=0), "version"),
])
def test_schema_errors(mutate, msg):
    d = doc()
    mutate(d)
    with pytest.raises(C.ContractError, match=msg):
        C.validate(d)


def test_approved_change_detection():
    v1, v2 = C.load(doc(1)), C.load(doc(2, "Discount is capped at 25%"))
    assert C.approved_changes(v1, v2) == {"C1"}
    assert C.approved_changes(v1, C.load(doc(1))) == set()
    # Same text, higher version: nothing about C1 changed.
    assert C.approved_changes(v1, C.load(doc(2))) == set()


# ---------------------------------------------------------------- pipeline outcomes


def test_weakening_on_guarded_test_is_blocking_and_failed():
    r = pipeline.run([(P, BASE, WEAK)], doc())
    assert r["outcome"] == "FAILED"
    f = r["findings"][0]
    assert (f["rule"], f["severity"], f["condition_ids"]) == ("S2", "blocking", ["C1"])
    assert r["reasons"][0]["id"] == f["id"]


def test_weakening_on_unguarded_test_is_warning_only():
    r = pipeline.run([(P, BASE, BASE.replace("== 1", ">= 0"))], doc())
    assert [f["severity"] for f in r["findings"]] == ["warning"]
    assert r["outcome"] == "INCONCLUSIVE"  # static: C1 needs execution


def test_static_never_verified_even_when_clean():
    r = pipeline.run([(P, BASE, BASE)], doc())
    assert r["outcome"] == "INCONCLUSIVE"
    assert {g["reason"] for g in r["gaps"]} == {"requires_execution"}


def test_condition_without_checks_is_a_gap():
    r = pipeline.run([(P, BASE, BASE)], doc(checks=False), executed=True)
    assert r["outcome"] == "INCONCLUSIVE"
    assert [g["reason"] for g in r["gaps"]] == ["no_check"]


def test_legit_change_under_approved_contract_is_not_blocking():
    r = pipeline.run([(P, BASE, LEGIT)], doc(2, "Discount is capped at 25%"), doc(1))
    f = r["findings"][0]
    assert (f["rule"], f["severity"]) == ("V0", "info")
    assert f["detail"]["approved_contract_change"] == ["C1"]
    assert r["outcome"] == "INCONCLUSIVE"


def test_weakening_under_approved_change_is_info_not_blocking():
    r = pipeline.run([(P, BASE, WEAK)], doc(2, "Discount is capped at 25%"), doc(1))
    assert r["findings"][0]["severity"] == "info"


def ev(result, check="chk-cap"):
    return Evidence("E1", check, ["C1"], "contract", result, 0 if result == "pass" else 1)


def test_recorded_pass_is_verified():
    r = pipeline.run([(P, BASE, LEGIT)], doc(2, "x"), doc(1), mode="recorded", evidence=[ev("pass")], executed=True)
    assert r["outcome"] == "VERIFIED"
    assert r["condition_status"] == {"C1": "pass"}


def test_recorded_fail_is_failed_even_without_findings():
    r = pipeline.run([(P, BASE, BASE)], doc(), mode="recorded", evidence=[ev("fail")], executed=True)
    assert r["outcome"] == "FAILED"
    assert r["reasons"][0]["kind"] == "evidence"


def test_repo_owned_evidence_is_not_sufficient():
    e = ev("pass")
    e.owner = "repo"
    r = pipeline.run([(P, BASE, BASE)], doc(), mode="recorded", evidence=[e], executed=True)
    assert r["outcome"] == "INCONCLUSIVE"


def test_error_evidence_is_gap_not_pass():
    r = pipeline.run([(P, BASE, BASE)], doc(), mode="recorded", evidence=[ev("error")], executed=True)
    assert r["outcome"] == "INCONCLUSIVE"


def test_contract_touched_is_blocking():
    r = pipeline.run([(P, BASE, BASE)], doc(), touched_protected=["contracts/checkout.yaml"])
    assert r["outcome"] == "FAILED"
    assert r["findings"][0]["rule"] == "CONTRACT_TOUCHED"


def test_tampered_contract_gives_inconclusive_with_gap():
    d = doc()
    d["conditions"][0]["mandatory"] = False
    r = pipeline.run([(P, BASE, WEAK)], d)
    assert r["outcome"] == "INCONCLUSIVE"
    assert r["gaps"][0]["reason"] == "contract_invalid"


def test_unparseable_guarded_file_is_inconclusive_not_verified():
    r = pipeline.run([(P, BASE, "def test_cap(:\n")], doc(), evidence=[ev("pass")], executed=True, mode="recorded")
    assert r["outcome"] == "INCONCLUSIVE"
    assert "unparseable" in {g["reason"] for g in r["gaps"]}


def test_failed_beats_inconclusive():
    r = pipeline.run([(P, BASE, WEAK), ("tests/test_b.py", "def test_z(:\n", "x")], doc())
    assert r["outcome"] == "FAILED"


def test_report_shape():
    r = pipeline.run([(P, BASE, WEAK)], doc(), provenance={"base_sha": "a" * 40, "head_sha": "b" * 40})
    for key in ("schema_version", "mode", "outcome", "reasons", "contract", "findings", "evidence", "gaps",
                "provenance", "summary", "condition_status"):
        assert key in r
    assert r["schema_version"] == "1"
    assert r["provenance"]["base_sha"] == "a" * 40
    f = r["findings"][0]
    for key in ("id", "rule", "title", "severity", "file", "test", "base_lines", "head_lines", "before", "after",
                "condition_ids", "rationale"):
        assert key in f


def test_non_test_files_are_ignored():
    r = pipeline.run([("checkout/pricing.py", "x = 1\n", "x = 2\n")], doc())
    assert r["files"] == []
