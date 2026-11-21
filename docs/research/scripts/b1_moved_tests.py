"""How many real B1 hits are tests moved to another file in the same commit?

Research measurement on docs/research/data/real_history_scan.json. Not product code. For every B1
finding, the deleted test counts as "moved" when another test file changed by the same commit
gains a test (absent from that file's base) whose body is identical (AST dump, docstring
included), under any name. A commit counts as "all moved" when every B1 finding in it is a move.

Prerequisite: python docs/research/scripts/real_history_scan.py (fills the history cache).
Run:  python docs/research/scripts/b1_moved_tests.py  ->  docs/research/data/b1_moved_tests.json
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import real_history_scan as R  # noqa: E402

OUT = ROOT / "docs" / "research" / "data" / "b1_moved_tests.json"


def bodies(src: str | None) -> dict[str, str]:
    """test id suffix (name or Class::name) -> body dump."""
    if src is None:
        return {}
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return {}
    out = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
            out[n.name] = ast.dump(ast.Module(body=n.body, type_ignores=[]))
        elif isinstance(n, ast.ClassDef):
            for s in n.body:
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name.startswith("test"):
                    out[f"{n.name}::{s.name}"] = ast.dump(ast.Module(body=s.body, type_ignores=[]))
    return out


def main() -> int:
    scan = json.loads((ROOT / "docs" / "research" / "data" / "real_history_scan.json").read_text(encoding="utf-8"))
    rows = []
    for c in scan["candidates_for_owner_labeling"]:
        b1 = [f for f in c["findings"] if f["rule"] == "B1" and f["test"]]
        if not b1:
            continue
        repo = R.CACHE / (c["repo"].replace("/", "__") + ".git")
        parent = R.git(repo, "rev-parse", c["commit"] + "^").strip()
        files = R.changes(repo, parent, c["commit"])
        gained: dict[str, set[str]] = {}  # file -> body dumps of tests new in that file
        base_bodies = {}
        for f in files:
            b, h = bodies(f[1]), bodies(f[2])
            gained[f[0]] = {body for k, body in h.items() if k not in b}
            base_bodies[f[3] if len(f) > 3 else f[0]] = b
        moved = 0
        for f in b1:
            test_file, suffix = f["test"].split("::", 1)
            body = base_bodies.get(test_file, {}).get(suffix)
            if body is not None and any(body in g for other, g in gained.items() if other != f["file"]):
                moved += 1
        rows.append({"repo": c["repo"], "commit": c["commit"], "b1_findings": len(b1), "moved": moved,
                     "all_moved": moved == len(b1), "other_rules": sorted(set(c["rules"]) - {"B1"})})
    result = {
        "schema_version": "1",
        "definition": "a deleted test is 'moved' when another file changed by the same commit gains a test with an "
                      "identical body",
        "b1_commits": len(rows),
        "b1_findings": sum(r["b1_findings"] for r in rows),
        "moved_findings": sum(r["moved"] for r in rows),
        "all_moved_commits": sum(r["all_moved"] for r in rows),
        "all_moved_and_no_other_rule": sum(r["all_moved"] and not r["other_rules"] for r in rows),
        "commits": rows,
        "command": "python docs/research/scripts/b1_moved_tests.py",
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print({k: v for k, v in result.items() if k not in ("commits", "definition")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
