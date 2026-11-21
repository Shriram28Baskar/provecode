"""The seven cheapest cheats of PROVECODE_V2_BUILD_BRIEF.md section 0.3. Each produced zero findings
on the engine at 70f2dce; each must now produce the expected finding (ADR 0012)."""

from provecode import pipeline
from provecode.changeset import Changeset, FileChange

T = "tests/test_pricing.py"
BASE = "from pkg.pricing import discount\n\n\ndef test_cap():\n    assert discount(100, 0.50) == 80.0\n"


def run(*changes: FileChange, base_paths=None) -> dict:
    return pipeline.run(Changeset(list(changes), base_paths=base_paths), None)


def rules(rep: dict) -> list[str]:
    return sorted({f["rule"] for f in rep["findings"]})


def test_probe_1_input_changed_assertion_same():
    rep = run(FileChange(T, "M", BASE, BASE.replace("0.50", "0.10")))
    assert rules(rep) == ["T1"]


def test_probe_2_assertion_moved_into_trivially_true_helper():
    head = ("from pkg.pricing import discount\n\n\ndef _ok(value):\n    return True\n\n\n"
            "def test_cap():\n    assert _ok(discount(100, 0.50))\n")
    rep = run(FileChange(T, "M", BASE, head))
    assert "T2" in rules(rep)


def test_probe_3_monkeypatch_on_function_under_test():
    base = "import pkg.pricing\n\n\ndef test_cap(monkeypatch):\n    assert pkg.pricing.discount(100, 0.50) == 80.0\n"
    head = ("import pkg.pricing\n\n\ndef test_cap(monkeypatch):\n"
            "    monkeypatch.setattr(pkg.pricing, 'discount', lambda p, d: 80.0)\n"
            "    assert pkg.pricing.discount(100, 0.50) == 80.0\n")
    rep = run(FileChange(T, "M", base, head))
    assert rules(rep) == ["T3"]


def test_probe_4_loop_iterable_emptied():
    base = "from pkg.pricing import discount\n\n\ndef test_cap():\n    for d in [0.5, 0.9]:\n        assert discount(100, d) == 80.0\n"
    rep = run(FileChange(T, "M", base, base.replace("[0.5, 0.9]", "[]")))
    assert rules(rep) == ["T4"]


def test_probe_5_conftest_makereport_forces_pass():
    head = ("import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtest_makereport(item, call):\n"
            "    outcome = yield\n    report = outcome.get_result()\n    report.outcome = 'passed'\n")
    rep = run(FileChange("tests/conftest.py", "A", None, head))
    assert rules(rep) == ["H1"]
    assert rep["findings"][0]["severity"] == "blocking"
    assert rep["outcome"] == "FAILED"  # blocking with no contract at all (ADR 0012)


def test_probe_6_addopts_deselects_by_keyword():
    rep = run(FileChange("pytest.ini", "M", "[pytest]\n", "[pytest]\naddopts = -k 'not cap'\n"))
    assert rules(rep) == ["H4"]
    assert "-k not cap" in rep["findings"][0]["rationale"]


def test_probe_7_ci_ignores_test_failures():
    base = "on: [pull_request]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n"
    rep = run(FileChange(".github/workflows/ci.yml", "M", base, base.replace("run: pytest", "run: pytest || true")))
    assert rules(rep) == ["H5"]


# --------------------------------------------------------------------------- end to end through git


def _git(repo, *args):
    import subprocess

    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, check=True).stdout


def _repo(tmp_path):
    r = tmp_path / "repo"
    (r / "tests").mkdir(parents=True)
    (r / "src" / "pkg").mkdir(parents=True)
    (r / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (r / "tests" / "test_pricing.py").write_text(BASE, encoding="utf-8")
    (r / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    _git(r, "init", "-q", "-b", "main")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    return r


CONFTEST_HACK = ("import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtest_makereport(item, call):\n"
                 "    outcome = yield\n    outcome.get_result().outcome = 'passed'\n")


def test_check_command_fails_a_planted_conftest(tmp_path, capsys):
    from provecode import cli

    r = _repo(tmp_path)
    (r / "tests" / "conftest.py").write_text(CONFTEST_HACK, encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "head")
    code = cli.main(["check", "--repo", str(r), "--base", "HEAD~1", "--head", "HEAD",
                     "--contract", str(tmp_path / "missing.yaml")])
    out = capsys.readouterr().out
    assert code == 1 and "H1" in out


def test_guard_blocks_a_new_higher_precedence_config_and_a_shadow_module(tmp_path, capsys):
    from provecode import cli

    r = _repo(tmp_path)
    (r / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (r / "tests" / "pkg.py").write_text("def __getattr__(name):\n    return None\n", encoding="utf-8")
    code = cli.main(["guard", "--repo", str(r)])
    err = capsys.readouterr().err
    assert code == 1 and "H4" in err and "H3" in err
