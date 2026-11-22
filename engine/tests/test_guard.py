"""provecode guard: working-tree / staged checks and the Claude Code hooks (ADR 0010).

Each test builds a throwaway git repository; nothing outside tmp_path is touched.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from provecode import cli, guard

TEST = "tests/test_cap.py"
BASE_SRC = "def test_cap():\n    assert cap(100) == 80\n\n\ndef test_floor():\n    assert floor(1) == 0\n"
WEAK_SRC = BASE_SRC.replace("assert cap(100) == 80", "assert cap(100) >= 0")


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                          "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, check=True)
    return out.stdout


def write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    write(r, TEST, BASE_SRC)
    write(r, "src/cap.py", "def cap(x):\n    return min(x, 80)\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "base")
    return r


CONTRACT = """\
contract: {id: checkout, version: 1}
conditions:
  - id: C1
    description: discount is capped at 80
    type: boundary
    mandatory: true
    guarded_by: ["tests/test_cap.py::test_cap"]
    checks: []
checks: []
"""


def add_contract(repo: Path) -> None:
    from provecode import contract as C
    import yaml

    doc = C.approve(yaml.safe_load(CONTRACT), "owner", "2026-11-21T00:00:00Z")
    write(repo, ".provecode/contract.yaml", yaml.safe_dump(doc, sort_keys=False))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "contract")


def run_guard(repo: Path, *extra: str) -> tuple[int, dict]:
    out = repo.parent / "guard.json"
    code = cli.main(["guard", "--repo", str(repo), "--json", str(out), *extra])
    return code, json.loads(out.read_text(encoding="utf-8"))


def rules(rep: dict) -> list[str]:
    return sorted(f["rule"] for f in rep["findings"])


# --- working tree --------------------------------------------------------------------------------

def test_uncommitted_weakening_blocks_without_a_contract(repo):
    write(repo, TEST, WEAK_SRC)
    code, rep = run_guard(repo)
    assert code == 1
    assert rules(rep) == ["S2"]
    assert rep["findings"][0]["severity"] == "warning"


def test_clean_working_tree_passes(repo):
    write(repo, "src/cap.py", "def cap(x):\n    return min(x, 80)  # unchanged behaviour\n")
    code, rep = run_guard(repo)
    assert code == 0 and rep["findings"] == []


NESTED_CFG = '[tool.pytest.ini_options]\naddopts = "-ra --strict-markers"\ntestpaths = ["tests"]\n'


def test_nested_pytest_config_shadowing_a_committed_subdirectory_config_blocks(repo):
    write(repo, "engine/pyproject.toml", NESTED_CFG)
    write(repo, "engine/tests/test_e.py", "def test_e():\n    assert 1 == 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "engine")
    write(repo, "engine/tests/pytest.ini", "[pytest]\n")
    code, rep = run_guard(repo)
    assert code == 1 and rules(rep) == ["H4"]
    assert rep["findings"][0]["file"] == "engine/tests/pytest.ini"


def test_nested_addopts_narrowing_blocks(repo):
    write(repo, "engine/pyproject.toml", NESTED_CFG)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "engine")
    write(repo, "engine/pyproject.toml", NESTED_CFG.replace('"-ra --strict-markers"', '"-ra -k \'not slow\'"'))
    code, rep = run_guard(repo)
    assert code == 1 and rules(rep) == ["H4"]


def test_strengthened_test_passes(repo):
    write(repo, TEST, BASE_SRC.replace("    assert cap(100) == 80\n", "    assert cap(100) == 80\n    assert cap(5) == 5\n"))
    code, _ = run_guard(repo)
    assert code == 0


def test_deleted_test_file_on_disk_is_b1(repo):
    (repo / TEST).unlink()
    code, rep = run_guard(repo)
    assert code == 1 and set(rules(rep)) == {"B1"}


def test_untracked_move_is_a_rename_not_a_deletion(repo):
    (repo / TEST).rename(repo / "tests" / "test_discount_cap.py")
    code, rep = run_guard(repo)
    assert code == 0
    assert "B1" not in rules(rep)


def test_untracked_new_test_file_is_fine(repo):
    write(repo, "tests/test_new.py", "def test_new():\n    assert 1 + 1 == 2\n")
    code, rep = run_guard(repo)
    assert code == 0 and rep["findings"] == []


def test_unrelated_untracked_move_still_counts_as_deletion(repo):
    (repo / TEST).unlink()
    write(repo, "tests/test_other.py", "def test_other():\n    assert something_else() is True\n")
    code, rep = run_guard(repo)
    assert code == 1 and "B1" in rules(rep)


# --- staged --------------------------------------------------------------------------------------

def test_staged_mode_checks_only_what_will_be_committed(repo):
    write(repo, TEST, WEAK_SRC)  # weakened on disk, not staged
    code, rep = run_guard(repo, "--staged")
    assert code == 0 and rep["findings"] == []
    git(repo, "add", TEST)
    code, rep = run_guard(repo, "--staged")
    assert code == 1 and rules(rep) == ["S2"]


# --- contract ------------------------------------------------------------------------------------

def test_contract_is_read_at_base_and_grades_blocking(repo):
    add_contract(repo)
    write(repo, TEST, WEAK_SRC)
    code, rep = run_guard(repo)
    assert code == 1 and rep["outcome"] == "FAILED"
    assert rep["findings"][0]["severity"] == "blocking"
    assert rep["findings"][0]["condition_ids"] == ["C1"]


def test_relaxing_the_contract_in_the_working_tree_does_not_help(repo):
    add_contract(repo)
    write(repo, TEST, WEAK_SRC)
    text = (repo / ".provecode/contract.yaml").read_text(encoding="utf-8")
    write(repo, ".provecode/contract.yaml", text.replace("mandatory: true", "mandatory: false"))
    code, rep = run_guard(repo)
    assert code == 1 and rep["outcome"] == "FAILED"
    assert "CONTRACT_TOUCHED" in rules(rep)
    s2 = next(f for f in rep["findings"] if f["rule"] == "S2")
    assert s2["severity"] == "blocking"  # graded by the base contract, not the edited one


def test_block_on_failed_ignores_ungraded_warnings(repo):
    write(repo, TEST, WEAK_SRC)
    code, _ = run_guard(repo, "--block-on", "failed")
    assert code == 0


# --- read-only -----------------------------------------------------------------------------------

def _state(repo: Path) -> tuple:
    idx = (repo / ".git" / "index").read_bytes()
    objs = sorted(p.relative_to(repo / ".git" / "objects").as_posix()
                  for p in (repo / ".git" / "objects").rglob("*") if p.is_file())
    return idx, objs, git(repo, "status", "--porcelain")


def test_guard_never_writes_to_the_repository(repo):
    (repo / TEST).rename(repo / "tests" / "test_moved.py")
    write(repo, "tests/test_new.py", "def test_new():\n    assert 1 == 1\n")
    before = _state(repo)
    run_guard(repo)
    run_guard(repo, "--staged")
    assert _state(repo) == before


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlinked_test_file_is_a_gap_not_followed(repo, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("def test_cap():\n    assert True\n", encoding="utf-8")
    (repo / TEST).unlink()
    (repo / TEST).symlink_to(outside)
    _, rep = run_guard(repo)
    assert any(g["reason"] == "unparseable" and "symlink" in g["detail"] for g in rep["gaps"])


# --- Claude Code hooks ---------------------------------------------------------------------------

def hook(kind: str, event: dict, monkeypatch, tmp_path) -> tuple[int, dict | None]:
    monkeypatch.setenv("PROVECODE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    code = cli.main(["guard", "--hook", kind])
    text = buf.getvalue().strip()
    return code, (json.loads(text) if text else None)


def test_stop_hook_blocks_a_weakening(repo, monkeypatch, tmp_path):
    write(repo, TEST, WEAK_SRC)
    code, out = hook("claude-stop", {"session_id": "s1", "cwd": str(repo), "stop_hook_active": False},
                     monkeypatch, tmp_path)
    assert code == 0
    assert out["decision"] == "block"
    assert "S2" in out["reason"] and "tests/test_cap.py" in out["reason"]
    assert "systemMessage" in out


def test_stop_hook_allows_a_clean_stop_silently(repo, monkeypatch, tmp_path):
    code, out = hook("claude-stop", {"session_id": "s1", "cwd": str(repo)}, monkeypatch, tmp_path)
    assert code == 0 and out is None


def test_stop_hook_keeps_blocking_while_still_weakened(repo, monkeypatch, tmp_path):
    write(repo, TEST, WEAK_SRC)
    _, out = hook("claude-stop", {"session_id": "s1", "cwd": str(repo), "stop_hook_active": True},
                  monkeypatch, tmp_path)
    assert out["decision"] == "block"


def test_session_start_base_survives_an_agent_commit(repo, monkeypatch, tmp_path):
    hook("claude-session-start", {"session_id": "s-9", "cwd": str(repo), "source": "startup"}, monkeypatch, tmp_path)
    write(repo, TEST, WEAK_SRC)
    git(repo, "commit", "-q", "-am", "agent commits the weakening")
    _, out = hook("claude-stop", {"session_id": "s-9", "cwd": str(repo)}, monkeypatch, tmp_path)
    assert out is not None and out["decision"] == "block"


def test_hook_fails_open_outside_a_git_repository(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    code, out = hook("claude-stop", {"session_id": "s1", "cwd": str(plain)}, monkeypatch, tmp_path)
    assert code == 0
    assert "decision" not in out and "PROVECODE" in out["systemMessage"]


@pytest.mark.parametrize("sid,ok", [("abc-123_X", True), ("../../etc/passwd", False), ("", False), ("a" * 300, False)])
def test_session_ids_are_sanitised(sid, ok):
    assert (guard.session_file(sid) is not None) is ok


def test_first_commit_in_a_new_repository_is_allowed(tmp_path):
    r = tmp_path / "fresh"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    write(r, TEST, BASE_SRC)
    git(r, "add", "-A")
    code = cli.main(["guard", "--repo", str(r), "--staged"])
    assert code == 0


def test_unknown_explicit_base_is_an_error_not_a_pass(repo):
    assert cli.main(["guard", "--repo", str(repo), "--base", "no-such-ref"]) == 3
