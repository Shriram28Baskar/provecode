"""Read-only git access. Never checks out, writes to, or modifies the user's repo."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


class GitError(Exception):
    pass


class DecodeError(GitError):
    """The file exists but is not UTF-8 text; recorded as an unparseable gap."""


def _git(repo: str, *args: str, binary: bool = False):
    try:
        out = subprocess.run(["git", "-C", repo, *args], capture_output=True, timeout=60, check=False)
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
    out = _git(repo, "diff", "--name-status", "-z", "-M", base, head, "--")
    parts = out.split("\0")
    changes: list[Change] = []
    i = 0
    while i < len(parts) and parts[i]:
        st = parts[i]
        if st.startswith("R") or st.startswith("C"):
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


def commit_time(repo: str, sha: str) -> str:
    return _git(repo, "show", "-s", "--format=%cI", sha).strip()
