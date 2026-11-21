"""Run B's tree: head code, base tests and harness (ADR 0013, section 1). Host-side; CLI only.

Starting from the exported head tree:
  1. every base test file, conftest.py, root pytest/coverage config file, and every non-Python file
     under a test directory is copied over from base;
  2. pyproject.toml keeps head's content, with its pytest tables replaced by base's (textually: the
     stdlib cannot write TOML); the result must parse and must give pytest exactly base's settings;
  3. test-side files that exist only in head (new test modules, new conftest.py files, new files in
     test directories) and import-time harness files (.pth, sitecustomize.py, usercustomize.py,
     modules shadowing the stdlib, a test dependency or the package) are deleted, because pytest
     imports test modules and conftests before any test runs and could be told to pass.
Every replacement and deletion is returned as evidence.
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .analyzer import is_test_path
from .changeset import PYTEST_CONFIG_NAMES, Changeset, FileChange
from .harness import _h3, pytest_config

ROOT_CONFIG = set(PYTEST_CONFIG_NAMES) - {"pyproject.toml"} | {".coveragerc"}
PYTEST_TABLES = ("[tool.pytest]", "[tool.pytest.ini_options]")


class OverlayError(Exception):
    """The overlay could not be built faithfully; the run is INCONCLUSIVE (overlay_failed)."""


@dataclass
class Overlay:
    replaced: list[str] = field(default_factory=list)  # taken from base
    deleted: list[str] = field(default_factory=list)  # head-only test-side or harness files removed
    reasons: dict[str, str] = field(default_factory=dict)


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def _test_side(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return is_test_path(rel) or name == "conftest.py" or "tests" in parts[:-1] or "test" in parts[:-1]


def _tables(text: str) -> tuple[list[str], list[tuple[int, int]]]:
    """Lines, and [start, end) line ranges of the pytest tables (including dotted sub-tables)."""
    lines = text.splitlines(keepends=True)
    spans, start = [], None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and not s.startswith("[["):
            if start is not None:
                spans.append((start, i))
                start = None
            head = s.split("]")[0] + "]"
            if head in PYTEST_TABLES or head.startswith(("[tool.pytest.", "[tool.pytest]")):
                start = i
    if start is not None:
        spans.append((start, len(lines)))
    return lines, spans


def merge_pyproject(base: str, head: str) -> str:
    """head's pyproject.toml with its pytest tables replaced by base's."""
    hl, hs = _tables(head)
    bl, bs = _tables(base)
    kept = [l for i, l in enumerate(hl) if not any(a <= i < b for a, b in hs)]
    merged = "".join(kept)
    if not merged.endswith("\n"):
        merged += "\n"
    for a, b in bs:
        merged += "\n" + "".join(bl[a:b])
    try:
        tomllib.loads(merged)
        if (pytest_config("pyproject.toml", merged) or {}) != (pytest_config("pyproject.toml", base) or {}):
            raise OverlayError("merged pyproject.toml does not give pytest the base settings")
    except tomllib.TOMLDecodeError as exc:
        raise OverlayError(f"merged pyproject.toml is not valid TOML: {exc}") from None
    return merged


def build(base_dir: Path, head_dir: Path, out_dir: Path) -> Overlay:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(head_dir, out_dir)
    ov = Overlay()
    base_files, head_files = _files(base_dir), _files(head_dir)
    for rel in sorted(base_files):
        name = rel.rsplit("/", 1)[-1]
        if _test_side(rel) or ("/" not in rel and name in ROOT_CONFIG):
            dst = out_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(base_dir / rel, dst)
            if rel not in head_files or (head_dir / rel).read_bytes() != (base_dir / rel).read_bytes():
                ov.replaced.append(rel)
    if "pyproject.toml" in base_files and "pyproject.toml" in head_files:
        b = (base_dir / "pyproject.toml").read_text(encoding="utf-8")
        h = (head_dir / "pyproject.toml").read_text(encoding="utf-8")
        merged = merge_pyproject(b, h)
        if merged != h:
            (out_dir / "pyproject.toml").write_text(merged, encoding="utf-8", newline="\n")
            ov.replaced.append("pyproject.toml [tool.pytest*]")
    for rel in sorted(head_files - base_files):
        if _test_side(rel) or ("/" not in rel and rel.rsplit("/", 1)[-1] in ROOT_CONFIG):
            (out_dir / rel).unlink()
            ov.deleted.append(rel)
            ov.reasons[rel] = "test-side file that exists only in head"
    added = [FileChange(rel, "A", None, _read(head_dir / rel)) for rel in sorted(head_files - base_files)
             if rel not in ov.deleted]
    for f in _h3(Changeset(added, base_paths=tuple(sorted(base_files)))):
        if (out_dir / f.file).exists():
            (out_dir / f.file).unlink()
            ov.deleted.append(f.file)
            ov.reasons[f.file] = f.rationale
    for rel in sorted(head_files & base_files):
        name = rel.rsplit("/", 1)[-1]
        if name.endswith(".pth") or name in ("sitecustomize.py", "usercustomize.py"):
            shutil.copyfile(base_dir / rel, out_dir / rel)
            if (head_dir / rel).read_bytes() != (base_dir / rel).read_bytes():
                ov.replaced.append(rel)
    return ov


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""
