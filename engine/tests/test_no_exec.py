"""Architecture section 7.4: the core never imports, evals or executes analyzed source,
and never depends on an LLM SDK or on spike/ code."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "provecode"
CORE = ["analyzer.py", "limits.py", "model.py", "decide.py", "report.py", "contract.py", "pipeline.py"]

FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "literal_eval", "system", "popen", "getattr"}
FORBIDDEN_MODULES = {"importlib", "runpy", "pickle", "marshal", "subprocess", "socket", "ctypes", "os"}
LLM_OR_SPIKE = {"anthropic", "openai", "google", "langchain", "litellm", "provecode_llm", "spike", "cohere", "mistralai"}


def _calls_and_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls, mods = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            # getattr with a constant attribute name (reading AST fields) is harmless;
            # a dynamic attribute name is not allowed.
            if name == "getattr" and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant):
                continue
            calls.add(name)
        elif isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods.add(n.module.split(".")[0])
    return calls, mods


@pytest.mark.parametrize("name", CORE)
def test_core_module_has_no_exec_paths(name):
    calls, mods = _calls_and_imports(SRC / name)
    assert not (calls & FORBIDDEN_CALLS), f"{name} calls {calls & FORBIDDEN_CALLS}"
    assert not (mods & FORBIDDEN_MODULES), f"{name} imports {mods & FORBIDDEN_MODULES}"


@pytest.mark.parametrize("path", sorted(SRC.glob("*.py")), ids=lambda p: p.name)
def test_no_llm_or_spike_imports_anywhere(path):
    _, mods = _calls_and_imports(path)
    assert not (mods & LLM_OR_SPIKE), f"{path.name} imports {mods & LLM_OR_SPIKE}"


def test_core_list_covers_every_stdlib_module():
    host_specific = {"git.py", "cli.py", "exec.py", "guard.py", "__init__.py"}
    present = {p.name for p in SRC.glob("*.py")}
    assert present - host_specific <= set(CORE), present - host_specific - set(CORE)
