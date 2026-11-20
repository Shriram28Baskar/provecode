"""Recorded runs: contract-owned checks and the repo's own tests in Docker (Gate 1b).

CLI/CI only; never part of the browser path. Repository code is treated as
untrusted: it runs in a fresh container with no network, a read-only root
filesystem and source mount, and CPU, memory, process and time limits.
Docker is not a complete security boundary (architecture section 7).
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tarfile
import tempfile
import uuid
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


def _run(image: str, src: Path, checks: Path, argv: list[str], timeout: int) -> tuple[int | None, str, float]:
    name = f"provecode-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker", "run", "--rm", "--name", name,
        "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=64m",
        "--memory", "512m", "--memory-swap", "512m", "--cpus", "1", "--pids-limit", "128",
        "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--user", "65534:65534",
        "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONPATH=/work/src", "-e", "HOME=/tmp",
        "-v", f"{src}:/work/src:ro", "-v", f"{checks}:/work/checks:ro", "-w", "/work/src",
        image, *argv,
    ]
    import time

    t0 = time.monotonic()
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
        return None, "timeout", time.monotonic() - t0
    text = (out.stdout + out.stderr).decode("utf-8", "replace")
    return out.returncode, text, time.monotonic() - t0


def _evidence(eid: str, check_id: str, cids: list[str], owner: str, code: int | None, text: str,
              cmd: str, dur: float) -> Evidence:
    # pytest: 0 all passed, 1 tests failed; anything else (2-5, timeout) is an error, never a pass.
    result = "pass" if code == 0 else "fail" if code == 1 else "error"
    excerpt = "\n".join(text.strip().splitlines()[-EXCERPT_LINES:])
    return Evidence(eid, check_id, cids, owner, result, code, excerpt, hashlib.sha256(text.encode()).hexdigest(),
                    cmd, round(dur, 2))


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
        src, checks = work / "src", work / "checks"
        src.mkdir()
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
                argv = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", "/work/checks",
                        f"/work/checks/{ch['path']}"]
                code, text, dur = _run(args.image, src, checks, argv, args.timeout)
                n += 1
                ev = _evidence(f"E{n}", check_id, [cond.id], "contract", code, text, " ".join(argv[2:]), dur)
                if code is None:
                    gaps.append(Gap("timeout", f"{check_id} exceeded {args.timeout}s", condition_id=cond.id))
                evidence.append(ev)
        argv = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        code, text, dur = _run(args.image, src, checks, argv, args.timeout)
        n += 1
        evidence.append(_evidence(f"E{n}", "repo-tests", [], "repo", code, text, " ".join(argv[2:]), dur))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    prov = {**ident, "sandbox": {"network": "none", "memory": "512m", "cpus": 1, "pids": 128, "read_only": True,
                                 "user": "65534:65534", "timeout_s": args.timeout}}
    return evidence, gaps, prov
