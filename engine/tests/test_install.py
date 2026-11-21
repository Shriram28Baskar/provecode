"""provecode install-hooks (ADR 0010): merge, idempotence, refusals, uninstall, and a real git commit."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from provecode import cli

BASE_SRC = "def test_cap():\n    assert cap(100) == 80\n"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, check=check)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "tests").mkdir(parents=True)
    git(r, "init", "-q", "-b", "main")
    (r / "tests" / "test_cap.py").write_text(BASE_SRC, encoding="utf-8", newline="\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "base")
    return r


def settings(repo: Path, name: str = "settings.local.json") -> dict:
    return json.loads((repo / ".claude" / name).read_text(encoding="utf-8"))


def commands(data: dict, event: str) -> list[str]:
    return [h["command"] for g in data["hooks"].get(event, []) for h in g["hooks"]]


def test_claude_install_creates_both_hooks(repo):
    assert cli.main(["install-hooks", "--repo", str(repo), "--claude"]) == 0
    data = settings(repo)
    assert any(c.endswith("guard --hook claude-session-start") for c in commands(data, "SessionStart"))
    assert any(c.endswith("guard --hook claude-stop") for c in commands(data, "Stop"))
    assert "-m provecode.cli" in commands(data, "Stop")[0]  # local: absolute interpreter, no PATH needed


def test_claude_install_merges_and_is_idempotent(repo):
    existing = {"permissions": {"allow": ["Bash(ls)"]},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo done"}]}]}}
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.local.json").write_text(json.dumps(existing), encoding="utf-8")
    cli.main(["install-hooks", "--repo", str(repo), "--claude"])
    first = settings(repo)
    cli.main(["install-hooks", "--repo", str(repo), "--claude"])
    assert settings(repo) == first  # second install changes nothing
    assert first["permissions"] == existing["permissions"]
    assert "echo done" in commands(first, "Stop") and len(commands(first, "Stop")) == 2


def test_shared_install_uses_bare_command(repo):
    cli.main(["install-hooks", "--repo", str(repo), "--claude", "--shared"])
    assert commands(settings(repo, "settings.json"), "Stop") == ["provecode guard --hook claude-stop"]


def test_invalid_json_is_refused_and_left_alone(repo, capsys):
    (repo / ".claude").mkdir()
    p = repo / ".claude" / "settings.local.json"
    p.write_text("{not json", encoding="utf-8")
    assert cli.main(["install-hooks", "--repo", str(repo), "--claude"]) == 3
    assert p.read_text(encoding="utf-8") == "{not json"
    assert "not valid JSON" in capsys.readouterr().err


def test_uninstall_removes_only_ours(repo):
    existing = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo done"}]}]}}
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.local.json").write_text(json.dumps(existing), encoding="utf-8")
    cli.main(["install-hooks", "--repo", str(repo), "--claude"])
    cli.main(["install-hooks", "--repo", str(repo), "--claude", "--uninstall"])
    assert settings(repo) == existing


def test_precommit_blocks_a_weakening_commit_and_allows_a_clean_one(repo):
    assert cli.main(["install-hooks", "--repo", str(repo), "--pre-commit"]) == 0
    env_ok = git(repo, "rev-parse", "--git-path", "hooks/pre-commit").stdout.strip()
    assert (repo / env_ok).exists() or Path(env_ok).exists()
    t = repo / "tests" / "test_cap.py"
    t.write_text(BASE_SRC.replace("== 80", ">= 0"), encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    blocked = git(repo, "commit", "-q", "-m", "weaken", check=False)
    assert blocked.returncode != 0 and "S2" in blocked.stderr
    t.write_text(BASE_SRC + "\n\ndef test_more():\n    assert cap(5) == 5\n", encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    assert git(repo, "commit", "-q", "-m", "add a test", check=False).returncode == 0


def test_foreign_precommit_hook_is_left_alone(repo, capsys):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    assert cli.main(["install-hooks", "--repo", str(repo), "--pre-commit"]) == 3
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"
    assert "not written by provecode" in capsys.readouterr().err


def test_precommit_uninstall(repo):
    cli.main(["install-hooks", "--repo", str(repo), "--pre-commit"])
    cli.main(["install-hooks", "--repo", str(repo), "--pre-commit", "--uninstall"])
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()


def test_nothing_chosen_is_a_usage_error(repo):
    assert cli.main(["install-hooks", "--repo", str(repo)]) == 3


def test_interpreter_is_this_one():
    from provecode import install

    assert Path(install._python().strip('"')) == Path(sys.executable)


# --- found by running the hook in a real Claude Code session: an interpreter that cannot import
# provecode made every hook run fail before guard started, so the hook failed open silently.

@pytest.mark.parametrize("flag", ["--claude", "--pre-commit"])
def test_refuses_when_the_interpreter_cannot_import_provecode(repo, capsys, monkeypatch, flag):
    from provecode import install
    monkeypatch.setattr(install, "_PROBE", "import provecode_not_a_module")
    assert cli.main(["install-hooks", "--repo", str(repo), flag]) == 3
    assert "fail open" in capsys.readouterr().err
    assert not (repo / ".claude").exists()
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()


def test_shared_install_does_not_probe_this_interpreter(repo, monkeypatch):
    from provecode import install
    monkeypatch.setattr(install, "_PROBE", "import provecode_not_a_module")
    assert cli.main(["install-hooks", "--repo", str(repo), "--claude", "--shared"]) == 0
