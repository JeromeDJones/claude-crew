"""Implementation-level tests for the Broker.

The broker is exercised in isolation here. Teammates are stand-in mocks that
do nothing; the broker should not need a working teammate to validate its
own contract.
"""

from __future__ import annotations

import asyncio
import time
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
        # Activity telemetry (base class fields — required for status_snapshot())
        self._last_activity_monotonic = time.monotonic()
        self._last_activity_wallclock = time.time()
        self._current_turn_started_at_wallclock: float | None = None

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


def _factory(id: str, name: str, role: str, **_kwargs) -> _NoopTeammate:
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
        result = await broker.broadcast(sender=LEAD_ID, payload={"announce": True})
        ids = result["message_ids"]
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
        result = await broker.broadcast(sender=LEAD_ID, payload="hello")
        assert result["message_ids"] == []
        assert result["skipped_dead"] == []


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
        """After kill, teammate stays in list with alive=False (D11 tombstone)."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker.kill_teammate(tid)
        crew = broker.list_crew()
        assert len(crew) == 1
        assert crew[0].id == tid
        assert crew[0].alive is False


# ---------- kill_teammate ----------

class TestKillTeammate:
    async def test_kill_tombstones_teammate_not_evicts(self, broker: Broker) -> None:
        """D11: kill_teammate creates a tombstone; teammate stays in _info with alive=False."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker.kill_teammate(tid)
        crew = broker.list_crew()
        assert len(crew) == 1
        assert crew[0].alive is False
        # Not in the active set
        assert tid not in broker._teammates  # type: ignore[attr-defined]

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
        with pytest.raises(TeammateAlreadyDeadError):
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


# ---------- T3 new: tombstone semantics (D2/D3/D6/D11/D12) ----------

class TestTombstoneSemantics:

    # Scenario: kill_teammate tombstones (does not evict) — D11
    async def test_kill_produces_tombstone_with_death_record(self, broker: Broker) -> None:
        """kill_teammate creates alive=False entry with died_at_wallclock set, exit_code=None."""
        tid = await broker.spawn_teammate(role="r", name="alice", factory=_factory)
        before = time.time()
        await broker.kill_teammate(tid)
        after = time.time()

        status = broker.get_teammate_status(tid)
        assert status["alive"] is False
        assert status["exit_code"] is None
        assert before <= status["died_at_wallclock"] <= after
        assert status["last_activity_at_wallclock_at_death"] is not None
        assert status["idle_seconds"] is not None
        assert status["current_turn_started_at_wallclock"] is None

    # Scenario: send_to a killed teammate raises TeammateAlreadyDeadError — D6
    async def test_send_to_tombstoned_raises_dead_error_not_unknown(
        self, broker: Broker,
    ) -> None:
        """D6: post-kill send raises TeammateAlreadyDeadError (not UnknownTeammateError)."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker.kill_teammate(tid)
        env = Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
            timestamp=0.0, payload="hello",
        )
        with pytest.raises(TeammateAlreadyDeadError):
            await broker.send(env)

    # Scenario: get_teammate_status on unknown id
    async def test_get_status_unknown_id_returns_error_dict(
        self, broker: Broker,
    ) -> None:
        result = broker.get_teammate_status("ghost")
        assert result["error"] == "unknown_teammate"
        assert "ghost" in result["message"]

    # Scenario: get_teammate_status on alive teammate
    async def test_get_status_alive_teammate(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="planner", name="bob", factory=_factory)
        status = broker.get_teammate_status(tid)
        assert status["alive"] is True
        assert status["teammate_id"] == tid
        assert status["role"] == "planner"
        assert status["name"] == "bob"
        assert status["died_at_wallclock"] is None
        assert status["exit_code"] is None
        assert "idle_seconds" in status

    # Scenario: broadcast filters dead recipients and reports skipped_dead — D12
    async def test_broadcast_skips_dead_recipients(self, broker: Broker) -> None:
        """D12: tombstoned teammates are skipped and reported in skipped_dead."""
        a = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        b = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        c = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker.kill_teammate(c)

        result = await broker.broadcast(sender=LEAD_ID, payload={"hello": "all"})
        assert len(result["message_ids"]) == 2
        assert c in result["skipped_dead"]
        # c's log should not have the broadcast message
        c_msgs = broker.get_messages(recipient=c)
        assert all(m.payload != {"hello": "all"} for m in c_msgs)

    # Scenario: _handle_teammate_death is idempotent — D2
    async def test_handle_teammate_death_idempotent(self, broker: Broker) -> None:
        """D2: calling _handle_teammate_death twice produces one tombstone (died_at unchanged)."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker._handle_teammate_death(tid, exit_code=137)  # type: ignore[attr-defined]

        first_died_at = broker.get_teammate_status(tid)["died_at_wallclock"]
        assert first_died_at is not None

        # Brief sleep to ensure time.time() would differ if not idempotent
        await asyncio.sleep(0.01)

        # Second call is a no-op
        await broker._handle_teammate_death(tid, exit_code=99)  # type: ignore[attr-defined]

        # died_at_wallclock is unchanged
        assert broker.get_teammate_status(tid)["died_at_wallclock"] == first_died_at
        assert broker.get_teammate_status(tid)["exit_code"] == 137  # from first call

    # Scenario: _handle_teammate_death drains inbox and bounces pending envelopes — SC-5b
    async def test_handle_teammate_death_drains_inbox_and_bounces(
        self, broker: Broker,
    ) -> None:
        """SC-5b: each pending envelope's sender receives a teammate_dead bounce."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)

        # Directly fill the inbox (bypasses send(); teammate won't consume before handler runs)
        inbox = broker._inboxes[tid]  # type: ignore[attr-defined]
        e1 = Envelope(id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="msg1")
        e2 = Envelope(id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="msg2")
        e3 = Envelope(id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="msg3")
        inbox.put_nowait(e1)
        inbox.put_nowait(e2)
        inbox.put_nowait(e3)

        await broker._handle_teammate_death(tid, exit_code=137)  # type: ignore[attr-defined]

        # Lead should have 3 bounce messages
        lead_msgs = broker.get_messages(recipient=LEAD_ID)
        dead_bounces = [
            m for m in lead_msgs
            if isinstance(m.payload, dict) and m.payload.get("error") == "teammate_dead"
        ]
        assert len(dead_bounces) == 3

    # Scenario: _handle_teammate_death bounces the in-flight envelope — SC-5b clause 1
    async def test_handle_teammate_death_bounces_in_flight_envelope(
        self, broker: Broker,
    ) -> None:
        """SC-5b clause 1: in-flight envelope (set by SDK worker) gets bounced to its sender."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)

        # Simulate what SdkTeammate does: set _death_in_flight_envelope on the teammate
        in_flight = Envelope(
            id=new_message_id(), seq=1, sender=LEAD_ID, recipient=tid,
            timestamp=0.0, payload="in-flight-payload",
        )
        broker._teammates[tid]._death_in_flight_envelope = in_flight  # type: ignore[attr-defined]

        await broker._handle_teammate_death(tid, exit_code=1)  # type: ignore[attr-defined]

        # Lead receives a teammate_dead bounce for the in-flight envelope
        lead_msgs = broker.get_messages(recipient=LEAD_ID)
        dead_bounces = [
            m for m in lead_msgs
            if isinstance(m.payload, dict) and m.payload.get("error") == "teammate_dead"
        ]
        assert len(dead_bounces) >= 1
        # The bounce payload should reference the dead teammate
        assert any(tid in m.payload.get("message", "") for m in dead_bounces)

    # Scenario: get_teammate_status freezes idle_seconds at death — D3
    async def test_get_status_freezes_idle_seconds_at_death(
        self, broker: Broker,
    ) -> None:
        """D3: idle_seconds returned post-death is frozen at death time, not growing."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker.kill_teammate(tid)

        status_t1 = broker.get_teammate_status(tid)
        idle_at_t1 = status_t1["idle_seconds"]

        await asyncio.sleep(0.05)

        status_t2 = broker.get_teammate_status(tid)
        idle_at_t2 = status_t2["idle_seconds"]

        # idle_seconds must not grow after death
        assert idle_at_t2 == idle_at_t1
        assert status_t2["current_turn_started_at_wallclock"] is None

    # Scenario: Concurrent send_to during _handle_teammate_death sees teammate_dead — D2
    async def test_send_after_tombstone_sees_teammate_dead_not_unknown(
        self, broker: Broker,
    ) -> None:
        """D2 tombstone-before-pop: send to tombstoned id raises TeammateAlreadyDeadError."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        await broker.kill_teammate(tid)

        # Tombstone is in _info (alive=False), teammate is NOT in _teammates
        assert tid not in broker._teammates  # type: ignore[attr-defined]
        info = broker._info[tid]  # type: ignore[attr-defined]
        assert info.alive is False

        env = Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
            timestamp=0.0, payload="too late",
        )
        with pytest.raises(TeammateAlreadyDeadError):
            await broker.send(env)

    # Scenario: kill_teammate on a stub produces same shape as SDK death — Q9 cohesion
    async def test_kill_stub_status_shape_matches_sdk_death_shape(
        self, broker: Broker,
    ) -> None:
        """Q9: stub kill produces alive=False, died_at_wallclock set, exit_code=None."""
        tid = await broker.spawn_teammate(role="worker", name="w", factory=_factory)
        before = time.time()
        await broker.kill_teammate(tid)
        after = time.time()

        status = broker.get_teammate_status(tid)
        assert status["alive"] is False
        assert before <= status["died_at_wallclock"] <= after
        assert status["exit_code"] is None
        assert status["last_activity_at_wallclock_at_death"] is not None
        assert status["idle_seconds"] is not None
        # Same fields as SDK death shape
        for key in (
            "teammate_id", "name", "role", "alive", "spawned_at",
            "last_activity_at_wallclock", "current_turn_started_at_wallclock",
            "idle_seconds", "died_at_wallclock", "exit_code",
            "last_activity_at_wallclock_at_death",
        ):
            assert key in status, f"missing field: {key}"
