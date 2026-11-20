"""Regression tests for the 2026-11-20 engine code review findings."""

from __future__ import annotations

import pytest

from provecode.analyzer import analyze_file

P = "tests/test_x.py"


def rules(base, head):
    return sorted(f.rule for f in analyze_file(P, base, head).findings)


def test_form_feed_does_not_hide_a_weakening():
    base = "\x0c\ndef test_a():\n    x = 1\n    assert f() == 5\n"
    assert rules(base, base.replace("assert f() == 5", "assert f()")) == ["S2"]


@pytest.mark.parametrize("sep", ["\x0c", "\x1c", "\x85", "\u2028"])
def test_separators_in_comments_and_strings_do_not_misalign_lines(sep):
    base = f"# note{sep}more\nX = 'a{sep}b'\ndef test_a():\n    x = 1\n    assert f() == 5\n"
    res = analyze_file(P, base, base.replace("assert f() == 5", "assert f()"))
    assert [f.rule for f in res.findings] == ["S2"]
    assert res.findings[0].before == "assert f() == 5"


@pytest.mark.parametrize("sep", ["\x1c", "\x85", "\u2028"])
def test_bare_invalid_separator_is_a_gap_not_a_pass(sep):
    base = f"{sep}\ndef test_a():\n    assert f() == 5\n"
    assert [g.reason for g in analyze_file(P, base, base).gaps] == ["unparseable"]


def test_long_unbracketed_chain_is_a_gap_not_a_crash():
    chain = "+".join(["1"] * 1200)
    src = f"def test_a():\n    assert f() == {chain}\n"
    res = analyze_file(P, src, src.replace("== ", ">= "))
    assert [g.reason for g in res.gaps] == ["input_limit"]


def test_s4_drop_hard_cases_add_trivial_one():
    base = "import pytest\n@pytest.mark.parametrize('a', [1, 2, 3])\ndef test_a(a):\n    assert f(a)\n"
    assert rules(base, base.replace("[1, 2, 3]", "[1, 99]")) == ["S4"]


def test_b2_class_decorator_skip():
    base = "import pytest\nclass TestA:\n    def test_a(self):\n        assert f() == 1\n"
    assert rules(base, base.replace("class TestA", "@pytest.mark.skip\nclass TestA")) == ["B2"]


def test_b2_class_level_pytestmark():
    base = "import pytest\nclass TestA:\n    def test_a(self):\n        assert f() == 1\n"
    head = base.replace("class TestA:\n", "class TestA:\n    pytestmark = pytest.mark.skip\n")
    assert rules(base, head) == ["B2"]


def test_b2_bare_mark_import():
    base = "from pytest import mark\ndef test_a():\n    assert f() == 1\n"
    assert rules(base, base.replace("def test_a", "@mark.skip\ndef test_a")) == ["B2"]


def test_b2_imperative_xfail():
    base = "import pytest\ndef test_a():\n    assert f() == 1\n"
    assert rules(base, base.replace("    assert", "    pytest.xfail('x')\n    assert")) == ["B2"]


def test_b2_imported_skip_function():
    base = "from pytest import skip\ndef test_a():\n    assert f() == 1\n"
    assert rules(base, base.replace("    assert", "    skip('x')\n    assert")) == ["B2"]


@pytest.mark.parametrize("call", ["reader.skip(2)", "it.skip()", "skip(3)"])
def test_non_pytest_skip_calls_are_not_guards(call):
    base = "import pytest\ndef test_a(reader, it):\n    assert f() == 1\n"
    assert rules(base, base.replace("    assert", f"    {call}\n    assert")) == []


def test_utf8_bom_file_is_analyzed():
    base = "\ufeffdef test_a():\n    assert f() == 5\n"
    assert rules(base, base.replace("== 5", "> 0")) == ["S2"]


def test_approx_positional_tolerance_increase():
    base = "import pytest\ndef test_a():\n    assert f() == pytest.approx(3.0, 0.01)\n"
    assert rules(base, base.replace("0.01)", "0.5)")) == ["S1"]


def test_list_equality_relaxed_to_approx():
    base = "import pytest\ndef test_a():\n    assert f() == [1.0, 2.0]\n"
    assert rules(base, base.replace("== [1.0, 2.0]", "== pytest.approx([1.0, 2.0], rel=0.5)")) == ["S1"]


@pytest.mark.parametrize("loose", ["''", "'.*'"])
def test_s3_match_loosened_to_anything(loose):
    base = 'import pytest\ndef test_a():\n    with pytest.raises(ValueError, match="bad input"):\n        f()\n'
    assert rules(base, base.replace('"bad input"', loose)) == ["S3"]


def test_s3_match_added_where_none_was_is_not_flagged():
    base = "import pytest\ndef test_a():\n    with pytest.raises(ValueError):\n        f()\n"
    assert rules(base, base.replace("ValueError)", "ValueError, match='.*')")) == []


# ---------------------------------------------------------------- pipeline, decide, CLI

from provecode import cli, contract as C, git as git_mod, pipeline  # noqa: E402


def _doc():
    d = {"contract": {"id": "c", "version": 1},
         "conditions": [{"id": "C1", "description": "cap", "type": "boundary", "mandatory": True,
                         "guarded_by": ["tests/test_cap.py::test_cap"], "checks": []}],
         "checks": []}
    return C.approve(d, "owner", "2026-11-20T00:00:00Z")


CAP = "def test_cap():\n    assert f() == 80\n"


def test_rename_to_non_test_name_is_b1_on_the_guarded_test():
    r = pipeline.run([("tests/cap_cases.py", CAP, "def check_cap():\n    pass\n", "tests/test_cap.py")], _doc())
    f = r["findings"][0]
    assert (f["rule"], f["severity"], f["condition_ids"]) == ("B1", "blocking", ["C1"])
    assert "pytest does not collect" in f["rationale"]
    assert r["outcome"] == "FAILED"


def test_rename_between_test_names_keeps_contract_links():
    r = pipeline.run([("tests/test_pricing_cap.py", CAP, CAP.replace("== 80", ">= 0"), "tests/test_cap.py")], _doc())
    f = r["findings"][0]
    assert (f["rule"], f["severity"], f["condition_ids"]) == ("S2", "blocking", ["C1"])
    assert f["detail"]["renamed_to"] == "tests/test_pricing_cap.py"


def test_contract_touched_fails_even_without_a_valid_contract():
    r = pipeline.run([], None, touched_protected=["contracts/c.yaml"])
    assert r["outcome"] == "FAILED"


def test_approving_a_json_contract_keeps_it_json(tmp_path):
    p = tmp_path / "c.json"
    d = _doc()
    del d["approval"]
    import json

    p.write_text(json.dumps(d), encoding="utf-8")
    assert cli.main(["approve", str(p), "--by", "alice", "--at", "2026-11-20T00:00:00Z"]) == 0
    assert C.load(C.read_any(p)).approval["approved_by"] == "alice"


def test_unquoted_yaml_timestamp_does_not_crash(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("contract: {id: c, version: 1}\nconditions:\n- {id: C1, description: d, type: boundary, "
                 "mandatory: true}\nnote: 2026-11-20\n", encoding="utf-8")
    assert C.read_any(p)["note"] == "2026-11-20"


def test_internal_error_exits_3_not_1(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("docker image inspect failed")

    real = cli.build_parser

    def parser():
        ap = real()
        ap._subparsers._group_actions[0].choices["check"].set_defaults(fn=boom)
        return ap

    monkeypatch.setattr(cli, "build_parser", parser)
    assert cli.main(["check", "--base", "a", "--head", "b", "--contract", "c"]) == 3


def test_git_failure_other_than_missing_path_is_raised(tmp_path):
    import subprocess

    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.py").write_bytes(b"x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"],
                   check=True)
    assert git_mod.show(str(repo), "HEAD", "missing.py") is None          # absent: None
    assert git_mod.show(str(repo), "HEAD", "a.py") == "x = 1\n"
    with pytest.raises(git_mod.GitError):                                  # real failure: raised
        git_mod.show(str(repo), "0" * 40, "a.py")
