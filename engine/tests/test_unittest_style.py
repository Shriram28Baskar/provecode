"""unittest-style tests (ADR 0009): TestCase classes, self.assert* checks and unittest skips.

Synthetic fixtures only; none of these come from benchmark cases.
"""

from __future__ import annotations

import pytest

from provecode import contract as C, pipeline
from provecode.analyzer import analyze_file, is_collected_name, is_test_path

P = "app/tests.py"

HEADER = "import unittest\n\n\nclass ParserTests(unittest.TestCase):\n"


def case(body: str, header: str = HEADER) -> str:
    return header + "".join(f"    {line}\n" for line in body.strip("\n").split("\n"))


def rules(base: str, head: str, path: str = P) -> list[str]:
    return sorted(f.rule for f in analyze_file(path, base, head).findings)


BASE_EQ = case("def test_parse(self):\n    self.assertEqual(parse('1'), 1)")


def test_methods_of_a_testcase_subclass_are_recognised():
    res = analyze_file(P, BASE_EQ, BASE_EQ)
    assert res.findings == [] and res.gaps == []
    head = BASE_EQ.replace("self.assertEqual(parse('1'), 1)", "self.assertGreaterEqual(parse('1'), 1)")
    f = analyze_file(P, BASE_EQ, head).findings
    assert [x.test for x in f] == ["app/tests.py::ParserTests::test_parse"]


# --- S1: comparison weakened -------------------------------------------------------------------

def test_s1_assert_equal_to_assert_greater_equal():
    head = BASE_EQ.replace("assertEqual", "assertGreaterEqual")
    assert rules(BASE_EQ, head) == ["S1"]


def test_s1_assert_equal_to_assert_almost_equal_with_delta():
    head = BASE_EQ.replace("self.assertEqual(parse('1'), 1)", "self.assertAlmostEqual(parse('1'), 1, delta=5)")
    assert rules(BASE_EQ, head) == ["S1"]


# --- S2: expected value replaced by a trivial check ----------------------------------------------

@pytest.mark.parametrize("weak", [
    "self.assertIsNotNone(parse('1'))",
    "self.assertTrue(parse('1'))",
    "self.assertIsInstance(parse('1'), int)",
])
def test_s2_assert_equal_literal_to_trivial_check(weak):
    head = BASE_EQ.replace("self.assertEqual(parse('1'), 1)", weak)
    assert rules(BASE_EQ, head) == ["S2"]


# --- S3: exception check widened or loosened -----------------------------------------------------

def test_s3_assert_raises_widened_to_exception():
    base = case("def test_bad(self):\n    with self.assertRaises(ValueError):\n        parse('x')")
    head = base.replace("ValueError", "Exception")
    f = analyze_file(P, base, head).findings
    assert [x.rule for x in f] == ["S3"]
    assert f[0].rationale.startswith("self.assertRaises:")


def test_s3_assert_raises_regex_to_assert_raises():
    base = case("def test_bad(self):\n    with self.assertRaisesRegex(ValueError, 'bad digit'):\n        parse('x')")
    head = base.replace("self.assertRaisesRegex(ValueError, 'bad digit')", "self.assertRaises(ValueError)")
    f = analyze_file(P, base, head).findings
    assert [x.rule for x in f] == ["S3"]
    assert "expected-message check was removed" in f[0].rationale


def test_s3_negative_assert_raises_narrowed():
    base = case("def test_bad(self):\n    with self.assertRaises(Exception):\n        parse('x')")
    assert rules(base, base.replace("Exception", "ValueError")) == []


# --- B2: skips -----------------------------------------------------------------------------------

def test_b2_unittest_skip_on_method():
    head = BASE_EQ.replace("    def test_parse", "    @unittest.skip('flaky')\n    def test_parse")
    assert rules(BASE_EQ, head) == ["B2"]


def test_b2_unittest_skip_on_class():
    head = BASE_EQ.replace("class ParserTests", "@unittest.skip('later')\nclass ParserTests")
    assert rules(BASE_EQ, head) == ["B2"]


def test_b2_imported_skip_decorator():
    base = BASE_EQ.replace("import unittest\n", "import unittest\nfrom unittest import skip\n")
    head = base.replace("    def test_parse", "    @skip('flaky')\n    def test_parse")
    assert rules(base, head) == ["B2"]


@pytest.mark.parametrize("deco,flagged", [
    ("@unittest.skipIf(True, 'x')", True),
    ("@unittest.skipUnless(False, 'x')", True),
    ("@unittest.expectedFailure", True),
    ("@unittest.skipIf(sys.platform == 'win32', 'x')", False),
])
def test_b2_conditional_unittest_skips(deco, flagged):
    head = BASE_EQ.replace("    def test_parse", f"    {deco}\n    def test_parse")
    assert rules(BASE_EQ, head) == (["B2"] if flagged else [])


def test_b2_skip_test_first_in_body():
    head = BASE_EQ.replace("    self.assertEqual", "    self.skipTest('later')\n        self.assertEqual")
    assert rules(BASE_EQ, head) == ["B2"]


def test_skip_test_on_another_object_is_not_a_guard():
    head = BASE_EQ.replace("    self.assertEqual", "    runner.skipTest('x')\n        self.assertEqual")
    assert rules(BASE_EQ, head) == []


# --- B3: check removed ---------------------------------------------------------------------------

def test_b3_self_assert_removed():
    base = case("def test_parse(self):\n    self.assertEqual(parse('1'), 1)\n    self.assertEqual(parse('2'), 2)")
    head = base.replace("    self.assertEqual(parse('2'), 2)\n", "")
    assert rules(base, head) == ["B3"]


def test_custom_helper_counts_only_for_b3():
    base = case("def test_render(self):\n    self.assertMarkdownRenders('*a*', '<p><em>a</em></p>')\n    x = 1")
    removed = base.replace("    self.assertMarkdownRenders('*a*', '<p><em>a</em></p>')\n", "")
    assert rules(base, removed) == ["B3"]
    # Changing its arguments is not judged: its strength is unknown.
    changed = base.replace("'<p><em>a</em></p>'", "'<p>a</p>'")
    assert rules(base, changed) == []


# --- Not flagged ---------------------------------------------------------------------------------

@pytest.mark.parametrize("head_line", [
    "self.assertEqual(parse('1'), 1, 'parse of one')",  # message added
    "self.assertEqual(1, parse('1'))",  # arguments swapped
])
def test_equivalent_rewrites_are_not_flagged(head_line):
    head = BASE_EQ.replace("self.assertEqual(parse('1'), 1)", head_line)
    assert rules(BASE_EQ, head) == []


def test_assert_true_to_assert_is_true_is_not_flagged():
    base = case("def test_ok(self):\n    self.assertTrue(ok())")
    assert rules(base, base.replace("self.assertTrue(ok())", "self.assertIs(ok(), True)")) == []


# --- Class recognition ---------------------------------------------------------------------------

@pytest.mark.parametrize("header", [
    "from django.test import TestCase\n\n\nclass ViewTests(TestCase):\n",
    "from django.test import SimpleTestCase\n\n\nclass WriterTests(SimpleTestCase):\n",
    "from django import test\n\n\nclass ViewTests(test.TransactionTestCase):\n",
])
def test_django_testcase_subclasses_are_recognised(header):
    base = case("def test_view(self):\n    self.assertEqual(view(), 200)", header)
    head = base.replace("assertEqual", "assertGreaterEqual")
    assert rules(base, head) == ["S1"]


def test_plain_class_with_test_methods_is_not_a_test_class():
    base = case("def test_view(self):\n    self.assertEqual(view(), 200)", "class Helpers(Base):\n")
    assert rules(base, base.replace("assertEqual", "assertGreaterEqual")) == []


def test_test_method_deleted_from_testcase_is_b1():
    base = case("def test_a(self):\n    self.assertEqual(a(), 1)\n\ndef test_b(self):\n    self.assertEqual(b(), 2)")
    head = case("def test_a(self):\n    self.assertEqual(a(), 1)")
    assert rules(base, head) == ["B1"]


# --- Paths ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("tests/test_x.py", True),
    ("pkg/x_test.py", True),
    ("app/tests.py", True),
    ("tests/helpers.py", True),
    ("pkg/test/util.py", True),
    ("tests\\sub\\cases.py", True),
    ("tests/conftest.py", False),
    ("tests/__init__.py", False),
    ("app/models.py", False),
    ("app/testing.py", False),
    ("tests/data.json", False),
])
def test_is_test_path(path, expected):
    assert is_test_path(path) is expected


@pytest.mark.parametrize("path,expected", [
    ("tests/test_x.py", True),
    ("pkg/x_test.py", True),
    ("app/tests.py", True),
    ("app/testing.py", True),  # unittest discovery pattern test*.py
    ("tests/helpers.py", False),
    ("tests/cases.py", False),
    ("tests/test_x.txt", False),
])
def test_is_collected_name(path, expected):
    assert is_collected_name(path) is expected


def _doc(guarded_by: str):
    d = {"contract": {"id": "c", "version": 1},
         "conditions": [{"id": "C1", "description": "parse", "type": "boundary", "mandatory": True,
                         "guarded_by": [guarded_by], "checks": []}],
         "checks": []}
    return C.approve(d, "owner", "2026-11-20T00:00:00Z")


def test_rename_within_tests_dir_to_uncollected_name_is_b1():
    base = case("def test_a(self):\n    self.assertEqual(a(), 1)")
    r = pipeline.run([("tests/cases.py", base, base, "tests/test_cases.py")],
                     _doc("tests/test_cases.py::ParserTests::test_a"))
    assert [f["rule"] for f in r["findings"]] == ["B1"]
    assert "do not collect" in r["findings"][0]["rationale"]


def test_rename_between_collected_names_is_not_deletion():
    base = case("def test_a(self):\n    self.assertEqual(a(), 1)")
    r = pipeline.run([("tests/test_new.py", base, base, "tests/test_old.py")],
                     _doc("tests/test_old.py::ParserTests::test_a"))
    assert "B1" not in [f["rule"] for f in r["findings"]]
