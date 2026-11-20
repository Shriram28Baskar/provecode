"""Render Gate 0 labeling packets (spike/PREREGISTRATION.md section 8.1 step 2).

Non-detector spike code. It imports nothing from the detector and uses the
Python standard library only. It is read-only toward the clones: the only git
subcommands it runs are rev-parse, cat-file -e, show and diff-tree.

Usage:
  python -I -B spike/labeling/render_packet.py --clone-root <folder>
      (--sha <40-hex> | --all) [--stratum random|keyword] [--out-dir <folder>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Repo root: two levels above this file (spike/labeling/render_packet.py).
REPO_ROOT = Path(__file__).resolve().parents[2]

# Pinned inputs (PREREGISTRATION section 13.4). These are the only files read.
RANDOM_LIST_NAME = "sample_random_shas.txt"
RANDOM_LIST_SHA256 = "dec6fbf2439e7ed4c77e5765a462612255a1c5ad691de4319adc86532fa05dfc"
KEYWORD_LIST_NAME = "sample_keyword_shas.txt"
KEYWORD_LIST_SHA256 = "7f290cdf1b2fd411238658631d6bd0f14e33b196bccfcfae55ed8cbdec5e4cb1"
QUALIFYING_PINNED = {
    "mitmproxy/mitmproxy": (
        "qualifying_mitmproxy__mitmproxy.json",
        "39ddc1938cf25e8f73512f63be07d638d15f7a3863252be63336d97ce9c5bda1",
    ),
    "plotly/dash": (
        "qualifying_plotly__dash.json",
        "bdd9122136470bdf1485cc6bfa9bc83f9e9c77ee883b1e5d00d0bdd01ed5b7af",
    ),
    "realiti4/claude-swap": (
        "qualifying_realiti4__claude-swap.json",
        "415847807fc24a8c1ca66a05fe9dda202a878309c3b36c60c8f87e2cf37280d6",
    ),
}

NOT_LISTED_MSG = "SHA is not in the pre-registered random or keyword lists."
SEPARATOR = "=" * 72
STRATA = ("random", "keyword")
BOM = chr(0xFEFF)
GIT_TIMEOUT_S = 120
GIT_GLOBAL_OPTS = [
    "-c", "core.quotepath=false",
    "-c", "core.autocrlf=false",
    "-c", "core.pager=cat",
    "-c", "color.ui=never",
]
SHA_RE = re.compile(r"[0-9a-f]{40}")
LIST_LINE_RE = re.compile(r"([A-Za-z0-9._-]+/[A-Za-z0-9._-]+) ([0-9a-f]{40})")


class GuardError(Exception):
    """A preflight guard failed. The run aborts and nothing is written."""


class NotListedError(Exception):
    """A requested SHA is not in the lists. Reported only with NOT_LISTED_MSG."""


class CommitError(Exception):
    """Rendering or writing one commit failed. That SHA is counted as failed."""


class GitError(Exception):
    """A git call failed. The message never contains git's own stderr."""


@dataclass(frozen=True)
class InputFile:
    path: Path
    sha256: str


@dataclass(frozen=True)
class Config:
    """Input locations and pinned hashes. The command line always uses
    default_config(); only the unit tests pass a different Config."""

    repo_root: Path
    random_list: InputFile
    keyword_list: InputFile
    qualifying: dict  # "owner/name" -> InputFile


def default_config() -> Config:
    spike = REPO_ROOT / "spike"
    return Config(
        repo_root=REPO_ROOT,
        random_list=InputFile(spike / RANDOM_LIST_NAME, RANDOM_LIST_SHA256),
        keyword_list=InputFile(spike / KEYWORD_LIST_NAME, KEYWORD_LIST_SHA256),
        qualifying={
            repo: InputFile(spike / name, digest)
            for repo, (name, digest) in QUALIFYING_PINNED.items()
        },
    )


@dataclass(frozen=True)
class Listed:
    repo: str
    sha: str
    stratum: str


@dataclass(frozen=True)
class Job:
    repo: str
    sha: str
    stratum: str
    parent: str
    files: list
    clone: Path


# ---------------------------------------------------------------- text / git


def normalise(raw: bytes) -> tuple[str, int, bool]:
    """Decode git output: UTF-8 with replacement, strip a leading BOM, CRLF to
    LF, and any remaining lone CR to LF.

    Returns (text, cr_count, lone_cr). cr_count is the number of CR
    characters removed or converted (CRLF pairs plus lone CRs), counted on the
    decoded text before replacement. lone_cr is True if any lone CR was seen."""
    text = raw.decode("utf-8", errors="replace")
    if text.startswith(BOM):
        text = text[1:]
    cr_count = text.count("\r")
    text = text.replace("\r\n", "\n")
    lone_cr = "\r" in text
    if lone_cr:
        text = text.replace("\r", "\n")
    return text, cr_count, lone_cr


def git_env() -> dict:
    """Child environment for git: no personal or system config, no prompts,
    no optional locks, literal pathspecs."""
    env = dict(os.environ)
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_OPTIONAL_LOCKS="0",
        GIT_PAGER="cat",
        PAGER="cat",
        GIT_LITERAL_PATHSPECS="1",
    )
    return env


def run_git(clone: Path, args: list[str]) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(clone), *GIT_GLOBAL_OPTS, *args]
    try:
        return subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=GIT_TIMEOUT_S,
            env=git_env(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {GIT_TIMEOUT_S} s") from exc
    except OSError as exc:
        raise GitError(f"could not start git: {exc.strerror}") from exc


def git_text(clone: Path, args: list[str]) -> tuple[str, int, bool]:
    result = run_git(clone, args)
    if result.returncode != 0:
        # git's stderr is not echoed: it can contain the clone path.
        raise GitError(f"git {args[0]} failed (exit {result.returncode})")
    return normalise(result.stdout)


# ---------------------------------------------------------------- inputs


def load_inputs(config: Config) -> dict:
    """Guard 1: read every pinned input once and check its SHA-256 on the raw
    bytes. The same bytes are parsed later, so nothing is read twice."""
    blobs = {}
    for item in [config.random_list, config.keyword_list, *config.qualifying.values()]:
        try:
            data = item.path.read_bytes()
        except OSError as exc:
            raise GuardError(f"cannot read input file {item.path.name}: {exc.strerror}") from exc
        if hashlib.sha256(data).hexdigest() != item.sha256:
            raise GuardError(f"SHA-256 mismatch for input file {item.path.name}")
        blobs[item.path] = data
    return blobs


def parse_list(data: bytes, stratum: str, label: str, config: Config) -> list[Listed]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GuardError(f"{label} is not valid UTF-8") from exc
    if text.startswith(BOM):
        text = text[1:]
    entries = []
    for number, line in enumerate(text.split("\n"), start=1):
        if line == "":
            continue
        match = LIST_LINE_RE.fullmatch(line)
        if match is None:
            raise GuardError(f"{label} line {number} is malformed")
        repo, sha = match.group(1), match.group(2)
        if repo not in config.qualifying:
            raise GuardError(f"{label} line {number}: repo is not one of the pinned repos")
        entries.append(Listed(repo, sha, stratum))
    return entries


def index_lists(config: Config, blobs: dict) -> tuple[list[Listed], dict]:
    ordered = parse_list(blobs[config.random_list.path], "random", config.random_list.path.name, config)
    ordered += parse_list(blobs[config.keyword_list.path], "keyword", config.keyword_list.path.name, config)
    by_sha = {}
    for entry in ordered:
        if entry.sha in by_sha:
            raise GuardError(f"{entry.sha} appears more than once in the lists")
        by_sha[entry.sha] = entry
    return ordered, by_sha


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GuardError(message)


def _is_sha(value) -> bool:
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def index_qualifying(config: Config, blobs: dict) -> tuple[dict, dict]:
    docs = {}
    where = {}  # sha -> list of (repo, commit object)
    for repo, item in config.qualifying.items():
        name = item.path.name
        try:
            doc = json.loads(blobs[item.path])
        except ValueError as exc:
            raise GuardError(f"{name} is not valid JSON") from exc
        _require(isinstance(doc, dict), f"{name}: top level is not an object")
        _require(doc.get("full_name") == repo, f"{name}: full_name does not match {repo}")
        _require(_is_sha(doc.get("head_sha")), f"{name}: head_sha is not a 40-hex SHA")
        commits = doc.get("qualifying_commits")
        _require(isinstance(commits, list), f"{name}: qualifying_commits is not a list")
        for commit in commits:
            _require(
                isinstance(commit, dict) and _is_sha(commit.get("sha")),
                f"{name}: a qualifying commit has no valid sha",
            )
            where.setdefault(commit["sha"], []).append((repo, commit))
        docs[repo] = doc
    return docs, where


def validate_commit(sha: str, commit: dict) -> None:
    _require(_is_sha(commit.get("parent")), f"{sha}: parent in the qualifying JSON is not a 40-hex SHA")
    files = commit.get("files")
    _require(isinstance(files, list) and files, f"{sha}: files in the qualifying JSON is empty or not a list")
    for entry in files:
        _require(isinstance(entry, dict), f"{sha}: a file entry is not an object")
        status = entry.get("status")
        old = entry.get("old")
        new = entry.get("new")
        sim = entry.get("similarity")
        _require(status in ("M", "D", "R"), f"{sha}: file status is not M, D or R")
        _require(isinstance(old, str) and old != "", f"{sha}: file old path is missing")
        _require(new is None or isinstance(new, str), f"{sha}: file new path is not a string or null")
        _require(
            sim is None or (isinstance(sim, int) and not isinstance(sim, bool)),
            f"{sha}: file similarity is not an integer or null",
        )
        _require(
            isinstance(entry.get("blob_unreadable", False), bool),
            f"{sha}: blob_unreadable is not a boolean",
        )
        if status == "M":
            _require(new == old, f"{sha}: M entry has different old and new paths")
        elif status == "D":
            _require(new is None, f"{sha}: D entry has a new path")
        else:
            _require(isinstance(new, str) and new != "" and new != old, f"{sha}: R entry paths are not a rename")
            _require(sim is None or sim < 100, f"{sha}: R entry similarity is not below 100")


# ---------------------------------------------------------------- guards


def resolve_out_dir(config: Config, out_arg: str | None) -> Path:
    """Guard 7: the output folder is under <repo root>/spike/packets, or
    outside the repo root entirely."""
    root = config.repo_root.resolve()
    packets_root = (root / "spike" / "packets").resolve()
    out_dir = Path(out_arg).resolve() if out_arg else packets_root
    if out_dir.is_relative_to(root) and not out_dir.is_relative_to(packets_root):
        raise GuardError("output folder is inside the repository but not under spike/packets")
    return out_dir


def _short_path(path: str) -> str | None:
    """8.3 short form of an existing path, Windows only, via the standard
    library (ctypes). None when there is no short form or it cannot be had."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        func = ctypes.windll.kernel32.GetShortPathNameW
        func.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        func.restype = wintypes.DWORD
        needed = func(path, None, 0)
        if needed == 0:
            return None
        buffer = ctypes.create_unicode_buffer(needed)
        written = func(path, buffer, needed)
        if written == 0 or written >= needed:
            return None
        return buffer.value
    except (OSError, AttributeError, ValueError):
        # Documented fallback: no short form is added to the needles.
        return None


def leak_needles(clone_root_arg: str) -> list[str]:
    """Guard 6 needles: the clone-root text as given (when absolute) and as
    resolved, its 8.3 short form, each with both slash forms, casefolded."""
    forms = set()
    given = Path(clone_root_arg)
    if given.is_absolute():
        forms.add(clone_root_arg)
    forms.add(str(given.resolve()))
    for form in list(forms):
        short = _short_path(form)
        if short:
            forms.add(short)
    needles = set()
    for form in forms:
        trimmed = form.rstrip("\\/")
        if not trimmed:
            continue
        for variant in (trimmed, trimmed.replace("\\", "/"), trimmed.replace("/", "\\")):
            needles.add(variant.casefold())
    return sorted(needles)


def contains_leak(text: str, needles: list[str]) -> bool:
    folded = text.casefold()
    return any(needle in folded for needle in needles)


def preflight(config: Config, args: argparse.Namespace) -> tuple[list[Job], Path, list[str]]:
    blobs = load_inputs(config)  # guard 1
    ordered, by_sha = index_lists(config, blobs)

    # Guard 2.
    if args.sha is not None:
        if not SHA_RE.fullmatch(args.sha) or args.sha not in by_sha:
            raise NotListedError()
        requested = [by_sha[args.sha]]
    else:
        requested = [e for e in ordered if args.stratum is None or e.stratum == args.stratum]

    out_dir = resolve_out_dir(config, args.out_dir)  # guard 7

    # Guard 5.
    clone_root = Path(args.clone_root)
    if not clone_root.is_dir():
        raise GuardError("clone root does not exist or is not a folder")
    clones = {}
    for entry in requested:
        if entry.repo not in clones:
            clone = clone_root / entry.repo.replace("/", "__")
            if not clone.is_dir():
                raise GuardError(f"clone folder for {entry.repo} does not exist")
            clones[entry.repo] = clone

    # Guard 3.
    docs, where = index_qualifying(config, blobs)
    commits = {}
    for entry in requested:
        found = where.get(entry.sha, [])
        if not found:
            raise GuardError(f"{entry.sha}: not in the qualifying JSON of {entry.repo}")
        if len(found) != 1 or found[0][0] != entry.repo:
            raise GuardError(f"{entry.sha}: the list and the qualifying JSON disagree on the repo")
        validate_commit(entry.sha, found[0][1])
        commits[entry.sha] = found[0][1]

    # Guard 4.
    try:
        for repo, clone in clones.items():
            head = git_text(clone, ["rev-parse", "--verify", "HEAD"])[0]
            if head.strip() != docs[repo]["head_sha"]:
                raise GuardError(f"{repo}: clone HEAD does not equal head_sha in the qualifying JSON")
        for entry in requested:
            clone = clones[entry.repo]
            exists = run_git(clone, ["cat-file", "-e", f"{entry.sha}^{{commit}}"])
            if exists.returncode != 0:
                raise GuardError(f"{entry.sha}: commit does not exist in the {entry.repo} clone")
            parent = git_text(clone, ["rev-parse", "--verify", "--quiet", f"{entry.sha}^"])[0]
            if parent.strip() != commits[entry.sha]["parent"]:
                raise GuardError(f"{entry.sha}: parent in the clone does not equal parent in the qualifying JSON")
    except GitError as exc:
        raise GuardError(str(exc)) from exc

    jobs = [
        Job(
            repo=e.repo,
            sha=e.sha,
            stratum=e.stratum,
            parent=commits[e.sha]["parent"],
            files=commits[e.sha]["files"],
            clone=clones[e.repo],
        )
        for e in requested
    ]
    return jobs, out_dir, leak_needles(args.clone_root)


# ---------------------------------------------------------------- rendering


def _dash(value) -> str:
    return "-" if value is None else str(value)


def _count_lines(text: str) -> int:
    if text == "":
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def parse_name_status_z(text: str) -> list[tuple[str, list[str]]]:
    tokens = text.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    entries = []
    i = 0
    while i < len(tokens):
        status = tokens[i]
        i += 1
        width = 2 if status[:1] in ("R", "C") else 1
        paths = tokens[i:i + width]
        i += width
        if status == "" or len(paths) != width:
            raise CommitError("unexpected git diff-tree output")
        entries.append((status, paths))
    return entries


def render_packet(job: Job) -> tuple[str, list[str]]:
    warnings = []
    qualifying_paths = []
    for entry in job.files:
        for path in (entry["old"], entry["new"]):
            if path is not None and path not in qualifying_paths:
                qualifying_paths.append(path)
    qualifying_set = set(qualifying_paths)

    message, _, lone_msg = git_text(job.clone, ["show", "-s", "--format=%B", job.sha])

    tree, _, lone_tree = git_text(
        job.clone,
        ["diff-tree", "-r", "--no-commit-id", "-M50%", "--name-status", "-z", job.parent, job.sha],
    )
    other_lines = [
        "\t".join([status, *paths])
        for status, paths in parse_name_status_z(tree)
        if not any(path in qualifying_set for path in paths)
    ]

    diff, diff_cr_count, lone_diff = git_text(
        job.clone,
        ["show", "-W", "-M50%", "--no-ext-diff", "--no-textconv", "--format=", job.sha, "--", *qualifying_paths],
    )
    if lone_msg or lone_tree or lone_diff:
        warnings.append("git output had a lone CR; it was written as LF")
    diff_body = diff.rstrip("\n")
    if diff_body.strip() == "":
        diff_body = "(empty diff: check the clone)"
        diff_lines = 0
        warnings.append("empty test diff; check the clone")
    else:
        diff_lines = _count_lines(diff_body)

    file_lines = []
    for entry in job.files:
        fields = [entry["status"], entry["old"], _dash(entry["new"]), _dash(entry["similarity"])]
        if entry.get("blob_unreadable") is True:
            fields.append("blob_unreadable")
        file_lines.append("\t".join(fields))

    parts = [
        "PACKET",
        f"repo: {job.repo}",
        f"sha: {job.sha}",
        f"stratum: {job.stratum}",
        f"parent: {job.parent}",
        f"qualifying files: {len(job.files)}",
        f"diff lines: {diff_lines}",
        f"CR characters normalised in test diff: {diff_cr_count}",
        "qualifying test files (status, old path, new path, similarity):",
        *file_lines,
        "Units are NOT listed here. List them by hand from the diff below.",
        SEPARATOR,
        "B. COMMIT MESSAGE",
        message.rstrip("\n"),
        SEPARATOR,
        "C. OTHER CHANGED FILES",
        "# all changed files that are not qualifying test files, with git status",
        *(other_lines or ["(none)"]),
        SEPARATOR,
        "D. TEST DIFF",
        diff_body,
        SEPARATOR,
        "END OF PACKET",
    ]
    text = "\n".join(parts) + "\n"
    if "\r" in text:
        raise CommitError("packet text contains a CR byte")
    return text, warnings


def write_packet(out_dir: Path, job: Job, text: str) -> str:
    """Write once, never overwrite. Returns "rendered" or "skipped"."""
    folder = out_dir / job.repo.replace("/", "__")
    target = folder / f"{job.sha}.txt"
    data = text.encode("utf-8")
    try:
        if target.exists():
            if target.read_bytes() == data:
                return "skipped"
            raise CommitError("an existing packet has different bytes; it was not overwritten")
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CommitError(f"could not prepare the output folder: {exc.strerror}") from exc

    tmp = folder / f".{job.sha}.txt.tmp"
    created = False
    try:
        with open(tmp, "x", encoding="utf-8", newline="\n") as handle:
            created = True
            handle.write(text)
        if target.exists():
            raise CommitError("the packet appeared during writing; it was not overwritten")
        os.rename(tmp, target)  # on Windows, rename never replaces an existing file
        created = False
    except FileExistsError as exc:
        raise CommitError("a packet or temporary file already exists; nothing was overwritten") from exc
    except OSError as exc:
        raise CommitError(f"could not write the packet: {exc.strerror}") from exc
    finally:
        if created:
            try:
                tmp.unlink()
            except OSError as exc:
                print(f"WARNING: {job.sha}: could not remove the temporary file: {exc.strerror}", file=sys.stderr)
    return "rendered"


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render Gate 0 labeling packets (read-only toward the clones).")
    parser.add_argument("--clone-root", required=True, help="folder that holds <owner>__<name> clones")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sha", help="render one pre-registered commit")
    group.add_argument("--all", action="store_true", help="render every commit in the random and keyword lists")
    parser.add_argument("--stratum", choices=STRATA, help="with --all only: render one stratum")
    parser.add_argument("--out-dir", help="output folder (default: spike/packets in the repo)")
    return parser


def main(argv: list[str] | None = None, config: Config | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.stratum is not None and not args.all:
        parser.error("--stratum is only valid with --all")
    config = config if config is not None else default_config()

    try:
        jobs, out_dir, needles = preflight(config, args)
    except NotListedError:
        print(NOT_LISTED_MSG, file=sys.stderr)
        return 2
    except GuardError as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        print("Nothing was written.", file=sys.stderr)
        return 2

    rendered = skipped = 0
    failed = []
    for job in jobs:
        try:
            text, warnings = render_packet(job)
            for warning in warnings:
                print(f"WARNING: {job.sha}: {warning}", file=sys.stderr)
            if contains_leak(text, needles):
                raise CommitError("packet text contains the clone-root path; it was not written")
            outcome = write_packet(out_dir, job, text)
        except (CommitError, GitError) as exc:
            print(f"FAILED: {job.sha}: {exc}", file=sys.stderr)
            failed.append(job.sha)
            continue
        if outcome == "skipped":
            skipped += 1
        else:
            rendered += 1

    print(f"rendered {rendered}, skipped {skipped}, failed {len(failed)}")
    for sha in failed:
        print(f"failed: {sha}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
