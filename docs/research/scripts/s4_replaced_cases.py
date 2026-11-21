"""Counterfactual for the owner's pending S4 decision: how many real S4 hits are "replaced cases"?

Research measurement on docs/research/data/real_history_scan.json. Not product code; the engine is
not changed. For every commit where S4 fired, each removed parametrize case is called "replaced"
when an added case in the same decorator has the same length and differs only in its last element
(usually the expected value), the way V0 treats an assert whose expected value changed. A commit
counts as "all replaced" when every removed case in every S4 finding is replaced.

Prerequisite: python docs/research/scripts/real_history_scan.py (fills the history cache).
Run:  python docs/research/scripts/s4_replaced_cases.py  ->  docs/research/data/s4_replaced_cases.json
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import real_history_scan as R  # noqa: E402

OUT = ROOT / "docs" / "research" / "data" / "s4_replaced_cases.json"


def _cases(src: str | None, func: str, cls: str | None) -> dict[str, list[ast.AST]]:
    """argnames-dump -> list of case nodes, for the parametrize decorators of func."""
    if src is None:
        return {}
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return {}
    scope = tree.body
    if cls:
        scope = next((n.body for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls), [])
    fn = next((n for n in scope if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func), None)
    out: dict[str, list[ast.AST]] = {}
    for d in getattr(fn, "decorator_list", []):
        if isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "parametrize" and len(d.args) >= 2 \
                and isinstance(d.args[1], (ast.List, ast.Tuple)):
            out[ast.dump(d.args[0])] = list(d.args[1].elts)
    return out


def _elts(n: ast.AST) -> list[str] | None:
    if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "param":  # pytest.param(...)
        n = ast.Tuple(elts=list(n.args), ctx=ast.Load())
    return [ast.dump(e) for e in n.elts] if isinstance(n, (ast.Tuple, ast.List)) else None


def replaced(removed: ast.AST, added: list[ast.AST]) -> bool:
    r = _elts(removed)
    if not r or len(r) < 2:
        return False
    return any((a := _elts(x)) is not None and len(a) == len(r) and a[:-1] == r[:-1] for x in added)


def main() -> int:
    scan = json.loads((ROOT / "docs" / "research" / "data" / "real_history_scan.json").read_text(encoding="utf-8"))
    rows = []
    for c in scan["candidates_for_owner_labeling"]:
        s4 = [f for f in c["findings"] if f["rule"] == "S4"]
        if not s4:
            continue
        repo = R.CACHE / (c["repo"].replace("/", "__") + ".git")
        parent = R.git(repo, "rev-parse", c["commit"] + "^").strip()
        files = {f[0]: f for f in R.changes(repo, parent, c["commit"])}
        n_removed = n_replaced = 0
        for f in s4:
            entry = files.get(f["file"])
            parts = f["test"].split("::")
            func, cls = parts[-1], parts[1] if len(parts) == 3 else None
            base = _cases(entry[1] if entry else None, func, cls)
            head = _cases(entry[2] if entry else None, func, cls)
            for names, bcases in base.items():
                hcases = head.get(names, [])
                hd = {ast.dump(x) for x in hcases}
                bd = {ast.dump(x) for x in bcases}
                added = [x for x in hcases if ast.dump(x) not in bd]
                for r in bcases:
                    if ast.dump(r) not in hd:
                        n_removed += 1
                        n_replaced += replaced(r, added)
        rows.append({"repo": c["repo"], "commit": c["commit"], "removed_cases": n_removed, "replaced_cases": n_replaced,
                     "all_replaced": n_removed > 0 and n_removed == n_replaced,
                     "other_rules": sorted(set(c["rules"]) - {"S4"})})
    result = {
        "schema_version": "1",
        "definition": "a removed case is 'replaced' when an added case in the same decorator has the same length "
                      "and differs only in its last element",
        "s4_commits": len(rows),
        "all_replaced_commits": sum(r["all_replaced"] for r in rows),
        "all_replaced_and_no_other_rule": sum(r["all_replaced"] and not r["other_rules"] for r in rows),
        "removed_cases": sum(r["removed_cases"] for r in rows),
        "replaced_cases": sum(r["replaced_cases"] for r in rows),
        "commits": rows,
        "command": "python docs/research/scripts/s4_replaced_cases.py",
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print({k: v for k, v in result.items() if k not in ("commits", "definition")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
