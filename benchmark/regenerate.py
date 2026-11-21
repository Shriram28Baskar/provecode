"""Regenerate every Tier A and Tier A-u benchmark result from pinned inputs (PRD section 5.4).

  python -m benchmark.regenerate               write artifacts/benchmark/{tierA,tierA_u,tierA_b,perf}.json and the manifest
  python -m benchmark.regenerate --check       recompute tierA, tierA_u and tierA_b.json; fail if any differs
  python -m benchmark.regenerate --tuning-only print tuning-split results only; writes nothing

Inputs: benchmark/{cases,split,sources}.lock.json (Tier A, frozen in M1, before any
detector code) and benchmark/{cases,split,sources}_unittest.lock.json (Tier A-u, frozen
before the ADR 0009 detector change). Cases are regenerated from the pinned repos and
verified against the locks first. Headline numbers use the held-out split only.
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

from . import (baseline_count, generate_cases, generate_cases_blindspots, generate_cases_unittest, manifest, presets,
               readme_results)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "benchmark"
TIER_A = OUT / "tierA.json"
TIER_A_U = OUT / "tierA_u.json"
TIER_A_B = OUT / "tierA_b.json"
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
    },
    {
        "date": "2026-11-20",
        "source": "CI reproducibility check on Linux",
        "changes": [
            "case files are read as raw git objects instead of a Windows working tree where core.autocrlf had "
            "converted LF to CRLF; cases.lock.json byte hashes were regenerated",
        ],
        "effect_on_tier_a": "none: same 168 case ids, patterns, sites and split; only base/head byte hashes changed; "
                            "all case results and metrics identical",
    },
    {
        "date": "2026-11-21",
        "source": "ADR 0009 (recognise unittest-style tests), from the landscape research report, not Tier A data",
        "changes": [
            "TestCase subclasses are test classes; self.assert* calls are checks mapped onto S1/S2/B3",
            "self.assertRaises*/assertWarns* feed S3; the Regex argument counts as match=",
            "unittest.skip/skipIf/skipUnless/expectedFailure and a leading self.skipTest() are B2",
            "tests.py and modules under tests/ are test files; renames to uncollected names are B1",
        ],
        "effect_on_tier_a": "none: all 168 case results and all metrics identical before and after",
    },
    {
        "date": "2026-11-21",
        "source": "ADR 0011 (rules S5 and B4), developed on the Tier A-b tuning split, not on Tier A",
        "changes": [
            "S5: a check replaced by a self-comparison or given an `or <true constant>`",
            "B4: a check newly inside a try/except or contextlib.suppress that swallows AssertionError",
        ],
        "effect_on_tier_a": "17 case results changed, all of them W17-W19 weakenings, which these rules target and "
                            "whose results had been seen; W17 5/6, W18 6/6, W19 6/6 now flagged; held-out recall "
                            "37 -> 43 of 44; no legitimate case changed. Recall on W17-W19 is therefore no longer an "
                            "unbiased estimate; Tier A-b is the fresh evaluation of these rules",
    },
]

# Tier A-b: rule development rounds, on the tuning split only (ADR 0011).
TUNING_LOG_BLINDSPOTS: list[dict] = [
    {
        "round": 1,
        "rule": "S5",
        "change": "A base self-comparison such as `c == c` is a real reflexivity test of a custom __eq__, not a "
                  "vacuous check, so adding `or True` to it is S5; only an `or`-vacuous base check counts as "
                  "already vacuous.",
        "evidence": ["X03-01"],
        "split_used": "tuning",
    }
]

# Tier A-u: changes made after its held-out results were first seen (2026-11-21).
REVISION_LOG_UNITTEST = [
    {
        "date": "2026-11-21",
        "source": "SWE-bench Verified gold test-file measurement (docs/research), not Tier A-u data",
        "changes": [
            "a singular test/ directory no longer makes every module a test file (django/test/testcases.py is "
            "framework code); tests/ directories and test_*.py / *_test.py / tests.py names are unchanged",
        ],
        "effect_on_tier_a": "none: no Tier A-u case is in a test/ directory; all 132 case results and all metrics "
                            "identical before and after",
    },
    {
        "date": "2026-11-21",
        "source": "ADR 0011 (rules S5 and B4), developed on the Tier A-b tuning split, not on Tier A-u",
        "changes": [
            "S5: a check replaced by a self-comparison or given an `or <true constant>`",
            "B4: a check newly inside a try/except or contextlib.suppress that swallows AssertionError",
        ],
        "effect_on_tier_a": "11 case results changed, all of them U13-U14 weakenings, which these rules target and "
                            "whose results had been seen; U13 6/6, U14 5/6 now flagged; held-out recall 23 -> 27 of 28; "
                            "no legitimate case changed. The remaining miss, U14-04 (a swallowed self.assertRaises), "
                            "is a known B4 gap, deliberately not fixed on seen data",
    },
]


def _real_history_fix(n: int) -> dict:
    """Two false positives found by inspecting real commits (docs/research/data/real_history_scan.json)."""
    return {
        "date": "2026-11-21",
        "source": "inspection of S5/B4 hits on real commits (more-itertools 5c715d813a, pyparsing e0a6f21205), "
                  "not benchmark data",
        "changes": [
            "S5 self-comparison fires only when the base check compared against a different expected value "
            "(assertTrue(r) -> assertEqual(r, r) is not a weakening)",
            "B4 matches swallowed checks by what they check, not their text, and fires only when more checks are "
            "swallowed than before (a reworded message inside an existing try/except is not a finding)",
        ],
        "effect_on_tier_a": f"none: all {n} case results and all metrics identical before and after",
    }


REVISION_LOG.append(_real_history_fix(168))
REVISION_LOG_UNITTEST.append(_real_history_fix(132))
REVISION_LOG_BLINDSPOTS = [_real_history_fix(99)]


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


def load_cases(gm=generate_cases) -> tuple[list[dict], dict]:
    """gm is the tier's case generator module (generate_cases or generate_cases_unittest)."""
    gen = gm.generate(gm.load_sources())
    cases_lock, split_lock = gm.build_locks(gen)
    for path, want in ((gm.CASES_LOCK, cases_lock), (gm.SPLIT_LOCK, split_lock)):
        if path.read_bytes().replace(b"\r\n", b"\n") != generate_cases.canonical(want):
            raise SystemExit(f"regenerated cases do not match {path.name}; inputs drifted")
    gm.write_cache(gen)
    held = set(split_lock["held_out"])
    for c in cases_lock["cases"]:
        c["split"] = "held_out" if c["id"] in held else "tuning"
    return cases_lock["cases"], split_lock


def run_case(meta: dict, cases_dir: Path = generate_cases.CASES_DIR) -> dict:
    d = cases_dir / meta["id"]
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


def _git_lines(args: list[str]) -> list[str] | None:
    out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    lines = [line for line in out.stdout.splitlines() if line.strip()]
    return lines if out.returncode == 0 and lines else None


def _commit(line: str) -> dict:
    sha, when = line.split(" ", 1)
    return {"commit": sha, "committed_at": when}


def _first_added(path: str) -> dict | None:
    lines = _git_lines(["log", "--diff-filter=A", "--format=%H %cI", "--", path])
    return _commit(lines[-1]) if lines else None


def commit_order() -> dict:
    return {"split_frozen": _first_added("benchmark/split.lock.json"),
            "patterns_frozen": _first_added("benchmark/patterns.md"),
            "first_detector_code": _first_added("engine/src/provecode/analyzer.py")}


def _frozen_before_change(split_path: str, patterns_path: str) -> dict:
    """The split and patterns were committed before the detector change. The detector change is the
    first commit after the split freeze, on its ancestry path, that touches analyzer.py, so by
    construction it descends from the freeze."""
    split = _first_added(split_path)
    change = None
    if split:
        lines = _git_lines(["log", "--format=%H %cI", "--ancestry-path", f"{split['commit']}..HEAD",
                            "--", "engine/src/provecode/analyzer.py"])
        change = _commit(lines[-1]) if lines else None
    return {"split_frozen": split, "patterns_frozen": _first_added(patterns_path), "detector_change": change}


def commit_order_unittest() -> dict:
    """Tier A-u: frozen before the ADR 0009 analyzer change."""
    return _frozen_before_change("benchmark/split_unittest.lock.json", "benchmark/patterns_unittest.md")


def commit_order_blindspots() -> dict:
    """Tier A-b: frozen before the ADR 0011 rule code."""
    return _frozen_before_change("benchmark/split_blindspots.lock.json", "benchmark/patterns_blindspots.md")


TIERS = {
    "A": {"gen": generate_cases, "out": TIER_A, "commit_order": commit_order,
          "bench_code": ("baseline_count.py", "generate_cases.py", "regenerate.py"),
          "label": "Injected cases on real repositories (experimental). Not real-world performance.",
          "tuning_log": TUNING_LOG, "revision_log": REVISION_LOG},
    "A-u": {"gen": generate_cases_unittest, "out": TIER_A_U, "commit_order": commit_order_unittest,
            "bench_code": ("baseline_count.py", "generate_cases.py", "generate_cases_unittest.py", "regenerate.py"),
            "label": "Injected unittest-style cases on real repositories (experimental, ADR 0009). "
                     "Not real-world performance.",
            "tuning_log": [], "revision_log": REVISION_LOG_UNITTEST},
    "A-b": {"gen": generate_cases_blindspots, "out": TIER_A_B, "commit_order": commit_order_blindspots,
            "bench_code": ("baseline_count.py", "generate_cases.py", "generate_cases_unittest.py",
                           "generate_cases_blindspots.py", "regenerate.py"),
            "label": "Injected vacuous and swallowed checks on real repositories (experimental, ADR 0011). "
                     "Not real-world performance.",
            "tuning_log": TUNING_LOG_BLINDSPOTS, "revision_log": REVISION_LOG_BLINDSPOTS},
}


def provenance(split_lock: dict, tier: str = "A") -> dict:
    t = TIERS[tier]
    gm = t["gen"]
    eng = sorted((ROOT / "engine" / "src" / "provecode").glob("*.py"))
    bench = [ROOT / "benchmark" / n for n in t["bench_code"]]
    return {
        "engine_version": ENGINE_VERSION,
        "engine_source_sha256": sha256_paths(eng),
        "benchmark_code_sha256": sha256_paths(bench),
        "cases_lock_sha256": hashlib.sha256(gm.CASES_LOCK.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "split_lock_sha256": hashlib.sha256(gm.SPLIT_LOCK.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "case_ids_sha256": split_lock["case_ids_sha256"],
        "sources": [{"repo": s["repo"], "sha": s["sha"], "licenses": [x["spdx"] for x in s["licenses"]]}
                    for s in gm.load_sources()],
        "python": ".".join(platform.python_version_tuple()[:2]),
        "commit_order": t["commit_order"](),
        "command": "python -m benchmark.regenerate",
    }


def build_tier_a(rows: list[dict], split_lock: dict, tier: str = "A") -> dict:
    held = [r for r in rows if r["split"] == "held_out"]
    tun = [r for r in rows if r["split"] == "tuning"]
    m_held = metrics(held)
    t = TIERS[tier]
    return {
        "schema_version": "1",
        "tier": tier,
        "label": t["label"],
        "detection_definition": {
            "provecode": "At least one S1-S5/B1-B4 finding with severity warning or blocking.",
            "baseline": "At least one finding from the count-based reimplementation.",
        },
        "baseline_rules": baseline_count.RULES,
        "headline": "held_out",
        "held_out": m_held,
        "tuning": metrics(tun),
        "all": metrics(rows),
        "falsification": falsification(m_held),
        "tuning_log": t["tuning_log"],
        "revision_log": t["revision_log"],
        "cases": rows,
        "provenance": provenance(split_lock, tier),
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

    if args.tuning_only:
        for tier, t in TIERS.items():
            cases, _ = load_cases(t["gen"])
            rows = [run_case(c, t["gen"].CASES_DIR) for c in cases if c["split"] == "tuning"]
            m = metrics(rows)
            print(f"Tier {tier}, tuning split:")
            print(json.dumps({k: m[k] for k in ("recall", "recall_count_preserving", "legit_flagged", "by_pattern")},
                             indent=1))
            for r in rows:
                exp = r["kind"] == "weakening"
                if r["provecode"]["flagged"] != exp:
                    print("MISS" if exp else "FALSE+", r["id"], r["provecode"]["rules"], r["provecode"]["gaps"])
        return 0

    built: dict[str, tuple[bytes, list[dict]]] = {}
    for tier, t in TIERS.items():
        cases, split_lock = load_cases(t["gen"])
        rows = [run_case(c, t["gen"].CASES_DIR) for c in cases]
        built[tier] = (dump(build_tier_a(rows, split_lock, tier)), rows)
    if args.check:
        problems = []
        for tier, t in TIERS.items():
            have = t["out"].read_bytes().replace(b"\r\n", b"\n") if t["out"].exists() else b""
            if have != built[tier][0]:
                problems.append(f"artifacts/benchmark/{t['out'].name} differs from a fresh regeneration")
        problems += presets.check() + readme_results.check() + manifest.verify()
        for p in problems:
            print(p, file=sys.stderr)
        print("all tier artifacts reproduced byte-for-byte" if not problems else "regeneration problems")
        return 1 if problems else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for tier, t in TIERS.items():
        t["out"].write_bytes(built[tier][0])
    PERF.write_bytes(dump(perf(built["A"][1])))
    presets.write()
    manifest.write()
    readme_results.write()
    for tier in TIERS:
        h = json.loads(built[tier][0])["held_out"]
        print(f"Tier {tier} held-out: recall PC {h['recall']['provecode']['rate']} vs "
              f"BL {h['recall']['baseline']['rate']}; "
              f"count-preserving PC {h['recall_count_preserving']['provecode']['rate']} vs "
              f"BL {h['recall_count_preserving']['baseline']['rate']}; "
              f"legit blocking {h['legit_blocking']['provecode']['rate']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
