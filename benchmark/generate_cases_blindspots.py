"""Tier A-b case generator: the known blind spots (benchmark/patterns_blindspots.md, ADR 0011).

Same method as benchmark/generate_cases.py and generate_cases_unittest.py, whose site finders and
byte-splicing helpers it reuses. Benchmark code, written before any rule code for ADR 0011;
imports nothing from the engine.

  python -m benchmark.generate_cases_blindspots generate   # fetch, write cache + locks
  python -m benchmark.generate_cases_blindspots verify     # regenerate and compare to the locks
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import shutil
import sys
from dataclasses import dataclass
from typing import Callable

from . import generate_cases as g
from . import generate_cases_unittest as u
from .generate_cases import Edit, SkipSite, splice

BENCH = g.BENCH
SOURCES_LOCK = BENCH / "sources_blindspots.lock.json"
CASES_LOCK = BENCH / "cases_blindspots.lock.json"
SPLIT_LOCK = BENCH / "split_blindspots.lock.json"
CASES_DIR = g.CACHE / "cases_blindspots"
SEED = "20261121-TAB"
GENERATOR_VERSION = "1"


# --------------------------------------------------------------------------- sites
# pytest-style patterns use generate_cases sites (site.func); unittest-style ones use
# generate_cases_unittest sites (site.m). Both have .fi, .node and .line.


def _key(site) -> str:
    return site.func.key if hasattr(site, "func") else site.m.key


def _wrap(site, handler: bytes) -> list[Edit]:
    """Wrap a single-line statement in try: ... <handler lines>, keeping its indentation."""
    fi, n = site.fi, site.node
    ind, eol = fi.indent(n.lineno), fi.eol
    s, e = fi.span(n)
    lines = handler.split(b"\n")
    body = b"".join(eol + ind + line for line in lines)
    return [(s, e, b"try:" + eol + ind + b"    " + fi.seg(n) + body)]


def wrap(handler: bytes) -> Callable:
    return lambda site: _wrap(site, handler)


def apply_x04(site) -> list[Edit]:
    arg = site.node.value.args[0]
    s, e = site.fi.span(arg)
    text = site.fi.seg(arg)
    if isinstance(arg, (ast.IfExp, ast.Lambda, ast.NamedExpr)):
        text = b"(" + text + b")"
    return [(s, e, text + b" or True")]


def _insert_before(site, line: bytes) -> list[Edit]:
    fi, n = site.fi, site.node
    off = g.line_off(fi, n.lineno)
    return [(off, off, fi.indent(n.lineno) + line + fi.eol)]


def apply_y01(site) -> list[Edit]:
    a = site.fi.seg(site.node.test.left)
    if b"\n" in a:
        raise SkipSite("multi-line subject")
    return _insert_before(site, b"assert " + a + b" == " + a)


def apply_y02(site) -> list[Edit]:
    a = site.fi.seg(site.node.value.args[0])
    if b"\n" in a:
        raise SkipSite("multi-line subject")
    return _insert_before(site, b"self.assertEqual(" + a + b", " + a + b")")


@dataclass
class Pattern:
    id: str
    style: str  # "pytest" | "unittest"
    kind: str
    category: str
    cp: bool
    n: int
    sites: Callable
    apply: Callable


PY, UT = "pytest", "unittest"
PATTERNS = [
    Pattern("X01", PY, "weakening", "vacuous-check", True, 8, g.eq_asserts, g.replace_compare(lambda s, a, b: a + b" == " + a)),
    Pattern("X02", UT, "weakening", "vacuous-check", True, 8, u.eq2,
            u.replace_call(lambda s, a, b: b"self.assertEqual(" + a + b", " + a + b")")),
    Pattern("X03", PY, "weakening", "vacuous-check", True, 8, g.eq_asserts,
            g.replace_compare(lambda s, a, b: a + b" == " + b + b" or True")),
    Pattern("X04", UT, "weakening", "vacuous-check", True, 8, u.assert_true_1, apply_x04),
    Pattern("X05", PY, "weakening", "assertion-swallowed", True, 8, g.simple_assert_single_line,
            wrap(b"except AssertionError:\n    pass")),
    Pattern("X06", UT, "weakening", "assertion-swallowed", True, 8, u.single_line_assert,
            wrap(b"except AssertionError:\n    pass")),
    Pattern("X07", PY, "weakening", "assertion-swallowed", True, 8, g.simple_assert_single_line,
            wrap(b"except Exception:\n    pass")),
    Pattern("X08", UT, "weakening", "assertion-swallowed", True, 8, u.single_line_assert, wrap(b"except:\n    pass")),
    Pattern("Y01", PY, "legit", "assert-added", False, 5, g.eq_asserts, apply_y01),
    Pattern("Y02", UT, "legit", "assert-added", False, 5, u.eq2, apply_y02),
    Pattern("Y03", PY, "legit", "reraise-with-context", True, 5, g.simple_assert_single_line,
            wrap(b'except AssertionError as exc:\n    raise AssertionError(f"context: {exc}") from exc')),
    Pattern("Y04", UT, "legit", "reraise", True, 5, u.single_line_assert, wrap(b"except AssertionError:\n    raise")),
    Pattern("Y05", PY, "legit", "try-finally", True, 5, g.simple_assert_single_line, wrap(b"finally:\n    pass")),
    Pattern("Y06", UT, "legit", "message-added", True, 5, u.eq2, u.apply_v08),
    Pattern("Y07", PY, "legit", "operands-swapped", True, 5, g.eq_asserts, g.replace_compare(lambda s, a, b: b + b" == " + a)),
]


# --------------------------------------------------------------------------- generation


def load_sources() -> list[dict]:
    return json.loads(SOURCES_LOCK.read_text(encoding="utf-8"))["sources"]


def _units(fi: g.FileInfo, style: str) -> list:
    return fi.funcs if style == PY else u.methods(fi)


def generate(sources) -> g.Generated:
    per = u.load_files(sources)
    order = [s["repo"] for s in sources]
    shas = {s["repo"]: s["sha"] for s in sources}
    gen = g.Generated()
    for pat in PATTERNS:
        by_repo = {}
        for repo in order:
            sites = [s for fi in per[repo] for unit in _units(fi, pat.style) for s in pat.sites(fi, unit)]
            sites.sort(key=lambda s: (s.fi.path, s.line, _key(s)))
            random.Random(f"{SEED}-{pat.id}-{repo}").shuffle(sites)
            by_repo[repo] = sites
        eligible = sum(len(v) for v in by_repo.values())
        chosen, used, rejected = [], set(), 0
        cur = {r: 0 for r in order}
        while len(chosen) < pat.n and any(cur[r] < len(by_repo[r]) for r in order):
            for repo in order:
                if len(chosen) >= pat.n:
                    break
                while cur[repo] < len(by_repo[repo]):
                    site = by_repo[repo][cur[repo]]
                    cur[repo] += 1
                    if _key(site) in used:
                        continue
                    try:
                        head = splice(site.fi.data, pat.apply(site))
                        ast.parse(head)
                    except (SkipSite, SyntaxError, ValueError):
                        rejected += 1
                        continue
                    if head == site.fi.data:
                        rejected += 1
                        continue
                    used.add(_key(site))
                    chosen.append((site, head))
                    break
        gen.report[pat.id] = {"target": pat.n, "generated": len(chosen), "eligible_sites": eligible,
                              "rejected_sites": rejected, "shortfall": pat.n - len(chosen)}
        for k, (site, head) in enumerate(chosen, start=1):
            cid = f"{pat.id}-{k:02d}"
            base = site.fi.data
            v1 = g.contract_for(cid, _key(site), 1, "Behaviour guarded by the mutated test holds")
            meta = {"id": cid, "pattern": pat.id, "style": pat.style, "kind": pat.kind, "category": pat.category,
                    "expected_weakening": pat.kind == "weakening", "count_preserving": pat.cp,
                    "count_preserving_measured": u.count_checks(site.fi.tree) == u.count_checks(ast.parse(head)),
                    "repo": site.fi.repo, "sha": shas[site.fi.repo], "file": site.fi.path, "test_key": _key(site),
                    "line": site.line, "contract_change": False, "contract_base": v1, "contract_head": v1,
                    "base_sha256": g.sha256(base), "head_sha256": g.sha256(head)}
            gen.cases.append(meta)
            gen.files[cid] = {"base.py": base, "head.py": head}
    return gen


def build_locks(gen):
    cases_lock = {
        "generator_version": GENERATOR_VERSION,
        "patterns_md_sha256": g.sha256((BENCH / "patterns_blindspots.md").read_bytes().replace(b"\r\n", b"\n")),
        "sources_lock_sha256": g.sha256(SOURCES_LOCK.read_bytes().replace(b"\r\n", b"\n")),
        "pattern_report": gen.report, "cases": gen.cases,
    }
    ids = sorted(c["id"] for c in gen.cases)
    shuffled = ids[:]
    random.Random(SEED).shuffle(shuffled)
    held = sorted(shuffled[:round(g.HELD_OUT_FRACTION * len(ids))])
    split = {"seed": SEED, "held_out_fraction": g.HELD_OUT_FRACTION,
             "case_ids_sha256": g.sha256("\n".join(ids).encode()), "n_cases": len(ids),
             "held_out": held, "tuning": sorted(set(ids) - set(held))}
    return cases_lock, split


def write_cache(gen):
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    for child in CASES_DIR.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for meta in gen.cases:
        d = CASES_DIR / meta["id"]
        d.mkdir(parents=True)
        for name, data in gen.files[meta["id"]].items():
            (d / name).write_bytes(data)
        (d / "meta.json").write_bytes(g.canonical(meta))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["generate", "verify"])
    args = ap.parse_args(argv)
    gen = generate(load_sources())
    cases_lock, split = build_locks(gen)
    if args.command == "generate":
        write_cache(gen)
        CASES_LOCK.write_bytes(g.canonical(cases_lock))
        SPLIT_LOCK.write_bytes(g.canonical(split))
        print(f"wrote {len(gen.cases)} cases; held-out {len(split['held_out'])}")
        for pid, r in gen.report.items():
            if r["shortfall"]:
                print(f"  shortfall {pid}: {r}")
        return 0
    ok = all(p.read_bytes().replace(b"\r\n", b"\n") == g.canonical(w)
             for p, w in ((CASES_LOCK, cases_lock), (SPLIT_LOCK, split)))
    if ok:
        write_cache(gen)
        print(f"verified {len(gen.cases)} cases against the locks")
    else:
        print("MISMATCH against cases_blindspots/split_blindspots locks")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
