"""Brief section 5: the analyzer must never crash on any Python source. A crash is a test failure;
unparseable input must be an explicit gap.

- Corpus: every .py file, conftest, pytest/coverage config and GitHub workflow in the pinned benchmark
  repositories (benchmark/.cache/src, created by `python -m benchmark.regenerate`). Skipped, and
  saying so, when that cache is absent.
- Fuzz: a seeded mutation generator over real files (and over built-in samples when the cache is
  absent), so the test also runs on a fresh checkout.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from provecode import analyzer, harness, pipeline, yamlsubset
from provecode.changeset import CONFIG, CONFTEST, CI, Changeset, FileChange, classify
from provecode.limits import Limits

SRC = Path(__file__).resolve().parents[2] / "benchmark" / ".cache" / "src"
SAMPLES = {
    "tests/test_x.py": "import pytest\n\n\nclass TestA:\n    @pytest.mark.parametrize('x', [1, 2])\n"
                       "    def test_a(self, x):\n        for y in [x, x + 1]:\n            assert f(y) == 2\n"
                       "        with pytest.raises(ValueError, match='bad'):\n            g(x)\n",
    "tests/conftest.py": "import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtest_makereport(item, call):\n"
                         "    outcome = yield\n\ncollect_ignore = ['a.py']\n",
    "pyproject.toml": '[tool.pytest.ini_options]\naddopts = "-ra -k \'not x\'"\ntestpaths = ["tests"]\n'
                      '[tool.coverage.report]\nfail_under = 90\n',
    ".github/workflows/ci.yml": "on: [push]\njobs:\n  t:\n    steps:\n      - run: |\n          pytest -q\n"
                                "        continue-on-error: false\n",
}


def _corpus(pred) -> list[Path]:
    if not SRC.exists():
        return []
    return sorted(p for p in SRC.rglob("*") if p.is_file() and ".git" not in p.parts and pred(p))


def _text(p: Path) -> str | None:
    try:
        return p.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None


def _rel(p: Path) -> str:
    return "/".join(p.relative_to(SRC).parts[1:])


@pytest.mark.skipif(not SRC.exists(), reason="benchmark/.cache/src absent: run python -m benchmark.regenerate first")
def test_every_python_file_in_the_pinned_repositories_is_analyzed_without_a_crash():
    files = _corpus(lambda p: p.suffix == ".py")
    assert len(files) > 1000  # 27 repositories
    gaps = 0
    for p in files:
        src = _text(p)
        if src is None:
            continue
        rel = _rel(p)
        for b, h in ((src, src), (None, src), (src, None)):
            res = analyzer.analyze_file(rel, b, h, Limits.cli())
            gaps += bool(res.gaps)
            assert all(f.rule for f in res.findings)
    assert gaps < len(files)  # most real files parse; the rest are explicit gaps, never exceptions


@pytest.mark.skipif(not SRC.exists(), reason="benchmark/.cache/src absent: run python -m benchmark.regenerate first")
def test_every_harness_config_and_workflow_file_is_analyzed_without_a_crash():
    files = _corpus(lambda p: classify(_rel(p)) in (CONFTEST, CONFIG, CI))
    assert len(files) > 50
    for p in files:
        src = _text(p)
        if src is None:
            continue
        rel = _rel(p)
        cs = Changeset([FileChange(rel, "M", src, src + "\n# provecode corpus\n")])
        harness.analyze(cs, Limits.cli())
        pipeline.run(cs, None)


MUTATIONS = ("truncate", "delete_line", "duplicate_line", "insert_bracket", "dedent", "indent", "junk", "swap")


def _mutate(text: str, rng: random.Random) -> str:
    lines = text.splitlines(keepends=True) or [""]
    kind = rng.choice(MUTATIONS)
    i = rng.randrange(len(lines))
    if kind == "truncate":
        return text[: rng.randrange(len(text) + 1)]
    if kind == "delete_line":
        del lines[i]
    elif kind == "duplicate_line":
        lines.insert(i, lines[i])
    elif kind == "insert_bracket":
        pos = rng.randrange(len(lines[i]) + 1)
        lines[i] = lines[i][:pos] + rng.choice("([{)]}:'\"") + lines[i][pos:]
    elif kind == "dedent":
        lines[i] = lines[i].lstrip()
    elif kind == "indent":
        lines[i] = "  " * rng.randrange(1, 40) + lines[i]
    elif kind == "junk":
        lines.insert(i, "".join(rng.choice("\t\x0c\\#@$`~ \u00e9\u2028") for _ in range(rng.randrange(1, 20))) + "\n")
    else:
        j = rng.randrange(len(lines))
        lines[i], lines[j] = lines[j], lines[i]
    return "".join(lines)


def _seeds() -> list[tuple[str, str]]:
    real = [(_rel(p), t) for p in _corpus(lambda p: p.suffix in (".py", ".toml", ".cfg", ".ini", ".yml", ".yaml"))[:400]
            if (t := _text(p)) is not None and len(t) < 60_000]
    return real or list(SAMPLES.items())


def test_mutated_real_files_never_crash_the_engine():
    rng = random.Random("provecode-fuzz-20261121")
    seeds = _seeds()
    for n in range(1500):
        path, text = seeds[n % len(seeds)]
        bad = text
        for _ in range(rng.randrange(1, 4)):
            bad = _mutate(bad, rng)
        cs = Changeset([FileChange(path, "M", text, bad)])
        rep = pipeline.run(cs, None, limits=Limits.live())
        assert rep["outcome"] in ("FAILED", "INCONCLUSIVE")
        if path.endswith((".yml", ".yaml")):
            try:
                yamlsubset.loads(bad)
            except yamlsubset.YamlError:
                pass


def test_warnings_about_the_analyzed_code_do_not_escape_or_break_parsing():
    import warnings

    src = 'def test_a():\n    assert f("' + chr(92) + ' ") == 1\n'  # an invalid escape sequence in the analyzed file
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        res = analyzer.analyze_file("tests/test_a.py", src, src.replace("== 1", ">= 0"), Limits.cli())
    assert not res.gaps and [f.rule for f in res.findings] == ["S2"]
