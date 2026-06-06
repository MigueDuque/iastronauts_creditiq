"""
Pytest entry point for the eval harness — one parametrized test per golden case,
so `pytest` (and CI) fails when an engine change regresses a recorded case.

To work on cases interactively use the richer CLI instead:
    python -m tests.eval.runner -v
    python -m tests.eval.runner --update      # after an intentional change
"""
import json
from pathlib import Path

import pytest

from tests.eval.runner import CASES_DIR, _run_case

_CASE_DIRS = sorted(
    d for d in CASES_DIR.iterdir() if d.is_dir() and (d / "meta.json").exists()
) if CASES_DIR.exists() else []


@pytest.mark.parametrize("case_dir", _CASE_DIRS, ids=[d.name for d in _CASE_DIRS])
def test_eval_case(case_dir: Path):
    res = _run_case(case_dir, update=False)
    assert res.error is None, f"{case_dir.name}: {res.error}"
    diffs = [
        f"{m}: expected {e!r} got {a!r}"
        for m, e, a, ok in res.rows if not ok
    ]
    assert not diffs, "\n".join(diffs)
