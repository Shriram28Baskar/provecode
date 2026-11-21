"""provecode guard: check uncommitted or staged test changes before they become a commit (ADR 0010).

  provecode guard [--repo .] [--base HEAD] [--staged] [--contract PATH] [--block-on weakening|failed]
  provecode guard --hook claude-session-start   (Claude Code SessionStart hook; event JSON on stdin)
  provecode guard --hook claude-stop            (Claude Code Stop hook; event JSON on stdin)

Same engine, rules and report as `provecode check`; nothing is written to the repository. The
contract is read from the base commit, so editing it in the working tree cannot relax it.
Exit codes: 0 allow, 1 block, 3 usage or internal error. Hooks always exit 0 and answer in JSON.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from . import contract as contract_mod
from . import git as git_mod
from . import pipeline, report as report_mod
from .analyzer import is_test_path
from .model import BLOCKING, CONTRACT_TOUCHED, WARNING, WEAKENING_RULES, Gap

DEFAULT_CONTRACT = ".provecode/contract.yaml"
STATE_ENV = "PROVECODE_STATE_DIR"
RENAME_SIMILARITY = 0.5  # git's default rename threshold
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


# --------------------------------------------------------------------------- collection


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.splitlines(), b.splitlines(), autojunk=False).ratio()


def _pair_renames(entries: list[tuple]) -> list[tuple]:
    """Pair deleted and added test files whose contents are similar enough into renames.

    git only detects renames between tracked paths, so an uncommitted `mv` to an untracked name
    would otherwise read as a deleted test file (B1). Greedy by similarity, deterministic order.
    """
    deleted = [e for e in entries if len(e) == 3 and e[1] is not None and e[2] is None]
    added = [e for e in entries if len(e) == 3 and e[1] is None and e[2] is not None]
    scores = sorted(((_similarity(d[1], a[2]), d[0], a[0]) for d in deleted for a in added), key=lambda x: (-x[0], x[1], x[2]))
    used_d, used_a, pairs = set(), set(), []
    for score, dp, ap in scores:
        if score < RENAME_SIMILARITY or dp in used_d or ap in used_a:
            continue
        used_d.add(dp)
        used_a.add(ap)
        pairs.append((dp, ap))
    if not pairs:
        return entries
    by_path = {e[0]: e for e in entries}
    out = [e for e in entries if e[0] not in used_d and e[0] not in used_a]
    for dp, ap in pairs:
        out.append((ap, by_path[dp][1], by_path[ap][2], dp))
    return out


def collect(repo: str, base_ref: str, staged: bool, protect: list[str]) -> tuple[list, list[str], dict, list[Gap], str, str]:
    """(files, touched_protected, provenance, gaps, top, base_sha) for pipeline.run."""
    from .cli import _protected  # shared protected-path rule; imported lazily (cli imports guard lazily)

    top = git_mod.toplevel(repo)
    base = git_mod.rev_parse(top, base_ref)
    entries, touched, gaps = [], [], []
    for ch in git_mod.changed_files_local(top, base, staged):
        if _protected(ch.path, protect) or (ch.old_path and _protected(ch.old_path, protect)):
            touched.append(ch.path)
        if not (is_test_path(ch.path) or (ch.old_path and is_test_path(ch.old_path))):
            continue
        try:
            head = git_mod.show_index(top, ch.path) if staged else git_mod.show_worktree(top, ch.path)
            if ch.status == "R":
                entries.append((ch.path, git_mod.show(top, base, ch.old_path), head, ch.old_path))
            else:
                b = None if ch.status == "A" else git_mod.show(top, base, ch.path)
                entries.append((ch.path, b, head))
        except git_mod.DecodeError as exc:
            gaps.append(Gap("unparseable", str(exc), file=ch.path))
    prov = {"repo": Path(top).name, "base_sha": base, "head": "index" if staged else "working tree",
            "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()}
    return _pair_renames(entries), sorted(set(touched)), prov, gaps, top, base


def base_contract(top: str, base: str, path: str | None) -> tuple[dict | None, list[Gap]]:
    """The approved contract as it is at the base commit. A missing default contract is not an
    error (findings are then ungraded); a missing explicit one is a gap."""
    rel = path or DEFAULT_CONTRACT
    text = git_mod.show(top, base, rel)
    if text is None:
        if path is None:
            return None, []
        return None, [Gap("contract_invalid", f"{rel} does not exist at the base commit {base[:12]}")]
    try:
        return contract_mod.parse_text(text, rel), []
    except contract_mod.ContractError as exc:
        return None, [Gap("contract_invalid", str(exc))]


def evaluate(repo: str, base_ref: str, staged: bool, contract: str | None, protect: list[str]) -> dict:
    files, touched, prov, gaps, top, base = collect(repo, base_ref, staged, protect)
    head, cgaps = base_contract(top, base, contract)
    return pipeline.run(files, head, None, mode="static", touched_protected=touched,
                        extra_gaps=gaps + cgaps, provenance=prov)


def weakenings(rep: dict) -> list[dict]:
    return [f for f in rep["findings"] if f["severity"] in (WARNING, BLOCKING)
            and (f["rule"] in WEAKENING_RULES or f["rule"] == CONTRACT_TOUCHED)]


def blocks(rep: dict, block_on: str) -> bool:
    return rep["outcome"] == "FAILED" or (block_on == "weakening" and bool(weakenings(rep)))


def _clip(text: str | None, n: int = 240) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 3] + "..."


def summary(rep: dict, block: bool) -> str:
    prov = rep.get("provenance", {})
    lines = [f"PROVECODE guard: {'BLOCK' if block else 'ok'} ({rep['outcome']}; {prov.get('head', 'head')} "
             f"compared with {str(prov.get('base_sha', ''))[:12]})"]
    for f in weakenings(rep):
        where = f["test"] or f["file"]
        lines.append(f"- {f['rule']} [{f['severity']}] {where}: {f['rationale']}")
        if f.get("before") or f.get("after"):
            lines.append(f"    before: {_clip(f.get('before'))}")
            lines.append(f"    after:  {_clip(f.get('after'))}")
    for g in rep["gaps"]:
        if g["reason"] not in ("contract_invalid", "requires_execution", "no_check"):
            lines.append(f"- gap {g['reason']}: {g['detail']}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- hooks


def state_dir() -> Path:
    return Path(os.environ.get(STATE_ENV) or Path(tempfile.gettempdir()) / "provecode-sessions")


def session_file(session_id: object) -> Path | None:
    """Where a session's starting commit is recorded, or None for an unusable id."""
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        return None
    return state_dir() / f"{session_id}.json"


def _read_event(stdin) -> dict:
    try:
        event = json.load(stdin)
    except json.JSONDecodeError as exc:
        raise ValueError(f"hook input is not JSON: {exc}") from exc
    if not isinstance(event, dict):
        raise ValueError("hook input must be a JSON object")
    return event


def _say(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def hook_session_start(event: dict) -> None:
    """Record where the session began, so a weakening the agent commits is still caught."""
    path = session_file(event.get("session_id"))
    if path is None:
        raise ValueError("SessionStart event has no usable session_id")
    if event.get("source") not in (None, "startup") and path.exists():
        return  # resume / clear / compact keep the original starting point
    cwd = str(event.get("cwd") or os.getcwd())
    top = git_mod.toplevel(cwd)
    sha = git_mod.rev_parse(top, "HEAD")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"base": sha, "repo": top}), encoding="utf-8")


def _session_base(event: dict, top: str) -> str:
    path = session_file(event.get("session_id"))
    if path is None or not path.is_file():
        return "HEAD"
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable session record {path}: {exc}") from exc
    if not isinstance(rec, dict) or rec.get("repo") != top or not _SHA.fullmatch(str(rec.get("base", ""))):
        return "HEAD"  # a record for another repository, or malformed: compare with HEAD
    return rec["base"]


def hook_stop(event: dict, args) -> None:
    cwd = str(event.get("cwd") or os.getcwd())
    top = git_mod.toplevel(cwd)
    rep = evaluate(top, _session_base(event, top), False, args.contract, args.protect)
    if not blocks(rep, args.block_on):
        return
    n = len(weakenings(rep))
    reason = (
        "PROVECODE found uncommitted changes that weaken tests, compared with where this session began. "
        "Restore the original checks before finishing. If the requirement itself changed, stop and ask the user "
        "to approve a contract change; do not edit .provecode/ or the tests' expectations to make them pass.\n\n"
        + summary(rep, True)
    )
    _say({"decision": "block", "reason": reason,
          "systemMessage": f"PROVECODE: {n} test weakening finding(s) in this session's changes; asked Claude to "
                           "restore them."})


def run_hook(kind: str, args) -> int:
    """Hooks fail open: an error allows the stop or start and is shown to the user, because the PR
    check is the enforcement point and a hook that wedges every session gets uninstalled."""
    try:
        event = _read_event(sys.stdin)
        if kind == "claude-session-start":
            hook_session_start(event)
        else:
            hook_stop(event, args)
    except (git_mod.GitError, contract_mod.ContractError, OSError, ValueError) as exc:
        _say({"systemMessage": f"PROVECODE guard could not run ({type(exc).__name__}: {exc}); nothing was blocked."})
    except Exception as exc:  # noqa: BLE001 - fail open, but never silently
        _say({"systemMessage": f"PROVECODE guard internal error ({type(exc).__name__}: {exc}); nothing was blocked."})
    return 0


# --------------------------------------------------------------------------- command


def cmd_guard(args) -> int:
    if args.hook:
        return run_hook(args.hook, args)
    if args.base == "HEAD" and not git_mod.has_commits(git_mod.toplevel(args.repo)):
        print("PROVECODE guard: ok (no commits yet, so there is nothing a change could weaken)")
        return 0
    rep = evaluate(args.repo, args.base, args.staged, args.contract, args.protect)
    block = blocks(rep, args.block_on)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(report_mod.dumps(rep), encoding="utf-8", newline="\n")
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(report_mod.to_markdown(rep), encoding="utf-8", newline="\n")
    (sys.stderr if block else sys.stdout).write(summary(rep, block))
    return 1 if block else 0


def add_parser(sub) -> None:
    p = sub.add_parser("guard", help="check uncommitted or staged test changes (local and agent loops)",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", default=".")
    p.add_argument("--base", default="HEAD", help="commit to compare with (default HEAD)")
    p.add_argument("--staged", action="store_true", help="check the index (what a commit would contain)")
    p.add_argument("--contract", help=f"contract path, read at the base commit (default {DEFAULT_CONTRACT} if present)")
    p.add_argument("--protect", action="append", default=[], help="extra protected path (repeatable)")
    p.add_argument("--block-on", choices=["weakening", "failed"], default="weakening")
    p.add_argument("--hook", choices=["claude-stop", "claude-session-start"])
    p.add_argument("--json")
    p.add_argument("--md")
    p.set_defaults(fn=cmd_guard)
