"""Core analyzer: align base/head tests and apply rules S1-S4, B1-B3, V0, R0.

Spec: docs/03-build-spec.md section 4. Pure and stdlib only. The analyzed
source is only ever passed to tokenize (via limits) and ast.parse; it is
never imported, compiled to code, or executed.
"""

from __future__ import annotations

import ast
import builtins
from collections import defaultdict
from dataclasses import dataclass, field

from . import limits as _limits
from .model import B1, B2, B3, R0, S1, S2, S3, S4, V0, FileResult, Finding, Gap

# Builtin exception classes by name, resolved once at import from the
# interpreter's own namespace (never looked up from analyzed input).
_BUILTIN_EXC: dict[str, type] = {
    name: obj for name, obj in vars(builtins).items() if isinstance(obj, type) and issubclass(obj, BaseException)
}

# pytest.approx defaults (pytest docs): rel=1e-6, abs=1e-12.
_APPROX_DEFAULT = {"rel": 1e-6, "abs": 1e-12}

_RANK = {"==": 3, "is": 3, "approx": 3, "<": 2, "<=": 2, ">": 2, ">=": 2, "in": 2, "not in": 2, "!=": 1, "is not": 1}
_TRIVIAL = {"truthy", "isinstance", "not_none"}
_FLIP = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "==": "==", "!=": "!=", "is": "is", "is not": "is not"}
_OP_TEXT = {
    ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
    ast.Is: "is", ast.IsNot: "is not", ast.In: "in", ast.NotIn: "not in",
}


# --------------------------------------------------------------------------- parsing


@dataclass
class TestFn:
    key: str
    node: ast.FunctionDef | ast.AsyncFunctionDef

    @property
    def name(self) -> str:
        return self.node.name


@dataclass
class Parsed:
    source: str
    tree: ast.Module
    tests: dict[str, TestFn]
    module_skip: bool
    lines: list[str] = field(default_factory=list)

    def text(self, node: ast.AST) -> str:
        """Full source lines spanned by node, including its decorators."""
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        return "".join(self.lines[start - 1:node.end_lineno])


def _dump(node: ast.AST | None) -> str:
    return "" if node is None else ast.dump(node, annotate_fields=False)


def _parse(path: str, source: str, lim: _limits.Limits) -> tuple[Parsed | None, Gap | None]:
    try:
        _limits.check(source, lim)
    except _limits.LimitExceeded as exc:
        return None, Gap("input_limit", str(exc), file=path)
    except _limits.Untokenizable as exc:
        return None, Gap("unparseable", str(exc), file=path)
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        return None, Gap("unparseable", f"{type(exc).__name__}: {exc}", file=path)
    tests: dict[str, TestFn] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            tests[f"{path}::{node.name}"] = TestFn(f"{path}::{node.name}", node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    key = f"{path}::{node.name}::{sub.name}"
                    tests[key] = TestFn(key, sub)
    module_skip = any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in n.targets)
        and any(_skip_mark(m) for m in (n.value.elts if isinstance(n.value, (ast.List, ast.Tuple)) else [n.value]))
        for n in tree.body
    )
    return Parsed(source, tree, tests, module_skip, source.splitlines(keepends=True)), None


def _walk_own(func: ast.AST):
    """Walk a function body without entering nested functions, lambdas or classes."""
    stack = list(reversed(getattr(func, "body", [])))
    while stack:
        node = stack.pop()
        yield node
        for child in reversed(list(ast.iter_child_nodes(node))):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                stack.append(child)


def _seg(p: Parsed, node: ast.AST) -> str:
    """Exact source of node (columns are UTF-8 byte offsets, as in ast.get_source_segment)."""
    lineno, end = getattr(node, "lineno", None), getattr(node, "end_lineno", None)
    if lineno is None or end is None or end > len(p.lines):
        return ""
    col, end_col = node.col_offset, node.end_col_offset
    if lineno == end:
        return p.lines[lineno - 1].encode("utf-8")[col:end_col].decode("utf-8", "replace")
    first = p.lines[lineno - 1].encode("utf-8")[col:].decode("utf-8", "replace")
    last = p.lines[end - 1].encode("utf-8")[:end_col].decode("utf-8", "replace")
    return first + "".join(p.lines[lineno:end - 1]) + last


def _lines(node: ast.AST) -> tuple[int, int]:
    return (node.lineno, getattr(node, "end_lineno", None) or node.lineno)


def _body_dump(fn: TestFn) -> str:
    n = fn.node
    return _dump(ast.Module(body=n.body, type_ignores=[])) + "|" + _dump(n.args) + "|" + "|".join(
        _dump(d) for d in n.decorator_list
    )


# --------------------------------------------------------------------------- assertion model


def _is_literal(n: ast.AST) -> bool:
    if isinstance(n, ast.Constant):
        return True
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)) and isinstance(n.operand, ast.Constant):
        return True
    if isinstance(n, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_literal(e) for e in n.elts)
    if isinstance(n, ast.Dict):
        return all(k is not None and _is_literal(k) for k in n.keys) and all(_is_literal(v) for v in n.values)
    return False


def _num(n: ast.AST | None) -> float | None:
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
        return float(n.value)
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
        v = _num(n.operand)
        return -v if v is not None else None
    return None


def _is_approx_call(n: ast.AST) -> bool:
    if not isinstance(n, ast.Call):
        return False
    f = n.func
    return (isinstance(f, ast.Attribute) and f.attr == "approx") or (isinstance(f, ast.Name) and f.id == "approx")


@dataclass
class Check:
    """One assert statement, normalised to subject / operator / expected."""

    node: ast.Assert
    subject: str  # dump of the subject expression
    op: str  # comparison op text, "approx", or a trivial kind
    expected: ast.AST | None
    tol: dict = field(default_factory=dict)

    @property
    def full(self) -> str:
        return _dump(self.node.test)

    @property
    def expected_literal(self) -> bool:
        return self.expected is not None and _is_literal(self.expected) and not (
            isinstance(self.expected, ast.Constant) and self.expected.value is None
        )

    @property
    def rank(self) -> int:
        return 0 if self.op in _TRIVIAL else _RANK.get(self.op, 2)


def _classify(node: ast.Assert) -> Check:
    t = node.test
    if isinstance(t, ast.Compare) and len(t.ops) == 1:
        left, right = t.left, t.comparators[0]
        op = _OP_TEXT.get(type(t.ops[0]), "?")
        if _is_literal(left) and not _is_literal(right):
            left, right, op = right, left, _FLIP.get(op, op)
        if op in ("is not", "!=") and isinstance(right, ast.Constant) and right.value is None:
            return Check(node, _dump(left), "not_none", None)
        if op == "==" and _is_approx_call(right):
            tol = {k.arg: _num(k.value) for k in right.keywords if k.arg in ("rel", "abs")}
            return Check(node, _dump(left), "approx", right.args[0] if right.args else None, tol)
        return Check(node, _dump(left), op, right)
    if (
        isinstance(t, ast.Call)
        and isinstance(t.func, ast.Name)
        and t.func.id == "isinstance"
        and len(t.args) == 2
    ):
        return Check(node, _dump(t.args[0]), "isinstance", t.args[1])
    return Check(node, _dump(t), "truthy", None)


def _approx_tol(c: Check) -> dict[str, float]:
    out = dict(_APPROX_DEFAULT)
    for k, v in c.tol.items():
        if v is not None:
            out[k] = v
    return out


def _compare_pair(b: Check, h: Check) -> tuple[str, str] | None:
    """Classify a base/head pair with the same subject. Returns (rule, rationale) or None."""
    strong = b.op in ("==", "is")
    if strong and b.expected_literal:
        if h.op in _TRIVIAL:
            kinds = {"truthy": "a bare truthiness check", "isinstance": "an isinstance() type check",
                     "not_none": "a not-None check"}
            return S2, f"Equality to a specific expected value was replaced by {kinds[h.op]}."
        if h.op in (">=", ">"):
            hv, bv = _num(h.expected), _num(b.expected)
            if hv is not None and (hv == 0 or (bv is not None and hv < bv)):
                return S2, f"Equality to a specific expected value was replaced by the bound '{h.op} {hv:g}'."
    if b.op == "==" and h.op == "approx" and _num(b.expected) is not None:
        return S1, "Exact equality was relaxed to pytest.approx."
    if b.op == "approx" and h.op == "approx":
        bt, ht = _approx_tol(b), _approx_tol(h)
        widened = [k for k in ("rel", "abs") if ht[k] > bt[k]]
        if widened:
            return S1, f"pytest.approx tolerance was increased ({', '.join(widened)})."
        if _dump(b.expected) != _dump(h.expected):
            return V0, "The expected value changed; comparison strength is unchanged."
        return None
    if h.rank < b.rank:
        return S1, f"Comparison operator weakened from '{_op_label(b)}' to '{_op_label(h)}'."
    if h.rank == b.rank and h.op == b.op and _dump(b.expected) != _dump(h.expected):
        return V0, "The expected value changed; comparison strength is unchanged."
    return None


def _op_label(c: Check) -> str:
    return {"truthy": "truthiness", "isinstance": "isinstance", "not_none": "is not None"}.get(c.op, c.op)


# --------------------------------------------------------------------------- per-test rules


def _raises_calls(fn: TestFn) -> list[ast.Call]:
    out = []
    for n in _walk_own(fn.node):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Attribute) and f.attr == "raises" and isinstance(f.value, ast.Name)
                    and f.value.id == "pytest") or (isinstance(f, ast.Name) and f.id == "raises"):
                out.append(n)
    return out


def _exc_names(n: ast.AST | None) -> list[str] | None:
    if isinstance(n, ast.Name):
        return [n.id]
    if isinstance(n, ast.Attribute):
        return [n.attr]
    if isinstance(n, ast.Tuple):
        names = []
        for e in n.elts:
            sub = _exc_names(e)
            if sub is None:
                return None
            names.extend(sub)
        return names
    return None


def _covers(head: str, base: str) -> bool:
    """True if catching `head` also catches `base`, and head is strictly broader."""
    if head == base:
        return False
    h, b = _BUILTIN_EXC.get(head), _BUILTIN_EXC.get(base)
    if h is not None and b is not None:
        return issubclass(b, h)
    # base is a project-defined exception: Exception/BaseException cover it.
    return head in ("Exception", "BaseException")


def _s3(pb: Parsed, ph: Parsed, tb: TestFn, th: TestFn, path: str) -> list[Finding]:
    out = []
    rb, rh = _raises_calls(tb), _raises_calls(th)
    for cb, ch in zip(rb, rh):
        reasons = []
        bn = _exc_names(cb.args[0]) if cb.args else None
        hn = _exc_names(ch.args[0]) if ch.args else None
        if bn and hn:
            widened = [h for h in hn if h not in bn and any(_covers(h, b) for b in bn)]
            if widened:
                reasons.append(f"expected exception widened from {', '.join(bn)} to include {', '.join(widened)}")
        if any(k.arg == "match" for k in cb.keywords) and not any(k.arg == "match" for k in ch.keywords):
            reasons.append("the match= message check was removed")
        if reasons:
            out.append(Finding(
                S3, path, th.key, "pytest.raises: " + "; ".join(reasons) + ".",
                base_lines=_lines(cb), head_lines=_lines(ch), before=_seg(pb, cb), after=_seg(ph, ch),
            ))
    return out


def _parametrize(fn: TestFn) -> dict[str, ast.Call]:
    out = {}
    for d in fn.node.decorator_list:
        if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "parametrize"
                and len(d.args) >= 2 and isinstance(d.args[1], (ast.List, ast.Tuple))):
            out[_dump(d.args[0])] = d
    return out


def _s4(pb: Parsed, ph: Parsed, tb: TestFn, th: TestFn, path: str) -> list[Finding]:
    out = []
    hp = _parametrize(th)
    for names, db in _parametrize(tb).items():
        dh = hp.get(names)
        if dh is None:
            continue
        base_cases = {_dump(e): e for e in db.args[1].elts}
        head_cases = {_dump(e) for e in dh.args[1].elts}
        if head_cases < set(base_cases):
            removed = [base_cases[k] for k in base_cases if k not in head_cases]
            out.append(Finding(
                S4, path, th.key,
                f"{len(removed)} of {len(base_cases)} parametrize case(s) were removed.",
                base_lines=_lines(removed[0]), head_lines=_lines(dh),
                before="\n".join(_seg(pb, e) for e in removed), after=None,
                detail={"removed_cases": [_seg(pb, e) for e in removed], "base_count": len(base_cases),
                        "head_count": len(head_cases)},
            ))
    return out


def _mark_name(d: ast.AST) -> tuple[str | None, ast.Call | None]:
    call = d if isinstance(d, ast.Call) else None
    target = d.func if call else d
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute) and target.value.attr == "mark":
        return target.attr, call
    return None, None


def _skip_mark(d: ast.AST) -> bool:
    name, call = _mark_name(d)
    if name == "skip":
        return True
    if name == "xfail":
        if call is None or not call.args:
            return True
        return isinstance(call.args[0], ast.Constant) and bool(call.args[0].value)
    if name == "skipif" and call is not None and call.args:
        return isinstance(call.args[0], ast.Constant) and bool(call.args[0].value)
    return False


def _body_after_doc(fn: TestFn) -> list[ast.stmt]:
    body = fn.node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(
        body[0].value.value, str
    ):
        return body[1:]
    return body


def _guards(fn: TestFn) -> dict[str, ast.stmt]:
    """Top-level statements that stop the test before the rest of its body runs.

    Tuning round 1 (tuning split only): a guard counts whenever statements
    follow it (or sit inside an `if False:`), not only when visible asserts
    follow, because many tests check through helper calls.
    """
    out = {}
    body = _body_after_doc(fn)
    for i, s in enumerate(body):
        rest = body[i + 1:]
        is_guard = False
        if isinstance(s, ast.Return) and s.value is None and rest:
            is_guard = True
        elif isinstance(s, ast.Expr) and isinstance(s.value, ast.Call):
            f = s.value.func
            if ((isinstance(f, ast.Attribute) and f.attr == "skip") or (isinstance(f, ast.Name) and f.id == "skip")) \
                    and rest:
                is_guard = True
        elif isinstance(s, ast.If) and isinstance(s.test, ast.Constant) and not s.test.value and not s.orelse:
            is_guard = True
        if is_guard:
            out[_dump(s)] = s
    return out


def _b2(pb: Parsed, ph: Parsed, tb: TestFn, th: TestFn, path: str) -> list[Finding]:
    out = []
    base_marks = {_dump(d) for d in tb.node.decorator_list if _skip_mark(d)}
    for d in th.node.decorator_list:
        if _skip_mark(d) and _dump(d) not in base_marks:
            out.append(Finding(
                B2, path, th.key, "An unconditional skip/xfail marker was added; the test no longer gates the change.",
                head_lines=_lines(d), after="@" + _seg(ph, d),
            ))
    base_guards = _guards(tb)
    for k, s in _guards(th).items():
        if k not in base_guards:
            first = _seg(ph, s).splitlines()[0] if _seg(ph, s) else ""
            out.append(Finding(
                B2, path, th.key, "A guard was added before the test's checks, so they no longer run.",
                head_lines=(s.lineno, s.lineno), after=first,
            ))
    return out


def _assert_rules(pb: Parsed, ph: Parsed, tb: TestFn, th: TestFn, path: str) -> list[Finding]:
    out: list[Finding] = []
    base = [_classify(n) for n in _walk_own(tb.node) if isinstance(n, ast.Assert)]
    head = [_classify(n) for n in _walk_own(th.node) if isinstance(n, ast.Assert)]
    # 1. Drop exact matches (multiset on the full normalised test expression).
    pool: dict[str, list[Check]] = defaultdict(list)
    for h in head:
        pool[h.full].append(h)
    rem_base = []
    for b in base:
        if pool[b.full]:
            pool[b.full].pop(0)
        else:
            rem_base.append(b)
    left = {id(x) for lst in pool.values() for x in lst}
    rem_head = [h for h in head if id(h) in left]
    # 2. Pair the rest by subject, in order.
    by_subject: dict[str, list[Check]] = defaultdict(list)
    for h in rem_head:
        by_subject[h.subject].append(h)
    unpaired_base = []
    for b in rem_base:
        if by_subject[b.subject]:
            h = by_subject[b.subject].pop(0)
            res = _compare_pair(b, h)
            if res:
                rule, why = res
                out.append(Finding(
                    rule, path, th.key, why, base_lines=_lines(b.node), head_lines=_lines(h.node),
                    before=_seg(pb, b.node), after=_seg(ph, h.node),
                ))
        else:
            unpaired_base.append(b)
    # 3. B3: fewer checks (assert + pytest.raises) and unexplained removals.
    deficit = (len(base) + len(_raises_calls(tb))) - (len(head) + len(_raises_calls(th)))
    if deficit > 0:
        removed_raises = _raises_calls(tb)[len(_raises_calls(th)):] if len(_raises_calls(tb)) > len(_raises_calls(th)) else []
        candidates = [c.node for c in unpaired_base] + removed_raises
        for node in candidates[:deficit]:
            out.append(Finding(
                B3, path, th.key, "A check was removed from the test and not replaced.",
                base_lines=_lines(node), before=_seg(pb, node),
            ))
        if len(candidates) < deficit:
            out.append(Finding(
                B3, path, th.key,
                f"The test has {deficit} fewer check(s) than before.",
                detail={"deficit": deficit, "cited": len(candidates)},
            ))
    return out


# --------------------------------------------------------------------------- file level


def analyze_file(
    path: str,
    base_source: str | None,
    head_source: str | None,
    lim: _limits.Limits | None = None,
) -> FileResult:
    """Compare one test file between base and head. None means the file is absent on that side."""
    lim = lim or _limits.Limits.cli()
    res = FileResult(path)
    pb = ph = None
    if base_source is not None:
        pb, gap = _parse(path, base_source, lim)
        if gap:
            res.gaps.append(gap)
            return res
        res.tests_base = sorted(pb.tests)
    if head_source is not None:
        ph, gap = _parse(path, head_source, lim)
        if gap:
            res.gaps.append(gap)
            return res
        res.tests_head = sorted(ph.tests)
    if pb is None:
        return res  # new file: nothing can have been weakened
    if ph is None:
        for key, t in sorted(pb.tests.items()):
            res.findings.append(Finding(
                B1, path, key, "The test file was deleted.", base_lines=_lines(t.node),
                before=_seg(pb, t.node).splitlines()[0] if _seg(pb, t.node) else None, detail={"file_deleted": True},
            ))
        return res

    head_bodies: dict[str, list[str]] = defaultdict(list)
    for key, t in ph.tests.items():
        if key not in pb.tests:
            head_bodies[_body_dump(t)].append(key)
    for key, tb in sorted(pb.tests.items()):
        th = ph.tests.get(key)
        if th is None:
            candidates = head_bodies.get(_body_dump(tb))
            if candidates:
                new_key = candidates.pop(0)
                res.findings.append(Finding(
                    R0, path, new_key, f"Renamed from {key.split('::')[-1]}; the body is unchanged.",
                    base_lines=_lines(tb.node), head_lines=_lines(ph.tests[new_key].node),
                    detail={"renamed_from": key},
                ))
            else:
                res.findings.append(Finding(
                    B1, path, key, "The test function was deleted.", base_lines=_lines(tb.node),
                    before=_seg(pb, tb.node).splitlines()[0] if _seg(pb, tb.node) else None,
                ))
            continue
        if pb.text(tb.node) == ph.text(th.node) or _dump(tb.node) == _dump(th.node):
            continue
        res.findings.extend(_assert_rules(pb, ph, tb, th, path))
        res.findings.extend(_s3(pb, ph, tb, th, path))
        res.findings.extend(_s4(pb, ph, tb, th, path))
        res.findings.extend(_b2(pb, ph, tb, th, path))
    if ph.module_skip and not pb.module_skip:
        for key in sorted(k for k in ph.tests if k in pb.tests):
            res.findings.append(Finding(
                B2, path, key, "A module-level pytestmark skip was added; no test in this file gates the change.",
                detail={"module": True},
            ))
    res.findings.sort(key=Finding.sort_key)
    return res


def is_test_path(path: str) -> bool:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))
