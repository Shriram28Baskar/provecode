"""Read-only git access. Never checks out, writes to, or modifies the user's repo."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, overload


class GitError(Exception):
    pass


class DecodeError(GitError):
    """The file exists but is not UTF-8 text; recorded as an unparseable gap."""


@overload
def _git(repo: str, *args: str, binary: Literal[False] = ...) -> str: ...


@overload
def _git(repo: str, *args: str, binary: Literal[True]) -> bytes: ...


def _git(repo: str, *args: str, binary: bool = False) -> str | bytes:
    try:
        # --no-optional-locks: even `git diff` must not refresh the index's stat cache.
        out = subprocess.run(["git", "--no-optional-locks", "-C", repo, *args], capture_output=True, timeout=60,
                             check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {' '.join(args)}: {exc}") from exc
    if out.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {out.stderr.decode('utf-8', 'replace').strip()}")
    return out.stdout if binary else out.stdout.decode("utf-8", "replace")


def rev_parse(repo: str, ref: str) -> str:
    if ref.startswith("-"):
        raise GitError(f"invalid ref {ref!r}")
    return _git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").strip()


@dataclass
class Change:
    status: str  # A, M, D, R
    path: str  # head path (base path for D)
    old_path: str | None = None


def changed_files(repo: str, base: str, head: str) -> list[Change]:
    return _name_status(_git(repo, "diff", "--name-status", "-z", "-M", base, head, "--"))


def changed_files_local(repo: str, base: str, staged: bool) -> list[Change]:
    """Changes from base to the index (staged) or to the working tree, including untracked files
    (as additions) in working-tree mode. Paths are relative to the repository top level."""
    if staged:
        return _name_status(_git(repo, "diff", "--cached", "--name-status", "-z", "-M", base, "--"))
    changes = _name_status(_git(repo, "diff", "--name-status", "-z", "-M", base, "--"))
    known = {c.path for c in changes}
    top = toplevel(repo)
    for path in _git(top, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if path and path not in known:
            changes.append(Change("A", path))
    return changes


def _name_status(out: str) -> list[Change]:
    parts = out.split("\0")
    changes: list[Change] = []
    i = 0
    while i < len(parts) and parts[i]:
        st = parts[i]
        if st.startswith(("R", "C")):
            changes.append(Change("R", parts[i + 2], parts[i + 1]))
            i += 3
        else:
            changes.append(Change(st[0], parts[i + 1]))
            i += 2
    return changes


def exists(repo: str, sha: str, path: str) -> bool:
    """True if path exists at sha. `git ls-tree` prints nothing for a missing path and fails
    (raising GitError) only on real errors, so absence is never confused with failure."""
    return bool(_git(repo, "ls-tree", "--name-only", sha, "--", path).strip())


def show(repo: str, sha: str, path: str) -> str | None:
    """File content at sha, or None if the path does not exist there.

    Any other git failure raises GitError (never silently treated as "absent").
    A leading UTF-8 BOM is removed, as Python does when reading source files.
    """
    if not exists(repo, sha, path):
        return None
    data = _git(repo, "cat-file", "blob", f"{sha}:{path}", binary=True)
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DecodeError(f"{path} at {sha[:12]} is not UTF-8") from exc


def ls_tree(repo: str, sha: str) -> tuple[str, ...]:
    """Every file path at sha (used by the shadowing check, H3)."""
    return tuple(p for p in _git(repo, "ls-tree", "-r", "--name-only", "-z", sha).split("\0") if p)


def has_commits(repo: str) -> bool:
    """False for a freshly initialised repository whose HEAD is unborn."""
    return bool(_git(repo, "rev-list", "-n", "1", "--all").strip())


def git_path(repo: str, rel: str) -> str:
    """Where git keeps rel (e.g. hooks/pre-commit), honouring core.hooksPath and worktrees."""
    return _git(repo, "rev-parse", "--git-path", rel).strip()


def toplevel(repo: str) -> str:
    return _git(repo, "rev-parse", "--show-toplevel").strip()


def show_index(repo: str, path: str) -> str | None:
    """Staged content of path (index stage 0), or None if it is not in the index."""
    entries = [e for e in _git(repo, "ls-files", "--stage", "-z", "--", path).split("\0") if e]
    if not entries:
        return None
    if any(not e.split("\t", 1)[0].endswith(" 0") for e in entries):
        raise DecodeError(f"{path} has unresolved merge conflicts in the index")
    data = _git(repo, "cat-file", "blob", f":0:{path}", binary=True)
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DecodeError(f"{path} in the index is not UTF-8") from exc


def show_worktree(top: str, path: str) -> str | None:
    """Working-tree content of path (relative to the top level), or None if absent. Symlinks are
    not followed, so a test file cannot be pointed at content outside the repository."""
    p = Path(top) / path
    if p.is_symlink():
        raise DecodeError(f"{path} is a symlink; symlinks are not followed")
    if not p.is_file():
        return None
    try:
        return p.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DecodeError(f"{path} in the working tree is not UTF-8") from exc


def commit_time(repo: str, sha: str) -> str:
    return _git(repo, "show", "-s", "--format=%cI", sha).strip()
