"""The GitHub Action entry point (action/run.sh), run against throwaway demo repos."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run([*GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> tuple[Path, str]:
    spec = importlib.util.spec_from_file_location("build_demo", ROOT / "demo" / "build_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    r = tmp_path / "repo"
    mod.build(r)
    (r / "contracts").mkdir()
    for v in ("v1", "v2"):
        shutil.copy(ROOT / "demo" / "contracts" / f"checkout.{v}.yaml", r / "contracts" / f"checkout.{v}.yaml")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "add approved contracts")
    base = _git(r, "rev-parse", "HEAD")
    return r, base


def candidate(repo: Path, base: str, scenario: str, extra=None) -> str:
    _git(repo, "checkout", "-q", "-B", f"pr-{scenario}", base)
    shutil.copy(ROOT / "demo" / "scenario" / f"test_pricing_{scenario}.py", repo / "tests" / "test_pricing.py")
    if extra:
        extra(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", scenario)
    return _git(repo, "rev-parse", "HEAD")


def run_action(repo: Path, base: str, head: str, tmp: Path, **env) -> tuple[int, dict, str]:
    out = tmp / "gh"
    out.mkdir(exist_ok=True)
    summary, outputs = out / "summary.md", out / "outputs.txt"
    scripts = Path(sys.executable).parent
    e = {**os.environ, "PATH": f"{scripts}{os.pathsep}{os.environ['PATH']}", "RUNNER_TEMP": str(out),
         "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_OUTPUT": str(outputs),
         "PC_CONTRACT": "contracts/checkout.v1.yaml", "PC_BASE": base, "PC_HEAD": head, **env}
    p = subprocess.run([BASH, str(ROOT / "action" / "run.sh")], cwd=repo, env=e, capture_output=True, text=True)
    report = json.loads((out / "provecode" / "report.json").read_text(encoding="utf-8")) \
        if (out / "provecode" / "report.json").exists() else {}
    return p.returncode, report, summary.read_text(encoding="utf-8") if summary.exists() else ""


def test_weakened_pr_fails_and_writes_summary(repo, tmp_path):
    r, base = repo
    head = candidate(r, base, "weakened")
    code, rep, summary = run_action(r, base, head, tmp_path)
    assert code == 1 and rep["outcome"] == "FAILED"
    assert "S2" in summary and "FAILED" in summary


def test_legit_pr_is_inconclusive_and_passes_unless_strict(repo, tmp_path):
    r, base = repo
    head = candidate(r, base, "legit")
    env = {"PC_CONTRACT": "contracts/checkout.v2.yaml", "PC_CONTRACT_BASE": "contracts/checkout.v1.yaml"}
    code, rep, _ = run_action(r, base, head, tmp_path, **env)
    assert code == 0 and rep["outcome"] == "INCONCLUSIVE"
    code, _, _ = run_action(r, base, head, tmp_path, PC_FAIL_ON_INCONCLUSIVE="true", **env)
    assert code == 1


def test_pr_that_edits_its_contract_is_judged_by_the_base_contract_and_fails(repo, tmp_path):
    r, base = repo

    def loosen(rr: Path):  # the PR tries to drop C1 from the contract it is judged by
        p = rr / "contracts" / "checkout.v1.yaml"
        p.write_text(p.read_text(encoding="utf-8").replace("mandatory: true", "mandatory: false"), encoding="utf-8")

    head = candidate(r, base, "weakened", loosen)
    code, rep, _ = run_action(r, base, head, tmp_path)
    assert code == 1
    rules = {f["rule"] for f in rep["findings"]}
    assert {"CONTRACT_TOUCHED", "S2"} <= rules
    assert rep["contract"]["conditions"][0]["mandatory"] is True  # the base version was used


def test_without_a_contract_a_weakened_pr_fails(repo, tmp_path):
    r, base = repo
    head = candidate(r, base, "weakened")
    code, rep, summary = run_action(r, base, head, tmp_path, PC_CONTRACT="")
    assert code == 1 and rep["outcome"] == "INCONCLUSIVE"
    assert "S2" in summary


def test_without_a_contract_a_legit_pr_passes(repo, tmp_path):
    r, base = repo
    head = candidate(r, base, "legit")
    code, rep, _ = run_action(r, base, head, tmp_path, PC_CONTRACT="")
    assert code == 0 and rep["outcome"] == "INCONCLUSIVE"


@pytest.mark.parametrize("block_on", ["failed", "sometimes"])
def test_without_a_contract_block_on_failed_or_a_bad_value_is_a_usage_error(repo, tmp_path, block_on):
    r, base = repo
    head = candidate(r, base, "weakened")
    code, _, _ = run_action(r, base, head, tmp_path, PC_CONTRACT="", PC_BLOCK_ON=block_on)
    assert code == 3


def test_with_a_contract_block_on_weakening_fails_on_an_ungraded_warning(repo, tmp_path):
    r, base = repo
    head = candidate(r, base, "legit")
    env = {"PC_CONTRACT": "contracts/checkout.v2.yaml", "PC_CONTRACT_BASE": "contracts/checkout.v1.yaml"}
    code, rep, _ = run_action(r, base, head, tmp_path, **env)
    weak = [f for f in rep["findings"] if f["severity"] in ("warning", "blocking") and f["rule"] not in ("V0", "R0")]
    code2, _, _ = run_action(r, base, head, tmp_path, PC_BLOCK_ON="weakening", **env)
    assert code == 0 and code2 == (1 if weak else 0)


def test_missing_contract_at_base_is_a_usage_error(repo, tmp_path):
    r, base = repo
    head = candidate(r, base, "weakened")
    code, _, _ = run_action(r, base, head, tmp_path, PC_CONTRACT="contracts/nope.yaml")
    assert code == 3
