"""Every report the engine produces anywhere in this test suite is validated against
engine/schema/report.v2.json (PROVECODE_V2_BUILD_BRIEF.md section 3)."""

from __future__ import annotations

import pytest

from provecode import pipeline

from schema_check import errors

_original_run = pipeline.run
VALIDATED = {"count": 0}


def _validating_run(*args, **kwargs):
    report = _original_run(*args, **kwargs)
    problems = errors(report)
    assert not problems, "report does not match engine/schema/report.v2.json:\n" + "\n".join(problems[:20])
    VALIDATED["count"] += 1
    return report


@pytest.fixture(autouse=True, scope="session")
def _validate_every_report():
    pipeline.run = _validating_run
    yield
    pipeline.run = _original_run
