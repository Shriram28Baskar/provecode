"""Tier H (ADR 0012): run the frozen harness and test-body cases and build artifacts/benchmark/tierH.json.

Called by benchmark.regenerate. Each case's Changeset is the one changed file, the repository's
unchanged root pytest configuration files at the pinned SHA as context (exactly what `provecode
check` collects), and the base file list. It is written to the case's cache directory as
changeset.json, so the Pyodide parity run reads byte-identical inputs.

Comparisons fixed in benchmark/patterns_harness.md before any rule code:
  - PROVECODE at the Tier H freeze commit (the real code of that commit, extracted from git);
  - the count-based baseline (test files only);
  - "harness file touched": flags any case whose changed file is not a test module.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from provecode import pipeline
from provecode.changeset import PYTEST_CONFIG_NAMES, Changeset, FileChange
from provecode.limits import Limits
from provecode.model import WEAKENING_RULES

from . import baseline_count, generate_cases, generate_cases_harness

ROOT = Path(__file__).resolve().parent.parent
TIER_H = ROOT / "artifacts" / "benchmark" / "tierH.json"
FREEZE_CACHE = generate_cases.CACHE / "engine_freeze"
CORE = ("__init__.py", "model.py", "limits.py", "analyzer.py", "contract.py", "decide.py", "report.py", "pipeline.py")
LABEL = ("Injected harness and test-body weakenings on real repositories (experimental, ADR 0012). The patterns "
         "mirror the rules' stated targets, so this shows the rules work as specified, not real-world recall.")

# Rule development rounds, on the tuning split only (ADR 0012). Held-out was not run before these.
TUNING_LOG: list[dict] = [
    {
        "round": 1,
        "rule": "H6",
        "change": "A `# pragma: no cover` on `if <alias>.TYPE_CHECKING:` (e.g. `import typing as t`) is trivial, like "
                  "`if typing.TYPE_CHECKING:`; the trivial-line pattern accepted only the `typing.` spelling.",
        "evidence": ["NV02-04"],
        "split_used": "tuning",
    },
    {
        "round": 1,
        "rule": "H7",
        "change": "'Pinned down' means the head's highest allowed major version is lower than the base's (an "
                  "unbounded base counts as unbounded), not only lower than the base's minimum: `pytest>=6.0` -> "
                  "`pytest<7` caps pytest at 6.x where the base allowed the latest release.",
        "evidence": ["HD02-02"],
        "split_used": "tuning",
    },
    {
        "round": 1,
        "rule": "H3",
        "change": "A module named like the package under test (or a test dependency) added inside a test package "
                  "(a tests/ directory with __init__.py) does not shadow under pytest's import modes, so it is not "
                  "blocked; it is now reported as an advisory warning, because it shadows if that directory reaches "
                  "sys.path. The pattern labels these cases weakenings; whether that label holds is for the owner.",
        "evidence": ["HS02-02", "HS02-03"],
        "split_used": "tuning",
    },
]


# Changes made AFTER the held-out results were first seen, with their measured effect (none came from Tier H data).
REVISION_LOG: list[dict] = [
    {
        "date": "2026-11-22",
        "source": "review of the real-history scan (docs/research/data/real_history_scan_v2.json) and of real "
                  "workflows, not Tier H data",
        "changes": [
            "H5: a tox/nox call counts as a test command unless every selected env or session is a non-test one "
            "(lint, typing, docs, docset, build, ...); `docs-doctests` and other names containing 'test' still count",
            "H4: with the repository's file list, testpaths/python_files/norecursedirs changes are reported only "
            "when pytest would stop collecting a test file it collected before, and those files are named",
            "H6: regex spellings of an ellipsis in exclude patterns, and omitting __main__.py or version files, "
            "are boilerplate",
            "analyzed source is parsed with Python's warnings silenced (they describe the input, not PROVECODE)",
        ],
        "effect": "8 case results changed, all of them workflow weakening cases whose edited step runs "
                  "`tox -e docs`, `tox run -e typing`, `tox -e lint` or `tox run -e docset` (HW01-03, HW01-06, "
                  "HW02-06, HW03-03, HW03-06, HW04-02, HW04-03, HW04-06; 3 of them held-out). They are no longer "
                  "flagged. The frozen pattern definition treated any tox call as a test command; under the corrected "
                  "definition these are not test steps, so their 'weakening' labels are disputed and listed for the "
                  "owner to relabel. Held-out recall against the frozen labels: 47 -> 44 of 47 (44 of 44 if the 3 "
                  "disputed labels are set aside). No legitimate case changed; no other tier changed.",
        "disputed_labels": ["HW01-03", "HW01-06", "HW02-06", "HW03-03", "HW03-06", "HW04-02", "HW04-03", "HW04-06"],
    },
]


def _git_lines(args: list[str]) -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    return [x for x in out.stdout.splitlines() if x.strip()] if out.returncode == 0 else []


def _first_added(path: str) -> dict | None:
    lines = _git_lines(["log", "--diff-filter=A", "--format=%H %cI", "--", path])
    if not lines:
        return None
    sha, when = lines[-1].split(" ", 1)
    return {"commit": sha, "committed_at": when}


def commit_order() -> dict:
    split = _first_added("benchmark/split_harness.lock.json")
    rule = None
    if split:
        lines = _git_lines(["log", "--format=%H %cI", "--ancestry-path", f"{split['commit']}..HEAD", "--",
                            "engine/src/provecode/harness.py"])
        if lines:
            sha, when = lines[-1].split(" ", 1)
            rule = {"commit": sha, "committed_at": when}
    return {"split_frozen": split, "patterns_frozen": _first_added("benchmark/patterns_harness.md"),
            "first_rule_code": rule}


def freeze_engine():
    """(pipeline, WEAKENING_RULES) of the engine exactly as committed at the Tier H freeze."""
    split = _first_added("benchmark/split_harness.lock.json")
    if split is None:
        return None
    sha = split["commit"]
    pkg = FREEZE_CACHE / sha[:12] / "provecode_freeze"
    if not (pkg / "pipeline.py").exists():
        pkg.mkdir(parents=True, exist_ok=True)
        for name in CORE:
            out = subprocess.run(["git", "-C", str(ROOT), "show", f"{sha}:engine/src/provecode/{name}"],
                                 capture_output=True, check=True)
            (pkg / name).write_bytes(out.stdout)
    if str(pkg.parent) not in sys.path:
        sys.path.insert(0, str(pkg.parent))
    pl = importlib.import_module("provecode_freeze.pipeline")
    model = importlib.import_module("provecode_freeze.model")
    return pl, model.WEAKENING_RULES, sha


def _is_test_module(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py") or name == "tests.py")


def changeset_doc(meta: dict) -> dict:
    d = generate_cases_harness.CASES_DIR / meta["id"]
    base = (d / "base").read_bytes().decode("utf-8") if (d / "base").exists() else None
    head = (d / "head").read_bytes().decode("utf-8")
    paths = [p for p in (d / "base_paths.txt").read_text(encoding="utf-8").splitlines() if p]
    files = [{"path": meta["file"], "status": meta["status"], "base": base, "head": head}]
    src = generate_cases.repo_dir(meta["repo"])
    for name in PYTEST_CONFIG_NAMES:
        if name != meta["file"] and name in paths:
            text = generate_cases.blob(src, name).decode("utf-8")
            files.append({"path": name, "status": "U", "base": text, "head": text})
    doc = {"files": files, "base_paths": paths}
    (d / "changeset.json").write_bytes(generate_cases.canonical(doc))
    return doc


def to_changeset(doc: dict) -> Changeset:
    return Changeset([FileChange(f["path"], f["status"], f["base"], f["head"]) for f in doc["files"]],
                     base_paths=tuple(doc["base_paths"]))


def run_case(meta: dict, approve, freeze) -> dict:
    doc = changeset_doc(meta)
    rep = pipeline.run(to_changeset(doc), approve(meta["contract_head"]), approve(meta["contract_base"]),
                       mode="static", limits=Limits.cli())
    weak = [f for f in rep["findings"] if f["rule"] in WEAKENING_RULES and f["severity"] != "info"]
    changed = doc["files"][0]
    tested = _is_test_module(meta["file"])
    bl = baseline_count.detect(changed["base"], changed["head"]) if tested and changed["base"] is not None else []
    old = {"flagged": False, "rules": []}
    if freeze is not None and tested:
        pl, old_rules, _ = freeze
        r = pl.run([(meta["file"], changed["base"], changed["head"])], approve(meta["contract_head"]),
                   approve(meta["contract_base"]), mode="static", limits=Limits.cli())
        ow = [f for f in r["findings"] if f["rule"] in old_rules and f["severity"] != "info"]
        old = {"flagged": bool(ow), "rules": sorted({f["rule"] for f in r["findings"]})}
    return {
        "id": meta["id"], "split": meta["split"], "pattern": meta["pattern"], "family": meta["family"],
        "category": meta["category"], "kind": meta["kind"], "count_preserving": meta["count_preserving"],
        "repo": meta["repo"],
        "provecode": {
            "flagged": bool(weak),
            "blocking": any(f["severity"] == "blocking" for f in rep["findings"]),
            "rules": sorted({f["rule"] for f in rep["findings"]}),
            "outcome": rep["outcome"],
            "gaps": sorted({g["reason"] for g in rep["gaps"]} - {"requires_execution", "no_check"}),
        },
        "baseline": {"flagged": bool(bl), "rules": sorted({f.rule for f in bl})},
        "harness_touched": {"flagged": not tested},
        "engine_at_freeze": old,
    }


def metrics(rows: list[dict], wilson, mcnemar) -> dict:
    weak = [r for r in rows if r["kind"] == "weakening"]
    legit = [r for r in rows if r["kind"] == "legit"]
    who = ("provecode", "baseline", "harness_touched", "engine_at_freeze")

    def rate(rs, w):
        return wilson(sum(r[w]["flagged"] for r in rs), len(rs))

    def paired(rs, other):
        a = sum(r["provecode"]["flagged"] and not r[other]["flagged"] for r in rs)
        b = sum(r[other]["flagged"] and not r["provecode"]["flagged"] for r in rs)
        return {"only_provecode": a, f"only_{other}": b, "mcnemar_exact_p": mcnemar(a, b)}

    by_family: dict[str, list] = defaultdict(list)
    by_pat: dict[str, list] = defaultdict(list)
    for r in rows:
        by_pat[r["pattern"]].append(r)
        by_family[r["family"]].append(r)
    return {
        "n_cases": len(rows), "n_weakening": len(weak), "n_legit": len(legit),
        "recall": {w: rate(weak, w) for w in who},
        "legit_flagged": {w: rate(legit, w) for w in who},
        "legit_blocking": {"provecode": wilson(sum(r["provecode"]["blocking"] for r in legit), len(legit))},
        "paired_recall": {o: paired(weak, o) for o in who[1:]},
        "paired_legit": {o: paired(legit, o) for o in who[1:]},
        "by_family": {fam: {"recall": {w: rate([r for r in rs if r["kind"] == "weakening"], w) for w in who},
                            "legit_flagged": {w: rate([r for r in rs if r["kind"] == "legit"], w) for w in who}}
                      for fam, rs in sorted(by_family.items())},
        "by_pattern": {k: {"kind": v[0]["kind"], "family": v[0]["family"], "category": v[0]["category"], "n": len(v),
                           **{f"{w}_flagged": sum(r[w]["flagged"] for r in v) for w in who}}
                       for k, v in sorted(by_pat.items())},
        "outcomes": dict(sorted(Counter(r["provecode"]["outcome"] for r in rows).items())),
        "analysis_gaps": dict(sorted(Counter(g for r in rows for g in r["provecode"]["gaps"]).items())),
    }


def sha256_paths(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.relative_to(ROOT).as_posix().encode() + b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def load_cases() -> tuple[list[dict], dict]:
    gm = generate_cases_harness
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


def build(approve, wilson, mcnemar, engine_version: str, python: str, tuning_only: bool = False) -> dict:
    cases, split_lock = load_cases()
    freeze = freeze_engine()
    if tuning_only:
        cases = [c for c in cases if c["split"] == "tuning"]
    rows = [run_case(c, approve, freeze) for c in cases]
    held = [r for r in rows if r["split"] == "held_out"]
    tun = [r for r in rows if r["split"] == "tuning"]
    gm = generate_cases_harness
    eng = sorted((ROOT / "engine" / "src" / "provecode").glob("*.py"))
    bench = [ROOT / "benchmark" / n for n in ("baseline_count.py", "generate_cases.py", "generate_cases_harness.py",
                                              "tier_h.py", "regenerate.py")]
    return {
        "schema_version": "1",
        "tier": "H",
        "label": LABEL,
        "detection_definition": {
            "provecode": "At least one weakening finding (S1-S5, B1-B4, H1-H7, T1-T5) with severity warning or "
                         "blocking, on the case's Changeset, with the case contract.",
            "baseline": "At least one finding from the count-based reimplementation (test files only).",
            "harness_touched": "The changed file is not a test module (test_*.py, *_test.py, tests.py).",
            "engine_at_freeze": "PROVECODE's code at the Tier H freeze commit, on the test-file tuple API it had.",
        },
        "headline": "held_out",
        "held_out": metrics(held, wilson, mcnemar) if held else None,
        "tuning": metrics(tun, wilson, mcnemar),
        "all": metrics(rows, wilson, mcnemar),
        "tuning_log": TUNING_LOG,
        "revision_log": REVISION_LOG,
        "cases": rows,
        "provenance": {
            "engine_version": engine_version,
            "engine_source_sha256": sha256_paths(eng),
            "benchmark_code_sha256": sha256_paths(bench),
            "cases_lock_sha256": hashlib.sha256(gm.CASES_LOCK.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
            "split_lock_sha256": hashlib.sha256(gm.SPLIT_LOCK.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
            "case_ids_sha256": split_lock["case_ids_sha256"],
            "sources": [{"repo": s["repo"], "sha": s["sha"], "licenses": [x["spdx"] for x in s["licenses"]]}
                        for s in gm.load_sources()],
            "engine_at_freeze_commit": freeze[2] if freeze else None,
            "python": python,
            "commit_order": commit_order(),
            "command": "python -m benchmark.regenerate",
        },
    }


def tuning_report() -> int:
    """Tuning split only: misses and false flags, for rule development (ADR 0012). Never touches held-out."""
    from .regenerate import _approved, mcnemar_exact, wilson

    t = build(_approved, wilson, mcnemar_exact, "dev", "dev", tuning_only=True)
    m = t["tuning"]
    print(json.dumps({"recall": m["recall"], "legit_flagged": m["legit_flagged"]}, indent=1))
    for r in t["cases"]:
        exp = r["kind"] == "weakening"
        if r["provecode"]["flagged"] != exp:
            print("MISS" if exp else "FALSE+", r["id"], r["provecode"]["rules"], r["provecode"]["gaps"])
    return 0


if __name__ == "__main__":
    sys.exit(tuning_report())
