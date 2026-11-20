"""Pilot: do real AI-agent patches touch tests, and what does PROVECODE see in them?

Research measurement for docs/research/landscape-2026-11.md. Not product code; edits nothing.
It does NOT label anything as weakening: it reports counts and rule hits, and writes a candidate
list for the owner to label (ground rule 4).

Data (all public, fetched anonymously):
- SWE-bench Verified instances (repo, instance_id, base_commit) from the Hugging Face datasets-server.
- Each agent's submitted patch per instance: s3://swe-bench-submissions/bash-only/<run>/logs/<id>/patch.diff
  (asset paths from github.com/SWE-bench/experiments metadata.yaml).
- Base versions of modified test files from raw.githubusercontent.com at the instance's base_commit.

Agents (same scaffold, mini-SWE-agent bash-only, so the comparison is controlled):
  20260217_mini-v2.0.0_claude-4-6-opus, 20251211_mini-v1.17.2_gpt-5.2-2025-12-11-high,
  20260226_mini-v2.0.0_gemini-3-pro-high

Third-party code stays in docs/research/.cache (git-ignored); the committed output has ids,
paths and counts only.
Run:  python docs/research/scripts/swebench_agent_tests.py  ->  docs/research/data/swebench_agent_tests.json
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "src"))
sys.path.insert(0, str(ROOT))
from provecode.analyzer import analyze_file, is_test_path  # noqa: E402
from provecode.limits import Limits  # noqa: E402
from provecode.model import WEAKENING_RULES  # noqa: E402
from benchmark import baseline_count  # noqa: E402

CACHE = ROOT / "docs" / "research" / ".cache" / "swebench"
OUT = ROOT / "docs" / "research" / "data" / "swebench_agent_tests.json"
AGENTS = [
    "20260217_mini-v2.0.0_claude-4-6-opus",
    "20251211_mini-v1.17.2_gpt-5.2-2025-12-11-high",
    "20260226_mini-v2.0.0_gemini-3-pro-high",
]
DS = "https://datasets-server.huggingface.co/rows?dataset=princeton-nlp%2FSWE-bench_Verified&config=default&split=test"
S3 = "https://swe-bench-submissions.s3.amazonaws.com/bash-only/{run}/logs/{iid}/patch.diff"
RAW = "https://raw.githubusercontent.com/{repo}/{sha}/{path}"


def fetch(url: str, dest: Path) -> bytes | None:
    """Cached GET. Returns None only on HTTP 404 (absent); other failures raise."""
    if dest.exists():
        data = dest.read_bytes()
        return None if data == b"\x00404" else data
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "research-script"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            dest.write_bytes(data)
            return data
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                dest.write_bytes(b"\x00404")
                return None
            if attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
    return None


def instances() -> list[dict]:
    rows = []
    for off in range(0, 500, 100):
        data = fetch(f"{DS}&offset={off}&length=100", CACHE / "ds" / f"{off}.json")
        rows += [r["row"] for r in json.loads(data)["rows"]]
    return [{"repo": r["repo"], "instance_id": r["instance_id"], "base_commit": r["base_commit"]} for r in rows]


def split_diff(diff: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for chunk in re.split(r"(?m)^(?=diff --git )", diff):
        m = re.match(r"diff --git a/(\S+) b/(\S+)", chunk)
        if m:
            parts[m.group(2)] = chunk
    return parts


def testish(path: str) -> bool:
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    return p.endswith(".py") and (is_test_path(p) or name == "tests.py" or "/tests/" in f"/{p}" or "/testing/" in p)


def apply_file_diff(base: str | None, chunk: str, path: str) -> str | None:
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q", d], check=True)
        target = Path(d) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if base is not None:
            target.write_bytes(base.encode("utf-8"))
        (Path(d) / "x.patch").write_text(chunk, encoding="utf-8")
        r = subprocess.run(["git", "-C", d, "apply", "--whitespace=nowarn", "--ignore-whitespace", "x.patch"], capture_output=True)
        if r.returncode != 0:
            return "__APPLY_FAILED__"
        return target.read_text(encoding="utf-8") if target.exists() else None


def gold_visibility() -> dict:
    """Share of the official (gold) test-patch .py files that PROVECODE's filename filter sees."""
    rows = []
    for off in range(0, 500, 100):
        data = fetch(f"{DS}&offset={off}&length=100", CACHE / "ds" / f"{off}.json")
        rows += [r["row"] for r in json.loads(data)["rows"]]
    files = [(r["repo"], m.group(1)) for r in rows
             for m in re.finditer(r"(?m)^diff --git a/\S+ b/(\S+\.py)", r["test_patch"])]
    seen = Counter(repo for repo, f in files if is_test_path(f))
    total = Counter(repo for repo, _ in files)
    return {"files": len(files), "visible": sum(seen.values()),
            "by_repo": {repo: {"visible": seen[repo], "files": n} for repo, n in total.most_common()},
            "instances_by_repo": dict(Counter(r["repo"] for r in rows).most_common())}


def main() -> int:
    inst = instances()
    by_id = {i["instance_id"]: i for i in inst}
    jobs = [(a, i["instance_id"]) for a in AGENTS for i in inst]
    with ThreadPoolExecutor(16) as ex:
        patches = list(ex.map(lambda j: fetch(S3.format(run=j[0], iid=j[1]), CACHE / "patches" / j[0] / f"{j[1]}.diff"), jobs))
    result = {"schema_version": "1", "agents": {}, "instances": len(inst),
              "command": "python docs/research/scripts/swebench_agent_tests.py"}
    candidates = []
    for agent in AGENTS:
        c = Counter()
        rule_hits = Counter()
        baseline_hits = 0
        for (a, iid), p in zip(jobs, patches):
            if a != agent:
                continue
            if p is None:
                c["patch_missing"] += 1
                continue
            text = p.decode("utf-8", "replace")
            if not text.strip():
                c["patch_empty"] += 1
                continue
            c["patches"] += 1
            files = split_diff(text)
            tfiles = {f: ch for f, ch in files.items() if testish(f)}
            if not tfiles:
                continue
            c["patches_touching_test_files"] += 1
            for f, chunk in tfiles.items():
                new = "new file mode" in chunk.split("@@", 1)[0]
                deleted = "deleted file mode" in chunk.split("@@", 1)[0]
                c["test_files_new" if new else "test_files_deleted" if deleted else "test_files_modified"] += 1
                if new:
                    continue
                c["visible_to_provecode_filename_filter" if is_test_path(f) else "missed_by_filename_filter"] += 1
                i = by_id[iid]
                base = fetch(RAW.format(repo=i["repo"], sha=i["base_commit"], path=f),
                             CACHE / "raw" / i["repo"].replace("/", "__") / i["base_commit"] / f)
                if base is None:
                    c["base_fetch_missing"] += 1
                    continue
                base_s = base.decode("utf-8", "replace")
                head = None if deleted else apply_file_diff(base_s, chunk, f)
                if head == "__APPLY_FAILED__":
                    c["apply_failed"] += 1
                    continue
                res = analyze_file(f, base_s, head, Limits.cli())
                if not res.tests_base:
                    c["no_tests_recognised_in_base"] += 1  # e.g. unittest classes not named Test*
                if res.gaps:
                    c["analysis_gaps"] += 1
                weak = sorted({x.rule for x in res.findings if x.rule in WEAKENING_RULES})
                for r in weak:
                    rule_hits[r] += 1
                bl = bool(baseline_count.detect(base_s, head))
                baseline_hits += bl
                # Every modified test file is a labeling candidate (detector-independent selection,
                # PRD section 6.2), not only the flagged ones.
                candidates.append({"agent": agent, "instance_id": iid, "file": f, "provecode_rules": weak,
                                   "baseline_flagged": bl, "visible_to_filename_filter": is_test_path(f),
                                   "tests_recognised_in_base": len(res.tests_base), "owner_label": None})
        result["agents"][agent] = {"counts": dict(sorted(c.items())), "provecode_rule_hits_by_file": dict(rule_hits),
                                   "baseline_flagged_files": baseline_hits}
    result["candidates_for_owner_labeling"] = candidates
    result["gold_test_file_visibility"] = gold_visibility()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    for a, v in result["agents"].items():
        print(a, v)
    print("candidates:", len(candidates))
    return 0


if __name__ == "__main__":
    sys.exit(main())
