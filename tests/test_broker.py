"""Implementation-level tests for the Broker.

The broker is exercised in isolation here. Teammates are stand-in mocks that
do nothing; the broker should not need a working teammate to validate its
own contract.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from claude_crew.broker import (
    Broker,
    LEAD_ID,
    TeammateAlreadyDeadError,
    UnknownTeammateError,
)
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import Teammate


class _NoopTeammate(Teammate):
    """A teammate that does nothing — drains its inbox into a list."""

    def __init__(self, id: str, name: str, role: str) -> None:
        self.id = id
        self.name = name
        self.role = role
        self.received: list[Envelope] = []
        self._task: asyncio.Task[None] | None = None
        self._inbox: asyncio.Queue[Envelope | object] | None = None
        self._stopped = asyncio.Event()

    async def start(self, broker: Broker, inbox: asyncio.Queue) -> None:
        self._inbox = inbox
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        assert self._inbox is not None
        try:
            while True:
                msg = await self._inbox.get()
                if msg is _SENTINEL:
                    return
                assert isinstance(msg, Envelope)
                self.received.append(msg)
        finally:
            self._stopped.set()

    async def shutdown(self) -> None:
        if self._inbox is not None:
            await self._inbox.put(_SENTINEL)
        if self._task is not None:
            await self._task


_SENTINEL: object = object()


def _factory(id: str, name: str, role: str) -> _NoopTeammate:
    return _NoopTeammate(id=id, name=name, role=role)


@pytest.fixture
async def broker() -> Broker:
    b = Broker()
    yield b
    await b.shutdown_all()


# ---------- spawn_teammate ----------

class TestSpawnTeammate:
    async def test_spawn_returns_teammate_id(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="planner", name="alice", factory=_factory)
        assert isinstance(tid, str) and tid

    async def test_spawn_registers_teammate_in_list(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="planner", name="alice", factory=_factory)
        crew = broker.list_crew()
        assert len(crew) == 1
        assert crew[0].id == tid
        assert crew[0].name == "alice"
        assert crew[0].role == "planner"
        assert crew[0].alive is True

    async def test_spawn_without_name_uses_role_as_default(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="planner", name=None, factory=_factory)
        crew = broker.list_crew()
        assert crew[0].name == "planner"
        assert crew[0].id == tid

    async def test_two_spawns_get_distinct_ids(self, broker: Broker) -> None:
        a = await broker.spawn_teammate(role="planner", name=None, factory=_factory)
        b = await broker.spawn_teammate(role="planner", name=None, factory=_factory)
        assert a != b


# ---------- send ----------

class TestSend:
    async def test_send_delivers_to_recipient_inbox(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        env = Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
            timestamp=0.0, payload={"hi": "there"},
        )
        await broker.send(env)
        # Drain
        await asyncio.sleep(0)
        teammate = broker._teammates[tid]  # type: ignore[attr-defined]
        # Give the loop a tick to deliver
        for _ in range(10):
            if teammate.received:
                break
            await asyncio.sleep(0)
        assert len(teammate.received) == 1
        assert teammate.received[0].payload == {"hi": "there"}

    async def test_send_assigns_monotonic_seq_starting_at_1(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        seqs = []
        for i in range(3):
            env = Envelope(
                id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
                timestamp=0.0, payload=i,
            )
            assigned = await broker.send(env)
            seqs.append(assigned.seq)
        assert seqs == [1, 2, 3]

    async def test_send_to_unknown_teammate_raises(self, broker: Broker) -> None:
        env = Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient="ghost",
            timestamp=0.0, payload=None,
        )
        with pytest.raises(UnknownTeammateError):
            await broker.send(env)

    async def test_duplicate_id_is_silently_dropped(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        mid = new_message_id()
        env1 = Envelope(id=mid, seq=0, sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="first")
        env2 = Envelope(id=mid, seq=0, sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="second")
        await broker.send(env1)
        result = await broker.send(env2)  # dedup'd
        assert result is None  # dedup signal: nothing enqueued
        # Only one message logged
        msgs = broker.get_messages(recipient=tid)
        assert len(msgs) == 1
        assert msgs[0].payload == "first"

    async def test_per_recipient_fifo_preserved(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        for i in range(5):
            env = Envelope(
                id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
                timestamp=0.0, payload=i,
            )
            await broker.send(env)
        msgs = broker.get_messages(recipient=tid)
        assert [m.payload for m in msgs] == [0, 1, 2, 3, 4]
        assert [m.seq for m in msgs] == [1, 2, 3, 4, 5]


# ---------- broadcast ----------

class TestBroadcast:
    async def test_broadcast_fans_out_to_all_teammates(self, broker: Broker) -> None:
        a = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        b = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        ids = await broker.broadcast(sender=LEAD_ID, payload={"announce": True})
        assert len(ids) == 2
        # Both teammates have one message each
        assert len(broker.get_messages(recipient=a)) == 1
        assert len(broker.get_messages(recipient=b)) == 1

    async def test_broadcast_does_not_loop_back_to_sender(self, broker: Broker) -> None:
        a = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker.broadcast(sender=a, payload="ping")
        # Sender 'a' should not receive its own broadcast
        assert broker.get_messages(recipient=a) == []

    async def test_broadcast_to_empty_crew_returns_empty(self, broker: Broker) -> None:
        ids = await broker.broadcast(sender=LEAD_ID, payload="hello")
        assert ids == []


# ---------- get_messages ----------

class TestGetMessages:
    async def test_get_messages_with_no_traffic_returns_empty(self, broker: Broker) -> None:
        assert broker.get_messages(recipient=LEAD_ID) == []

    async def test_get_messages_filters_by_recipient(self, broker: Broker) -> None:
        a = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        b = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker.send(Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient=a,
            timestamp=0.0, payload="for-a",
        ))
        await broker.send(Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient=b,
            timestamp=0.0, payload="for-b",
        ))
        a_msgs = broker.get_messages(recipient=a)
        assert len(a_msgs) == 1
        assert a_msgs[0].payload == "for-a"

    async def test_since_seq_returns_only_strictly_greater(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        for i in range(5):
            await broker.send(Envelope(
                id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
                timestamp=0.0, payload=i,
            ))
        msgs = broker.get_messages(recipient=tid, since_seq=2)
        assert [m.seq for m in msgs] == [3, 4, 5]

    async def test_since_seq_past_end_returns_empty(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker.send(Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
            timestamp=0.0, payload="x",
        ))
        assert broker.get_messages(recipient=tid, since_seq=999) == []

    async def test_limit_caps_results(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        for i in range(5):
            await broker.send(Envelope(
                id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
                timestamp=0.0, payload=i,
            ))
        msgs = broker.get_messages(recipient=tid, limit=2)
        assert [m.seq for m in msgs] == [1, 2]


# ---------- list_crew ----------

class TestListCrew:
    async def test_list_crew_empty_when_no_spawns(self, broker: Broker) -> None:
        assert broker.list_crew() == []

    async def test_list_crew_reflects_kills(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker.kill_teammate(tid)
        assert broker.list_crew() == []


# ---------- kill_teammate ----------

class TestKillTeammate:
    async def test_kill_removes_teammate_from_crew(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker.kill_teammate(tid)
        assert broker.list_crew() == []

    async def test_kill_unknown_teammate_raises(self, broker: Broker) -> None:
        with pytest.raises(UnknownTeammateError):
            await broker.kill_teammate("ghost")

    async def test_send_to_killed_teammate_raises(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker.kill_teammate(tid)
        env = Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
            timestamp=0.0, payload=None,
        )
        with pytest.raises((UnknownTeammateError, TeammateAlreadyDeadError)):
            await broker.send(env)

    async def test_killed_teammate_history_still_readable(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker.send(Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
            timestamp=0.0, payload="before-kill",
        ))
        await broker.kill_teammate(tid)
        msgs = broker.get_messages(recipient=tid)
        assert len(msgs) == 1
        assert msgs[0].payload == "before-kill"
