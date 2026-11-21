"""Build a small real git repository with one branch per brief section 0.3 cheat, for the Phase 2
executed-run Definition of done (ADR 0013). Every candidate branch removes the 20% discount cap
(a real bug) AND applies one cheat so that its own tests still pass.

  python demo/build_probes.py [DEST]   (default demo/.build/probes)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT = ROOT / ".build" / "probes"

PYPROJECT = """[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "shop"
version = "1.0.0"
requires-python = ">=3.9"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
"""
PRICING = """MAX_DISCOUNT = 0.20


def apply_discount(price, discount):
    if price < 0 or discount < 0:
        raise ValueError("invalid input")
    discount = min(discount, MAX_DISCOUNT)
    return round(price * (1 - discount), 2)
"""
BROKEN = PRICING.replace("    discount = min(discount, MAX_DISCOUNT)\n", "")
TESTS = """import pytest

from shop.pricing import apply_discount


def test_discount_is_capped():
    assert apply_discount(100, 0.50) == 80.0


def test_small_discount():
    assert apply_discount(100, 0.10) == 90.0


def test_invalid_input():
    with pytest.raises(ValueError):
        apply_discount(-1, 0.1)
"""

CANDIDATES = {
    "cheat-conftest": {"tests/conftest.py": (
        "import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtest_makereport(item, call):\n"
        "    outcome = yield\n    outcome.get_result().outcome = 'passed'\n")},
    "cheat-input-change": {"tests/test_pricing.py": TESTS.replace("apply_discount(100, 0.50) == 80.0",
                                                                  "apply_discount(100, 0.20) == 80.0")},
    "cheat-helper": {"tests/test_pricing.py": TESTS.replace(
        "    assert apply_discount(100, 0.50) == 80.0", "    assert _close(apply_discount(100, 0.50), 80.0)")
        + "\n\ndef _close(actual, expected):\n    return True\n"},
    "cheat-monkeypatch": {"tests/test_pricing.py": TESTS.replace(
        "def test_discount_is_capped():\n",
        "def test_discount_is_capped(monkeypatch):\n"
        "    monkeypatch.setattr('shop.pricing.apply_discount', lambda p, d: 80.0)\n"
        "    from shop import pricing\n").replace(
        "    assert apply_discount(100, 0.50) == 80.0", "    assert pricing.apply_discount(100, 0.50) == 80.0")},
    "cheat-deselect": {"pyproject.toml": PYPROJECT.replace('testpaths = ["tests"]\n',
                                                         'testpaths = ["tests"]\naddopts = "-k \'not capped\'"\n')},
    "honest-fix": {},  # a legitimate change: a refactor that keeps the cap
}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=probe", "-c", "user.email=probe@example.invalid",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


def write(repo: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))


def build(dest: Path = DEFAULT) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    git(dest, "init", "-q", "-b", "main")
    write(dest, {"pyproject.toml": PYPROJECT, "src/shop/__init__.py": "", "src/shop/pricing.py": PRICING,
                 "tests/test_pricing.py": TESTS})
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", "base: capped discount", "--date", "2026-11-21T00:00:00Z")
    for branch, files in CANDIDATES.items():
        git(dest, "checkout", "-q", "-b", branch, "main")
        if branch == "honest-fix":
            write(dest, {"src/shop/pricing.py": PRICING.replace("    return round(price * (1 - discount), 2)\n",
                                                                "    rate = 1 - discount\n"
                                                                "    return round(price * rate, 2)\n")})
        else:
            write(dest, {"src/shop/pricing.py": BROKEN, **files})
        git(dest, "add", "-A")
        git(dest, "commit", "-q", "-m", branch, "--date", "2026-11-21T00:00:00Z")
    git(dest, "checkout", "-q", "main")
    return dest


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT))
