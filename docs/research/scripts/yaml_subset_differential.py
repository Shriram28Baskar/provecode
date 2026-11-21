"""ADR 0012 section 3: is the stdlib YAML subset parser good enough for real GitHub workflow files?

Every distinct `.github/workflows/*.yml|yaml` blob in the full history of the 18 benchmark source
repositories and the 9 Tier H repositories is parsed twice: by provecode.yamlsubset.loads and by
PyYAML's BaseLoader (which, like the subset parser, keeps every scalar a string). Outcomes:

  identical   both parse and the trees are equal
  refused     the subset parser raises (an `unparseable` gap in the engine; never a guess)
  different   both parse but the trees differ (a silent misparse: the drop criterion's red line)
  invalid     PyYAML itself rejects the blob (not a valid workflow; excluded from the rate); whether
              the subset parser also rejects it is counted separately

Drop criterion (ADR 0012): identical < 95% of the blobs PyYAML accepts, or any `different`.

  python docs/research/scripts/yaml_subset_differential.py
    -> docs/research/data/yaml_subset_differential.json (blob ids and counts only; no third-party text)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "src"))
from provecode import yamlsubset  # noqa: E402

CACHE = ROOT / "docs" / "research" / ".cache" / "history"
OUT = ROOT / "docs" / "research" / "data" / "yaml_subset_differential.json"
LOCKS = [ROOT / "benchmark" / n for n in ("sources.lock.json", "sources_unittest.lock.json",
                                          "sources_blindspots.lock.json", "sources_harness.lock.json")]


def clone(repo: str) -> Path:
    d = CACHE / (repo.replace("/", "__") + ".git")
    if not d.exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--bare", f"https://github.com/{repo}.git", str(d)], check=True)
    return d


def workflow_blobs(d: Path) -> dict[str, str]:
    """blob sha -> one path it appeared under, for every workflow file in all of history."""
    out = subprocess.run(["git", "-C", str(d), "rev-list", "--all", "--objects", "--", ".github/workflows"],
                         capture_output=True, text=True, check=True).stdout
    blobs: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, path = line.partition(" ")
        if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")) and path.count("/") == 2:
            blobs.setdefault(sha, path)
    return blobs


def main() -> int:
    repos = []
    for lock in LOCKS:
        repos += [s["repo"] for s in json.loads(lock.read_text(encoding="utf-8"))["sources"]]
    rows, totals = [], {"identical": 0, "refused": 0, "different": 0, "invalid": 0, "invalid_but_subset_accepts": 0}
    examples: dict[str, list] = {"refused": [], "different": [], "invalid_accepted": []}
    reasons: dict[str, int] = {}
    for repo in sorted(set(repos)):
        d = clone(repo)
        counts = {"identical": 0, "refused": 0, "different": 0, "invalid": 0, "invalid_but_subset_accepts": 0}
        for sha, path in sorted(workflow_blobs(d).items()):
            raw = subprocess.run(["git", "-C", str(d), "cat-file", "blob", sha], capture_output=True, check=True).stdout
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                counts["invalid"] += 1
                continue
            try:
                want = yaml.load(text, Loader=yaml.BaseLoader)
            except yaml.YAMLError:
                counts["invalid"] += 1
                try:
                    yamlsubset.loads(text)
                    counts["invalid_but_subset_accepts"] += 1
                    examples["invalid_accepted"].append({"repo": repo, "blob": sha, "path": path})
                except yamlsubset.YamlError:
                    pass
                continue
            try:
                got = yamlsubset.loads(text)
            except yamlsubset.YamlError as exc:
                counts["refused"] += 1
                why = str(exc).split(":")[-1].strip()[:60]
                reasons[why] = reasons.get(why, 0) + 1
                examples["refused"].append({"repo": repo, "blob": sha, "path": path, "reason": str(exc)[:120]})
                continue
            if got == want:
                counts["identical"] += 1
            else:
                counts["different"] += 1
                examples["different"].append({"repo": repo, "blob": sha, "path": path})
        rows.append({"repo": repo, **counts})
        for k in totals:
            totals[k] += counts[k]
    accepted = totals["identical"] + totals["refused"] + totals["different"]
    rate = round(totals["identical"] / accepted, 4) if accepted else None
    result = {
        "schema_version": "1",
        "command": "python docs/research/scripts/yaml_subset_differential.py",
        "pyyaml": yaml.__version__,
        "repos": rows,
        "totals": totals,
        "blobs_pyyaml_accepts": accepted,
        "identical_rate": rate,
        "refusal_reasons": dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
        "examples": examples,
        "drop_criterion": "identical_rate < 0.95 or totals.different > 0",
        "drop": rate is None or rate < 0.95 or totals["different"] > 0,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: result[k] for k in ("totals", "blobs_pyyaml_accepts", "identical_rate", "drop")}))
    print("refusals:", json.dumps(result["refusal_reasons"])[:800])
    for ex in examples["different"][:10]:
        print("DIFFERENT", ex)
    return 0


if __name__ == "__main__":
    sys.exit(main())
