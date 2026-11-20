"""Build the checkout demo git repository with deterministic commit SHAs.

Branches:
  main                 baseline (cap 20%, test asserts == 80.00)
  candidate-weakened   cap removed, test weakened to >= 0 (primer section 2)
  candidate-legit      cap raised to 25% under approved contract v2, test == 75.00

Fixed author, committer and dates make the SHAs reproducible on any machine.
Usage: python demo/build_demo.py [target_dir]   (default demo/.build/checkout)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCEN = HERE / "scenario"
DEFAULT = HERE / ".build" / "checkout"

ENV = {
    "GIT_AUTHOR_NAME": "PROVECODE demo",
    "GIT_AUTHOR_EMAIL": "demo@provecode.invalid",
    "GIT_COMMITTER_NAME": "PROVECODE demo",
    "GIT_COMMITTER_EMAIL": "demo@provecode.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
}

PYPROJECT = """[project]
name = "checkout"
version = "0.1.0"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
"""


def git(repo: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ, **ENV}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    out = subprocess.run(["git", "-C", str(repo), "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args],
                         env=env, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr}")
    return out.stdout.strip()


def write(repo: Path, rel: str, src: Path | str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    data = src.read_bytes() if isinstance(src, Path) else src.encode()
    p.write_bytes(data.replace(b"\r\n", b"\n"))


def commit(repo: Path, msg: str, date: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg, date=date)
    return git(repo, "rev-parse", "HEAD")


def _force_remove(func, path, _exc):
    # git marks object files read-only; on Windows rmtree needs them writable.
    os.chmod(path, 0o700)
    func(path)


def build(target: Path) -> dict:
    if target.exists():
        shutil.rmtree(target, onerror=_force_remove)
    target.mkdir(parents=True)
    git(target, "init", "-q", "-b", "main")
    write(target, "pyproject.toml", PYPROJECT)
    write(target, "checkout/__init__.py", "")
    write(target, "checkout/pricing.py", SCEN / "pricing_base.py")
    write(target, "tests/test_pricing.py", SCEN / "test_pricing_base.py")
    base = commit(target, "checkout: discount pricing with 20% cap", "2026-11-20T09:00:00+00:00")

    git(target, "checkout", "-q", "-b", "candidate-weakened")
    write(target, "checkout/pricing.py", SCEN / "pricing_weakened.py")
    write(target, "tests/test_pricing.py", SCEN / "test_pricing_weakened.py")
    weakened = commit(target, "Fix checkout discount calculation", "2026-11-20T10:00:00+00:00")

    git(target, "checkout", "-q", "main")
    git(target, "checkout", "-q", "-b", "candidate-legit")
    write(target, "checkout/pricing.py", SCEN / "pricing_legit.py")
    write(target, "tests/test_pricing.py", SCEN / "test_pricing_legit.py")
    legit = commit(target, "Raise discount cap to 25% (approved contract v2)", "2026-11-20T11:00:00+00:00")
    git(target, "checkout", "-q", "main")
    return {"base": base, "weakened": weakened, "legit": legit}


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    shas = build(target)
    for k, v in shas.items():
        print(f"{k} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
