"""artifacts/manifest.json: sha256 and provenance of every generated artifact (ADR 0005).

The site build and CI resolve every rendered number through this manifest.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
MANIFEST = ARTIFACTS / "manifest.json"

GENERATORS = {
    "benchmark/tierA.json": ("python -m benchmark.regenerate", True),
    "benchmark/perf.json": ("python -m benchmark.regenerate", False),
    "benchmark/parity.json": ("node benchmark/pyodide_parity.mjs", True),
    "runs/": ("python -m benchmark.record_demo", False),
    "presets/": ("python -m benchmark.presets", True),
}


def _meta(rel: str) -> tuple[str, bool]:
    for prefix, meta in GENERATORS.items():
        if rel == prefix or (prefix.endswith("/") and rel.startswith(prefix)):
            return meta
    return ("unknown", False)


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build() -> dict:
    entries = []
    for p in sorted(ARTIFACTS.rglob("*")):
        if not p.is_file() or p == MANIFEST:
            continue
        rel = p.relative_to(ARTIFACTS).as_posix()
        gen, deterministic = _meta(rel)
        entries.append({"path": rel, "sha256": sha256_file(p), "bytes": p.stat().st_size,
                        "generator": gen, "deterministic": deterministic})
    return {"schema_version": "1", "artifacts": entries}


def write() -> None:
    ARTIFACTS.mkdir(exist_ok=True)
    MANIFEST.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def verify() -> list[str]:
    """Return problems: files missing from the manifest, hash mismatches, or listed files that are absent."""
    if not MANIFEST.exists():
        return ["artifacts/manifest.json is missing"]
    have = {e["path"]: e for e in json.loads(MANIFEST.read_text(encoding="utf-8"))["artifacts"]}
    now = {e["path"]: e for e in build()["artifacts"]}
    problems = []
    for path in sorted(set(have) | set(now)):
        if path not in now:
            problems.append(f"{path}: listed in manifest but missing")
        elif path not in have:
            problems.append(f"{path}: not in manifest")
        elif have[path]["sha256"] != now[path]["sha256"]:
            problems.append(f"{path}: sha256 mismatch")
    oversize = [e["path"] for e in now.values() if e["bytes"] > 1_000_000]
    problems += [f"{p}: exceeds the 1 MB per-file bound (ADR 0005)" for p in oversize]
    return problems


if __name__ == "__main__":
    import sys

    issues = verify()
    for i in issues:
        print(i)
    sys.exit(1 if issues else 0)
