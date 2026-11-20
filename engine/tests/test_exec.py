"""Recorded-run evidence rules and archive safety. Docker itself is exercised by
benchmark.record_demo and by test_exec_docker below when PROVECODE_DOCKER=1."""

import io
import os
import tarfile
from pathlib import Path

import pytest

from provecode import exec as ex

OK = {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}
FAILED = {"tests": 1, "failures": 1, "errors": 0, "skipped": 0}


@pytest.mark.parametrize("code, junit, result", [
    (0, OK, "pass"),
    (1, FAILED, "fail"),
    (0, None, "error"),                                                     # exit 0 but no report: never a pass
    (0, {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}, "error"),   # nothing ran
    (0, {"tests": 2, "failures": 0, "errors": 0, "skipped": 2}, "error"),   # everything skipped
    (1, None, "error"),
    (2, OK, "error"),
    (5, None, "error"),
    (None, None, "error"),                                                  # timeout
])
def test_pass_requires_positive_evidence(code, junit, result):
    assert ex.classify(code, junit) == result


def test_exit_zero_at_import_without_report_is_not_a_pass():
    # The review's attack: candidate code calls os._exit(0) on import, so pytest never writes a report.
    ev = ex._evidence("E1", "chk-cap", ["C1"], "contract", 0, "", "cmd", 0.1, None)
    assert ev.result == "error"
    assert "no valid JUnit report" in ev.output_excerpt


def test_junit_summary_parses_pytest_reports(tmp_path):
    p = tmp_path / "r.xml"
    p.write_text('<testsuites><testsuite tests="3" failures="1" errors="0" skipped="1"/></testsuites>')
    assert ex.junit_summary(p) == {"tests": 3, "failures": 1, "errors": 0, "skipped": 1}
    p.write_text("<not-xml")
    assert ex.junit_summary(p) is None
    assert ex.junit_summary(tmp_path / "missing.xml") is None


def test_runner_isolates_and_appends_candidate_path():
    assert "sys.path.append('/work/src')" in ex.RUNNER
    assert "--junitxml=" in ex.RUNNER


def test_unsafe_archive_member_is_refused(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))

    class Out:
        returncode = 0
        stdout = buf.getvalue()
        stderr = b""

    monkeypatch.setattr(ex.subprocess, "run", lambda *a, **k: Out())
    with pytest.raises(RuntimeError, match="refusing"):
        ex._export_tree(".", "HEAD", tmp_path)
    assert not (tmp_path.parent / "escape.txt").exists()
