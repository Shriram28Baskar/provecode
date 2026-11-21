"""How often does PROVECODE fire on real, mostly human, test edits? (a Tier C precursor)

Research measurement. Not product code; edits nothing in the repository. Design fixed before the
first run:
- Repositories: the 18 benchmark source repositories (Tier A, A-u, A-b), cloned bare with full
  history into docs/research/.cache/history (git-ignored; third-party code is never committed).
- Commits: per repository, the most recent MAX_COMMITS non-merge commits, reachable from the
  pinned benchmark SHA, that change at least one test file (provecode.analyzer.is_test_path).
- Each commit is compared with its first parent, exactly as `provecode check` would compare a PR,
  with no contract, so findings are ungraded.
- Reported: the share of test-editing commits with at least one weakening finding, split into the
  semantic rules (S1-S5) and the standard rules (B1-B4), next to the count-based baseline.
- NOT precision: humans do sometimes weaken tests, and deleting obsolete tests is often
  legitimate. Every flagged commit is listed for the owner to label (ids, paths, rules only).
- Covariate: whether the commit message carries an AI co-author trailer.

Run:  python docs/research/scripts/real_history_scan.py  ->  docs/research/data/real_history_scan.json
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "src"))
sys.path.insert(0, str(ROOT))
from provecode import pipeline  # noqa: E402
from provecode.analyzer import is_test_path  # noqa: E402
from provecode.limits import Limits  # noqa: E402
from provecode.model import WEAKENING_RULES  # noqa: E402
from benchmark import baseline_count  # noqa: E402

CACHE = ROOT / "docs" / "research" / ".cache" / "history"
OUT = ROOT / "docs" / "research" / "data" / "real_history_scan.json"
LOCKS = ["benchmark/sources.lock.json", "benchmark/sources_unittest.lock.json", "benchmark/sources_blindspots.lock.json"]
MAX_COMMITS = 200
SEMANTIC = {"S1", "S2", "S3", "S4", "S5"}
AI_TRAILER = re.compile(r"(?im)^co-authored-by:.*\b(claude|copilot|cursor|devin|codex|gemini|aider|openhands|chatgpt)\b")


def git(repo: Path, *args: str, binary: bool = False):
    out = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *args], capture_output=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in {repo}: {out.stderr.decode('utf-8', 'replace').strip()}")
    return out.stdout if binary else out.stdout.decode("utf-8", "replace")


def clone(repo: str) -> Path:
    d = CACHE / (repo.replace("/", "__") + ".git")
    if not d.exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--bare", f"https://github.com/{repo}.git", str(d)], check=True)
    return d


def blob(repo: Path, sha: str, path: str) -> str | None:
    if not git(repo, "ls-tree", "--name-only", sha, "--", path).strip():
        return None
    try:
        return git(repo, "cat-file", "blob", f"{sha}:{path}", binary=True).decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def changes(repo: Path, parent: str, sha: str) -> list[tuple]:
    out = git(repo, "diff", "--name-status", "-z", "-M", parent, sha, "--").split(chr(0))
    files, i = [], 0
    while i < len(out) and out[i]:
        st = out[i]
        if st[0] in "RC":
            old, new = out[i + 1], out[i + 2]
            i += 3
            if is_test_path(old) or is_test_path(new):
                files.append((new, blob(repo, parent, old), blob(repo, sha, new), old))
        else:
            path = out[i + 1]
            i += 2
            if is_test_path(path):
                files.append((path, None if st[0] == "A" else blob(repo, parent, path),
                              None if st[0] == "D" else blob(repo, sha, path)))
    return files


def main() -> int:
    sources = []
    for lock in LOCKS:
        sources += json.loads((ROOT / lock).read_text(encoding="utf-8"))["sources"]
    t0 = time.perf_counter()
    per_repo, candidates = {}, []
    for src in sources:
        repo = clone(src["repo"])
        log = git(repo, "log", "--no-merges", "--format=%H %P", src["sha"]).splitlines()
        c = Counter()
        rule_commits = Counter()
        for line in log:
            if c["commits_scanned"] >= MAX_COMMITS:
                break
            parts = line.split()
            if len(parts) < 2:
                continue  # root commit
            sha, parent = parts[0], parts[1]
            files = changes(repo, parent, sha)
            if not files:
                continue
            c["commits_scanned"] += 1
            msg = git(repo, "show", "-s", "--format=%B", sha)
            ai = bool(AI_TRAILER.search(msg))
            c["ai_trailer_commits"] += ai
            rep = pipeline.run(files, None, None, mode="static", limits=Limits.cli())
            weak = [f for f in rep["findings"] if f["rule"] in WEAKENING_RULES]
            rules = sorted({f["rule"] for f in weak})
            bl = any(baseline_count.detect(f[1], f[2]) for f in files if len(f) == 3)
            c["baseline_flagged_commits"] += bl
            if rep["gaps"]:
                c["commits_with_gaps"] += any(g["reason"] not in ("contract_invalid",) for g in rep["gaps"])
            if weak:
                c["flagged_commits"] += 1
                c["semantic_flagged_commits"] += any(r in SEMANTIC for r in rules)
                c["standard_only_flagged_commits"] += all(r not in SEMANTIC for r in rules)
                for r in rules:
                    rule_commits[r] += 1
                candidates.append({"repo": src["repo"], "commit": sha, "ai_trailer": ai, "rules": rules,
                                   "findings": [{"rule": f["rule"], "file": f["file"], "test": f["test"]} for f in weak],
                                   "baseline_flagged": bl, "owner_label": None})
        per_repo[src["repo"]] = {"counts": dict(sorted(c.items())), "commits_by_rule": dict(sorted(rule_commits.items()))}
        print(src["repo"], per_repo[src["repo"]], flush=True)
    tot = Counter()
    by_rule = Counter()
    for v in per_repo.values():
        tot.update(v["counts"])
        by_rule.update(v["commits_by_rule"])
    result = {
        "schema_version": "1",
        "design": "see the module docstring; findings are ungraded (no contract); this is a flag rate, not precision",
        "max_commits_per_repo": MAX_COMMITS,
        "totals": dict(sorted(tot.items())),
        "commits_by_rule": dict(sorted(by_rule.items())),
        "per_repo": per_repo,
        "candidates_for_owner_labeling": candidates,
        "seconds": round(time.perf_counter() - t0, 1),
        "command": "python docs/research/scripts/real_history_scan.py",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print("totals", result["totals"], "by rule", result["commits_by_rule"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
