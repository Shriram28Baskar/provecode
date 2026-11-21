"""Run candidate code in a separate process, for contract-owned checks (ADR 0013).

A contract check imports this module, never the candidate:

    from provecode import isolate
    assert isolate.call("checkout.pricing:apply_discount", 100, 0.50) == 80.0

The check runs in the trusted pytest process. Each call goes, as one JSON line, to a child
`python -I` process that has the candidate tree on its sys.path, imports the target there, calls
it, and answers with one JSON line. Whatever the candidate does at import or call time (patching
pytest, _pytest.reports, builtins, the assertion rewriter) happens in the child and cannot reach
the process that decides whether the check passed.

Limits, stated plainly:
- Arguments and return values must be JSON values (tuples arrive as lists). Anything else is an
  IsolationError, never a silent pass.
- Side effects inside the candidate (files, globals, mutated arguments) are not observable here.
- This separates processes; it is not a sandbox. The container is the sandbox.
- A built-in exception raised by the candidate is re-raised here with the same built-in type, so
  `pytest.raises(ValueError)` works; any other exception type becomes CandidateError with the
  remote type name.
"""

from __future__ import annotations

import builtins
import json
import os
import queue
import subprocess
import sys
import threading

ROOT_ENV = "PROVECODE_CANDIDATE_ROOT"
MAX_LINE = 1_000_000  # bytes of one response line
DEFAULT_TIMEOUT = 30.0

# The child: keep the real stdout for the protocol, then point fd 1 at stderr so anything the
# candidate prints (even from C code) cannot corrupt or forge a protocol line by accident.
_CHILD = r'''
import importlib, json, os, sys
proto = os.fdopen(os.dup(1), "w", encoding="utf-8")
os.dup2(2, 1)
sys.stdout = sys.stderr
sys.path.append(sys.argv[1])
for line in sys.stdin:
    try:
        req = json.loads(line)
        mod, _, qual = req["target"].partition(":")
        obj = importlib.import_module(mod)
        for part in qual.split("."):
            obj = getattr(obj, part)
        value = obj(*req["args"], **req["kwargs"])
        try:
            out = json.dumps({"ok": True, "value": value}, allow_nan=False)
        except (TypeError, ValueError) as exc:
            out = json.dumps({"ok": False, "kind": "not_serializable", "type": type(value).__name__,
                              "message": str(exc)[:500]})
    except BaseException as exc:
        t = type(exc)
        out = json.dumps({"ok": False, "kind": "raised", "type": t.__name__,
                          "module": getattr(t, "__module__", ""), "message": str(exc)[:2000]})
    proto.write(out + "\n")
    proto.flush()
'''


class IsolationError(Exception):
    """The isolated call could not produce a trustworthy answer (crash, timeout, bad value)."""


class CandidateError(Exception):
    """The candidate raised an exception whose type is not a Python built-in."""

    def __init__(self, type_name: str, module: str, message: str):
        super().__init__(f"{module}.{type_name}: {message}" if module else f"{type_name}: {message}")
        self.type_name, self.module, self.message = type_name, module, message


_BUILTIN_EXC = {n: t for n, t in vars(builtins).items() if isinstance(t, type) and issubclass(t, BaseException)}


class Session:
    """One child process serving calls into one candidate tree."""

    def __init__(self, root: str, timeout: float = DEFAULT_TIMEOUT):
        self.root, self.timeout = os.path.abspath(root), timeout
        self.proc = subprocess.Popen([sys.executable, "-I", "-c", _CHILD, self.root], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.root)
        self._lines: queue.Queue = queue.Queue()
        self._stderr: list[bytes] = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain, daemon=True).start()

    def _read(self) -> None:
        for line in iter(self.proc.stdout.readline, b""):
            self._lines.put(line)
        self._lines.put(None)

    def _drain(self) -> None:
        for line in iter(self.proc.stderr.readline, b""):
            if len(self._stderr) < 200:
                self._stderr.append(line)

    def stderr_tail(self) -> str:
        return b"".join(self._stderr[-20:]).decode("utf-8", "replace")

    def call(self, target: str, *args, **kwargs):
        if ":" not in target:
            raise IsolationError(f"target must be 'module:qualname', got {target!r}")
        try:
            req = json.dumps({"target": target, "args": list(args), "kwargs": kwargs}, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise IsolationError(f"arguments are not JSON values: {exc}") from None
        if self.proc.poll() is not None:
            raise IsolationError(f"the candidate process has exited ({self.proc.returncode}); "
                                 f"stderr: {self.stderr_tail()}")
        try:
            self.proc.stdin.write(req.encode("utf-8") + b"\n")
            self.proc.stdin.flush()
        except OSError as exc:
            raise IsolationError(f"cannot reach the candidate process: {exc}") from None
        try:
            line = self._lines.get(timeout=self.timeout)
        except queue.Empty:
            self.close()
            raise IsolationError(f"{target} did not answer within {self.timeout:g}s") from None
        if line is None:
            raise IsolationError(f"the candidate process exited during {target}; stderr: {self.stderr_tail()}")
        if len(line) > MAX_LINE:
            raise IsolationError(f"{target} answered with more than {MAX_LINE} bytes")
        try:
            resp = json.loads(line)
        except json.JSONDecodeError:
            raise IsolationError(f"{target} answered with a malformed line") from None
        if not isinstance(resp, dict) or not isinstance(resp.get("ok"), bool):
            raise IsolationError(f"{target} answered with an unexpected shape")
        if resp["ok"]:
            return resp.get("value")
        if resp.get("kind") == "not_serializable":
            raise IsolationError(f"{target} returned a {resp.get('type')}, which is not a JSON value; "
                                 "contract checks can only compare JSON values")
        name, module, msg = str(resp.get("type", "")), str(resp.get("module", "")), str(resp.get("message", ""))
        exc_type = _BUILTIN_EXC.get(name) if module == "builtins" else None
        if exc_type is not None and exc_type not in (SystemExit, KeyboardInterrupt, GeneratorExit):
            raise exc_type(msg)
        raise CandidateError(name, module, msg)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


_sessions: dict[str, Session] = {}


def call(target: str, *args, root: str | None = None, **kwargs):
    """Call module:qualname in the candidate tree (root, or $PROVECODE_CANDIDATE_ROOT)."""
    root = root or os.environ.get(ROOT_ENV)
    if not root:
        raise IsolationError(f"no candidate tree: pass root= or set {ROOT_ENV}")
    s = _sessions.get(root)
    if s is None or s.proc.poll() is not None:
        s = _sessions[root] = Session(root)
    return s.call(target, *args, **kwargs)
