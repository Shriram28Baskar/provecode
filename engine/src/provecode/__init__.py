"""PROVECODE: semantic test-weakening detection tied to approved contracts.

The core modules (limits, analyzer, model, contract's JSON path, decide,
report) are standard library only, pure, and never import, eval or execute
analyzed source. They run unchanged in CPython and in Pyodide.
"""

__version__ = "0.2.1"
