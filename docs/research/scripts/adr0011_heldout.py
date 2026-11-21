"""ADR 0011 held-out evaluation on Tier A-b: the new rules versus "import Ruff instead".

Research measurement, fixed in benchmark/patterns_blindspots.md before any rule code. Not product
code; edits nothing in the repository.

Compared, per Tier A-b case:
- provecode:     the current engine (S5, B4), read from artifacts/benchmark/tierA_b.json;
- before_0011:   the engine at the Tier A-b freeze commit, exported with `git archive` into a
                 temporary directory and run in a separate isolated interpreter;
- ruff_variant:  before_0011, plus the case counts when one of RUFF_CODES fires more often in head
                 than in base (Ruff 0.17.6, --isolated);
- baseline:      the count-based baseline, from tierA_b.json.

Drop criteria (ADR 0011):
- a new rule flags any held-out legitimate edit;
- the Ruff variant reaches the same held-out recall with no more legitimate edits flagged.

Prerequisites: `python -m benchmark.regenerate` (writes tierA_b.json and benchmark/.cache/cases_blindspots)
and `pip install ruff==0.17.6`.
Run:  python docs/research/scripts/adr0011_heldout.py  ->  docs/research/data/adr0011_heldout.json
"""

from __future__ import annotations

import io
import json
import math
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CASES = ROOT / "benchmark" / ".cache" / "cases_blindspots"
TIER = ROOT / "artifacts" / "benchmark" / "tierA_b.json"
OUT = ROOT / "docs" / "research" / "data" / "adr0011_heldout.json"
FREEZE = "5b8a20a"  # benchmark: freeze Tier A-b ... before any ADR 0011 rule code
RUFF_CODES = ["PLR0124", "SIM222", "B015", "B011", "PT015", "S110", "SIM105"]
Z = 1.959963984540054

OLD_ENGINE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from provecode import pipeline, contract
from provecode.limits import Limits
from provecode.model import WEAKENING_RULES
out = {}
for d in json.loads(sys.stdin.read()):
    from pathlib import Path
    p = Path(d)
    m = json.loads((p / "meta.json").read_text(encoding="utf-8"))
    ap = lambda doc: contract.approve(doc, "tierA-harness (out of band)", "2026-11-20T00:00:00Z")
    r = pipeline.run([(m["file"], (p / "base.py").read_bytes().decode("utf-8"), (p / "head.py").read_bytes().decode("utf-8"))],
                     ap(m["contract_head"]), ap(m["contract_base"]), mode="static", limits=Limits.cli())
    out[m["id"]] = any(f["rule"] in WEAKENING_RULES and f["severity"] != "info" for f in r["findings"])
print(json.dumps(out))
"""


def wilson(k: int, n: int) -> dict:
    if n == 0:
        return {"k": 0, "n": 0, "rate": None, "lo": None, "hi": None}
    p = k / n
    d = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / d
    h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return {"k": k, "n": n, "rate": round(p, 4), "lo": round(max(0, c - h), 4), "hi": round(min(1, c + h), 4)}


def ruff_codes(source: bytes, path: str) -> Counter:
    out = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache", "--select", ",".join(RUFF_CODES),
         "--output-format", "json", "--exit-zero", "--stdin-filename", path, "-"],
        input=source, capture_output=True,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode("utf-8", "replace"))
    return Counter(d["code"] for d in json.loads(out.stdout or b"[]") if d.get("code"))


def old_engine(case_dirs: list[str]) -> dict[str, bool]:
    with tempfile.TemporaryDirectory(prefix="provecode-pre0011-") as tmp:
        tar = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", FREEZE, "engine/src/provecode"],
                             capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
            # The archive is this repository's own history. Use the safe extraction filter where the
            # interpreter has it (3.11.4+); plain extraction otherwise.
            if hasattr(tarfile, "data_filter"):
                tf.extractall(tmp, filter="data")
            else:
                tf.extractall(tmp)
        src = str(Path(tmp) / "engine" / "src")
        out = subprocess.run([sys.executable, "-I", "-c", OLD_ENGINE, src], input=json.dumps(case_dirs),
                             capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(out.stderr)
        return json.loads(out.stdout)


def main() -> int:
    version = subprocess.run([sys.executable, "-m", "ruff", "--version"], capture_output=True, text=True).stdout.strip()
    tier = json.loads(TIER.read_text(encoding="utf-8"))
    old = old_engine([str(CASES / c["id"]) for c in tier["cases"]])
    rows = []
    for c in tier["cases"]:
        d = CASES / c["id"]
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        b, h = ruff_codes((d / "base.py").read_bytes(), meta["file"]), ruff_codes((d / "head.py").read_bytes(), meta["file"])
        new = sorted(code for code in h if h[code] > b.get(code, 0))
        rows.append({"id": c["id"], "split": c["split"], "pattern": c["pattern"], "kind": c["kind"],
                     "count_preserving": c["count_preserving"],
                     "provecode": c["provecode"]["flagged"], "provecode_rules": c["provecode"]["rules"],
                     "before_0011": old[c["id"]], "ruff_codes": new, "ruff_alone": bool(new),
                     "ruff_variant": old[c["id"]] or bool(new), "baseline": c["baseline"]["flagged"]})
    whos = ("provecode", "before_0011", "ruff_variant", "ruff_alone", "baseline")

    def summarize(rs):
        weak = [r for r in rs if r["kind"] == "weakening"]
        legit = [r for r in rs if r["kind"] == "legit"]
        return {"n_weakening": len(weak), "n_legit": len(legit),
                **{w: {"recall": wilson(sum(r[w] for r in weak), len(weak)),
                       "legit_flagged": wilson(sum(r[w] for r in legit), len(legit))} for w in whos}}

    held = [r for r in rows if r["split"] == "held_out"]
    hs = summarize(held)
    new_rule_fp = sorted({rule for r in held if r["kind"] == "legit" for rule in r["provecode_rules"]
                          if rule in ("S5", "B4")})
    pc, rv = hs["provecode"], hs["ruff_variant"]
    by_pattern = {}
    for r in rows:
        bp = by_pattern.setdefault(r["pattern"], Counter())
        bp["n"] += 1
        for w in whos:
            bp[w] += r[w]
    result = {
        "schema_version": "1",
        "ruff_version": version,
        "ruff_codes": RUFF_CODES,
        "before_0011_engine": f"git archive {FREEZE} engine/src/provecode",
        "detection_definition": "provecode/before_0011: a weakening finding with severity warning or blocking; "
                                "ruff: a selected code fires more often in head than in base",
        "held_out": hs,
        "tuning": summarize([r for r in rows if r["split"] == "tuning"]),
        "all": summarize(rows),
        "drop_criteria": {
            "new_rule_flags_heldout_legit": new_rule_fp,
            "ruff_variant_matches": (rv["recall"]["k"] >= pc["recall"]["k"]
                                     and rv["legit_flagged"]["k"] <= pc["legit_flagged"]["k"]),
        },
        "by_pattern": {k: dict(v) for k, v in sorted(by_pattern.items())},
        "cases": rows,
        "command": "python docs/research/scripts/adr0011_heldout.py",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    for label, s in (("held-out", hs), ("all", result["all"])):
        print(label, {w: (s[w]["recall"]["k"], s[w]["recall"]["n"], s[w]["legit_flagged"]["k"], s[w]["legit_flagged"]["n"])
                      for w in whos})
    print("drop criteria:", result["drop_criteria"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
