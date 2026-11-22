"""Rules H1-H7 (ADR 0012): a true positive, legitimate near-misses that must not fire, and
regressions, per rule."""

import pytest

from provecode import pipeline
from provecode.changeset import Changeset, FileChange, classify, from_tuples
from provecode.harness import _bounds, dependencies, pytest_config

WF = ".github/workflows/ci.yml"


def run(*changes, base_paths=None, contract=None):
    return pipeline.run(Changeset(list(changes), base_paths=base_paths), contract)


def found(rep, rule=None):
    return [f for f in rep["findings"] if rule is None or f["rule"] == rule]


# --------------------------------------------------------------------------- changeset


@pytest.mark.parametrize("path,kind", [
    ("tests/conftest.py", "conftest"), ("conftest.py", "conftest"), ("sitecustomize.py", "harness"),
    ("src/x.pth", "harness"), ("tests/test_a.py", "test"), ("pyproject.toml", "config"), ("tox.ini", "config"),
    ("requirements/test.txt", "config"), ("requirements-dev.txt", "config"), (".github/workflows/ci.yaml", "ci"),
    ("src/pkg/core.py", "source"), ("README.md", "other"), ("docs/requirements.txt", "other"),
])
def test_classify(path, kind):
    assert classify(path) == kind


def test_legacy_tuples_and_changeset_give_identical_test_findings():
    base = "def test_a():\n    assert f(1) == 2\n"
    head = "def test_a():\n    assert f(1) >= 0\n"
    legacy = pipeline.run([("tests/test_a.py", base, head)], None)
    new = pipeline.run(from_tuples([("tests/test_a.py", base, head)]), None)
    assert legacy["findings"] == new["findings"] and legacy["outcome"] == new["outcome"]


# --------------------------------------------------------------------------- H1

CONF = "import pytest\n\n\n@pytest.fixture\ndef app():\n    return 1\n"


@pytest.mark.parametrize("snippet,impact", [
    ("def pytest_runtest_makereport(item, call):\n    pass\n", "outcome"),
    ("def pytest_pyfunc_call(pyfuncitem):\n    return True\n", "outcome"),
    ("def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n", "outcome"),
    ("def pytest_collection_modifyitems(config, items):\n    items[:] = []\n", "collection"),
    ("def pytest_ignore_collect(collection_path, config):\n    return True\n", "collection"),
    ("collect_ignore = ['tests/test_core.py']\n", "collection"),
    ("collect_ignore_glob = ['*_slow.py']\n", "collection"),
    ("import os\n\n\ndef pytest_unconfigure(config):\n    os._exit(0)\n", "process_exit"),
    ("import _pytest.reports\n\n_pytest.reports.TestReport.passed = True\n", "internals"),
    ("def pytest_configure(config):\n    config.option.markexpr = 'not slow'\n", "collection"),
])
def test_h1_blocks_outcome_and_collection_changes(snippet, impact):
    rep = run(FileChange("tests/conftest.py", "M", CONF, CONF + "\n\n" + snippet))
    h1 = found(rep, "H1")
    assert h1 and h1[0]["detail"]["impact"] == impact and h1[0]["severity"] == "blocking"
    assert rep["outcome"] == "FAILED"


@pytest.mark.parametrize("snippet", [
    "@pytest.fixture\ndef other():\n    return 2\n",
    "def pytest_configure(config):\n    config.addinivalue_line('markers', 'slow: slow tests')\n",
    "def pytest_report_header(config):\n    return 'hello'\n",
    "def pytest_addoption(parser):\n    parser.addoption('--runslow', action='store_true')\n",
    "def pytest_configure(config):\n    if config.option.verbose:\n        print('verbose')\n",
])
def test_h1_ignores_fixtures_markers_and_display_hooks(snippet):
    rep = run(FileChange("tests/conftest.py", "M", CONF, CONF + "\n\n" + snippet))
    assert found(rep) == []


def test_h1_unchanged_hook_is_not_a_finding_and_a_changed_one_is():
    hook = "def pytest_collection_modifyitems(config, items):\n    items.sort(key=lambda i: i.name)\n"
    assert found(run(FileChange("conftest.py", "M", CONF + hook, CONF + "\n" + hook))) == []
    changed = hook.replace("items.sort(key=lambda i: i.name)", "del items[1:]")
    assert found(run(FileChange("conftest.py", "M", CONF + hook, CONF + changed)), "H1")


def test_h1_collect_ignore_shrinking_is_not_a_finding():
    base = CONF + "collect_ignore = ['a.py', 'b.py']\n"
    assert found(run(FileChange("conftest.py", "M", base, CONF + "collect_ignore = ['a.py']\n"))) == []


def test_h1_unparseable_conftest_is_a_gap_not_a_crash():
    rep = run(FileChange("conftest.py", "M", CONF, "def broken(:\n"))
    assert found(rep) == [] and any(g["reason"] == "unparseable" for g in rep["gaps"])


# --------------------------------------------------------------------------- H2


def test_h2_pytest_plugins_added():
    rep = run(FileChange("conftest.py", "M", CONF, CONF + "pytest_plugins = ['tests.evil']\n"))
    assert [f["rule"] for f in found(rep)] == ["H2"]
    assert found(rep)[0]["severity"] == "warning"


@pytest.mark.parametrize("opt,expect", [
    ("-p no:cacheprovider", False), ("-p no:xdist", False), ("-p no:randomly", True),
    ("-p no:warnings", True), ("-p no:cov", True), ("-p myplugin", True),
])
def test_h2_disabling_a_plugin_that_cannot_change_results_is_fine(opt, expect):
    base = "[pytest]\naddopts = -ra\n"
    rep = run(FileChange("pytest.ini", "M", base, base.replace("-ra", f"-ra {opt}")))
    assert bool(found(rep, "H2")) is expect


def test_h2_addopts_plugin_and_pytest11_entry_point():
    rep = run(FileChange("pytest.ini", "M", "[pytest]\n", "[pytest]\naddopts = -p no:randomly\n"))
    assert [f["rule"] for f in found(rep)] == ["H2"]
    base = "[project]\nname = 'x'\n"
    head = base + "\n[project.entry-points.pytest11]\nx = 'x.plugin'\n"
    assert [f["rule"] for f in found(run(FileChange("pyproject.toml", "M", base, head)))] == ["H2"]


PKG_TREE = ("src/shop/__init__.py", "src/shop/core.py", "tests/test_core.py", "tests/conftest.py")
AUTOUSE = "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef _fake(monkeypatch):\n    {body}\n"


@pytest.mark.parametrize("body", [
    "monkeypatch.setattr('shop.core.price', lambda *a: 80)",
    "monkeypatch.setattr(shop.core, 'price', lambda *a: 80)",
    "mocker.patch('shop.core.price', return_value=80)",
])
def test_h1_autouse_fixture_patching_the_code_under_test(body):
    head = "import shop.core\n" + AUTOUSE.format(body=body)
    rep = run(FileChange("tests/conftest.py", "A", None, head), base_paths=PKG_TREE)
    h1 = found(rep, "H1")
    assert h1 and h1[0]["detail"]["impact"] == "patches_code_under_test"


@pytest.mark.parametrize("body", [
    "monkeypatch.setenv('SHOP_ENV', 'test')",
    "monkeypatch.setattr('time.time', lambda: 0)",
    "monkeypatch.setattr('requests.get', lambda *a, **k: None)",
])
def test_h1_autouse_fixture_patching_environment_or_third_party_is_fine(body):
    rep = run(FileChange("tests/conftest.py", "A", None, AUTOUSE.format(body=body)), base_paths=PKG_TREE)
    assert found(rep) == []


def test_h1_non_autouse_fixture_patching_the_code_under_test_is_fine():
    head = AUTOUSE.format(body="monkeypatch.setattr('shop.core.price', lambda *a: 80)").replace("(autouse=True)", "")
    assert found(run(FileChange("tests/conftest.py", "A", None, head), base_paths=PKG_TREE)) == []


def test_h1_existing_autouse_patch_unchanged_is_fine():
    text = AUTOUSE.format(body="monkeypatch.setattr('shop.core.price', lambda *a: 80)")
    rep = run(FileChange("tests/conftest.py", "M", text, text + "\n\ndef helper():\n    return 1\n"),
              base_paths=PKG_TREE)
    assert found(rep) == []


# --------------------------------------------------------------------------- H3


def test_h3_startup_files():
    for path in ("sitecustomize.py", "src/usercustomize.py", "evil.pth"):
        rep = run(FileChange(path, "A", None, "import os\n"))
        assert [f["rule"] for f in found(rep)] == ["H3"], path
        assert rep["outcome"] == "FAILED"


def test_h3_shadowing_modules():
    paths = ("src/pkg/__init__.py", "tests/test_a.py")
    assert found(run(FileChange("tests/pkg.py", "A", None, "x = 1\n"), base_paths=paths), "H3")
    assert found(run(FileChange("tests/json.py", "A", None, "x = 1\n"), base_paths=paths), "H3")
    assert found(run(FileChange("pytest.py", "A", None, "x = 1\n"), base_paths=paths), "H3")


def test_h3_near_misses():
    paths = ("src/pkg/__init__.py", "tests/__init__.py", "tests/test_a.py")
    for path in ("tests/helpers.py", "src/pkg/json.py", "tests/json.py", "docs/conf.py"):
        assert found(run(FileChange(path, "A", None, "x = 1\n"), base_paths=paths), "H3") == [], path


# --------------------------------------------------------------------------- H4

PYPROJECT = '[project]\nname = "x"\n\n[tool.pytest.ini_options]\naddopts = "-ra"\ntestpaths = ["tests", "docs"]\n'


@pytest.mark.parametrize("head", [
    PYPROJECT.replace('"-ra"', '"-ra -m \'not slow\'"'),
    PYPROJECT.replace('"-ra"', '"-ra --deselect tests/test_a.py::test_x"'),
    PYPROJECT.replace('"-ra"', '"-ra --ignore=tests/test_b.py"'),
    PYPROJECT.replace('["tests", "docs"]', '["tests"]'),
    PYPROJECT + 'python_files = ["test_core.py"]\n',
    PYPROJECT + 'norecursedirs = ["tests/slow"]\n',
])
def test_h4_selection_narrowed(head):
    assert found(run(FileChange("pyproject.toml", "M", PYPROJECT, head)), "H4")


@pytest.mark.parametrize("head", [
    PYPROJECT.replace('"-ra"', '"-ra --strict-markers"'),
    PYPROJECT.replace('["tests", "docs"]', '["tests", "docs", "examples"]'),
    PYPROJECT + 'markers = ["slow: slow"]\n',
    PYPROJECT + 'xfail_strict = true\n',
])
def test_h4_near_misses(head):
    assert found(run(FileChange("pyproject.toml", "M", PYPROJECT, head))) == []


def test_h4_native_toml_table_and_xfail_strict():
    base = '[tool.pytest]\naddopts = ["-ra"]\nxfail_strict = true\n'
    assert found(run(FileChange("pyproject.toml", "M", base, base.replace("true", "false"))), "H4")
    assert pytest_config("pyproject.toml", base) == {"addopts": ["-ra"], "xfail_strict": True}


def test_h4_higher_precedence_config_file_shadows_the_old_one():
    rep = run(FileChange("pytest.ini", "A", None, "[pytest]\n"),
              FileChange("pyproject.toml", "U", PYPROJECT, PYPROJECT))
    h4 = found(rep, "H4")
    assert h4 and "takes precedence" in h4[0]["rationale"]


def test_h4_new_config_without_an_older_one_is_fine():
    assert found(run(FileChange("pytest.ini", "A", None, "[pytest]\naddopts = -ra\n"))) == []


@pytest.mark.parametrize("base,head", [
    ('[tool.pytest.ini_options]\nfilterwarnings = ["error"]\n', '[tool.pytest.ini_options]\nfilterwarnings = []\n'),
    ('[tool.pytest.ini_options]\nfilterwarnings = ["error", "ignore::UserWarning"]\n',
     '[tool.pytest.ini_options]\nfilterwarnings = ["ignore::UserWarning"]\n'),
    ('[tool.pytest.ini_options]\nfilterwarnings = ["error::DeprecationWarning"]\n', '[tool.pytest.ini_options]\n'),
])
def test_h4_warnings_as_errors_removed(base, head):
    h4 = found(run(FileChange("pyproject.toml", "M", base, head)), "H4")
    assert h4 and "warnings" in h4[0]["rationale"]


def test_h4_warnings_as_errors_removed_in_ini_line_list():
    base = "[pytest]\nfilterwarnings =\n    error\n    ignore:some message here:UserWarning\n"
    head = "[pytest]\nfilterwarnings =\n    ignore:some message here:UserWarning\n"
    assert found(run(FileChange("pytest.ini", "M", base, head)), "H4")


@pytest.mark.parametrize("head", [
    '[tool.pytest.ini_options]\nfilterwarnings = ["error", "ignore::DeprecationWarning:thirdparty"]\n',
    '[tool.pytest.ini_options]\nfilterwarnings = ["error"]\nmarkers = ["slow: slow"]\n',
])
def test_h4_warning_filter_near_misses(head):
    base = '[tool.pytest.ini_options]\nfilterwarnings = ["error"]\n'
    assert found(run(FileChange("pyproject.toml", "M", base, head))) == []


SUB = "engine/pyproject.toml"


def test_h4_reads_pytest_config_in_a_subdirectory():
    rep = run(FileChange(SUB, "M", PYPROJECT, PYPROJECT.replace('"-ra"', '"-ra -k \'not action\'"')))
    h4 = found(rep, "H4")
    assert h4 and h4[0]["file"] == SUB and "-k not action" in h4[0]["rationale"]


def test_h4_subdirectory_config_change_unrelated_to_pytest_is_fine():
    rep = run(FileChange(SUB, "M", PYPROJECT, PYPROJECT + "\n[tool.ruff]\nline-length = 120\n"))
    assert found(rep) == []


def test_h4_new_nested_config_shadows_the_parent_directory_config():
    rep = run(FileChange("engine/tests/pytest.ini", "A", None, "[pytest]\n"),
              FileChange(SUB, "U", PYPROJECT, PYPROJECT))
    h4 = found(rep, "H4")
    assert h4 and h4[0]["file"] == "engine/tests/pytest.ini" and SUB in h4[0]["rationale"]


def test_h4_nested_config_with_its_own_settings_is_fine_when_none_were_inherited():
    rep = run(FileChange("engine/tests/pytest.ini", "A", None, "[pytest]\naddopts = -ra\n"))
    assert found(rep) == []


def test_h4_subdirectory_testpaths_change_names_lost_files_by_full_path():
    tree = ("engine/pyproject.toml", "engine/tests/test_core.py", "engine/tests/slow/test_big.py", "tests/test_top.py")
    rep = run(FileChange(SUB, "M", _cfg('testpaths = ["tests"]\n'), _cfg('testpaths = ["tests/slow"]\n')),
              base_paths=tree)
    h4 = found(rep, "H4")
    assert len(h4) == 1 and h4[0]["detail"]["lost_files"] == ["engine/tests/test_core.py"]


def test_h4_unparseable_config_is_a_gap():
    rep = run(FileChange("pyproject.toml", "M", PYPROJECT, PYPROJECT + "[broken\n"))
    assert any(g["reason"] == "unparseable" for g in rep["gaps"])


# --------------------------------------------------------------------------- H5

WORKFLOW = """name: CI
on:
  push:
    branches: [main]
  pull_request:
jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest]
        python: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - name: Install
        run: pip install -e . tox
      - name: Run tests
        run: |
          python -m pytest tests
      - name: Lint
        run: ruff check .
"""


@pytest.mark.parametrize("head,change", [
    (WORKFLOW.replace("python -m pytest tests", "python -m pytest tests || true"), "exit_ignored"),
    (WORKFLOW.replace("      - name: Lint", "        continue-on-error: true\n      - name: Lint"), "continue_on_error"),
    (WORKFLOW.replace("      - name: Lint", "        if: false\n      - name: Lint"), "never_runs"),
    (WORKFLOW.replace("      - name: Run tests\n        run: |\n          python -m pytest tests\n", ""), "step_removed"),
    (WORKFLOW.replace("python -m pytest tests", "python -m pytest tests -k 'not slow'"), "narrowed"),
    (WORKFLOW.replace("python -m pytest tests", "python -m pytest tests/test_a.py"), "narrowed"),
    (WORKFLOW.replace("  pull_request:\n", "  pull_request:\n    paths-ignore: ['**.py']\n"), "paths_ignore"),
    (WORKFLOW.replace("  pull_request:\n", ""), "trigger_removed"),
    (WORKFLOW.replace("    runs-on:", "    continue-on-error: true\n    runs-on:"), "continue_on_error"),
])
def test_h5_ci_weakened(head, change):
    h5 = found(run(FileChange(WF, "M", WORKFLOW, head)), "H5")
    assert change in [f["detail"]["change"] for f in h5], h5


@pytest.mark.parametrize("head", [
    WORKFLOW.replace('python: ["3.11", "3.12"]', 'python: ["3.11", "3.12", "3.13"]'),
    WORKFLOW.replace("ruff check .", "ruff check . || true"),
    WORKFLOW.replace("        run: ruff check .", "        run: ruff check .\n        continue-on-error: true"),
    WORKFLOW.replace("      - name: Lint", "      - run: python -m pip list\n      - name: Lint"),
    WORKFLOW.replace("python -m pytest tests", "python -m pytest tests -n auto"),
    WORKFLOW.replace("  pull_request:\n", "  pull_request:\n    paths-ignore: ['docs/**']\n"),
    WORKFLOW.replace("run: pip install -e . tox", "run: pip install -e . tox || true"),
])
def test_h5_near_misses(head):
    assert found(run(FileChange(WF, "M", WORKFLOW, head))) == []


def test_h5_workflow_with_tests_deleted():
    assert found(run(FileChange(WF, "D", WORKFLOW, None)), "H5")


def test_h5_anchors_aliases_and_flow_style_parse():
    wf = ("on: [push, pull_request]\njobs:\n  a: &job\n    runs-on: ubuntu-latest\n    steps:\n"
          "      - run: tox\n  b: *job\n")
    rep = run(FileChange(WF, "M", wf, wf.replace("- run: tox\n", "- run: tox || true\n", 1)))
    assert found(rep, "H5")


def test_h5_unsupported_yaml_is_a_gap_never_a_guess():
    wf = "on: push\njobs:\n  a:\n    <<: *x\n"
    rep = run(FileChange(WF, "M", WORKFLOW, wf))
    assert found(rep) == [] and any(g["reason"] == "unparseable" for g in rep["gaps"])


# --------------------------------------------------------------------------- H6

RC = "[run]\nsource = pkg\nomit =\n    tests/*\n\n[report]\nfail_under = 90\n"


@pytest.mark.parametrize("head,key", [
    (RC.replace("fail_under = 90", "fail_under = 80"), "fail_under"),
    (RC.replace("fail_under = 90\n", ""), "fail_under"),
    (RC.replace("    tests/*\n", "    tests/*\n    pkg/core.py\n"), "omit"),
    (RC + "exclude_lines =\n    def \n", "exclude"),
])
def test_h6_coverage_config_weakened(head, key):
    h6 = found(run(FileChange(".coveragerc", "M", RC, head)), "H6")
    assert h6 and h6[0]["detail"]["key"] == key


@pytest.mark.parametrize("head", [
    RC.replace("fail_under = 90", "fail_under = 95"),
    RC.replace("    tests/*\n", "    tests/*\n    */tests/conftest.py\n"),
    RC + "exclude_also =\n    if TYPE_CHECKING:\n",
])
def test_h6_near_misses(head):
    assert found(run(FileChange(".coveragerc", "M", RC, head))) == []


def test_h6_pragma_on_non_trivial_source_line():
    base = "def f(x):\n    if x:\n        return x * 2\n    return 0\n\n\nif __name__ == '__main__':\n    f(1)\n"
    weak = base.replace("return x * 2", "return x * 2  # pragma: no cover")
    assert found(run(FileChange("src/pkg/core.py", "M", base, weak)), "H6")
    trivial = base.replace("if __name__ == '__main__':", "if __name__ == '__main__':  # pragma: no cover")
    assert found(run(FileChange("src/pkg/core.py", "M", base, trivial))) == []


@pytest.mark.parametrize("line", [
    'MSG = "add `# pragma: no cover` to skip"\n',
    "DOC = '''\nx = 1  # pragma: no cover\n'''\n",
])
def test_h6_pragma_text_inside_a_string_is_not_a_pragma(line):
    base = "def f(x):\n    return x * 2\n"
    assert found(run(FileChange("src/pkg/core.py", "M", base, base + line))) == []


def test_h6_pragma_still_found_when_the_file_does_not_tokenize():
    base = "def f(x):\n    return x * 2\n"
    head = "def f(x):\n    return x * 2  # pragma: no cover\n    '''unterminated\n"
    assert found(run(FileChange("src/pkg/core.py", "M", base, head)), "H6")


def test_h6_cov_fail_under_lowered_in_addopts():
    base = "[pytest]\naddopts = --cov=pkg --cov-fail-under=99\n"
    assert found(run(FileChange("tox.ini", "M", base, base.replace("99", "50"))), "H6")


CI_PY = "on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest --cov=pkg\n"


@pytest.mark.parametrize("head,expect", [
    (CI_PY.replace("run: pytest", "run: python -m pytest"), False),
    (CI_PY.replace("run: pytest", "run: python3.12 -m pytest"), False),
    (CI_PY.replace("run: pytest", "run: python -m pytest -m 'not slow'"), True),
])
def test_h5_python_dash_m_is_the_runner_not_a_marker_filter(head, expect):
    assert bool(found(run(FileChange(WF, "M", CI_PY, head)), "H5")) is expect


TOX = "[tox]\nenv_list = py, lint\n\n[testenv]\ndeps = pytest\ncommands =\n    pytest --cov=pkg --cov-fail-under=90 {posargs}\n\n[testenv:lint]\ncommands = ruff check .\n"


@pytest.mark.parametrize("head,what", [
    (TOX.replace("pytest --cov", "pytest -k 'not slow' --cov"), "fewer tests"),
    (TOX.replace("    pytest --cov", "    - pytest --cov"), "exit status"),
    (TOX.replace("    pytest --cov=pkg --cov-fail-under=90 {posargs}\n", "    python -c 'print(1)'\n"), "no longer runs"),
    (TOX.replace("--cov-fail-under=90", "--cov-fail-under=10"), "coverage threshold"),
])
def test_h5_tox_test_command_weakened(head, what):
    fs = found(run(FileChange("tox.ini", "M", TOX, head)))
    assert fs and any(what in f["rationale"] for f in fs), fs


@pytest.mark.parametrize("head", [
    TOX.replace("pytest --cov", "pytest -n auto --cov"),
    TOX.replace("ruff check .", "ruff check --fix ."),
    TOX.replace("deps = pytest", "deps =\n    pytest\n    pytest-xdist"),
    TOX + "\n[testenv:docs]\ncommands = sphinx-build docs out\n",
    TOX.replace("pytest --cov", "python -m pytest --cov"),
])
def test_h5_tox_near_misses(head):
    assert found(run(FileChange("tox.ini", "M", TOX, head))) == []


CI_COV = ("on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
          "      - run: pytest --cov=pkg --cov-fail-under=90\n")


@pytest.mark.parametrize("head,expect", [
    (CI_COV.replace("=90", "=10"), True),
    (CI_COV.replace(" --cov-fail-under=90", ""), True),
    (CI_COV.replace("=90", "=95"), False),
    (CI_COV, False),
])
def test_h6_cov_fail_under_lowered_in_a_ci_test_command(head, expect):
    rep = run(FileChange(WF, "M", CI_COV, head))
    assert bool(found(rep, "H6")) is expect


# --------------------------------------------------------------------------- H7

DEPS = '[dependency-groups]\ntest = [\n    "pytest>=8",\n    "pytest-cov",\n    "hypothesis>=6",\n]\n'


def test_h7_plugin_removed_and_pytest_pinned_down():
    rep = run(FileChange("pyproject.toml", "M", DEPS, DEPS.replace('    "pytest-cov",\n', "")))
    assert [f["detail"]["change"] for f in found(rep, "H7")] == ["removed"]
    rep = run(FileChange("pyproject.toml", "M", DEPS, DEPS.replace('"pytest>=8"', '"pytest<7"')))
    assert [f["detail"]["change"] for f in found(rep, "H7")] == ["pinned_down"]
    assert all(f["severity"] == "warning" for f in found(rep))


def test_h7_near_misses_and_moves():
    assert found(run(FileChange("pyproject.toml", "M", DEPS, DEPS.replace('"pytest>=8"', '"pytest>=9"')))) == []
    added = DEPS.replace('    "pytest-cov",\n', '    "pytest-cov",\n    "pytest-timeout",\n')
    assert found(run(FileChange("pyproject.toml", "M", DEPS, added))) == []
    # Moved from pyproject to a requirements file in the same change: not a removal.
    moved = run(FileChange("pyproject.toml", "M", DEPS, DEPS.replace('    "pytest-cov",\n', "")),
                FileChange("requirements/test.txt", "A", None, "pytest-cov\n"))
    assert found(moved, "H7") == []


def test_h7_version_bounds():
    assert _bounds([">= 9.0"]) == (9, None)
    assert _bounds(["<7"]) == (None, 6)
    assert _bounds(["<7.2"]) == (None, 7)
    assert _bounds(["~=7.2"]) == (7, 7)
    assert dependencies("requirements.txt", "pytest==8.1  # pinned\n-r base.txt\nrequests\n") == {"pytest": ["==8.1"]}


# --------------------------------------------------------------------------- grading


def test_contract_makes_h2_h4_h5_h6_blocking_but_never_h7():
    from provecode import contract

    doc = contract.approve({"contract": {"id": "c", "version": 1}, "conditions": [
        {"id": "C1", "description": "d", "type": "functional", "mandatory": True, "guarded_by": [], "checks": []}],
        "checks": []}, "owner", "2026-11-21T00:00:00Z")
    rep = run(FileChange("pytest.ini", "M", "[pytest]\n", "[pytest]\naddopts = -k 'not cap'\n"), contract=doc)
    assert found(rep, "H4")[0]["severity"] == "blocking" and rep["outcome"] == "FAILED"
    rep = run(FileChange("pyproject.toml", "M", DEPS, DEPS.replace('    "pytest-cov",\n', "")), contract=doc)
    assert found(rep, "H7")[0]["severity"] == "warning"


# --------------------------------------------------------------------------- regressions (Tier H tuning round 1)


def test_regression_pragma_on_aliased_type_checking_is_trivial():
    base = "import typing as t\n\nif t.TYPE_CHECKING:\n    import os\n"
    head = base.replace("if t.TYPE_CHECKING:", "if t.TYPE_CHECKING:  # pragma: no cover")
    assert found(run(FileChange("src/pkg/a.py", "M", base, head))) == []


def test_regression_capping_an_unbounded_dependency_is_pinning_down():
    base = '[dependency-groups]\ndev = [\n    "pytest>=6.0",\n]\n'
    rep = run(FileChange("pyproject.toml", "M", base, base.replace('"pytest>=6.0"', '"pytest<7"')))
    assert [f["detail"]["change"] for f in found(rep, "H7")] == ["pinned_down"]


def test_regression_shadow_inside_a_test_package_is_advisory():
    paths = ("arrow/__init__.py", "tests/__init__.py", "tests/test_a.py")
    rep = run(FileChange("tests/arrow.py", "A", None, "x = 1\n"), base_paths=paths)
    h3 = found(rep, "H3")
    assert h3 and h3[0]["severity"] == "warning" and h3[0]["detail"]["advisory"]
    assert rep["outcome"] != "FAILED"


# --------------------------------------------------------------------------- H4 by simulated collection (real-history review)

TREE = ("pyproject.toml", "src/pkg/__init__.py", "tests/test_core.py", "tests/test_io.py", "tests/slow/test_big.py",
        "docs/conf.py", "scripts/check_x.py")


def _cfg(extra: str) -> str:
    return "[tool.pytest.ini_options]\n" + extra


@pytest.mark.parametrize("before,after", [
    ("", 'testpaths = ["tests"]\n'),                                    # introduced, but every test is under tests/
    ('testpaths = ["tests", "docs"]\n', 'testpaths = ["tests"]\n'),     # removed entry held no tests
    ('python_files = ["test_*.py", "*_test.py"]\n', 'python_files = ["test_*.py"]\n'),  # no *_test.py files exist
    ("", 'norecursedirs = ["docs", "scripts"]\n'),                      # no tests there
])
def test_h4_selection_changes_that_lose_no_test_file_are_not_findings(before, after):
    rep = run(FileChange("pyproject.toml", "M", _cfg(before), _cfg(after)), base_paths=TREE)
    assert found(rep, "H4") == []


@pytest.mark.parametrize("before,after,lost", [
    ("", 'testpaths = ["tests/slow"]\n', "tests/test_core.py"),
    ('testpaths = ["tests"]\n', 'testpaths = ["tests/slow"]\n', "tests/test_io.py"),
    ("", 'python_files = ["test_core.py"]\n', "tests/test_io.py"),
    ("", 'norecursedirs = ["slow"]\n', "tests/slow/test_big.py"),
])
def test_h4_selection_changes_that_lose_test_files_name_them(before, after, lost):
    rep = run(FileChange("pyproject.toml", "M", _cfg(before), _cfg(after)), base_paths=TREE)
    h4 = found(rep, "H4")
    assert len(h4) == 1 and lost in h4[0]["detail"]["lost_files"]


def test_h4_without_a_file_list_still_judges_by_value():
    rep = run(FileChange("pyproject.toml", "M", _cfg(""), _cfg('testpaths = ["tests"]\n')))
    assert found(rep, "H4")


@pytest.mark.parametrize("line,expected", [
    ("tox", True), ("tox run -e py311", True), ("uvx --with tox-uv tox run -f typing", False),
    ("tox -e lint -- --show-diff-on-failure", False), ("tox run -e docs-doctests", True),
    ("uv run --locked tox run -e typing", False), ("nox -s lint mypy", False), ("nox -s tests-3.12", True),
    ("nox --session docs", False), ("tox -e py311,lint", True), ("pip install tox", False),
    ("python -m pytest tests", True), ("coverage run -m pytest", True), ("ruff check .", False),
])
def test_h5_test_command_recognition(line, expected):
    from provecode.harness import is_test_command

    assert is_test_command(line) is expected


def test_h5_removing_a_lint_job_run_through_tox_is_not_a_finding():
    wf = ("on: [push]\njobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n      - run: tox -e lint\n"
          "  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: tox -e py\n")
    head = wf.replace("  lint:\n    runs-on: ubuntu-latest\n    steps:\n      - run: tox -e lint\n", "")
    assert found(run(FileChange(WF, "M", wf, head))) == []
    assert found(run(FileChange(WF, "M", wf, wf.replace("tox -e py\n", "tox -e py || true\n"))), "H5")


@pytest.mark.parametrize("pattern", [r"\.\.\.", r"\.{3}", r"^\s*\.\.\.\s*$", "if TYPE_CHECKING:"])
def test_h6_boilerplate_exclude_regexes_are_benign(pattern):
    head = RC + "exclude_also =\n    " + pattern + "\n"
    assert found(run(FileChange(".coveragerc", "M", RC, head))) == []


@pytest.mark.parametrize("pattern", ["**/__main__.py", "src/pkg/_version.py"])
def test_h6_omitting_entry_shims_and_version_files_is_benign(pattern):
    head = RC.replace("    tests/*\n", "    tests/*\n    " + pattern + "\n")
    assert found(run(FileChange(".coveragerc", "M", RC, head))) == []


def test_regression_a_malformed_base_job_is_not_a_crash():
    # Found by mypy --strict: a job that is not a mapping in the base workflow.
    base = "on: [push]\njobs:\n  a: oops\n  t:\n    steps:\n      - run: pytest\n"
    head = "on: [push]\njobs:\n  a: oops\n  t:\n    steps:\n      - run: pytest || true\n"
    assert found(run(FileChange(WF, "M", base, head)), "H5")


def test_h2_pytest_plugins_added_in_a_test_module():
    base = "def test_a():\n    assert 1 == 1\n"
    rep = run(FileChange("tests/test_a.py", "M", base, base + "pytest_plugins = ['tests.evil']\n"))
    assert "H2" in [f["rule"] for f in found(rep)]


def test_regression_h2_module_does_not_parse_a_module_without_pytest_plugins(monkeypatch):
    # Found by the guard timing eval: parsing a 1.5 MB test module twice only to look for pytest_plugins cost 11 s.
    # Without the identifier in the head text no assignment to it can exist, so the rule cannot fire.
    from provecode import harness
    from provecode.limits import Limits

    def boom(*a, **k):
        raise AssertionError("parsed")

    monkeypatch.setattr(harness, "_parse_py", boom)
    fc = FileChange("tests/test_a.py", "M", "def test_a():\n    pass\n", "def test_a():\n    assert 1\n")
    assert harness._h2_module(fc, Limits.cli()) == ([], [])


def test_regression_h2_module_still_parses_a_non_ascii_spelling():
    # Python NFKC-normalises identifiers, so a full-width first letter is the same name in the AST.
    base = "def test_a():\n    assert 1 == 1\n"
    head = base + chr(0xFF50) + "ytest_plugins = ['tests.evil']\n"
    rep = run(FileChange("tests/test_a.py", "M", base, head))
    assert "H2" in [f["rule"] for f in found(rep)]


def test_regression_h2_module_does_not_parse_non_ascii_data_without_pytest_plugins(monkeypatch):
    # kjd/idna's large test module is full of non-ASCII test data; that alone must not force the double parse.
    from provecode import harness
    from provecode.limits import Limits

    def boom(*a, **k):
        raise AssertionError("parsed")

    monkeypatch.setattr(harness, "_parse_py", boom)
    head = "def test_a():\n    assert decode('" + chr(0x1E906) + "') == '" + chr(0x1E928) + "'\n"
    fc = FileChange("tests/test_a.py", "M", "def test_a():\n    pass\n", head)
    assert harness._h2_module(fc, Limits.cli()) == ([], [])


# --------------------------------------------------------------------------- H5 command parsing (real history)
# Each near-miss is the shape of a real commit that the 0.2.0 real-history scan flagged
# (docs/research/data/real_history_scan_v2.json). The engine reads them as fewer tests only by misparsing.

def _tox(cmd: str, env: str = "testenv") -> str:
    return f"[tox]\nenv_list = py\n\n[{env}]\ndeps = pytest\ncommands =\n    {cmd}\n"


@pytest.mark.parametrize("before,after", [
    # python-attrs/attrs 6879ffd11d: a {posargs} default of options, not paths
    ("pytest {posargs}", "pytest {posargs:-n auto}"),
    ("coverage run -m pytest {posargs}", "coverage run -m pytest {posargs:-n auto}"),
    # pytest-dev/pluggy e413c70d01: the default path widened to its directory
    ("py.test {posargs:testing/test_pluggy.py}", "py.test {posargs:testing/}"),
    ("pytest tests", "pytest {posargs:tests}"),
    # jpadilla/pyjwt 97cd403154: {envpython} and an interpreter flag before -m
    ("coverage run -m pytest {posargs}", "{envpython} -b -m coverage run -m pytest {posargs}"),
    # Python-Markdown 49249b3fb6: coverage run's -m names the module
    ("coverage run --source=markdown {toxinidir}/run-tests.py {posargs}",
     "coverage run --source=markdown -m unittest discover {toxinidir}/tests"),
    # Delgan/loguru 37104c8be0: option values are not paths
    ("pytest --cov loguru/ --cov-report=xml", "pytest --cov loguru/ --cov-report xml --cov-report term-missing"),
    # pyparsing 567aa9e805: the base did not run pytest, so its paths cannot be compared
    ("coverage run --parallel --branch -m unittest", "pytest tests"),
])
def test_regression_h5_tox_command_rewrites_that_run_the_same_tests(before, after):
    assert found(run(FileChange("tox.ini", "M", _tox(before), _tox(after))), "H5") == []


def test_regression_h5_tox_lint_env_is_not_a_test_env():
    # scrapy/w3lib 6c23d82b0d: [testenv:flake8] moved from `pytest --flake8` to plain flake8.
    base, head = _tox("pytest --flake8", "testenv:flake8"), _tox("flake8 w3lib tests", "testenv:flake8")
    assert found(run(FileChange("tox.ini", "M", base, head)), "H5") == []


@pytest.mark.parametrize("before,after", [
    ("pytest {posargs:tests}", "pytest {posargs:tests/unit}"),
    ("pytest tests", "pytest {posargs:tests/test_a.py}"),
    ("coverage run -m pytest {posargs}", "coverage run -m pytest -k 'not slow' {posargs}"),
    ("{envpython} -m pytest {posargs}", "{envpython} -m pytest -m 'not slow' {posargs}"),
    ("pytest --cov-report xml tests", "pytest --cov-report xml tests/test_a.py"),
])
def test_h5_tox_narrowing_through_the_same_spellings_still_fires(before, after):
    fs = found(run(FileChange("tox.ini", "M", _tox(before), _tox(after))), "H5")
    assert fs and "fewer tests" in fs[0]["rationale"], fs


def test_h5_tox_test_env_named_like_a_test_is_still_a_test_env():
    base, head = _tox("pytest", "testenv:py3{9,10}-tests"), _tox("python -c 'print(1)'", "testenv:py3{9,10}-tests")
    assert found(run(FileChange("tox.ini", "M", base, head)), "H5")


def test_regression_h5_workflow_coverage_run_unittest_is_not_a_marker_filter():
    # hukkin/tomli f0d053f0a9: `python -m pytest` replaced by `coverage run -m unittest`.
    wf = "on: [push]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: {}\n"
    rep = run(FileChange(WF, "M", wf.format("python -m pytest --cov"), wf.format("coverage run -m unittest")))
    assert [f for f in found(rep, "H5") if "fewer tests" in f["rationale"]] == []


def test_regression_h5_workflow_matrix_expression_is_one_unknown_value():
    # marshmallow-code/marshmallow fb49a6671c: `tox -e ${{ matrix.toxenv }}` was split mid-expression.
    wf = "on: [push]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: {}\n"
    rep = run(FileChange(WF, "M", wf.format("tox -e py311"), wf.format("tox -e ${{ matrix.toxenv }}")))
    assert found(rep, "H5") == []
    rep = run(FileChange(WF, "M", wf.format("tox -e py311,py312"), wf.format("tox -e py311")))
    assert found(rep, "H5")  # a literal narrowing still fires


def test_regression_a_shell_comment_is_not_a_test_command():
    # hukkin/tomli c5045f4778: a comment mentioning `python -m pytest` was paired with the base command,
    # so the unchanged --cov-fail-under=100 looked removed.
    wf = "on: [push]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - name: Test\n        run: |\n{}"
    base = wf.format("          pytest --cov --cov-fail-under=100\n")
    head = wf.format("          # Use 'python -m pytest' to add CWD to sys.path\n"
                     "          python -m pytest --cov --cov-fail-under=100\n")
    assert found(run(FileChange(WF, "M", base, head))) == []
    lowered = head.replace("=100", "=50")
    assert found(run(FileChange(WF, "M", base, lowered)), "H6")
