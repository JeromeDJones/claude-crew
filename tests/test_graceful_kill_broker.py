"""AT#6-10, 12, 13 — Broker graceful-kill orchestration tests.

Covers:
AT#6  — graceful kill happy path: flush invoked before tombstone, telemetry intact.
AT#7  — auto-skip when has_memory_surface() is False.
AT#8  — graceful=False skips flush entirely.
AT#9  — flush timeout / sad path: hanging flush times out, tombstone still runs.
AT#10 — terminating-window bounce: sends during flush window raise TeammateAlreadyDeadError.
AT#12 — death never flushes: _handle_teammate_death routes straight to _tombstone_teammate.
AT#13 — double kill idempotent: second kill raises TeammateAlreadyDeadError; no second flush.

Plan-review NOTE (MEDIUM-01): AT#10 uses a _HoldingTeammate whose begin_graceful_termination
awaits a controllable asyncio.Event so the flush window has a non-zero width and the bounce
assertion is real.  Construction is inline in the test class per the spec note.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from claude_crew.broker import (
    Broker,
    LEAD_ID,
    TeammateAlreadyDeadError,
    UnknownTeammateError,
)
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import StubTeammate


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def _with_memory_factory(id: str, name: str, role: str, **kw) -> StubTeammate:
    """StubTeammate with has_memory_surface() == True."""
    return StubTeammate(id=id, name=name, role=role, has_memory=True)


def _no_memory_factory(id: str, name: str, role: str, **kw) -> StubTeammate:
    """StubTeammate with has_memory_surface() == False (default)."""
    return StubTeammate(id=id, name=name, role=role, has_memory=False)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def broker() -> Broker:
    b = Broker()
    yield b
    await b.shutdown_all()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBrokerGracefulKill:
    """AT#6-10, 12, 13: Broker-level graceful kill orchestration."""

    # ------------------------------------------------------------------
    # AT#6 — graceful kill happy path
    # ------------------------------------------------------------------

    async def test_graceful_kill_invokes_flush_before_tombstone(
        self, broker: Broker
    ) -> None:
        """AT#6: begin_graceful_termination called exactly once BEFORE tombstone.

        Verifies flush was invoked, and afterward the teammate is tombstoned with
        telemetry snapshot intact (alive=False, died_at_wallclock set, exit_code=None).
        """
        before = time.time()
        tid = await broker.spawn_teammate(
            role="worker", name=None, factory=_with_memory_factory
        )
        # Capture reference to teammate object before tombstone moves it to _dead_teammates
        tm: StubTeammate = broker._teammates[tid]  # type: ignore[index]

        await broker.kill_teammate(tid, graceful=True)
        after = time.time()

        # Flush was invoked
        assert tm._flush_invoked is True, "begin_graceful_termination should have been called"

        # Teammate is tombstoned
        crew = broker.list_crew()
        assert len(crew) == 1
        info = crew[0]
        assert info.alive is False
        assert info.died_at_wallclock is not None
        assert before <= info.died_at_wallclock <= after
        assert info.exit_code is None  # explicit kill → no exit code

    async def test_graceful_kill_default_is_graceful(self, broker: Broker) -> None:
        """AT#6: kill_teammate with no args defaults to graceful=True."""
        tid = await broker.spawn_teammate(
            role="worker", name=None, factory=_with_memory_factory
        )
        tm: StubTeammate = broker._teammates[tid]  # type: ignore[index]

        # Default call — should be graceful
        await broker.kill_teammate(tid)

        assert tm._flush_invoked is True
        assert broker.list_crew()[0].alive is False

    # ------------------------------------------------------------------
    # AT#7 — auto-skip when no memory surface
    # ------------------------------------------------------------------

    async def test_auto_skip_when_no_memory_surface(self, broker: Broker) -> None:
        """AT#7: begin_graceful_termination NOT invoked when has_memory_surface() is False."""
        tid = await broker.spawn_teammate(
            role="worker", name=None, factory=_no_memory_factory
        )
        tm: StubTeammate = broker._teammates[tid]  # type: ignore[index]

        await broker.kill_teammate(tid, graceful=True)

        # Flush must NOT have been invoked
        assert tm._flush_invoked is False, "no flush expected for memory-less teammate"
        # Teammate still tombstoned (hard kill)
        assert broker.list_crew()[0].alive is False

    # ------------------------------------------------------------------
    # AT#8 — graceful=False skips flush
    # ------------------------------------------------------------------

    async def test_graceful_false_hard_kills_immediately(self, broker: Broker) -> None:
        """AT#8: graceful=False → no flush even when has_memory_surface() is True."""
        tid = await broker.spawn_teammate(
            role="worker", name=None, factory=_with_memory_factory
        )
        tm: StubTeammate = broker._teammates[tid]  # type: ignore[index]

        await broker.kill_teammate(tid, graceful=False)

        assert tm._flush_invoked is False, "graceful=False must skip flush"
        assert broker.list_crew()[0].alive is False

    # ------------------------------------------------------------------
    # AT#9 — flush timeout / sad path
    # ------------------------------------------------------------------

    async def test_flush_timeout_falls_through_to_tombstone(
        self, broker: Broker
    ) -> None:
        """AT#9: hanging flush times out; broker proceeds to tombstone; no exception escapes.

        Teammate double: _HangingTeammate sleeps indefinitely inside
        begin_graceful_termination; broker wraps in asyncio.wait_for with
        flush_timeout=0.2 and must return within a small bound.
        """

        class _HangingTeammate(StubTeammate):
            """begin_graceful_termination blocks forever — exercises timeout path."""

            async def begin_graceful_termination(self, *, timeout: float) -> None:
                self._flush_invoked = True
                await asyncio.sleep(999.0)  # intentionally hangs

        hanging_ref: list[_HangingTeammate] = []

        def hanging_factory(id: str, name: str, role: str, **kw) -> _HangingTeammate:
            tm = _HangingTeammate(id=id, name=name, role=role, has_memory=True)
            hanging_ref.append(tm)
            return tm

        tid = await broker.spawn_teammate(
            role="worker", name=None, factory=hanging_factory
        )

        t0 = time.monotonic()
        # Must not raise; must return within timeout + reasonable headroom
        await broker.kill_teammate(tid, graceful=True, flush_timeout=0.2)
        elapsed = time.monotonic() - t0

        assert elapsed < 2.0, (
            f"kill_teammate took {elapsed:.2f}s; expected < 2.0s after 0.2s timeout"
        )

        # Teammate tombstoned despite timeout
        info = broker.list_crew()[0]
        assert info.alive is False
        assert info.died_at_wallclock is not None

    # ------------------------------------------------------------------
    # AT#10 — terminating-window bounce
    # ------------------------------------------------------------------

    async def test_terminating_window_bounces_sends(self) -> None:
        """AT#10: sends to a terminating teammate are bounced as TeammateAlreadyDeadError.

        Teammate double (_HoldingTeammate): begin_graceful_termination sets
        flush_started and then awaits flush_released — giving the test a
        controllable non-zero flush window to send into.  The double is
        constructed inline here per the spec MEDIUM-01 note.
        """
        b = Broker()
        flush_started: asyncio.Event = asyncio.Event()
        flush_released: asyncio.Event = asyncio.Event()

        class _HoldingTeammate(StubTeammate):
            """Holds the flush window open until flush_released is set."""

            async def begin_graceful_termination(self, *, timeout: float) -> None:
                self._flush_invoked = True
                flush_started.set()
                # Block until released (or timeout fires as safety net)
                await asyncio.wait_for(flush_released.wait(), timeout=timeout)

        holder_ref: list[_HoldingTeammate] = []

        def holding_factory(id: str, name: str, role: str, **kw) -> _HoldingTeammate:
            tm = _HoldingTeammate(id=id, name=name, role=role, has_memory=True)
            holder_ref.append(tm)
            return tm

        tid = await b.spawn_teammate(role="worker", name=None, factory=holding_factory)

        # Start kill in a background task; use large timeout so broker doesn't
        # cancel the hold before the test can inject the send.
        kill_task = asyncio.create_task(
            b.kill_teammate(tid, graceful=True, flush_timeout=30.0)
        )

        # Wait for the flush window to open
        await asyncio.wait_for(flush_started.wait(), timeout=2.0)

        # In the terminating window: send should bounce as TeammateAlreadyDeadError
        env = Envelope(
            id=new_message_id(),
            seq=0,
            sender=LEAD_ID,
            recipient=tid,
            timestamp=0.0,
            payload={"interloper": True},
        )
        with pytest.raises(TeammateAlreadyDeadError):
            await b.send(env)

        # Release the flush and let kill complete
        flush_released.set()
        await asyncio.wait_for(kill_task, timeout=2.0)

        # Teammate tombstoned after flush completes
        assert b.list_crew()[0].alive is False

        await b.shutdown_all()

    # ------------------------------------------------------------------
    # AT#12 — death never flushes
    # ------------------------------------------------------------------

    async def test_death_never_invokes_flush(self, broker: Broker) -> None:
        """AT#12: _handle_teammate_death → _tombstone_teammate directly; no flush.

        Verifies begin_graceful_termination is NOT called and the teammate is
        tombstoned with exit_code set (death path, not explicit-kill path).
        """
        tid = await broker.spawn_teammate(
            role="worker", name=None, factory=_with_memory_factory
        )
        tm: StubTeammate = broker._teammates[tid]  # type: ignore[index]

        # Simulate unexpected subprocess death
        await broker._handle_teammate_death(tid, exit_code=1)

        # Flush must NOT have been invoked
        assert tm._flush_invoked is False, (
            "_handle_teammate_death must never call begin_graceful_termination"
        )

        # Tombstoned via death path: exit_code preserved
        crew = broker.list_crew()
        assert len(crew) == 1
        info = crew[0]
        assert info.alive is False
        assert info.exit_code == 1

    # ------------------------------------------------------------------
    # AT#13 — double kill idempotent
    # ------------------------------------------------------------------

    async def test_double_kill_raises_already_dead(self, broker: Broker) -> None:
        """AT#13: second kill raises TeammateAlreadyDeadError; no second flush."""
        tid = await broker.spawn_teammate(
            role="worker", name=None, factory=_with_memory_factory
        )
        tm: StubTeammate = broker._teammates[tid]  # type: ignore[index]

        # First kill succeeds and flushes
        await broker.kill_teammate(tid, graceful=True)
        assert tm._flush_invoked is True

        # Reset to detect a spurious second flush
        tm._flush_invoked = False

        # Second kill must raise immediately with no flush
        with pytest.raises(TeammateAlreadyDeadError):
            await broker.kill_teammate(tid, graceful=True)

        assert tm._flush_invoked is False, "second kill must not invoke a second flush"

    # ------------------------------------------------------------------
    # Additional: terminating set is cleared after tombstone
    # ------------------------------------------------------------------

    async def test_terminating_set_empty_after_kill(self, broker: Broker) -> None:
        """_terminating entry is removed before/during tombstone (no leak)."""
        tid = await broker.spawn_teammate(
            role="worker", name=None, factory=_with_memory_factory
        )

        await broker.kill_teammate(tid, graceful=True)

        assert tid not in broker._terminating, (  # type: ignore[attr-defined]
            "_terminating must not retain id after tombstone"
        )
