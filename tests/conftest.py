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


@pytest.fixture(autouse=True)
def _disable_transcripts(monkeypatch):
    """Default-disable JSONL transcripts in tests so the user's state dir
    isn't polluted with one file per test run. Tests that exercise the
    transcript opt back in by setting CLAUDE_CREW_TRANSCRIPT_DIR to a
    tmp_path *and* unsetting CLAUDE_CREW_TRANSCRIPT_DISABLED."""
    monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", "1")
    yield
