"""Regenerate every Tier A benchmark result from pinned inputs (PRD section 5.4).

  python -m benchmark.regenerate               write artifacts/benchmark/{tierA,perf}.json and the manifest
  python -m benchmark.regenerate --check       recompute tierA.json and fail if it differs from the committed file
  python -m benchmark.regenerate --tuning-only print tuning-split results only; writes nothing

Inputs: benchmark/{cases,split,sources}.lock.json (frozen in M1, before any
detector code). Cases are regenerated from the pinned repos and verified
against the locks first. Headline numbers use the held-out split only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections import Counter, defaultdict
from pathlib import Path

from provecode import __version__ as ENGINE_VERSION
from provecode import analyzer, contract as contract_mod, pipeline
from provecode.limits import Limits
from provecode.model import WEAKENING_RULES

from . import baseline_count, generate_cases, manifest, presets, readme_results

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "benchmark"
TIER_A = OUT / "tierA.json"
PERF = OUT / "perf.json"
APPROVER, APPROVED_AT = "tierA-harness (out of band)", "2026-11-20T00:00:00Z"
Z = 1.959963984540054

# One tuning round, decided on the tuning split only (held-out not run before it).
TUNING_LOG = [
    {
        "round": 1,
        "rule": "B2",
        "change": "A top-level return / pytest.skip() / `if False:` counts as a guard whenever statements follow "
                  "it (or sit inside it), not only when visible asserts follow; many tests check via helpers.",
        "evidence": ["W13-04", "W13-06", "W16-01"],
        "split_used": "tuning",
    }
]

# Changes made AFTER held-out results were first seen. Disclosed with their measured effect.
REVISION_LOG = [
    {
        "date": "2026-11-20",
        "source": "independent code review of engine/src/provecode (not benchmark data)",
        "changes": [
            "line splitting matches the tokenizer (form feeds no longer misalign lines)",
            "AST depth limit and RecursionError become input_limit gaps instead of crashes",
            "S4 flags removed parametrize cases even when others are added",
            "B2 covers class-level skips, `from pytest import mark`, and pytest.xfail(); ignores non-pytest .skip()",
            "S1 handles positional approx tolerances and non-numeric approx; S3 flags match= loosened to match-anything",
            "UTF-8 BOM files are analyzed; renamed test files keep their contract links",
        ],
        "effect_on_tier_a": "none: all 168 case results and all metrics identical before and after",
    }
]


# --------------------------------------------------------------------------- statistics


def wilson(k: int, n: int) -> dict:
    if n == 0:
        return {"k": 0, "n": 0, "rate": None, "lo": None, "hi": None}
    p = k / n
    denom = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / denom
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / denom
    return {"k": k, "n": n, "rate": round(p, 4), "lo": round(max(0.0, centre - half), 4),
            "hi": round(min(1.0, centre + half), 4)}


def mcnemar_exact(b: int, c: int) -> float | None:
    """Two-sided exact McNemar p-value on discordant pairs b (only A) and c (only B)."""
    n = b + c
    if n == 0:
        return None
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return float(f"{min(1.0, 2 * tail):.3g}")


# --------------------------------------------------------------------------- running


def sha256_paths(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.relative_to(ROOT).as_posix().encode() + b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def _approved(doc: dict) -> dict:
    return contract_mod.approve(doc, APPROVER, APPROVED_AT)


def load_cases() -> tuple[list[dict], dict]:
    gen = generate_cases.generate(generate_cases.load_sources())
    cases_lock, split_lock = generate_cases.build_locks(gen)
    for path, want in ((generate_cases.CASES_LOCK, cases_lock), (generate_cases.SPLIT_LOCK, split_lock)):
        if path.read_bytes().replace(b"\r\n", b"\n") != generate_cases.canonical(want):
            raise SystemExit(f"regenerated cases do not match {path.name}; inputs drifted")
    generate_cases.write_cache(gen)
    held = set(split_lock["held_out"])
    for c in cases_lock["cases"]:
        c["split"] = "held_out" if c["id"] in held else "tuning"
    return cases_lock["cases"], split_lock


def run_case(meta: dict) -> dict:
    d = generate_cases.CASES_DIR / meta["id"]
    base = (d / "base.py").read_bytes().decode("utf-8")
    head = (d / "head.py").read_bytes().decode("utf-8")
    rep = pipeline.run([(meta["file"], base, head)], _approved(meta["contract_head"]),
                       _approved(meta["contract_base"]), mode="static", limits=Limits.cli())
    weak = [f for f in rep["findings"] if f["rule"] in WEAKENING_RULES and f["severity"] != "info"]
    bl = baseline_count.detect(base, head)
    return {
        "id": meta["id"], "split": meta["split"], "pattern": meta["pattern"], "category": meta["category"],
        "kind": meta["kind"], "count_preserving": meta["count_preserving"], "repo": meta["repo"],
        "provecode": {
            "flagged": bool(weak),
            "blocking": any(f["severity"] == "blocking" for f in rep["findings"]),
            "rules": sorted({f["rule"] for f in rep["findings"]}),
            "outcome": rep["outcome"],
            "gaps": sorted({g["reason"] for g in rep["gaps"]} - {"requires_execution", "no_check"}),
        },
        "baseline": {"flagged": bool(bl), "rules": sorted({f.rule for f in bl})},
    }


# --------------------------------------------------------------------------- metrics


def _recall(rows: list[dict], who: str) -> dict:
    return wilson(sum(r[who]["flagged"] for r in rows), len(rows))


def metrics(rows: list[dict]) -> dict:
    weak = [r for r in rows if r["kind"] == "weakening"]
    legit = [r for r in rows if r["kind"] == "legit"]
    cp = [r for r in weak if r["count_preserving"]]
    ncp = [r for r in weak if not r["count_preserving"]]

    def paired(rs):
        only_pc = sum(r["provecode"]["flagged"] and not r["baseline"]["flagged"] for r in rs)
        only_bl = sum(r["baseline"]["flagged"] and not r["provecode"]["flagged"] for r in rs)
        return {"only_provecode": only_pc, "only_baseline": only_bl, "mcnemar_exact_p": mcnemar_exact(only_pc, only_bl)}

    def precision(who):
        tp = sum(r[who]["flagged"] for r in weak)
        fp = sum(r[who]["flagged"] for r in legit)
        return wilson(tp, tp + fp)

    by_cat: dict[str, list] = defaultdict(list)
    by_pat: dict[str, list] = defaultdict(list)
    for r in weak:
        by_cat[r["category"]].append(r)
    for r in rows:
        by_pat[r["pattern"]].append(r)
    return {
        "n_cases": len(rows), "n_weakening": len(weak), "n_legit": len(legit), "n_count_preserving": len(cp),
        "recall": {"provecode": _recall(weak, "provecode"), "baseline": _recall(weak, "baseline"),
                   "paired": paired(weak)},
        "recall_count_preserving": {"provecode": _recall(cp, "provecode"), "baseline": _recall(cp, "baseline"),
                                    "paired": paired(cp)},
        "recall_not_count_preserving": {"provecode": _recall(ncp, "provecode"), "baseline": _recall(ncp, "baseline"),
                                        "paired": paired(ncp)},
        "legit_flagged": {"provecode": wilson(sum(r["provecode"]["flagged"] for r in legit), len(legit)),
                          "baseline": wilson(sum(r["baseline"]["flagged"] for r in legit), len(legit))},
        "legit_blocking": {"provecode": wilson(sum(r["provecode"]["blocking"] for r in legit), len(legit))},
        "precision_on_tierA_mix": {"provecode": precision("provecode"), "baseline": precision("baseline")},
        "by_category": {k: {"provecode": _recall(v, "provecode"), "baseline": _recall(v, "baseline")}
                        for k, v in sorted(by_cat.items())},
        "by_pattern": {k: {"kind": v[0]["kind"], "category": v[0]["category"], "n": len(v),
                           "provecode_flagged": sum(r["provecode"]["flagged"] for r in v),
                           "baseline_flagged": sum(r["baseline"]["flagged"] for r in v)}
                       for k, v in sorted(by_pat.items())},
        "outcomes": dict(sorted(Counter(r["provecode"]["outcome"] for r in rows).items())),
        "analysis_gaps": dict(sorted(Counter(g for r in rows for g in r["provecode"]["gaps"]).items())),
    }


def falsification(held: dict) -> dict:
    """Pre-registered forms (PRD section 8.3), evaluated mechanically on held-out."""
    cp = held["recall_count_preserving"]
    ncp = held["recall_not_count_preserving"]
    f1 = cp["provecode"]["rate"] is not None and cp["provecode"]["k"] > cp["baseline"]["k"]
    return {
        "F1": {"statement": "On count-preserving held-out cases, PROVECODE recall exceeds the count-based baseline's.",
               "supported": f1, "provecode": cp["provecode"], "baseline": cp["baseline"],
               "mcnemar_exact_p": cp["paired"]["mcnemar_exact_p"]},
        "F2": {"statement": "On non-count-preserving cases (deletions, removed asserts), parity is expected; "
                            "the baseline outperforming PROVECODE is reported.",
               "baseline_outperforms": ncp["baseline"]["k"] > ncp["provecode"]["k"],
               "provecode": ncp["provecode"], "baseline": ncp["baseline"]},
        "F3": {"statement": "Share of legitimate held-out changes flagged as blocking (wide interval at this n; "
                            "Tier C is the stronger evidence).",
               "provecode": held["legit_blocking"]["provecode"]},
    }


def commit_order() -> dict:
    def first(path: str) -> dict | None:
        out = subprocess.run(["git", "-C", str(ROOT), "log", "--diff-filter=A", "--format=%H %cI", "--", path],
                             capture_output=True, text=True)
        lines = [line for line in out.stdout.splitlines() if line.strip()]
        if out.returncode != 0 or not lines:
            return None
        sha, when = lines[-1].split(" ", 1)
        return {"commit": sha, "committed_at": when}

    return {"split_frozen": first("benchmark/split.lock.json"), "patterns_frozen": first("benchmark/patterns.md"),
            "first_detector_code": first("engine/src/provecode/analyzer.py")}


def provenance(split_lock: dict) -> dict:
    eng = sorted((ROOT / "engine" / "src" / "provecode").glob("*.py"))
    bench = [ROOT / "benchmark" / n for n in ("baseline_count.py", "generate_cases.py", "regenerate.py")]
    return {
        "engine_version": ENGINE_VERSION,
        "engine_source_sha256": sha256_paths(eng),
        "benchmark_code_sha256": sha256_paths(bench),
        "cases_lock_sha256": hashlib.sha256(generate_cases.CASES_LOCK.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "split_lock_sha256": hashlib.sha256(generate_cases.SPLIT_LOCK.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "case_ids_sha256": split_lock["case_ids_sha256"],
        "sources": [{"repo": s["repo"], "sha": s["sha"], "licenses": [x["spdx"] for x in s["licenses"]]}
                    for s in generate_cases.load_sources()],
        "python": ".".join(platform.python_version_tuple()[:2]),
        "commit_order": commit_order(),
        "command": "python -m benchmark.regenerate",
    }


def build_tier_a(rows: list[dict], split_lock: dict) -> dict:
    held = [r for r in rows if r["split"] == "held_out"]
    tun = [r for r in rows if r["split"] == "tuning"]
    m_held = metrics(held)
    return {
        "schema_version": "1",
        "tier": "A",
        "label": "Injected cases on real repositories (experimental). Not real-world performance.",
        "detection_definition": {
            "provecode": "At least one S1-S4/B1-B3 finding with severity warning or blocking.",
            "baseline": "At least one finding from the count-based reimplementation.",
        },
        "baseline_rules": baseline_count.RULES,
        "headline": "held_out",
        "held_out": m_held,
        "tuning": metrics(tun),
        "all": metrics(rows),
        "falsification": falsification(m_held),
        "tuning_log": TUNING_LOG,
        "revision_log": REVISION_LOG,
        "cases": rows,
        "provenance": provenance(split_lock),
    }


# --------------------------------------------------------------------------- performance (non-deterministic)


def pathological_inputs() -> dict[str, tuple[str, str]]:
    test = "def test_{i}():\n    assert f({i}) == {i}\n\n"
    near_size = "".join(test.format(i=i) for i in range(10_000))
    # Largest input inside both live limits: <= 63 KiB and < 2,000 lines.
    near_size = near_size[: 63 * 1024]
    near_size = near_size[: near_size.rfind("\n\ndef ") + 1]
    while near_size.count("\n") >= 1_990:
        near_size = near_size[: near_size.rstrip("\n").rfind("\n\ndef ") + 1]
    long_test = "def test_{i}():\n    assert f('" + "x" * 600 + "') == 1\n\n"
    long_lines = "".join(long_test.format(i=i) for i in range(200))
    long_lines = long_lines[: 63 * 1024]
    long_lines = long_lines[: long_lines.rfind("\n\ndef ") + 1]
    lines = "def test_a():\n" + "    x = 1\n" * 1_990 + "    assert f() == 1\n"
    brackets = "def test_a():\n    assert f(" + "(" * 98 + "1" + ")" * 98 + ") == 1\n"
    indent = "def test_a():\n" + "".join("    " * (d + 1) + "if x:\n" for d in range(48)) + "    " * 49 + "assert f() == 1\n"
    over = "def test_a():\n    assert f(" + "(" * 5_000 + ")" * 5_000 + ") == 1\n"
    return {
        "many_tests_1989_lines": (near_size, near_size.replace("== 1\n", ">= 0\n", 50)),
        "63KiB_long_lines": (long_lines, long_lines.replace("== 1\n", ">= 0\n", 20)),
        "2000_lines": (lines, lines.replace("== 1", ">= 0")),
        "bracket_depth_100": (brackets, brackets.replace("== 1", ">= 0")),
        "indent_depth_49": (indent, indent.replace("== 1", ">= 0")),
        "over_limit_nesting_5000": (over, over),
    }


def perf(rows: list[dict]) -> dict:
    times, peaks = [], []
    for r in rows:
        d = generate_cases.CASES_DIR / r["id"]
        base, head = (d / "base.py").read_text(encoding="utf-8"), (d / "head.py").read_text(encoding="utf-8")
        t0 = time.perf_counter()  # timing pass, without tracemalloc overhead
        analyzer.analyze_file("tests/test_case.py", base, head, Limits.cli())
        times.append(time.perf_counter() - t0)
        tracemalloc.start()  # separate memory pass
        analyzer.analyze_file("tests/test_case.py", base, head, Limits.cli())
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
    worst = {}
    for name, (b, h) in pathological_inputs().items():
        t0 = time.perf_counter()
        res = analyzer.analyze_file("tests/test_p.py", b, h, Limits.live())
        dt_ = time.perf_counter() - t0
        tracemalloc.start()
        analyzer.analyze_file("tests/test_p.py", b, h, Limits.live())
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        worst[name] = {"bytes": len(b.encode()), "seconds": round(dt_, 4), "peak_bytes": peak,
                       "findings": len(res.findings), "gaps": [g.reason for g in res.gaps]}
    times.sort()
    return {
        "schema_version": "1",
        "note": "Measured on the machine that ran the regenerate command; not deterministic, not CI-diffed.",
        "machine": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                    "system": platform.system(), "machine": platform.machine()},
        "per_case": {"n": len(times), "median_s": round(statistics.median(times), 5),
                     "p95_s": round(times[int(0.95 * (len(times) - 1))], 5), "max_s": round(times[-1], 5),
                     "max_peak_bytes": max(peaks)},
        "worst_case_live_limits": worst,
        "live_limits": Limits.live().to_dict(),
    }


def dump(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--tuning-only", action="store_true")
    args = ap.parse_args(argv)

    cases, split_lock = load_cases()
    if args.tuning_only:
        rows = [run_case(c) for c in cases if c["split"] == "tuning"]
        m = metrics(rows)
        print(json.dumps({k: m[k] for k in ("recall", "recall_count_preserving", "legit_flagged", "by_pattern")},
                         indent=1))
        for r in rows:
            exp = r["kind"] == "weakening"
            if r["provecode"]["flagged"] != exp:
                print("MISS" if exp else "FALSE+", r["id"], r["provecode"]["rules"], r["provecode"]["gaps"])
        return 0

    rows = [run_case(c) for c in cases]
    data = dump(build_tier_a(rows, split_lock))
    if args.check:
        have = TIER_A.read_bytes().replace(b"\r\n", b"\n") if TIER_A.exists() else b""
        if have != data:
            print("artifacts/benchmark/tierA.json differs from a fresh regeneration", file=sys.stderr)
            return 1
        problems = presets.check() + readme_results.check() + manifest.verify()
        for p in problems:
            print(p, file=sys.stderr)
        print("tierA.json reproduced byte-for-byte" if not problems else "manifest problems")
        return 1 if problems else 0
    OUT.mkdir(parents=True, exist_ok=True)
    TIER_A.write_bytes(data)
    PERF.write_bytes(dump(perf(rows)))
    presets.write()
    manifest.write()
    readme_results.write()
    t = json.loads(data)["held_out"]
    print(f"held-out: recall PC {t['recall']['provecode']['rate']} vs BL {t['recall']['baseline']['rate']}; "
          f"count-preserving PC {t['recall_count_preserving']['provecode']['rate']} vs "
          f"BL {t['recall_count_preserving']['baseline']['rate']}; "
          f"legit blocking {t['legit_blocking']['provecode']['rate']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
