"""Real Docker runs of the recorded path, including the code-review attack.
Opt in with PROVECODE_DOCKER=1 (needs a running Docker daemon and the run image)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import types
from pathlib import Path

import pytest

from provecode import contract as C
from provecode import exec as ex

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.environ.get("PROVECODE_DOCKER") != "1", reason="set PROVECODE_DOCKER=1")
IMAGE = "provecode-run:local"


@pytest.fixture(scope="module")
def image():
    digest = (ROOT / "docker" / "base.digest").read_text(encoding="utf-8").strip()
    subprocess.run(["docker", "build", "-q", "--build-arg", f"BASE={digest}", "-t", IMAGE, str(ROOT / "docker")],
                   check=True, capture_output=True, timeout=900)
    return IMAGE


def _repo(tmp: Path) -> tuple[Path, dict]:
    spec = importlib.util.spec_from_file_location("build_demo", ROOT / "demo" / "build_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    r = tmp / "checkout"
    return r, mod.build(r)


def _commit(repo: Path, files: dict[str, bytes], msg: str) -> str:
    for rel, data in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(data)
    g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*g, "checkout", "-q", "-B", "attack", "candidate-weakened"], check=True)
    for rel, data in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(data)
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-q", "-m", msg], check=True)
    return subprocess.run([*g, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def _run(repo: Path, head: str, image: str):
    doc = C.read_any(ROOT / "demo" / "contracts" / "checkout.v1.yaml")
    args = types.SimpleNamespace(repo=str(repo), image=image, checks_dir=str(ROOT / "demo" / "contract_checks" / "v1"),
                                 timeout=120)
    evidence, gaps, _ = ex.run_checks(args, doc, head)
    return {e.check_id: e.result for e in evidence}


def test_weakened_candidate_fails_the_cap_check(tmp_path, image):
    repo, shas = _repo(tmp_path)
    res = _run(repo, shas["weakened"], image)
    assert res["chk-cap"] == "fail"
    assert res["repo-tests"] == "pass"


@pytest.mark.parametrize("files", [
    {"sitecustomize.py": b"import os\nos._exit(0)\n"},
    {"checkout/__init__.py": b"import os\nos._exit(0)\n"},
    {"pytest.py": b"import sys\nsys.exit(0)\n"},
], ids=["sitecustomize", "exit-on-import", "shadow-pytest"])
def test_candidate_cannot_fake_a_passing_contract_check(tmp_path, image, files):
    repo, _ = _repo(tmp_path)
    head = _commit(repo, files, "attack")
    res = _run(repo, head, image)
    assert res["chk-cap"] != "pass", res
