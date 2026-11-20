"""Tests for the benchmark statistics and the count-based baseline reimplementation."""

from benchmark import baseline_count as bl
from benchmark.regenerate import mcnemar_exact, wilson


def test_wilson_known_values():
    w = wilson(8, 10)  # reference: 0.4902 to 0.9433 (Wilson, z = 1.96)
    assert (w["lo"], w["hi"]) == (0.4902, 0.9433)
    assert wilson(0, 6)["hi"] == 0.3903
    assert wilson(0, 0)["rate"] is None


def test_mcnemar_exact():
    assert mcnemar_exact(0, 0) is None
    assert mcnemar_exact(3, 3) == 1.0
    assert mcnemar_exact(10, 0) == float(f"{2 / 1024:.3g}")


BASE = "import pytest\ndef test_a():\n    assert f() == 1\n    assert g() == 2\n\ndef test_b():\n    assert h() == 3\n"


def rules(head):
    return sorted({f.rule for f in bl.detect(BASE, head)})


def test_baseline_rules():
    assert rules(None) == ["C1"]
    assert rules(BASE.split("\ndef test_b")[0] + "\n") == ["C2", "C5"]
    assert rules(BASE.replace("def test_a", "@pytest.mark.skip\ndef test_a")) == ["C3"]
    assert rules(BASE.replace("    assert g() == 2\n", "")) == ["C5"]
    assert rules(BASE.replace("def test_a():\n", "def test_a():\n    return\n")) == ["C4"]
    assert rules(BASE + "\n__test__ = False\n") == ["C6"]


def test_baseline_misses_count_preserving_weakening_by_design():
    assert rules(BASE.replace("== 1", ">= 0")) == []
