"""Teammate ABC and built-in implementations.

The Teammate ABC is the seam where Feature #2 plugs in: ``SdkTeammate``
will implement this same interface around a ``ClaudeSDKClient`` so the
broker and MCP server stay constant.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from claude_crew.envelope import Envelope, new_message_id

if TYPE_CHECKING:
    from claude_crew.broker import Broker


class Teammate(ABC):
    id: str
    name: str
    role: str

    @abstractmethod
    async def start(self, broker: Broker, inbox: asyncio.Queue) -> None:
        """Begin consuming from ``inbox``. Must spawn a background task and return."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Stop consuming and release resources. Must be idempotent."""


# Sentinel placed in an inbox queue to signal shutdown to the consumer task.
_SHUTDOWN_SENTINEL: object = object()


class StubTeammate(Teammate):
    """Echoes inbound messages back to the sender.

    For each received envelope, sends a response of the form
    ``{"echo": <original_payload>, "from": <role>}`` back to the original
    sender. This is enough to validate the bus protocol end-to-end before
    real Agent-SDK teammates exist.
    """

    def __init__(self, id: str, name: str, role: str) -> None:
        self.id = id
        self.name = name
        self.role = role
        self._task: asyncio.Task[None] | None = None
        self._broker: Broker | None = None
        self._inbox: asyncio.Queue | None = None

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

    async def shutdown(self) -> None:
        if self._inbox is not None:
            await self._inbox.put(_SHUTDOWN_SENTINEL)
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
