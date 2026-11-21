"""provecode install-hooks: wire `provecode guard` into Claude Code and git pre-commit (ADR 0010).

  provecode install-hooks --claude [--shared]     .claude/settings.local.json (or settings.json)
  provecode install-hooks --pre-commit            the repository's git pre-commit hook
  provecode install-hooks --claude --uninstall    remove only what this command added

Existing settings are merged, never overwritten: other keys and other hooks are kept, a second
install changes nothing, invalid JSON is refused, and a pre-commit hook this command did not
write is left alone.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import git as git_mod

PRECOMMIT_TAG = "# installed by provecode install-hooks"


class InstallError(Exception):
    pass


_PROBE = "import provecode.cli"


def _require_importable(top: Path) -> None:
    """The hooks run `<python> -m provecode.cli` from the repository root. If that interpreter cannot
    import provecode there, every hook run fails before guard starts, and the hook fails open with no
    message. Refuse to install such a hook."""
    try:
        r = subprocess.run([sys.executable, "-c", _PROBE], cwd=top, capture_output=True, text=True, timeout=60)
        ok = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok:
        raise InstallError(
            f"{sys.executable} cannot import provecode when run from {top}, so the hook would fail open "
            "without checking anything. Install the engine into that interpreter first "
            "(python -m pip install <path to provecode>/engine), then run install-hooks again; not changed")


def _python() -> str:
    """The interpreter running provecode, quoted for a shell command line."""
    exe = Path(sys.executable).as_posix()
    return f'"{exe}"' if " " in exe else exe


def _commands(shared: bool) -> dict[str, str]:
    prefix = "provecode" if shared else f"{_python()} -m provecode.cli"
    return {"SessionStart": f"{prefix} guard --hook claude-session-start",
            "Stop": f"{prefix} guard --hook claude-stop"}


def _ours(entry: object) -> bool:
    """A hook entry this command wrote: either `provecode guard --hook claude-...` or
    `<python> -m provecode.cli guard --hook claude-...`."""
    if not isinstance(entry, dict):
        return False
    cmd = str(entry.get("command", ""))
    return "provecode" in cmd and " guard --hook claude-" in cmd


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise InstallError(f"{path} is not valid JSON ({exc}); not changed") from exc
    if not isinstance(data, dict):
        raise InstallError(f"{path} does not hold a JSON object; not changed")
    return data


def install_claude(top: Path, shared: bool) -> tuple[Path, bool]:
    """Return (settings path, changed)."""
    path = top / ".claude" / ("settings.json" if shared else "settings.local.json")
    data = _load(path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError(f"{path}: 'hooks' is not an object; not changed")
    if shared:
        if shutil.which("provecode") is None:
            print("provecode install-hooks: warning: `provecode` is not on PATH here; the shared hooks need it on "
                  "every machine that uses them", file=sys.stderr)
    else:
        _require_importable(top)
    changed = False
    for event, command in _commands(shared).items():
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise InstallError(f"{path}: hooks.{event} is not a list; not changed")
        present = any(_ours(h) for g in groups if isinstance(g, dict) for h in g.get("hooks", []) or [])
        if not present:
            entry: dict[str, Any] = {"type": "command", "command": command}
            if event == "Stop":
                entry["timeout"] = 60
            groups.append({"hooks": [entry]})
            changed = True
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path, changed


def uninstall_claude(top: Path, shared: bool) -> tuple[Path, bool]:
    path = top / ".claude" / ("settings.json" if shared else "settings.local.json")
    data = _load(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return path, False
    changed = False
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                inner = [h for h in g["hooks"] if not _ours(h)]
                if len(inner) != len(g["hooks"]):
                    changed = True
                if not inner:
                    continue
                g = {**g, "hooks": inner}
            kept.append(g)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        del data["hooks"]
    if changed:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path, changed


def _precommit_path(top: Path) -> Path:
    rel = git_mod.git_path(str(top), "hooks/pre-commit")
    p = Path(rel)
    return p if p.is_absolute() else top / p


def install_precommit(top: Path) -> tuple[Path, bool]:
    path = _precommit_path(top)
    _require_importable(top)
    body = f"#!/bin/sh\n{PRECOMMIT_TAG}\nexec {_python()} -m provecode.cli guard --staged\n"
    if path.exists():
        current = path.read_text(encoding="utf-8", errors="replace")
        if PRECOMMIT_TAG not in current:
            raise InstallError(f"{path} already exists and was not written by provecode; add this line to it "
                               f"yourself instead: {_python()} -m provecode.cli guard --staged")
        if current == body:
            return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path, True


def uninstall_precommit(top: Path) -> tuple[Path, bool]:
    path = _precommit_path(top)
    if path.exists() and PRECOMMIT_TAG in path.read_text(encoding="utf-8", errors="replace"):
        path.unlink()
        return path, True
    return path, False


def cmd_install(args: argparse.Namespace) -> int:
    if not (args.claude or args.pre_commit):
        print("provecode install-hooks: choose --claude and/or --pre-commit", file=sys.stderr)
        return 3
    top = Path(git_mod.toplevel(args.repo))
    try:
        done: list[tuple[Path, bool]] = []
        if args.claude:
            claude = uninstall_claude if args.uninstall else install_claude
            done.append(claude(top, args.shared))
        if args.pre_commit:
            precommit = uninstall_precommit if args.uninstall else install_precommit
            done.append(precommit(top))
    except InstallError as exc:
        print(f"provecode install-hooks: {exc}", file=sys.stderr)
        return 3
    verb = "removed from" if args.uninstall else "installed in"
    for path, changed in done:
        print(f"{verb} {path}" if changed else f"unchanged: {path}")
    return 0


def add_parser(sub: Any) -> None:
    p = sub.add_parser("install-hooks", help="wire provecode guard into Claude Code and/or git pre-commit",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", default=".")
    p.add_argument("--claude", action="store_true", help="Claude Code SessionStart and Stop hooks")
    p.add_argument("--shared", action="store_true",
                   help="write .claude/settings.json with a bare `provecode` command (for teams; needs it on PATH)")
    p.add_argument("--pre-commit", action="store_true", help="git pre-commit hook running `guard --staged`")
    p.add_argument("--uninstall", action="store_true", help="remove only the entries this command added")
    p.set_defaults(fn=cmd_install)
