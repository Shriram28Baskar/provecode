"""Real-history flag rate after ADR 0012 (harness rules H1-H7, test-body rules T1-T5).

Research measurement, design fixed before the first run. Not product code; edits nothing in the
analyzed repositories. Third-party code stays in the git-ignored cache.

- Repositories: the same 18 benchmark source repositories as real_history_scan.py (the Tier H
  repositories are deliberately not scanned, so this cannot leak into Tier H).
- Population T (comparable to v1): per repository, the most recent 200 non-merge commits reachable
  from the pinned SHA that change a test file. This is exactly v1's selection.
- Population H (new): per repository, the most recent 200 non-merge commits reachable from the
  pinned SHA that change a conftest.py, a pytest/coverage config file, a requirements file, a
  GitHub workflow or a .pth/sitecustomize file. Commits may be in both populations.
- Each commit is compared with its first parent through the full Changeset path, as `provecode
  check` collects it (changed test, harness, config, CI and source files, the unchanged root pytest
  config files as context, and the parent's file list). No contract, so findings are ungraded.
- Reported per population: flagged commits per rule. This is a flag rate, NOT precision. Every
  commit with an H or T finding is listed for the owner to label (ids, paths, rules and rationale
  only; owner_label null). Claude labels nothing.

Run:  python docs/research/scripts/real_history_scan_v2.py  ->  docs/research/data/real_history_scan_v2.json
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from provecode import changeset as cs_mod  # noqa: E402
from provecode import pipeline  # noqa: E402
from provecode.analyzer import is_test_path  # noqa: E402
from provecode.limits import Limits  # noqa: E402
from provecode.model import HARNESS_RULES, TEST_BODY_RULES, WEAKENING_RULES  # noqa: E402
from real_history_scan import AI_TRAILER, LOCKS, clone  # noqa: E402
from real_history_scan import git as _git_once  # noqa: E402


def git(repo, *args, binary=False):
    """v1's git call, retried on transient Windows resource errors (WinError 1450 under memory pressure)."""
    for attempt in range(5):
        try:
            return _git_once(repo, *args, binary=binary)
        except OSError:
            if attempt == 4:
                raise
            time.sleep(2 * (attempt + 1))

OUT = ROOT / "docs" / "research" / "data" / "real_history_scan_v2.json"
MAX_COMMITS = 200
MAX_SOURCE = 200
NEW_RULES = HARNESS_RULES | TEST_BODY_RULES


class Blobs:
    """One long-lived `git cat-file --batch` per repository: no process spawn per blob."""

    def __init__(self, repo: Path):
        self.p = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE)

    def get(self, sha: str, path: str) -> str | None:
        self.p.stdin.write(f"{sha}:{path}\n".encode("utf-8"))
        self.p.stdin.flush()
        header = self.p.stdout.readline().split()
        if len(header) != 3:
            return None  # "<name> missing"
        data = self.p.stdout.read(int(header[2]))
        self.p.stdout.read(1)
        if header[1] != b"blob":
            return None
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait()


def history(repo: Path, tip: str):
    """(sha, first parent or None, [(status, path, old_path)]) for each non-merge commit, newest first,
    from a single `git log`; each commit is diffed against its first parent, as in v1."""
    out = git(repo, "-c", "core.quotePath=false", "log", "--no-merges", "-M", "--name-status",
              "--format=@@%H %P", tip)
    cur = None
    for line in out.splitlines():
        if line.startswith("@@"):
            if cur:
                yield cur
            parts = line[2:].split()
            cur = (parts[0], parts[1] if len(parts) > 1 else None, [])
        elif line.strip() and cur is not None:
            f = line.split("\t")
            if f[0][:1] in "RC" and len(f) >= 3:
                cur[2].append(("R", f[2], f[1]))
            elif len(f) >= 2:
                cur[2].append((f[0][0], f[1], None))
    if cur:
        yield cur


def is_harness_change(path: str) -> bool:
    return cs_mod.classify(path) in (cs_mod.CONFTEST, cs_mod.HARNESS, cs_mod.CONFIG, cs_mod.CI)


def build(repo: Path, blobs: Blobs, parent: str, sha: str, changes) -> cs_mod.Changeset:
    paths = tuple(p for p in git(repo, "ls-tree", "-r", "--name-only", "-z", parent).split(chr(0)) if p)
    present = set(paths)
    files, sources, seen = [], 0, set()
    for st, path, old in changes:
        kind = cs_mod.classify(path)
        if kind not in cs_mod.ANALYZED | {cs_mod.SOURCE} and not (old and is_test_path(old)):
            continue
        if kind == cs_mod.SOURCE:
            if st == "D" or sources >= MAX_SOURCE:
                continue
            sources += 1
        b = None if st == "A" else blobs.get(parent, old or path)
        h = None if st == "D" else blobs.get(sha, path)
        files.append(cs_mod.FileChange(path, st, b, h, old))
        seen.add(path)
    for n in cs_mod.PYTEST_CONFIG_NAMES:
        if n not in seen and n in present:
            text = blobs.get(parent, n)
            if text is not None:
                files.append(cs_mod.FileChange(n, "U", text, text))
    return cs_mod.Changeset(files, parent, sha, paths)


def main() -> int:
    sources = []
    for lock in LOCKS:
        sources += json.loads((ROOT / lock).read_text(encoding="utf-8"))["sources"]
    t0 = time.perf_counter()
    pops = {"T": {"totals": Counter(), "by_rule": Counter()}, "H": {"totals": Counter(), "by_rule": Counter()}}
    candidates: dict[str, dict] = {}
    per_repo = {}
    for src in sources:
        repo = clone(src["repo"])
        blobs = Blobs(repo)
        taken = {"T": 0, "H": 0}
        rc = {"T": Counter(), "H": Counter()}
        for sha, parent, changes in history(repo, src["sha"]):
            if taken["T"] >= MAX_COMMITS and taken["H"] >= MAX_COMMITS:
                break
            if parent is None:
                continue  # root commit
            in_t = taken["T"] < MAX_COMMITS and any(is_test_path(p) or (o and is_test_path(o)) for _, p, o in changes)
            in_h = taken["H"] < MAX_COMMITS and any(is_harness_change(p) for _, p, _ in changes)
            if not (in_t or in_h):
                continue
            cs = build(repo, blobs, parent, sha, changes)
            rep = pipeline.run(cs, None, None, mode="static", limits=Limits.cli())
            # Flagged = a weakening finding of severity warning or blocking, as in guard and the benchmark
            # (T1 "inputs and expected changed together" is info without a contract and is not a flag).
            weak = [f for f in rep["findings"] if f["rule"] in WEAKENING_RULES and f["severity"] != "info"]
            rules = sorted({f["rule"] for f in weak})
            ai = bool(AI_TRAILER.search(git(repo, "show", "-s", "--format=%B", sha)))
            for pop, member in (("T", in_t), ("H", in_h)):
                if not member:
                    continue
                taken[pop] += 1
                c = pops[pop]["totals"]
                c["commits_scanned"] += 1
                c["ai_trailer_commits"] += ai
                c["commits_with_gaps"] += any(g["reason"] not in ("contract_invalid",) for g in rep["gaps"])
                if weak:
                    c["flagged_commits"] += 1
                    c["new_rule_flagged_commits"] += any(r in NEW_RULES for r in rules)
                    for r in rules:
                        pops[pop]["by_rule"][r] += 1
                        rc[pop][r] += 1
            new = [f for f in weak if f["rule"] in NEW_RULES]
            if new:
                candidates[f"{src['repo']}@{sha}"] = {
                    "repo": src["repo"], "commit": sha, "ai_trailer": ai, "populations": [p for p, m in
                                                                                       (("T", in_t), ("H", in_h)) if m],
                    "rules": sorted({f["rule"] for f in new}),
                    "findings": [{"rule": f["rule"], "file": f["file"], "test": f["test"], "severity": f["severity"],
                                  "rationale": f["rationale"]} for f in new],
                    "owner_label": None,
                }
        blobs.close()
        per_repo[src["repo"]] = {"taken": taken, "commits_by_rule": {k: dict(sorted(v.items())) for k, v in rc.items()}}
        print(src["repo"], per_repo[src["repo"]], flush=True)
    result = {
        "schema_version": "1",
        "design": "see the module docstring; findings ungraded (no contract); a flag rate, not precision",
        "max_commits_per_repo_per_population": MAX_COMMITS,
        "populations": {k: {"totals": dict(sorted(v["totals"].items())), "commits_by_rule": dict(sorted(v["by_rule"].items()))}
                        for k, v in pops.items()},
        "per_repo": per_repo,
        "candidates_for_owner_labeling": [candidates[k] for k in sorted(candidates)],
        "seconds": round(time.perf_counter() - t0, 1),
        "command": "python docs/research/scripts/real_history_scan_v2.py",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result["populations"], indent=1))
    print("commits with H/T findings for owner labeling:", len(candidates))
    return 0


if __name__ == "__main__":
    sys.exit(main())
