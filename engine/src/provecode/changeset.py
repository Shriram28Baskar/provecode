"""Host-independent change sets (ADR 0012). Stdlib only.

Every adapter (git for `check` and `guard`, the GitHub REST adapter in the browser, a report
upload) produces a Changeset; pipeline.run consumes one. Paths use forward slashes and are
relative to the repository root.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .analyzer import is_test_path

TEST, CONFTEST, HARNESS, CONFIG, CI, SOURCE, OTHER = "test", "conftest", "harness", "config", "ci", "source", "other"

# Files pytest reads its configuration from (pytest 9, _pytest/config/findpaths.py), plus the
# coverage and dependency files H4, H6 and H7 read.
PYTEST_CONFIG_NAMES = ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini",
                       "setup.cfg")
CONFIG_NAMES = frozenset(PYTEST_CONFIG_NAMES) | {".coveragerc"}


def _name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def is_requirements(path: str) -> bool:
    p = path.replace("\\", "/")
    name = _name(p)
    return name.endswith((".txt", ".in")) and ("requirements" in name or "/requirements/" in f"/{p}") \
        and not p.startswith(("docs/", "doc/"))


def classify(path: str) -> str:
    p = path.replace("\\", "/")
    name = _name(p)
    if name == "conftest.py":
        return CONFTEST
    if name.endswith(".pth") or name in ("sitecustomize.py", "usercustomize.py"):
        return HARNESS
    if is_test_path(p):
        return TEST
    if name in CONFIG_NAMES or is_requirements(p):
        return CONFIG
    if p.startswith(".github/workflows/") and name.endswith((".yml", ".yaml")):
        return CI
    if name.endswith(".py"):
        return SOURCE
    return OTHER


# Classes whose contents the engine reads. Source files are read only for H6's pragma check.
ANALYZED = frozenset({TEST, CONFTEST, HARNESS, CONFIG, CI})


@dataclass
class FileChange:
    path: str  # head path (base path for a deletion)
    status: str  # A, M, D or R
    base_text: str | None
    head_text: str | None
    old_path: str | None = None

    @property
    def kind(self) -> str:
        return classify(self.path)


@dataclass
class Changeset:
    files: list[FileChange] = field(default_factory=list)
    base_sha: str | None = None
    head_sha: str | None = None
    base_paths: tuple[str, ...] | None = None  # every path at base, when the adapter can list them

    def test_entries(self) -> list[tuple]:
        """The (path, base, head[, old_path]) tuples the test-file analyzer has always consumed."""
        out: list[tuple] = []
        for f in self.files:
            if not (is_test_path(f.path) or (f.old_path and is_test_path(f.old_path))):
                continue
            if f.old_path and f.old_path != f.path:
                out.append((f.path, f.base_text, f.head_text, f.old_path))
            else:
                out.append((f.path, f.base_text, f.head_text))
        return out

    def by_kind(self, *kinds: str) -> list[FileChange]:
        return [f for f in self.files if f.kind in kinds]


def from_tuples(files: list[tuple], base_paths: tuple[str, ...] | None = None) -> Changeset:
    """Adapter from the legacy tuple list, so old callers and new ones share one code path."""
    out = []
    for e in files:
        path, b, h = e[0], e[1], e[2]
        old = e[3] if len(e) > 3 else None
        status = "R" if old and old != path else "A" if b is None else "D" if h is None else "M"
        out.append(FileChange(path, status, b, h, old))
    return Changeset(out, base_paths=base_paths)
