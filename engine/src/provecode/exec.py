"""Recorded runs: contract-owned checks and the repo's own tests in Docker (Gate 1b).

CLI/CI only; never part of the browser path. Repository code is treated as
untrusted: it runs in a fresh container with no network, a read-only root
filesystem and source mount, and CPU, memory, process and time limits.
Docker is not a complete security boundary (architecture section 7).
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from . import contract as contract_mod
from .decide import Evidence
from .model import Gap

EXCERPT_LINES = 30


def _image_identity(image: str) -> dict:
    out = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}|{{json .RepoDigests}}"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"docker image inspect {image}: {out.stderr.strip()}")
    image_id, digests = out.stdout.strip().split("|", 1)
    return {"image": image, "image_id": image_id, "repo_digests": digests}


def _export_tree(repo: str, sha: str, dest: Path) -> None:
    """Copy the head tree out with git archive; the user's working copy is never touched."""
    out = subprocess.run(["git", "-C", repo, "archive", "--format=tar", sha], capture_output=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"git archive failed: {out.stderr.decode('utf-8', 'replace')}")
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tf:
        root = dest.resolve()
        members = []
        for m in tf.getmembers():
            if m.name == "pax_global_header" or m.type == tarfile.XGLTYPE:
                continue
            target = (root / m.name).resolve()
            if not (m.isfile() or m.isdir()) or (target != root and root not in target.parents):
                raise RuntimeError(f"refusing archive member {m.name!r} (type {m.type!r})")
            members.append(m)
        if hasattr(tarfile, "data_filter"):  # Python >= 3.11.4
            tf.extractall(dest, members=members, filter="data")
        else:
            tf.extractall(dest, members=members)


# Runs inside the container as `python -I -c RUNNER <target> <junit> <rootdir>`.
# -I ignores PYTHONPATH and user site and does not prepend the working directory, so the
# candidate cannot shadow pytest or the stdlib, and a candidate sitecustomize.py is never
# imported at startup. The candidate tree is APPENDED to sys.path, after site-packages.
RUNNER = (
    "import sys; sys.path.append('/work/src'); import pytest; "
    "sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', '--rootdir', sys.argv[3], "
    "'--junitxml=' + sys.argv[2], sys.argv[1]]))"
)
MAX_JUNIT_BYTES = 1_000_000


def _run(image: str, src: Path, checks: Path, results: Path, workdir: str, argv: list[str],
         timeout: int) -> tuple[int | None, str, float]:
    name = f"provecode-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker", "run", "--rm", "--name", name,
        "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=64m",
        "--memory", "512m", "--memory-swap", "512m", "--cpus", "1", "--pids-limit", "128",
        "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--user", "65534:65534",
        "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp",
        "-v", f"{src}:/work/src:ro", "-v", f"{checks}:/work/checks:ro", "-v", f"{results}:/work/results:rw",
        "-w", workdir, image, *argv,
    ]
    t0 = time.monotonic()
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
        return None, "timeout", time.monotonic() - t0
    text = (out.stdout + out.stderr).decode("utf-8", "replace")
    return out.returncode, text, time.monotonic() - t0


def junit_summary(path: Path) -> dict | None:
    """Counts from a pytest JUnit XML report, or None if it is missing or malformed."""
    if not path.is_file() or path.stat().st_size > MAX_JUNIT_BYTES:
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if not suites:
        return None
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for s in suites:
        for k in total:
            try:
                total[k] += int(s.get(k, "0"))
            except ValueError:
                return None
    return total


def classify(code: int | None, junit: dict | None) -> str:
    """pass needs positive evidence: exit 0 AND a report with at least one test that actually passed.

    An exit code alone is never trusted (a candidate could exit 0 at import time).
    """
    if code == 0 and junit and junit["tests"] > junit["skipped"] and junit["failures"] == 0 and junit["errors"] == 0:
        return "pass"
    if code == 1 and junit and junit["failures"] + junit["errors"] > 0:
        return "fail"
    return "error"


def _evidence(eid: str, check_id: str, cids: list[str], owner: str, code: int | None, text: str,
              cmd: str, dur: float, junit: dict | None) -> Evidence:
    excerpt = "\n".join(text.strip().splitlines()[-EXCERPT_LINES:])
    if junit is None:
        excerpt += "\n[provecode] no valid JUnit report was produced, so this result cannot count as a pass."
    return Evidence(eid, check_id, cids, owner, classify(code, junit), code, excerpt,
                    hashlib.sha256(text.encode()).hexdigest(), cmd, round(dur, 2))


def run_checks(args, contract_doc: dict | None, head_sha: str) -> tuple[list[Evidence], list[Gap], dict]:
    if contract_doc is None:
        return [], [Gap("contract_invalid", "No contract; recorded checks not run.")], {}
    try:
        c = contract_mod.load(contract_doc)
    except contract_mod.ContractError as exc:
        return [], [Gap("contract_invalid", str(exc))], {}
    ident = _image_identity(args.image)
    evidence: list[Evidence] = []
    gaps: list[Gap] = []
    work = Path(tempfile.mkdtemp(prefix="provecode-run-"))
    try:
        src, checks, results = work / "src", work / "checks", work / "results"
        src.mkdir()
        results.mkdir()
        os.chmod(results, 0o777)  # the container runs as nobody (65534)
        _export_tree(args.repo, head_sha, src)
        shutil.copytree(Path(args.checks_dir), checks)
        by_check = {ch["id"]: ch for ch in c.checks}
        n = 0
        for cond in c.conditions:
            for check_id in cond.checks:
                ch = by_check[check_id]
                if ch["kind"] != "pytest":
                    gaps.append(Gap("unsupported", f"{check_id}: kind {ch['kind']} is not supported in Phase 1",
                                    condition_id=cond.id))
                    continue
                n += 1
                junit = f"/work/results/E{n}.xml"
                argv = ["python", "-I", "-c", RUNNER, f"/work/checks/{ch['path']}", junit, "/work/checks"]
                code, text, dur = _run(args.image, src, checks, results, "/work/checks", argv, args.timeout)
                ev = _evidence(f"E{n}", check_id, [cond.id], "contract", code, text,
                               f"pytest {ch['path']} (isolated runner, JUnit report required)", dur,
                               junit_summary(results / f"E{n}.xml"))
                if code is None:
                    gaps.append(Gap("timeout", f"{check_id} exceeded {args.timeout}s", condition_id=cond.id))
                evidence.append(ev)
        n += 1
        argv = ["python", "-I", "-c", RUNNER, "/work/src", f"/work/results/E{n}.xml", "/work/src"]
        code, text, dur = _run(args.image, src, checks, results, "/work/src", argv, args.timeout)
        evidence.append(_evidence(f"E{n}", "repo-tests", [], "repo", code, text,
                                  "pytest (the repository's own suite)", dur, junit_summary(results / f"E{n}.xml")))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    prov = {**ident, "sandbox": {"network": "none", "memory": "512m", "cpus": 1, "pids": 128, "read_only": True,
                                 "user": "65534:65534", "timeout_s": args.timeout,
                                 "runner": "python -I, candidate tree appended to sys.path, JUnit report required"}}
    return evidence, gaps, prov
