"""Test-body rules T1-T5 (ADR 0012): true positives, near-misses and regressions."""

import pytest

from provecode import contract, pipeline

P = "tests/test_x.py"
HEAD_IMPORT = "from pkg.mod import f, g\n\n\n"


def run(base, head, guarded=False):
    doc = None
    if guarded:
        doc = contract.approve({"contract": {"id": "c", "version": 1}, "conditions": [
            {"id": "C1", "description": "d", "type": "functional", "mandatory": True,
             "guarded_by": [f"{P}::test_a"], "checks": []}], "checks": []}, "o", "2026-11-21T00:00:00Z")
    return pipeline.run([(P, HEAD_IMPORT + base, HEAD_IMPORT + head)], doc)


def rules(rep):
    return sorted(f["rule"] for f in rep["findings"] if f["severity"] != "info")


# --------------------------------------------------------------------------- T1

def test_t1_input_changed_expected_same():
    rep = run("def test_a():\n    assert f(10, 2) == 5\n", "def test_a():\n    assert f(10, 1) == 5\n")
    assert rules(rep) == ["T1"]


def test_t1_through_a_local():
    base = "def test_a():\n    r = f(100, 0.5)\n    assert r == 80\n    assert r > 0\n"
    rep = run(base, base.replace("0.5", "0.1"))
    assert rules(rep) == ["T1"]


def test_t1_unittest_style():
    base = "class T(TestCase):\n    def test_a(self):\n        self.assertEqual(f(3), 9)\n"
    assert rules(run(base, base.replace("f(3)", "f(4)"))) == ["T1"]


def test_t1_both_changed_is_only_notable_under_a_contract():
    base, head = "def test_a():\n    assert f(10, 2) == 5\n", "def test_a():\n    assert f(10, 5) == 2\n"
    assert rules(run(base, head)) == []
    rep = run(base, head, guarded=True)
    t1 = [f for f in rep["findings"] if f["rule"] == "T1"]
    assert t1 and t1[0]["detail"]["both_changed"] and t1[0]["severity"] == "warning"  # capped: never blocking


@pytest.mark.parametrize("base,head", [
    ("def test_a():\n    assert f(10, 2) == 5\n", "def test_a():\n    assert f(10, 2) == 6\n"),  # V0 only
    ("def test_a():\n    assert f(10, 2) == 5\n", "def test_a():\n    assert g(10, 2) == 5\n"),  # different callee
    ("def test_a():\n    assert f(x, 2) == 5\n", "def test_a():\n    assert f(y, 2) == 5\n"),  # non-literal input
    ("def test_a():\n    assert f(10, 2) == 5\n", "def test_a():\n    assert f(10, 2) == 5\n    assert f(10, 3) == 5\n"),
])
def test_t1_near_misses(base, head):
    assert "T1" not in rules(run(base, head))


# --------------------------------------------------------------------------- T2

def test_t2_delegated_to_helper_that_cannot_fail():
    base = "def test_a():\n    assert f(1) == 2\n"
    head = "def _check(a, b):\n    return True\n\n\ndef test_a():\n    assert _check(f(1), 2)\n"
    assert rules(run(base, head)) == ["T2"]


def test_t2_bare_call_to_pass_helper():
    base = "def test_a():\n    assert f(1) == 2\n"
    head = "def _check(a, b):\n    pass\n\n\ndef test_a():\n    _check(f(1), 2)\n"
    assert "T2" in rules(run(base, head))


def test_t2_hollowed_helper_flags_every_caller():
    base = ("def _check(a, b):\n    assert a == b\n\n\ndef test_a():\n    _check(f(1), 2)\n\n\n"
            "def test_b():\n    _check(f(2), 4)\n")
    head = base.replace("    assert a == b\n", "    return True\n")
    rep = run(base, head)
    assert [f["test"] for f in rep["findings"] if f["rule"] == "T2"] == [f"{P}::test_a", f"{P}::test_b"]


def test_t2_near_miss_helper_that_asserts_is_not_t2_or_b3():
    base = "def test_a():\n    assert f(1) == 2\n"
    head = "def _check(a, b):\n    assert a == b\n\n\ndef test_a():\n    _check(f(1), 2)\n"
    assert rules(run(base, head)) == []


def test_t2_helper_that_calls_another_checker_is_not_trivial():
    base = "def test_a():\n    assert f(1) == 2\n"
    head = "def _check(a, b):\n    other(a, b)\n\n\ndef test_a():\n    assert _check(f(1), 2)\n"
    assert "T2" not in rules(run(base, head))


# --------------------------------------------------------------------------- T3

def test_t3_monkeypatch_string_and_object_forms():
    base = "def test_a(monkeypatch):\n    assert f(1) == 2\n"
    head = "def test_a(monkeypatch):\n    monkeypatch.setattr('pkg.mod.f', lambda x: 2)\n    assert f(1) == 2\n"
    assert rules(run(base, head)) == ["T3"]
    base2 = "import pkg.mod\n\n\ndef test_a(monkeypatch):\n    assert pkg.mod.f(1) == 2\n"
    head2 = base2.replace("    assert", "    monkeypatch.setattr(pkg.mod, 'f', lambda x: 2)\n    assert")
    assert rules(run(base2, head2)) == ["T3"]


def test_t3_mock_patch_decorator():
    base = "def test_a():\n    assert f(1) == 2\n"
    head = "@mock.patch('pkg.mod.f', return_value=2)\ndef test_a(m):\n    assert f(1) == 2\n"
    assert "T3" in rules(run(base, head))


def test_t3_near_miss_collaborator_patched():
    base = "def test_a(monkeypatch):\n    assert f(1) == 2\n"
    head = "def test_a(monkeypatch):\n    monkeypatch.setattr('time.sleep', lambda s: None)\n    assert f(1) == 2\n"
    assert rules(run(base, head)) == []
    head2 = "def test_a(monkeypatch):\n    monkeypatch.setattr('pkg.mod.g', lambda: 0)\n    assert f(1) == 2\n"
    assert rules(run(base, head2)) == []


# --------------------------------------------------------------------------- T4

@pytest.mark.parametrize("empty", ["[]", "()", "range(0)", "list()", "cases[:0]", "''"])
def test_t4_loop_emptied(empty):
    base = "def test_a():\n    for x in [1, 2, 3]:\n        assert f(x) > 0\n"
    assert rules(run(base, base.replace("[1, 2, 3]", empty))) == ["T4"]


def test_t4_loop_reduced_and_parametrize_emptied():
    base = "def test_a():\n    for x in [1, 2, 3]:\n        assert f(x) > 0\n"
    assert rules(run(base, base.replace("[1, 2, 3]", "[1]"))) == ["T4"]
    pbase = "@pytest.mark.parametrize('x', [1, 2])\ndef test_a(x):\n    assert f(x) > 0\n"
    assert rules(run(pbase, pbase.replace("[1, 2]", "list()"))) == ["T4"]


@pytest.mark.parametrize("head_iter", ["[1, 2, 3, 4]", "[3, 2, 1]", "data"])
def test_t4_near_misses(head_iter):
    base = "def test_a():\n    for x in [1, 2, 3]:\n        assert f(x) > 0\n"
    assert "T4" not in rules(run(base, base.replace("[1, 2, 3]", head_iter)))


def test_t4_loop_without_checks_is_ignored():
    base = "def test_a():\n    for x in [1, 2]:\n        setup(x)\n    assert f() == 1\n"
    assert rules(run(base, base.replace("[1, 2]", "[]"))) == []


# --------------------------------------------------------------------------- T5

def test_t5_raises_replaced_by_value_check():
    base = "def test_a():\n    with pytest.raises(ValueError):\n        f(-1)\n"
    head = "def test_a():\n    assert f(-1) is not None\n"
    rep = run(base, head)
    assert rules(rep) == ["T5"]


def test_t5_unittest_assert_raises_replaced():
    base = "class T(TestCase):\n    def test_a(self):\n        self.assertRaises(ValueError, f, -1)\n"
    head = "class T(TestCase):\n    def test_a(self):\n        self.assertEqual(f(-1), 0)\n"
    assert "T5" in rules(run(base, head))


def test_t5_near_misses():
    base = "def test_a():\n    with pytest.raises(ValueError):\n        f(-1)\n"
    stronger = base.replace("pytest.raises(ValueError)", "pytest.raises(ValueError, match='neg')")
    assert "T5" not in rules(run(base, stronger))
    assert rules(run(base, "def test_a():\n    pass\n")) == ["B3"]  # a plain removal stays B3


def test_t_rules_do_not_fire_on_identical_tests():
    base = "def test_a():\n    for x in [1]:\n        assert f(x, 1) == 2\n"
    assert run(base, base)["findings"] == []


def test_regression_t3_resolves_imports_inside_the_test_function():
    # Found by an executed probe (demo/build_probes.py, cheat-monkeypatch), not by benchmark data.
    base = "def test_a(monkeypatch):\n    from pkg import mod\n    assert mod.f(1) == 2\n"
    head = ("def test_a(monkeypatch):\n    monkeypatch.setattr('pkg.mod.f', lambda x: 2)\n    from pkg import mod\n"
            "    assert mod.f(1) == 2\n")
    assert rules(run(base, head)) == ["T3"]
