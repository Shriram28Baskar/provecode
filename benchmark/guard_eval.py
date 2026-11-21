"""ADR 0010 drop criteria for `provecode guard`, measured on the real source repositories.

  python -m benchmark.guard_eval            write artifacts/benchmark/guard.json
  python -m benchmark.guard_eval --parity   check parity only (CI); writes nothing

For every Tier A and Tier A-u case, in a temporary copy of the case's pinned repository:
1. the case's head file is written to the working tree, and `guard` collects the change;
2. the same edit is committed, and commit mode (`provecode check`'s collector) collects it.
Both go through the same pipeline with the case's contracts, and the reports must be identical
(apart from provenance). Then the real `provecode guard` CLI is timed end to end as a subprocess,
including interpreter start-up. Latency is machine-dependent and not CI-diffed; parity is.
Inputs: benchmark/.cache (run `python -m benchmark.regenerate` first). The cache is never modified.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from argparse import Namespace
from collections import defaultdict
from pathlib import Path

from provecode import cli, contract as contract_mod, guard, pipeline
from provecode.limits import Limits

from . import generate_cases, generate_cases_unittest

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "benchmark" / "guard.json"
APPROVER, APPROVED_AT = "tierA-harness (out of band)", "2026-11-20T00:00:00Z"
TIERS = (("A", generate_cases), ("A-u", generate_cases_unittest))
GIT = ["git", "-c", "user.name=guard-eval", "-c", "user.email=guard-eval@example.invalid",
       "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", "-c", "advice.detachedHead=false"]


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run([*GIT, "-C", str(repo), *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in {repo}: {out.stderr.strip()}")
    return out.stdout


def _report_core(rep: dict) -> str:
    return json.dumps({k: v for k, v in rep.items() if k != "provenance"}, sort_keys=True)


def _approved(doc: dict) -> dict:
    return contract_mod.approve(doc, APPROVER, APPROVED_AT)


def _run(files, touched, gaps, meta) -> dict:
    return pipeline.run(files, _approved(meta["contract_head"]), _approved(meta["contract_base"]), mode="static",
                        limits=Limits.cli(), touched_protected=touched, extra_gaps=gaps)


def evaluate(time_cli: bool) -> dict:
    rows, mismatches = [], []
    env = {**os.environ, "PYTHONPATH": str(ROOT / "engine" / "src")}
    with tempfile.TemporaryDirectory(prefix="provecode-guard-eval-") as tmp:
        copies: dict[str, Path] = {}
        for tier, gm in TIERS:
            cases = json.loads(gm.CASES_LOCK.read_text(encoding="utf-8"))["cases"]
            for meta in cases:
                repo = meta["repo"]
                if repo not in copies:
                    src = generate_cases.repo_dir(repo)
                    if not (src / ".git").exists():
                        raise SystemExit(f"{src} missing; run python -m benchmark.regenerate first")
                    dst = Path(tmp) / repo.replace("/", "__")
                    shutil.copytree(src, dst, symlinks=True)
                    copies[repo] = dst
                top = copies[repo]
                _git(top, "reset", "-q", "--hard", meta["sha"])
                _git(top, "clean", "-qfdx")
                head = (gm.CASES_DIR / meta["id"] / "head.py").read_bytes()
                (top / meta["file"]).write_bytes(head)

                files, touched, _, gaps, _, _ = guard.collect(str(top), meta["sha"], False, [])
                rep_w = _run(files, touched, gaps, meta)
                seconds = exit_code = None
                if time_cli:
                    t0 = time.perf_counter()
                    p = subprocess.run([sys.executable, "-m", "provecode.cli", "guard", "--repo", str(top),
                                        "--base", meta["sha"]], capture_output=True, env=env)
                    seconds, exit_code = time.perf_counter() - t0, p.returncode
                    if exit_code not in (0, 1):
                        raise RuntimeError(f"{meta['id']}: guard exited {exit_code}: {p.stderr.decode(errors='replace')}")

                _git(top, "add", "-A")
                _git(top, "commit", "-q", "-m", f"case {meta['id']}")
                files_c, touched_c, _, gaps_c = cli._collect(Namespace(repo=str(top), base=meta["sha"], head="HEAD",
                                                                       protect=[]))
                rep_c = _run(files_c, touched_c, gaps_c, meta)
                same = _report_core(rep_w) == _report_core(rep_c)
                if not same:
                    mismatches.append({"tier": tier, "id": meta["id"], "worktree": rep_w["outcome"],
                                       "commit": rep_c["outcome"],
                                       "worktree_rules": sorted(f["rule"] for f in rep_w["findings"]),
                                       "commit_rules": sorted(f["rule"] for f in rep_c["findings"])})
                rows.append({"tier": tier, "id": meta["id"], "repo": repo, "kind": meta["kind"], "identical": same,
                             "seconds": seconds, "exit_code": exit_code})
    out = {
        "schema_version": "1",
        "adr": "0010",
        "parity": {"cases_compared": len(rows), "identical": sum(r["identical"] for r in rows),
                   "mismatches": mismatches,
                   "compared": "full report except provenance: findings, severities, gaps, outcome, reasons"},
        "command": "python -m benchmark.guard_eval",
    }
    if time_cli:
        secs = sorted(r["seconds"] for r in rows)
        by_repo = defaultdict(list)
        for r in rows:
            by_repo[r["repo"]].append(r["seconds"])
        out["latency"] = {
            "note": "End-to-end `provecode guard` CLI subprocess, including Python start-up, on a copy of the real "
                    "repository with the case's edit uncommitted. Machine-dependent; not CI-diffed.",
            "machine": {"python": platform.python_version(), "system": platform.system(),
                        "machine": platform.machine()},
            "n": len(secs), "median_s": round(statistics.median(secs), 3),
            "p95_s": round(secs[int(0.95 * (len(secs) - 1))], 3), "max_s": round(secs[-1], 3),
            "by_repo_median_s": {k: round(statistics.median(v), 3) for k, v in sorted(by_repo.items())},
            "blocked_without_contract": {
                k: {"blocked": sum(r["exit_code"] == 1 for r in rows if r["kind"] == k),
                    "n": sum(r["kind"] == k for r in rows)} for k in ("weakening", "legit")},
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parity", action="store_true", help="parity only; write nothing")
    args = ap.parse_args(argv)
    res = evaluate(time_cli=not args.parity)
    par = res["parity"]
    print(f"guard parity: {par['identical']} of {par['cases_compared']} cases identical to commit mode")
    for m in par["mismatches"]:
        print("  MISMATCH", m)
    if not args.parity:
        lat = res["latency"]
        print(f"guard latency: median {lat['median_s']} s, p95 {lat['p95_s']} s, max {lat['max_s']} s")
        OUT.write_text(json.dumps(res, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return 0 if not par["mismatches"] else 1


if __name__ == "__main__":
    sys.exit(main())
