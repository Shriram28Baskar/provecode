"""docs/research/scripts/label.py: the owner labeling tool for real-history flags (brief Phase 6).
Research tooling, loaded by path like demo/build_demo.py in test_cli.py. It must never write a label
without an explicit answer, and must refuse non-interactive input."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("label_tool", ROOT / "docs" / "research" / "scripts" / "label.py")
label = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(label)


def cand(repo, commit, rules):
    return {"repo": repo, "commit": commit, "ai_trailer": False, "populations": ["H"], "rules": rules,
            "findings": [{"rule": r, "file": "tox.ini", "test": None, "severity": "warning", "rationale": "why"}
                         for r in rules], "owner_label": None}


CANDS = [cand("o/a", "1" * 40, ["H5"]), cand("o/a", "2" * 40, ["H5", "H6"]), cand("o/b", "3" * 40, ["T1"]),
         cand("o/b", "4" * 40, ["H6"]), cand("o/c", "5" * 40, ["H5"])]


def write_data(tmp_path, cands=CANDS):
    p = tmp_path / "scan.json"
    p.write_text(json.dumps({"candidates_for_owner_labeling": cands}), encoding="utf-8")
    return p


class Tty(io.StringIO):
    def isatty(self):
        return True


def fake_git(repo, *args):
    return "diff --git a/tox.ini b/tox.ini\n" + "\n".join(f"+line {i}" for i in range(300))


def test_data_file_with_an_owner_label_is_refused(tmp_path):
    bad = [dict(CANDS[0], owner_label="legit")]
    with pytest.raises(SystemExit, match="owner_label"):
        label.load_candidates(write_data(tmp_path, bad))


def test_labels_latest_wins_and_malformed_lines_are_errors(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text('{"key": "k", "label": "legit"}\n\n{"key": "k", "label": "weakening"}\n', encoding="utf-8")
    assert label.load_labels(p)["k"]["label"] == "weakening"
    p.write_text('{"key": "k", "label": "maybe"}\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="label"):
        label.load_labels(p)
    p.write_text("not json\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="not JSON"):
        label.load_labels(p)


def test_select_filters_by_rule_and_skips_labeled_unless_relabel():
    labels = {label.key(CANDS[0]): {"label": "legit"}}
    assert {c["commit"][0] for c in label.select(CANDS, labels, rule="H5")} == {"2", "5"}
    assert {c["commit"][0] for c in label.select(CANDS, labels, rule="H5", relabel=True)} == {"1", "2", "5"}


def test_sample_is_per_rule_reproducible_and_independent_of_labels():
    a = label.select(CANDS, {}, sample=1)
    b = label.select(list(reversed(CANDS)), {}, sample=1)
    assert [label.key(c) for c in a] == [label.key(c) for c in b]
    covered = {r for c in a for r in c["rules"]}
    assert covered == {"H5", "H6", "T1"}  # every rule gets its sample
    assert len(a) <= 3


def test_wilson_interval():
    assert label.wilson(0, 0) == (0.0, 1.0)
    lo, hi = label.wilson(8, 10)
    assert round(lo, 3) == 0.490 and round(hi, 3) == 0.943
    lo, hi = label.wilson(10, 10)
    assert hi == 1.0 and round(lo, 3) == 0.722


def test_summary_excludes_unsure_from_precision():
    labels = {label.key(CANDS[0]): {"label": "weakening"}, label.key(CANDS[1]): {"label": "legit"},
              label.key(CANDS[4]): {"label": "unsure"}}
    h5 = next(r for r in label.summary(CANDS, labels) if r["rule"] == "H5")
    assert (h5["flagged"], h5["labeled"], h5["weakening"], h5["legit"], h5["unsure"]) == (3, 3, 1, 1, 1)
    assert h5["precision"] == 0.5
    t1 = next(r for r in label.summary(CANDS, labels) if r["rule"] == "T1")
    assert t1["precision"] is None and t1["ci95"] is None


def test_label_loop_records_only_explicit_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(label, "repo_dir", lambda repo: tmp_path)  # a clone cache that exists
    p = tmp_path / "labels.jsonl"
    answers = iter(["f", "w", "loosened on purpose? no", "s", "maybe", "l", "", "q"])
    out = io.StringIO()
    todo = label.select(CANDS, {})
    n = label.label_loop(todo, p, lambda _prompt: next(answers), out, "owner", lambda: "2026-11-22T00:00:00Z",
                         git=fake_git)
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    assert n == 2 and [r["label"] for r in rows] == ["weakening", "legit"]
    assert rows[0]["reason"] == "loosened on purpose? no" and rows[1]["reason"] == ""
    assert rows[0]["key"] == label.key(todo[0]) and rows[1]["key"] == label.key(todo[3])
    assert all(r["labeled_by"] == "owner" and r["labeled_at"] == "2026-11-22T00:00:00Z" for r in rows)
    text = out.getvalue()
    assert "more diff lines" in text and "+line 299" in text  # truncated first, then the full diff on 'f'
    assert "'maybe' is not a choice" in text


def test_main_refuses_non_interactive_input_and_writes_nothing(tmp_path):
    p = tmp_path / "labels.jsonl"
    out = io.StringIO()
    code = label.main(["--data", str(write_data(tmp_path)), "--labels", str(p)], stdin=io.StringIO("w\n"), out=out)
    assert code == 2 and not p.exists()
    assert "interactive terminal" in out.getvalue()


def test_main_labels_from_a_terminal_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(label, "_git", fake_git)
    monkeypatch.setattr(label, "repo_dir", lambda repo: tmp_path)
    data, p = write_data(tmp_path), tmp_path / "labels.jsonl"
    out = io.StringIO()
    assert label.main(["--data", str(data), "--labels", str(p), "--by", "me"], stdin=Tty("w\n\nq\n"), out=out) == 0
    assert len(p.read_text(encoding="utf-8").splitlines()) == 1
    out = io.StringIO()
    label.main(["--data", str(data), "--labels", str(p), "--by", "me"], stdin=Tty("q\n"), out=out)
    assert "4 commit(s) to label (1 labeled so far)" in out.getvalue()


def test_summary_runs_without_any_labels(tmp_path):
    out = io.StringIO()
    assert label.main(["--data", str(write_data(tmp_path)), "--labels", str(tmp_path / "none.jsonl"),
                       "--summary"], out=out) == 0
    assert "not measured until labels exist" in out.getvalue()


def test_the_committed_scan_has_no_labels_and_a_stable_sample():
    cands = label.load_candidates(label.DATA)
    assert len(cands) == 179 and all(c["owner_label"] is None for c in cands)
    a, b = label.select(cands, {}, sample=10), label.select(cands, {}, sample=10)
    assert [label.key(c) for c in a] == [label.key(c) for c in b]
    for r in {r for c in cands for r in c["rules"]}:
        flagged = sum(r in c["rules"] for c in cands)
        assert sum(r in c["rules"] for c in a) >= min(10, flagged), r  # every rule gets its sample


def test_without_a_clone_it_links_to_the_commit_instead_of_a_diff(tmp_path, monkeypatch):
    monkeypatch.setattr(label, "repo_dir", lambda repo: tmp_path / "missing.git")
    out = io.StringIO()
    label.show(CANDS[0], out, git=fake_git)
    text = out.getvalue()
    assert "https://github.com/o/a/commit/" + "1" * 40 in text and "+line" not in text
    assert "H5 [warning] tox.ini" in text
