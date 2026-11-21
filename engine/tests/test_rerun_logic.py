"""The pure parts of the trusted re-run (ADR 0013): the overlay, the pyproject merge, the
dependency list and the A/B/C verdict. No Docker needed."""

from __future__ import annotations

from pathlib import Path

import pytest

from provecode import overlay, rerun
from provecode.rerun import RunResult, judge


def tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


BASE = {
    "src/shop/__init__.py": "",
    "src/shop/pricing.py": "def f():\n    return 1\n",
    "tests/test_a.py": "def test_a():\n    assert 1\n",
    "tests/conftest.py": "import pytest\n",
    "tests/data/case.json": "{}",
    "pyproject.toml": '[project]\nname = "shop"\nversion = "1"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
}


def test_overlay_runs_head_code_against_base_tests_and_harness(tmp_path):
    base = tree(tmp_path / "base", BASE)
    head = tree(tmp_path / "head", {
        **BASE,
        "src/shop/pricing.py": "def f():\n    return 2\n",
        "tests/test_a.py": "def test_a():\n    assert True or 1\n",
        "tests/conftest.py": "import pytest\n\ndef pytest_runtest_makereport(item, call):\n    pass\n",
        "tests/test_new.py": "import shop\nshop.pricing.f = lambda: 1\n",
        "tests/sub/conftest.py": "x = 1\n",
        "sitecustomize.py": "import os\n",
        "tests/shop.py": "x = 1\n",
        "tests/data/case.json": '{"changed": 1}',
        "pyproject.toml": BASE["pyproject.toml"].replace('version = "1"', 'version = "2"').replace(
            'testpaths = ["tests"]', 'testpaths = ["tests"]\naddopts = "-k not_a"'),
    })
    out = tmp_path / "B"
    ov = overlay.build(base, head, out)
    assert (out / "src/shop/pricing.py").read_text() == "def f():\n    return 2\n"  # head code
    for rel in ("tests/test_a.py", "tests/conftest.py", "tests/data/case.json"):
        assert (out / rel).read_text() == BASE[rel], rel  # base tests, harness and data
    for rel in ("tests/test_new.py", "tests/sub/conftest.py", "sitecustomize.py", "tests/shop.py"):
        assert not (out / rel).exists(), rel
        assert rel in ov.deleted
    merged = (out / "pyproject.toml").read_text()
    assert 'version = "2"' in merged and "addopts" not in merged  # head packaging, base pytest settings
    assert "tests/conftest.py" in ov.replaced


def test_merge_pyproject_native_table_and_absent_tables():
    base = '[project]\nname = "x"\n\n[tool.pytest]\naddopts = ["-ra"]\n\n[tool.coverage.run]\nbranch = true\n'
    head = base.replace('["-ra"]', '["-ra", "-k", "not slow"]').replace('name = "x"', 'name = "y"')
    merged = overlay.merge_pyproject(base, head)
    assert 'name = "y"' in merged and '"-k"' not in merged and "[tool.coverage.run]" in merged
    assert overlay.merge_pyproject('[project]\nname = "x"\n', '[project]\nname = "x"\n\n[tool.pytest]\nx = 1\n') \
        .count("[tool.pytest]") == 0


def test_dependency_list_comes_from_the_base(tmp_path):
    base = tree(tmp_path / "base", {
        "pyproject.toml": '[build-system]\nrequires = ["hatchling"]\n[project]\nname = "x"\nversion = "1"\n'
                          'dependencies = ["attrs>=22"]\n[project.optional-dependencies]\ntests = ["hypothesis"]\n'
                          'docs = ["sphinx"]\n[dependency-groups]\ntest = ["pytest>=8", {include-group = "cov"}]\n'
                          'cov = ["pytest-cov"]\n',
        "requirements/test.txt": "-r base.txt\npytest-xdist  # parallel\n",
        "requirements/base.txt": "freezegun\n",
        "docs/requirements.txt": "sphinx-rtd-theme\n",
    })
    reqs, _ = rerun._dep_lines(base)
    assert reqs[:3] == ["hatchling", "attrs>=22", "hypothesis"]
    assert {"pytest>=8", "pytest-cov", "freezegun", "pytest-xdist"} <= set(reqs)
    assert "sphinx" not in reqs and "sphinx-rtd-theme" not in reqs


def rr(name, outcomes, collected=None, code=1, setup=None):
    r = RunResult(name, code, 1.0)
    r.outcomes = dict(outcomes)
    r.collected = list(collected if collected is not None else outcomes)
    r.setup_failed = setup
    return r


@pytest.fixture
def trees(tmp_path):
    base = tree(tmp_path / "base", {"tests/test_p.py": "def test_cap():\n    assert f(1) == 80\n\n\n"
                                                       "def test_ok():\n    assert f(0) == 0\n\n\n"
                                                       "def test_gone():\n    assert f(2) == 1\n"})
    head = tree(tmp_path / "head", {"tests/test_p.py": "def test_cap():\n    assert f(1) >= 0\n\n\n"
                                                       "def test_ok():\n    assert f(0) == 0\n"})
    return base, head


ALL_PASS = {"tests/test_p.py::test_cap": "passed", "tests/test_p.py::test_ok": "passed",
            "tests/test_p.py::test_gone": "passed"}


def test_judge_classifies_each_b_failure_by_cause(trees):
    base, head = trees
    a = rr("A", ALL_PASS, code=0)
    b = rr("B", {**ALL_PASS, "tests/test_p.py::test_cap": "failed", "tests/test_p.py::test_ok": "failed",
                 "tests/test_p.py::test_gone": "failed"})
    c = rr("C", {"tests/test_p.py::test_cap": "passed", "tests/test_p.py::test_ok": "passed"}, code=0)
    findings, gaps, summary = judge(a, a, b, b, c, base, head, {"tests/test_p.py::test_cap"})
    got = {f.detail["nodeid"]: f.rule for f in findings}
    assert got == {"tests/test_p.py::test_cap": "X4", "tests/test_p.py::test_ok": "X2",
                   "tests/test_p.py::test_gone": "X3"}
    assert summary["failing_in_b"] == 3 and not gaps


def test_judge_x5_x6_x1_flaky_and_baseline(trees):
    base, head = trees
    a = rr("A", ALL_PASS, code=0)
    b = rr("B", {"tests/test_p.py::test_cap": "failed", "tests/test_p.py::test_ok": "failed"},
           collected=["tests/test_p.py::test_cap", "tests/test_p.py::test_ok"])
    c = rr("C", {"tests/test_p.py::test_cap": "passed", "tests/test_p.py::test_ok": "failed"})
    findings, _, _ = judge(a, a, b, b, c, base, head, set())
    got = sorted((f.rule, f.test) for f in findings)
    assert ("X5", "tests/test_p.py::test_cap") in got  # edited, no weakening finding
    assert ("X6", "tests/test_p.py::test_ok") in got  # fails in C too: an ordinary regression
    assert ("X1", "tests/test_p.py::test_gone") in got  # collected in A, not in B
    flaky_b2 = rr("B2", {"tests/test_p.py::test_cap": "passed", "tests/test_p.py::test_ok": "failed"},
                  collected=b.collected)
    findings, gaps, _ = judge(a, a, b, flaky_b2, c, base, head, set())
    assert "tests/test_p.py::test_cap" not in {f.detail.get("nodeid") for f in findings}
    assert any(g.reason == "flaky" for g in gaps)
    broken = rr("A", {}, collected=[], code=2)
    findings, gaps, _ = judge(broken, broken, b, b, c, base, head, set())
    assert findings == [] and [g.reason for g in gaps] == ["baseline_broken"]


def test_judge_all_green_gives_no_findings(trees):
    base, head = trees
    a = rr("A", ALL_PASS, code=0)
    findings, gaps, summary = judge(a, a, a, a, a, base, head, set())
    assert findings == [] and gaps == [] and summary["judged"] == 3


def test_regression_hash_pinned_requirements(tmp_path):
    # Found on kjd/idna (requirements from pip-compile --generate-hashes), not on benchmark data.
    base = tree(tmp_path / "base", {"requirements/test.txt": (
        "# autogenerated\nattrs==26.1.0 \\\n    --hash=sha256:aa \\\n    --hash=sha256:bb\n"
        "pytest==8.3.5 \\\n    --hash=sha256:cc\n")})
    reqs, _ = rerun._dep_lines(base)
    assert reqs == ["attrs==26.1.0", "pytest==8.3.5"]
