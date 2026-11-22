"""A catalog of common ways tests get weakened, and of legitimate edits that look similar, run
through the engine as `provecode guard` would judge them (block on any weakening finding).

  python docs/research/scripts/cheat_catalog.py            print a table; exit 1 if any case is wrong
  python docs/research/scripts/cheat_catalog.py --json F   also write the results to F

Every case is hand-written for this catalog (2026-11-22) from patterns engineers and coding agents
use to get a red suite green; it is not drawn from the benchmark tiers, and it is not an
independent sample: whoever writes the engine's rules also wrote these. It finds blind spots; it
does not measure recall or precision. Each edit must apply to its base text, or the case errors.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "src"))

from provecode import pipeline  # noqa: E402
from provecode.changeset import Changeset, FileChange  # noqa: E402
from provecode.guard import blocks  # noqa: E402

T = "tests/test_shop.py"
PY = textwrap.dedent('''\
    from unittest import mock

    import pytest
    from shop import price, parse, Cart


    def test_price():
        assert price(100, 0.5) == 80


    def test_parse_error():
        with pytest.raises(ValueError, match="bad input"):
            parse("x")


    def test_cart():
        c = Cart()
        c.add("a", 2)
        assert c.total() == 2
        assert c.items == ["a", "a"]


    @pytest.mark.parametrize("n,exp", [(1, 1), (2, 4), (3, 9)])
    def test_square(n, exp):
        assert n * n == exp


    def test_flags():
        r = parse("ok")
        assert r.ok and r.code == 200
''')

U = "tests/test_unit.py"
UT = textwrap.dedent('''\
    import unittest
    from shop import price, parse


    class TestShop(unittest.TestCase):
        def test_price(self):
            self.assertEqual(price(100, 0.5), 80)

        def test_parse(self):
            with self.assertRaises(ValueError):
                parse("x")

        def test_close(self):
            self.assertAlmostEqual(price(3, 0.1), 2.7, places=6)
''')

PP = "pyproject.toml"
PPT = '[project]\nname = "shop"\n\n[tool.pytest.ini_options]\naddopts = "-ra --strict-markers"\ntestpaths = ["tests"]\nxfail_strict = true\nfilterwarnings = ["error"]\n'
WF = ".github/workflows/ci.yml"
WFT = textwrap.dedent('''\
    name: ci
    on: [push]
    jobs:
      test:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v4
          - run: pip install -e .[test]
          - run: pytest --cov=shop --cov-fail-under=90
''')
CF = "tests/conftest.py"
CFT = "import pytest\n\n\n@pytest.fixture\ndef cart():\n    from shop import Cart\n    return Cart()\n"
SRC = "src/shop/core.py"
SRCT = "def price(x, d):\n    return min(x * d, 80) if d > 0.4 else x * (1 - d)\n"
TREE = (PP, "src/shop/__init__.py", SRC, T, U, CF, WF)


@dataclass
class Case:
    id: str
    kind: str  # weakening | legit
    what: str
    edits: list[tuple[str, str, str, str | None]]  # (path, base, old, new); new None = delete file; old "" = add
    added: dict[str, str] | None = None  # new files (path -> text)


def e(path: str, base: str, old: str, new: str | None) -> tuple[str, str, str, str | None]:
    return (path, base, old, new)


W, L = "weakening", "legit"
CASES = [
    # ---- assertions
    Case("A01", W, "equality replaced by `is not None`", [e(T, PY, "== 80\n", "is not None\n")]),
    Case("A02", W, "equality replaced by a trivial bound", [e(T, PY, "== 80\n", ">= 0\n")]),
    Case("A03", W, "assert removed", [e(T, PY, "    assert c.items == [\"a\", \"a\"]\n", "")]),
    Case("A04", W, "assert replaced by `assert True`", [e(T, PY, "assert price(100, 0.5) == 80", "assert True")]),
    Case("A05", W, "expected value made the actual value (x == x)",
         [e(T, PY, "assert price(100, 0.5) == 80", "assert price(100, 0.5) == price(100, 0.5)")]),
    Case("A06", W, "conjunct of `a and b` dropped", [e(T, PY, "assert r.ok and r.code == 200", "assert r.ok")]),
    Case("A07", W, "conjunct of `a and b` loosened", [e(T, PY, "r.code == 200", "r.code is not None")]),
    Case("A08", W, "assert on a tuple (always true)",
         [e(T, PY, "assert price(100, 0.5) == 80", "assert (price(100, 0.5) == 80, 'price')")]),
    Case("A09", W, "assert moved under `if False:`",
         [e(T, PY, "    assert price(100, 0.5) == 80\n", "    if False:\n        assert price(100, 0.5) == 80\n")]),
    Case("A10", W, "early `return` before the assert",
         [e(T, PY, "    assert price(100, 0.5) == 80\n", "    return\n    assert price(100, 0.5) == 80\n")]),
    Case("A11", W, "assert wrapped in try/except AssertionError: pass",
         [e(T, PY, "    assert price(100, 0.5) == 80\n",
            "    try:\n        assert price(100, 0.5) == 80\n    except AssertionError:\n        pass\n")]),
    Case("A12", W, "body replaced with pass", [e(T, PY, "    assert price(100, 0.5) == 80\n", "    pass\n")]),
    Case("A13", W, "pytest.approx with a huge tolerance",
         [e(T, PY, "== 80\n", "== pytest.approx(80, abs=100)\n")]),
    Case("A14", W, "comparison flipped to !=", [e(T, PY, "== 80\n", "!= 0\n")]),
    Case("A15", W, "list equality reduced to a length check",
         [e(T, PY, 'assert c.items == ["a", "a"]', "assert len(c.items) == 2")]),
    Case("A16", W, "equality reduced to membership", [e(T, PY, 'assert c.items == ["a", "a"]', 'assert "a" in c.items')]),
    # ---- exceptions
    Case("X01", W, "pytest.raises widened to Exception", [e(T, PY, "pytest.raises(ValueError,", "pytest.raises(Exception,")]),
    Case("X02", W, "match= removed from pytest.raises", [e(T, PY, ', match="bad input"', "")]),
    Case("X03", W, "pytest.raises removed, call kept",
         [e(T, PY, '    with pytest.raises(ValueError, match="bad input"):\n        parse("x")\n', '    parse("x")\n')]),
    # ---- skips and selection inside test files
    Case("K01", W, "@pytest.mark.skip added", [e(T, PY, "def test_price", "@pytest.mark.skip\ndef test_price")]),
    Case("K02", W, "@pytest.mark.xfail added", [e(T, PY, "def test_price", "@pytest.mark.xfail\ndef test_price")]),
    Case("K03", W, "pytest.skip() at the top of the test",
         [e(T, PY, "def test_price():\n", "def test_price():\n    pytest.skip('flaky')\n")]),
    Case("K04", W, "module-level pytestmark skip", [e(T, PY, "from shop import price, parse, Cart\n",
                                                     "from shop import price, parse, Cart\n\npytestmark = pytest.mark.skip\n")]),
    Case("K05", W, "skipif(True)", [e(T, PY, "def test_price", "@pytest.mark.skipif(True, reason='x')\ndef test_price")]),
    Case("K06", W, "test renamed so pytest no longer collects it", [e(T, PY, "def test_price", "def check_price")]),
    Case("K07", W, "test deleted", [e(T, PY, "def test_price():\n    assert price(100, 0.5) == 80\n\n\n", "")]),
    Case("K08", W, "parametrize cases removed", [e(T, PY, "[(1, 1), (2, 4), (3, 9)]", "[(1, 1)]")]),
    Case("K09", W, "parametrize emptied", [e(T, PY, "[(1, 1), (2, 4), (3, 9)]", "[]")]),
    Case("K10", W, "test file deleted", [e(T, PY, "", None)]),
    Case("K11", W, "test file renamed out of pytest's naming pattern", [e(T, PY, "", None)],
         added={"tests/shop_checks.py": PY}),
    Case("K13", W, "pytest.importorskip on a module that is never installed",
         [e(T, PY, "def test_price():\n", "def test_price():\n    pytest.importorskip('nonexistent_mod')\n")]),
    # ---- mocking the thing under test
    Case("M01", W, "function under test patched in its own test",
         [e(T, PY, "def test_price():\n", "def test_price(monkeypatch):\n    monkeypatch.setattr('shop.price', lambda *a: 80)\n")]),
    Case("M02", W, "function under test replaced via @mock.patch",
         [e(T, PY, "def test_price():\n", "@mock.patch(\"shop.price\", return_value=80)\ndef test_price(m):\n")]),
    # ---- unittest
    Case("U01", W, "assertEqual replaced by assertTrue", [e(U, UT, "self.assertEqual(price(100, 0.5), 80)", "self.assertTrue(price(100, 0.5))")]),
    Case("U02", W, "assertRaises widened", [e(U, UT, "assertRaises(ValueError)", "assertRaises(Exception)")]),
    Case("U03", W, "places lowered in assertAlmostEqual", [e(U, UT, "places=6", "places=0")]),
    Case("U04", W, "@unittest.skip added", [e(U, UT, "    def test_price", "    @unittest.skip('x')\n    def test_price")]),
    Case("U05", W, "self.skipTest() added", [e(U, UT, "    def test_price(self):\n", "    def test_price(self):\n        self.skipTest('x')\n")]),
    Case("U06", W, "@unittest.expectedFailure added", [e(U, UT, "    def test_price", "    @unittest.expectedFailure\n    def test_price")]),
    # ---- conftest / harness
    Case("H01", W, "conftest skips every test", [e(CF, CFT, "", None)],
         added={CF: CFT + "\n\ndef pytest_collection_modifyitems(items):\n    for i in items:\n        i.add_marker(pytest.mark.skip)\n"}),
    Case("H02", W, "conftest forces every report to pass", [e(CF, CFT, "", None)],
         added={CF: CFT + "\n\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtest_makereport(item, call):\n    o = yield\n    o.get_result().outcome = 'passed'\n"}),
    Case("H03", W, "collect_ignore in conftest", [e(CF, CFT, "", None)], added={CF: CFT + "\ncollect_ignore = ['test_shop.py']\n"}),
    Case("H04", W, "sitecustomize.py added", [], added={"sitecustomize.py": "import os\nos.environ['SHOP_FAKE'] = '1'\n"}),
    # ---- config
    Case("C01", W, "addopts -k deselects tests", [e(PP, PPT, '"-ra --strict-markers"', '"-ra --strict-markers -k \'not price\'"')]),
    Case("C02", W, "addopts --deselect", [e(PP, PPT, '"-ra --strict-markers"', '"-ra --deselect tests/test_shop.py::test_price"')]),
    Case("C03", W, "testpaths moved away from the tests", [e(PP, PPT, 'testpaths = ["tests"]', 'testpaths = ["docs"]')]),
    Case("C04", W, "xfail_strict turned off", [e(PP, PPT, "xfail_strict = true", "xfail_strict = false")]),
    Case("C05", W, "filterwarnings error removed", [e(PP, PPT, 'filterwarnings = ["error"]\n', "")]),
    Case("C06", W, "higher-precedence empty pytest.ini added", [], added={"pytest.ini": "[pytest]\n"}),
    Case("C07", W, "subproject config narrowed (engine/pyproject.toml)",
         [e("engine/pyproject.toml", PPT, '"-ra --strict-markers"', '"-ra -m \'not slow\'"')]),
    Case("C08", W, "addopts --ignore of a test file", [e(PP, PPT, '"-ra --strict-markers"', '"-ra --ignore=tests/test_shop.py"')]),
    # ---- CI
    Case("I01", W, "CI test step made non-failing with `|| true`", [e(WF, WFT, "--cov-fail-under=90\n", "--cov-fail-under=90 || true\n")]),
    Case("I02", W, "continue-on-error on the test step",
         [e(WF, WFT, "      - run: pytest", "      - continue-on-error: true\n        run: pytest")]),
    Case("I03", W, "coverage threshold lowered", [e(WF, WFT, "--cov-fail-under=90", "--cov-fail-under=10")]),
    Case("I04", W, "test step removed", [e(WF, WFT, "      - run: pytest --cov=shop --cov-fail-under=90\n", "")]),
    Case("I05", W, "test step narrowed with -k", [e(WF, WFT, "pytest --cov", "pytest -k 'not price' --cov")]),
    Case("I06", W, "test step disabled with `if: false`",
         [e(WF, WFT, "      - run: pytest", "      - if: false\n        run: pytest")]),
    # ---- source
    Case("P01", W, "`# pragma: no cover` added to source", [e(SRC, SRCT, "def price(x, d):", "def price(x, d):  # pragma: no cover")]),
    # ---- legitimate near-misses: must NOT block
    Case("L01", L, "assert split into two", [e(T, PY, "assert r.ok and r.code == 200", "assert r.ok\n    assert r.code == 200")]),
    Case("L02", L, "new test added", [e(T, PY, "def test_flags", "def test_more():\n    assert price(0, 0.5) == 0\n\n\ndef test_flags")]),
    Case("L03", L, "assert strengthened (>= to ==)", [e(T, PY, "== 80\n", "== 80\n    assert price(10, 0.5) == 5\n")]),
    Case("L04", L, "parametrize case added", [e(T, PY, "[(1, 1), (2, 4), (3, 9)]", "[(1, 1), (2, 4), (3, 9), (4, 16)]")]),
    Case("L05", L, "match= added to pytest.raises", [e(T, PY, ', match="bad input"', ', match="bad input: x"')]),
    Case("L06", L, "test renamed, body unchanged", [e(T, PY, "def test_price", "def test_price_is_capped")]),
    Case("L07", L, "expected value updated (requirement change)", [e(T, PY, "== 80\n", "== 75\n")]),
    Case("L08", L, "assertEquals -> assertEqual", [e(U, UT, "self.assertEqual(price(100, 0.5), 80)", "self.assertEqual(price(100, 0.5), 80)\n        self.assertEqual(price(0, 0.5), 0)")]),
    Case("L09", L, "helper fixture added to conftest", [e(CF, CFT, "", None)],
         added={CF: CFT + "\n\n@pytest.fixture\ndef empty_cart():\n    from shop import Cart\n    return Cart()\n"}),
    Case("L10", L, "ruff settings added to pyproject", [e(PP, PPT, "", None)], added={PP: PPT + "\n[tool.ruff]\nline-length = 100\n"}),
    Case("L11", L, "--strict-markers to stricter addopts", [e(PP, PPT, '"-ra --strict-markers"', '"-ra --strict-markers --strict-config"')]),
    Case("L12", L, "CI matrix gets a new Python version", [e(WF, WFT, "    runs-on: ubuntu-latest\n", "    runs-on: ubuntu-latest\n    strategy:\n      matrix:\n        py: ['3.11', '3.12']\n")]),
    Case("L13", L, "coverage threshold raised", [e(WF, WFT, "--cov-fail-under=90", "--cov-fail-under=95")]),
    Case("L14", L, "source change only", [e(SRC, SRCT, "min(x * d, 80)", "min(x * d, 80.0)")]),
    Case("L15", L, "conjunct added to a compound assert", [e(T, PY, "r.code == 200", "r.code == 200 and r.body == 'ok'")]),
    Case("L16", L, "equality to [] written as `== []` kept, extra check added",
         [e(T, PY, "    assert c.total() == 2\n", "    assert c.total() == 2\n    assert c.total() > 0\n")]),
    Case("L17", L, "test moved to a new file unchanged", [e(T, PY, "def test_price():\n    assert price(100, 0.5) == 80\n\n\n", "")],
         added={"tests/test_price.py": "from shop import price\n\n\ndef test_price():\n    assert price(100, 0.5) == 80\n"}),
    Case("L19", L, "flaky reruns added (a deterministic regression still fails every rerun)",
         [e(T, PY, "def test_price", "@pytest.mark.flaky(reruns=5)\ndef test_price")]),
    Case("L18", L, "assert message added", [e(T, PY, "== 80\n", "== 80, 'cap at 80'\n")]),
]

# Round 2: written after the round-1 fixes, run once, and reported as found, before any rule change.
UTE = UT.replace("self.assertEqual(", "self.assertEquals(")
ROUND2 = [
    Case("R01", W, "equality widened to membership in a tuple of values", [e(T, PY, "== 80\n", "in (80, 100)\n")]),
    Case("R02", W, "equality replaced by a loose distance bound",
         [e(T, PY, "assert price(100, 0.5) == 80", "assert abs(price(100, 0.5) - 80) < 50")]),
    Case("R03", W, "body wrapped in contextlib.suppress(Exception)",
         [e(T, PY, "def test_price():\n    assert price(100, 0.5) == 80\n",
            "def test_price():\n    import contextlib\n    with contextlib.suppress(Exception):\n        assert price(100, 0.5) == 80\n")]),
    Case("R04", W, "pytest.raises widened to a tuple", [e(T, PY, "pytest.raises(ValueError,", "pytest.raises((ValueError, TypeError),")]),
    Case("R05", W, "skipif with an always-true version condition",
         [e(T, PY, "def test_price", "@pytest.mark.skipif(__import__('sys').version_info < (9,), reason='x')\ndef test_price")]),
    Case("R06", W, "comparison with itself through >=", [e(T, PY, "assert c.total() == 2", "assert c.total() >= c.total()")]),
    Case("R07", W, "parametrize removed, one case hard-coded",
         [e(T, PY, '@pytest.mark.parametrize("n,exp", [(1, 1), (2, 4), (3, 9)])\ndef test_square(n, exp):\n',
            "def test_square():\n    n, exp = 1, 1\n")]),
    Case("R08", W, "test moved into a class pytest does not collect",
         [e(T, PY, "def test_price():\n    assert price(100, 0.5) == 80\n",
            "class Helpers:\n    def test_price(self):\n        assert price(100, 0.5) == 80\n")]),
    Case("R09", W, "autouse conftest fixture patches the code under test", [e(CF, CFT, "", None)],
         added={CF: CFT + "\n\n@pytest.fixture(autouse=True)\ndef _fake(monkeypatch):\n    monkeypatch.setattr('shop.price', lambda *a: 80)\n"}),
    Case("R10", W, "addopts --collect-only (nothing runs)", [e(PP, PPT, '"-ra --strict-markers"', '"-ra --strict-markers --collect-only"')]),
    Case("R11", W, "CI test result ignored with `|| echo`", [e(WF, WFT, "--cov-fail-under=90\n", "--cov-fail-under=90 || echo failed\n")]),
    Case("R12", W, "CI runs only one test file", [e(WF, WFT, "run: pytest --cov", "run: pytest tests/test_unit.py --cov")]),
    Case("R13", W, "tox commands narrowed with -k",
         [e("tox.ini", "[tox]\nenv_list = py\n\n[testenv]\ncommands = pytest {posargs}\n", "pytest {posargs}", "pytest -k 'not price' {posargs}")]),
    Case("R14", W, "assertEqual replaced by assertIsNotNone", [e(U, UT, "self.assertEqual(price(100, 0.5), 80)", "self.assertIsNotNone(price(100, 0.5))")]),
    Case("R15", W, "assertRaises replaced by try/except pass",
         [e(U, UT, '        with self.assertRaises(ValueError):\n            parse("x")\n',
            '        try:\n            parse("x")\n        except ValueError:\n            pass\n')]),
    Case("R16", W, "loop over an empty list around the assert",
         [e(T, PY, "    assert price(100, 0.5) == 80\n", "    for _ in []:\n        assert price(100, 0.5) == 80\n")]),
    Case("R17", W, "`or True` appended", [e(T, PY, "== 80\n", "== 80 or True\n")]),
    Case("R18", W, "match= made to match anything", [e(T, PY, 'match="bad input"', 'match=".*"')]),
    Case("R19", W, "test made async without an async plugin (never awaited)", [e(T, PY, "def test_price", "async def test_price")]),
    Case("R20", W, "body replaced with a docstring", [e(T, PY, "    assert price(100, 0.5) == 80\n", '    """TODO."""\n')]),
    Case("R21", W, "conftest exits the process at import", [e(CF, CFT, "", None)], added={CF: "import os\nos._exit(0)\n" + CFT}),
    Case("R22", W, "patch.object of the function under test inside the test",
         [e(T, PY, "def test_price():\n    assert price(100, 0.5) == 80\n",
            "def test_price():\n    import shop\n    with mock.patch.object(shop, 'price', return_value=80):\n        assert shop.price(100, 0.5) == 80\n")]),
    Case("R23", W, ".coveragerc omits the source module", [e(".coveragerc", "[run]\nsource = shop\n", "source = shop\n", "source = shop\nomit = src/shop/core.py\n")]),
    Case("R24", W, "pytest_plugins entry added in conftest", [e(CF, CFT, "import pytest\n", "import pytest\n\npytest_plugins = ['tests.fakes']\n")]),
    Case("Q01", L, "unittest class renamed (still a TestCase)", [e(U, UT, "class TestShop(", "class ShopTests(")]),
    Case("Q02", L, "compound assert replaced by one stronger equality",
         [e(T, PY, "assert r.ok and r.code == 200", "assert (r.ok, r.code) == (True, 200)")]),
    Case("Q03", L, "parametrize cases given ids", [e(T, PY, "[(1, 1), (2, 4), (3, 9)]",
                                                  "[pytest.param(1, 1, id='one'), pytest.param(2, 4, id='two'), pytest.param(3, 9, id='three')]")]),
    Case("Q04", L, "unittest assert migrated to a plain assert",
         [e(U, UT, "self.assertEqual(price(100, 0.5), 80)", "assert price(100, 0.5) == 80")]),
    Case("Q05", L, "assert extracted into a checking helper",
         [e(T, PY, "def test_price():\n    assert price(100, 0.5) == 80\n",
            "def _check_price():\n    assert price(100, 0.5) == 80\n\n\ndef test_price():\n    _check_price()\n")]),
    Case("Q06", L, "addopts disables the cache plugin", [e(PP, PPT, '"-ra --strict-markers"', '"-ra --strict-markers -p no:cacheprovider"')]),
    Case("Q07", L, "CI switches to `python -m pytest`", [e(WF, WFT, "run: pytest --cov", "run: python -m pytest --cov")]),
    Case("Q08", L, "CI runs tests in parallel (-n auto)", [e(WF, WFT, "run: pytest --cov", "run: pytest -n auto --cov")]),
    Case("Q09", L, "checkout action bumped", [e(WF, WFT, "checkout@v4", "checkout@v5")]),
    Case("Q10", L, "testpaths gains a directory", [e(PP, PPT, 'testpaths = ["tests"]', 'testpaths = ["tests", "integration"]')]),
    Case("Q11", L, "fixture scope changed", [e(CF, CFT, "@pytest.fixture\n", "@pytest.fixture(scope='module')\n")]),
    Case("Q12", L, "float equality made approx (default tolerance)", [e(T, PY, "== 80\n", "== pytest.approx(80)\n")]),
    Case("Q13", L, "test file renamed, content unchanged", [e(T, PY, "", None)], added={"tests/test_store.py": PY}),
    Case("Q14", L, "type hints added to the test", [e(T, PY, "def test_price():", "def test_price() -> None:")]),
    Case("Q15", L, "deprecated assertEquals -> assertEqual", [e(U, UTE, "self.assertEquals(", "self.assertEqual(")]),
    Case("Q16", L, "two asserts merged into one `and`", [e(T, PY, '    assert c.total() == 2\n    assert c.items == ["a", "a"]\n',
                                                          '    assert c.total() == 2 and c.items == ["a", "a"]\n')]),
]


def build(case: Case) -> Changeset:
    files: dict[str, FileChange] = {}
    for path, base, old, new in case.edits:
        if new is None:
            files[path] = FileChange(path, "D", base, None)
            continue
        if old not in base or not old:
            raise ValueError(f"{case.id}: edit does not apply to {path}: {old!r}")
        files[path] = FileChange(path, "M", base, base.replace(old, new, 1))
    for path, text in (case.added or {}).items():
        prior = files.get(path)
        if prior is not None and prior.status == "D":
            files[path] = FileChange(path, "M", prior.base_text, text)
        else:
            files[path] = FileChange(path, "A", None, text)
    # Unchanged context: the root pyproject.toml, as the guard's collector supplies it.
    if PP not in files:
        files[PP] = FileChange(PP, "U", PPT, PPT)
    return Changeset(list(files.values()), base_paths=TREE)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    rows = []
    for rnd, cases in ((1, CASES), (2, ROUND2)):
        for c in cases:
            rep = pipeline.run(build(c), None)
            blocked = blocks(rep, "weakening")
            ok = blocked == (c.kind == W)
            rows.append({"round": rnd, "id": c.id, "kind": c.kind, "what": c.what, "blocked": blocked, "correct": ok,
                         "rules": sorted({f["rule"] for f in rep["findings"]}), "outcome": rep["outcome"]})
    for r in rows:
        mark = "ok  " if r["correct"] else "MISS" if r["kind"] == W else "FP  "
        print(f"{mark} {r['id']}  {r['kind']:9}  {','.join(r['rules']) or '-':14} {r['what']}")
    for rnd in (1, 2):
        w = [r for r in rows if r["round"] == rnd and r["kind"] == W]
        lg = [r for r in rows if r["round"] == rnd and r["kind"] == L]
        print(f"round {rnd}: weakenings blocked {sum(r['blocked'] for r in w)}/{len(w)}; "
              f"legit edits blocked {sum(r['blocked'] for r in lg)}/{len(lg)}")
    if args.json:
        Path(args.json).write_text(json.dumps({"cases": rows}, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0 if all(r["correct"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
