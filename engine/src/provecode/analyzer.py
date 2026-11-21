"""Core analyzer: align base/head tests and apply rules S1-S5, B1-B4, V0, R0.

Spec: docs/03-build-spec.md section 4. Pure and stdlib only. The analyzed
source is only ever passed to tokenize (via limits) and ast.parse; it is
never imported, compiled to code, or executed.
"""

from __future__ import annotations

import ast
import builtins
import io
from collections import defaultdict
from dataclasses import dataclass, field

from . import limits as _limits
from .model import B1, B2, B3, B4, R0, S1, S2, S3, S4, S5, T1, T2, T3, T4, T5, V0, FileResult, Finding, Gap

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


# ast.dump and our own recursion are bounded by this AST depth; deeper input is an
# input_limit gap (an unbracketed 1+1+...+1 chain passes the bracket pre-scan).
MAX_AST_DEPTH = 120


@dataclass
class TestFn:
    key: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    class_marks: frozenset[str] = frozenset()  # skip-like marks on the enclosing class

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
    pytest_names: frozenset[str] = frozenset()  # names bound by `from pytest import ...`
    unittest_names: frozenset[str] = frozenset()  # names bound by `from unittest import ...`

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
    depth = _max_depth(tree)
    if depth > MAX_AST_DEPTH:
        return None, Gap("input_limit", f"syntax tree depth exceeds {MAX_AST_DEPTH}", file=path)
    pytest_names = frozenset(
        (a.asname or a.name) for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "pytest"
        for a in n.names
    )
    unittest_names = frozenset(
        (a.asname or a.name) for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "unittest"
        for a in n.names
    )
    tests: dict[str, TestFn] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            tests[f"{path}::{node.name}"] = TestFn(f"{path}::{node.name}", node)
        elif isinstance(node, ast.ClassDef) and _is_test_class(node):
            marks = frozenset(_dump(d) for d in node.decorator_list if _skip_mark(d, unittest_names)) | \
                _pytestmark_skips(node.body)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    key = f"{path}::{node.name}::{sub.name}"
                    tests[key] = TestFn(key, sub, marks)
    module_skip = bool(_pytestmark_skips(tree.body))
    # Split lines exactly as the tokenizer counts them (\r\n, \r, \n only). str.splitlines
    # also splits on \f, \x1c-\x1e, \x85 and \u2028, which would misalign ast line numbers.
    lines = io.StringIO(source, newline="").readlines()
    return Parsed(source, tree, tests, module_skip, lines, pytest_names, unittest_names), None


def _base_name(b: ast.AST) -> str:
    if isinstance(b, ast.Subscript):  # e.g. TestCase[...] generics
        b = b.value
    return b.attr if isinstance(b, ast.Attribute) else b.id if isinstance(b, ast.Name) else ""


def _is_test_class(c: ast.ClassDef) -> bool:
    """pytest collects Test* classes; unittest collects TestCase subclasses (ADR 0009).

    Bases are judged syntactically (no import resolution): any base whose final name ends in
    "TestCase" (TestCase, SimpleTestCase, SynchronousTestCase, a project's own BaseTestCase...).
    """
    return c.name.startswith("Test") or any(_base_name(b).endswith("TestCase") for b in c.bases)


def _max_depth(tree: ast.AST) -> int:
    """Iterative maximum depth (stops early), safe on inputs that overflow recursive walkers."""
    best = 0
    stack = [(tree, 1)]
    while stack:
        node, d = stack.pop()
        if d > best:
            best = d
            if best > MAX_AST_DEPTH:
                return best
        stack.extend((c, d + 1) for c in ast.iter_child_nodes(node))
    return best


def _pytestmark_skips(body: list[ast.stmt]) -> frozenset[str]:
    out = set()
    for n in body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in n.targets):
            for m in n.value.elts if isinstance(n.value, (ast.List, ast.Tuple)) else [n.value]:
                if _skip_mark(m):
                    out.add(_dump(m))
    return frozenset(out)


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
    """One check (an assert statement or a unittest self.assert* call), normalised to
    subject / operator / expected."""

    node: ast.AST  # ast.Assert, or the ast.Call of a self.assert* check
    subject: str  # dump of the subject expression
    op: str  # comparison op text, "approx", or a trivial kind
    expected: ast.AST | None
    tol: dict = field(default_factory=dict)

    @property
    def full(self) -> str:
        return _dump(self.node.test if isinstance(self.node, ast.Assert) else self.node)

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
            for name, arg in zip(("rel", "abs"), right.args[1:3]):  # approx(expected, rel, abs)
                tol.setdefault(name, _num(arg))
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


# unittest assertion methods mapped onto the same model (ADR 0009). Two-argument comparisons:
_UT_BINARY = {
    "assertEqual": "==", "assertEquals": "==", "failUnlessEqual": "==",
    "assertListEqual": "==", "assertTupleEqual": "==", "assertDictEqual": "==", "assertSetEqual": "==",
    "assertSequenceEqual": "==", "assertMultiLineEqual": "==", "assertCountEqual": "==",
    "assertNotEqual": "!=", "assertNotEquals": "!=",
    "assertGreater": ">", "assertGreaterEqual": ">=", "assertLess": "<", "assertLessEqual": "<=",
    "assertIs": "is", "assertIsNot": "is not", "assertIn": "in", "assertNotIn": "not in",
}
# Calls handled as exception checks (S3), not as Check objects:
_UT_RAISES = {"assertRaises", "assertRaisesRegex", "assertRaisesRegexp", "assertWarns", "assertWarnsRegex"}


def _is_self_assert(n: ast.AST) -> bool:
    return (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr.startswith("assert")
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "self")


def _classify_call(call: ast.Call) -> Check:
    """Map a self.assert*(...) call onto subject / op / expected."""
    name, args = call.func.attr, call.args
    kw = {k.arg: k.value for k in call.keywords if k.arg}
    if name in _UT_BINARY and len(args) >= 2:
        left, right, op = args[0], args[1], _UT_BINARY[name]
        if _is_literal(left) and not _is_literal(right):
            left, right, op = right, left, _FLIP.get(op, op)
        if op in ("is not", "!=") and isinstance(right, ast.Constant) and right.value is None:
            return Check(call, _dump(left), "not_none", None)
        return Check(call, _dump(left), op, right)
    if name in ("assertAlmostEqual", "assertAlmostEquals") and len(args) >= 2:
        left, right = args[0], args[1]
        if _is_literal(left) and not _is_literal(right):
            left, right = right, left
        if "delta" in kw:
            tol = {"abs": _num(kw["delta"])}
        else:  # default places=7: |a - b| rounded to 7 places == 0, i.e. tolerance 0.5e-7
            places = _num(kw.get("places")) if "places" in kw else (_num(args[2]) if len(args) > 2 else 7.0)
            tol = {"abs": 0.5 * 10 ** -places if places is not None else None}
        return Check(call, _dump(left), "approx", right, tol)
    if name == "assertTrue" and args:
        return Check(call, _dump(args[0]), "truthy", None)
    if name == "assertIsNotNone" and args:
        return Check(call, _dump(args[0]), "not_none", None)
    if name == "assertIsNone" and args:
        return Check(call, _dump(args[0]), "is", ast.Constant(value=None))
    if name == "assertIsInstance" and len(args) >= 2:
        return Check(call, _dump(args[0]), "isinstance", args[1])
    if name == "assertFalse" and args:
        return Check(call, _dump(args[0]), "falsy", None)
    # Project-specific helpers (assertMarkdownRenders, ...): a check whose strength is unknown;
    # it can only match itself exactly, and its removal counts for B3.
    return Check(call, _dump(call), f"custom:{name}", None)


def _checks(fn: TestFn) -> list[Check]:
    """Every check in the test body, in source order: assert statements and self.assert* calls."""
    out = []
    for n in _walk_own(fn.node):
        if isinstance(n, ast.Assert):
            out.append(_classify(n))
        elif _is_self_assert(n) and n.func.attr not in _UT_RAISES:
            out.append(_classify_call(n))
    return out


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
    if b.op == "==" and h.op == "approx" and b.expected_literal:
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
    """pytest.raises(...) and unittest self.assertRaises*/assertWarns* calls, in source order."""
    out = []
    for n in _walk_own(fn.node):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Attribute) and f.attr == "raises" and isinstance(f.value, ast.Name)
                    and f.value.id == "pytest") or (isinstance(f, ast.Name) and f.id == "raises"):
                out.append(n)
            elif _is_self_assert(n) and f.attr in _UT_RAISES:
                out.append(n)
    return out


def _match_of(call: ast.Call) -> ast.AST | None:
    """The message pattern an exception check enforces: pytest's match=, or the regex
    argument of unittest's assertRaisesRegex / assertWarnsRegex."""
    for k in call.keywords:
        if k.arg in ("match", "expected_regex"):
            return k.value
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr.endswith(("Regex", "Regexp")) and len(call.args) >= 2:
        return call.args[1]
    return None


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


_MATCH_ANY = {"", ".*", ".+", "(?s).*", "(?s).+", "^.*$"}


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
        bm, hm = _match_of(cb), _match_of(ch)
        if bm is not None and hm is None:
            reasons.append("the expected-message check was removed")
        elif (bm is not None and isinstance(hm, ast.Constant) and hm.value in _MATCH_ANY
              and _dump(bm) != _dump(hm)):
            reasons.append(f"the expected-message check was loosened to {hm.value!r}, which matches any message")
        if reasons:
            label = cb.func.attr if isinstance(cb.func, ast.Attribute) else "raises"
            label = "pytest.raises" if label == "raises" else f"self.{label}"
            out.append(Finding(
                S3, path, th.key, f"{label}: " + "; ".join(reasons) + ".",
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
        removed = [base_cases[k] for k in base_cases if k not in head_cases]
        if removed:
            added = len(head_cases - set(base_cases))
            note = f" {added} new case(s) were added, which does not restore the removed ones." if added else ""
            out.append(Finding(
                S4, path, th.key,
                f"{len(removed)} of {len(base_cases)} parametrize case(s) were removed.{note}",
                base_lines=_lines(removed[0]), head_lines=_lines(dh),
                before="\n".join(_seg(pb, e) for e in removed), after=None,
                detail={"removed_cases": [_seg(pb, e) for e in removed], "base_count": len(base_cases),
                        "head_count": len(head_cases)},
            ))
    return out


def _mark_name(d: ast.AST) -> tuple[str | None, ast.Call | None]:
    call = d if isinstance(d, ast.Call) else None
    target = d.func if call else d
    if isinstance(target, ast.Attribute) and (
        (isinstance(target.value, ast.Attribute) and target.value.attr == "mark")
        or (isinstance(target.value, ast.Name) and target.value.id == "mark")
    ):
        return target.attr, call
    return None, None


def _unittest_skip(d: ast.AST, unittest_names: frozenset[str]) -> bool:
    """@unittest.skip(...), @skipIf(<truthy const>), @skipUnless(<falsy const>), @expectedFailure."""
    call = d if isinstance(d, ast.Call) else None
    target = d.func if call else d
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "unittest":
        name = target.attr
    elif isinstance(target, ast.Name) and target.id in unittest_names:
        name = target.id
    else:
        return False
    if name in ("skip", "expectedFailure"):
        return True
    if name in ("skipIf", "skipUnless") and call is not None and call.args and isinstance(call.args[0], ast.Constant):
        return bool(call.args[0].value) == (name == "skipIf")
    return False


def _skip_mark(d: ast.AST, unittest_names: frozenset[str] = frozenset()) -> bool:
    if _unittest_skip(d, unittest_names):
        return True
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


def _is_pytest_stop(call: ast.Call, pytest_names: frozenset[str]) -> bool:
    """pytest.skip(...) / pytest.xfail(...), skip/xfail imported from pytest, or unittest's
    self.skipTest(...). Not reader.skip()."""
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr == "skipTest":
        return isinstance(f.value, ast.Name) and f.value.id == "self"
    if isinstance(f, ast.Attribute) and f.attr in ("skip", "xfail"):
        return isinstance(f.value, ast.Name) and f.value.id == "pytest"
    return isinstance(f, ast.Name) and f.id in ("skip", "xfail") and f.id in pytest_names


def _guards(fn: TestFn, pytest_names: frozenset[str] = frozenset()) -> dict[str, ast.stmt]:
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
            if _is_pytest_stop(s.value, pytest_names) and rest:
                is_guard = True
        elif isinstance(s, ast.If) and isinstance(s.test, ast.Constant) and not s.test.value and not s.orelse:
            is_guard = True
        if is_guard:
            out[_dump(s)] = s
    return out


def _b2(pb: Parsed, ph: Parsed, tb: TestFn, th: TestFn, path: str) -> list[Finding]:
    out = []
    base_marks = {_dump(d) for d in tb.node.decorator_list if _skip_mark(d, pb.unittest_names)}
    for d in th.node.decorator_list:
        if _skip_mark(d, ph.unittest_names) and _dump(d) not in base_marks:
            out.append(Finding(
                B2, path, th.key, "An unconditional skip/xfail marker was added; the test no longer gates the change.",
                head_lines=_lines(d), after="@" + _seg(ph, d),
            ))
    new_class_marks = th.class_marks - tb.class_marks
    if new_class_marks:
        out.append(Finding(
            B2, path, th.key, "A skip/xfail marker was added to the test's class; the test no longer gates the change.",
            detail={"class_marks": sorted(new_class_marks)},
        ))
    base_guards = _guards(tb, pb.pytest_names)
    for k, s in _guards(th, ph.pytest_names).items():
        if k not in base_guards:
            first = _seg(ph, s).splitlines()[0] if _seg(ph, s) else ""
            out.append(Finding(
                B2, path, th.key, "A guard was added before the test's checks, so they no longer run.",
                head_lines=(s.lineno, s.lineno), after=first,
            ))
    return out


# --------------------------------------------------------------------------- S5 and B4 (ADR 0011)

_REFLEXIVE_OPS = {"==", "is", ">=", "<=", "approx"}


def _truthy_const(n: ast.AST) -> bool:
    return isinstance(n, ast.Constant) and bool(n.value)


def _checked_expr(c: Check) -> ast.AST | None:
    """The expression a truthiness-style check evaluates: the assert's test, or assertTrue's arg."""
    if isinstance(c.node, ast.Assert):
        return c.node.test
    if isinstance(c.node, ast.Call) and c.node.func.attr == "assertTrue" and c.node.args:
        return c.node.args[0]
    return None


def _vacuous(c: Check) -> tuple[str, ast.AST | None] | None:
    """("self", None) for a comparison of an expression with itself; ("or", rest) for an `or` with a
    truthy constant operand, where rest is what the check would test without the constant."""
    if c.op in _REFLEXIVE_OPS and c.expected is not None and _dump(c.expected) == c.subject:
        return "self", None
    e = _checked_expr(c)
    if isinstance(e, ast.BoolOp) and isinstance(e.op, ast.Or) and any(_truthy_const(v) for v in e.values):
        rest = [v for v in e.values if not _truthy_const(v)]
        if not rest:
            return "or", None
        return "or", rest[0] if len(rest) == 1 else ast.BoolOp(op=ast.Or(), values=rest)
    return None


def _key(c: Check) -> tuple:
    return c.subject, c.op, _dump(c.expected)


def _replaced_by_vacuous(b: Check, h: Check, v: tuple[str, ast.AST | None]) -> bool:
    kind, rest = v
    if kind == "self":
        # Only a check against a different expected value is weakened by a self-comparison; a
        # truthiness check (assertTrue(r)) becoming a reflexivity check (assertEqual(r, r)) is not.
        return b.subject == h.subject and b.expected is not None and _dump(b.expected) != b.subject
    if rest is None:
        return False
    under = (_classify(ast.Assert(test=rest)) if isinstance(h.node, ast.Assert)
             else Check(h.node, _dump(rest), "truthy", None))
    return _key(under) == _key(b)


def _s5_pairs(rem_base: list[Check], rem_head: list[Check]) -> list[tuple[Check, Check, str]]:
    """Pair each head check that became vacuous with the non-vacuous base check it replaced, and
    take both out of further pairing. A vacuous check with no such partner was added, not swapped
    in, and weakens nothing."""
    pairs = []
    for h in list(rem_head):
        v = _vacuous(h)
        if v is None:
            continue
        # A base self-comparison (`c == c`) still exercises a custom __eq__, so only an `or`-vacuous
        # base check is treated as already vacuous.
        b = next((b for b in rem_base if (_vacuous(b) or ("",))[0] != "or" and _replaced_by_vacuous(b, h, v)), None)
        if b is None:
            continue
        rem_base.remove(b)
        rem_head.remove(h)
        why = ("The check now compares the value with itself instead of the expected value, so it no longer "
               "checks the result." if v[0] == "self"
               else "An `or` with a true constant was added to the check, so it always passes.")
        pairs.append((b, h, why))
    return pairs


_CATCHES_ASSERTION = {"AssertionError", "Exception", "BaseException"}


def _catches_assertion(t: ast.AST | None) -> bool:
    if t is None:
        return True  # bare except:
    if isinstance(t, ast.Tuple):
        return any(_catches_assertion(e) for e in t.elts)
    name = t.attr if isinstance(t, ast.Attribute) else t.id if isinstance(t, ast.Name) else None
    return name in _CATCHES_ASSERTION


def _propagates(body: list[ast.stmt]) -> bool:
    """A handler body that re-raises, or fails the test explicitly (pytest.fail / self.fail)."""
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Raise):
                return True
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Attribute) and f.attr == "fail" and isinstance(f.value, ast.Name) \
                        and f.value.id in ("pytest", "self"):
                    return True
    return False


def _swallows(node: ast.AST) -> bool:
    if isinstance(node, (ast.Try, ast.TryStar)):
        return any(_catches_assertion(h.type) and not _propagates(h.body) for h in node.handlers)
    if isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            c = item.context_expr
            if isinstance(c, ast.Call):
                name = c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", None)
                if name == "suppress" and any(_catches_assertion(a) for a in c.args):
                    return True
    return False


def _is_check(n: ast.AST) -> bool:
    return isinstance(n, ast.Assert) or (_is_self_assert(n) and n.func.attr not in _UT_RAISES)


def _swallowed(fn: TestFn) -> list[tuple[ast.AST, ast.AST]]:
    """(check, swallowing statement) for every check whose failure a surrounding try/except or
    contextlib.suppress would swallow. Only a try's body is covered, not its else/finally."""
    out: list[tuple[ast.AST, ast.AST]] = []

    def visit_one(n: ast.AST, guard: ast.AST | None) -> None:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if isinstance(n, (ast.Try, ast.TryStar)):
            g = guard or (n if _swallows(n) else None)
            for st in n.body:
                visit_one(st, g)
            for part in (*n.handlers, *n.orelse, *n.finalbody):
                visit_one(part, guard)
            return
        if isinstance(n, (ast.With, ast.AsyncWith)):
            g = guard or (n if _swallows(n) else None)
            for item in n.items:
                visit_one(item, guard)
            for st in n.body:
                visit_one(st, g)
            return
        if guard is not None and _is_check(n):
            out.append((n, guard))
        for child in ast.iter_child_nodes(n):
            visit_one(child, guard)

    for st in fn.node.body:
        visit_one(st, None)
    return out


def _check_key(n: ast.AST) -> tuple:
    """What a check checks (subject, operator, expected), ignoring its message."""
    return _key(_classify(n) if isinstance(n, ast.Assert) else _classify_call(n))


def _b4(pb: Parsed, ph: Parsed, tb: TestFn, th: TestFn, path: str) -> list[Finding]:
    """More checks are swallowed than before. Swallowed checks are matched by what they check, not
    by their text, so a reworded message inside an existing try/except is not a finding. A swap
    (one check un-swallowed, another swallowed) is not flagged."""
    base_sw, head_sw = _swallowed(tb), _swallowed(th)
    excess = len(head_sw) - len(base_sw)
    if excess <= 0:
        return []
    pool: dict[tuple, int] = defaultdict(int)
    for n, _ in base_sw:
        pool[_check_key(n)] += 1
    unmatched = []
    for n, guard in head_sw:
        k = _check_key(n)
        if pool[k] > 0:
            pool[k] -= 1
        else:
            unmatched.append((n, guard))
    base_checks: dict[tuple, ast.AST] = {}
    for c in _checks(tb):
        base_checks.setdefault(_key(c), c.node)
    out = []
    for n, guard in unmatched[:excess]:
        b = base_checks.get(_check_key(n))
        out.append(Finding(
            B4, path, th.key,
            "The check now sits inside a try/except (or suppress) that catches AssertionError and does not "
            "re-raise, so a failing check can no longer fail the test.",
            base_lines=_lines(b) if b is not None else None, head_lines=_lines(guard),
            before=_seg(pb, b) if b is not None else None, after=_seg(ph, guard),
        ))
    return out


def _assert_rules(pb: Parsed, ph: Parsed, tb: TestFn, th: TestFn, path: str) -> list[Finding]:
    out: list[Finding] = []
    base = _checks(tb)
    head = _checks(th)
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
    # 1b. S5: a check replaced by a vacuous one (ADR 0011).
    for b, h, why in _s5_pairs(rem_base, rem_head):
        out.append(Finding(
            S5, path, th.key, why, base_lines=_lines(b.node), head_lines=_lines(h.node),
            before=_seg(pb, b.node), after=_seg(ph, h.node),
        ))
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
        # A check moved into an in-file helper that still checks is not a removal (ADR 0012).
        helpers = _helpers(ph, th)
        deficit -= sum(1 for name, _ in _new_delegations(tb, th) if name in helpers and _does_check(helpers[name]))
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


# --------------------------------------------------------------------------- T1-T5 (ADR 0012)


def _subject_node(c: Check) -> ast.AST | None:
    """The expression a check is about (the side that is not the expected literal)."""
    n = c.node
    if isinstance(n, ast.Assert):
        t = n.test
        if isinstance(t, ast.Compare) and len(t.ops) == 1:
            left, right = t.left, t.comparators[0]
            return right if (_is_literal(left) and not _is_literal(right)) else left
        if isinstance(t, ast.Call) and isinstance(t.func, ast.Name) and t.func.id == "isinstance" and len(t.args) == 2:
            return t.args[0]
        return t
    if isinstance(n, ast.Call) and n.args:
        if len(n.args) >= 2 and _is_literal(n.args[0]) and not _is_literal(n.args[1]):
            return n.args[1]
        return n.args[0]
    return None


def _literal_args_changed(b: ast.AST | None, h: ast.AST | None) -> bool:
    """Same callee, same argument shape, and only literal arguments differ."""
    if not (isinstance(b, ast.Call) and isinstance(h, ast.Call)) or _dump(b.func) != _dump(h.func):
        return False
    if len(b.args) != len(h.args) or [k.arg for k in b.keywords] != [k.arg for k in h.keywords]:
        return False
    pairs = list(zip(b.args, h.args)) + [(x.value, y.value) for x, y in zip(b.keywords, h.keywords)]
    diff = [(x, y) for x, y in pairs if _dump(x) != _dump(y)]
    return bool(diff) and all(_is_literal(x) and _is_literal(y) for x, y in diff)


def _unmatched_checks(tb: TestFn, th: TestFn) -> tuple[list[Check], list[Check]]:
    base, head = _checks(tb), _checks(th)
    pool: dict[str, int] = defaultdict(int)
    for h in head:
        pool[h.full] += 1
    rem_base = []
    for b in base:
        if pool[b.full]:
            pool[b.full] -= 1
        else:
            rem_base.append(b)
    seen: dict[str, int] = defaultdict(int)
    for b in base:
        seen[b.full] += 1
    rem_head = []
    for h in head:
        if seen[h.full]:
            seen[h.full] -= 1
        else:
            rem_head.append(h)
    return rem_base, rem_head


def _names_in(n: ast.AST | None) -> set[str]:
    return {x.id for x in ast.walk(n) if isinstance(x, ast.Name)} if n is not None else set()


def _t1(pb: Parsed, ph: Parsed, tb: TestFn, th: TestFn, path: str) -> list[Finding]:
    out = []
    rem_base, rem_head = _unmatched_checks(tb, th)
    used: set[int] = set()
    for b in rem_base:
        sb = _subject_node(b)
        for h in rem_head:
            if id(h) in used or h.op != b.op or not _literal_args_changed(sb, _subject_node(h)):
                continue
            same = _dump(b.expected) == _dump(h.expected)
            both = not same and b.expected_literal and h.expected_literal
            if not (same or both):
                continue
            used.add(id(h))
            out.append(Finding(
                T1, path, th.key,
                "The test's inputs changed but the value it asserts did not, so it no longer checks the case it "
                "checked before." if same else
                "The test's inputs and its expected value changed together, so it now checks a different case.",
                base_lines=_lines(b.node), head_lines=_lines(h.node), before=_seg(pb, b.node), after=_seg(ph, h.node),
                detail={"both_changed": both, "via": "check"},
            ))
            break
    # A local fed by a call whose literal inputs changed, while every check on it stayed the same.
    def assigns(fn: TestFn) -> dict[str, list[ast.Assign]]:
        d: dict[str, list[ast.Assign]] = defaultdict(list)
        for n in _walk_own(fn.node):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                    and isinstance(n.value, ast.Call):
                d[n.targets[0].id].append(n)
        return d

    ab, ah = assigns(tb), assigns(th)
    bchecks, hchecks = _checks(tb), _checks(th)
    for name in sorted(set(ab) & set(ah)):
        if len(ab[name]) != 1 or len(ah[name]) != 1:
            continue
        b, h = ab[name][0], ah[name][0]
        if not _literal_args_changed(b.value, h.value):
            continue
        on_b = sorted(c.full for c in bchecks if name in _names_in(_subject_node(c)))
        on_h = sorted(c.full for c in hchecks if name in _names_in(_subject_node(c)))
        if on_b and on_b == on_h:
            out.append(Finding(
                T1, path, th.key,
                f"The inputs that produce {name!r} changed but every check on {name!r} stayed the same, so the test "
                "no longer checks the case it checked before.",
                base_lines=_lines(b), head_lines=_lines(h), before=_seg(pb, b), after=_seg(ph, h),
                detail={"both_changed": False, "via": "local", "local": name},
            ))
    return out


def _doc_stripped(fn: ast.AST) -> list[ast.stmt]:
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and \
            isinstance(body[0].value.value, str):
        return body[1:]
    return body


def _always_passes(fn: ast.AST) -> bool:
    """A helper that cannot fail: no assert, raise, call, yield or await anywhere, only constant returns."""
    if getattr(fn, "decorator_list", None):
        return False
    for stmt in _doc_stripped(fn):
        for n in ast.walk(stmt):
            if isinstance(n, (ast.Assert, ast.Raise, ast.Call, ast.Yield, ast.YieldFrom, ast.Await)):
                return False
            if isinstance(n, ast.Return) and n.value is not None and not isinstance(n.value, ast.Constant):
                return False
    return True


def _does_check(fn: ast.AST) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, (ast.Assert, ast.Raise)) or _is_self_assert(n):
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "fail":
            return True
    return False


def _helpers(p: Parsed, fn: TestFn) -> dict[str, ast.AST]:
    """In-file helpers a test can delegate to: module-level non-test functions ("name") and methods
    of the test's own class ("self.name")."""
    out: dict[str, ast.AST] = {}
    for n in p.tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("test"):
            out[n.name] = n
    parts = fn.key.split("::")
    if len(parts) == 3:
        cls = next((n for n in p.tree.body if isinstance(n, ast.ClassDef) and n.name == parts[1]), None)
        for n in cls.body if cls else []:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("test"):
                out[f"self.{n.name}"] = n
    return out


def _delegations(fn: TestFn) -> list[tuple[str, ast.Call]]:
    """Calls used as a check: a bare `helper(...)` statement or `assert helper(...)`."""
    out = []
    for n in _walk_own(fn.node):
        call = n.value if isinstance(n, ast.Expr) else n.test if isinstance(n, ast.Assert) else None
        if not isinstance(call, ast.Call):
            continue
        f = call.func
        if isinstance(f, ast.Name):
            out.append((f.id, call))
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self" \
                and not f.attr.startswith(("assert", "fail", "skipTest", "subTest")):
            out.append((f"self.{f.attr}", call))
    return out


def _new_delegations(tb: TestFn, th: TestFn) -> list[tuple[str, ast.Call]]:
    pool: dict[str, int] = defaultdict(int)
    for _, c in _delegations(tb):
        pool[_dump(c)] += 1
    out = []
    for name, c in _delegations(th):
        if pool[_dump(c)]:
            pool[_dump(c)] -= 1
        else:
            out.append((name, c))
    return out


def _t2(pb: Parsed, ph: Parsed, tb: TestFn, th: TestFn, path: str) -> list[Finding]:
    helpers = _helpers(ph, th)
    out = []
    for name, call in _new_delegations(tb, th):
        h = helpers.get(name)
        if h is not None and _always_passes(h):
            out.append(Finding(
                T2, path, th.key,
                f"The check now goes through {name}(), which is defined in this file and can never fail, so the test "
                "no longer checks anything there.",
                head_lines=_lines(call), after=_seg(ph, call) + "\n" + _seg(ph, h).splitlines()[0],
                detail={"helper": name, "change": "delegated"},
            ))
    return out


def _hollowed(pb: Parsed, ph: Parsed, path: str) -> list[Finding]:
    """Helpers that checked something at base and can never fail at head, for every test calling them."""
    out = []
    for key, th in sorted(ph.tests.items()):
        tb = pb.tests.get(key)
        if tb is None:
            continue
        hb, hh = _helpers(pb, tb), _helpers(ph, th)
        calls = {name for name, _ in _delegations(th)}
        for name in sorted(calls):
            if name in hb and name in hh and _does_check(hb[name]) and _always_passes(hh[name]):
                out.append(Finding(
                    T2, path, key,
                    f"The helper {name}() that this test relies on was hollowed out: it checked something before and "
                    "now can never fail.",
                    base_lines=_lines(hb[name]), head_lines=_lines(hh[name]),
                    before=_seg(pb, hb[name]), after=_seg(ph, hh[name]),
                    detail={"helper": name, "change": "hollowed"},
                ))
    return out


def _module_imports(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for s in tree.body:
        if isinstance(s, ast.Import):
            for a in s.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(s, ast.ImportFrom) and s.module and s.level == 0:
            for a in s.names:
                out[a.asname or a.name] = f"{s.module}.{a.name}"
    return out


def _dotted(n: ast.AST, imports: dict[str, str]) -> str | None:
    parts = []
    while isinstance(n, ast.Attribute):
        parts.append(n.attr)
        n = n.value
    if not isinstance(n, ast.Name):
        return None
    root = imports.get(n.id, n.id)
    return ".".join([root] + list(reversed(parts)))


def _patch_target(call: ast.Call, imports: dict[str, str]) -> str | None:
    """The dotted symbol a monkeypatch.setattr / mock.patch / patch.object call replaces."""
    f = call.func
    chain = []
    x = f
    while isinstance(x, ast.Attribute):
        chain.append(x.attr)
        x = x.value
    head = x.id if isinstance(x, ast.Name) else ""
    args = call.args
    s0 = args[0].value if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str) else None
    if chain[:1] == ["setattr"] and "monkeypatch" in head:
        if s0 is not None and len(args) == 2:
            return s0
        if len(args) >= 3 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
            base = _dotted(args[0], imports)
            return f"{base}.{args[1].value}" if base else None
        return None
    if chain[:2] == ["object", "patch"] or (chain[:1] == ["object"] and head == "patch"):
        if len(args) >= 2 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
            base = _dotted(args[0], imports)
            return f"{base}.{args[1].value}" if base else None
        return None
    if (chain[:1] == ["patch"] or (not chain and head == "patch")) and s0 is not None:
        return s0
    return None


def _t3(pb: Parsed, ph: Parsed, tb: TestFn, th: TestFn, path: str) -> list[Finding]:
    imports = _module_imports(ph.tree)

    def patches(p: Parsed, fn: TestFn) -> list[tuple[str, ast.Call]]:
        imp = _module_imports(p.tree)
        out = []
        for n in list(_walk_own(fn.node)) + [d for d in fn.node.decorator_list if isinstance(d, ast.Call)]:
            if isinstance(n, ast.Call):
                t = _patch_target(n, imp)
                if t:
                    out.append((t, n))
        return out

    base = {(t, _dump(c)) for t, c in patches(pb, tb)}
    new = [(t, c) for t, c in patches(ph, th) if (t, _dump(c)) not in base]
    if not new:
        return []
    patch_calls = {id(c) for _, c in new}
    called = set()
    for c in _checks(th):
        for n in ast.walk(c.node):
            if isinstance(n, ast.Call) and id(n) not in patch_calls:
                d = _dotted(n.func, imports)
                if d:
                    called.add(d)
    for n in _walk_own(th.node):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            d = _dotted(n.value.func, imports)
            if d:
                called.add(d)
    out = []
    for target, call in new:
        if target in called:
            out.append(Finding(
                T3, path, th.key,
                f"The test now replaces {target} with a stand-in, and {target} is the function its checks call, so "
                "the checks test the stand-in instead of the code.",
                head_lines=_lines(call), after=_seg(ph, call), detail={"target": target},
            ))
    return out


def _is_empty_iter(n: ast.AST) -> bool:
    if isinstance(n, (ast.List, ast.Tuple, ast.Set)) and not n.elts:
        return True
    if isinstance(n, ast.Dict) and not n.keys:
        return True
    if isinstance(n, ast.Constant) and isinstance(n.value, (str, bytes)) and not n.value:
        return True
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and not n.keywords:
        if n.func.id in ("list", "tuple", "set", "dict", "frozenset") and not n.args:
            return True
        if n.func.id == "range":
            vals = [_num(a) for a in n.args]
            if vals and all(v is not None for v in vals):
                if len(vals) == 1:
                    return vals[0] <= 0
                step = vals[2] if len(vals) > 2 else 1
                return (step > 0 and vals[0] >= vals[1]) or (step < 0 and vals[0] <= vals[1])
    if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice):
        s = n.slice
        upper = _num(s.upper) if s.upper is not None else None
        lower = _num(s.lower) if s.lower is not None else 0
        return s.step is None and upper is not None and lower is not None and upper <= lower and upper >= 0
    return False


def _has_check(body: list[ast.stmt]) -> bool:
    return any(isinstance(x, ast.Assert) or _is_self_assert(x) for s in body for x in ast.walk(s))


def _t4(pb: Parsed, ph: Parsed, tb: TestFn, th: TestFn, path: str) -> list[Finding]:
    out = []

    def loops(fn: TestFn) -> dict[str, list[ast.For]]:
        d: dict[str, list] = defaultdict(list)
        for n in _walk_own(fn.node):
            if isinstance(n, (ast.For, ast.AsyncFor)) and _has_check(n.body):
                d[_dump(n.target)].append(n)
        return d

    lb, lh = loops(tb), loops(th)
    for target in sorted(set(lb) & set(lh)):
        for b, h in zip(lb[target], lh[target]):
            if _dump(b.iter) == _dump(h.iter):
                continue
            if _is_empty_iter(h.iter) and not _is_empty_iter(b.iter):
                out.append(Finding(
                    T4, path, th.key, "The loop that holds the test's checks now iterates over nothing, so its checks "
                                      "never run.",
                    base_lines=_lines(b.iter), head_lines=_lines(h.iter), before=_seg(pb, b.iter),
                    after=_seg(ph, h.iter), detail={"change": "emptied"},
                ))
            elif isinstance(b.iter, (ast.List, ast.Tuple, ast.Set)) and isinstance(h.iter, (ast.List, ast.Tuple, ast.Set)):
                hd = {_dump(e) for e in h.iter.elts}
                gone = [e for e in b.iter.elts if _dump(e) not in hd]
                if gone:
                    out.append(Finding(
                        T4, path, th.key, f"{len(gone)} of {len(b.iter.elts)} case(s) were removed from the loop that "
                                          "holds the test's checks.",
                        base_lines=_lines(b.iter), head_lines=_lines(h.iter), before=_seg(pb, b.iter),
                        after=_seg(ph, h.iter), detail={"change": "reduced", "removed": [_seg(pb, e) for e in gone]},
                    ))

    def params(fn: TestFn) -> dict[str, ast.Call]:
        d = {}
        for dec in fn.node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "parametrize" \
                    and len(dec.args) >= 2:
                d[_dump(dec.args[0])] = dec
        return d

    pbm, phm = params(tb), params(th)
    for k in sorted(set(pbm) & set(phm)):
        bv, hv = pbm[k].args[1], phm[k].args[1]
        if _is_empty_iter(hv) and not isinstance(hv, (ast.List, ast.Tuple)) and not _is_empty_iter(bv):
            out.append(Finding(
                T4, path, th.key, "The parametrize argument values were replaced by an empty collection, so the test "
                                  "runs no cases.",
                base_lines=_lines(bv), head_lines=_lines(hv), before=_seg(pb, bv), after=_seg(ph, hv),
                detail={"change": "parametrize_emptied"},
            ))
    return out


def _t5(pb: Parsed, ph: Parsed, tb: TestFn, th: TestFn, path: str) -> list[Finding]:
    rb, rh = _raises_calls(tb), _raises_calls(th)
    if len(rb) <= len(rh):
        return []
    base_checks, head_checks = _checks(tb), _checks(th)
    if (len(base_checks) + len(rb)) - (len(head_checks) + len(rh)) > 0:
        return []  # a removal; B3 reports it
    head_raises = defaultdict(int)
    for c in rh:
        head_raises[_dump(c)] += 1
    removed = []
    for c in rb:
        if head_raises[_dump(c)]:
            head_raises[_dump(c)] -= 1
        else:
            removed.append(c)
    _, new_checks = _unmatched_checks(tb, th)
    if not removed or not new_checks:
        return []
    return [Finding(
        T5, path, th.key, "An exception check was replaced by a value check, so the test no longer requires the error "
                          "to be raised.",
        base_lines=_lines(removed[0]), head_lines=_lines(new_checks[0].node), before=_seg(pb, removed[0]),
        after=_seg(ph, new_checks[0].node), detail={"removed_raises": len(removed)},
    )]


def _test_body_rules(pb: Parsed, ph: Parsed, tb: TestFn, th: TestFn, path: str) -> list[Finding]:
    return (_t1(pb, ph, tb, th, path) + _t2(pb, ph, tb, th, path) + _t3(pb, ph, tb, th, path)
            + _t4(pb, ph, tb, th, path) + _t5(pb, ph, tb, th, path))


# --------------------------------------------------------------------------- file level


def analyze_file(
    path: str,
    base_source: str | None,
    head_source: str | None,
    lim: _limits.Limits | None = None,
) -> FileResult:
    """Compare one test file between base and head. None means the file is absent on that side."""
    lim = lim or _limits.Limits.cli()
    try:
        return _analyze(path, _strip_bom(base_source), _strip_bom(head_source), lim)
    except (RecursionError, MemoryError) as exc:
        res = FileResult(path)
        res.gaps.append(Gap("input_limit", f"{type(exc).__name__} while analyzing; input too complex", file=path))
        return res


def _strip_bom(src: str | None) -> str | None:
    # Python accepts a UTF-8 BOM in source files; ast.parse on a str does not.
    return src[1:] if src is not None and src.startswith("\ufeff") else src


def _analyze(path: str, base_source: str | None, head_source: str | None, lim: _limits.Limits) -> FileResult:
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
        if th.class_marks == tb.class_marks and (
            pb.text(tb.node) == ph.text(th.node) or _dump(tb.node) == _dump(th.node)
        ):
            continue
        res.findings.extend(_assert_rules(pb, ph, tb, th, path))
        res.findings.extend(_s3(pb, ph, tb, th, path))
        res.findings.extend(_s4(pb, ph, tb, th, path))
        res.findings.extend(_b2(pb, ph, tb, th, path))
        res.findings.extend(_b4(pb, ph, tb, th, path))
        res.findings.extend(_test_body_rules(pb, ph, tb, th, path))
    res.findings.extend(_hollowed(pb, ph, path))
    if ph.module_skip and not pb.module_skip:
        for key in sorted(k for k in ph.tests if k in pb.tests):
            res.findings.append(Finding(
                B2, path, key, "A module-level pytestmark skip was added; no test in this file gates the change.",
                detail={"module": True},
            ))
    res.findings.sort(key=Finding.sort_key)
    return res


def is_test_path(path: str) -> bool:
    """pytest-named files (test_*.py, *_test.py), Django-style tests.py, and any module inside a
    tests/ directory (ADR 0009). conftest.py and __init__.py are support files, not tests. A singular
    test/ directory is not enough on its own: it is often framework code (django/test/testcases.py)."""
    p = path.replace("\\", "/")
    parts = p.split("/")
    name = parts[-1]
    if not name.endswith(".py") or name in ("conftest.py", "__init__.py"):
        return False
    if name.startswith("test_") or name.endswith("_test.py") or name == "tests.py":
        return True
    return "tests" in parts[:-1]


def is_collected_name(path: str) -> bool:
    """Would default test runners still collect this file by name? pytest: test_*.py / *_test.py;
    unittest/Django discovery: test*.py (which includes tests.py). Narrower than is_test_path, which
    answers "is this test code?". Custom runner configuration is not read."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test") or name.endswith("_test.py"))
