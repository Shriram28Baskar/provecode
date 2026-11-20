"""Tier A-u case generator: unittest-style tests (benchmark/patterns_unittest.md, ADR 0009).

Same method as benchmark/generate_cases.py, whose byte-splicing helpers it reuses. Benchmark code,
written before the detector change; imports nothing from the engine.

  python -m benchmark.generate_cases_unittest generate   # fetch, write cache + locks
  python -m benchmark.generate_cases_unittest verify     # regenerate and compare to the locks
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import generate_cases as g
from .generate_cases import Edit, SkipSite, splice

BENCH = g.BENCH
SOURCES_LOCK = BENCH / "sources_unittest.lock.json"
CASES_LOCK = BENCH / "cases_unittest.lock.json"
SPLIT_LOCK = BENCH / "split_unittest.lock.json"
CASES_DIR = g.CACHE / "cases_unittest"
SEED = "20261120-TAU"
GENERATOR_VERSION = "1"


# --------------------------------------------------------------------------- parsing


@dataclass
class Method:
    key: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    cls: ast.ClassDef


def _base_name(b: ast.AST) -> str:
    return b.attr if isinstance(b, ast.Attribute) else b.id if isinstance(b, ast.Name) else ""


def is_testcase_class(c: ast.ClassDef) -> bool:
    return c.name.startswith("Test") or any(_base_name(b).endswith("TestCase") for b in c.bases)


def methods(fi: g.FileInfo) -> list[Method]:
    out = []
    for node in fi.tree.body:
        if isinstance(node, ast.ClassDef) and is_testcase_class(node):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    out.append(Method(f"{fi.path}::{node.name}::{sub.name}", sub, node))
    return out


def imports_unittest(fi: g.FileInfo) -> bool:
    return any(isinstance(n, ast.Import) and any(a.name == "unittest" for a in n.names) for n in fi.tree.body)


def self_call(n: ast.AST, names: tuple[str, ...] | None = None) -> bool:
    return (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "self" and (n.func.attr in names if names else n.func.attr.startswith("assert")))


def assert_stmts(fi: g.FileInfo, m: Method, names=None) -> list[ast.Expr]:
    return [s for s in g.walk_own(m.node)
            if isinstance(s, ast.Expr) and self_call(s.value, names) and g.own_line_stmt(fi, s)]


def raises_calls(m: Method, name: str) -> list[ast.Call]:
    return [n for n in g.walk_own(m.node) if self_call(n, (name,))]


def count_checks(tree: ast.Module) -> tuple[int, int]:
    tests = checks = 0
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
            tests += 1
        elif isinstance(n, ast.Assert) or self_call(n):
            checks += 1
    return tests, checks


# --------------------------------------------------------------------------- sites


@dataclass
class Site:
    fi: g.FileInfo
    m: Method
    node: ast.AST | None
    line: int


def S(fi, m, node) -> Site:
    return Site(fi, m, node, getattr(node, "lineno", m.node.lineno))


def eq2(fi, m):  # self.assertEqual(A, B) statements with exactly two positional args
    return [S(fi, m, s) for s in assert_stmts(fi, m, ("assertEqual",))
            if len(s.value.args) == 2 and not s.value.keywords and not any(isinstance(a, ast.Starred) for a in s.value.args)]


def eq_lit(fi, m):
    return [s for s in eq2(fi, m) if g.lit_value(s.node.value.args[1])[0]]


def eq_lit_truthy(fi, m):
    return [s for s in eq_lit(fi, m) if g.lit_value(s.node.value.args[1])[1]]


def eq_num(fi, m):
    return [s for s in eq_lit(fi, m) if isinstance(g.lit_value(s.node.value.args[1])[1], (int, float))]


def eq_single_line(fi, m):
    return [s for s in eq2(fi, m) if s.node.lineno == s.node.end_lineno]


def eq_call_subject(fi, m):
    names = {n.id for n in g.walk_own(m.node) if isinstance(n, ast.Name)}
    if "result" in names:
        return []
    return [s for s in eq_single_line(fi, m) if isinstance(s.node.value.args[0], ast.Call)]


def raises_named(fi, m):
    return [S(fi, m, c) for c in raises_calls(m, "assertRaises")
            if c.args and isinstance(c.args[0], ast.Name) and c.args[0].id not in ("Exception", "BaseException")]


def raises_regex(fi, m):
    return [S(fi, m, c) for c in raises_calls(m, "assertRaisesRegex") if len(c.args) >= 2]


def any_method(fi, m):
    return [S(fi, m, None)] if g.body_first(m.node) is not None else []


def any_method_unittest(fi, m):
    return any_method(fi, m) if imports_unittest(fi) else []


def deletable(fi, m):
    siblings = [s for s in m.cls.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name.startswith("test")]
    return [S(fi, m, None)] if len(siblings) >= 2 else []


def removable_assert(fi, m):
    stmts = assert_stmts(fi, m)
    return [S(fi, m, s) for s in stmts] if len(stmts) >= 2 else []


def single_line_assert(fi, m):
    return [S(fi, m, s) for s in assert_stmts(fi, m) if s.lineno == s.end_lineno]


def renamable(fi, m):
    taken = {s.name for s in m.cls.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if f"{m.node.name}_renamed" in taken or f"{m.node.name}_extra" in taken:
        return []
    return [S(fi, m, None)]


def class_skippable(fi, m):
    # one site per class: only the first test method of the class is eligible
    first = next(s for s in m.cls.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name.startswith("test"))
    return [S(fi, m, m.cls)] if imports_unittest(fi) and first is m.node else []


def assert_true_1(fi, m):
    return [S(fi, m, s) for s in assert_stmts(fi, m, ("assertTrue",)) if len(s.value.args) == 1 and not s.value.keywords]


# --------------------------------------------------------------------------- edits


def _rename_call(site: Site, new: str) -> Edit:
    call = site.node.value if isinstance(site.node, ast.Expr) else site.node
    s, e = site.fi.span(call.func)
    return (s, e, b"self." + new.encode())


def _arg(site: Site, i: int) -> bytes:
    return site.fi.seg(site.node.value.args[i])


def replace_call(build: Callable[[Site, bytes, bytes], bytes]) -> Callable[[Site], list[Edit]]:
    def apply(site: Site) -> list[Edit]:
        s, e = site.fi.span(site.node.value)
        return [(s, e, build(site, _arg(site, 0), _arg(site, 1)))]
    return apply


def u04(site, a, b):
    v = g.lit_value(site.node.value.args[1])[1]
    return b"self.assertIsInstance(" + a + b", " + type(v).__name__.encode() + b")"


def v01(site, a, b):
    v = g.lit_value(site.node.value.args[1])[1]
    new = repr(v + 1) if isinstance(v, (int, float)) else repr(v + "!") if isinstance(v, str) else repr(v + b"!")
    return b"self.assertEqual(" + a + b", " + new.encode() + b")"


def apply_u06(site: Site) -> list[Edit]:
    s, e = site.fi.span(site.node.args[0])
    return [(s, e, b"Exception")]


def apply_u07(site: Site) -> list[Edit]:
    c = site.node
    _, a0 = site.fi.span(c.args[0])
    _, a1 = site.fi.span(c.args[1])
    s, e = site.fi.span(c.func)
    return [(s, e, b"self.assertRaises"), (a0, a1, b"")]


def apply_v07(site: Site) -> list[Edit]:
    c = site.node
    _, a0 = site.fi.span(c.args[0])
    s, e = site.fi.span(c.func)
    return [(s, e, b"self.assertRaisesRegex"), (a0, a0, b', ".*"')]


def insert_decorator_method(text: bytes):
    def apply(site: Site) -> list[Edit]:
        fi, fn = site.fi, site.m.node
        line = g.def_start_line(fn)
        off = g.line_off(fi, line)
        return [(off, off, fi.indent(line) + text + fi.eol)]
    return apply


def apply_u16(site: Site) -> list[Edit]:
    fi, cls = site.fi, site.m.cls
    line = min([cls.lineno] + [d.lineno for d in cls.decorator_list])
    off = g.line_off(fi, line)
    return [(off, off, fi.indent(line) + b'@unittest.skip("flaky")' + fi.eol)]


def insert_first(text: bytes):
    def apply(site: Site) -> list[Edit]:
        fi = site.fi
        first = g.body_first(site.m.node)
        off = g.line_off(fi, first.lineno)
        return [(off, off, fi.indent(first.lineno) + text + fi.eol)]
    return apply


def apply_delete_method(site: Site) -> list[Edit]:
    fi, fn = site.fi, site.m.node
    return [(g.line_off(fi, g.def_start_line(fn)), g.end_of_line_off(fi, fn.end_lineno), b"")]


def apply_delete_stmt(site: Site) -> list[Edit]:
    fi, n = site.fi, site.node
    return [(g.line_off(fi, n.lineno), g.end_of_line_off(fi, n.end_lineno), b"")]


def apply_u14(site: Site) -> list[Edit]:
    fi, n = site.fi, site.node
    ind, eol = fi.indent(n.lineno), fi.eol
    s, e = fi.span(n)
    return [(s, e, b"try:" + eol + ind + b"    " + fi.seg(n) + eol + ind + b"except AssertionError:" + eol + ind + b"    pass")]


def apply_u15(site: Site) -> list[Edit]:
    s, e = site.fi.span(site.node)
    return [(s, e, _arg(site, 0) + b" == " + _arg(site, 1))]


def apply_rename(suffix: str):
    def apply(site: Site) -> list[Edit]:
        fi, fn = site.fi, site.m.node
        line = fi.line_text(fn.lineno)
        needle = b"def " + fn.name.encode() + b"("
        i = line.find(needle)
        if i < 0:
            raise SkipSite("def line not found")
        off = g.line_off(fi, fn.lineno) + i + 4
        return [(off, off + len(fn.name), (fn.name + suffix).encode())]
    return apply


def apply_v04(site: Site) -> list[Edit]:
    fi, fn = site.fi, site.m.node
    start, end = g.line_off(fi, g.def_start_line(fn)), g.end_of_line_off(fi, fn.end_lineno)
    text = fi.data[start:end]
    needle = b"def " + fn.name.encode() + b"("
    if needle not in text:
        raise SkipSite("def line not found")
    copy = text.replace(needle, b"def " + fn.name.encode() + b"_extra(", 1)
    if not copy.endswith(b"\n"):
        copy = fi.eol + copy
    return [(end, end, fi.eol + copy)]


def apply_v03(site: Site) -> list[Edit]:
    fi, n = site.fi, site.node
    off = g.line_off(fi, n.lineno)
    return [(off, off, fi.indent(n.lineno) + b"self.assertIsNotNone(" + _arg(site, 0) + b")" + fi.eol)]


def apply_v05(site: Site) -> list[Edit]:
    fi, n = site.fi, site.node
    off = g.line_off(fi, n.lineno)
    s, e = fi.span(n.value.args[0])
    return [(off, off, fi.indent(n.lineno) + b"result = " + _arg(site, 0) + fi.eol), (s, e, b"result")]


def apply_v06(site: Site) -> list[Edit]:
    fi, n = site.fi, site.node
    off = g.line_off(fi, n.lineno)
    return [(off, off, fi.indent(n.lineno) + b"# check the expected value" + fi.eol)]


def apply_v08(site: Site) -> list[Edit]:
    _, e = site.fi.span(site.node.value.args[1])
    return [(e, e, b', msg="check"')]


def apply_v10(site: Site) -> list[Edit]:
    s, e = site.fi.span(site.node.value)
    return [(s, e, b"self.assertIs(" + _arg(site, 0) + b", True)")]


@dataclass
class Pattern:
    id: str
    kind: str
    category: str
    cp: bool
    n: int
    sites: Callable
    apply: Callable
    contract_change: bool = False


PATTERNS = [
    Pattern("U01", "weakening", "comparison-loosened", True, 6, eq2, lambda s: [_rename_call(s, "assertGreaterEqual")]),
    Pattern("U02", "weakening", "trivial-expected", True, 6, eq_lit, replace_call(lambda s, a, b: b"self.assertIsNotNone(" + a + b")")),
    Pattern("U03", "weakening", "trivial-expected", True, 6, eq_lit_truthy, replace_call(lambda s, a, b: b"self.assertTrue(" + a + b")")),
    Pattern("U04", "weakening", "trivial-expected", True, 6, eq_lit, replace_call(u04)),
    Pattern("U05", "weakening", "comparison-loosened", True, 6, eq_num,
            replace_call(lambda s, a, b: b"self.assertAlmostEqual(" + a + b", " + b + b", delta=1000)")),
    Pattern("U06", "weakening", "exception-widened", True, 6, raises_named, apply_u06),
    Pattern("U07", "weakening", "exception-widened", True, 6, raises_regex, apply_u07),
    Pattern("U08", "weakening", "test-deleted", False, 12, deletable, apply_delete_method),
    Pattern("U09", "weakening", "skip-added", True, 6, any_method_unittest, insert_decorator_method(b'@unittest.skip("flaky")')),
    Pattern("U10", "weakening", "skip-added", True, 6, any_method, insert_first(b'self.skipTest("flaky")')),
    Pattern("U11", "weakening", "assertion-removed", False, 6, removable_assert, apply_delete_stmt),
    Pattern("U12", "weakening", "skip-added", True, 6, any_method, insert_first(b"return")),
    Pattern("U13", "weakening", "other", True, 6, eq2, replace_call(lambda s, a, b: b"self.assertEqual(" + a + b", " + a + b")")),
    Pattern("U14", "weakening", "other", True, 6, single_line_assert, apply_u14),
    Pattern("U15", "weakening", "assertion-removed", False, 6, eq2, apply_u15),
    Pattern("U16", "weakening", "skip-added", True, 6, class_skippable, apply_u16),
    Pattern("V01", "legit", "expected-updated-with-contract", True, 3, eq_lit, replace_call(v01), contract_change=True),
    Pattern("V02", "legit", "rename", True, 3, renamable, apply_rename("_renamed")),
    Pattern("V03", "legit", "assert-added", False, 3, eq2, apply_v03),
    Pattern("V04", "legit", "test-added", False, 3, renamable, apply_v04),
    Pattern("V05", "legit", "extract-local", True, 3, eq_call_subject, apply_v05),
    Pattern("V06", "legit", "comment-added", True, 3, single_line_assert, apply_v06),
    Pattern("V07", "legit", "exception-narrowed", True, 3,
            lambda fi, m: [s for s in raises_named(fi, m) if not s.node.keywords], apply_v07),
    Pattern("V08", "legit", "message-added", True, 3, eq2, apply_v08),
    Pattern("V09", "legit", "operands-swapped", True, 3, eq2,
            replace_call(lambda s, a, b: b"self.assertEqual(" + b + b", " + a + b")")),
    Pattern("V10", "legit", "stricter-check", True, 3, assert_true_1, apply_v10),
]


# --------------------------------------------------------------------------- generation


def load_sources() -> list[dict]:
    return json.loads(SOURCES_LOCK.read_text(encoding="utf-8"))["sources"]


def load_files(sources):
    per = {}
    for src in sources:
        d = g.fetch(src)
        infos = []
        for path in g.test_files(d):
            fi = g.parse_file(src["repo"], path, g.blob(d, path))
            if fi is not None:
                infos.append(fi)
        per[src["repo"]] = infos
    return per


def generate(sources) -> g.Generated:
    per = load_files(sources)
    order = [s["repo"] for s in sources]
    shas = {s["repo"]: s["sha"] for s in sources}
    gen = g.Generated()
    for pat in PATTERNS:
        by_repo = {}
        for repo in order:
            sites = [s for fi in per[repo] for m in methods(fi) for s in pat.sites(fi, m)]
            sites.sort(key=lambda s: (s.fi.path, s.line, s.m.key))
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
                    if site.m.key in used:
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
                    used.add(site.m.key)
                    chosen.append((site, head))
                    break
        gen.report[pat.id] = {"target": pat.n, "generated": len(chosen), "eligible_sites": eligible,
                              "rejected_sites": rejected, "shortfall": pat.n - len(chosen)}
        for k, (site, head) in enumerate(chosen, start=1):
            cid = f"{pat.id}-{k:02d}"
            base = site.fi.data
            measured = count_checks(site.fi.tree) == count_checks(ast.parse(head))
            v1 = g.contract_for(cid, site.m.key, 1, "Behaviour guarded by the mutated test holds")
            v2 = (g.contract_for(cid, site.m.key, 2, "Behaviour guarded by the mutated test holds (expected value updated)")
                  if pat.contract_change else v1)
            meta = {"id": cid, "pattern": pat.id, "kind": pat.kind, "category": pat.category,
                    "expected_weakening": pat.kind == "weakening", "count_preserving": pat.cp,
                    "count_preserving_measured": measured, "repo": site.fi.repo, "sha": shas[site.fi.repo],
                    "file": site.fi.path, "test_key": site.m.key, "line": site.line,
                    "contract_change": pat.contract_change, "contract_base": v1, "contract_head": v2,
                    "base_sha256": g.sha256(base), "head_sha256": g.sha256(head)}
            gen.cases.append(meta)
            gen.files[cid] = {"base.py": base, "head.py": head}
    return gen


def build_locks(gen):
    cases_lock = {
        "generator_version": GENERATOR_VERSION,
        "patterns_md_sha256": g.sha256((BENCH / "patterns_unittest.md").read_bytes().replace(b"\r\n", b"\n")),
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
        print("MISMATCH against cases_unittest/split_unittest locks")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
