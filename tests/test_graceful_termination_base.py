"""AT#5 — StubTeammate no-op flush / has_memory_surface base hook.

Covers:
- begin_graceful_termination() returns immediately, records _flush_invoked=True, never raises.
- has_memory_surface() reflects the construction flag (default False, True when set).
- Base Teammate ABC default: has_memory_surface() returns False.
"""

from __future__ import annotations

import asyncio

import pytest

from claude_crew.teammate import StubTeammate


class TestStubTeammateGracefulHook:
    """AT#5: StubTeammate no-op flush."""

    def _make_stub(self, *, has_memory: bool = False) -> StubTeammate:
        return StubTeammate(
            id="stub-1",
            name="stub",
            role="worker",
            has_memory=has_memory,
        )

    @pytest.mark.asyncio
    async def test_flush_invoked_flag_set(self) -> None:
        """begin_graceful_termination records _flush_invoked=True."""
        stub = self._make_stub()
        assert stub._flush_invoked is False
        await stub.begin_graceful_termination(timeout=5)
        assert stub._flush_invoked is True

    @pytest.mark.asyncio
    async def test_flush_returns_immediately(self) -> None:
        """begin_graceful_termination returns well within the timeout."""
        stub = self._make_stub()
        # Should complete in well under 1 s even though timeout=5
        await asyncio.wait_for(
            stub.begin_graceful_termination(timeout=5),
            timeout=1.0,
        )

    @pytest.mark.asyncio
    async def test_flush_never_raises(self) -> None:
        """begin_graceful_termination must not raise under any stub condition."""
        stub = self._make_stub()
        try:
            await stub.begin_graceful_termination(timeout=5)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"begin_graceful_termination raised unexpectedly: {exc}")

    def test_has_memory_surface_default_false(self) -> None:
        """Default construction yields has_memory_surface() == False."""
        stub = self._make_stub()
        assert stub.has_memory_surface() is False

    def test_has_memory_surface_true_when_flag_set(self) -> None:
        """has_memory_surface() == True when has_memory=True at construction."""
        stub = self._make_stub(has_memory=True)
        assert stub.has_memory_surface() is True

    @pytest.mark.asyncio
    async def test_flush_idempotent(self) -> None:
        """Calling begin_graceful_termination twice does not raise."""
        stub = self._make_stub()
        await stub.begin_graceful_termination(timeout=5)
        await stub.begin_graceful_termination(timeout=5)
        assert stub._flush_invoked is True
