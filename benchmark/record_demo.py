"""Produce the RECORDED checkout runs in Docker (artifacts/runs/).

  python -m benchmark.record_demo

Builds the deterministic demo repo, builds the run image from the digest-pinned
base, then runs `provecode record` for the weakened candidate (contract v1)
and the legitimate candidate (contract v2, base v1). Writes report JSON and
Markdown per run, then updates the manifest.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from provecode import cli

from . import manifest

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
RUNS = ROOT / "artifacts" / "runs"
BASE_DIGEST = (ROOT / "docker" / "base.digest").read_text(encoding="utf-8").strip()
IMAGE = "provecode-run:local"
EXPECTED = {"checkout-weakened": "FAILED", "checkout-legit": "VERIFIED"}


def build_image() -> None:
    out = subprocess.run(["docker", "build", "-q", "--build-arg", f"BASE={BASE_DIGEST}", "-t", IMAGE,
                          str(ROOT / "docker")], capture_output=True, text=True, timeout=900)
    if out.returncode != 0:
        raise SystemExit(f"docker build failed: {out.stderr}")


def main() -> int:
    spec = importlib.util.spec_from_file_location("build_demo", DEMO / "build_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    repo = DEMO / ".build" / "checkout"
    mod.build(repo)
    build_image()
    runs = {
        "checkout-weakened": ["--head", "candidate-weakened", "--contract", str(DEMO / "contracts/checkout.v1.yaml"),
                              "--checks-dir", str(DEMO / "contract_checks/v1")],
        "checkout-legit": ["--head", "candidate-legit", "--contract", str(DEMO / "contracts/checkout.v2.yaml"),
                           "--contract-base", str(DEMO / "contracts/checkout.v1.yaml"),
                           "--checks-dir", str(DEMO / "contract_checks/v2")],
    }
    ok = True
    for name, extra in runs.items():
        out = RUNS / name
        out.mkdir(parents=True, exist_ok=True)
        code = cli.main(["record", "--repo", str(repo), "--base", "main", *extra, "--image", IMAGE,
                         "--json", str(out / "report.json"), "--md", str(out / "report.md")])
        rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
        rep["provenance"]["base_image"] = BASE_DIGEST
        (out / "report.json").write_text(json.dumps(rep, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                                         encoding="utf-8", newline="\n")
        print(f"{name}: {rep['outcome']} (exit {code}); expected {EXPECTED[name]}")
        ok &= rep["outcome"] == EXPECTED[name]
    manifest.write()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
