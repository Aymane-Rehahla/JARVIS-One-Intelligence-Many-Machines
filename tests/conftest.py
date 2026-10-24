"""Shared pytest setup.

Tests marked `deferred` could disturb the Phase 5 training (plan ground rule 1). They are written now, listed in
tests/DEFERRED.md with their exact command, and run only when JARVIS_RUN_DEFERRED=1.
"""
from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("JARVIS_RUN_DEFERRED") == "1":
        return
    skip = pytest.mark.skip(reason="deferred until the Phase 5 training ends; see tests/DEFERRED.md")
    for item in items:
        if "deferred" in item.keywords:
            item.add_marker(skip)
