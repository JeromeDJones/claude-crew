"""The crew message broker.

Single source of truth for the bus: teammate registry, append-only message
log, per-recipient inbox queues, monotonic ``seq`` counter, and id-based
dedup. All state mutations happen on the asyncio event loop; no threads.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import Teammate

LEAD_ID = "lead"


class UnknownTeammateError(KeyError):
    """Raised when a teammate id is not registered."""


class TeammateAlreadyDeadError(RuntimeError):
    """Raised when attempting to send to a killed teammate.

    (Currently the broker uses ``UnknownTeammateError`` for both cases
    since killed teammates are removed from the registry. This class is
    reserved for a future state where killed teammates remain registered
    in a tombstoned form.)
    """


@dataclass(frozen=True)
class TeammateInfo:
    id: str
    name: str
    role: str
    spawned_at: float
    alive: bool


# A factory takes (id, name, role) and returns an unstarted Teammate.
TeammateFactory = Callable[[str, str, str], Teammate]


class Broker:
    def __init__(self) -> None:
        self._teammates: dict[str, Teammate] = {}
        self._info: dict[str, TeammateInfo] = {}
        self._inboxes: dict[str, asyncio.Queue] = {LEAD_ID: asyncio.Queue()}
        self._log: list[Envelope] = []
        self._seen_ids: set[str] = set()
        self._next_seq: int = 1

    # ---------- spawn / kill ----------

    async def spawn_teammate(
        self,
        role: str,
        name: str | None,
        factory: TeammateFactory,
    ) -> str:
        teammate_id = f"t-{uuid4().hex[:12]}"
        resolved_name = name if name is not None else role
        inbox: asyncio.Queue = asyncio.Queue()
        teammate = factory(teammate_id, resolved_name, role)
        await teammate.start(self, inbox)

        self._teammates[teammate_id] = teammate
        self._inboxes[teammate_id] = inbox
        self._info[teammate_id] = TeammateInfo(
            id=teammate_id,
            name=resolved_name,
            role=role,
            spawned_at=time.time(),
            alive=True,
        )
        return teammate_id

    async def kill_teammate(self, teammate_id: str) -> None:
        if teammate_id not in self._teammates:
            raise UnknownTeammateError(teammate_id)
        teammate = self._teammates.pop(teammate_id)
        self._info.pop(teammate_id, None)
        # Inbox queue is kept around long enough for shutdown sentinel to drain.
        try:
            await teammate.shutdown()
        finally:
            self._inboxes.pop(teammate_id, None)

    async def shutdown_all(self) -> None:
        for tid in list(self._teammates.keys()):
            try:
                await self.kill_teammate(tid)
            except Exception:
                pass

    # ---------- send / broadcast ----------

    async def send(self, env: Envelope) -> Envelope | None:
        """Enqueue ``env`` for delivery.

        Returns the envelope as enqueued (with broker-assigned seq), or
        ``None`` if it was dropped as a duplicate.
        """
        if env.id in self._seen_ids:
            return None
        if env.recipient != LEAD_ID and env.recipient not in self._teammates:
            raise UnknownTeammateError(env.recipient)

        seq = self._next_seq
        self._next_seq += 1
        stamped = Envelope(
            id=env.id,
            seq=seq,
            sender=env.sender,
            recipient=env.recipient,
            timestamp=env.timestamp if env.timestamp else time.time(),
            payload=env.payload,
        )
        self._seen_ids.add(stamped.id)
        self._log.append(stamped)
        await self._inboxes[stamped.recipient].put(stamped)
        return stamped

    async def broadcast(
        self,
        sender: str,
        payload: Any,
        id: str | None = None,
    ) -> list[str]:
        """Fan-out one envelope per teammate (sender excluded). Returns message ids."""
        recipients = [tid for tid in self._teammates.keys() if tid != sender]
        out_ids: list[str] = []
        for rid in recipients:
            mid = id if id is not None else new_message_id()
            # If a single id was supplied for broadcast, only the first
            # recipient receives it; subsequent are deduped. Generate
            # fresh ids per-recipient unless caller intends dedup.
            if id is not None:
                # Per-recipient id derivation keeps each delivery distinct
                # while preserving the "supplied root id" for tracing.
                mid = f"{id}:{rid}"
            env = Envelope(
                id=mid,
                seq=0,
                sender=sender,
                recipient=rid,
                timestamp=time.time(),
                payload=payload,
            )
            stamped = await self.send(env)
            if stamped is not None:
                out_ids.append(stamped.id)
        return out_ids

    # ---------- reads ----------

    def get_messages(
        self,
        recipient: str,
        since_seq: int = 0,
        limit: int | None = None,
    ) -> list[Envelope]:
        result = [m for m in self._log if m.recipient == recipient and m.seq > since_seq]
        if limit is not None:
            result = result[:limit]
        return result

    def list_crew(self) -> list[TeammateInfo]:
        return list(self._info.values())
