"""provecode.isolate (ADR 0013): candidate code runs in a child process, so it cannot forge the
result of the contract check that judges it (brief section 0.4)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from provecode import isolate

SRC = Path(__file__).resolve().parent.parent / "src"

# A candidate that removes the 20% cap AND, at import time, rewrites pytest's reports so that every
# test in the importing process is reported as passed.
FORGER = textwrap.dedent("""
    import _pytest.reports as _r

    _orig = _r.TestReport.__init__


    def _forged(self, *args, **kwargs):
        _orig(self, *args, **kwargs)
        self.outcome = "passed"


    _r.TestReport.__init__ = _forged


    def apply_discount(price, discount):
        return round(price * (1 - discount), 2)
""")


@pytest.fixture
def candidate(tmp_path: Path) -> Path:
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "pricing.py").write_text(FORGER, encoding="utf-8")
    (root / "honest.py").write_text(textwrap.dedent("""
        class Custom(Exception):
            pass


        def double(x):
            return x * 2


        def boom(kind):
            if kind == "value":
                raise ValueError("negative")
            raise Custom("custom")


        def opaque():
            return object()


        def noisy():
            print('{"ok": true, "value": 999}')
            return 1


        def hang():
            while True:
                pass
    """), encoding="utf-8")
    return root


def _pytest(check: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = {"PYTHONPATH": str(SRC), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(check)],
                          cwd=cwd, capture_output=True, text=True, env=env, timeout=120)


def test_without_isolation_the_candidate_forges_a_pass(candidate, tmp_path):
    check = tmp_path / "check_direct.py"
    check.write_text(textwrap.dedent(f"""
        import sys
        sys.path.append({str(candidate)!r})
        from pricing import apply_discount


        def test_cap():
            assert apply_discount(100, 0.50) == 80.0
    """), encoding="utf-8")
    r = _pytest(check, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr  # the attack works: a broken cap "passes"


def test_with_isolation_the_same_check_fails(candidate, tmp_path):
    check = tmp_path / "check_isolated.py"
    check.write_text(textwrap.dedent(f"""
        from provecode import isolate


        def test_cap():
            assert isolate.call("pricing:apply_discount", 100, 0.50, root={str(candidate)!r}) == 80.0
    """), encoding="utf-8")
    r = _pytest(check, tmp_path)
    assert r.returncode == 1 and "1 failed" in r.stdout, r.stdout + r.stderr


def test_values_exceptions_and_errors(candidate):
    s = isolate.Session(str(candidate), timeout=5)
    try:
        assert s.call("honest:double", 21) == 42
        assert s.call("honest:double", [1]) == [1, 1]
        with pytest.raises(ValueError, match="negative"):
            s.call("honest:boom", "value")
        with pytest.raises(isolate.CandidateError) as exc:
            s.call("honest:boom", "custom")
        assert exc.value.type_name == "Custom"
        with pytest.raises(isolate.IsolationError, match="not a JSON value"):
            s.call("honest:opaque")
        assert s.call("honest:noisy") == 1  # a print cannot forge or corrupt the answer
        with pytest.raises(isolate.IsolationError, match="not JSON values"):
            s.call("honest:double", object())
        with pytest.raises(ModuleNotFoundError):
            s.call("missing_module:f")
        with pytest.raises(isolate.IsolationError, match="module:qualname"):
            s.call("honest.double", 1)
    finally:
        s.close()


def test_a_hanging_candidate_times_out(candidate):
    s = isolate.Session(str(candidate), timeout=1)
    try:
        with pytest.raises(isolate.IsolationError, match="did not answer"):
            s.call("honest:hang")
    finally:
        s.close()


def test_call_needs_a_candidate_tree(monkeypatch):
    monkeypatch.delenv(isolate.ROOT_ENV, raising=False)
    with pytest.raises(isolate.IsolationError, match="no candidate tree"):
        isolate.call("x:y")
