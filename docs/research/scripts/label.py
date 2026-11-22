"""Owner labeling of real-history flags (brief Phase 6): the commits PROVECODE flagged, one at a time.

Research tooling, not product code. For each flagged commit it shows the findings and the diff of the
flagged files, and records the OWNER's label. It never writes a label on its own: a label is written
only after a keypress at an interactive terminal, and it refuses to run with a non-interactive input.

  python docs/research/scripts/label.py                     label every unlabeled commit
  python docs/research/scripts/label.py --sample 10         about 10 per rule (a fixed, reproducible sample)
  python docs/research/scripts/label.py --rule H5           only commits flagged by H5
  python docs/research/scripts/label.py --summary           per-rule precision with Wilson 95% intervals

Input:  docs/research/data/real_history_scan_v2.json (`candidates_for_owner_labeling`)
Labels: docs/research/labels/real_history_scan_v2.jsonl, append-only, one JSON object per line; the
        latest line for a commit wins, so relabeling never rewrites history.
Labels: weakening (the change made verification weaker), legit (it did not), unsure.
Precision per rule = weakening / (weakening + legit) over the labeled commits that rule flagged;
`unsure` is excluded and counted separately. It is a commit-level figure (one label per commit).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "docs" / "research" / "data" / "real_history_scan_v2.json"
LABELS = ROOT / "docs" / "research" / "labels" / "real_history_scan_v2.jsonl"
CACHE = ROOT / "docs" / "research" / ".cache" / "history"
CHOICES = {"w": "weakening", "l": "legit", "u": "unsure"}
DIFF_LINES = 200


def key(c: dict[str, Any]) -> str:
    return f"{c['repo']}@{c['commit']}"


def load_candidates(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    cands = doc.get("candidates_for_owner_labeling")
    if not isinstance(cands, list):
        raise SystemExit(f"{path}: no candidates_for_owner_labeling list")
    for c in cands:
        if c.get("owner_label") is not None:
            raise SystemExit(f"{path}: {key(c)} has an owner_label inside the data file; labels belong in the .jsonl")
    return cands


def load_labels(path: Path) -> dict[str, dict[str, Any]]:
    """Latest label per commit key. A malformed line is an error, never skipped silently."""
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{n}: not JSON ({exc})") from None
        if not isinstance(e, dict) or e.get("label") not in CHOICES.values() or not isinstance(e.get("key"), str):
            raise SystemExit(f"{path}:{n}: expected an object with a 'key' and a label in {sorted(CHOICES.values())}")
        out[e["key"]] = e
    return out


def record(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")


def _order(c: dict[str, Any]) -> str:
    return hashlib.sha256(key(c).encode("utf-8")).hexdigest()


def select(cands: list[dict[str, Any]], labels: dict[str, dict[str, Any]], rule: str | None = None,
           sample: int | None = None, relabel: bool = False) -> list[dict[str, Any]]:
    """The commits to show, in a fixed order. With --sample N: per rule, the first N commits in a
    hash order of their keys, so the sample is reproducible and does not depend on labels so far."""
    pool = [c for c in cands if rule is None or rule in c["rules"]]
    if sample is not None:
        chosen: dict[str, dict[str, Any]] = {}
        for r in sorted({r for c in pool for r in c["rules"]} if rule is None else {rule}):
            for c in sorted((c for c in pool if r in c["rules"]), key=_order)[:sample]:
                chosen[key(c)] = c
        pool = list(chosen.values())
    pool.sort(key=_order)
    return [c for c in pool if relabel or key(c) not in labels]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def summary(cands: list[dict[str, Any]], labels: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for r in sorted({r for c in cands for r in c["rules"]}):
        flagged = [c for c in cands if r in c["rules"]]
        got = [labels[key(c)]["label"] for c in flagged if key(c) in labels]
        w, legit, unsure = got.count("weakening"), got.count("legit"), got.count("unsure")
        n = w + legit
        lo, hi = wilson(w, n)
        rows.append({"rule": r, "flagged": len(flagged), "labeled": len(got), "weakening": w, "legit": legit,
                     "unsure": unsure, "precision": (w / n) if n else None, "ci95": [lo, hi] if n else None})
    return rows


def print_summary(rows: list[dict[str, Any]], out: TextIO) -> None:
    out.write("rule  flagged  labeled  weakening  legit  unsure  precision  95% CI\n")
    for x in rows:
        prec = f"{x['precision']:.0%}" if x["precision"] is not None else "-"
        ci = f"{x['ci95'][0]:.0%}-{x['ci95'][1]:.0%}" if x["ci95"] else "-"
        out.write(f"{x['rule']:<5} {x['flagged']:>7}  {x['labeled']:>7}  {x['weakening']:>9}  {x['legit']:>5}  "
                  f"{x['unsure']:>6}  {prec:>9}  {ci}\n")
    out.write("Precision counts only commits labeled weakening or legit; it is not measured until labels exist.\n")


def repo_dir(repo: str) -> Path:
    return CACHE / (repo.replace("/", "__") + ".git")


def show(c: dict[str, Any], out: TextIO, full: bool = False,
         git: Callable[..., str] | None = None) -> None:
    """The commit header, its findings, and the diff of the flagged files."""
    run = git or _git
    files = sorted({f["file"] for f in c["findings"]})
    out.write("=" * 100 + "\n")
    out.write(f"{c['repo']}  {c['commit']}  populations={','.join(c['populations'])}"
              f"{'  AI co-author trailer' if c.get('ai_trailer') else ''}\n")
    d = repo_dir(c["repo"])
    if d.exists():
        out.write(run(d, "show", "-s", "--format=%an  %ad%n%n    %s", "--date=short", c["commit"]) + "\n")
    for f in c["findings"]:
        where = f["test"] or f["file"]
        out.write(f"  {f['rule']} [{f['severity']}] {where}\n      {f['rationale']}\n")
    if not d.exists():
        out.write(f"  (no local clone at {d}; run real_history_scan_v2.py once to create the cache, "
                  f"or open https://github.com/{c['repo']}/commit/{c['commit']})\n")
        return
    diff = run(d, "show", "--format=", "-M", c["commit"], "--", *files).splitlines()
    shown = diff if full else diff[:DIFF_LINES]
    out.write("\n".join(shown) + "\n")
    if len(shown) < len(diff):
        out.write(f"  ... {len(diff) - len(shown)} more diff lines (f = show the full diff)\n")


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *args], capture_output=True)
    if p.returncode != 0:
        return f"(git {' '.join(args[:2])} failed: {p.stderr.decode('utf-8', 'replace').strip()})"
    return p.stdout.decode("utf-8", "replace")


def _labeler() -> str:
    p = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True)
    return p.stdout.strip() or "owner"


def label_loop(todo: list[dict[str, Any]], labels_path: Path, ask: Callable[[str], str], out: TextIO,
               by: str, now: Callable[[], str], git: Callable[..., str] | None = None) -> int:
    """Shows each commit and records the owner's answer. Returns the number of labels written."""
    written = 0
    for i, c in enumerate(todo, 1):
        full = False
        while True:
            show(c, out, full=full, git=git)
            answer = ask(f"[{i}/{len(todo)}] w=weakening  l=legit  u=unsure  f=full diff  s=skip  q=quit > ")
            a = answer.strip().lower()
            if a == "f":
                full = True
                continue
            break
        if a == "q":
            break
        if a == "s" or a not in CHOICES:
            if a not in ("s", ""):
                out.write(f"  '{answer.strip()}' is not a choice; skipped, nothing recorded\n")
            continue
        reason = ask("  reason (optional) > ").strip()
        record(labels_path, {"key": key(c), "repo": c["repo"], "commit": c["commit"], "rules": c["rules"],
                             "label": CHOICES[a], "reason": reason, "labeled_by": by, "labeled_at": now()})
        written += 1
        out.write(f"  recorded: {CHOICES[a]}\n")
    return written


def _now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None, stdin: TextIO | None = None, out: TextIO | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=DATA)
    ap.add_argument("--labels", type=Path, default=LABELS)
    ap.add_argument("--rule", help="only commits flagged by this rule, e.g. H5")
    ap.add_argument("--sample", type=int, help="about N commits per rule, in a fixed reproducible order")
    ap.add_argument("--relabel", action="store_true", help="also show commits that already have a label")
    ap.add_argument("--summary", action="store_true", help="print per-rule precision and exit")
    ap.add_argument("--by", help="labeler name recorded with each label (default: git config user.name)")
    a = ap.parse_args(argv)
    stdin, out = stdin or sys.stdin, out or sys.stdout
    cands, labels = load_candidates(a.data), load_labels(a.labels)
    if a.summary:
        print_summary(summary(cands, labels), out)
        return 0
    if a.sample is not None and a.sample < 1:
        ap.error("--sample must be at least 1")
    if not stdin.isatty():
        out.write("label.py records the owner's judgment and needs an interactive terminal; "
                  "it never labels from piped or scripted input.\n")
        return 2
    todo = select(cands, labels, a.rule, a.sample, a.relabel)
    out.write(f"{len(todo)} commit(s) to label ({len(labels)} labeled so far). Labels go to {a.labels}.\n")

    def ask(prompt: str) -> str:
        out.write(prompt)
        out.flush()
        line = stdin.readline()
        return line if line else "q"

    n = label_loop(todo, a.labels, ask, out, a.by or _labeler(), _now)
    out.write(f"{n} label(s) recorded.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
