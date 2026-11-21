"""Harness integrity rules H1-H7 (ADR 0012). Stdlib only, deterministic, never executes input.

Each rule compares a file's base and head versions from a Changeset and reports what the change
did to the machinery that runs and judges the tests: conftest hooks (H1), plugin loading (H2),
import-time harness files and shadowing modules (H3), pytest test selection (H4), CI test steps
(H5), coverage requirements (H6) and test dependencies (H7). Findings are repository-wide
(`test` is None); their severity is set by contract.link_and_grade from the policy in model.py.
"""

from __future__ import annotations

import ast
import configparser
import difflib
import re
import shlex
import tomllib
from dataclasses import dataclass, field

from . import limits as _limits
from . import yamlsubset
from .changeset import CI, CONFIG, CONFTEST, PYTEST_CONFIG_NAMES, SOURCE, TEST, Changeset, FileChange, is_requirements
from .model import H1, H2, H3, H4, H5, H6, H7, Finding, Gap
from .stdlib_names import STDLIB_NAMES

# --------------------------------------------------------------------------- shared helpers

# pytest 9.2 hookspecs (_pytest/hookspec.py) that decide outcomes or collection.
OUTCOME_HOOKS = frozenset({
    "pytest_runtest_makereport", "pytest_runtest_call", "pytest_runtest_protocol", "pytest_runtest_setup",
    "pytest_runtest_teardown", "pytest_pyfunc_call", "pytest_runtestloop", "pytest_report_teststatus",
    "pytest_runtest_logreport", "pytest_sessionfinish", "pytest_report_to_serializable",
    "pytest_report_from_serializable", "pytest_fixture_setup", "pytest_cmdline_main",
})
COLLECTION_HOOKS = frozenset({
    "pytest_collection_modifyitems", "pytest_ignore_collect", "pytest_pycollect_makeitem",
    "pytest_pycollect_makemodule", "pytest_collect_file", "pytest_collect_directory", "pytest_collection",
    "pytest_make_collect_report", "pytest_generate_tests", "pytest_markeval_namespace",
})
CONFIG_HOOKS = frozenset({"pytest_configure", "pytest_cmdline_parse", "pytest_load_initial_conftests",
                          "pytest_sessionstart", "pytest_collection_finish"})
_CONFIG_ATTRS = frozenset({"option", "args", "pluginmanager", "inicfg", "known_args_namespace", "_inicache",
                           "invocation_params"})
_PM_CALLS = frozenset({"register", "unregister", "set_blocked", "import_plugin", "consider_module"})
_HARNESS_MODULES = frozenset({"pytest", "_pytest", "pluggy"})
TEST_DEPENDENCY_MODULES = frozenset({"pytest", "_pytest", "pluggy", "py", "iniconfig", "hypothesis", "mock",
                                     "coverage", "pytest_cov", "xdist", "pytest_mock"})


def _clip(text: str | None, n: int = 400) -> str | None:
    if text is None:
        return None
    return text if len(text) <= n else text[: n - 3] + "..."


def _name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _dir(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _is_root(path: str) -> bool:
    return "/" not in path


def _parse_py(path: str, text: str, lim: _limits.Limits) -> tuple[ast.Module | None, Gap | None]:
    try:
        _limits.check(text, lim)
    except _limits.LimitExceeded as exc:
        return None, Gap("input_limit", str(exc), file=path)
    except _limits.Untokenizable as exc:
        return None, Gap("unparseable", str(exc), file=path)
    try:
        tree = ast.parse(text, filename=path)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        return None, Gap("unparseable", f"{type(exc).__name__}: {exc}", file=path)
    from .analyzer import MAX_AST_DEPTH, _max_depth

    if _max_depth(tree) > MAX_AST_DEPTH:
        return None, Gap("input_limit", f"syntax tree depth exceeds {MAX_AST_DEPTH}", file=path)
    return tree, None


def _size_gap(path: str, text: str, lim: _limits.Limits) -> Gap | None:
    size = len(text.encode("utf-8", errors="surrogatepass"))
    if size > lim.max_bytes:
        return Gap("input_limit", f"{size} bytes exceeds {lim.max_bytes}", file=path)
    return None


def _dump(n: ast.AST) -> str:
    return ast.dump(n, annotate_fields=False)


def _seg_lines(text: str, node: ast.AST, n: int = 12) -> str:
    lines = text.splitlines()
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    chunk = lines[start - 1:(node.end_lineno or node.lineno)]
    if len(chunk) > n:
        chunk = chunk[:n] + ["..."]
    return "\n".join(chunk)


def _top_statements(body: list[ast.stmt]):
    """Module-level statements, descending into if/try/with blocks but not into defs or classes."""
    for s in body:
        yield s
        if isinstance(s, (ast.If, ast.With, ast.AsyncWith)):
            yield from _top_statements(s.body)
            yield from _top_statements(getattr(s, "orelse", []))
        elif isinstance(s, (ast.Try, ast.TryStar)):
            for part in (s.body, s.orelse, s.finalbody, *[h.body for h in s.handlers]):
                yield from _top_statements(part)


def _imports(tree: ast.Module) -> dict[str, str]:
    """Local name -> dotted module path for module-level imports."""
    out: dict[str, str] = {}
    for s in _top_statements(tree.body):
        if isinstance(s, ast.Import):
            for a in s.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(s, ast.ImportFrom) and s.module and s.level == 0:
            for a in s.names:
                out[a.asname or a.name] = f"{s.module}.{a.name}"
    return out


def _root_module(expr: ast.AST, imports: dict[str, str]) -> str | None:
    while isinstance(expr, (ast.Attribute, ast.Subscript, ast.Call)):
        expr = expr.value if not isinstance(expr, ast.Call) else expr.func
    if isinstance(expr, ast.Name) and expr.id in imports:
        return imports[expr.id].split(".")[0]
    return None


def _attr_chain(expr: ast.AST) -> list[str]:
    out = []
    while isinstance(expr, ast.Attribute):
        out.append(expr.attr)
        expr = expr.value
    return out


# --------------------------------------------------------------------------- H1, H2 (conftest)


@dataclass
class _Conftest:
    hooks: dict[str, tuple[str, ast.AST]] = field(default_factory=dict)
    collect_ignore: set[str] = field(default_factory=set)
    collect_ignore_opaque: set[str] = field(default_factory=set)
    exits: list[tuple[str, ast.AST]] = field(default_factory=list)
    tamper: list[tuple[str, ast.AST]] = field(default_factory=list)
    config_writes: dict[str, list[tuple[str, ast.AST]]] = field(default_factory=dict)
    plugins: set[str] = field(default_factory=set)
    plugins_opaque: set[str] = field(default_factory=set)


def _strings(n: ast.AST) -> list[str] | None:
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return [n.value]
    if isinstance(n, (ast.List, ast.Tuple, ast.Set)):
        out = []
        for e in n.elts:
            s = _strings(e)
            if s is None:
                return None
            out.extend(s)
        return out
    return None


def _module_list_assign(s: ast.stmt, names: tuple[str, ...]) -> tuple[str, ast.AST] | None:
    """(name, value) for `name = ...`, `name += ...`, `name: T = ...` or `name.append/extend(...)`."""
    if isinstance(s, ast.Assign):
        for t in s.targets:
            if isinstance(t, ast.Name) and t.id in names:
                return t.id, s.value
    elif isinstance(s, (ast.AugAssign, ast.AnnAssign)) and isinstance(s.target, ast.Name) and s.target.id in names:
        if s.value is not None:
            return s.target.id, s.value
    elif isinstance(s, ast.Expr) and isinstance(s.value, ast.Call):
        f = s.value.func
        if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in names
                and f.attr in ("append", "extend", "insert", "add", "update") and s.value.args):
            arg = s.value.args[-1]
            return f.value.id, (ast.List(elts=[arg]) if f.attr in ("append", "insert", "add") else arg)
    return None


def _conftest_model(tree: ast.Module) -> _Conftest:
    m = _Conftest()
    imports = _imports(tree)
    for s in _top_statements(tree.body):
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name.startswith("pytest_"):
            m.hooks[s.name] = (_dump(s), s)
            if s.name in CONFIG_HOOKS:
                m.config_writes[s.name] = _config_writes(s)
        hit = _module_list_assign(s, ("collect_ignore", "collect_ignore_glob"))
        if hit:
            vals = _strings(hit[1])
            if vals is None:
                m.collect_ignore_opaque.add(_dump(s))
            else:
                m.collect_ignore.update(f"{hit[0]}:{v}" for v in vals)
        hit = _module_list_assign(s, ("pytest_plugins",))
        if hit:
            vals = _strings(hit[1])
            if vals is None:
                m.plugins_opaque.add(_dump(s))
            else:
                m.plugins.update(vals)
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            chain = _attr_chain(f)
            if isinstance(f, ast.Attribute) and chain and chain[0] in ("_exit", "exit") and \
                    _root_module(f, imports) in ("os", "sys"):
                m.exits.append((_dump(n), n))
            elif isinstance(f, ast.Name) and f.id == "setattr" and n.args and \
                    _root_module(n.args[0], imports) in _HARNESS_MODULES:
                m.tamper.append((_dump(n), n))
            elif isinstance(f, ast.Attribute) and f.attr == "setattr" and n.args and isinstance(n.args[0], ast.Constant) \
                    and isinstance(n.args[0].value, str) and n.args[0].value.split(".")[0] in _HARNESS_MODULES:
                m.tamper.append((_dump(n), n))
        targets = []
        if isinstance(n, ast.Assign):
            targets = n.targets
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            targets = [n.target]
        elif isinstance(n, ast.Delete):
            targets = n.targets
        for t in targets:
            if isinstance(t, ast.Attribute) and _root_module(t, imports) in _HARNESS_MODULES:
                m.tamper.append((_dump(n), n))
    return m


def _config_writes(fn: ast.AST) -> list[tuple[str, ast.AST]]:
    """Assignments into config.option / .args / .inicfg ..., and plugin-manager or args mutations."""
    out = []
    for n in ast.walk(fn):
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, (ast.AugAssign,
                                                                                           ast.AnnAssign)) else []
        for t in targets:
            if any(a in _CONFIG_ATTRS for a in _attr_chain(t)[1:] + ([t.attr] if isinstance(t, ast.Attribute) else [])):
                out.append((_dump(n), n))
                break
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            chain = _attr_chain(n.func)
            if (n.func.attr in _PM_CALLS and "pluginmanager" in chain) or (
                    n.func.attr in ("append", "extend", "insert", "remove", "clear") and "args" in chain[1:]):
                out.append((_dump(n), n))
    return out


def _multiset_new(base: list[tuple[str, ast.AST]], head: list[tuple[str, ast.AST]]) -> list[ast.AST]:
    pool: dict[str, int] = {}
    for d, _ in base:
        pool[d] = pool.get(d, 0) + 1
    out = []
    for d, n in head:
        if pool.get(d, 0) > 0:
            pool[d] -= 1
        else:
            out.append(n)
    return out


def _h1_h2(fc: FileChange, lim: _limits.Limits) -> tuple[list[Finding], list[Gap]]:
    if fc.head_text is None:
        return [], []
    head, gap = _parse_py(fc.path, fc.head_text, lim)
    if gap:
        return [], [gap]
    base = None
    if fc.base_text is not None:
        base, gap = _parse_py(fc.path, fc.base_text, lim)
        if gap:
            return [], [gap]
    hb = _conftest_model(base) if base is not None else _Conftest()
    hh = _conftest_model(head)
    out: list[Finding] = []
    p, ht, bt = fc.path, fc.head_text, fc.base_text or ""
    new_file = " in a new conftest.py" if base is None else ""
    for name in sorted(hh.hooks):
        d, node = hh.hooks[name]
        if name not in OUTCOME_HOOKS | COLLECTION_HOOKS:
            continue
        if name in hb.hooks and hb.hooks[name][0] == d:
            continue
        impact = "outcome" if name in OUTCOME_HOOKS else "collection"
        verb = "changed" if name in hb.hooks else "added"
        what = ("decides test outcomes and reports, so it can turn failures into passes" if impact == "outcome"
                else "decides which tests are collected and run, so it can drop tests silently")
        out.append(Finding(
            H1, p, None, f"The pytest hook {name} was {verb}{new_file}; this hook {what}.",
            base_lines=(hb.hooks[name][1].lineno, hb.hooks[name][1].end_lineno) if name in hb.hooks else None,
            head_lines=(node.lineno, node.end_lineno),
            before=_clip(_seg_lines(bt, hb.hooks[name][1])) if name in hb.hooks else None,
            after=_clip(_seg_lines(ht, node)), detail={"impact": impact, "hook": name}))
    for name in sorted(hh.config_writes):
        for node in _multiset_new(hb.config_writes.get(name, []), hh.config_writes[name]):
            out.append(Finding(
                H1, p, None, f"{name} now rewrites pytest's options, arguments or plugin manager at start-up, which "
                             "can change which tests run or how they are judged.",
                head_lines=(node.lineno, node.end_lineno), after=_clip(_seg_lines(ht, node, 3)),
                detail={"impact": "collection", "hook": name}))
    added_ignore = sorted(hh.collect_ignore - hb.collect_ignore)
    if added_ignore or (hh.collect_ignore_opaque - hb.collect_ignore_opaque):
        shown = [x.split(":", 1)[1] for x in added_ignore]
        out.append(Finding(
            H1, p, None, "collect_ignore / collect_ignore_glob now excludes "
                         + (", ".join(shown) if shown else "paths computed at run time")
                         + " from collection, so the tests there never run.",
            detail={"impact": "collection", "added": shown}))
    for node in _multiset_new([(d, n) for d, n in hb.exits], hh.exits):
        out.append(Finding(
            H1, p, None, "A process exit (os._exit / sys.exit) was added to conftest.py; it can end pytest with any "
                         "exit status before failures are reported.",
            head_lines=(node.lineno, node.end_lineno), after=_clip(_seg_lines(ht, node, 3)),
            detail={"impact": "process_exit"}))
    for node in _multiset_new(hb.tamper, hh.tamper):
        out.append(Finding(
            H1, p, None, "conftest.py now assigns attributes on pytest, _pytest or pluggy internals, which can change "
                         "how results are produced or reported.",
            head_lines=(node.lineno, node.end_lineno), after=_clip(_seg_lines(ht, node, 3)),
            detail={"impact": "internals"}))
    added_plugins = sorted(hh.plugins - hb.plugins)
    if added_plugins or (hh.plugins_opaque - hb.plugins_opaque):
        out.append(Finding(
            H2, p, None, "pytest_plugins now loads " + (", ".join(added_plugins) or "plugins computed at run time")
                         + "; a plugin runs inside pytest and can change collection and results.",
            detail={"plugins": added_plugins}))
    return out, []


def _h2_module(fc: FileChange, lim: _limits.Limits) -> tuple[list[Finding], list[Gap]]:
    """pytest_plugins in a test module (pytest honours it there too)."""
    if fc.head_text is None:
        return [], []
    head, gap = _parse_py(fc.path, fc.head_text, lim)
    if gap or head is None:
        return [], []  # the test analyzer reports the gap
    base = _parse_py(fc.path, fc.base_text, lim)[0] if fc.base_text is not None else None
    hb = _conftest_model(base).plugins if base is not None else set()
    hh = _conftest_model(head).plugins
    added = sorted(hh - hb)
    if not added:
        return [], []
    return [Finding(H2, fc.path, None, f"pytest_plugins in this test module now loads {', '.join(added)}; a plugin "
                                       "runs inside pytest and can change collection and results.",
                    detail={"plugins": added})], []


# --------------------------------------------------------------------------- H3


def _packages(paths: tuple[str, ...] | None) -> set[str]:
    out = set()
    for p in paths or ():
        parts = p.split("/")
        if len(parts) == 2 and parts[1] == "__init__.py" and parts[0] not in ("tests", "test"):
            out.add(parts[0])
        elif len(parts) == 3 and parts[0] == "src" and parts[2] == "__init__.py" and parts[1] not in ("tests", "test"):
            out.add(parts[1])
    return out


def _on_import_path(dirpath: str, known: set[str]) -> bool:
    """Directories pytest or a plain checkout put on sys.path: the root, src/, and test directories
    that are not packages (pytest's rootdir-relative `prepend` import mode inserts them)."""
    if dirpath in ("", "src"):
        return True
    parts = dirpath.split("/")
    return ("tests" in parts or "test" in parts) and f"{dirpath}/__init__.py" not in known


def _h3(cs: Changeset) -> list[Finding]:
    out = []
    known = set(cs.base_paths or ()) | {f.path for f in cs.files if f.head_text is not None}
    pkgs = _packages(cs.base_paths)
    for fc in cs.files:
        if fc.head_text is None or fc.status == "U":
            continue
        name = _name(fc.path)
        if name.endswith(".pth") or name in ("sitecustomize.py", "usercustomize.py"):
            verb = "added" if fc.base_text is None else "changed"
            if fc.base_text is not None and fc.base_text == fc.head_text:
                continue
            out.append(Finding(
                H3, fc.path, None, f"{name} was {verb}. Python runs it when the interpreter starts (from a site "
                                   "directory or the path), before any test, so it can change what the tests see.",
                after=_clip(fc.head_text), detail={"kind": "startup"}))
            continue
        if fc.base_text is not None and fc.status != "R":
            continue
        if name == "__init__.py":
            mod, where = _name(_dir(fc.path)), _dir(_dir(fc.path))
        elif name.endswith(".py"):
            mod, where = name[:-3], _dir(fc.path)
        else:
            continue
        if not mod:
            continue
        shadows = ("the package under test" if mod in pkgs and not fc.path.startswith((f"{mod}/", f"src/{mod}/"))
                   else "a test dependency" if mod in TEST_DEPENDENCY_MODULES
                   else "a standard-library module" if mod in STDLIB_NAMES else None)
        if shadows is None:
            continue
        if _on_import_path(where, known):
            out.append(Finding(
                H3, fc.path, None, f"A module named {mod!r} was added where it is importable ahead of the real one; "
                                   f"it shadows {shadows}, so tests can import this file instead.",
                after=_clip(fc.head_text), detail={"kind": "shadow", "module": mod, "shadows": shadows}))
        elif shadows != "a standard-library module" and ("tests" in where.split("/") or "test" in where.split("/")):
            # Inside a test package it shadows only if that directory is put on sys.path (a `pythonpath`
            # setting, a sys.path edit, or running from inside it). Suspicious, so reported, but advisory.
            out.append(Finding(
                H3, fc.path, None, f"A module named {mod!r}, the same name as {shadows}, was added inside the test "
                                   f"package {where}/. It shadows the real one only if {where}/ is on sys.path, so "
                                   "this is a warning, not a block.",
                after=_clip(fc.head_text), detail={"kind": "shadow", "module": mod, "shadows": shadows,
                                                   "advisory": True}))
    return out


# --------------------------------------------------------------------------- pytest configuration (H4, H2, H6)

DEFAULTS = {
    "python_files": ["test_*.py", "*_test.py"],
    "python_classes": ["Test"],
    "python_functions": ["test"],
    "norecursedirs": ["*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}"],
}
# Options that narrow what runs. Value-taking ones consume the next argument.
_SELECT_VALUE = {"-k", "-m", "--deselect", "--ignore", "--ignore-glob", "--rootdir", "-c", "--config-file",
                 "--confcutdir"}
_SELECT_FLAG = {"--co", "--collect-only", "--lf", "--last-failed", "--sw", "--stepwise",
                "--continue-on-collection-errors", "--sw-skip", "--stepwise-skip"}
_DOCTEST = {"--doctest-modules", "--doctest-glob", "--doctest-continue-on-failure"}


class ConfigError(ValueError):
    pass


def _split_args(value) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            out.extend(_split_args(v) if not isinstance(v, str) else [v])
        return out
    if isinstance(value, str):
        try:
            return shlex.split(value)
        except ValueError:
            return value.split()
    return []


def _split_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return value.split()
    return []


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def pytest_config(name: str, text: str | None) -> dict | None:
    """The pytest settings a root config file holds, or None if pytest does not read it."""
    if text is None:
        return None
    try:
        if name.endswith(".toml"):
            doc = tomllib.loads(text)
            if name in ("pytest.toml", ".pytest.toml"):
                return dict(doc.get("pytest", {})) if isinstance(doc.get("pytest", {}), dict) else {}
            tool = doc.get("tool", {}).get("pytest") if isinstance(doc.get("tool"), dict) else None
            if not isinstance(tool, dict):
                return None
            ini = tool.get("ini_options")
            if isinstance(ini, dict):
                return dict(ini)
            native = {k: v for k, v in tool.items() if k != "ini_options"}
            return native or None
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.read_string(text)
    except (tomllib.TOMLDecodeError, configparser.Error, UnicodeError) as exc:
        raise ConfigError(f"{type(exc).__name__}: {exc}") from None
    section = {"setup.cfg": "tool:pytest"}.get(name, "pytest")
    if cp.has_section(section):
        return dict(cp.items(section))
    return {} if name in ("pytest.ini", ".pytest.ini") else None


def _active(files: dict[str, str | None]) -> tuple[str | None, dict]:
    """(file name, settings) of the config pytest uses, by pytest 9's precedence."""
    for name in PYTEST_CONFIG_NAMES:
        cfg = pytest_config(name, files.get(name))
        if cfg is not None:
            return name, cfg
    return None, {}


def _opts(args: list[str]) -> list[tuple[str, str]]:
    """(option, value) pairs for the options PROVECODE reads from an argument list."""
    out = []
    i = 0
    while i < len(args):
        a = args[i]
        key, val = a, ""
        if a.startswith("--") and "=" in a:
            key, val = a.split("=", 1)
        elif a.startswith("-") and not a.startswith("--") and len(a) > 2 and a[:2] in ("-k", "-m", "-p", "-c"):
            key, val = a[:2], a[2:]
        elif a in _SELECT_VALUE or a in ("-p", "--cov-fail-under", "--doctest-glob"):
            if i + 1 < len(args):
                val = args[i + 1]
                i += 1
        out.append((key, val))
        i += 1
    return out


def _new(base: list, head: list) -> list:
    pool: dict = {}
    for x in base:
        pool[x] = pool.get(x, 0) + 1
    out = []
    for x in head:
        if pool.get(x, 0):
            pool[x] -= 1
        else:
            out.append(x)
    return out


def _fmt(opt: tuple[str, str]) -> str:
    return f"{opt[0]} {opt[1]}".strip()


def compare_args(base_args: list[str], head_args: list[str]) -> dict[str, list[str]]:
    """Weakening changes between two pytest argument lists, by rule bucket."""
    b, h = _opts(base_args), _opts(head_args)
    added = _new(b, h)
    removed = _new(h, b)
    out: dict[str, list[str]] = {"select": [], "plugin": [], "doctest_removed": [], "cov_lowered": []}
    for o in added:
        if o[0] in _SELECT_VALUE or o[0] in _SELECT_FLAG:
            out["select"].append(_fmt(o))
        elif o[0] == "-p":
            out["plugin"].append(_fmt(o))
    for o in removed:
        if o[0] in _DOCTEST:
            out["doctest_removed"].append(_fmt(o))
    bc = [_num(v) for k, v in b if k == "--cov-fail-under"]
    hc = [_num(v) for k, v in h if k == "--cov-fail-under"]
    if bc and bc[-1] is not None and (not hc or (hc[-1] is not None and hc[-1] < bc[-1])):
        out["cov_lowered"].append(f"--cov-fail-under {bc[-1]:g} -> " + (f"{hc[-1]:g}" if hc else "removed"))
    return out


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _config_findings(base_name, base_cfg, head_name, head_cfg, where: str) -> list[Finding]:
    out: list[Finding] = []
    if base_name and head_name and base_name != head_name and base_cfg:
        order = list(PYTEST_CONFIG_NAMES)
        why = (f"which takes precedence over {base_name}" if order.index(head_name) < order.index(base_name)
               else f"because {base_name} no longer holds pytest settings")
        out.append(Finding(
            H4, head_name, None, f"pytest now reads its settings from {head_name}, {why}; the settings in {base_name} "
                                 f"({', '.join(sorted(base_cfg)[:8])}) no longer apply.",
            detail={"from": base_name, "to": head_name}))
    path = head_name or base_name or where
    diff = compare_args(_split_args(base_cfg.get("addopts")), _split_args(head_cfg.get("addopts")))
    if diff["select"]:
        out.append(Finding(H4, path, None, "addopts now narrows which tests run: " + ", ".join(diff["select"]) + ".",
                           after=", ".join(diff["select"]), detail={"key": "addopts", "added": diff["select"]}))
    if diff["doctest_removed"]:
        out.append(Finding(H4, path, None, "addopts no longer collects doctests (" + ", ".join(diff["doctest_removed"])
                           + " removed).", before=", ".join(diff["doctest_removed"]),
                           detail={"key": "addopts", "removed": diff["doctest_removed"]}))
    if diff["plugin"]:
        out.append(Finding(H2, path, None, "addopts now loads or disables pytest plugins: " + ", ".join(diff["plugin"])
                           + "; a plugin runs inside pytest and can change collection and results.",
                           after=", ".join(diff["plugin"]), detail={"key": "addopts", "plugins": diff["plugin"]}))
    if diff["cov_lowered"]:
        out.append(Finding(H6, path, None, "The coverage threshold was lowered: " + diff["cov_lowered"][0] + ".",
                           detail={"key": "--cov-fail-under"}))
    bt, ht = _split_list(base_cfg.get("testpaths")), _split_list(head_cfg.get("testpaths"))
    gone = [t for t in bt if t not in ht]
    if gone and ht:
        out.append(Finding(H4, path, None, "testpaths no longer includes " + ", ".join(gone) + ".",
                           before=" ".join(bt), after=" ".join(ht), detail={"key": "testpaths", "removed": gone}))
    elif ht and not bt:
        out.append(Finding(H4, path, None, "testpaths was introduced (" + " ".join(ht) + "); pytest now collects only "
                           "there instead of from the whole rootdir.", after=" ".join(ht),
                           detail={"key": "testpaths", "introduced": ht}))
    for key in ("python_files", "python_classes", "python_functions"):
        b = _split_list(base_cfg.get(key)) or DEFAULTS[key]
        h = _split_list(head_cfg.get(key)) or DEFAULTS[key]
        gone = [x for x in b if x not in h]
        if gone:
            out.append(Finding(H4, path, None, f"{key} no longer matches {', '.join(gone)}, so tests named that way "
                                               "are no longer collected.", before=" ".join(b), after=" ".join(h),
                               detail={"key": key, "removed": gone}))
    b = _split_list(base_cfg.get("norecursedirs")) or DEFAULTS["norecursedirs"]
    h = _split_list(head_cfg.get("norecursedirs")) or DEFAULTS["norecursedirs"]
    added = [x for x in h if x not in b]
    if added:
        out.append(Finding(H4, path, None, "norecursedirs now skips " + ", ".join(added) + "; tests there are no "
                           "longer collected.", before=" ".join(b), after=" ".join(h),
                           detail={"key": "norecursedirs", "added": added}))
    if _truthy(base_cfg.get("xfail_strict", False)) and not _truthy(head_cfg.get("xfail_strict", False)):
        out.append(Finding(H4, path, None, "xfail_strict was turned off, so a test marked xfail that unexpectedly "
                                           "passes no longer fails the run.", detail={"key": "xfail_strict"}))
    return out


def _h4(cs: Changeset) -> tuple[list[Finding], list[Gap]]:
    roots = {f.path: f for f in cs.files if _is_root(f.path) and f.path in PYTEST_CONFIG_NAMES}
    if not any(f.status != "U" and f.base_text != f.head_text for f in roots.values()):
        return [], []
    base_files = {p: f.base_text for p, f in roots.items()}
    head_files = {p: f.head_text for p, f in roots.items()}
    gaps = []
    try:
        bn, bc = _active(base_files)
    except ConfigError as exc:
        return [], [Gap("unparseable", f"pytest configuration at base: {exc}")]
    try:
        hn, hc = _active(head_files)
    except ConfigError as exc:
        return [], [Gap("unparseable", f"pytest configuration at head: {exc}")]
    return _config_findings(bn, bc, hn, hc, "pytest configuration"), gaps


# --------------------------------------------------------------------------- H5 (CI workflows)

TEST_COMMAND = (
    r"(?<![\w./-])(pytest|py\.test|tox|nox)(?![\w./-])|python[0-9.]*\s+-I?m\s*(pytest|unittest)\b"
    r"|\bcoverage\s+run\b|\bhatch\s+(test|run)\b")
_SUPPRESS = (r"\|\|\s*(true|:|exit\s+0|echo\b)|;\s*exit\s+0\b|\bset\s+\+e\b")
_FALSE_IF = r"(?i)^\s*(\$\{\{\s*)?(false|0|''|\"\")(\s*\}\})?\s*$"


def is_test_command(line: str) -> bool:
    return bool(re.search(TEST_COMMAND, line)) and not re.search(r"\binstall\b", line)


def _test_lines(run: str) -> list[str]:
    """Logical command lines (backslash continuations joined) that run tests."""
    logical, cur = [], ""
    for line in run.split("\n"):
        s = line.rstrip()
        if s.endswith("\\"):
            cur += s[:-1] + " "
            continue
        logical.append(" ".join((cur + s).split()))
        cur = ""
    if cur:
        logical.append(" ".join(cur.split()))
    return [x for x in logical if is_test_command(x)]


@dataclass
class _Step:
    name: str
    run: str
    tests: list[str]
    coe: str
    cond: str
    env: dict


def _steps(job) -> list[_Step]:
    out = []
    steps = job.get("steps") if isinstance(job, dict) else None
    for s in steps if isinstance(steps, list) else []:
        if not isinstance(s, dict):
            continue
        run = s.get("run") if isinstance(s.get("run"), str) else ""
        out.append(_Step(str(s.get("name") or ""), run, _test_lines(run), str(s.get("continue-on-error") or ""),
                         str(s.get("if") or ""), s.get("env") if isinstance(s.get("env"), dict) else {}))
    return out


def _triggers(doc) -> dict:
    on = doc.get("on", doc.get(True)) if isinstance(doc, dict) else None
    if isinstance(on, str):
        return {on: {}}
    if isinstance(on, list):
        return {str(x): {} for x in on}
    if isinstance(on, dict):
        return {str(k): (v if isinstance(v, dict) else {}) for k, v in on.items()}
    return {}


def _covers_python(pattern: str, pkgs: set[str]) -> bool:
    p = pattern.strip().lstrip("!")
    if p in ("**", "*", "**/*", "**/**") or p.endswith((".py", "*.py", "**.py")) or "*.py" in p:
        return True
    top = p.split("/")[0]
    return top == "src" or top in pkgs


def _cmd_diff(b: str, h: str) -> list[str]:
    try:
        ba, ha = shlex.split(b), shlex.split(h)
    except ValueError:
        ba, ha = b.split(), h.split()
    why = []
    d = compare_args(ba, ha)
    why += d["select"]
    for flag in ("-e", "-s", "--session", "--sessions", "-t", "--tags"):
        bv = [ba[i + 1] for i, a in enumerate(ba[:-1]) if a == flag]
        hv = [ha[i + 1] for i, a in enumerate(ha[:-1]) if a == flag]
        if hv and hv != bv and not (bv and set(hv) >= set(bv)):
            why.append(f"{flag} {' '.join(hv)}")
    if "pytest" in " ".join(ha):
        def positional(args):
            if "pytest" not in args:
                return []
            rest = args[args.index("pytest") + 1:]
            out, skip = [], False
            for a in rest:
                if skip:
                    skip = False
                    continue
                if a.startswith("-"):
                    skip = a in _SELECT_VALUE or a in ("-p", "-n", "-W", "-o", "--cov", "--junitxml", "--tb", "-r")
                    continue
                if a in ("&&", "||", ";", "|"):
                    break
                out.append(a)
            return out
        bp, hp = positional(ba), positional(ha)
        if hp and (not bp or any(x not in hp for x in bp)):
            why.append("paths " + " ".join(hp))
    return why


def _h5_file(fc: FileChange, pkgs: set[str], lim: _limits.Limits) -> tuple[list[Finding], list[Gap]]:
    p = fc.path
    docs = []
    for side, text in (("base", fc.base_text), ("head", fc.head_text)):
        if text is None:
            docs.append(None)
            continue
        gap = _size_gap(p, text, lim)
        if gap:
            return [], [gap]
        try:
            docs.append(yamlsubset.loads(text))
        except yamlsubset.YamlError as exc:
            return [], [Gap("unparseable", f"{side} workflow: {exc}", file=p)]
    base, head = docs
    if not isinstance(base, dict):
        return [], []
    out: list[Finding] = []
    bjobs = base.get("jobs") if isinstance(base.get("jobs"), dict) else {}
    btests = {j: _steps(v) for j, v in bjobs.items()}
    if not any(s.tests for ss in btests.values() for s in ss):
        base_has_tests = False
    else:
        base_has_tests = True
    if head is None:
        if base_has_tests:
            out.append(Finding(H5, p, None, "A workflow that ran the tests was deleted.",
                               before="; ".join(s.tests[0] for ss in btests.values() for s in ss if s.tests)[:400],
                               detail={"change": "workflow_deleted"}))
        return out, []
    if not isinstance(head, dict):
        return out, []
    hjobs = head.get("jobs") if isinstance(head.get("jobs"), dict) else {}
    for jid, bsteps in sorted(btests.items()):
        tsteps = [s for s in bsteps if s.tests]
        if not tsteps:
            continue
        bjob, hjob = bjobs.get(jid), hjobs.get(jid)
        if not isinstance(hjob, dict):
            out.append(Finding(H5, p, None, f"Job {jid!r}, which ran the tests, was removed.",
                               before=tsteps[0].tests[0], detail={"job": jid, "change": "job_removed"}))
            continue
        for key, label in (("continue-on-error", "continue-on-error"), ("if", "if")):
            bv, hv = str(bjob.get(key) or ""), str(hjob.get(key) or "")
            if key == "continue-on-error" and hv and hv.strip().lower() != "false" and bv != hv:
                out.append(Finding(H5, p, None, f"Job {jid!r} runs the tests and now has continue-on-error: {hv}, "
                                                "so failing tests no longer fail the workflow.",
                                   after=f"continue-on-error: {hv}", detail={"job": jid, "change": "continue_on_error"}))
            if key == "if" and re.match(_FALSE_IF, hv) and not re.match(_FALSE_IF, bv):
                out.append(Finding(H5, p, None, f"Job {jid!r} runs the tests and now has if: {hv}, so it never runs.",
                                   after=f"if: {hv}", detail={"job": jid, "change": "never_runs"}))
        hsteps = [s for s in _steps(hjob) if s.tests]
        unmatched = list(hsteps)
        for bs in tsteps:
            hs = next((s for s in unmatched if bs.name and s.name == bs.name), None)
            if hs is None:
                hs = next((s for s in unmatched if s.tests == bs.tests), None)
            if hs is None and len(tsteps) == len(hsteps):
                hs = unmatched[0] if unmatched else None
            if hs is None:
                gone = [t for t in bs.tests if not any(t in s.tests for s in hsteps)]
                if gone:
                    out.append(Finding(H5, p, None, f"In job {jid!r}, the step that ran `{gone[0][:120]}` was removed.",
                                       before=gone[0], detail={"job": jid, "change": "step_removed"}))
                continue
            unmatched.remove(hs)
            where = f"In job {jid!r}, the test step" + (f" {hs.name!r}" if hs.name else "")
            if len(re.findall(_SUPPRESS, hs.run)) > len(re.findall(_SUPPRESS, bs.run)):
                out.append(Finding(H5, p, None, f"{where} now ignores the test command's exit status, so failing tests "
                                                "no longer fail CI.", before=bs.tests[0], after=hs.tests[0] if hs.tests
                                   else hs.run[:400], detail={"job": jid, "change": "exit_ignored"}))
            if hs.coe and hs.coe.strip().lower() != "false" and hs.coe != bs.coe:
                out.append(Finding(H5, p, None, f"{where} now has continue-on-error: {hs.coe}, so failing tests no "
                                                "longer fail the job.", after=f"continue-on-error: {hs.coe}",
                                   detail={"job": jid, "change": "continue_on_error"}))
            if re.match(_FALSE_IF, hs.cond) and not re.match(_FALSE_IF, bs.cond):
                out.append(Finding(H5, p, None, f"{where} now has if: {hs.cond}, so it never runs.",
                                   after=f"if: {hs.cond}", detail={"job": jid, "change": "never_runs"}))
            for bt, ht in zip(bs.tests, hs.tests):
                if bt != ht:
                    why = _cmd_diff(bt, ht)
                    if why:
                        out.append(Finding(H5, p, None, f"{where} now runs fewer tests: " + ", ".join(why) + ".",
                                           before=bt, after=ht, detail={"job": jid, "change": "narrowed"}))
            for env_owner, benv, henv in (("step", bs.env, hs.env),):
                bv, hv = str(benv.get("PYTEST_ADDOPTS", "")), str(henv.get("PYTEST_ADDOPTS", ""))
                d = compare_args(_split_args(bv), _split_args(hv))
                if d["select"]:
                    out.append(Finding(H5, p, None, f"{where} now sets PYTEST_ADDOPTS to narrow the run: "
                                       + ", ".join(d["select"]) + ".", after=f"PYTEST_ADDOPTS: {hv}",
                                       detail={"job": jid, "change": "narrowed"}))
    btrig, htrig = _triggers(base), _triggers(head)
    if base_has_tests:
        for t in ("pull_request", "push"):
            if t in btrig and t not in htrig:
                out.append(Finding(H5, p, None, f"The test workflow no longer runs on {t}.",
                                   detail={"change": "trigger_removed", "trigger": t}))
        for t in ("pull_request", "push", "pull_request_target"):
            if t not in htrig:
                continue
            bpi = _split_list(btrig.get(t, {}).get("paths-ignore")) if isinstance(btrig.get(t), dict) else []
            hpi = _split_list(htrig[t].get("paths-ignore"))
            cover = [x for x in hpi if x not in bpi and _covers_python(x, pkgs)]
            if cover:
                out.append(Finding(H5, p, None, f"The test workflow's {t} trigger now ignores changes to "
                                                f"{', '.join(cover)}, which covers Python source, so such changes are "
                                                "never tested.", after=f"paths-ignore: {cover}",
                                   detail={"change": "paths_ignore", "trigger": t}))
            bpaths = _split_list(btrig.get(t, {}).get("paths")) if isinstance(btrig.get(t), dict) else []
            hpaths = _split_list(htrig[t].get("paths"))
            if hpaths and not bpaths and not any(_covers_python(x, pkgs) for x in hpaths):
                out.append(Finding(H5, p, None, f"The test workflow's {t} trigger now runs only for "
                                                f"{', '.join(hpaths)}, which excludes Python source.",
                                   after=f"paths: {hpaths}", detail={"change": "paths_only", "trigger": t}))
    return out, []


# --------------------------------------------------------------------------- H6 (coverage)

_COVERAGE_SECTIONS = {
    ".coveragerc": ("run", "report"),
    "setup.cfg": ("coverage:run", "coverage:report"),
    "tox.ini": ("coverage:run", "coverage:report"),
}
_BENIGN_EXCLUDE = ("type_checking", "notimplemented", "__main__", "overload", "abstractmethod", "__repr__",
                   "protocol", "pragma", "...", "pass", "if 0:", "if false:", "assert_never", "typing.")
_PRAGMA = r"(?i)#\s*pragma:\s*no\s*cover"
_TRIVIAL_CODE = (
    r"^(if\s+__name__\s*==\s*['\"]__main__['\"]\s*:|if\s+(\w+\.)?TYPE_CHECKING\s*:|raise\s+NotImplementedError\b.*"
    r"|pass|\.\.\.|except\s+ImportError\b.*:|except\s+\(?ImportError\b.*:|def\s+__repr__\s*\(.*|@(typing\.)?overload"
    r"|@(abc\.)?abstractmethod|else\s*:|)$")


def coverage_settings(name: str, text: str | None) -> dict | None:
    if text is None:
        return None
    out: dict = {"omit": [], "exclude": [], "fail_under": None}
    try:
        if name == "pyproject.toml":
            doc = tomllib.loads(text)
            cov = doc.get("tool", {}).get("coverage") if isinstance(doc.get("tool"), dict) else None
            if not isinstance(cov, dict):
                return None
            for sec in ("run", "report"):
                s = cov.get(sec) if isinstance(cov.get(sec), dict) else {}
                out["omit"] += _split_list(s.get("omit"))
                out["exclude"] += _split_list(s.get("exclude_lines")) + _split_list(s.get("exclude_also"))
                if s.get("fail_under") is not None:
                    out["fail_under"] = _num(s.get("fail_under"))
            return out
        sections = _COVERAGE_SECTIONS.get(name)
        if sections is None:
            return None
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.read_string(text)
    except (tomllib.TOMLDecodeError, configparser.Error) as exc:
        raise ConfigError(f"{type(exc).__name__}: {exc}") from None
    found = False
    for sec in sections:
        if not cp.has_section(sec):
            continue
        found = True
        s = dict(cp.items(sec))
        out["omit"] += [x.strip() for x in re.split(r"[,\n]", s.get("omit", "")) if x.strip()]
        out["exclude"] += [x.strip() for x in s.get("exclude_lines", "").split("\n") + s.get("exclude_also", "").split("\n")
                           if x.strip()]
        if s.get("fail_under"):
            out["fail_under"] = _num(s["fail_under"])
    return out if found else None


def _h6_config(fc: FileChange) -> tuple[list[Finding], list[Gap]]:
    name = fc.path
    if name not in ("pyproject.toml", ".coveragerc", "setup.cfg", "tox.ini") or fc.base_text == fc.head_text:
        return [], []
    try:
        b = coverage_settings(name, fc.base_text) or {"omit": [], "exclude": [], "fail_under": None}
        h = coverage_settings(name, fc.head_text) or {"omit": [], "exclude": [], "fail_under": None}
    except ConfigError as exc:
        return [], [Gap("unparseable", f"coverage configuration: {exc}", file=name)]
    out = []
    if b["fail_under"] is not None and (h["fail_under"] is None or h["fail_under"] < b["fail_under"]):
        new = "removed" if h["fail_under"] is None else f"{h['fail_under']:g}"
        out.append(Finding(H6, name, None, f"The coverage threshold fail_under was lowered from {b['fail_under']:g} to "
                                           f"{new}.", detail={"key": "fail_under"}))
    omitted = [x for x in h["omit"] if x not in b["omit"] and not re.search(r"test|conftest", x, re.I)]
    if omitted:
        out.append(Finding(H6, name, None, "Coverage now omits " + ", ".join(omitted) + ", so code there no longer "
                                           "counts toward the coverage requirement.", after=", ".join(omitted),
                           detail={"key": "omit", "added": omitted}))
    excluded = [x for x in h["exclude"] if x not in b["exclude"] and not any(k in x.lower() for k in _BENIGN_EXCLUDE)]
    if excluded:
        out.append(Finding(H6, name, None, "Coverage now excludes lines matching " + ", ".join(excluded) + ".",
                           after=", ".join(excluded), detail={"key": "exclude", "added": excluded}))
    return out, []


def _h6_pragma(fc: FileChange) -> list[Finding]:
    if fc.head_text is None:
        return []
    b = (fc.base_text or "").splitlines()
    h = fc.head_text.splitlines()
    base_pragmas: dict[str, int] = {}
    for line in b:
        if re.search(_PRAGMA, line):
            k = line.split("#", 1)[0].strip()
            base_pragmas[k] = base_pragmas.get(k, 0) + 1
    added = []
    sm = difflib.SequenceMatcher(None, b, h, autojunk=False)
    for tag, _, _, j1, j2 in sm.get_opcodes():
        if tag not in ("replace", "insert"):
            continue
        for j in range(j1, j2):
            line = h[j]
            if not re.search(_PRAGMA, line):
                continue
            code = line.split("#", 1)[0].strip()
            if re.match(_TRIVIAL_CODE, code):
                continue
            if base_pragmas.get(code, 0) > 0:
                base_pragmas[code] -= 1
                continue
            added.append((j + 1, line.strip()))
    if not added:
        return []
    return [Finding(H6, fc.path, None, f"`# pragma: no cover` was added to {len(added)} non-trivial line(s), which "
                                       "removes them from coverage measurement.",
                    head_lines=(added[0][0], added[0][0]), after=_clip("\n".join(x for _, x in added[:5])),
                    detail={"key": "pragma", "lines": [n for n, _ in added]})]


# --------------------------------------------------------------------------- H7 (test dependencies)

_REQ = (r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^;#@]*)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def is_test_dependency(name: str) -> bool:
    n = _norm(name)
    return n in ("pytest", "hypothesis", "coverage") or n.startswith("pytest-")


def _req(s: str) -> tuple[str, str] | None:
    m = re.match(_REQ, s)
    return (_norm(m.group(1)), m.group(3).strip()) if m else None


def dependencies(name: str, text: str | None) -> dict[str, list[str]]:
    """Test dependencies declared in a file: normalised name -> specifier strings."""
    out: dict[str, list[str]] = {}
    if text is None:
        return out

    def add(s):
        r = _req(s) if isinstance(s, str) else None
        if r and is_test_dependency(r[0]):
            out.setdefault(r[0], []).append(r[1])

    try:
        if name.endswith("pyproject.toml"):
            doc = tomllib.loads(text)
            proj = doc.get("project", {}) if isinstance(doc.get("project"), dict) else {}
            for s in proj.get("dependencies", []) or []:
                add(s)
            for lst in (proj.get("optional-dependencies") or {}).values():
                for s in lst if isinstance(lst, list) else []:
                    add(s)
            for lst in (doc.get("dependency-groups") or {}).values():
                for s in lst if isinstance(lst, list) else []:
                    add(s)
            poetry = (doc.get("tool", {}) or {}).get("poetry", {}) if isinstance(doc.get("tool"), dict) else {}
            groups = [poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})]
            groups += [g.get("dependencies", {}) for g in (poetry.get("group", {}) or {}).values() if isinstance(g, dict)]
            for g in groups:
                for k, v in (g or {}).items() if isinstance(g, dict) else []:
                    if is_test_dependency(k):
                        out.setdefault(_norm(k), []).append(v if isinstance(v, str) else str(v.get("version", ""))
                                                            if isinstance(v, dict) else "")
        elif name.endswith("setup.cfg"):
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            cp.read_string(text)
            if cp.has_section("options.extras_require"):
                for _, v in cp.items("options.extras_require"):
                    for line in v.split("\n"):
                        add(line.strip())
            if cp.has_option("options", "tests_require"):
                for line in cp.get("options", "tests_require").split("\n"):
                    add(line.strip())
        else:
            for line in text.splitlines():
                s = line.strip()
                if s and not s.startswith(("#", "-")):
                    add(s)
    except (tomllib.TOMLDecodeError, configparser.Error) as exc:
        raise ConfigError(f"{type(exc).__name__}: {exc}") from None
    return out


def _version(v: str) -> tuple[int, ...] | None:
    m = re.match(r"^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?", v)
    return tuple(int(x) for x in m.groups() if x is not None) if m else None


def _bounds(specs: list[str]) -> tuple[int | None, int | None]:
    """(lowest major allowed, highest major allowed) implied by specifier strings."""
    lo = hi = None
    for spec in specs:
        for part in spec.split(","):
            m = re.match(r"^\s*(===|==|~=|>=|<=|!=|>|<)\s*(.+?)\s*$", part)
            if not m:
                continue
            op, ver = m.group(1), _version(m.group(2))
            if ver is None:
                continue
            major = ver[0]
            if op in (">=", "==", "===", "~=", ">"):
                lo = major if lo is None else max(lo, major)
            if op in ("==", "===", "~=", "<="):
                hi = major if hi is None else min(hi, major)
            elif op == "<":
                top = major - 1 if all(x == 0 for x in ver[1:]) else major
                hi = top if hi is None else min(hi, top)
    return lo, hi


def _h7(changes: list[FileChange]) -> tuple[list[Finding], list[Gap]]:
    base: dict[str, list[str]] = {}
    head: dict[str, list[str]] = {}
    where: dict[str, str] = {}
    gaps = []
    for fc in changes:
        if fc.base_text == fc.head_text:
            continue
        try:
            b = dependencies(fc.path, fc.base_text)
            h = dependencies(fc.path, fc.head_text)
        except ConfigError as exc:
            gaps.append(Gap("unparseable", f"dependency file: {exc}", file=fc.path))
            continue
        for k, v in b.items():
            base.setdefault(k, []).extend(v)
            where.setdefault(k, fc.path)
        for k, v in h.items():
            head.setdefault(k, []).extend(v)
            where.setdefault(k, fc.path)
    out = []
    for name in sorted(base):
        if name not in head:
            out.append(Finding(H7, where[name], None, f"The test dependency {name} was removed.",
                               before=f"{name} {' '.join(x for x in base[name] if x)}".strip(),
                               detail={"dependency": name, "change": "removed"}))
            continue
        lo, base_hi = _bounds(base[name])
        _, hi = _bounds(head[name])
        if hi is not None and (base_hi is None or hi < base_hi) and (lo is None or hi < lo or base_hi is None):
            was = f"at least {lo}" if base_hi is None and lo is not None else "unbounded" if base_hi is None                 else f"at most {base_hi}"
            out.append(Finding(H7, where[name], None, f"The test dependency {name} was pinned down: its highest allowed "
                                                      f"major version went from {was} to {hi}.",
                               before=f"{name} {' '.join(base[name])}".strip(),
                               after=f"{name} {' '.join(head[name])}".strip(),
                               detail={"dependency": name, "change": "pinned_down"}))
    return out, gaps


# --------------------------------------------------------------------------- entry point


def _pytest11(fc: FileChange) -> list[Finding]:
    """A pytest11 entry point makes the package itself a pytest plugin that auto-loads when installed."""
    def names(text):
        if text is None:
            return set()
        try:
            if fc.path.endswith(".toml"):
                doc = tomllib.loads(text)
                ep = ((doc.get("project", {}) or {}).get("entry-points", {}) or {}).get("pytest11", {})
                return set(ep) if isinstance(ep, dict) else set()
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            cp.read_string(text)
            if cp.has_option("options.entry_points", "pytest11"):
                return {x.split("=")[0].strip() for x in cp.get("options.entry_points", "pytest11").split("\n")
                        if x.strip()}
        except (tomllib.TOMLDecodeError, configparser.Error):
            return set()
        return set()

    added = sorted(names(fc.head_text) - names(fc.base_text))
    if not added:
        return []
    return [Finding(H2, fc.path, None, "A pytest11 entry point was added (" + ", ".join(added) + "); once the package "
                                       "is installed, pytest loads it automatically in every test run.",
                    detail={"entry_points": added})]


def analyze(cs: Changeset, lim: _limits.Limits) -> tuple[list[Finding], list[Gap], list[str]]:
    """(findings, gaps, analyzed paths) for everything except the per-test rules."""
    findings: list[Finding] = []
    gaps: list[Gap] = []
    analyzed: list[str] = []
    pkgs = _packages(cs.base_paths)
    for fc in sorted(cs.files, key=lambda f: f.path):
        if fc.status == "U" or fc.base_text == fc.head_text:
            continue
        kind = fc.kind
        if kind == CONFTEST:
            f, g = _h1_h2(fc, lim)
        elif kind == TEST:
            f, g = _h2_module(fc, lim)
        elif kind == CI:
            f, g = _h5_file(fc, pkgs, lim)
        elif kind == CONFIG:
            f, g = _h6_config(fc)
            if _is_root(fc.path) and fc.path in ("pyproject.toml", "setup.cfg"):
                f = f + _pytest11(fc)
        elif kind == SOURCE:
            f, g = _h6_pragma(fc), []
        else:
            continue
        if kind != TEST:
            analyzed.append(fc.path)
        findings += f
        gaps += g
    f, g = _h4(cs)
    findings += f
    gaps += g
    findings += _h3(cs)
    dep_files = [fc for fc in cs.files if fc.status != "U" and (
        _name(fc.path) == "pyproject.toml" or _name(fc.path) == "setup.cfg" or is_requirements(fc.path))]
    f, g = _h7(dep_files)
    findings += f
    gaps += g
    return findings, gaps, analyzed
