"""provecode command line.

  provecode check   --repo . --base REF --head REF --contract C.yaml [--contract-base C0.yaml]
  provecode record  ...same as check, plus contract checks and repo tests in Docker
  provecode rerun   --repo . --base REF --head REF --image IMG   runs A/B/C: base tests vs head code (ADR 0013)
  provecode guard   [--staged] [--base HEAD]   uncommitted/staged changes; pre-commit and agent hooks
  provecode install-hooks --claude | --pre-commit   wire guard into Claude Code or git
  provecode approve C.yaml --by NAME [--at ISO8601]
  provecode compile C.yaml [-o C.json]

Exit codes: 0 VERIFIED, 1 FAILED, 2 INCONCLUSIVE, 3 usage or internal error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any
from collections.abc import Callable

from . import contract as contract_mod
from . import git as git_mod
from . import changeset as cs_mod
from . import pipeline, report as report_mod
from .analyzer import is_test_path
from .model import Gap

EXIT = {"VERIFIED": 0, "FAILED": 1, "INCONCLUSIVE": 2}
DEFAULT_PROTECTED = ("contracts/", ".provecode/", "contract_checks/")


def _protected(path: str, extra: list[str]) -> bool:
    p = path.replace("\\", "/")
    return any(p == x.rstrip("/") or p.startswith(x if x.endswith("/") else x + "/") or p == x
               for x in (*DEFAULT_PROTECTED, *extra))


MAX_SOURCE_FILES = 500  # H6 reads changed source files for added `# pragma: no cover`; beyond this, a gap


def harness_changes(repo: str, base: str, changes: list[git_mod.Change], read_head: Callable[[str], str | None],
                    test_paths: set[str]) -> tuple[list[cs_mod.FileChange], list[Gap]]:
    """FileChanges for everything except test files that the harness rules read (ADR 0012), plus the
    unchanged root pytest configuration files as context. read_head(path) returns head content."""
    out, gaps = [], []
    seen = set()
    sources = 0
    for ch in changes:
        kind = cs_mod.classify(ch.path)
        if ch.path in test_paths or kind not in cs_mod.ANALYZED | {cs_mod.SOURCE}:
            continue
        if kind == cs_mod.SOURCE:
            if ch.status == "D":
                continue
            sources += 1
            if sources > MAX_SOURCE_FILES:
                if sources == MAX_SOURCE_FILES + 1:
                    gaps.append(Gap("input_limit", f"more than {MAX_SOURCE_FILES} changed source files; the rest were "
                                                   "not checked for added `# pragma: no cover`"))
                continue
        try:
            b = None if ch.status == "A" else git_mod.show(repo, base, ch.old_path or ch.path)
            h = None if ch.status == "D" else read_head(ch.path)
        except git_mod.DecodeError as exc:
            gaps.append(Gap("unparseable", str(exc), file=ch.path))
            continue
        out.append(cs_mod.FileChange(ch.path, ch.status, b, h, ch.old_path))
        seen.add(ch.path)
    # Context: the unchanged pytest config files at the root and, for a changed config file in a
    # subdirectory (a subproject such as engine/), in that directory and every one above it.
    dirs = {""}
    for p in seen:
        if p.rsplit("/", 1)[-1] in cs_mod.PYTEST_CONFIG_NAMES and "/" in p:
            d = p.rsplit("/", 1)[0]
            while d:
                dirs.add(d)
                d = d.rsplit("/", 1)[0] if "/" in d else ""
    for d in sorted(dirs):
        for name in cs_mod.PYTEST_CONFIG_NAMES:
            path = f"{d}/{name}" if d else name
            if path in seen:
                continue
            try:
                text = git_mod.show(repo, base, path)
            except git_mod.DecodeError:
                continue
            if text is not None:
                out.append(cs_mod.FileChange(path, "U", text, text))
    return out, gaps


def _collect(args: argparse.Namespace) -> tuple[cs_mod.Changeset, list[str], dict[str, Any], list[Gap]]:
    repo = args.repo
    base = git_mod.rev_parse(repo, args.base)
    head = git_mod.rev_parse(repo, args.head)
    files: list[tuple[Any, ...]] = []
    touched: list[str] = []
    gaps: list[Gap] = []
    for ch in git_mod.changed_files(repo, base, head):
        if _protected(ch.path, args.protect) or (ch.old_path and _protected(ch.old_path, args.protect)):
            touched.append(ch.path)
        # Only test files are analyzed; fetching other blobs (binaries, assets) would add noise.
        if not (is_test_path(ch.path) or (ch.old_path and is_test_path(ch.old_path))):
            continue
        try:
            if ch.status == "A":
                files.append((ch.path, None, git_mod.show(repo, head, ch.path)))
            elif ch.status == "D":
                files.append((ch.path, git_mod.show(repo, base, ch.path), None))
            elif ch.status == "R":
                files.append((ch.path, git_mod.show(repo, base, ch.old_path or ch.path),
                              git_mod.show(repo, head, ch.path),
                              ch.old_path))
            else:
                files.append((ch.path, git_mod.show(repo, base, ch.path), git_mod.show(repo, head, ch.path)))
        except git_mod.DecodeError as exc:  # other git failures propagate and exit 3
            gaps.append(Gap("unparseable", str(exc), file=ch.path))
    test_paths = {e[0] for e in files}
    extra, egaps = harness_changes(repo, base, git_mod.changed_files(repo, base, head),
                                   lambda p: git_mod.show(repo, head, p), test_paths)
    cs = cs_mod.from_tuples(files, base_paths=git_mod.ls_tree(repo, base))
    cs.files += extra
    cs.base_sha, cs.head_sha = base, head
    prov = {"repo": str(Path(repo).resolve().name), "base_sha": base, "head_sha": head,
            "generated_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()}
    return cs, touched, prov, gaps + egaps


def _contracts(args: argparse.Namespace) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[Gap]]:
    gaps = []
    head = base = None
    try:
        head = contract_mod.read_any(args.contract)
    except contract_mod.ContractError as exc:
        gaps.append(Gap("contract_invalid", str(exc)))
    if args.contract_base:
        try:
            base = contract_mod.read_any(args.contract_base)
        except contract_mod.ContractError as exc:
            gaps.append(Gap("contract_invalid", f"base contract: {exc}"))
    return head, base, gaps


def _emit(rep: dict[str, Any], args: argparse.Namespace) -> int:
    text = report_mod.dumps(rep)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(text, encoding="utf-8", newline="\n")
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(report_mod.to_markdown(rep), encoding="utf-8", newline="\n")
    if not args.json and not args.md:
        sys.stdout.write(report_mod.to_markdown(rep))
    else:
        print(f"{rep['outcome']}: {len(rep['findings'])} finding(s), {len(rep['gaps'])} gap(s)")
    return EXIT[rep["outcome"]]


def cmd_rerun(args: argparse.Namespace) -> int:
    from . import rerun  # Docker path; CLI/CI only (ADR 0013)

    return _emit(rerun.run(args), args)


def cmd_check(args: argparse.Namespace) -> int:
    if getattr(args, "execute", False):
        if not args.image:
            print("provecode: error: --execute needs --image (a Docker image with Python and pytest)", file=sys.stderr)
            return 3
        if args.contract:
            print("provecode: error: --execute uses the base branch's tests as the contract; run `provecode record` "
                  "for contract-owned checks", file=sys.stderr)
            return 3
        return cmd_rerun(args)
    block_on = getattr(args, "block_on", None)
    if not args.contract and block_on != "weakening":
        print("provecode: error: --contract is required (or use --block-on weakening to block any test weakening "
              "without one, or --execute to judge against the base branch's tests)", file=sys.stderr)
        return 3
    files, touched, prov, gaps = _collect(args)
    head, base, cgaps = _contracts(args) if args.contract else (None, None, [])
    rep = pipeline.run(files, head, base, mode="static", touched_protected=touched,
                       extra_gaps=gaps + cgaps, provenance=prov)
    code = _emit(rep, args)
    if block_on == "weakening" and code != EXIT["FAILED"]:
        from .guard import blocks  # same zero-config rule as `provecode guard` (ADR 0010)

        if blocks(rep, "weakening"):
            print("provecode: blocking: the change weakens tests (--block-on weakening)", file=sys.stderr)
            return EXIT["FAILED"]
    return code


def cmd_record(args: argparse.Namespace) -> int:
    from . import exec as exec_mod  # Docker path; CLI/CI only

    files, touched, prov, gaps = _collect(args)
    head, base, cgaps = _contracts(args)
    evidence, egaps, eprov = exec_mod.run_checks(args, head, prov["head_sha"])
    prov.update(eprov)
    rep = pipeline.run(files, head, base, mode="recorded", touched_protected=touched, evidence=evidence,
                       executed=True, extra_gaps=gaps + cgaps + egaps, provenance=prov)
    return _emit(rep, args)


def cmd_approve(args: argparse.Namespace) -> int:
    doc = contract_mod.read_any(args.contract)
    at = args.at or dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    out = contract_mod.approve(doc, args.by, at)
    if Path(args.contract).suffix == ".json":  # keep the file's own format
        text = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    else:
        import yaml  # type: ignore[import-untyped]  # CLI extra

        text = yaml.safe_dump(out, sort_keys=False, allow_unicode=True)
    Path(args.contract).write_text(text, encoding="utf-8", newline="\n")
    print(out["approval"]["content_hash"])
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    data = contract_mod.compile_json(contract_mod.read_any(args.contract))
    if args.output:
        Path(args.output).write_bytes(data)
    else:
        sys.stdout.write(data.decode("utf-8"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="provecode", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("check", cmd_check), ("record", cmd_record)):
        p = sub.add_parser(name)
        p.add_argument("--repo", default=".")
        p.add_argument("--base", required=True)
        p.add_argument("--head", required=True)
        p.add_argument("--contract", required=(name == "record"),
                       help="trusted, approved contract (outside the candidate)")
        p.add_argument("--contract-base", help="contract version in effect at base, to detect approved changes")
        p.add_argument("--protect", action="append", default=[], help="extra protected path (repeatable)")
        p.add_argument("--json")
        p.add_argument("--md")
        if name == "record":
            p.add_argument("--image", required=True, help="Docker image reference pinned by digest")
            p.add_argument("--checks-dir", required=True, help="trusted directory holding contract-owned checks")
            p.add_argument("--timeout", type=int, default=120)
        else:
            p.add_argument("--execute", action="store_true",
                           help="also run the base branch's tests against the head in sealed containers (ADR 0013)")
            p.add_argument("--image", help="Docker image with Python and pytest, for --execute")
            p.add_argument("--timeout", type=int, default=600, help="seconds per sealed test run")
            p.add_argument("--deps-timeout", type=int, default=900, help="seconds for building the wheelhouse")
            p.add_argument("--block-on", choices=["weakening", "failed"],
                           help="weakening: exit 1 on any test-weakening finding, contract or not (no contract "
                                "needed); failed: exit 1 only on a FAILED verdict (the default)")
        p.set_defaults(fn=fn)
    p = sub.add_parser("rerun", help="trusted re-run: runs A/B/C in sealed containers, base tests as the contract")
    p.add_argument("--repo", default=".")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--image", required=True, help="Docker image with Python and pytest (pin by digest)")
    p.add_argument("--timeout", type=int, default=600, help="seconds per sealed test run")
    p.add_argument("--deps-timeout", type=int, default=900, help="seconds for building the wheelhouse")
    p.add_argument("--wheelhouse", choices=["host", "container"], default="host",
                   help="host: pip download --only-binary on the host (no package code runs); container: pip wheel in "
                        "a networked container that sees only the base dependency list")
    p.add_argument("--protect", action="append", default=[], help="extra protected path (repeatable)")
    p.add_argument("--json")
    p.add_argument("--md")
    p.set_defaults(fn=cmd_rerun)
    from . import guard, install  # local and agent-loop gate (ADR 0010)

    guard.add_parser(sub)
    install.add_parser(sub)
    p = sub.add_parser("approve")
    p.add_argument("contract")
    p.add_argument("--by", required=True)
    p.add_argument("--at")
    p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("compile")
    p.add_argument("contract")
    p.add_argument("-o", "--output")
    p.set_defaults(fn=cmd_compile)
    return ap


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except (git_mod.GitError, contract_mod.ContractError, OSError) as exc:
        print(f"provecode: error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"provecode: internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
