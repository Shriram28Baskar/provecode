"""Tier H case generator: harness and test-body weakenings (benchmark/patterns_harness.md, ADR 0012).

Same method as the other tiers: pinned sources, byte splices on raw git blobs, seeded selection,
a frozen held-out split. Benchmark code, written before any rule code for ADR 0012; it imports
nothing from the engine. Validation of edited files uses tomllib, configparser, ast and PyYAML
(BaseLoader), never the engine.

  python -m benchmark.generate_cases_harness generate   # fetch, write cache + locks
  python -m benchmark.generate_cases_harness verify     # regenerate and compare to the locks
"""

from __future__ import annotations

import argparse
import ast
import configparser
import json
import random
import re
import shlex
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from typing import Callable

import yaml

from . import generate_cases as g
from .generate_cases import SkipSite, splice

BENCH = g.BENCH
SOURCES_LOCK = BENCH / "sources_harness.lock.json"
CASES_LOCK = BENCH / "cases_harness.lock.json"
SPLIT_LOCK = BENCH / "split_harness.lock.json"
CASES_DIR = g.CACHE / "cases_harness"
SEED = "20261121-TH"
GENERATOR_VERSION = "1"

NOT_PACKAGES = {"tests", "test", "docs", "doc", "examples", "example", "scripts", "benchmarks", "bench", "benchmark"}


# --------------------------------------------------------------------------- repository context


@dataclass
class Repo:
    name: str
    sha: str
    d: object
    paths: list[str]
    _blobs: dict = field(default_factory=dict)
    tests: list = field(default_factory=list)  # generate_cases.FileInfo
    pkgs: list = field(default_factory=list)  # (name, prefix) with prefix "" or "src/"

    def blob(self, path: str) -> bytes:
        if path not in self._blobs:
            self._blobs[path] = g.blob(self.d, path)
        return self._blobs[path]

    @property
    def first_test_key(self) -> str:
        keys = sorted(f.key for fi in self.tests for f in fi.funcs)
        return keys[0] if keys else "tests::none"

    @property
    def test_names(self) -> list[str]:
        return sorted({f.node.name for fi in self.tests for f in fi.funcs})

    @property
    def test_paths(self) -> list[str]:
        return sorted(fi.path for fi in self.tests)


def load_repo(src: dict) -> Repo:
    d = g.fetch(src)
    paths = sorted(p for p in g._git(["ls-files"], d).splitlines() if p)
    r = Repo(src["repo"], src["sha"], d, paths)
    for p in g.test_files(d):
        fi = g.parse_file(src["repo"], p, r.blob(p))
        if fi is not None:
            r.tests.append(fi)
    pk = set()
    for p in paths:
        parts = p.split("/")
        if len(parts) == 2 and parts[1] == "__init__.py" and parts[0] not in NOT_PACKAGES:
            pk.add((parts[0], ""))
        elif len(parts) == 3 and parts[0] == "src" and parts[2] == "__init__.py" and parts[1] not in NOT_PACKAGES:
            pk.add((parts[1], "src/"))
    r.pkgs = sorted(pk)
    return r


def pick(seed: str, items: list):
    items = sorted(items)
    if not items:
        raise SkipSite("nothing to pick")
    return random.Random(seed).choice(items)


# --------------------------------------------------------------------------- sites


@dataclass
class HSite:
    path: str
    line: int
    key: str
    status: str  # "M" (modified) or "A" (added)
    base: bytes | None
    build: Callable[[], bytes]
    test_key: str | None = None


def eol_of(data: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in data else b"\n"


def lines_of(data: bytes) -> list[bytes]:
    return data.splitlines(keepends=True)


def join(lines: list[bytes]) -> bytes:
    return b"".join(lines)


def append_block(data: bytes, block: str) -> bytes:
    eol = eol_of(data)
    body = block.replace("\n", eol.decode()).encode()
    if data and not data.endswith(b"\n"):
        data += eol
    return data + eol + eol + body


def check_py(head: bytes) -> bytes:
    ast.parse(head)
    return head


def check_toml(head: bytes) -> bytes:
    tomllib.loads(head.decode("utf-8"))
    return head


def check_ini(head: bytes) -> bytes:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.read_string(head.decode("utf-8"))
    return head


def check_yaml(head: bytes) -> bytes:
    import yaml

    yaml.load(head.decode("utf-8"), Loader=yaml.BaseLoader)
    return head


# ----------------------------------------------------------------- conftest (HC*, NC*)

HOOK_FORCE_PASS = '''import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    report.outcome = "passed"
'''


def conftests(r: Repo) -> list[str]:
    return [p for p in r.paths if p.rsplit("/", 1)[-1] == "conftest.py"]


def conftest_sites(r: Repo, pid: str, block: Callable[[Repo, str, bytes], str | None]) -> list[HSite]:
    out = []
    for p in conftests(r):
        base = r.blob(p)

        def build(p=p, base=base):
            text = block(r, p, base)
            if text is None:
                raise SkipSite("not applicable")
            return check_py(append_block(base, text))

        out.append(HSite(p, 1, p, "M", base, build, r.first_test_key))
    return out


def hc01(r, p, base):
    return HOOK_FORCE_PASS


def hc02(r, p, base):
    name = pick(f"{SEED}-HC02-{r.name}-pick", r.test_names)
    return ("def pytest_collection_modifyitems(config, items):\n"
            f'    items[:] = [item for item in items if "{name}" not in item.nodeid]\n')


def hc03(r, p, base):
    if b"collect_ignore" in base:
        return None
    here = p.rsplit("/", 1)[0] + "/" if "/" in p else ""
    below = [t[len(here):] for t in r.test_paths if t.startswith(here)]
    if not below:
        return None
    rel = pick(f"{SEED}-HC03-{r.name}-pick", below)
    return f'collect_ignore = ["{rel}"]\n'


def hc04(r, p, base):
    return "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n"


def nc01(r, p, base):
    return "import pytest\n\n\n@pytest.fixture\ndef provecode_sample_value():\n    return 42\n"


def nc02(r, p, base):
    if b"def pytest_configure" in base:
        return None
    return ("def pytest_configure(config):\n"
            '    config.addinivalue_line("markers", "provecode: marker added by a legitimate change")\n')


def nc03(r, p, base):
    if b"pytest_report_header" in base:
        return None
    return 'def pytest_report_header(config):\n    return "provecode: example header"\n'


def hc05_sites(r: Repo) -> list[HSite]:
    if "conftest.py" in r.paths:
        return []
    return [HSite("conftest.py", 1, "conftest.py", "A", None, lambda: check_py(HOOK_FORCE_PASS.encode()),
                  r.first_test_key)]


# ----------------------------------------------------------------- pytest config (HP*, NP*)


@dataclass
class Cfg:
    path: str
    header: bytes
    mode: str  # "ini" | "toml"


def active_config(r: Repo) -> Cfg | None:
    for name in ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"):
        if name not in r.paths:
            continue
        heads = {l.strip() for l in lines_of(r.blob(name))}
        if name in ("pytest.toml", ".pytest.toml"):
            return Cfg(name, b"[pytest]", "toml")
        if name in ("pytest.ini", ".pytest.ini"):
            return Cfg(name, b"[pytest]", "ini")
        if name == "pyproject.toml":
            for h in (b"[tool.pytest.ini_options]", b"[tool.pytest]"):
                if h in heads:
                    return Cfg(name, h, "toml")
        if name == "tox.ini" and b"[pytest]" in heads:
            return Cfg(name, b"[pytest]", "ini")
        if name == "setup.cfg" and b"[tool:pytest]" in heads:
            return Cfg(name, b"[tool:pytest]", "ini")
    return None


def section(lines: list[bytes], header: bytes) -> tuple[int, int]:
    h = next((i for i, l in enumerate(lines) if l.strip() == header), None)
    if h is None:
        raise SkipSite(f"no section {header!r}")
    end = len(lines)
    for i in range(h + 1, len(lines)):
        if lines[i].startswith(b"["):
            end = i
            break
    return h, end


def key_line(lines: list[bytes], h: int, end: int, key: str, mode: str) -> int | None:
    pat = re.compile((rb"^" if mode == "ini" else rb"^\s*") + re.escape(key.encode()) + rb"\s*=")
    return next((i for i in range(h + 1, end) if pat.match(lines[i])), None)


def _insert_list_items(line: bytes, items: bytes) -> bytes:
    """`key = [rest` -> `key = [items, rest`; only for a value that starts with `[`."""
    m = re.match(rb"^(\s*[\w.-]+\s*=\s*)\[", line)
    if not m:
        raise SkipSite("value is not a list")
    rest = line[m.end():]
    if rest.lstrip().startswith(b"]"):
        return line[:m.end()] + items + rest
    if rest.strip() == b"":
        return line[:m.end()] + items + b"," + rest
    return line[:m.end()] + items + b", " + rest


def edit_config(cfg: Cfg, data: bytes, fn: Callable[[list[bytes], int, int, bytes], None]) -> bytes:
    lines = lines_of(data)
    h, end = section(lines, cfg.header)
    fn(lines, h, end, eol_of(data))
    head = join(lines)
    return check_toml(head) if cfg.mode == "toml" else check_ini(head)


def add_opts(cfg: Cfg, data: bytes, flags: list[str]) -> bytes:
    def fn(lines, h, end, eol):
        k = key_line(lines, h, end, "addopts", cfg.mode)
        if cfg.mode == "ini":
            text = shlex.join(flags).encode()
            if k is None:
                lines.insert(h + 1, b"addopts = " + text + eol)
                return
            if b"#" in lines[k]:
                raise SkipSite("comment on addopts line")
            body = lines[k].rstrip(b"\r\n")
            lines[k] = body.rstrip() + b" " + text + eol
            return
        items = ", ".join(json.dumps(f) for f in flags).encode()
        if k is None:
            lines.insert(h + 1, b"addopts = [" + items + b"]" + eol)
            return
        m = re.match(rb'^(\s*addopts\s*=\s*)(["\[])', lines[k])
        if not m:
            raise SkipSite("unsupported addopts value")
        if m.group(2) == b"[":
            lines[k] = _insert_list_items(lines[k], items)
        else:
            if lines[k][m.end(1):].startswith(b'"""'):
                raise SkipSite("multi-line string")
            text = shlex.join(flags).encode()
            if b'"' in text or b"\\" in text:
                raise SkipSite("flags not representable in a basic string")
            lines[k] = lines[k][:m.end(1) + 1] + text + b" " + lines[k][m.end(1) + 1:]

    return edit_config(cfg, data, fn)


def cfg_sites(r: Repo, build: Callable[[Repo, Cfg, bytes], bytes]) -> list[HSite]:
    cfg = active_config(r)
    if cfg is None:
        return []
    base = r.blob(cfg.path)
    return [HSite(cfg.path, 1, cfg.path, "M", base, lambda: build(r, cfg, base), r.first_test_key)]


def hp01(r, cfg, base):
    name = pick(f"{SEED}-HP01-{r.name}-pick", r.test_names)
    return add_opts(cfg, base, ["-k", f"not {name}"])


def hp02(r, cfg, base):
    node = pick(f"{SEED}-HP02-{r.name}-pick", [f.key for fi in r.tests for f in fi.funcs])
    return add_opts(cfg, base, ["--deselect", node])


def hp03(r, cfg, base):
    return add_opts(cfg, base, ["-p", "provecode_injected_plugin"])


def hp04(r, cfg, base):
    name = pick(f"{SEED}-HP04-{r.name}-pick", r.test_paths).rsplit("/", 1)[-1]

    def fn(lines, h, end, eol):
        if key_line(lines, h, end, "python_files", cfg.mode) is not None:
            raise SkipSite("python_files already set")
        val = name.encode() if cfg.mode == "ini" else b'["' + name.encode() + b'"]'
        lines.insert(h + 1, b"python_files = " + val + eol)

    return edit_config(cfg, base, fn)


def hp05_sites(r: Repo) -> list[HSite]:
    cfg = active_config(r)
    if cfg is None or cfg.path in ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini"):
        return []
    return [HSite("pytest.ini", 1, "pytest.ini", "A", None, lambda: b"[pytest]\n", r.first_test_key)]


def hp06(r, cfg, base):
    def fn(lines, h, end, eol):
        k = key_line(lines, h, end, "xfail_strict", cfg.mode)
        if k is None or not re.search(rb"=\s*true\b", lines[k], re.I):
            raise SkipSite("xfail_strict is not true")
        lines[k] = re.sub(rb"(=\s*)true\b", rb"\1false", lines[k], flags=re.I)

    return edit_config(cfg, base, fn)


def np01(r, cfg, base):
    lines = lines_of(base)
    h, end = section(lines, cfg.header)
    flag = "--tb=short" if b"-ra" in join(lines[h:end]) else "-ra"
    return add_opts(cfg, base, [flag])


def np02(r, cfg, base):
    def fn(lines, h, end, eol):
        k = key_line(lines, h, end, "testpaths", cfg.mode)
        if k is None:
            raise SkipSite("no testpaths")
        if cfg.mode == "ini":
            lines[k] = lines[k].rstrip(b"\r\n").rstrip() + b" docs" + eol
        else:
            lines[k] = _insert_list_items(lines[k], b'"docs"')

    return edit_config(cfg, base, fn)


# ----------------------------------------------------------------- workflows (HW*, NW*)

TEST_CMD = re.compile(
    rb"(?<![\w./-])(pytest|py\.test|tox|nox)(?![\w./-])|python[0-9.]*\s+-I?m\s*(pytest|unittest)\b"
    rb"|\bcoverage\s+run\b|\bhatch\s+(test|run)\b")


def is_test_line(line: bytes) -> bool:
    """A test command, not a line that installs one (`pip install tox`)."""
    return bool(TEST_CMD.search(line)) and not re.search(rb"\binstall\b", line)


@dataclass
class Step:
    start: int
    end: int
    dash: int  # column of "-"
    key: int  # column of the step's keys
    run_lines: list[int] = field(default_factory=list)
    run_kind: str = ""  # "inline" | "literal" | "folded" | ""
    keys: set = field(default_factory=set)

    @property
    def is_test(self) -> bool:
        return self.run_kind != "" and self._test

    _test: bool = False


def _indent(line: bytes) -> int:
    return len(line) - len(line.lstrip(b" "))


def _blank(line: bytes) -> bool:
    s = line.strip()
    return not s or s.startswith(b"#")


def workflow_steps(lines: list[bytes]) -> list[Step]:
    out: list[Step] = []
    i, n = 0, len(lines)
    while i < n:
        m = re.match(rb"^( *)steps:\s*(#.*)?$", lines[i].rstrip(b"\r\n"))
        if not m:
            i += 1
            continue
        s_ind = len(m.group(1))
        j, dash, items = i + 1, None, []
        while j < n:
            ln = lines[j]
            if _blank(ln):
                j += 1
                continue
            ind, st = _indent(ln), ln.strip()
            if st.startswith(b"-") and (dash is None or ind == dash) and ind >= s_ind:
                dash = ind
                items.append(j)
            elif ind <= (dash if dash is not None else s_ind):
                break
            j += 1
        bounds = items + [j]
        for a, b in zip(items, bounds[1:]):
            last = b
            while last > a and _blank(lines[last - 1]):
                last -= 1
            dl = lines[a]
            after = dl[dash + 1:]
            key = dash + 1 + (len(after) - len(after.lstrip(b" ")))
            out.append(_describe(lines, Step(a, last, dash, key)))
        i = j
    return out


def _key_of(lines: list[bytes], st: Step, idx: int) -> bytes | None:
    ln = lines[idx].rstrip(b"\r\n")
    if idx == st.start:
        body = ln[st.key:]
    elif _indent(ln) == st.key:
        body = ln.strip()
    else:
        return None
    m = re.match(rb"^([\w-]+):", body)
    return m.group(1) if m else None


def _describe(lines: list[bytes], st: Step) -> Step:
    for idx in range(st.start, st.end):
        k = _key_of(lines, st, idx)
        if k is None:
            continue
        st.keys.add(k)
        if k != b"run":
            continue
        ln = lines[idx].rstrip(b"\r\n")
        val = ln[ln.index(b"run:") + 4:].strip()
        if val[:1] in (b"|", b">"):
            st.run_kind = "literal" if val[:1] == b"|" else "folded"
            k2 = idx + 1
            while k2 < st.end and (_blank(lines[k2]) or _indent(lines[k2]) > st.key):
                if not _blank(lines[k2]):
                    st.run_lines.append(k2)
                k2 += 1
        else:
            st.run_kind = "inline"
            st.run_lines = [idx]
    st._test = any(is_test_line(lines[i]) for i in st.run_lines)
    return st


def workflows(r: Repo) -> list[str]:
    return [p for p in r.paths if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))]


def step_sites(r: Repo, pid: str, want_test: bool, build: Callable) -> list[HSite]:
    out = []
    for p in workflows(r):
        base = r.blob(p)
        lines = lines_of(base)
        for st in workflow_steps(lines):
            if st.is_test != want_test:
                continue
            if not want_test and re.search(rb"test", join(lines[st.start:st.end]), re.I):
                continue  # a near-miss must be clearly not a test step (e.g. `run: scripts/test`)

            def b(p=p, base=base, st=st):
                head = build(lines_of(base), st, eol_of(base))
                if head == base:
                    raise SkipSite("no change")
                return check_yaml(head)

            out.append(HSite(p, st.start + 1, f"{p}:{st.start + 1}", "M", base, b, r.first_test_key))
    return out


def _append_line_end(line: bytes, text: bytes) -> bytes:
    body = line.rstrip(b"\r\n")
    return body.rstrip() + text + line[len(body):]


def hw01(lines, st, eol):
    if any(b"exit" in lines[i] for i in st.run_lines):
        raise SkipSite("the run already manages its exit status")
    if st.run_kind == "inline":
        ln = lines[st.run_lines[0]]
        val = ln[ln.index(b"run:") + 4:].strip()
        if val[:1] in (b"'", b'"') or b"#" in val or val.endswith(b"\\"):
            raise SkipSite("quoted, commented or continued inline run")
        lines[st.run_lines[0]] = _append_line_end(ln, b" || true")
    elif st.run_kind == "folded":
        lines[st.run_lines[-1]] = _append_line_end(lines[st.run_lines[-1]], b" || true")
    else:
        i = next(i for i in st.run_lines if is_test_line(lines[i]))
        while lines[i].rstrip().endswith(b"\\"):
            nxt = [k for k in st.run_lines if k > i]
            if not nxt:
                raise SkipSite("dangling continuation")
            i = nxt[0]
        lines[i] = _append_line_end(lines[i], b" || true")
    return join(lines)


def _add_key(key: bytes, value: bytes) -> Callable:
    def fn(lines, st, eol):
        if key in st.keys:
            raise SkipSite(f"step already has {key!r}")
        lines.insert(st.end, b" " * st.key + key + b": " + value + eol)
        return join(lines)

    return fn


def hw04(lines, st, eol):
    del lines[st.start:st.end]
    return join(lines)


def nw01(lines, st, eol):
    lines.insert(st.start, b" " * st.dash + b"- run: python -m pip list" + eol)
    return join(lines)


def nw03(lines, st, eol):
    if st.run_kind != "inline":
        raise SkipSite("not an inline run")
    ln = lines[st.run_lines[0]]
    val = ln[ln.index(b"run:") + 4:].strip()
    if not val or val[:1] in (b"'", b'"') or b"#" in val or b"||" in val or val.endswith(b"\\"):
        raise SkipSite("unsuitable run value")
    lines[st.run_lines[0]] = _append_line_end(ln, b" || true")
    return join(lines)


def hw05_sites(r: Repo) -> list[HSite]:
    out = []
    for p in workflows(r):
        base = r.blob(p)
        lines = lines_of(base)
        if not any(s.is_test for s in workflow_steps(lines)):
            continue
        on = next((i for i, l in enumerate(lines) if re.match(rb'^(on|"on"|\'on\'):\s*(#.*)?$', l.rstrip(b"\r\n"))), None)
        if on is None:
            continue
        stop = next((i for i in range(on + 1, len(lines)) if not _blank(lines[i]) and _indent(lines[i]) == 0), len(lines))
        pr = next((i for i in range(on + 1, stop)
                   if re.match(rb"^\s+pull_request:\s*(#.*)?$", lines[i].rstrip(b"\r\n"))), None)
        if pr is None:
            continue

        def build(base=base, pr=pr):
            ls = lines_of(base)
            pind = _indent(ls[pr])
            kids = [i for i in range(pr + 1, len(ls)) if not _blank(ls[i])]
            child = pind + 2
            if kids and _indent(ls[kids[0]]) > pind:
                child = _indent(ls[kids[0]])
                for i in kids:
                    if _indent(ls[i]) <= pind:
                        break
                    if _indent(ls[i]) == child and re.match(rb"^\s*(paths|paths-ignore):", ls[i]):
                        raise SkipSite("trigger already filters paths")
            ls.insert(pr + 1, b" " * child + b"paths-ignore: ['**.py']" + eol_of(base))
            return check_yaml(join(ls))

        out.append(HSite(p, pr + 1, f"{p}:{pr + 1}", "M", base, build, r.first_test_key))
    return out


# ----------------------------------------------------------------- coverage (HV*, NV*)


def coverage_sections(r: Repo) -> list[Cfg]:
    out = []
    for path, header, mode in ((".coveragerc", b"[run]", "ini"), ("setup.cfg", b"[coverage:run]", "ini"),
                               ("tox.ini", b"[coverage:run]", "ini"), ("pyproject.toml", b"[tool.coverage.run]", "toml")):
        if path in r.paths and header in {l.strip() for l in lines_of(r.blob(path))}:
            out.append(Cfg(path, header, mode))
    return out


def add_omit(cfg: Cfg, data: bytes, pattern: str) -> bytes:
    def fn(lines, h, end, eol):
        k = key_line(lines, h, end, "omit", cfg.mode)
        if cfg.mode == "ini":
            if k is None:
                lines.insert(h + 1, b"omit = " + pattern.encode() + eol)
            else:
                lines.insert(k + 1, b"    " + pattern.encode() + eol)
            return
        if k is None:
            lines.insert(h + 1, b'omit = ["' + pattern.encode() + b'"]' + eol)
        else:
            lines[k] = _insert_list_items(lines[k], b'"' + pattern.encode() + b'"')

    return edit_config(cfg, data, fn)


def omit_sites(r: Repo, which: str) -> list[HSite]:
    out = []
    if which == "source" and not r.pkgs:
        return out
    pattern = "tests/*" if which == "tests" else f"{r.pkgs[0][1]}{r.pkgs[0][0]}/*"
    for cfg in coverage_sections(r):
        base = r.blob(cfg.path)
        out.append(HSite(cfg.path, 1, f"{cfg.path}:{cfg.header.decode()}", "M", base,
                         lambda cfg=cfg, base=base: add_omit(cfg, base, pattern), r.first_test_key))
    return out


def source_files(r: Repo) -> list[str]:
    out = []
    for name, prefix in r.pkgs:
        root = f"{prefix}{name}/"
        for p in r.paths:
            n = p.rsplit("/", 1)[-1]
            if p.startswith(root) and p.endswith(".py") and "/tests/" not in p and not n.startswith("test_"):
                out.append(p)
    return sorted(set(out))


def _trivial_if(n: ast.If) -> bool:
    t = n.test
    if isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "__name__":
        return True
    return (isinstance(t, ast.Name) and t.id == "TYPE_CHECKING") or (
        isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING")


def _nie(n: ast.Raise) -> bool:
    e = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
    return isinstance(e, ast.Name) and e.id == "NotImplementedError"


def pragma_sites(r: Repo, trivial: bool) -> list[HSite]:
    out = []
    for p in source_files(r):
        base = r.blob(p)
        try:
            tree = ast.parse(base)
        except (SyntaxError, ValueError):
            continue
        ls = lines_of(base)
        cand = set()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and trivial is False:
                continue
            for n in (ast.walk(fn) if not trivial else [fn]):
                if trivial:
                    if isinstance(n, ast.If) and _trivial_if(n):
                        cand.add(n.lineno)
                    elif isinstance(n, ast.Raise) and n.exc is not None and _nie(n) and n.lineno == n.end_lineno:
                        cand.add(n.lineno)
                elif isinstance(n, ast.Return) and n.value is not None and n.lineno == n.end_lineno:
                    cand.add(n.lineno)
        for line in sorted(cand):
            text = ls[line - 1]
            if b"#" in text or (trivial and text.rstrip().endswith(b"\\")):
                continue
            if trivial and b"if" in text.split(b"#")[0] and not text.rstrip().endswith(b":"):
                continue

            def build(base=base, line=line):
                l2 = lines_of(base)
                l2[line - 1] = _append_line_end(l2[line - 1], b"  # pragma: no cover")
                return check_py(join(l2))

            out.append(HSite(p, line, f"{p}:{line}", "M", base, build, r.first_test_key))
    return out


# ----------------------------------------------------------------- dependencies (HD*, ND*)

TOML_ITEM = re.compile(rb'^(\s*)"([^"]*)"(\s*,?)(\s*)$')
REQ_LINE = re.compile(rb"^([A-Za-z0-9_.-]+)(\[[^\]]*\])?\s*([^;#]*?)\s*(;[^#]*)?(#.*)?$")
DEP_SECTIONS = {b"[dependency-groups]", b"[project.optional-dependencies]", b"[project]"}


def dep_files(r: Repo) -> list[str]:
    out = []
    for p in r.paths:
        n = p.rsplit("/", 1)[-1]
        if p == "pyproject.toml":
            out.append(p)
        elif n.endswith((".txt", ".in")) and "requirements" in p and not p.startswith(("docs/", "doc/")):
            out.append(p)
    return out


def dep_entries(path: str, lines: list[bytes]) -> list[tuple[int, str, bool]]:
    """(line index, normalised name, has environment marker) for one-per-line entries."""
    out = []
    if path.endswith(".toml"):
        sec = None
        for i, l in enumerate(lines):
            if l.startswith(b"["):
                sec = l.strip()
                continue
            if sec not in DEP_SECTIONS:
                continue
            m = TOML_ITEM.match(l.rstrip(b"\r\n"))
            if m:
                nm = re.match(rb"\s*([A-Za-z0-9_.-]+)", m.group(2))
                if nm:
                    out.append((i, nm.group(1).decode().lower().replace("_", "-"), b";" in m.group(2)))
        return out
    for i, l in enumerate(lines):
        s = l.rstrip(b"\r\n")
        if not s or s.startswith((b"#", b"-")):
            continue
        m = REQ_LINE.match(s)
        if m:
            out.append((i, m.group(1).decode().lower().replace("_", "-"), m.group(4) is not None))
    return out


def dep_sites(r: Repo, pid: str) -> list[HSite]:
    out = []
    for p in dep_files(r):
        base = r.blob(p)
        ls = lines_of(base)
        for i, name, marker in dep_entries(p, ls):
            if pid == "HD01" and not name.startswith("pytest-"):
                continue
            if pid in ("HD02", "ND01") and (name != "pytest" or marker):
                continue

            def build(p=p, base=base, i=i):
                l2 = lines_of(base)
                eol = eol_of(base)
                toml = p.endswith(".toml")
                if pid == "HD01":
                    del l2[i]
                elif pid == "HD02":
                    if toml:
                        m = TOML_ITEM.match(l2[i].rstrip(b"\r\n"))
                        l2[i] = m.group(1) + b'"pytest<7"' + m.group(3) + eol
                    else:
                        l2[i] = b"pytest<7" + eol
                else:
                    present = {n for _, n, _ in dep_entries(p, l2)}
                    plugin = next(x for x in ("pytest-timeout", "pytest-randomly", "pytest-repeat") if x not in present)
                    if toml:
                        m = TOML_ITEM.match(l2[i].rstrip(b"\r\n"))
                        if not m.group(3).strip():
                            l2[i] = m.group(1) + b'"' + m.group(2) + b'",' + eol
                        l2.insert(i + 1, m.group(1) + b'"' + plugin.encode() + b'",' + eol)
                    else:
                        l2.insert(i + 1, plugin.encode() + eol)
                head = join(l2)
                return check_toml(head) if toml else head

            out.append(HSite(p, i + 1, f"{p}:{i + 1}", "M", base, build, r.first_test_key))
    return out


# ----------------------------------------------------------------- harness files (HS*, NS*)


def hs01_sites(r: Repo) -> list[HSite]:
    if "sitecustomize.py" in r.paths:
        return []
    body = b'import os\n\nos.environ.setdefault("PROVECODE_INJECTED", "1")\n'
    return [HSite("sitecustomize.py", 1, "sitecustomize.py", "A", None, lambda: body, r.first_test_key)]


def hs02_sites(r: Repo) -> list[HSite]:
    if not any(p.startswith("tests/") for p in r.paths):
        return []
    body = b'"""Stand-in module."""\n\n\ndef __getattr__(name):\n    return None\n'
    return [HSite(f"tests/{name}.py", 1, f"tests/{name}.py", "A", None, lambda: body, r.first_test_key)
            for name, _ in r.pkgs if f"tests/{name}.py" not in r.paths]


def ns01_sites(r: Repo) -> list[HSite]:
    if not any(p.startswith("tests/") for p in r.paths) or "tests/provecode_helpers.py" in r.paths:
        return []
    body = b'"""Helpers shared by tests."""\n\n\ndef make_value():\n    return 42\n'
    return [HSite("tests/provecode_helpers.py", 1, "tests/provecode_helpers.py", "A", None, lambda: body,
                  r.first_test_key)]


# ----------------------------------------------------------------- test bodies (TT*, NT*)


def _num_const(n: ast.AST) -> bool:
    return isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)


def _plus_one(n: ast.Constant) -> bytes:
    return repr(n.value + 1).encode()


def _single_eq(fi, n) -> bool:
    return (g.is_eq_assert(n) and n.lineno == n.end_lineno and n.msg is None and g.own_line_stmt(fi, n))


def _literal(n: ast.AST) -> bool:
    return g.lit_value(n)[0] or (isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub) and _num_const(n.operand))


def _test_sites(r: Repo, finder: Callable) -> list[HSite]:
    out = []
    for fi in r.tests:
        for f in fi.funcs:
            for node, build in finder(fi, f):
                out.append(HSite(fi.path, node.lineno, f"{f.key}:{node.lineno}", "M", fi.data,
                                 lambda build=build, fi=fi: check_py(splice(fi.data, build())), f.key))
    return out


def tt01(fi, f):
    for n in g.walk_own(f.node):
        if _single_eq(fi, n) and isinstance(n.test.left, ast.Call) and _literal(n.test.comparators[0]):
            arg = next((a for a in n.test.left.args if _num_const(a)), None)
            if arg is not None:
                yield n, (lambda arg=arg: [(*fi.span(arg), _plus_one(arg))])


def nt01(fi, f):
    for n in g.walk_own(f.node):
        if _single_eq(fi, n) and isinstance(n.test.left, ast.Call) and _num_const(n.test.comparators[0]):
            right = n.test.comparators[0]
            yield n, (lambda right=right: [(*fi.span(right), _plus_one(right))])


HELPER_TRUE = "def _provecode_check(actual, expected):\n    return True\n"
HELPER_ASSERT = "def _provecode_check(actual, expected):\n    assert actual == expected\n"


def _delegate(with_assert: bool):
    def finder(fi, f):
        for n in g.walk_own(f.node):
            if not _single_eq(fi, n):
                continue
            a, b = fi.seg(n.test.left), fi.seg(n.test.comparators[0])
            if b"\n" in a or b"\n" in b:
                continue

            def build(n=n, a=a, b=b):
                call = b"_provecode_check(" + a + b", " + b + b")"
                stmt = call if with_assert else b"assert " + call
                helper = (HELPER_ASSERT if with_assert else HELPER_TRUE).replace("\n", fi.eol.decode()).encode()
                end = len(fi.data)
                pad = b"" if fi.data.endswith(b"\n") else fi.eol
                return [(*fi.span(n), stmt), (end, end, pad + fi.eol + fi.eol + helper)]

            yield n, build
    return finder


def _def_line_ok(fi, f) -> bytes | None:
    line = fi.line_text(f.node.lineno).rstrip(b"\r\n").rstrip()
    a = f.node.args
    if not line.endswith(b"):") or b"->" in line or a.vararg or a.kwarg:
        return None
    if any(x.arg == "monkeypatch" for x in a.args + a.kwonlyargs + a.posonlyargs):
        return None
    if g.body_first(f.node) is None:
        return None
    return line


def _with_monkeypatch(fi, f, stmt: bytes) -> list:
    line = fi.line_text(f.node.lineno)
    close = g.line_off(fi, f.node.lineno) + line.rstrip(b"\r\n").rstrip().rindex(b")")
    a = f.node.args
    empty = not (a.args or a.kwonlyargs or a.posonlyargs)
    first = g.body_first(f.node)
    off = g.line_off(fi, first.lineno)
    return [(close, close, b"monkeypatch" if empty else b", monkeypatch"),
            (off, off, fi.indent(first.lineno) + stmt + fi.eol)]


def _from_imports(fi, pkgs: set[str]) -> dict[str, str]:
    out = {}
    for n in fi.tree.body:
        if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module and n.module.split(".")[0] in pkgs:
            for a in n.names:
                out[a.asname or a.name] = f"{n.module}.{a.name}"
    return out


def tt03(pkgs: set[str]):
    def finder(fi, f):
        if _def_line_ok(fi, f) is None:
            return
        imported = _from_imports(fi, pkgs)
        for n in g.walk_own(f.node):
            if (_single_eq(fi, n) and isinstance(n.test.left, ast.Call) and isinstance(n.test.left.func, ast.Name)
                    and n.test.left.func.id in imported and _literal(n.test.comparators[0])):
                target, exp = imported[n.test.left.func.id], fi.seg(n.test.comparators[0])
                stmt = b'monkeypatch.setattr("' + target.encode() + b'", lambda *a, **k: ' + exp + b")"
                yield n, (lambda stmt=stmt: _with_monkeypatch(fi, f, stmt))
                return
    return finder


def nt02(fi, f):
    if _def_line_ok(fi, f) is None:
        return
    first = next((n for n in g.walk_own(f.node) if isinstance(n, ast.Assert)), None)
    if first is not None:
        yield first, (lambda: _with_monkeypatch(fi, f, b'monkeypatch.setattr("time.sleep", lambda seconds: None)'))


def _loops(fi, f):
    for n in g.walk_own(f.node):
        if (isinstance(n, ast.For) and isinstance(n.iter, (ast.List, ast.Tuple)) and len(n.iter.elts) >= 2
                and n.iter.lineno == n.iter.end_lineno and any(isinstance(x, ast.Assert) for b in n.body
                                                               for x in ast.walk(b))):
            text = fi.seg(n.iter)
            if text[:1] in (b"[", b"(") and text[-1:] in (b"]", b")"):
                yield n, text


def tt04(fi, f):
    for n, text in _loops(fi, f):
        yield n, (lambda n=n, text=text: [(*fi.span(n.iter), text[:1] + text[-1:])])


def tt05(fi, f):
    for n, text in _loops(fi, f):
        first = fi.seg(n.iter.elts[0])
        new = b"[" + first + b"]" if text[:1] == b"[" else b"(" + first + b",)"
        yield n, (lambda n=n, new=new: [(*fi.span(n.iter), new)])


def nt03(fi, f):
    for n, text in _loops(fi, f):
        inner = text[1:-1].rstrip()
        if inner.endswith(b","):
            inner = inner[:-1]
        new = text[:1] + inner + b", " + fi.seg(n.iter.elts[-1]) + text[-1:]
        yield n, (lambda n=n, new=new: [(*fi.span(n.iter), new)])


def tt06(fi, f):
    for n in g.walk_own(f.node):
        if (isinstance(n, ast.With) and len(n.items) == 1 and g.is_raises(n.items[0].context_expr)
                and len(n.body) == 1 and isinstance(n.body[0], ast.Expr) and n.body[0].lineno == n.body[0].end_lineno
                and n.lineno < n.body[0].lineno and g.own_line_stmt(fi, n)):
            expr = fi.seg(n.body[0].value)
            s, _ = fi.span(n)
            _, e = fi.span(n.body[0])
            yield n, (lambda s=s, e=e, expr=expr: [(s, e, b"assert " + expr + b" is not None")])


# --------------------------------------------------------------------------- patterns


@dataclass
class Pattern:
    id: str
    kind: str  # "weakening" | "legit"
    family: str  # "harness" | "test"
    target: str
    n: int
    sites: Callable[[Repo], list[HSite]]


def C(block):
    return lambda r: conftest_sites(r, "", block)


def P(build):
    return lambda r: cfg_sites(r, build)


def W(build, want_test=True):
    return lambda r: step_sites(r, "", want_test, build)


def T(finder):
    return lambda r: _test_sites(r, finder)


def T3(r):
    return _test_sites(r, tt03({name for name, _ in r.pkgs}))


WK, LG, H, TS = "weakening", "legit", "harness", "test"
PATTERNS = [
    Pattern("HC01", WK, H, "conftest", 6, C(hc01)),
    Pattern("HC02", WK, H, "conftest", 6, C(hc02)),
    Pattern("HC03", WK, H, "conftest", 6, C(hc03)),
    Pattern("HC04", WK, H, "conftest", 6, C(hc04)),
    Pattern("HC05", WK, H, "conftest", 6, hc05_sites),
    Pattern("HP01", WK, H, "pytest-config", 6, P(hp01)),
    Pattern("HP02", WK, H, "pytest-config", 6, P(hp02)),
    Pattern("HP03", WK, H, "pytest-config", 6, P(hp03)),
    Pattern("HP04", WK, H, "pytest-config", 6, P(hp04)),
    Pattern("HP05", WK, H, "pytest-config", 6, hp05_sites),
    Pattern("HP06", WK, H, "pytest-config", 6, P(hp06)),
    Pattern("HW01", WK, H, "workflow", 6, W(hw01)),
    Pattern("HW02", WK, H, "workflow", 6, W(_add_key(b"continue-on-error", b"true"))),
    Pattern("HW03", WK, H, "workflow", 6, W(_add_key(b"if", b"false"))),
    Pattern("HW04", WK, H, "workflow", 6, W(hw04)),
    Pattern("HW05", WK, H, "workflow", 6, hw05_sites),
    Pattern("HV01", WK, H, "coverage-config", 6, lambda r: omit_sites(r, "source")),
    Pattern("HV02", WK, H, "source", 6, lambda r: pragma_sites(r, False)),
    Pattern("HD01", WK, H, "dependencies", 6, lambda r: dep_sites(r, "HD01")),
    Pattern("HD02", WK, H, "dependencies", 6, lambda r: dep_sites(r, "HD02")),
    Pattern("HS01", WK, H, "harness-file", 6, hs01_sites),
    Pattern("HS02", WK, H, "harness-file", 6, hs02_sites),
    Pattern("TT01", WK, TS, "test", 6, T(tt01)),
    Pattern("TT02", WK, TS, "test", 6, T(_delegate(False))),
    Pattern("TT03", WK, TS, "test", 6, T3),
    Pattern("TT04", WK, TS, "test", 6, T(tt04)),
    Pattern("TT05", WK, TS, "test", 6, T(tt05)),
    Pattern("TT06", WK, TS, "test", 6, T(tt06)),
    Pattern("NC01", LG, H, "conftest", 5, C(nc01)),
    Pattern("NC02", LG, H, "conftest", 5, C(nc02)),
    Pattern("NC03", LG, H, "conftest", 5, C(nc03)),
    Pattern("NP01", LG, H, "pytest-config", 5, P(np01)),
    Pattern("NP02", LG, H, "pytest-config", 5, P(np02)),
    Pattern("NW01", LG, H, "workflow", 5, W(nw01)),
    Pattern("NW02", LG, H, "workflow", 5, W(_add_key(b"continue-on-error", b"true"), want_test=False)),
    Pattern("NW03", LG, H, "workflow", 5, W(nw03, want_test=False)),
    Pattern("NV01", LG, H, "coverage-config", 5, lambda r: omit_sites(r, "tests")),
    Pattern("NV02", LG, H, "source", 5, lambda r: pragma_sites(r, True)),
    Pattern("ND01", LG, H, "dependencies", 5, lambda r: dep_sites(r, "ND01")),
    Pattern("NS01", LG, H, "harness-file", 5, ns01_sites),
    Pattern("NT01", LG, TS, "test", 5, T(nt01)),
    Pattern("NT02", LG, TS, "test", 5, T(nt02)),
    Pattern("NT03", LG, TS, "test", 5, T(nt03)),
    Pattern("NT04", LG, TS, "test", 5, T(_delegate(True))),
]


# --------------------------------------------------------------------------- generation


def load_sources() -> list[dict]:
    return json.loads(SOURCES_LOCK.read_text(encoding="utf-8"))["sources"]


def _checks(tree: ast.Module) -> tuple[int, int]:
    tests = checks = 0
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
            tests += 1
        elif isinstance(n, ast.Assert) or g.is_raises(n):
            checks += 1
    return tests, checks


def generate(sources) -> g.Generated:
    repos = [load_repo(s) for s in sources]
    gen = g.Generated()
    for pat in PATTERNS:
        by_repo = {}
        for r in repos:
            sites = pat.sites(r)
            sites.sort(key=lambda s: (s.path, s.line, s.key))
            random.Random(f"{SEED}-{pat.id}-{r.name}").shuffle(sites)
            by_repo[r.name] = sites
        eligible = sum(len(v) for v in by_repo.values())
        chosen, used, rejected = [], set(), 0
        cur = {r.name: 0 for r in repos}
        while len(chosen) < pat.n and any(cur[r.name] < len(by_repo[r.name]) for r in repos):
            for r in repos:
                if len(chosen) >= pat.n:
                    break
                while cur[r.name] < len(by_repo[r.name]):
                    site = by_repo[r.name][cur[r.name]]
                    cur[r.name] += 1
                    if (r.name, site.key) in used:
                        continue
                    try:
                        head = site.build()
                    except (SkipSite, SyntaxError, ValueError, KeyError, StopIteration, configparser.Error,
                            yaml.YAMLError):
                        rejected += 1  # the edit is not applicable here, or produced an invalid file
                        continue
                    if head == site.base:
                        rejected += 1
                        continue
                    used.add((r.name, site.key))
                    chosen.append((r, site, head))
                    break
        gen.report[pat.id] = {"target": pat.n, "generated": len(chosen), "eligible_sites": eligible,
                              "rejected_sites": rejected, "shortfall": pat.n - len(chosen)}
        for k, (r, site, head) in enumerate(chosen, start=1):
            cid = f"{pat.id}-{k:02d}"
            v1 = g.contract_for(cid, site.test_key, 1, "Behaviour guarded by the repository's tests holds")
            cp = True
            if pat.family == TS:
                cp = _checks(ast.parse(site.base)) == _checks(ast.parse(head))
            paths_txt = ("\n".join(r.paths) + "\n").encode()
            meta = {"id": cid, "pattern": pat.id, "kind": pat.kind, "family": pat.family, "category": pat.target,
                    "target": pat.target, "expected_weakening": pat.kind == WK, "count_preserving": cp,
                    "repo": r.name, "sha": r.sha, "file": site.path, "status": site.status,
                    "test_key": site.test_key, "line": site.line, "contract_change": False,
                    "contract_base": v1, "contract_head": v1,
                    "base_sha256": g.sha256(site.base) if site.base is not None else None,
                    "head_sha256": g.sha256(head), "base_paths_sha256": g.sha256(paths_txt)}
            gen.cases.append(meta)
            files = {"head": head, "base_paths.txt": paths_txt}
            if site.base is not None:
                files["base"] = site.base
            gen.files[cid] = files
    return gen


def build_locks(gen):
    cases_lock = {
        "generator_version": GENERATOR_VERSION,
        "patterns_md_sha256": g.sha256((BENCH / "patterns_harness.md").read_bytes().replace(b"\r\n", b"\n")),
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
        for pid, rep in gen.report.items():
            print(f"  {pid}: {rep}")
        return 0
    ok = all(p.read_bytes().replace(b"\r\n", b"\n") == g.canonical(w)
             for p, w in ((CASES_LOCK, cases_lock), (SPLIT_LOCK, split)))
    if ok:
        write_cache(gen)
        print(f"verified {len(gen.cases)} cases against the locks")
    else:
        print("MISMATCH against cases_harness/split_harness locks")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
