"""provecode rerun / check --execute (ADR 0013). The end-to-end runs need Docker and the run image,
so they are skipped unless PROVECODE_DOCKER=1 (like test_exec_docker.py); the argument checks
always run."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from provecode import cli

ROOT = Path(__file__).resolve().parents[2]
IMAGE = os.environ.get("PROVECODE_RUN_IMAGE", "provecode-run:local")
docker = pytest.mark.skipif(os.environ.get("PROVECODE_DOCKER") != "1", reason="set PROVECODE_DOCKER=1")


def test_execute_needs_an_image(capsys):
    assert cli.main(["check", "--base", "HEAD", "--head", "HEAD", "--execute"]) == 3
    assert "--image" in capsys.readouterr().err


def test_check_without_contract_or_execute_is_a_usage_error(capsys):
    assert cli.main(["check", "--base", "HEAD", "--head", "HEAD"]) == 3
    assert "--contract is required" in capsys.readouterr().err


@pytest.fixture(scope="module")
def probes(tmp_path_factory) -> Path:
    spec = importlib.util.spec_from_file_location("build_probes", ROOT / "demo" / "build_probes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build(tmp_path_factory.mktemp("probes") / "repo")


@docker
@pytest.mark.parametrize("branch,expect_rules", [
    ("cheat-conftest", {"H1", "X2"}),
    ("cheat-input-change", {"X4"}),
    ("cheat-helper", {"X4"}),
    ("cheat-monkeypatch", {"X4"}),
    ("cheat-deselect", {"H4", "X2"}),
])
def test_each_cheat_fails_in_a_real_sealed_run(probes, tmp_path, branch, expect_rules):
    out = tmp_path / "r.json"
    code = cli.main(["check", "--repo", str(probes), "--base", "main", "--head", branch, "--execute",
                     "--image", IMAGE, "--json", str(out)])
    rep = json.loads(out.read_text(encoding="utf-8"))
    ev = {e["id"]: e["result"] for e in rep["evidence"]}
    assert code == 1 and rep["outcome"] == "FAILED"
    assert ev == {"E-A": "pass", "E-B": "fail", "E-C": "pass"}  # B fails, ordinary CI (C) passes
    assert expect_rules <= {f["rule"] for f in rep["findings"] if f["severity"] == "blocking"}


@docker
def test_an_honest_change_passes_the_base_tests(probes, tmp_path):
    out = tmp_path / "r.json"
    code = cli.main(["rerun", "--repo", str(probes), "--base", "main", "--head", "honest-fix", "--image", IMAGE,
                     "--json", str(out)])
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0 and rep["outcome"] == "VERIFIED"
    assert {e["id"]: e["result"] for e in rep["evidence"]} == {"E-A": "pass", "E-B": "pass", "E-C": "pass"}


@pytest.fixture(scope="module")
def demo(tmp_path_factory) -> Path:
    spec = importlib.util.spec_from_file_location("build_demo", ROOT / "demo" / "build_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    repo = tmp_path_factory.mktemp("demo") / "checkout"
    mod.build(repo)
    return repo


@docker
@pytest.mark.parametrize("branch,outcome,code", [
    ("candidate-weakened", "FAILED", 1),       # cap removed and the test weakened: X4 blocks
    ("candidate-legit", "INCONCLUSIVE", 2),    # an approved behaviour change: the base tests alone cannot judge it
])
def test_rerun_on_the_demo(demo, tmp_path, branch, outcome, code):
    out = tmp_path / "r.json"
    rc = cli.main(["rerun", "--repo", str(demo), "--base", "main", "--head", branch, "--image", IMAGE,
                   "--json", str(out)])
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert (rc, rep["outcome"]) == (code, outcome)
    if outcome == "INCONCLUSIVE":
        assert any(g["reason"] == "expectation_changed" for g in rep["gaps"])
