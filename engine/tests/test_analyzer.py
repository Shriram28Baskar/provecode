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


@pytest.mark.parametrize("head", ["pytest.approx(3.0)", "pytest.approx(3.0, rel=1e-6)", "pytest.approx(3.0, abs=1e-12)",
                                  "pytest.approx(3.0, rel=1e-9)"])
def test_s1_negative_equality_to_approx_at_default_tolerance(head):
    base = "import pytest\ndef test_a():\n    assert f() == 3.0\n"
    assert rules(base, base.replace("== 3.0", f"== {head}")) == []


def test_s1_equality_to_approx_with_an_explicit_loose_abs():
    base = "import pytest\ndef test_a():\n    assert f() == 80\n"
    assert rules(base, base.replace("== 80", "== pytest.approx(80, abs=100)")) == ["S1"]


def test_equality_to_approx_of_a_different_value_is_v0():
    base = "import pytest\ndef test_a():\n    assert f() == 3.0\n"
    assert rules(base, base.replace("== 3.0", "== pytest.approx(3.1)")) == ["V0"]


@pytest.mark.parametrize("head,expect", [
    ("assert abs(f() - 80) < 50", ["S1"]),
    ("assert abs(80 - f()) <= 50", ["S1"]),
    ("assert math.isclose(f(), 80, abs_tol=50)", ["S1"]),
    ("assert math.isclose(f(), 80)", []),
    ("assert abs(f() - 80) < 1e-12", []),
])
def test_tolerance_spellings_are_approx(head, expect):
    base = "import math\ndef test_a():\n    assert f() == 80\n"
    assert rules(base, base.replace("assert f() == 80", head)) == expect


def test_s1_abs_tolerance_widened():
    base = "def test_a():\n    assert abs(f() - 3.0) < 1e-9\n"
    assert rules(base, base.replace("1e-9", "0.5")) == ["S1"]


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


# ---------------------------------------------------------------- compound `assert a and b`
# `assert a and b` checks exactly what `assert a` then `assert b` check, so each conjunct is a check.

AND_BASE = "def test_a():\n    assert code == 1 and f(x) == 'FAILED'\n"


def test_and_conjunct_weakened_to_not_none_is_s2():
    res = run(AND_BASE, AND_BASE.replace("f(x) == 'FAILED'", "f(x) is not None"))
    assert [f.rule for f in res.findings] == ["S2"]
    assert res.findings[0].before == "f(x) == 'FAILED'" and res.findings[0].after == "f(x) is not None"


def test_and_conjunct_dropped_is_b3():
    assert rules(AND_BASE, AND_BASE.replace(" and f(x) == 'FAILED'", "")) == ["B3"]


def test_and_split_into_two_asserts_is_not_a_finding():
    head = "def test_a():\n    assert code == 1\n    assert f(x) == 'FAILED'\n"
    assert rules(AND_BASE, head) == []


def test_and_conjuncts_reordered_is_not_a_finding():
    assert rules(AND_BASE, "def test_a():\n    assert f(x) == 'FAILED' and code == 1\n") == []


def test_and_conjunct_added_is_not_a_finding():
    assert rules(AND_BASE, AND_BASE.replace("'FAILED'\n", "'FAILED' and g() == 2\n")) == []


def test_and_merged_into_one_tuple_equality_is_not_a_removal():
    assert rules(AND_BASE, "def test_a():\n    assert (code, f(x)) == (1, 'FAILED')\n") == []


def test_and_conjunct_dropped_while_another_check_changes_is_still_b3():
    head = "def test_a():\n    assert code >= 0\n"
    assert "B3" in rules(AND_BASE, head)


def test_nested_and_is_flattened():
    base = "def test_a():\n    assert a == 1 and (b == 2 and c == 3)\n"
    assert rules(base, base.replace("c == 3", "c is not None")) == ["S2"]


def test_or_is_left_whole():
    base = "def test_a():\n    assert a == 1 or b == 2\n"
    assert rules(base, base.replace(" or b == 2", "")) == []


# ---------------------------------------------------------------- B2: importorskip added to an existing test

IOS = "import pytest\n\ndef test_a():\n    assert f(x) == 42\n"


@pytest.mark.parametrize("line", ["    pytest.importorskip('nonexistent_mod')\n",
                                  "    np = pytest.importorskip('numpy')\n"])
def test_b2_importorskip_added_to_an_existing_test(line):
    head = IOS.replace("def test_a():\n", "def test_a():\n" + line)
    res = run(IOS, head)
    assert [f.rule for f in res.findings] == ["B2"]
    assert "import fails" in res.findings[0].rationale


def test_importorskip_in_a_new_test_is_not_a_finding():
    assert rules(IOS, IOS + "\ndef test_b():\n    np = pytest.importorskip('numpy')\n    assert np\n") == []


def test_importorskip_kept_is_not_a_finding():
    base = IOS.replace("def test_a():\n", "def test_a():\n    np = pytest.importorskip('numpy')\n")
    assert rules(base, base.replace("== 42", "== 42\n    assert f(y) == 1")) == []


# ---------------------------------------------------------------- S2: equality reduced to a length check

COLL = "def test_a():\n    assert c.items == ['a', 'a']\n"


@pytest.mark.parametrize("head", ["assert len(c.items) == 2", "assert len(c.items) > 0",
                                  "self.assertEqual(len(c.items), 2)"])
def test_s2_collection_equality_reduced_to_its_length(head):
    if head.startswith("self."):
        base = ("import unittest\n\nclass T(unittest.TestCase):\n    def test_a(self):\n"
                "        self.assertEqual(c.items, ['a', 'a'])\n")
        assert rules(base, base.replace("self.assertEqual(c.items, ['a', 'a'])", head)) == ["S2"]
    else:
        assert rules(COLL, COLL.replace("assert c.items == ['a', 'a']", head)) == ["S2"]


def test_length_check_added_next_to_the_equality_is_not_a_finding():
    assert rules(COLL, COLL + "    assert len(c.items) == 2\n") == []


def test_length_of_something_else_is_not_paired():
    assert "S2" not in rules(COLL, COLL.replace("assert c.items == ['a', 'a']", "assert len(c.other) == 2"))


# ---------------------------------------------------------------- S5: always-true replacements

ONE = "def test_a():\n    assert f(x) == 42\n"


@pytest.mark.parametrize("head", ["assert True", "assert 1", "assert 'ok'", "assert (f(x) == 42, 'msg')",
                                  "assert [f(x) == 42]"])
def test_s5_check_replaced_by_an_always_true_assert(head):
    assert rules(ONE, ONE.replace("assert f(x) == 42", head)) == ["S5"]


def test_s5_unittest_assert_true_constant():
    base = "import unittest\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(f(x), 42)\n"
    assert rules(base, base.replace("self.assertEqual(f(x), 42)", "self.assertTrue(True)")) == ["S5"]


def test_s5_always_true_assert_added_alongside_is_not_a_finding():
    assert rules(ONE, ONE + "    assert True\n") == []


def test_s5_always_true_does_not_steal_a_reworded_check():
    head = "def test_a():\n    assert f(x) == 43\n    assert True\n"
    assert rules(ONE, head) == ["V0"]


def test_assert_false_and_empty_tuple_are_not_always_true():
    assert "S5" not in rules(ONE, ONE.replace("assert f(x) == 42", "assert ()"))


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


@pytest.mark.parametrize("head", ["(ValueError, TypeError)", "(ValueError, BarError)"])
def test_s3_unrelated_exception_added_alongside_the_expected_one(head):
    base = "import pytest\ndef test_a():\n    with pytest.raises(ValueError):\n        f()\n"
    assert rules(base, base.replace("ValueError", head)) == ["S3"]


def test_s3_negative_tuple_narrowed():
    base = "import pytest\ndef test_a():\n    with pytest.raises((ValueError, TypeError)):\n        f()\n"
    assert rules(base, base.replace("(ValueError, TypeError)", "ValueError")) == []


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


def test_s4_parametrize_removed_and_one_case_hard_coded():
    head = "\ndef test_a():\n    x, y = 1, 2\n    assert f(x) == y\n"
    res = run(PARAM, PARAM[:PARAM.index("@pytest.mark.parametrize")] + head.lstrip("\n"))
    assert [f.rule for f in res.findings] == ["S4"]
    assert "3" in res.findings[0].rationale


def test_s4_negative_parametrize_moved_into_a_loop_over_the_same_cases():
    head = "def test_a():\n    for x, y in [(1, 2), (0, 0), (-1, -2)]:\n        assert f(x) == y\n"
    assert "S4" not in rules(PARAM, PARAM[:PARAM.index("@pytest.mark.parametrize")] + head)


def test_s4_cases_given_ids_are_the_same_cases():
    head = PARAM.replace("[(1, 2), (0, 0), (-1, -2)]",
                         "[pytest.param(1, 2, id='pos'), pytest.param(0, 0, id='zero'), (-1, -2)]")
    assert rules(PARAM, head) == []


def test_s4_single_argument_case_given_an_id_is_the_same_case():
    base = "import pytest\n\n@pytest.mark.parametrize('x', [1, 2])\ndef test_a(x):\n    assert f(x)\n"
    assert rules(base, base.replace("[1, 2]", "[pytest.param(1, id='one'), 2]")) == []


def test_s4_case_given_a_skip_mark_is_a_removal():
    head = PARAM.replace("(-1, -2)]", "pytest.param(-1, -2, marks=pytest.mark.skip)]")
    assert rules(PARAM, head) == ["S4"]


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


@pytest.mark.parametrize("wrap", ["for _ in []:", "for _ in ():", "for _ in range(0):", "while False:"])
def test_b2_checks_moved_into_a_loop_that_never_runs(wrap):
    base = "import pytest\ndef test_a():\n    assert f() == 1\n"
    assert rules(base, f"import pytest\ndef test_a():\n    {wrap}\n        assert f() == 1\n") == ["B2"]


def test_b2_negative_checks_moved_into_a_loop_that_runs():
    base = "import pytest\ndef test_a():\n    assert f() == 1\n"
    assert "B2" not in rules(base, "import pytest\ndef test_a():\n    for _ in range(3):\n        assert f() == 1\n")


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
