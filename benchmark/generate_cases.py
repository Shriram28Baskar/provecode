"""Tier A case generator (benchmark/patterns.md, docs/03-build-spec.md section 8).

Fetches the pinned source repos, applies each frozen pattern to real test
functions, and writes (base, head, meta) triples to benchmark/.cache/cases/.
Third-party code never leaves benchmark/.cache/ (ADR 0005); only hashes and
metadata are committed, in benchmark/cases.lock.json and split.lock.json.

This file is benchmark code, written before the detector. It imports nothing
from the engine. Edits are byte-range splices on the original file so that
output does not depend on the Python version's ast.unparse.

Usage (from the repo root):
  python -m benchmark.generate_cases generate   # fetch if needed, write cache + locks
  python -m benchmark.generate_cases verify     # regenerate and compare to the locks
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "benchmark"
CACHE = BENCH / ".cache"
SRC_DIR = CACHE / "src"
CASES_DIR = CACHE / "cases"
SOURCES_LOCK = BENCH / "sources.lock.json"
CASES_LOCK = BENCH / "cases.lock.json"
SPLIT_LOCK = BENCH / "split.lock.json"

GENERATOR_VERSION = "1"
SPLIT_SEED = "20261120-TA"
HELD_OUT_FRACTION = 0.3

LICENSE_MARKERS = {
    "MIT": "Permission is hereby granted, free of charge",
    "BSD-3-Clause": "Redistribution and use in source and binary forms",
    "Apache-2.0": "Apache License",
}


# --------------------------------------------------------------------------- sources


def load_sources() -> list[dict]:
    return json.loads(SOURCES_LOCK.read_text(encoding="utf-8"))["sources"]


def repo_dir(repo: str) -> Path:
    return SRC_DIR / repo.replace("/", "__")


def _git(args: list[str], cwd: Path) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {out.stderr.strip()}")
    return out.stdout.strip()


def fetch(source: dict) -> Path:
    d = repo_dir(source["repo"])
    if not (d / ".git").exists():
        d.mkdir(parents=True, exist_ok=True)
        _git(["init", "-q"], d)
        _git(["fetch", "-q", "--depth", "1", f"https://github.com/{source['repo']}.git", source["sha"]], d)
        _git(["-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"], d)
    head = _git(["rev-parse", "HEAD"], d)
    if head != source["sha"]:
        raise RuntimeError(f"{source['repo']}: HEAD {head} != pinned {source['sha']}")
    status = _git(["status", "--porcelain"], d)
    if status:
        raise RuntimeError(f"{source['repo']}: working tree is not clean")
    for lic in source["licenses"]:
        text = (d / lic["file"]).read_text(encoding="utf-8", errors="replace")
        if LICENSE_MARKERS[lic["spdx"]] not in text:
            raise RuntimeError(f"{source['repo']}: {lic['file']} lacks the {lic['spdx']} marker text")
    return d


def blob(d: Path, path: str) -> bytes:
    """Exact bytes of path at HEAD, as stored by git (no line-ending conversion)."""
    out = subprocess.run(["git", "cat-file", "blob", f"HEAD:{path}"], cwd=d, capture_output=True)
    if out.returncode != 0:
        raise RuntimeError(f"git cat-file HEAD:{path} failed in {d}: {out.stderr.decode('utf-8', 'replace')}")
    return out.stdout


def test_files(d: Path) -> list[str]:
    files = _git(["ls-files", "*.py"], d).splitlines()
    out = []
    for f in files:
        name = f.rsplit("/", 1)[-1]
        if name.startswith("test_") or name.endswith("_test.py"):
            out.append(f)
    return sorted(out)


# --------------------------------------------------------------------------- parsing helpers


@dataclass
class Func:
    key: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    cls: str | None


@dataclass
class FileInfo:
    repo: str
    path: str
    data: bytes
    tree: ast.Module
    line_starts: list[int]
    funcs: list[Func]
    imports_pytest: bool

    def off(self, lineno: int, col: int) -> int:
        return self.line_starts[lineno - 1] + col

    def span(self, node: ast.AST) -> tuple[int, int]:
        return self.off(node.lineno, node.col_offset), self.off(node.end_lineno, node.end_col_offset)

    def seg(self, node: ast.AST) -> bytes:
        s, e = self.span(node)
        return self.data[s:e]

    def line_text(self, lineno: int) -> bytes:
        start = self.line_starts[lineno - 1]
        end = self.line_starts[lineno] if lineno < len(self.line_starts) else len(self.data)
        return self.data[start:end]

    def indent(self, lineno: int) -> bytes:
        line = self.line_text(lineno)
        return line[: len(line) - len(line.lstrip(b" \t"))]

    @property
    def eol(self) -> bytes:
        return b"\r\n" if b"\r\n" in self.data else b"\n"


def _line_starts(data: bytes) -> list[int]:
    starts = [0]
    for i, b in enumerate(data):
        if b == 0x0A:
            starts.append(i + 1)
    return starts


def parse_file(repo: str, path: str, data: bytes) -> FileInfo | None:
    try:
        tree = ast.parse(data)
    except (SyntaxError, ValueError):
        return None
    funcs: list[Func] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            funcs.append(Func(f"{path}::{node.name}", node, None))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    funcs.append(Func(f"{path}::{node.name}::{sub.name}", sub, node.name))
    imports_pytest = any(
        isinstance(n, ast.Import) and any(a.name == "pytest" for a in n.names) for n in tree.body
    )
    return FileInfo(repo, path, data, tree, _line_starts(data), funcs, imports_pytest)


def walk_own(func: ast.AST) -> Iterator[ast.AST]:
    """Walk a function body without entering nested functions, lambdas or classes."""
    stack = list(reversed(getattr(func, "body", [])))
    while stack:
        node = stack.pop()
        yield node
        for child in reversed(list(ast.iter_child_nodes(node))):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            stack.append(child)


def is_eq_assert(n: ast.AST) -> bool:
    return (
        isinstance(n, ast.Assert)
        and isinstance(n.test, ast.Compare)
        and len(n.test.ops) == 1
        and isinstance(n.test.ops[0], ast.Eq)
    )


def lit_value(n: ast.AST):
    """Return (True, value) when n is a usable expected literal (not None/bool)."""
    if isinstance(n, ast.Constant) and n.value is not None and not isinstance(n.value, bool):
        if isinstance(n.value, (int, float, str, bytes)):
            return True, n.value
    return False, None


def is_raises(n: ast.AST) -> bool:
    return (
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "raises"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "pytest"
    )


def parametrize_decorators(func: ast.AST) -> list[ast.Call]:
    out = []
    for d in getattr(func, "decorator_list", []):
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "parametrize":
            if len(d.args) >= 2 and isinstance(d.args[1], (ast.List, ast.Tuple)):
                out.append(d)
    return out


def def_start_line(func: ast.AST) -> int:
    lines = [func.lineno] + [d.lineno for d in func.decorator_list]
    return min(lines)


def body_first(func: ast.AST) -> ast.stmt | None:
    """First statement after an optional docstring, only if it starts on its own line."""
    body = func.body
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
        and len(body) > 1
    ):
        first = body[1]
    if first.lineno <= func.lineno:
        return None
    return first


def own_line_stmt(fi: FileInfo, stmt: ast.stmt) -> bool:
    """Statement begins its line after indentation and nothing but a comment follows it."""
    first = fi.line_text(stmt.lineno)
    s, e = fi.span(stmt)
    if first[: stmt.col_offset].strip(b" \t"):
        return False
    tail_line_end = fi.line_starts[stmt.end_lineno] if stmt.end_lineno < len(fi.line_starts) else len(fi.data)
    tail = fi.data[e:tail_line_end].strip()
    return tail == b"" or tail.startswith(b"#")


def count_static(tree: ast.Module) -> tuple[int, int]:
    tests = asserts = 0
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
            tests += 1
        elif isinstance(n, ast.Assert):
            asserts += 1
    return tests, asserts


# --------------------------------------------------------------------------- edits

Edit = tuple[int, int, bytes]  # [start, end) replaced by bytes


def splice(data: bytes, edits: list[Edit]) -> bytes:
    out = data
    for s, e, new in sorted(edits, key=lambda x: x[0], reverse=True):
        out = out[:s] + new + out[e:]
    return out


def line_off(fi: FileInfo, lineno: int) -> int:
    return fi.line_starts[lineno - 1]


def end_of_line_off(fi: FileInfo, lineno: int) -> int:
    return fi.line_starts[lineno] if lineno < len(fi.line_starts) else len(fi.data)


@dataclass
class Site:
    fi: FileInfo
    func: Func
    node: ast.AST | None  # the specific site node (assert, call, decorator) or None
    line: int


@dataclass
class Pattern:
    id: str
    kind: str  # "weakening" | "legit"
    category: str
    cp: bool
    n: int
    sites: Callable[[FileInfo, Func], list[Site]]
    apply: Callable[[Site], list[Edit]]
    contract_change: bool = False


def S(fi: FileInfo, f: Func, node: ast.AST | None) -> Site:
    return Site(fi, f, node, getattr(node, "lineno", f.node.lineno))


# site finders -----------------------------------------------------------------


def eq_asserts(fi: FileInfo, f: Func) -> list[Site]:
    return [S(fi, f, n) for n in walk_own(f.node) if is_eq_assert(n) and own_line_stmt(fi, n)]


def eq_lit_asserts(fi: FileInfo, f: Func) -> list[Site]:
    return [s for s in eq_asserts(fi, f) if lit_value(s.node.test.comparators[0])[0]]


def eq_lit_truthy(fi: FileInfo, f: Func) -> list[Site]:
    return [s for s in eq_lit_asserts(fi, f) if lit_value(s.node.test.comparators[0])[1]]


def eq_num_asserts_pytest(fi: FileInfo, f: Func) -> list[Site]:
    if not fi.imports_pytest:
        return []
    return [
        s
        for s in eq_lit_asserts(fi, f)
        if isinstance(lit_value(s.node.test.comparators[0])[1], (int, float))
    ]


def eq_single_line(fi: FileInfo, f: Func) -> list[Site]:
    return [s for s in eq_asserts(fi, f) if s.node.lineno == s.node.end_lineno]


def eq_no_msg(fi: FileInfo, f: Func) -> list[Site]:
    return [s for s in eq_asserts(fi, f) if s.node.msg is None]


def eq_call_subject(fi: FileInfo, f: Func) -> list[Site]:
    names = {n.id for n in walk_own(f.node) if isinstance(n, ast.Name)}
    if "result" in names:
        return []
    return [s for s in eq_single_line(fi, f) if isinstance(s.node.test.left, ast.Call)]


def raises_named(fi: FileInfo, f: Func) -> list[Site]:
    out = []
    for n in walk_own(f.node):
        if is_raises(n) and n.args and isinstance(n.args[0], ast.Name):
            if n.args[0].id not in ("Exception", "BaseException"):
                out.append(S(fi, f, n))
    return out


def raises_with_match(fi: FileInfo, f: Func) -> list[Site]:
    out = []
    for n in walk_own(f.node):
        if is_raises(n) and n.args and any(k.arg == "match" for k in n.keywords):
            out.append(S(fi, f, n))
    return out


def raises_without_match(fi: FileInfo, f: Func) -> list[Site]:
    return [
        s
        for s in raises_named(fi, f)
        if not any(k.arg == "match" for k in s.node.keywords) and len(s.node.args) == 1
    ]


def param_min(k: int) -> Callable[[FileInfo, Func], list[Site]]:
    def finder(fi: FileInfo, f: Func) -> list[Site]:
        return [S(fi, f, d) for d in parametrize_decorators(f.node) if len(d.args[1].elts) >= k]

    return finder


def any_func(fi: FileInfo, f: Func) -> list[Site]:
    return [S(fi, f, None)] if body_first(f.node) is not None else []


def any_func_pytest(fi: FileInfo, f: Func) -> list[Site]:
    return any_func(fi, f) if fi.imports_pytest else []


def deletable_func(fi: FileInfo, f: Func) -> list[Site]:
    return [S(fi, f, None)] if len(fi.funcs) > 1 else []


def removable_assert(fi: FileInfo, f: Func) -> list[Site]:
    asserts = [n for n in walk_own(f.node) if isinstance(n, ast.Assert)]
    if len(asserts) < 2:
        return []
    return [S(fi, f, n) for n in asserts if own_line_stmt(fi, n)]


def simple_assert_single_line(fi: FileInfo, f: Func) -> list[Site]:
    return [
        S(fi, f, n)
        for n in walk_own(f.node)
        if isinstance(n, ast.Assert) and own_line_stmt(fi, n) and n.lineno == n.end_lineno
    ]


def renamable_func(fi: FileInfo, f: Func) -> list[Site]:
    taken = {x.node.name for x in fi.funcs}
    if f"{f.node.name}_renamed" in taken or f"{f.node.name}_extra" in taken:
        return []
    return [S(fi, f, None)]


# appliers ---------------------------------------------------------------------


def _compare_parts(site: Site) -> tuple[ast.Compare, bytes, bytes]:
    cmp_ = site.node.test
    return cmp_, site.fi.seg(cmp_.left), site.fi.seg(cmp_.comparators[0])


def replace_compare(build: Callable[[Site, bytes, bytes], bytes]) -> Callable[[Site], list[Edit]]:
    def apply(site: Site) -> list[Edit]:
        cmp_, a, b = _compare_parts(site)
        s, e = site.fi.span(cmp_)
        return [(s, e, build(site, a, b))]

    return apply


def w03(site: Site, a: bytes, b: bytes) -> bytes:
    v = lit_value(site.node.test.comparators[0])[1]
    if isinstance(v, (int, float)) and v >= 0:
        return a + b" >= 0"
    return a + b" is not None"


def w05(site: Site, a: bytes, b: bytes) -> bytes:
    v = lit_value(site.node.test.comparators[0])[1]
    return b"isinstance(" + a + b", " + type(v).__name__.encode() + b")"


def l01(site: Site, a: bytes, b: bytes) -> bytes:
    v = lit_value(site.node.test.comparators[0])[1]
    if isinstance(v, (int, float)):
        new = repr(v + 1)
    elif isinstance(v, str):
        new = repr(v + "!")
    else:
        new = repr(v + b"!")
    return a + b" == " + new.encode()


def apply_w08(site: Site) -> list[Edit]:
    s, e = site.fi.span(site.node.args[0])
    return [(s, e, b"Exception")]


def apply_w21(site: Site) -> list[Edit]:
    s, e = site.fi.span(site.node.args[0])
    return [(s, e, b"(" + site.fi.seg(site.node.args[0]) + b", Exception)")]


def apply_w09(site: Site) -> list[Edit]:
    call = site.node
    items = sorted([*call.args, *call.keywords], key=lambda x: (x.lineno, x.col_offset))
    idx = next(i for i, x in enumerate(items) if isinstance(x, ast.keyword) and x.arg == "match")
    if idx == 0:
        raise SkipSite("match is the first argument")
    _, prev_end = site.fi.span(items[idx - 1])
    _, kw_end = site.fi.span(items[idx])
    return [(prev_end, kw_end, b"")]


def apply_l08(site: Site) -> list[Edit]:
    _, e = site.fi.span(site.node.args[0])
    return [(e, e, b', match=".*"')]


def apply_w10(site: Site) -> list[Edit]:
    elts = site.node.args[1].elts
    _, prev_end = site.fi.span(elts[-2])
    _, last_end = site.fi.span(elts[-1])
    return [(prev_end, last_end, b"")]


def apply_l04(site: Site) -> list[Edit]:
    elts = site.node.args[1].elts
    _, last_end = site.fi.span(elts[-1])
    return [(last_end, last_end, b", " + site.fi.seg(elts[-1]))]


class SkipSite(Exception):
    pass


def insert_decorator(text: bytes) -> Callable[[Site], list[Edit]]:
    def apply(site: Site) -> list[Edit]:
        fi, fn = site.fi, site.func.node
        line = def_start_line(fn)
        off = line_off(fi, line)
        return [(off, off, fi.indent(line) + text + fi.eol)]

    return apply


def insert_first(text: bytes) -> Callable[[Site], list[Edit]]:
    def apply(site: Site) -> list[Edit]:
        fi = site.fi
        first = body_first(site.func.node)
        off = line_off(fi, first.lineno)
        return [(off, off, fi.indent(first.lineno) + text + fi.eol)]

    return apply


def apply_w15(site: Site) -> list[Edit]:
    fi = site.fi
    first = body_first(site.func.node)
    end = site.func.node.end_lineno
    ind = fi.indent(first.lineno)
    start = line_off(fi, first.lineno)
    # One edit for the first line: header plus that line's extra indent (two
    # zero-width edits at the same offset would apply in the wrong order).
    edits: list[Edit] = [(start, start, ind + b"if False:" + fi.eol + b"    ")]
    for ln in range(first.lineno + 1, end + 1):
        if fi.line_text(ln).strip():
            off = line_off(fi, ln)
            edits.append((off, off, b"    "))
    return edits


def apply_delete_func(site: Site) -> list[Edit]:
    fi, fn = site.fi, site.func.node
    return [(line_off(fi, def_start_line(fn)), end_of_line_off(fi, fn.end_lineno), b"")]


def apply_delete_stmt(site: Site) -> list[Edit]:
    fi, n = site.fi, site.node
    return [(line_off(fi, n.lineno), end_of_line_off(fi, n.end_lineno), b"")]


def apply_w18(site: Site) -> list[Edit]:
    fi, n = site.fi, site.node
    ind = fi.indent(n.lineno)
    stmt = fi.seg(n)
    s, e = fi.span(n)
    eol = fi.eol
    new = b"try:" + eol + ind + b"    " + stmt + eol + ind + b"except AssertionError:" + eol + ind + b"    pass"
    return [(s, e, new)]


def apply_w22(site: Site) -> list[Edit]:
    s, e = site.fi.span(site.node)
    return [(s, e, site.fi.seg(site.node.test))]


def apply_l02(site: Site) -> list[Edit]:
    fi, fn = site.fi, site.func.node
    line = fi.line_text(fn.lineno)
    needle = b"def " + fn.name.encode() + b"("
    i = line.find(needle)
    if i < 0:
        raise SkipSite("def line not found")
    off = line_off(fi, fn.lineno) + i + 4
    return [(off, off + len(fn.name), (fn.name + "_renamed").encode())]


def apply_l05(site: Site) -> list[Edit]:
    fi, fn = site.fi, site.func.node
    start, end = line_off(fi, def_start_line(fn)), end_of_line_off(fi, fn.end_lineno)
    text = fi.data[start:end]
    needle = b"def " + fn.name.encode() + b"("
    if needle not in text:
        raise SkipSite("def line not found")
    copy = text.replace(needle, b"def " + fn.name.encode() + b"_extra(", 1)
    if not copy.endswith(b"\n"):
        copy = fi.eol + copy
    return [(end, end, fi.eol + copy)]


def apply_l03(site: Site) -> list[Edit]:
    fi, n = site.fi, site.node
    off = line_off(fi, n.lineno)
    return [(off, off, fi.indent(n.lineno) + b"assert " + fi.seg(n.test.left) + b" is not None" + fi.eol)]


def apply_l06(site: Site) -> list[Edit]:
    fi, n = site.fi, site.node
    off = line_off(fi, n.lineno)
    s, e = fi.span(n.test.left)
    return [
        (off, off, fi.indent(n.lineno) + b"result = " + fi.seg(n.test.left) + fi.eol),
        (s, e, b"result"),
    ]


def apply_l07(site: Site) -> list[Edit]:
    fi, n = site.fi, site.node
    off = line_off(fi, n.lineno)
    return [(off, off, fi.indent(n.lineno) + b"# check the expected value" + fi.eol)]


def apply_l09(site: Site) -> list[Edit]:
    _, e = site.fi.span(site.node)
    return [(e, e, b', "check"')]


PATTERNS: list[Pattern] = [
    Pattern("W01", "weakening", "comparison-loosened", True, 6, eq_asserts, replace_compare(lambda s, a, b: a + b" >= " + b)),
    Pattern("W02", "weakening", "comparison-loosened", True, 6, eq_asserts, replace_compare(lambda s, a, b: a + b" != None")),
    Pattern("W03", "weakening", "trivial-expected", True, 6, eq_lit_asserts, replace_compare(w03)),
    Pattern("W04", "weakening", "trivial-expected", True, 6, eq_lit_truthy, replace_compare(lambda s, a, b: a)),
    Pattern("W05", "weakening", "trivial-expected", True, 6, eq_lit_asserts, replace_compare(w05)),
    Pattern("W06", "weakening", "comparison-loosened", True, 6, eq_num_asserts_pytest,
            replace_compare(lambda s, a, b: a + b" == pytest.approx(" + b + b", rel=0.5)")),
    Pattern("W08", "weakening", "exception-widened", True, 6, raises_named, apply_w08),
    Pattern("W09", "weakening", "exception-widened", True, 6, raises_with_match, apply_w09),
    Pattern("W10", "weakening", "parametrize-reduced", True, 12, param_min(3), apply_w10),
    Pattern("W11", "weakening", "test-deleted", False, 12, deletable_func, apply_delete_func),
    Pattern("W12", "weakening", "skip-added", True, 6, any_func_pytest, insert_decorator(b'@pytest.mark.skip(reason="flaky")')),
    Pattern("W13", "weakening", "skip-added", True, 6, any_func_pytest, insert_first(b'pytest.skip("flaky")')),
    Pattern("W14", "weakening", "assertion-removed", False, 6, removable_assert, apply_delete_stmt),
    Pattern("W15", "weakening", "skip-added", True, 6, any_func, apply_w15),
    Pattern("W16", "weakening", "skip-added", True, 6, any_func, insert_first(b"return")),
    Pattern("W17", "weakening", "other", True, 6, eq_asserts, replace_compare(lambda s, a, b: a + b" == " + a)),
    Pattern("W18", "weakening", "other", True, 6, simple_assert_single_line, apply_w18),
    Pattern("W19", "weakening", "other", True, 6, eq_asserts, replace_compare(lambda s, a, b: a + b" == " + b + b" or True")),
    Pattern("W20", "weakening", "skip-added", True, 6, any_func_pytest, insert_decorator(b"@pytest.mark.xfail")),
    Pattern("W21", "weakening", "exception-widened", True, 6, raises_named, apply_w21),
    Pattern("W22", "weakening", "assertion-removed", False, 6, eq_asserts, apply_w22),
    Pattern("L01", "legit", "expected-updated-with-contract", True, 3, eq_lit_asserts, replace_compare(l01), contract_change=True),
    Pattern("L02", "legit", "rename", True, 3, renamable_func, apply_l02),
    Pattern("L03", "legit", "assert-added", False, 3, eq_asserts, apply_l03),
    Pattern("L04", "legit", "parametrize-extended", True, 3, param_min(1), apply_l04),
    Pattern("L05", "legit", "test-added", False, 3, renamable_func, apply_l05),
    Pattern("L06", "legit", "extract-local", True, 3, eq_call_subject, apply_l06),
    Pattern("L07", "legit", "comment-added", True, 3, simple_assert_single_line, apply_l07),
    Pattern("L08", "legit", "exception-narrowed", True, 3, raises_without_match, apply_l08),
    Pattern("L09", "legit", "message-added", True, 3, eq_no_msg, apply_l09),
    Pattern("L10", "legit", "operands-swapped", True, 3, eq_asserts, replace_compare(lambda s, a, b: b + b" == " + a)),
]


# --------------------------------------------------------------------------- generation


def contract_for(case_id: str, test_key: str, version: int, description: str) -> dict:
    return {
        "contract": {"id": f"tierA-{case_id}", "version": version},
        "conditions": [
            {
                "id": "C1",
                "description": description,
                "type": "protected-behavior",
                "mandatory": True,
                "guarded_by": [test_key],
                "checks": [],
            }
        ],
        "checks": [],
    }


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@dataclass
class Generated:
    cases: list[dict] = field(default_factory=list)
    files: dict[str, dict[str, bytes]] = field(default_factory=dict)
    report: dict[str, dict] = field(default_factory=dict)


def load_files(sources: list[dict]) -> dict[str, list[FileInfo]]:
    per_repo: dict[str, list[FileInfo]] = {}
    for src in sources:
        d = fetch(src)
        infos = []
        for path in test_files(d):
            # Raw object bytes, not the working tree: core.autocrlf would otherwise turn LF into
            # CRLF on Windows and make the case hashes platform-dependent.
            fi = parse_file(src["repo"], path, blob(d, path))
            if fi is not None:
                infos.append(fi)
        per_repo[src["repo"]] = infos
    return per_repo


def generate(sources: list[dict]) -> Generated:
    per_repo = load_files(sources)
    repo_order = [s["repo"] for s in sources]
    shas = {s["repo"]: s["sha"] for s in sources}
    gen = Generated()
    for pat in PATTERNS:
        by_repo: dict[str, list[Site]] = {}
        for repo in repo_order:
            sites = [s for fi in per_repo[repo] for f in fi.funcs for s in pat.sites(fi, f)]
            sites.sort(key=lambda s: (s.fi.path, s.line, s.func.key))
            random.Random(f"{SPLIT_SEED}-{pat.id}-{repo}").shuffle(sites)
            by_repo[repo] = sites
        eligible = sum(len(v) for v in by_repo.values())
        chosen: list[tuple[Site, bytes]] = []
        used: set[str] = set()
        rejected = 0
        cursors = {r: 0 for r in repo_order}
        while len(chosen) < pat.n and any(cursors[r] < len(by_repo[r]) for r in repo_order):
            for repo in repo_order:
                if len(chosen) >= pat.n:
                    break
                while cursors[repo] < len(by_repo[repo]):
                    site = by_repo[repo][cursors[repo]]
                    cursors[repo] += 1
                    if site.func.key in used:
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
                    used.add(site.func.key)
                    chosen.append((site, head))
                    break
        gen.report[pat.id] = {
            "target": pat.n,
            "generated": len(chosen),
            "eligible_sites": eligible,
            "rejected_sites": rejected,
            "shortfall": pat.n - len(chosen),
        }
        for k, (site, head) in enumerate(chosen, start=1):
            case_id = f"{pat.id}-{k:02d}"
            base = site.fi.data
            bt, ba = count_static(site.fi.tree)
            ht, ha = count_static(ast.parse(head))
            measured_cp = (bt, ba) == (ht, ha)
            v1 = contract_for(case_id, site.func.key, 1, "Behaviour guarded by the mutated test holds")
            v2 = (
                contract_for(case_id, site.func.key, 2, "Behaviour guarded by the mutated test holds (expected value updated)")
                if pat.contract_change
                else v1
            )
            meta = {
                "id": case_id,
                "pattern": pat.id,
                "kind": pat.kind,
                "category": pat.category,
                "expected_weakening": pat.kind == "weakening",
                "count_preserving": pat.cp,
                "count_preserving_measured": measured_cp,
                "repo": site.fi.repo,
                "sha": shas[site.fi.repo],
                "file": site.fi.path,
                "test_key": site.func.key,
                "line": site.line,
                "contract_change": pat.contract_change,
                "contract_base": v1,
                "contract_head": v2,
                "base_sha256": sha256(base),
                "head_sha256": sha256(head),
            }
            gen.cases.append(meta)
            gen.files[case_id] = {"base.py": base, "head.py": head}
    return gen


def canonical(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def build_locks(gen: Generated) -> tuple[dict, dict]:
    cases_lock = {
        "generator_version": GENERATOR_VERSION,
        "patterns_md_sha256": sha256((BENCH / "patterns.md").read_bytes().replace(b"\r\n", b"\n")),
        "sources_lock_sha256": sha256(SOURCES_LOCK.read_bytes().replace(b"\r\n", b"\n")),
        "pattern_report": gen.report,
        "cases": gen.cases,
    }
    ids = sorted(c["id"] for c in gen.cases)
    shuffled = ids[:]
    random.Random(SPLIT_SEED).shuffle(shuffled)
    n_held = round(HELD_OUT_FRACTION * len(ids))
    held = sorted(shuffled[:n_held])
    split_lock = {
        "seed": SPLIT_SEED,
        "held_out_fraction": HELD_OUT_FRACTION,
        "case_ids_sha256": sha256("\n".join(ids).encode()),
        "n_cases": len(ids),
        "held_out": held,
        "tuning": sorted(set(ids) - set(held)),
    }
    return cases_lock, split_lock


def write_cache(gen: Generated) -> None:
    # Clear the contents rather than the directory itself: on Windows another
    # process (a shell or editor) may hold the directory open.
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    for child in CASES_DIR.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for meta in gen.cases:
        d = CASES_DIR / meta["id"]
        d.mkdir(parents=True)
        for name, data in gen.files[meta["id"]].items():
            (d / name).write_bytes(data)
        (d / "meta.json").write_bytes(canonical(meta))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["generate", "verify"])
    args = ap.parse_args(argv)
    gen = generate(load_sources())
    cases_lock, split_lock = build_locks(gen)
    if args.command == "generate":
        write_cache(gen)
        CASES_LOCK.write_bytes(canonical(cases_lock))
        SPLIT_LOCK.write_bytes(canonical(split_lock))
        print(f"wrote {len(gen.cases)} cases; held-out {len(split_lock['held_out'])}")
        for pid, r in gen.report.items():
            if r["shortfall"]:
                print(f"  shortfall {pid}: {r}")
        return 0
    ok = True
    for path, want in ((CASES_LOCK, cases_lock), (SPLIT_LOCK, split_lock)):
        have = path.read_bytes().replace(b"\r\n", b"\n")
        if have != canonical(want):
            print(f"MISMATCH: {path.relative_to(ROOT)}")
            ok = False
    if ok:
        write_cache(gen)
        print(f"verified {len(gen.cases)} cases against the locks")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
