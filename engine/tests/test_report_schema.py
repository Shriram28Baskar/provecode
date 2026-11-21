"""engine/schema/report.v2.json: committed reports conform, and the validator really rejects bad ones."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from provecode import pipeline
from schema_check import errors

import conftest

ROOT = Path(__file__).resolve().parents[2]


def _report():
    return pipeline.run([("tests/test_a.py", "def test_a():\n    assert f(1) == 2\n",
                          "def test_a():\n    assert f(1) >= 0\n")], None)


def test_a_fresh_report_is_valid():
    assert errors(_report()) == []


@pytest.mark.parametrize("path", sorted((ROOT / "artifacts" / "runs").glob("*/report.json")), ids=lambda p: p.parent.name)
def test_recorded_run_artifacts_are_valid(path):
    assert errors(json.loads(path.read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("outcome"),
    lambda r: r.update(outcome="PASSING"),
    lambda r: r.update(extra=1),
    lambda r: r["findings"][0].update(rule="Z9"),
    lambda r: r["findings"][0].update(severity="fatal"),
    lambda r: r["findings"][0].update(base_lines=[3]),
    lambda r: r.update(schema_version="1"),
    lambda r: r["summary"].update(files_analyzed=-1),
])
def test_the_validator_rejects_broken_reports(mutate):
    r = copy.deepcopy(_report())
    mutate(r)
    assert errors(r)


def test_reports_from_other_tests_are_being_validated():
    _report()
    assert conftest.VALIDATED["count"] >= 1
