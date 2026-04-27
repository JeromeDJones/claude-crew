"""Teammate ABC and built-in implementations.

The Teammate ABC is the seam where Feature #2 plugs in: ``SdkTeammate``
will implement this same interface around a ``ClaudeSDKClient`` so the
broker and MCP server stay constant.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from claude_crew.envelope import Envelope, new_message_id

if TYPE_CHECKING:
    from claude_crew.broker import Broker


class Teammate(ABC):
    id: str
    name: str
    role: str
    _last_activity_monotonic: float
    _last_activity_wallclock: float
    _current_turn_started_at_wallclock: float | None

    @abstractmethod
    async def start(self, broker: Broker, inbox: asyncio.Queue) -> None:
        """Begin consuming from ``inbox``. Must spawn a background task and return."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Stop consuming and release resources. Must be idempotent."""

    def _stamp_activity(self) -> None:
        """Update activity timestamps to now."""
        self._last_activity_monotonic = time.monotonic()
        self._last_activity_wallclock = time.time()

    def _begin_turn(self) -> None:
        """Mark the start of a turn: set current_turn_started_at and stamp activity."""
        self._current_turn_started_at_wallclock = time.time()
        self._stamp_activity()

    def _end_turn(self) -> None:
        """Mark the end of a turn: clear current_turn_started_at."""
        self._current_turn_started_at_wallclock = None

    def status_snapshot(self) -> dict[str, Any]:
        """Read-only snapshot of activity telemetry for get_teammate_status.

        Returns:
            dict with 'last_activity_at_wallclock', 'current_turn_started_at_wallclock',
            and 'idle_seconds' (computed from monotonic clock).
        """
        return {
            "last_activity_at_wallclock": self._last_activity_wallclock,
            "current_turn_started_at_wallclock": self._current_turn_started_at_wallclock,
            "idle_seconds": time.monotonic() - self._last_activity_monotonic,
        }


# Sentinel placed in an inbox queue to signal shutdown to the consumer task.
_SHUTDOWN_SENTINEL: object = object()


class StubTeammate(Teammate):
    """Echoes inbound messages back to the sender.

    For each received envelope, sends a response of the form
    ``{"echo": <original_payload>, "from": <role>}`` back to the original
    sender. This is enough to validate the bus protocol end-to-end before
    real Agent-SDK teammates exist.
    """

    def __init__(self, id: str, name: str, role: str, slow_echo_delay: float = 0.0) -> None:
        self.id = id
        self.name = name
        self.role = role
        self._task: asyncio.Task[None] | None = None
        self._broker: Broker | None = None
        self._inbox: asyncio.Queue | None = None
        self._slow_echo_delay = slow_echo_delay
        # Initialize activity telemetry (base class fields)
        self._last_activity_monotonic = time.monotonic()
        self._last_activity_wallclock = time.time()
        self._current_turn_started_at_wallclock: float | None = None

    async def start(self, broker: Broker, inbox: asyncio.Queue) -> None:
        self._broker = broker
        self._inbox = inbox
        self._task = asyncio.create_task(self._run(), name=f"stub-{self.id}")

    async def _run(self) -> None:
        assert self._broker is not None and self._inbox is not None
        while True:
            msg = await self._inbox.get()
            if msg is _SHUTDOWN_SENTINEL:
                return
            assert isinstance(msg, Envelope)
            # Begin turn: set current_turn_started_at and stamp activity
            self._begin_turn()
            # Simulate optional delay before responding
            if self._slow_echo_delay > 0.0:
                await asyncio.sleep(self._slow_echo_delay)
            response = Envelope(
                id=new_message_id(),
                seq=0,  # broker assigns
                sender=self.id,
                recipient=msg.sender,
                timestamp=time.time(),
                payload={"echo": msg.payload, "from": self.role},
            )
            try:
                await self._broker.send(response)
            except Exception:
                # If the recipient is gone (e.g., killed mid-flight),
                # drop silently. The broker is the source of truth.
                pass
            finally:
                # End turn: clear current_turn_started_at
                self._end_turn()

    async def shutdown(self) -> None:
        if self._inbox is not None:
            await self._inbox.put(_SHUTDOWN_SENTINEL)
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
