"""A test moved, body unchanged, to another collected test file in the same change is not a
deletion (B1): it is recorded as R0 with where it went. Anything short of that stays B1."""

from provecode import pipeline

A = "def test_keep():\n    assert f() == 1\n\n\ndef test_price():\n    assert price(100) == 80\n"
A_WITHOUT = "def test_keep():\n    assert f() == 1\n"
B_BASE = "def test_other():\n    assert g() == 2\n"
MOVED = "\n\ndef test_price():\n    assert price(100) == 80\n"


def rules(entries):
    rep = pipeline.run(entries, None)
    return sorted(f["rule"] for f in rep["findings"]), rep


def test_test_moved_to_an_existing_file_is_r0_not_b1():
    rs, rep = rules([("tests/test_a.py", A, A_WITHOUT), ("tests/test_b.py", B_BASE, B_BASE + MOVED)])
    assert rs == ["R0"]
    f = rep["findings"][0]
    assert f["detail"]["moved_to"] == "tests/test_b.py::test_price"


def test_test_moved_to_a_new_file_is_r0():
    rs, _ = rules([("tests/test_a.py", A, A_WITHOUT), ("tests/test_price.py", None, MOVED.lstrip())])
    assert rs == ["R0"]


def test_whole_file_split_into_new_files_is_r0():
    rs, _ = rules([("tests/test_a.py", A, None), ("tests/test_keep.py", None, A_WITHOUT),
                   ("tests/test_price.py", None, MOVED.lstrip())])
    assert rs == ["R0", "R0"]


def test_moved_with_a_changed_body_stays_b1():
    rs, _ = rules([("tests/test_a.py", A, A_WITHOUT),
                   ("tests/test_b.py", B_BASE, B_BASE + MOVED.replace("== 80", ">= 0"))])
    assert rs == ["B1"]


def test_moved_to_a_file_pytest_does_not_collect_stays_b1():
    rs, _ = rules([("tests/test_a.py", A, A_WITHOUT), ("tests/helpers.py", None, MOVED.lstrip())])
    assert rs == ["B1"]


def test_moved_into_a_module_skipped_file_stays_b1():
    dest = "import pytest\n\npytestmark = pytest.mark.skip\n" + MOVED
    rs, _ = rules([("tests/test_a.py", A, A_WITHOUT), ("tests/test_b.py", None, dest)])
    assert "B1" in rs


def test_one_copy_cannot_explain_two_deletions():
    two = "def test_price():\n    assert price(100) == 80\n"
    rs, _ = rules([("tests/test_a.py", A, A_WITHOUT), ("tests/test_c.py", two, ""),
                   ("tests/test_b.py", B_BASE, B_BASE + MOVED)])
    assert rs == ["B1", "R0"]
