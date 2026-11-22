"""End-to-end CLI on the deterministic checkout demo repository."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from provecode import cli

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo"


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("build_demo", DEMO / "build_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    target = tmp_path_factory.mktemp("demo") / "checkout"
    shas = mod.build(target)
    return target, shas


def check(repo, head, contract, base_contract=None, tmp=None):
    out = tmp / f"{head}.json"
    argv = ["check", "--repo", str(repo), "--base", "main", "--head", head, "--contract", str(contract),
            "--json", str(out)]
    if base_contract:
        argv += ["--contract-base", str(base_contract)]
    code = cli.main(argv)
    return code, json.loads(out.read_text(encoding="utf-8"))


def test_demo_shas_are_deterministic(repo):
    _, shas = repo
    assert shas["base"] == "8653281964fe5be36ee96cdd788b2b4f29e274dd"


def test_weakened_candidate_fails(repo, tmp_path):
    code, r = check(repo[0], "candidate-weakened", DEMO / "contracts/checkout.v1.yaml", tmp=tmp_path)
    assert code == 1 and r["outcome"] == "FAILED"
    assert [(f["rule"], f["severity"], f["condition_ids"]) for f in r["findings"]] == [("S2", "blocking", ["C1"])]
    assert r["provenance"]["head_sha"] == repo[1]["weakened"]


def test_legit_candidate_is_inconclusive_statically(repo, tmp_path):
    code, r = check(repo[0], "candidate-legit", DEMO / "contracts/checkout.v2.yaml",
                    DEMO / "contracts/checkout.v1.yaml", tmp=tmp_path)
    assert code == 2 and r["outcome"] == "INCONCLUSIVE"
    assert [(f["rule"], f["severity"]) for f in r["findings"]] == [("V0", "info")]


def test_legit_change_without_contract_update_is_still_not_blocking(repo, tmp_path):
    # V0 is info regardless; only weakening rules can block.
    code, r = check(repo[0], "candidate-legit", DEMO / "contracts/checkout.v1.yaml", tmp=tmp_path)
    assert code == 2 and all(f["severity"] == "info" for f in r["findings"])


def test_candidate_touching_contract_path_fails(repo, tmp_path):
    target = repo[0]
    subprocess.run(["git", "-C", str(target), "checkout", "-q", "-b", "touch-contract", "main"], check=True)
    (target / "contracts").mkdir()
    (target / "contracts" / "checkout.yaml").write_text("weakened: true\n")
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"],
                   check=True)
    subprocess.run(["git", "-C", str(target), "checkout", "-q", "main"], check=True)
    code, r = check(target, "touch-contract", DEMO / "contracts/checkout.v1.yaml", tmp=tmp_path)
    assert code == 1
    assert r["findings"][0]["rule"] == "CONTRACT_TOUCHED"


def _check_zero_config(repo, head, tmp):
    out = tmp / f"{head}-zc.json"
    code = cli.main(["check", "--repo", str(repo), "--base", "main", "--head", head, "--block-on", "weakening",
                     "--json", str(out)])
    return code, json.loads(out.read_text(encoding="utf-8"))


def test_check_without_a_contract_blocks_a_weakening_with_block_on_weakening(repo, tmp_path):
    code, rep = _check_zero_config(repo[0], "candidate-weakened", tmp_path)
    assert code == 1
    assert "S2" in {f["rule"] for f in rep["findings"]}


def test_check_without_a_contract_passes_a_legit_change_with_block_on_weakening(repo, tmp_path):
    code, rep = _check_zero_config(repo[0], "candidate-legit", tmp_path)
    assert code == 2 and rep["outcome"] == "INCONCLUSIVE"


def test_check_without_a_contract_or_block_on_is_still_a_usage_error(repo, capsys):
    assert cli.main(["check", "--repo", str(repo[0]), "--base", "main", "--head", "candidate-legit"]) == 3


def test_bad_ref_is_usage_error(repo, capsys):
    assert cli.main(["check", "--repo", str(repo[0]), "--base", "main", "--head", "nope",
                     "--contract", str(DEMO / "contracts/checkout.v1.yaml")]) == 3


def test_option_like_ref_is_rejected(repo):
    # A ref that git would read as an option must be refused before reaching git.
    assert cli.main(["check", "--repo", str(repo[0]), "--base", "main", "--head=--output=x",
                     "--contract", str(DEMO / "contracts/checkout.v1.yaml")]) == 3


def test_compile_emits_canonical_json(tmp_path):
    out = tmp_path / "c.json"
    assert cli.main(["compile", str(DEMO / "contracts/checkout.v1.yaml"), "-o", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["approval"]["content_hash"].startswith("sha256:")
