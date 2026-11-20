"""Measure Ruff as a "tests + static analysis" baseline on Tier A (PRD section 6.3, baseline 2).

Research measurement for docs/research/landscape-2026-11.md. Not product code; edits nothing.

Design, fixed before the first run:
- Each case's base.py and head.py are linted with `ruff check --isolated --no-cache` via stdin,
  using the case's real file path so path-based rules behave as in the repo.
- A case is "flagged" when any rule code fires MORE times in head than in base (new diagnostics).
- Two configurations:
    ALL     every Ruff rule (an upper bound on what a linter diff can see; expected to be noisy)
    SUBSET  rules whose documentation is about assertion strength or test strength:
            PLR0124 comparison-with-itself, PLR0133 comparison-of-constant, F631 assert-tuple,
            B011 assert-false, PT015 pytest-assert-always-false, PLW0129 assert-on-string-literal,
            B015 useless-comparison, PT011 pytest-raises-too-broad, PT012 pytest-raises-with-multiple-
            statements, PT018 pytest-composite-assertion
- PROVECODE's per-case results are read from artifacts/benchmark/tierA.json (not recomputed).

Prerequisites: `python -m benchmark.regenerate --check` (populates benchmark/.cache/cases) and
`pip install ruff==0.17.6`.
Run:  python docs/research/scripts/ruff_tierA.py   ->  docs/research/data/ruff_tierA.json
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CASES = ROOT / "benchmark" / ".cache" / "cases"
OUT = ROOT / "docs" / "research" / "data" / "ruff_tierA.json"
SUBSET = ["PLR0124", "PLR0133", "F631", "B011", "PT015", "PLW0129", "B015", "PT011", "PT012", "PT018"]
Z = 1.959963984540054


def wilson(k: int, n: int) -> dict:
    if n == 0:
        return {"k": 0, "n": 0, "rate": None, "lo": None, "hi": None}
    p = k / n
    d = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / d
    h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return {"k": k, "n": n, "rate": round(p, 4), "lo": round(max(0, c - h), 4), "hi": round(min(1, c + h), 4)}


def ruff_codes(source: bytes, path: str, select: str) -> Counter:
    out = subprocess.run(
        ["ruff", "check", "--isolated", "--no-cache", "--select", select, "--output-format", "json",
         "--exit-zero", "--stdin-filename", path, "-"],
        input=source, capture_output=True,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode("utf-8", "replace"))
    return Counter(d["code"] for d in json.loads(out.stdout or b"[]") if d.get("code"))


def new_codes(base: Counter, head: Counter) -> list[str]:
    return sorted(c for c in head if head[c] > base.get(c, 0))


def main() -> int:
    version = subprocess.run(["ruff", "--version"], capture_output=True, text=True).stdout.strip()
    tier = json.loads((ROOT / "artifacts" / "benchmark" / "tierA.json").read_text(encoding="utf-8"))
    rows = []
    for c in tier["cases"]:
        d = CASES / c["id"]
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        base, head = (d / "base.py").read_bytes(), (d / "head.py").read_bytes()
        row = {"id": c["id"], "split": c["split"], "pattern": c["pattern"], "kind": c["kind"],
               "category": c["category"], "count_preserving": c["count_preserving"],
               "provecode": c["provecode"]["flagged"]}
        for name, select in (("all", "ALL"), ("subset", ",".join(SUBSET))):
            codes = new_codes(ruff_codes(base, meta["file"], select), ruff_codes(head, meta["file"], select))
            row[f"ruff_{name}"] = bool(codes)
            row[f"ruff_{name}_codes"] = codes
        rows.append(row)

    def summarize(rs):
        weak = [r for r in rs if r["kind"] == "weakening"]
        legit = [r for r in rs if r["kind"] == "legit"]
        out = {"n_weakening": len(weak), "n_legit": len(legit)}
        for who in ("provecode", "ruff_all", "ruff_subset"):
            out[who] = {"recall": wilson(sum(r[who] for r in weak), len(weak)),
                        "recall_count_preserving": wilson(sum(r[who] for r in weak if r["count_preserving"]),
                                                          sum(r["count_preserving"] for r in weak)),
                        "legit_flagged": wilson(sum(r[who] for r in legit), len(legit))}
        out["union_provecode_ruff_subset"] = {
            "recall": wilson(sum(r["provecode"] or r["ruff_subset"] for r in weak), len(weak)),
            "legit_flagged": wilson(sum(r["provecode"] or r["ruff_subset"] for r in legit), len(legit))}
        out["only_ruff_subset_caught"] = sorted(r["id"] for r in weak if r["ruff_subset"] and not r["provecode"])
        out["only_provecode_caught"] = len([r for r in weak if r["provecode"] and not r["ruff_subset"]])
        return out

    by_pattern = defaultdict(lambda: Counter())
    subset_codes = defaultdict(Counter)
    for r in rows:
        bp = by_pattern[r["pattern"]]
        bp["n"] += 1
        for who in ("provecode", "ruff_all", "ruff_subset"):
            bp[who] += r[who]
        for code in r["ruff_subset_codes"]:
            subset_codes[r["pattern"]][code] += 1

    result = {
        "schema_version": "1",
        "ruff_version": version,
        "subset_rules": SUBSET,
        "cases_lock_sha256": hashlib.sha256(
            (ROOT / "benchmark" / "cases.lock.json").read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "detection_definition": "a rule code fires more times in head than in base",
        "held_out": summarize([r for r in rows if r["split"] == "held_out"]),
        "all": summarize(rows),
        "by_pattern": {k: dict(v) for k, v in sorted(by_pattern.items())},
        "subset_codes_by_pattern": {k: dict(v) for k, v in sorted(subset_codes.items())},
        "cases": rows,
        "command": "python docs/research/scripts/ruff_tierA.py",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    h, a = result["held_out"], result["all"]
    for label, s in (("held-out", h), ("all", a)):
        print(label, {w: (s[w]["recall"]["k"], s[w]["recall"]["n"], s[w]["legit_flagged"]["k"])
                      for w in ("provecode", "ruff_all", "ruff_subset")},
              "union", s["union_provecode_ruff_subset"]["recall"]["k"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
