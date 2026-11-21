"""Phase 2 Definition of done (ADR 0013): `provecode rerun` on pinned real open-source repositories.

For each repository, base = the first parent of the pinned benchmark SHA and head = the pinned SHA
(a real, recent change). Uses the full-history bare clones in docs/research/.cache/history. Reports
A/B/C outcomes, the outcome, wall-clock time per run, pytest's own time, and the setup overhead.
Every repository attempted is reported, including failures; nothing is dropped.

  python docs/research/scripts/rerun_real_repos.py [--image provecode-run:local]
    -> docs/research/data/rerun_real_repos.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "src"))
from provecode import rerun  # noqa: E402

CACHE = ROOT / "docs" / "research" / ".cache" / "history"
OUT = ROOT / "docs" / "research" / "data" / "rerun_real_repos.json"
REPOS = ["tkem/cachetools", "kjd/idna", "more-itertools/more-itertools", "hukkin/tomli", "python-humanize/humanize",
         "jazzband/prettytable", "pallets/itsdangerous"]
LOCKS = ["sources.lock.json", "sources_unittest.lock.json", "sources_blindspots.lock.json"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="provecode-run:local")
    ap.add_argument("--repos", nargs="*", default=REPOS, help="rerun only these; earlier results are kept")
    ap.add_argument("--deps-timeout", type=int, default=900)
    a = ap.parse_args()
    previous = {r["repo"]: r for r in json.loads(OUT.read_text(encoding="utf-8"))["repos"]} if OUT.exists() else {}
    pinned = {}
    for lock in LOCKS:
        for s in json.loads((ROOT / "benchmark" / lock).read_text(encoding="utf-8"))["sources"]:
            pinned[s["repo"]] = s["sha"]
    rows = []
    for repo in a.repos:
        d = CACHE / (repo.replace("/", "__") + ".git")
        head = pinned[repo]
        base = subprocess.run(["git", "--git-dir", str(d), "rev-parse", f"{head}^1"], capture_output=True,
                              text=True, check=True).stdout.strip()
        args = argparse.Namespace(repo=str(d), base=base, head=head, image=a.image, timeout=900, deps_timeout=a.deps_timeout,
                                  wheelhouse="host", protect=[])
        t0 = time.monotonic()
        try:
            rep = rerun.run(args)
            err = None
        except Exception as exc:  # noqa: BLE001 - reported per repository, never hidden
            rep, err = None, f"{type(exc).__name__}: {exc}"
        row = {"repo": repo, "base": base, "head": head, "seconds": round(time.monotonic() - t0, 1), "error": err}
        if rep:
            rr = rep["provenance"]["rerun"]
            t = rr["timings"]
            runs = [k[:-2] for k in t if k.endswith("_s") and not k.endswith("pytest_s") and k[:-2] in
                    ("A1", "A2", "B1", "B2", "C")]
            pytest_s = [t.get(f"{k}_pytest_s") for k in runs if t.get(f"{k}_pytest_s") is not None]
            row.update({
                "outcome": rep["outcome"],
                "evidence": {e["id"]: {"result": e["result"], "summary": e["output_excerpt"]} for e in rep["evidence"]},
                "findings": [{"rule": f["rule"], "severity": f["severity"], "where": f["test"] or f["file"]}
                             for f in rep["findings"]],
                "gaps": [{"reason": g["reason"], "detail": g["detail"][:300]} for g in rep["gaps"]],
                "summary": rr["summary"], "timings": t,
                "overlay": {"replaced": len((rr["overlay"] or {}).get("replaced", [])),
                            "deleted": (rr["overlay"] or {}).get("deleted", [])},
                "sealed_run_seconds_mean": round(sum(t[f"{k}_s"] for k in runs) / len(runs), 2) if runs else None,
                "pytest_seconds_mean": round(sum(pytest_s) / len(pytest_s), 2) if pytest_s else None,
            })
        if repo in previous:  # an earlier attempt stays visible, with what it reported
            prior = previous[repo]
            row["previous_attempts"] = prior.pop("previous_attempts", []) + [
                {k: prior.get(k) for k in ("outcome", "error", "gaps", "seconds")}]
        rows.append(row)
        print(json.dumps({k: row.get(k) for k in ("repo", "outcome", "error", "seconds", "sealed_run_seconds_mean",
                                                  "pytest_seconds_mean")}), flush=True)
    merged = {**previous, **{r["repo"]: r for r in rows}}
    rows = [merged[r] for r in REPOS if r in merged]
    result = {"schema_version": "1", "image": a.image, "command": "python docs/research/scripts/rerun_real_repos.py",
              "note": "base = first parent of the pinned benchmark SHA; head = the pinned SHA. Docker Desktop on "
                      "Windows 11; timings are machine-specific.", "repos": rows}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
