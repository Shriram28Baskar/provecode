"""provecode rerun: the trusted re-run, runs A/B/C in sealed containers (ADR 0013). CLI only.

  A  base code, base tests and harness      the reference
  B  head code, base tests and harness       the trusted result (overlay, provecode.overlay)
  C  head code, head tests and harness       what ordinary CI would say

Dependencies come from the BASE tree only. They are built into a wheelhouse by a networked
container that never sees candidate code. Every test container then runs with --network none,
a read-only root, no capabilities, a non-root user and resource limits. Inside it:
  - a venv is created in tmpfs;
  - the wheelhouse is installed offline;
  - the tree is installed with --no-deps;
  - pytest runs with plugin autoloading disabled and only the plugins the BASE dependencies
    declare (pytest11 entry points) loaded with -p, plus PROVECODE's trusted reporter plugin.
A and B run twice; any disagreement makes a test flaky and excludes it, it never fails the run.

Stated limits:
- In runs B and C the head code and pytest share one process, as they must for the tests to call
  the code. Deliberately malicious head code can tamper with pytest at import time and forge B's
  result. Contract-owned checks through provecode.isolate are the defence for what matters most.
- Docker is not a complete security boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import analyzer
from . import exec as exec_mod
from . import overlay as overlay_mod
from .decide import Evidence
from .harness import _norm
from .limits import Limits
from .model import Finding, Gap

REPORTER = '''"""PROVECODE's trusted pytest reporter (rerun, ADR 0013). Loaded with -p; writes JSON lines."""
import json
import os

_OUT = os.environ["PROVECODE_REPORT"]


def _w(obj):
    with open(_OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj) + "\\n")


def pytest_collection_finish(session):
    _w({"event": "collected", "nodeids": [i.nodeid for i in session.items]})


def pytest_runtest_logreport(report):
    if report.when == "call" or report.outcome != "passed":
        text = ""
        if report.failed:
            text = str(report.longrepr)[-1500:]
        _w({"event": "report", "nodeid": report.nodeid, "when": report.when, "outcome": report.outcome,
            "text": text})


def pytest_sessionfinish(session, exitstatus):
    _w({"event": "finish", "exitstatus": int(exitstatus)})
'''

# Runs inside the test container: venv in tmpfs, offline install, pytest with autoload disabled.
RUN_SCRIPT = r'''
set -u
python -m venv /tmp/venv >/tmp/setup.log 2>&1 || { echo "PROVECODE_SETUP_FAILED venv"; cat /tmp/setup.log; exit 90; }
/tmp/venv/bin/python -m pip install -q --no-index --find-links /work/wh \
  -r /work/wh/provecode-requirements.txt >>/tmp/setup.log 2>&1 \
  || { echo "PROVECODE_SETUP_FAILED dependencies"; tail -n 30 /tmp/setup.log; exit 91; }
cp -a /work/tree /tmp/tree
cd /tmp/tree
if [ -f pyproject.toml ] || [ -f setup.py ] || [ -f setup.cfg ]; then
  SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0 /tmp/venv/bin/python -m pip install -q --no-index --find-links /work/wh \
    --no-build-isolation --no-deps . >>/tmp/setup.log 2>&1 \
    || { echo "PROVECODE_SETUP_FAILED install"; tail -n 30 /tmp/setup.log; exit 92; }
fi
SITE=$(/tmp/venv/bin/python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
cp /work/plugin/provecode_reporter.py "$SITE/"
PLUGINS=$(tr -d '\r' < /work/wh/provecode-plugins.txt)
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PROVECODE_REPORT=/work/results/report.jsonl HOME=/tmp
/tmp/venv/bin/python -I -m pytest -p provecode_reporter $PLUGINS -p no:cacheprovider -q -rN \
  --rootdir /tmp/tree "$@"
'''

# Runs inside the networked wheelhouse container, which sees only the BASE tree.
WHEEL_SCRIPT = r'''
set -u
python -m pip wheel -q --retries 5 --timeout 60 --wheel-dir /work/wh -r /work/wh/provecode-requirements.txt \
  -c /work/wh/provecode-constraints.txt 2>&1 | tail -n 30
test "${PIPESTATUS[0]}" -eq 0 || exit 93
'''

TEST_ONLY_NAMES = ("test", "tests", "testing")
TEST_GROUP_NAMES = ("dev", "develop", "development")  # fallback when nothing is named for tests


@dataclass
class RunResult:
    name: str
    exit_code: int | None
    seconds: float
    collected: list[str] = field(default_factory=list)
    outcomes: dict[str, str] = field(default_factory=dict)  # nodeid -> passed | failed | skipped
    texts: dict[str, str] = field(default_factory=dict)
    setup_failed: str | None = None
    output_tail: str = ""
    pytest_seconds: float | None = None  # pytest's own session time, for the overhead figure


def _dep_lines(base: Path) -> tuple[list[str], list[str]]:
    """(requirements, notes) for the base tree: runtime dependencies, test extras and groups,
    test requirement files, build backends, and pytest."""
    reqs, notes = [], []
    pp = base / "pyproject.toml"
    if pp.exists():
        try:
            doc = tomllib.loads(pp.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            notes.append(f"pyproject.toml unreadable: {exc}")
            doc = {}
        reqs += [str(x) for x in doc.get("build-system", {}).get("requires", []) or []]
        proj = doc.get("project", {}) or {}
        reqs += [str(x) for x in proj.get("dependencies", []) or []]
        groups = doc.get("dependency-groups") or {}
        extras = proj.get("optional-dependencies") or {}

        def expand(name: str, seen: set[str]) -> list[str]:
            out = []
            for item in groups.get(name, []) or []:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict) and isinstance(item.get("include-group"), str) and \
                        item["include-group"] not in seen:
                    out += expand(item["include-group"], seen | {item["include-group"]})
            return out

        # Prefer what is named for tests; use dev groups only when nothing is (a dev group often pulls docs and
        # linters the tests do not need, which made more-itertools' wheelhouse exceed its timeout).
        chosen = [k for k in list(extras) + list(groups) if k.lower() in TEST_ONLY_NAMES]
        if not chosen:
            chosen = [k for k in list(extras) + list(groups) if k.lower() in TEST_GROUP_NAMES]
        for k in chosen:
            if k in extras:
                reqs += [str(x) for x in extras[k]]
            if k in groups:
                reqs += expand(k, {k})
        if chosen:
            notes.append("dependency groups/extras used: " + ", ".join(chosen))
    files = [f for f in sorted(base.rglob("*.txt"))
             if not f.relative_to(base).as_posix().startswith(("docs/", "doc/"))
             and "requirements" in f.relative_to(base).as_posix().lower()]
    test_files = [f for f in files if "test" in f.name.lower()]
    for f in test_files or [f for f in files if re.search(r"(dev|ci)", f.name.lower())]:
        reqs += _read_requirements(f, base, set())
    if not any(_norm(re.split(r"[<>=!~;\[ ]", r.strip(), maxsplit=1)[0]) == "pytest" for r in reqs if r.strip()):
        reqs.append("pytest")
    out, seen = [], set()
    for r in reqs:
        r = r.strip()
        if r and not r.startswith(("-e", "--", "git+")) and r not in seen:
            seen.add(r)
            out.append(r)
    return out, notes


def _read_requirements(f: Path, base: Path, seen: set[Path]) -> list[str]:
    if f in seen or not f.exists():
        return []
    seen.add(f)
    out = []
    # Join backslash continuations, then drop per-requirement options such as --hash (pip-compile
    # --generate-hashes). The exact == pins are kept; hashes are not, because pip's require-hashes
    # mode would then demand hashes for every other requirement too.
    logical, cur = [], ""
    for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
        part = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if part.endswith("\\"):
            cur += part[:-1] + " "
            continue
        logical.append(cur + part)
        cur = ""
    if cur:
        logical.append(cur)
    for line in logical:
        s = re.sub(r"\s--hash[= ]\S+", "", " " + line).strip()
        if not s:
            continue
        if s.startswith(("-r ", "--requirement ")):
            out += _read_requirements((f.parent / s.split(None, 1)[1]).resolve(), base, seen)
        elif s.startswith(("-c ", "-")):
            continue
        else:
            out.append(s)
    return out


def _docker(args: list[str], timeout: int) -> tuple[int | None, str, float]:
    t0 = time.monotonic()
    name = f"provecode-{uuid.uuid4().hex[:12]}"
    try:
        out = subprocess.run(["docker", "run", "--rm", "--name", name, *args], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
        return None, "timeout", time.monotonic() - t0
    return out.returncode, (out.stdout + out.stderr).decode("utf-8", "replace"), time.monotonic() - t0


HOST_PLATFORM_ARGS = ["--only-binary=:all:", "--platform", "manylinux2014_x86_64", "--platform",
                      "manylinux_2_17_x86_64", "--platform", "musllinux_1_1_x86_64", "--platform", "any",
                      "--implementation", "cp", "--abi", "abi3", "--abi", "none"]


NETWORK_ATTEMPTS = 3
NETWORK_ERRORS = (r"ReadTimeout|ConnectTimeout|ConnectionError|Connection reset|IncompleteRead|"
                  r"ProtocolError|RemoteDisconnected|HTTPSConnectionPool|Temporary failure in name resolution|"
                  r"Read timed out|ChunkedEncodingError")


def lock_pins(base: Path) -> tuple[dict[str, str], str]:
    """Exact versions from the base lockfile (uv.lock, poetry.lock or pdm.lock), used as pip constraints.

    Only registry packages locked at exactly one version are pinned; a package locked at several
    versions (marker-split) or from git/path/URL sources is left to pip. Returns (pins, note)."""
    for name in ("uv.lock", "poetry.lock", "pdm.lock"):
        f = base / name
        if not f.exists():
            continue
        try:
            doc = tomllib.loads(f.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            return {}, f"{name} unreadable ({exc}); dependency versions not pinned"
        versions: dict[str, set[str]] = {}
        for pkg in doc.get("package", []) or []:
            if not (isinstance(pkg, dict) and isinstance(pkg.get("name"), str)
                    and isinstance(pkg.get("version"), str)):
                continue
            src = pkg.get("source")
            if name == "uv.lock" and not (isinstance(src, dict) and "registry" in src):
                continue  # the project itself, workspace members, git or path sources
            if name == "poetry.lock" and isinstance(src, dict) and src.get("type") not in (None, "legacy"):
                continue
            versions.setdefault(_norm(pkg["name"]), set()).add(pkg["version"])
        pins = {n: next(iter(v)) for n, v in versions.items() if len(v) == 1}
        return pins, f"pinned {len(pins)} package version(s) from the base {name}"
    return {}, ""


def _container_python(image: str, timeout: int) -> str:
    code, text, _ = _docker(["--network", "none", image, "python", "-c",
                             "import sys; print('%d.%d' % sys.version_info[:2])"], timeout)
    v = text.strip().splitlines()[-1] if code == 0 and text.strip() else ""
    if not re.fullmatch(r"3\.\d+", v):
        raise RuntimeError(f"cannot read the Python version in {image}: {text[-300:]}")
    return v


def build_wheelhouse(image: str, base: Path, wh: Path, timeout: int, mode: str = "host") -> tuple[bool, str, list[str]]:
    """Fill wh with wheels for the BASE dependency list.

    mode "host" (default): `pip download --only-binary=:all:` for the container's Linux platform and
    Python version. It downloads wheels only, so no package code runs on the host, and the
    candidate tree is never involved. Environment markers are evaluated by the host's pip, so a
    Linux-only dependency can be missed; the offline install then fails and the run is
    INCONCLUSIVE, never a silent pass. mode "container": `pip wheel` in a networked container that
    sees only the base dependency list.
    """
    reqs, notes = _dep_lines(base)
    (wh / "provecode-requirements.txt").write_text("\n".join(reqs) + "\n", encoding="utf-8", newline="\n")
    pins, lock_note = lock_pins(base)
    if lock_note:
        notes.append(lock_note)
    cons = wh / "provecode-constraints.txt"
    cons.write_text("".join(f"{n}=={v}\n" for n, v in sorted(pins.items())), encoding="utf-8", newline="\n")
    if mode == "host":
        import sys

        py = _container_python(image, 120)
        abi = "cp" + py.replace(".", "")
        cmd = [sys.executable, "-m", "pip", "download", "-q", "--disable-pip-version-check", "--retries", "5",
               "--timeout", "60", "--dest", str(wh), *HOST_PLATFORM_ARGS, "--python-version", py, "--abi", abi,
               "-r", str(wh / "provecode-requirements.txt"), "-c", str(cons)]
        deadline = time.monotonic() + timeout
        text = ""
        for attempt in range(1, NETWORK_ATTEMPTS + 1):
            left = deadline - time.monotonic()
            if left <= 0:
                return False, f"pip download timed out after {attempt - 1} attempt(s): {text[-600:]}", notes
            try:
                out = subprocess.run(cmd, capture_output=True, timeout=left)
            except subprocess.TimeoutExpired:
                return False, f"pip download timed out (attempt {attempt}): {text[-600:]}", notes
            text = (out.stdout + out.stderr).decode("utf-8", "replace")
            if out.returncode == 0:
                notes.append(f"wheelhouse: host pip download --only-binary for Linux, Python {py}"
                             + (f", after {attempt} attempts" if attempt > 1 else ""))
                return True, "\n".join(text.strip().splitlines()[-30:]), notes
            if not re.search(NETWORK_ERRORS, text):
                break  # a resolution or availability error: retrying cannot help
        return False, "\n".join(text.strip().splitlines()[-30:]), notes
    code, text, _ = _docker(["--network", "bridge", "--memory", "2g", "--cpus", "2", "--user", "65534:65534",
                             "-e", "HOME=/tmp", "-e", "PIP_DISABLE_PIP_VERSION_CHECK=1",
                             "-v", f"{base}:/work/base:ro", "-v", f"{wh}:/work/wh:rw",
                             image, "bash", "-c", WHEEL_SCRIPT], timeout)
    return code == 0, "\n".join(text.strip().splitlines()[-30:]), notes


def discover_plugins(image: str, wh: Path, timeout: int) -> tuple[list[str], str]:
    """pytest11 entry points declared by the base dependency set (installed offline, no tree code)."""
    script = ("set -u; python -m venv /tmp/v >/dev/null && /tmp/v/bin/python -m pip install -q --no-index "
              "--find-links /work/wh -r /work/wh/provecode-requirements.txt >/tmp/log 2>&1 || { tail -n 20 /tmp/log; "
              "exit 91; }; /tmp/v/bin/python -c \"import importlib.metadata as m; "
              "print('\\n'.join(sorted({e.value.split(':')[0] for e in m.entry_points(group='pytest11')})))\"")
    code, text, _ = _docker(["--network", "none", "--read-only", "--tmpfs", "/tmp:rw,exec,size=1g", "--memory", "1g",
                             "--cpus", "1", "--pids-limit", "256", "--security-opt", "no-new-privileges",
                             "--cap-drop", "ALL", "--user", "65534:65534", "-e", "HOME=/tmp",
                             "-v", f"{wh}:/work/wh:ro", image, "bash", "-c", script], timeout)
    if code != 0:
        return [], text
    mods = [m for m in text.split() if re.fullmatch(r"[A-Za-z_][\w.]*", m)]
    return mods, ""


def run_tests(name: str, image: str, tree: Path, wh: Path, plugin_dir: Path, timeout: int) -> RunResult:
    results = Path(tempfile.mkdtemp(prefix=f"provecode-{name}-"))
    os.chmod(results, 0o777)
    try:
        code, text, dur = _docker([
            "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,exec,size=1g",
            "--memory", "1g", "--memory-swap", "1g", "--cpus", "1", "--pids-limit", "256",
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--user", "65534:65534",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{tree}:/work/tree:ro", "-v", f"{wh}:/work/wh:ro", "-v", f"{plugin_dir}:/work/plugin:ro",
            "-v", f"{results}:/work/results:rw", image, "bash", "-c", RUN_SCRIPT, "provecode-run"], timeout)
        rr = RunResult(name, code, round(dur, 2), output_tail="\n".join(text.strip().splitlines()[-40:]))
        durations = re.findall(r" in ([0-9]+(?:\.[0-9]+)?)s", text)
        if durations:
            rr.pytest_seconds = float(durations[-1])
        m = re.search(r"PROVECODE_SETUP_FAILED (\w+)", text)
        if m:
            rr.setup_failed = m.group(1)
        rep = results / "report.jsonl"
        if rep.exists() and rep.stat().st_size <= 20_000_000:
            for line in rep.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event") == "collected":
                    rr.collected = [str(x) for x in ev.get("nodeids", [])]
                elif ev.get("event") == "report":
                    nid, out = str(ev.get("nodeid")), str(ev.get("outcome"))
                    prev = rr.outcomes.get(nid)
                    if out == "failed" or prev is None or (prev == "passed" and out == "skipped"):
                        rr.outcomes[nid] = out
                    if ev.get("text"):
                        rr.texts[nid] = str(ev["text"])
        return rr
    finally:
        shutil.rmtree(results, ignore_errors=True)


def export(repo: str, sha: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    exec_mod._export_tree(repo, sha, dest)


def test_status(base_tree: Path, head_tree: Path, nodeid: str) -> str:
    """unchanged | edited | deleted, for the test function a node id belongs to (static, by AST)."""
    path, _, rest = nodeid.partition("::")
    key = path + "::" + re.sub(r"\[.*$", "", rest)
    b, h = base_tree / path, head_tree / path
    if not h.exists():
        return "deleted"
    pb, _ = analyzer._parse(path, b.read_text(encoding="utf-8", errors="replace"), Limits.cli())
    ph, _ = analyzer._parse(path, h.read_text(encoding="utf-8", errors="replace"), Limits.cli())
    if pb is None or ph is None:
        return "edited"
    tb, th = pb.tests.get(key), ph.tests.get(key)
    if th is None:
        return "deleted"
    if tb is None:
        return "edited"
    return "unchanged" if analyzer._body_dump(tb) == analyzer._body_dump(th) else "edited"


# --------------------------------------------------------------------------- verdict (ADR 0013 section 3)


def _key(nodeid: str) -> str:
    path, _, rest = nodeid.partition("::")
    return path + "::" + re.sub(r"\[.*$", "", rest)


def _fail_text(rr: RunResult, nodeid: str) -> str:
    return " ".join(rr.texts.get(nodeid, "").split())[-400:]


def judge(a1: RunResult, a2: RunResult, b1: RunResult, b2: RunResult, c: RunResult, base_tree: Path,
          head_tree: Path, weakened_tests: set[str]) -> tuple[list[Finding], list[Gap], dict[str, Any]]:
    """(findings, gaps, summary) from the five runs. Pure over its inputs."""
    from .model import X1, X2, X3, X4, X5, X6, Finding, Gap

    findings: list[Finding] = []
    gaps: list[Gap] = []
    for rr in (a1, a2):
        if rr.setup_failed:
            gaps.append(Gap("deps_failed", f"run {rr.name}: {rr.setup_failed} failed in the sealed container "
                                           f"(base dependencies, offline): {rr.output_tail[-600:]}"))
            return findings, gaps, {"baseline": "setup_failed"}
        if rr.exit_code not in (0, 1) or not rr.collected:
            gaps.append(Gap("baseline_broken", f"run {rr.name} (base code, base tests) did not produce a usable "
                                               f"test run (exit {rr.exit_code}): {rr.output_tail[-600:]}"))
            return findings, gaps, {"baseline": "broken"}
    for rr in (b1, b2, c):
        if rr.setup_failed:
            gaps.append(Gap("deps_failed", f"run {rr.name}: the head tree could not be set up with the base "
                                           f"dependencies ({rr.setup_failed}): {rr.output_tail[-600:]}"))
    if any(g.reason == "deps_failed" for g in gaps):
        return findings, gaps, {"baseline": "ok", "head": "setup_failed"}

    a_coll = set(a1.collected) & set(a2.collected)
    a_pass = {n for n in a_coll if a1.outcomes.get(n) == "passed" and a2.outcomes.get(n) == "passed"}
    a_fail = {n for n in a_coll if "failed" in (a1.outcomes.get(n), a2.outcomes.get(n))}
    flaky_a = {n for n in a_coll if a1.outcomes.get(n) != a2.outcomes.get(n)}
    flaky_b = {n for n in set(b1.collected) | set(b2.collected) if b1.outcomes.get(n) != b2.outcomes.get(n)}
    flaky = flaky_a | flaky_b
    if a_fail:
        gaps.append(Gap("baseline_failing", f"{len(a_fail)} base test(s) already fail with the base code in the "
                                            "sealed run and are not used to judge the change: "
                                            + ", ".join(sorted(a_fail)[:10])))
    if flaky:
        gaps.append(Gap("flaky", f"{len(flaky)} test(s) gave different results in two identical runs and are not "
                                 "used: " + ", ".join(sorted(flaky)[:10])))
    lost = sorted(a_pass - set(b1.collected) - flaky)
    by_test: dict[str, list[str]] = {}
    for n in lost:
        by_test.setdefault(_key(n), []).append(n)
    for key, nodes in sorted(by_test.items()):
        findings.append(Finding(
            X1, key.split("::")[0], key, f"{len(nodes)} base test(s) that are collected with the base code are not "
                                         "collected at all with the head code (run B), so they no longer run.",
            detail={"nodeids": nodes[:50], "run_b_output": b1.output_tail[-400:]}))
    judged = sorted(a_pass & set(b1.collected) - flaky)
    failing = [n for n in judged if b1.outcomes.get(n) == "failed" and b2.outcomes.get(n) == "failed"]
    for n in failing:
        cstat = c.outcomes.get(n) if n in c.collected else None
        status = test_status(base_tree, head_tree, n)
        excerpt = _fail_text(b1, n)
        if cstat == "failed":
            rule, why = X6, "fails against the head code, and the head's own version of it fails too"
        elif status == "unchanged":
            rule, why = X2, ("fails against the head code with the base harness but passes in ordinary CI; the test "
                             "is unchanged, so the head's harness (conftest, config or plugins) is hiding the failure")
        elif status == "deleted":
            rule, why = X3, "was deleted by the change, and the behaviour it checked no longer holds"
        elif _key(n) in weakened_tests:
            rule, why = X4, "fails against the head code; the change weakened this test so that it passes"
        else:
            rule, why = X5, "fails against the head code; the change edited this test's expectation"
        findings.append(Finding(rule, n.split("::")[0], _key(n), f"The base branch's test {n} {why}.",
                                after=excerpt or None, detail={"nodeid": n, "run_c": cstat or "not collected",
                                                               "test_status": status}))
    summary = {
        "baseline": "ok",
        "collected": {"A": len(a1.collected), "B": len(b1.collected), "C": len(c.collected)},
        "judged": len(judged), "failing_in_b": len(failing), "lost_in_b": len(lost),
        "baseline_failing": len(a_fail), "flaky": len(flaky),
        "c_failed": sum(1 for v in c.outcomes.values() if v == "failed"),
    }
    return findings, gaps, summary


def run_b_result(findings: list[Any]) -> str:
    """Run B's evidence result for the implicit contract.

    fail: a blocking execution finding (X1 lost collection, X2 harness-masked, X3 deleted test, X4 weakened
    test, X6 regression). expectation_changed: only X5, an edited expectation with no weakening finding,
    which the base tests alone cannot judge; decide() treats it as missing evidence, so INCONCLUSIVE.
    pass: every judged base test passed."""
    rules = {f.rule for f in findings}
    if rules - {"X5"}:
        return "fail"
    return "expectation_changed" if rules else "pass"


IMPLICIT_ID = "implicit-base-tests"


def implicit_contract(approved_at: str) -> dict[str, Any]:
    """With no contract, the base branch's own tests are the contract (ADR 0013): one mandatory
    condition, verified by run B."""
    from . import contract as contract_mod

    doc = {"contract": {"id": IMPLICIT_ID, "version": 1},
           "conditions": [{"id": "BASE-TESTS", "description": "The base branch's tests (and harness) pass against "
                                                              "the head code in a sealed run (run B).",
                           "type": "protected-behavior", "mandatory": True, "guarded_by": [], "checks": ["run-B"]}],
           "checks": [{"id": "run-B", "kind": "pytest", "path": "(the base branch's test suite)"}]}
    return contract_mod.approve(doc, "provecode (implicit contract: the base branch's tests)", approved_at)


# --------------------------------------------------------------------------- orchestration


def _own_pytest11(base_tree: Path) -> list[str]:
    """pytest11 plugins the BASE project itself declares (trusted, e.g. anyio's own plugin)."""
    pp = base_tree / "pyproject.toml"
    if not pp.exists():
        return []
    try:
        doc = tomllib.loads(pp.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return []
    ep = ((doc.get("project") or {}).get("entry-points") or {}).get("pytest11") or {}
    return sorted({str(v).split(":")[0] for v in ep.values()}) if isinstance(ep, dict) else []


def _evidence(eid: str, check: str, cids: list[str], owner: str, rr: RunResult, result: str,
              what: str) -> Evidence:
    from .decide import Evidence

    counts = {k: sum(1 for v in rr.outcomes.values() if v == k) for k in ("passed", "failed", "skipped")}
    text = rr.output_tail
    import hashlib

    return Evidence(eid, check, cids, owner, result, rr.exit_code,
                    f"{what}: {len(rr.collected)} collected, {counts['passed']} passed, {counts['failed']} failed, "
                    f"{counts['skipped']} skipped, {rr.seconds}s" + (f"; setup failed ({rr.setup_failed})"
                                                                     if rr.setup_failed else ""),
                    hashlib.sha256(text.encode()).hexdigest(), f"pytest ({what}, sealed container)", rr.seconds)


def run(args: argparse.Namespace) -> dict[str, Any]:
    """The rerun report for args.repo, args.base..args.head (ADR 0013)."""
    from . import cli, pipeline
    from .model import WEAKENING_RULES, Gap

    cs, touched, prov, gaps = cli._collect(args)
    base, head = prov["base_sha"], prov["head_sha"]
    static = pipeline.run(cs, None, mode="static", touched_protected=touched, extra_gaps=list(gaps))
    weakened = {f["test"] for f in static["findings"] if f["rule"] in WEAKENING_RULES and f.get("test")}
    t0 = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="provecode-rerun-"))
    findings: list[Finding] = []
    extra_gaps: list[Gap] = []
    summary: dict[str, Any] = {}
    evidence: list[Evidence] = []
    ov: overlay_mod.Overlay | None = None
    dep_notes: list[str] = []
    timings: dict[str, float | None] = {}
    try:
        base_tree, head_tree, b_tree = work / "base", work / "head", work / "overlay"
        wh, plug = work / "wheelhouse", work / "plugin"
        export(args.repo, base, base_tree)
        export(args.repo, head, head_tree)
        for d in (wh, plug):
            d.mkdir()
            os.chmod(d, 0o777)
        (plug / "provecode_reporter.py").write_text(REPORTER, encoding="utf-8", newline="\n")
        try:
            ov = overlay_mod.build(base_tree, head_tree, b_tree)
        except overlay_mod.OverlayError as exc:
            extra_gaps.append(Gap("overlay_failed", str(exc)))
        t = time.monotonic()
        ok, wtext, notes = build_wheelhouse(args.image, base_tree, wh, args.deps_timeout,
                                            getattr(args, "wheelhouse", "host"))
        timings["wheelhouse_s"] = round(time.monotonic() - t, 2)
        dep_notes = notes
        perr = ""
        if ok and ov is not None:
            plugins, perr = discover_plugins(args.image, wh, args.timeout)
            if perr:
                # Without the base's plugins the tests could fail for the wrong reason: never judge then.
                ok = False
                wtext = "discovering the pytest plugins of the base dependencies failed: " + perr
        if not ok:
            extra_gaps.append(Gap("deps_failed", "building the wheelhouse from the base dependencies failed: "
                                                 + wtext[-800:]))
        elif ov is not None:
            plugins = sorted(set(plugins) | set(_own_pytest11(base_tree)))
            (wh / "provecode-plugins.txt").write_text(" ".join(f"-p {m}" for m in plugins) + "\n", encoding="utf-8",
                                                      newline="\n")
            runs = {}
            for name, tree in (("A1", base_tree), ("A2", base_tree), ("B1", b_tree), ("B2", b_tree), ("C", head_tree)):
                runs[name] = run_tests(name, args.image, tree, wh, plug, args.timeout)
                timings[f"{name}_s"] = runs[name].seconds
                timings[f"{name}_pytest_s"] = runs[name].pytest_seconds
            findings, jgaps, summary = judge(runs["A1"], runs["A2"], runs["B1"], runs["B2"], runs["C"],
                                             base_tree, head_tree, weakened)
            extra_gaps += jgaps
            summary["plugins"] = plugins
            usable = not any(g.reason in ("baseline_broken", "deps_failed") for g in jgaps)
            b_result = run_b_result(findings) if usable else "error"
            if b_result == "expectation_changed":
                n = sum(1 for f in findings if f.rule == "X5")
                extra_gaps.append(Gap("expectation_changed", f"The change edited {n} base test(s) whose old "
                                                             "expectation "
                                                             "now fails against the head code, without weakening them. "
                                                             "The base tests cannot tell an intended behaviour change "
                                                             "from a regression: approve the new behaviour in a "
                                                             "contract (provecode record), or review it."))
            evidence = [
                _evidence("E-A", "run-A", [], "repo", runs["A1"], "pass" if usable else "error",
                          "run A: base code, base tests"),
                _evidence("E-B", "run-B", ["BASE-TESTS"], "contract", runs["B1"], b_result,
                          "run B: head code, base tests and harness"),
                _evidence("E-C", "run-C", [], "repo", runs["C"],
                          "error" if runs["C"].setup_failed or runs["C"].exit_code not in (0, 1)
                          or not runs["C"].collected else "fail" if summary.get("c_failed") else "pass",
                          "run C: head code, head tests"),
            ]
    finally:
        shutil.rmtree(work, ignore_errors=True)
    timings["total_s"] = round(time.monotonic() - t0, 2)
    ident = exec_mod._image_identity(args.image)
    prov = {**prov, **ident, "rerun": {
        "summary": summary, "timings": timings, "dependencies": dep_notes,
        "overlay": {"replaced": ov.replaced, "deleted": ov.deleted, "reasons": ov.reasons} if ov else None,
        "sandbox": {"network": "none (tests); bridge (wheelhouse, base tree only)", "memory": "1g", "cpus": 1,
                    "pids": 256, "read_only": True, "user": "65534:65534", "timeout_s": args.timeout,
                    "plugins": "autoload disabled; -p for base-declared pytest11 plugins only"}}}
    approved_at = prov.get("generated_at", "1970-01-01T00:00:00+00:00")
    return pipeline.run(cs, implicit_contract(approved_at), None, mode="recorded", touched_protected=touched,
                        evidence=evidence, executed=bool(evidence), extra_gaps=list(gaps) + extra_gaps,
                        extra_findings=findings, provenance=prov)
