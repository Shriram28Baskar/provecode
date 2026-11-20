"""PRECOMPUTED live-detector presets (architecture section 5.2), written to artifacts/presets/.

Each preset holds the base and head test file, the approved contract(s) the
browser bundles, and the report the same pipeline produces at build time.
Called by `python -m benchmark.regenerate`. Deterministic: no timestamps.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from provecode import contract as contract_mod, pipeline
from provecode.limits import Limits

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
OUT = ROOT / "artifacts" / "presets"
TEST_PATH = "tests/test_pricing.py"


def _demo_shas() -> dict:
    spec = importlib.util.spec_from_file_location("build_demo", DEMO / "build_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        return mod.build(Path(d) / "checkout")


def _src(name: str) -> str:
    return (DEMO / "scenario" / name).read_bytes().decode("utf-8").replace("\r\n", "\n")


def build() -> dict[str, bytes]:
    shas = _demo_shas()
    v1 = contract_mod.read_any(DEMO / "contracts" / "checkout.v1.yaml")
    v2 = contract_mod.read_any(DEMO / "contracts" / "checkout.v2.yaml")
    presets = [
        {"id": "weakened", "title": "The cap is removed and the test is weakened",
         "summary": "The agent removed the 20% cap and loosened the test so everything still passes.",
         "head_name": "test_pricing_weakened.py", "contract_head": v1, "contract_base": None, "head_sha": shas["weakened"]},
        {"id": "legit", "title": "The cap is raised to 25% under an approved contract",
         "summary": "The business approved contract v2. The test changes its expected value to match.",
         "head_name": "test_pricing_legit.py", "contract_head": v2, "contract_base": v1, "head_sha": shas["legit"]},
    ]
    out: dict[str, bytes] = {}
    base_src = _src("test_pricing_base.py")
    for p in presets:
        head_src = _src(p["head_name"])
        rep = pipeline.run([(TEST_PATH, base_src, head_src)], p["contract_head"], p["contract_base"],
                           mode="precomputed", limits=Limits.live(),
                           provenance={"base_sha": shas["base"], "head_sha": p["head_sha"],
                                       "demo_repo": "demo/build_demo.py (deterministic SHAs)"})
        doc = {"schema_version": "1", "id": p["id"], "title": p["title"], "summary": p["summary"], "path": TEST_PATH,
               "base_source": base_src, "head_source": head_src, "contract_head": p["contract_head"],
               "contract_base": p["contract_base"], "report": rep}
        out[f"{p['id']}.json"] = (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    return out


def write() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in build().items():
        (OUT / name).write_bytes(data)


def check() -> list[str]:
    problems = []
    for name, data in build().items():
        p = OUT / name
        if not p.exists() or p.read_bytes().replace(b"\r\n", b"\n") != data:
            problems.append(f"artifacts/presets/{name} differs from a fresh build")
    return problems


def digest() -> str:
    return hashlib.sha256(b"".join(build().values())).hexdigest()
