"""Recorded-run evidence mapping and archive safety (Docker itself is exercised by benchmark.record_demo)."""

import io
import tarfile

import pytest

from provecode import exec as ex


@pytest.mark.parametrize("code, result", [(0, "pass"), (1, "fail"), (2, "error"), (4, "error"), (5, "error"),
                                          (None, "error")])
def test_exit_code_mapping_never_turns_errors_into_passes(code, result):
    assert ex._evidence("E1", "chk", ["C1"], "contract", code, "out", "cmd", 0.1).result == result


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
