"""Count-based test-weakening detector: a REIMPLEMENTATION, not the original tools.

It reimplements the publicly documented rules of the detectors in
docs/00-competitive-audit.md section 3 (PRD section 6.3). Each rule cites the
PR description it comes from. Where a description is underspecified, the
interpretation is stated next to the rule. It works per file, as those tools
describe, and does no semantic comparison of assertions.

Sources (descriptions as of November 2026, per the audit):
  [CP] caiopizzol/skills PR #99      https://github.com/caiopizzol/skills/pull/99
  [RF] Refactron PR #169             https://github.com/Refactron-ai/refactron/pull/169
  [CM] manaflow-ai/cmux PR #34485    https://github.com/manaflow-ai/cmux/pull/34485
  [MH] tinhtute/monoharness PR #246  https://github.com/tinhtute/monoharness/pull/246
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

RULES = {
    "C1": "Test file deleted [CP][CM][MH]",
    "C2": "Test definition removed or renamed [RF][CM]",
    "C3": "Unconditional skip/xfail marker or skip call added [CP][RF][CM][MH]",
    "C4": "Bare return added to a test [CP]",
    "C5": "Net drop in assertion count [RF][CM][MH]; counted forms from [CP]",
    "C6": "__test__ = False or collect_ignore added [CP]",
    "C7": "Trivial assertion added, interpreted as assert <truthy constant> [MH]",
}


@dataclass
class BaselineFinding:
    rule: str
    detail: str


def _tests(tree: ast.Module) -> dict[str, ast.AST]:
    out = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
            out[n.name] = n
        elif isinstance(n, ast.ClassDef):
            for s in n.body:
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name.startswith("test"):
                    out[f"{n.name}.{s.name}"] = s
    return out


def _assertion_count(tree: ast.Module) -> int:
    """Counted forms per [CP]: assert, self.assert*, mock .assert_*, pytest.raises / warns."""
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            n += 1
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr.startswith("assert") or attr in ("raises", "warns"):
                n += 1
    return n


def _unconditional_marks(fn: ast.AST) -> int:
    count = 0
    for d in getattr(fn, "decorator_list", []):
        target = d.func if isinstance(d, ast.Call) else d
        name = target.attr if isinstance(target, ast.Attribute) else None
        if name in ("skip", "xfail"):
            args = d.args if isinstance(d, ast.Call) else []
            if not args or (isinstance(args[0], ast.Constant) and args[0].value):
                count += 1
    return count


def _skip_calls(fn: ast.AST) -> int:
    return sum(
        1 for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "skip"
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "pytest"
    )


def _bare_returns(fn: ast.AST) -> int:
    return sum(1 for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is None)


def _trivial_asserts(tree: ast.Module) -> int:
    return sum(
        1 for n in ast.walk(tree)
        if isinstance(n, ast.Assert) and isinstance(n.test, ast.Constant) and bool(n.test.value)
    )


def _collection_disabled(tree: ast.Module) -> bool:
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id in ("__test__", "collect_ignore"):
                    return True
    return False


def detect(base: str | None, head: str | None) -> list[BaselineFinding]:
    """Compare one test file. None means absent on that side. Unparseable input yields no findings."""
    if base is None:
        return []
    try:
        bt = ast.parse(base)
    except (SyntaxError, ValueError):
        return []
    if head is None:
        return [BaselineFinding("C1", "test file deleted")]
    try:
        ht = ast.parse(head)
    except (SyntaxError, ValueError):
        return []
    out: list[BaselineFinding] = []
    btests, htests = _tests(bt), _tests(ht)
    for name in sorted(set(btests) - set(htests)):
        out.append(BaselineFinding("C2", f"test {name} removed or renamed"))
    for name in sorted(set(btests) & set(htests)):
        b, h = btests[name], htests[name]
        if _unconditional_marks(h) > _unconditional_marks(b) or _skip_calls(h) > _skip_calls(b):
            out.append(BaselineFinding("C3", f"skip/xfail added to {name}"))
        if _bare_returns(h) > _bare_returns(b):
            out.append(BaselineFinding("C4", f"bare return added to {name}"))
    ba, ha = _assertion_count(bt), _assertion_count(ht)
    if ha < ba:
        out.append(BaselineFinding("C5", f"assertion count dropped {ba} -> {ha}"))
    if _collection_disabled(ht) and not _collection_disabled(bt):
        out.append(BaselineFinding("C6", "test collection disabled"))
    if _trivial_asserts(ht) > _trivial_asserts(bt):
        out.append(BaselineFinding("C7", "trivial assertion added"))
    return out
