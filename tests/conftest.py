"""Pytest hooks: optional connectivity tests require RUN_CONNECTIVITY=1."""

from __future__ import annotations

import os

import pytest


def _connectivity_enabled() -> bool:
    v = os.environ.get("RUN_CONNECTIVITY", "")
    return v.lower() in ("1", "true", "yes")


def pytest_runtest_setup(item: pytest.Item) -> None:
    marks = {m.name for m in item.iter_markers()}
    if "requires_db" in marks and not _connectivity_enabled():
        pytest.skip("Set RUN_CONNECTIVITY=1 to run requires_db tests")
