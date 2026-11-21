"""S5 (check made vacuous) and B4 (assertion failure swallowed), ADR 0011.

Synthetic fixtures only; none of these come from benchmark cases.
"""

from __future__ import annotations

import pytest

from provecode.analyzer import analyze_file

P = "tests/test_x.py"


def rules(base: str, head: str) -> list[str]:
    return sorted(f.rule for f in analyze_file(P, base, head).findings)


PY = "def test_a():\n    x = 1\n    assert total(x) == 5\n"
UT = ("import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        x = 1\n"
      "        self.assertEqual(total(x), 5)\n")
UT_TRUE = ("import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        x = 1\n"
           "        self.assertTrue(ok(x))\n")


# --- S5: check made vacuous ----------------------------------------------------------------------

@pytest.mark.parametrize("base,old,new", [
    (PY, "assert total(x) == 5", "assert total(x) == total(x)"),
    (PY, "assert total(x) == 5", "assert total(x) is total(x)"),
    (PY, "assert total(x) == 5", "assert total(x) == 5 or True"),
    (PY, "assert total(x) == 5", "assert total(x) == 5 or 1"),
    (PY, "assert total(x) == 5", "assert True or total(x) == 5"),
    (UT, "self.assertEqual(total(x), 5)", "self.assertEqual(total(x), total(x))"),
    (UT_TRUE, "self.assertTrue(ok(x))", "self.assertTrue(ok(x) or True)"),
])
def test_s5_vacuous_check(base, old, new):
    f = analyze_file(P, base, base.replace(old, new)).findings
    assert [x.rule for x in f] == ["S5"]
    assert f[0].before and f[0].after


def test_s5_rationale_names_the_reason():
    taut = analyze_file(P, PY, PY.replace("== 5", "== total(x)")).findings[0]
    assert "itself" in taut.rationale
    ortrue = analyze_file(P, PY, PY.replace("== 5", "== 5 or True")).findings[0]
    assert "always" in ortrue.rationale


@pytest.mark.parametrize("head", [
    PY.replace("    assert total(x) == 5\n", "    assert total(x) == total(x)\n    assert total(x) == 5\n"),  # added
    PY.replace("assert total(x) == 5", "assert total(x) == 5 or total(x) == 6"),  # not a constant (S-other, not S5)
    PY.replace("assert total(x) == 5", "assert total(x) == 5 and True"),
    PY.replace("assert total(x) == 5", "assert total(x) != 4 or False"),
])
def test_s5_not_fired(head):
    assert "S5" not in rules(PY, head)


def test_s5_unchanged_vacuous_check_is_not_new():
    base = "def test_a():\n    assert a == a\n    assert b() == 2\n"
    assert rules(base, base.replace("b() == 2", "b() == 2  # same")) == []


# --- B4: assertion failure swallowed -------------------------------------------------------------

def wrap_py(handler: str, stmt: str = "assert total(x) == 5") -> str:
    return PY.replace(f"    {stmt}\n", f"    try:\n        {stmt}\n{handler}")


@pytest.mark.parametrize("handler", [
    "    except AssertionError:\n        pass\n",
    "    except Exception:\n        pass\n",
    "    except BaseException:\n        pass\n",
    "    except:\n        pass\n",
    "    except (ValueError, AssertionError):\n        pass\n",
    "    except AssertionError as e:\n        print(e)\n",
])
def test_b4_swallowed_pytest(handler):
    f = analyze_file(P, PY, wrap_py(handler)).findings
    assert [x.rule for x in f] == ["B4"]
    assert f[0].after.startswith("try:")


def test_b4_swallowed_unittest():
    head = UT.replace("        self.assertEqual(total(x), 5)\n",
                      "        try:\n            self.assertEqual(total(x), 5)\n        except AssertionError:\n"
                      "            pass\n")
    assert rules(UT, head) == ["B4"]


@pytest.mark.parametrize("ctx", ["contextlib.suppress(AssertionError)", "suppress(Exception)"])
def test_b4_contextlib_suppress(ctx):
    head = PY.replace("    assert total(x) == 5\n", f"    with {ctx}:\n        assert total(x) == 5\n")
    assert rules(PY, head) == ["B4"]


@pytest.mark.parametrize("handler", [
    "    except AssertionError:\n        raise\n",
    "    except AssertionError as exc:\n        raise AssertionError(f'ctx {exc}') from exc\n",
    "    except AssertionError:\n        pytest.fail('ctx')\n",
    "    except AssertionError:\n        self.fail('ctx')\n",
    "    except ValueError:\n        pass\n",
    "    finally:\n        cleanup()\n",
])
def test_b4_not_fired(handler):
    assert rules(PY, wrap_py(handler)) == []


def test_b4_check_in_else_block_is_not_swallowed():
    head = PY.replace("    assert total(x) == 5\n",
                      "    try:\n        prepare()\n    except Exception:\n        pass\n    else:\n"
                      "        assert total(x) == 5\n")
    assert rules(PY, head) == []


def test_b4_already_swallowed_in_base_is_not_new():
    base = wrap_py("    except AssertionError:\n        pass\n")
    assert rules(base, base.replace("x = 1", "x = 1  # same")) == []


def test_b4_nested_function_is_not_the_test_body():
    head = PY.replace("    assert total(x) == 5\n",
                      "    def helper():\n        try:\n            assert other() == 1\n        except Exception:\n"
                      "            pass\n    assert total(x) == 5\n")
    assert rules(PY, head) == []


def test_b4_in_testcase_class_method():
    base = UT
    head = base.replace("        self.assertEqual(total(x), 5)\n",
                        "        try:\n            self.assertEqual(total(x), 5)\n        except:\n            pass\n")
    f = analyze_file(P, base, head).findings
    assert [x.rule for x in f] == ["B4"] and f[0].test.endswith("::T::test_a")


def test_s5_or_true_on_a_reflexivity_check():
    # `c == c` exercises a custom __eq__ (a real reflexivity test); `or True` makes it vacuous.
    base = "def test_eq():\n    c = make()\n    assert c == c\n"
    assert rules(base, base.replace("assert c == c", "assert c == c or True")) == ["S5"]
