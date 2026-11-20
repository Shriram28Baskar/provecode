"""Rule tests: a true positive, a legitimate-change negative and edge cases per rule.

Fixtures are synthetic or the primer's checkout example. Benchmark cases are
never used here (they are evaluation data).
"""

from __future__ import annotations

import textwrap

import pytest

from provecode.analyzer import analyze_file, is_test_path
from provecode.limits import Limits

P = "tests/test_x.py"


def run(base: str | None, head: str | None, lim: Limits | None = None):
    b = textwrap.dedent(base) if base is not None else None
    h = textwrap.dedent(head) if head is not None else None
    return analyze_file(P, b, h, lim)


def rules(base, head):
    return sorted(f.rule for f in run(base, head).findings)


BASE = """
import pytest
from checkout.pricing import apply_discount

def test_cap():
    assert apply_discount(100, 0.50) == 80.00
"""


# ---------------------------------------------------------------- the canonical case


def test_checkout_weakening_is_s2_and_cites_lines():
    res = run(BASE, BASE.replace("== 80.00", ">= 0"))
    assert [f.rule for f in res.findings] == ["S2"]
    f = res.findings[0]
    assert f.test == "tests/test_x.py::test_cap"
    assert f.before == "assert apply_discount(100, 0.50) == 80.00"
    assert f.after == "assert apply_discount(100, 0.50) >= 0"
    assert f.base_lines == (6, 6) and f.head_lines == (6, 6)


def test_checkout_legit_value_change_is_v0_only():
    assert rules(BASE, BASE.replace("== 80.00", "== 75.00")) == ["V0"]


def test_unchanged_file_has_no_findings():
    assert rules(BASE, BASE) == []


# ---------------------------------------------------------------- S1


@pytest.mark.parametrize("op", [">=", "<=", "<", ">", "!="])
def test_s1_operator_downgrade(op):
    base = "def test_a():\n    assert f(x) == g(y)\n"
    assert rules(base, base.replace("==", op)) == ["S1"]


def test_s1_equality_to_approx():
    base = "import pytest\ndef test_a():\n    assert f() == 3.0\n"
    assert rules(base, base.replace("== 3.0", "== pytest.approx(3.0, rel=0.5)")) == ["S1"]


def test_s1_approx_tolerance_increase():
    base = "import pytest\ndef test_a():\n    assert f() == pytest.approx(3.0, rel=0.01)\n"
    assert rules(base, base.replace("rel=0.01", "rel=0.2")) == ["S1"]


def test_s1_negative_tolerance_tightened():
    base = "import pytest\ndef test_a():\n    assert f() == pytest.approx(3.0, rel=0.2)\n"
    assert rules(base, base.replace("rel=0.2", "rel=0.01")) == []


def test_s1_negative_strengthened():
    base = "def test_a():\n    assert f(x) >= g(y)\n"
    assert rules(base, base.replace(">=", "==")) == []


def test_s1_negative_operands_swapped():
    base = "def test_a():\n    assert f(x) == 4\n"
    assert rules(base, base.replace("f(x) == 4", "4 == f(x)")) == []


def test_s1_flipped_literal_side_normalised():
    base = "def test_a():\n    assert 4 == f(x)\n"
    assert rules(base, base.replace("4 == f(x)", "f(x) >= 4")) == ["S1"]


# ---------------------------------------------------------------- S2


@pytest.mark.parametrize(
    "head",
    ["assert f(x) >= 0", "assert f(x) > 0", "assert f(x) is not None", "assert f(x) != None",
     "assert f(x)", "assert isinstance(f(x), int)", "assert f(x) >= 3"],
)
def test_s2_trivial_bounds(head):
    base = "def test_a():\n    assert f(x) == 42\n"
    assert rules(base, base.replace("assert f(x) == 42", head)) == ["S2"]


def test_s2_bound_equal_to_expected_is_s1_not_s2():
    base = "def test_a():\n    assert f(x) == 42\n"
    assert rules(base, base.replace("== 42", ">= 42")) == ["S1"]


def test_s2_negative_message_added():
    base = "def test_a():\n    assert f(x) == 42\n"
    assert rules(base, base.replace("== 42", '== 42, "msg"')) == []


def test_s2_negative_extra_weak_assert_added():
    base = "def test_a():\n    assert f(x) == 42\n"
    head = "def test_a():\n    assert f(x) is not None\n    assert f(x) == 42\n"
    assert rules(base, head) == []


# ---------------------------------------------------------------- S3


def test_s3_widened_to_exception():
    base = "import pytest\ndef test_a():\n    with pytest.raises(ValueError):\n        f()\n"
    assert rules(base, base.replace("ValueError", "Exception")) == ["S3"]


def test_s3_widened_to_builtin_ancestor():
    base = "import pytest\ndef test_a():\n    with pytest.raises(KeyError):\n        f()\n"
    assert rules(base, base.replace("KeyError", "LookupError")) == ["S3"]


def test_s3_tuple_widened():
    base = "import pytest\ndef test_a():\n    with pytest.raises(ValueError):\n        f()\n"
    assert rules(base, base.replace("ValueError", "(ValueError, Exception)")) == ["S3"]


def test_s3_match_removed():
    base = 'import pytest\ndef test_a():\n    with pytest.raises(ValueError, match="bad"):\n        f()\n'
    assert rules(base, base.replace(', match="bad"', "")) == ["S3"]


def test_s3_negative_narrowed_and_match_added():
    base = "import pytest\ndef test_a():\n    with pytest.raises(Exception):\n        f()\n"
    assert rules(base, base.replace("Exception)", 'ValueError, match="x")')) == []


def test_s3_negative_project_exception_swap():
    base = "import pytest\ndef test_a():\n    with pytest.raises(FooError):\n        f()\n"
    assert rules(base, base.replace("FooError", "BarError")) == []


# ---------------------------------------------------------------- S4


PARAM = """
import pytest

@pytest.mark.parametrize("x, y", [(1, 2), (0, 0), (-1, -2)])
def test_a(x, y):
    assert f(x) == y
"""


def test_s4_case_removed():
    res = run(PARAM, PARAM.replace(", (-1, -2)", ""))
    assert [f.rule for f in res.findings] == ["S4"]
    assert res.findings[0].detail["removed_cases"] == ["(-1, -2)"]


def test_s4_negative_case_added():
    assert rules(PARAM, PARAM.replace("(-1, -2)]", "(-1, -2), (5, 10)]")) == []


def test_s4_case_swapped_for_another_is_still_a_removal():
    res = run(PARAM, PARAM.replace("(-1, -2)", "(-2, -4)"))
    assert [f.rule for f in res.findings] == ["S4"]
    assert "1 new case(s) were added" in res.findings[0].rationale


# ---------------------------------------------------------------- B1 / R0


TWO = """
def test_a():
    assert f() == 1

def test_b():
    assert g() == 2
"""


def test_b1_function_deleted():
    res = run(TWO, "def test_a():\n    assert f() == 1\n")
    assert [(f.rule, f.test) for f in res.findings] == [("B1", "tests/test_x.py::test_b")]


def test_b1_file_deleted():
    res = run(TWO, None)
    assert [f.rule for f in res.findings] == ["B1", "B1"]
    assert all(f.detail.get("file_deleted") for f in res.findings)


def test_r0_rename_is_info_not_deletion():
    assert rules(TWO, TWO.replace("def test_b", "def test_b_renamed")) == ["R0"]


def test_new_file_and_new_test_have_no_findings():
    assert rules(None, TWO) == []
    assert rules(TWO, TWO + "\ndef test_c():\n    assert h() == 3\n") == []


def test_class_methods_are_keyed_by_class():
    base = "class TestX:\n    def test_a(self):\n        assert f() == 1\n"
    res = run(base, base.replace("== 1", ">= 0"))
    assert res.findings[0].test == "tests/test_x.py::TestX::test_a"


# ---------------------------------------------------------------- B2


@pytest.mark.parametrize(
    "deco",
    ['@pytest.mark.skip(reason="x")', "@pytest.mark.skip", "@pytest.mark.xfail", "@pytest.mark.skipif(True, reason='x')"],
)
def test_b2_marker_added(deco):
    base = "import pytest\ndef test_a():\n    assert f() == 1\n"
    assert rules(base, base.replace("def test_a", deco + "\ndef test_a")) == ["B2"]


def test_b2_negative_conditional_skipif():
    base = "import pytest, sys\ndef test_a():\n    assert f() == 1\n"
    assert rules(base, base.replace("def test_a", '@pytest.mark.skipif(sys.platform == "win32", reason="x")\ndef test_a')) == []


@pytest.mark.parametrize(
    "head_body",
    ['    pytest.skip("x")\n    assert f() == 1\n', "    return\n    assert f() == 1\n", "    if False:\n        assert f() == 1\n"],
)
def test_b2_guard_added(head_body):
    base = "import pytest\ndef test_a():\n    assert f() == 1\n"
    assert rules(base, "import pytest\ndef test_a():\n" + head_body) == ["B2"]


@pytest.mark.parametrize("guard", ['pytest.skip("x")', "return"])
def test_b2_guard_before_helper_based_checks(guard):
    base = "import pytest\ndef test_a(t):\n    t.header = False\n    check_lengths(t)\n"
    head = base.replace("    t.header", f"    {guard}\n    t.header")
    assert rules(base, head) == ["B2"]


def test_b2_negative_trailing_return():
    base = "def test_a():\n    assert f() == 1\n"
    assert rules(base, base + "    return\n") == []


def test_b2_module_pytestmark():
    base = "import pytest\ndef test_a():\n    assert f() == 1\n"
    head = 'import pytest\npytestmark = pytest.mark.skip(reason="x")\ndef test_a():\n    assert f() == 1\n'
    assert rules(base, head) == ["B2"]


def test_b2_negative_existing_skip_inside_except():
    base = 'import pytest\ndef test_a():\n    try:\n        assert f() == 1\n    except OSError:\n        pytest.skip("no")\n'
    assert rules(base, base.replace("== 1", "== 1, 'm'")) == []


# ---------------------------------------------------------------- B3


def test_b3_assert_removed():
    base = "def test_a():\n    assert f() == 1\n    assert g() == 2\n"
    res = run(base, "def test_a():\n    assert f() == 1\n")
    assert [f.rule for f in res.findings] == ["B3"]
    assert res.findings[0].before == "assert g() == 2"


def test_b3_assert_turned_into_expression():
    base = "def test_a():\n    assert f() == 1\n"
    assert rules(base, "def test_a():\n    f() == 1\n") == ["B3"]


def test_b3_raises_removed():
    base = "import pytest\ndef test_a():\n    with pytest.raises(ValueError):\n        f()\n    assert g() == 1\n"
    head = "import pytest\ndef test_a():\n    f()\n    assert g() == 1\n"
    assert rules(base, head) == ["B3"]


def test_b3_negative_extract_local():
    base = "def test_a():\n    assert f(x) == 1\n"
    assert rules(base, "def test_a():\n    result = f(x)\n    assert result == 1\n") == []


# ---------------------------------------------------------------- gaps and hostile input


def test_unparseable_head_is_gap_not_pass():
    res = run(BASE, "def test_cap(:\n")
    assert res.findings == []
    assert [g.reason for g in res.gaps] == ["unparseable"]


def test_null_byte_is_gap():
    res = analyze_file(P, "def test_a():\n    assert 1\n", "def test_a():\n    assert 1\x00\n")
    assert [g.reason for g in res.gaps] == ["unparseable"]


def test_deep_nesting_is_input_limit_gap():
    head = "def test_a():\n    assert f(" + "(" * 500 + ")" * 500 + ") == 1\n"
    res = analyze_file(P, "def test_a():\n    assert f() == 1\n", head)
    assert [g.reason for g in res.gaps] == ["input_limit"]


def test_size_limit_is_gap_live_only():
    big = "def test_a():\n" + "    x = 1\n" * 3000 + "    assert f() == 1\n"
    assert [g.reason for g in analyze_file(P, big, big, Limits.live()).gaps] == ["input_limit"]
    assert analyze_file(P, big, big, Limits.cli()).gaps == []


def test_malicious_looking_source_is_only_parsed(tmp_path, monkeypatch):
    marker = tmp_path / "pwned"
    payload = f"__import__('pathlib').Path({str(marker)!r}).write_text('x')"
    base = f"{payload}\ndef test_a():\n    {payload}\n    assert f() == 1\n"
    res = analyze_file(P, base, base.replace("== 1", ">= 0"))
    assert [f.rule for f in res.findings] == ["S2"]
    assert not marker.exists()


def test_is_test_path():
    assert is_test_path("tests/test_a.py") and is_test_path("a_test.py")
    assert not is_test_path("src/a.py") and not is_test_path("tests/conftest.py")
