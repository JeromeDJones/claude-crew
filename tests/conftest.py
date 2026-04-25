"""Pytest configuration shared across the suite.

Default-stub-mode fixture: existing tests (and most new ones) want
StubTeammate semantics from `make_server()` so they don't accidentally
hit the SDK factory. Tests that need SDK mode opt in by passing
`factory=` explicitly or by clearing the env var inside the test.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _default_stub_teammate_mode(monkeypatch):
    """Force `default_factory()` to return `stub_factory` for every test
    that doesn't override CLAUDE_CREW_TEAMMATE_MODE itself."""
    monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "stub")
    yield
