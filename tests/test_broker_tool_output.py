"""Unit tests for Broker.get_tool_output (broker-lookup task).

Covers:
- live hit: known teammate + known tool_use_id → body
- miss: unknown teammate_id → None
- miss: known teammate, evicted (unknown) tool_use_id → None
- tombstoned teammate lookup still succeeds
"""

from __future__ import annotations

import asyncio
import collections
import time
from typing import Any

import pytest

from claude_crew.broker import Broker
from claude_crew.envelope import Envelope
from claude_crew.teammate import Teammate, _tool_events_maxlen


# ---------------------------------------------------------------------------
# Minimal no-op teammate compatible with Broker.spawn_teammate
# ---------------------------------------------------------------------------

_SENTINEL: object = object()


class _MinimalTeammate(Teammate):
    """Lightweight stand-in — drains inbox; exposes _tool_outputs."""

    def __init__(self, id: str, name: str, role: str, **_kwargs: Any) -> None:
        self.id = id
        self.name = name
        self.role = role
        self._inbox: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self._broker = None
        # Activity fields required by status_snapshot()
        self._last_activity_monotonic = time.monotonic()
        self._last_activity_wallclock = time.time()
        self._current_turn_started_at_wallclock: float | None = None
        # Tool tracking
        self._tool_uses: dict = {}
        self._recently_closed_tool_use_ids: collections.deque = collections.deque(maxlen=64)
        self._last_tool_completed = None
        # F19 completed tool-events deque
        self._completed_tool_events: collections.deque = collections.deque(
            maxlen=_tool_events_maxlen()
        )
        # Tool output store (added by tool-output-store task)
        self._tool_outputs: collections.OrderedDict[str, str] = collections.OrderedDict()

    async def start(self, broker: "Broker", inbox: asyncio.Queue) -> None:
        self._broker = broker
        self._inbox = inbox
        self._task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        assert self._inbox is not None
        while True:
            msg = await self._inbox.get()
            if msg is _SENTINEL:
                return

    async def shutdown(self) -> None:
        if self._inbox is not None:
            await self._inbox.put(_SENTINEL)
        if self._task is not None:
            await self._task


def _factory(id: str, name: str, role: str, **kwargs: Any) -> _MinimalTeammate:
    return _MinimalTeammate(id=id, name=name, role=role)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def broker() -> Broker:
    b = Broker()
    yield b
    await b.shutdown_all()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBrokerGetToolOutput:
    async def test_unknown_teammate_returns_none(self, broker: Broker) -> None:
        """Miss path 1: teammate_id not in registry → None."""
        result = broker.get_tool_output("t-nonexistent", "toolu_abc")
        assert result is None

    async def test_evicted_tool_use_id_returns_none(self, broker: Broker) -> None:
        """Miss path 2: teammate known but tool_use_id not in their store → None."""
        tid = await broker.spawn_teammate("role", "name", _factory)
        result = broker.get_tool_output(tid, "toolu_not_stored")
        assert result is None

    async def test_live_hit_returns_body(self, broker: Broker) -> None:
        """Live hit: stored output is returned verbatim."""
        tid = await broker.spawn_teammate("role", "name", _factory)
        # Directly seed the teammate's store (simulates what store_tool_output does)
        tm = broker._teammates[tid]
        tm.store_tool_output("toolu_xyz", "hello world")

        result = broker.get_tool_output(tid, "toolu_xyz")
        assert result == "hello world"

    async def test_tombstoned_teammate_hit_returns_body(self, broker: Broker) -> None:
        """Tombstoned (dead) teammate: lookup still succeeds via _dead_teammates."""
        tid = await broker.spawn_teammate("role", "name", _factory)
        tm = broker._teammates[tid]
        tm.store_tool_output("toolu_dead", "output after death")

        # Kill the teammate so it's tombstoned
        await broker.kill_teammate(tid)

        # Teammate should no longer be in live registry
        assert tid not in broker._teammates
        # But lookup should still work via _dead_teammates
        result = broker.get_tool_output(tid, "toolu_dead")
        assert result == "output after death"

    async def test_tombstoned_evicted_returns_none(self, broker: Broker) -> None:
        """Tombstoned teammate, tool_use_id not in store → None (not exception)."""
        tid = await broker.spawn_teammate("role", "name", _factory)
        await broker.kill_teammate(tid)

        result = broker.get_tool_output(tid, "toolu_never_stored")
        assert result is None
