"""Implementation-level tests for the Broker.

The broker is exercised in isolation here. Teammates are stand-in mocks that
do nothing; the broker should not need a working teammate to validate its
own contract.
"""

from __future__ import annotations

import asyncio
import collections
import json
import time
from typing import Any

import pytest

from claude_crew import sdk_teammate as sdk_module
from claude_crew.broker import (
    Broker,
    BrokerSnapshot,
    LEAD_ID,
    LiveTeammateInfo,
    TeammateAlreadyDeadError,
    UnknownTeammateError,
)
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import StubTeammate, Teammate, _ToolUseEntry
from claude_crew.sdk_teammate import SdkTeammate, _SubagentUseEntry
from tests.fakes.sdk import FakeSDKClient, text_response_with_usage


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
        # F8: tool-tracking fields (required by status_snapshot())
        self._broker = None
        self._tool_uses: dict = {}
        self._recently_closed_tool_use_ids: collections.deque = collections.deque(maxlen=64)
        self._last_tool_completed = None
        # F19: completed tool-event deque (required by Broker.snapshot()).
        from claude_crew.teammate import _tool_events_maxlen
        self._completed_tool_events: collections.deque = collections.deque(
            maxlen=_tool_events_maxlen()
        )

    async def start(self, broker: Broker, inbox: asyncio.Queue) -> None:
        self._broker = broker
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


# ---------- T5 FIX-NOW: transcript assertions (sentinel inner-4) ----------


def _stub_factory_for_transcript(id: str, name: str, role: str, **kw: Any) -> StubTeammate:
    return StubTeammate(id=id, name=name, role=role)


def _read_transcript_lines(tmp_path) -> list[dict]:
    files = list(tmp_path.iterdir())
    assert files, "no transcript file found"
    return [json.loads(line) for line in files[0].read_text().splitlines() if line.strip()]


class TestTranscriptAssertions:
    """SC-6 and SC-9: transcript content verification (FIX-NOW from sentinel inner-4)."""

    async def test_kill_transcript_writes_kill_not_died(
        self, monkeypatch, tmp_path,
    ) -> None:
        """SC-6: explicit kill emits lifecycle event 'kill', NOT 'died'."""
        monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", raising=False)
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="r", name=None, factory=_stub_factory_for_transcript,
            )
            await b.kill_teammate(tid)
        finally:
            await b.shutdown_all()

        lines = _read_transcript_lines(tmp_path)
        lifecycle_events = [
            l["event"] for l in lines if l.get("kind") == "lifecycle"
        ]
        assert "kill" in lifecycle_events, f"expected 'kill' in events, got {lifecycle_events}"
        assert "died" not in lifecycle_events, (
            f"'died' must not appear for explicit kill, got {lifecycle_events}"
        )
        kill_line = next(
            l for l in lines
            if l.get("kind") == "lifecycle" and l.get("event") == "kill"
        )
        assert kill_line["teammate_id"] == tid

    async def test_lifecycle_died_carries_death_record_fields(
        self, monkeypatch, tmp_path,
    ) -> None:
        """SC-9: _handle_teammate_death emits 'died' with exit_code,
        idle_seconds_at_death, and last_activity_at_wallclock.
        """
        monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", raising=False)
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="r", name=None, factory=_stub_factory_for_transcript,
            )
            await b._handle_teammate_death(tid, exit_code=137)
        finally:
            await b.shutdown_all()

        lines = _read_transcript_lines(tmp_path)
        died_lines = [
            l for l in lines
            if l.get("kind") == "lifecycle" and l.get("event") == "died"
        ]
        assert len(died_lines) == 1, f"expected exactly 1 'died' line, got {len(died_lines)}"
        d = died_lines[0]
        assert d["exit_code"] == 137, f"exit_code mismatch: {d}"
        assert "idle_seconds_at_death" in d, f"missing idle_seconds_at_death: {d}"
        assert "last_activity_at_wallclock" in d, f"missing last_activity_at_wallclock: {d}"
        assert d["teammate_id"] == tid


# ---------- T4: _close_open_tools wired into broker death/kill paths ----------


class TestT4ToolClosureOnDeathAndKill:
    """T4 BDD: death/kill paths call _close_open_tools before lifecycle event.

    Scenarios:
      1. death-mid-tool emits tool_end(abandoned) before lifecycle:died
      2. kill-mid-tool emits tool_end(killed) before lifecycle:kill
      3. tombstoned teammate retains last_tool_completed (clean tool from before death)
      4. get_teammate_status alive includes F8 fields
      5. get_teammate_status unknown unchanged (no regression)
      6. broker MCP tool treated as first-class tool event (D12)
    """

    async def test_death_mid_tool_emits_abandoned_tool_end_before_lifecycle_died(
        self, monkeypatch, tmp_path,
    ) -> None:
        """SC-14: subprocess death while tool in flight emits tool_end(abandoned)
        BEFORE lifecycle:died in the transcript (replay-ordering guarantee)."""
        monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", raising=False)
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
        b = Broker()
        try:
            tid = await b.spawn_teammate(role="r", name=None, factory=_factory)
            teammate = b._teammates[tid]  # type: ignore[attr-defined]
            teammate._tool_uses["toolu_bash_abc"] = _ToolUseEntry(
                tool_name="Bash",
                tool_use_id="toolu_bash_abc",
                started_at_wallclock=time.time() - 5.0,
                args_summary="pytest tests/",
            )
            await b._handle_teammate_death(tid, exit_code=1)  # type: ignore[attr-defined]
        finally:
            await b.shutdown_all()

        lines = _read_transcript_lines(tmp_path)
        tool_end_idx = [i for i, l in enumerate(lines) if l.get("kind") == "tool_end"]
        died_idx = [
            i for i, l in enumerate(lines)
            if l.get("kind") == "lifecycle" and l.get("event") == "died"
        ]

        assert len(tool_end_idx) == 1, f"expected 1 tool_end, got {tool_end_idx} in {lines}"
        assert len(died_idx) == 1, f"expected 1 lifecycle:died, got {died_idx} in {lines}"
        assert tool_end_idx[0] < died_idx[0], (
            f"tool_end (line {tool_end_idx[0]}) must precede lifecycle:died (line {died_idx[0]})"
        )

        te = lines[tool_end_idx[0]]
        assert te["outcome"] == "abandoned"
        assert te["tool_name"] == "Bash"
        assert te["tool_use_id"] == "toolu_bash_abc"
        assert te["teammate_id"] == tid

    async def test_kill_mid_tool_emits_killed_tool_end_before_lifecycle_kill(
        self, monkeypatch, tmp_path,
    ) -> None:
        """SC-14: explicit kill while tool in flight emits tool_end(killed)
        BEFORE lifecycle:kill in the transcript."""
        monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", raising=False)
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
        b = Broker()
        try:
            tid = await b.spawn_teammate(role="r", name=None, factory=_factory)
            teammate = b._teammates[tid]  # type: ignore[attr-defined]
            teammate._tool_uses["toolu_fetch_xyz"] = _ToolUseEntry(
                tool_name="WebFetch",
                tool_use_id="toolu_fetch_xyz",
                started_at_wallclock=time.time() - 2.0,
                args_summary="https://example.com",
            )
            await b.kill_teammate(tid)
        finally:
            await b.shutdown_all()

        lines = _read_transcript_lines(tmp_path)
        tool_end_idx = [i for i, l in enumerate(lines) if l.get("kind") == "tool_end"]
        kill_idx = [
            i for i, l in enumerate(lines)
            if l.get("kind") == "lifecycle" and l.get("event") == "kill"
        ]

        assert len(tool_end_idx) == 1, f"expected 1 tool_end, got {lines}"
        assert len(kill_idx) == 1, f"expected 1 lifecycle:kill, got {lines}"
        assert tool_end_idx[0] < kill_idx[0], (
            f"tool_end must precede lifecycle:kill"
        )

        te = lines[tool_end_idx[0]]
        assert te["outcome"] == "killed"
        assert te["tool_name"] == "WebFetch"
        assert te["teammate_id"] == tid

    async def test_tombstoned_teammate_retains_last_tool_completed_not_abandoned(
        self, broker: Broker,
    ) -> None:
        """SC-7 / SC-14: post-mortem status preserves last cleanly-finished tool;
        the abandoned tool does NOT overwrite last_tool_completed."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        teammate = broker._teammates[tid]  # type: ignore[attr-defined]

        clean_tool: dict[str, Any] = {
            "tool_name": "Read",
            "outcome": "ok",
            "finished_at_wallclock": time.time() - 1.0,
            "duration_seconds": 0.05,
        }
        teammate._last_tool_completed = clean_tool

        # Inject an in-flight tool — will be abandoned when kill fires
        teammate._tool_uses["toolu_inflight"] = _ToolUseEntry(
            tool_name="Bash",
            tool_use_id="toolu_inflight",
            started_at_wallclock=time.time() - 5.0,
            args_summary=None,
        )

        await broker.kill_teammate(tid)

        status = broker.get_teammate_status(tid)
        assert status["alive"] is False
        assert status["last_tool_completed"] == clean_tool, (
            "last_tool_completed must reflect the clean tool, not the abandoned one"
        )
        assert status["current_tools"] == []
        assert status["current_tool"] is None
        assert status["current_tool_count"] == 0

    async def test_get_status_alive_teammate_includes_f8_fields(
        self, broker: Broker,
    ) -> None:
        """SC-7 (alive path): get_teammate_status surfaces F8 fields for alive teammate."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        status = broker.get_teammate_status(tid)

        for key in ("current_tools", "current_tool", "current_tool_count",
                    "last_tool_completed", "redaction_version"):
            assert key in status, f"F8 field missing from alive status: {key!r}"

        assert status["current_tools"] == []
        assert status["current_tool"] is None
        assert status["current_tool_count"] == 0
        assert status["last_tool_completed"] is None

    async def test_get_status_unknown_teammate_unchanged(
        self, broker: Broker,
    ) -> None:
        """SC-7 (no regression): unknown teammate returns only the error dict."""
        result = broker.get_teammate_status("ghost-id")
        assert result["error"] == "unknown_teammate"
        # F8 fields must NOT appear on the error shape
        assert "current_tools" not in result
        assert "alive" not in result
        assert "current_tool" not in result

    async def test_broker_mcp_tool_treated_as_first_class_tool_event(
        self, monkeypatch, tmp_path,
    ) -> None:
        """D12: broker MCP tools (e.g. send_to) are first-class — closed on death
        like any tool, no special-casing that would create a blind spot."""
        monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", raising=False)
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
        b = Broker()
        try:
            tid = await b.spawn_teammate(role="r", name=None, factory=_factory)
            teammate = b._teammates[tid]  # type: ignore[attr-defined]
            teammate._tool_uses["toolu_mcp_send"] = _ToolUseEntry(
                tool_name="mcp__claude-crew__send_to",
                tool_use_id="toolu_mcp_send",
                started_at_wallclock=time.time() - 0.5,
                args_summary=None,
            )
            await b._handle_teammate_death(tid, exit_code=0)  # type: ignore[attr-defined]
        finally:
            await b.shutdown_all()

        lines = _read_transcript_lines(tmp_path)
        tool_end_lines = [l for l in lines if l.get("kind") == "tool_end"]
        assert len(tool_end_lines) == 1, f"expected 1 tool_end for MCP tool, got {lines}"
        te = tool_end_lines[0]
        assert te["tool_name"] == "mcp__claude-crew__send_to"
        assert te["outcome"] == "abandoned"
        assert te["teammate_id"] == tid


# ---------- T4 (F7): subagent-activity fields surfaced via broker ----------

class _SubagentAwareNoopTeammate(_NoopTeammate):
    """Extends _NoopTeammate with F7 subagent-tracking fields and snapshot support.

    This mirrors the _subagent_uses / _closed_subagent_scratch / _last_subagent_completed
    fields that SdkTeammate owns, so broker tests can exercise the T4 subagent paths
    without spinning up a real SDK subprocess.
    """

    def __init__(self, id: str, name: str, role: str) -> None:
        super().__init__(id=id, name=name, role=role)
        self._subagent_uses: dict[str, _SubagentUseEntry] = {}
        self._closed_subagent_scratch: dict = {}
        self._last_subagent_completed: dict[str, Any] | None = None

    def status_snapshot(self) -> dict[str, Any]:
        snap = super().status_snapshot()
        subagent_entries = [
            {
                "agent_id": e.agent_id,
                "tool_use_id": e.tool_use_id,
                "spawned_at_wallclock": e.spawned_at_wallclock,
            }
            for e in self._subagent_uses.values()
        ]
        snap["current_subagents"] = subagent_entries
        snap["last_subagent_completed"] = self._last_subagent_completed
        snap["in_flight_subagents_at_death"] = None
        return snap

    def _close_open_subagents(self, reason: str) -> None:
        """Drain in-flight subagent state (stub — records call for test assertions)."""
        self._subagents_closed_reason = reason
        self._subagent_uses.clear()
        self._closed_subagent_scratch.clear()


def _subagent_aware_factory(id: str, name: str, role: str, **_kw) -> _SubagentAwareNoopTeammate:
    return _SubagentAwareNoopTeammate(id=id, name=name, role=role)


@pytest.mark.asyncio
class TestSubagentBrokerIntegration:
    """T4 BDD: subagent-activity fields surfaced in get_teammate_status for alive and dead paths.

    Scenarios:
      1. Alive status includes subagent fields (all empty/null baseline)
      2. Alive status reflects in-flight subagents from _subagent_uses
      3. Tombstone captures in_flight_subagents_at_death count
      4. Tombstone preserves last_subagent_completed (D9 flip — F8 symmetry)
      5. _close_open_subagents called on death; transcript contains subagent_abandoned_batch
    """

    async def test_alive_status_includes_subagent_fields_baseline(
        self, broker: Broker,
    ) -> None:
        """SC: alive teammate has current_subagents=[], last_subagent_completed=None,
        in_flight_subagents_at_death=None, plus F6/F8 fields all present."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_subagent_aware_factory)
        status = broker.get_teammate_status(tid)

        assert status["alive"] is True
        assert status["current_subagents"] == []
        assert status["last_subagent_completed"] is None
        assert status["in_flight_subagents_at_death"] is None
        # Verify F6/F8 fields still present (no regression)
        for key in ("current_tools", "current_tool", "current_tool_count",
                    "last_tool_completed", "redaction_version"):
            assert key in status, f"F8 field missing from alive status: {key!r}"

    async def test_alive_status_reflects_in_flight_subagents(
        self, broker: Broker,
    ) -> None:
        """SC: alive teammate with one entry in _subagent_uses → current_subagents has
        one entry with agent_id, tool_use_id, and spawned_at_wallclock."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_subagent_aware_factory)
        teammate = broker._teammates[tid]  # type: ignore[attr-defined]

        entry = _SubagentUseEntry(
            agent_id="agent-abc",
            tool_use_id="toolu_subagent_1",
            spawned_at_wallclock=time.time() - 2.0,
        )
        teammate._subagent_uses["toolu_subagent_1"] = entry

        status = broker.get_teammate_status(tid)
        assert len(status["current_subagents"]) == 1
        sub = status["current_subagents"][0]
        assert sub["agent_id"] == "agent-abc"
        assert sub["tool_use_id"] == "toolu_subagent_1"
        assert "spawned_at_wallclock" in sub

    async def test_tombstone_captures_in_flight_subagent_count(
        self, broker: Broker,
    ) -> None:
        """SC: two entries in _subagent_uses when killed → in_flight_subagents_at_death == 2
        in dead status."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_subagent_aware_factory)
        teammate = broker._teammates[tid]  # type: ignore[attr-defined]

        now = time.time()
        teammate._subagent_uses["toolu_sub_a"] = _SubagentUseEntry(
            agent_id="agent-a", tool_use_id="toolu_sub_a", spawned_at_wallclock=now - 3.0,
        )
        teammate._subagent_uses["toolu_sub_b"] = _SubagentUseEntry(
            agent_id="agent-b", tool_use_id="toolu_sub_b", spawned_at_wallclock=now - 1.5,
        )

        await broker.kill_teammate(tid)

        status = broker.get_teammate_status(tid)
        assert status["alive"] is False
        assert status["in_flight_subagents_at_death"] == 2
        # After death, current_subagents must be empty
        assert status["current_subagents"] == []

    async def test_tombstone_preserves_last_subagent_completed(
        self, broker: Broker,
    ) -> None:
        """SC (D9 flip — F8 symmetry): teammate that ran one subagent (so
        _last_subagent_completed is set) then was killed → dead status preserves
        last_subagent_completed from the tombstone."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_subagent_aware_factory)
        teammate = broker._teammates[tid]  # type: ignore[attr-defined]

        completed_record: dict[str, Any] = {
            "agent_id": "agent-done",
            "tool_use_id": "toolu_done",
            "finished_at_wallclock": time.time() - 0.5,
            "hook_outcome": "success",
        }
        teammate._last_subagent_completed = completed_record

        await broker.kill_teammate(tid)

        status = broker.get_teammate_status(tid)
        assert status["alive"] is False
        assert status["last_subagent_completed"] == completed_record, (
            "last_subagent_completed must be preserved in tombstone"
        )

    async def test_close_open_subagents_called_on_death(
        self, broker: Broker,
    ) -> None:
        """SC: one in-flight subagent when killed → _close_open_subagents was called
        (sentinel: _subagents_closed_reason set) and dead status has
        in_flight_subagents_at_death == 1."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_subagent_aware_factory)
        teammate = broker._teammates[tid]  # type: ignore[attr-defined]

        teammate._subagent_uses["toolu_active"] = _SubagentUseEntry(
            agent_id="agent-running",
            tool_use_id="toolu_active",
            spawned_at_wallclock=time.time() - 1.0,
        )

        # Capture count before kill (broker reads it at tombstone time)
        await broker.kill_teammate(tid)

        # Verify broker tombstoned with correct count
        status = broker.get_teammate_status(tid)
        assert status["alive"] is False
        assert status["in_flight_subagents_at_death"] == 1

        # Verify _close_open_subagents was called on the teammate
        assert hasattr(teammate, "_subagents_closed_reason"), (
            "_close_open_subagents was not called — _subagents_closed_reason not set"
        )
        assert teammate._subagents_closed_reason == "kill"


# ---------- F9: lead long-poll / wait_for_lead_message ----------


@pytest.mark.asyncio
class TestLeadMessageLongPoll:
    """F9: SC-11 (LEAD inbox removal) and wait_for_lead_message contract.

    Scenarios:
      1. LEAD_ID absent from _inboxes at init (SC-11 baseline)
      2. LEAD_ID stays absent after lead-bound sends; messages still in _log (SC-11)
      3. wait_for_lead_message(0) is a no-op (immediate return)
      4. wait_for_lead_message(-x) is a no-op (immediate return)
      5. wait_for_lead_message(0.2) times out cleanly in ~0.2 s (SC-4)
      6. wait_for_lead_message(5) wakes when a LEAD-bound send arrives (SC-3)
      7. Teammate-to-teammate send does NOT wake a lead long-poll (SC-5)
      8. Cancellation of wait_for_lead_message does not deadlock subsequent ops (SC-7)
      9. shutdown_all wakes any pending wait_for_lead_message (SC-10)
    """

    # SC-11: LEAD_ID not in _inboxes immediately after Broker.__init__
    async def test_lead_id_not_in_inboxes_at_init(self, broker: Broker) -> None:
        assert LEAD_ID not in broker._inboxes  # type: ignore[attr-defined]

    # SC-11: LEAD_ID stays absent after multiple lead-bound sends; messages readable via _log
    async def test_lead_id_not_in_inboxes_after_lead_sends(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        for i in range(3):
            env = Envelope(
                id=new_message_id(), seq=0, sender=tid, recipient=LEAD_ID,
                timestamp=0.0, payload=i,
            )
            await broker.send(env)
        assert LEAD_ID not in broker._inboxes  # type: ignore[attr-defined]
        msgs = broker.get_messages(recipient=LEAD_ID)
        assert len(msgs) == 3
        assert [m.payload for m in msgs] == [0, 1, 2]

    # wait_for_lead_message no-ops on timeout <= 0
    async def test_wait_for_lead_message_noop_on_zero(self, broker: Broker) -> None:
        start = time.monotonic()
        await broker.wait_for_lead_message(0.0)  # type: ignore[attr-defined]
        assert time.monotonic() - start < 0.05

    async def test_wait_for_lead_message_noop_on_negative(self, broker: Broker) -> None:
        start = time.monotonic()
        await broker.wait_for_lead_message(-5.0)  # type: ignore[attr-defined]
        assert time.monotonic() - start < 0.05

    # SC-4: timeout returns silently after the specified duration
    async def test_wait_for_lead_message_times_out(self, broker: Broker) -> None:
        start = time.monotonic()
        await broker.wait_for_lead_message(0.2)  # type: ignore[attr-defined]
        elapsed = time.monotonic() - start
        assert 0.15 <= elapsed <= 0.6, f"expected ~0.2 s wait, got {elapsed:.3f} s"

    # SC-3: wakes when a LEAD-bound send arrives during wait
    async def test_wait_for_lead_message_wakes_on_lead_send(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)

        async def _delayed_send() -> None:
            await asyncio.sleep(0.2)
            env = Envelope(
                id=new_message_id(), seq=0, sender=tid, recipient=LEAD_ID,
                timestamp=0.0, payload="wake",
            )
            await broker.send(env)

        send_task = asyncio.create_task(_delayed_send())
        start = time.monotonic()
        await broker.wait_for_lead_message(5.0)  # type: ignore[attr-defined]
        elapsed = time.monotonic() - start
        await send_task

        assert 0.1 <= elapsed <= 0.7, f"expected ~0.2 s wake, got {elapsed:.3f} s"
        assert len(broker.get_messages(recipient=LEAD_ID)) == 1

    # SC-5: teammate-to-teammate send does NOT notify the lead Condition
    async def test_teammate_send_does_not_wake_lead_poll(self, broker: Broker) -> None:
        a = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        b_id = await broker.spawn_teammate(role="r", name=None, factory=_factory)

        async def _teammate_send() -> None:
            await asyncio.sleep(0.05)
            env = Envelope(
                id=new_message_id(), seq=0, sender=a, recipient=b_id,
                timestamp=0.0, payload="peer-msg",
            )
            await broker.send(env)

        task = asyncio.create_task(_teammate_send())
        start = time.monotonic()
        # Times out — NOT woken by the teammate-to-teammate send
        await broker.wait_for_lead_message(0.2)  # type: ignore[attr-defined]
        elapsed = time.monotonic() - start
        await task

        assert elapsed >= 0.15, (
            f"lead poll was spuriously woken by a teammate-to-teammate send: {elapsed:.3f} s"
        )

    # SC-7: cancellation does not leave the Condition locked / deadlock subsequent ops
    async def test_cancellation_does_not_deadlock(self, broker: Broker) -> None:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)

        poll_task = asyncio.create_task(
            broker.wait_for_lead_message(10.0)  # type: ignore[attr-defined]
        )
        await asyncio.sleep(0.05)
        poll_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await poll_task

        # Subsequent send must not deadlock; fresh get_messages must return the message.
        env = Envelope(
            id=new_message_id(), seq=0, sender=tid, recipient=LEAD_ID,
            timestamp=0.0, payload="after-cancel",
        )
        stamped = await asyncio.wait_for(broker.send(env), timeout=1.0)
        assert stamped is not None
        msgs = broker.get_messages(recipient=LEAD_ID)
        assert len(msgs) == 1
        assert msgs[0].payload == "after-cancel"

    # SC-10: shutdown_all wakes any pending wait_for_lead_message cleanly
    async def test_shutdown_all_wakes_pending_long_poll(self) -> None:
        b = Broker()  # manual lifecycle — not the fixture

        poll_task = asyncio.create_task(
            b.wait_for_lead_message(30.0)  # type: ignore[attr-defined]
        )
        await asyncio.sleep(0.05)

        await b.shutdown_all()  # must notify Condition before closing sink

        done, _ = await asyncio.wait([poll_task], timeout=0.5)
        assert poll_task in done, (
            "wait_for_lead_message did not return after shutdown_all"
        )


# ---------- F14 (T3): tombstone token/cost preservation + broker forwarding ----------


def _patch_sdk_broker(monkeypatch: pytest.MonkeyPatch, fake: FakeSDKClient) -> None:
    """Patch ClaudeSDKClient in sdk_teammate module to use FakeSDKClient."""
    captured: dict[str, Any] = {}

    def _ctor(*args: Any, **kwargs: Any) -> FakeSDKClient:
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(sdk_module, "ClaudeSDKClient", _ctor)


def _sdk_factory(id: str, name: str, role: str, **_kwargs: Any) -> SdkTeammate:
    return SdkTeammate(id=id, name=name, role=role)


async def _wait_lead_msgs_broker(broker: Broker, count: int, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(broker.get_messages(recipient=LEAD_ID)) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"timed out waiting for {count} lead messages; "
        f"got {len(broker.get_messages(recipient=LEAD_ID))}"
    )


@pytest.mark.asyncio
class TestTokenCostBrokerIntegration:
    """T3 BDD: tombstone preservation of token/cost + get_teammate_status forwarding.

    Scenarios:
      1. Tombstone preserves last cumulative cost after multiple turns
      2. Tombstone with no completed turns has zero at-death values (D-7)
      3. Alive teammate's status returns live snap token/cost fields
    """

    async def test_tombstone_preserves_last_cumulative_after_n_turns(
        self, broker: Broker, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SC-4: three turns with progressive cumulative cost 0.10 → 0.30 → 0.60.

        After killing the teammate, TeammateInfo._at_death fields reflect the
        final cumulative values, and get_teammate_status returns them on the dead branch.
        """
        fake = FakeSDKClient(
            scripted_responses=[
                text_response_with_usage(
                    "turn1",
                    turn_input_tokens=100,
                    turn_output_tokens=50,
                    cumulative_cost_usd=0.10,
                ),
                text_response_with_usage(
                    "turn2",
                    turn_input_tokens=200,  # per-turn: 300 total = 100 + 200
                    turn_output_tokens=100,  # per-turn: 150 total = 50 + 100
                    cumulative_cost_usd=0.30,
                ),
                text_response_with_usage(
                    "turn3",
                    turn_input_tokens=300,  # per-turn: 600 total = 300 + 300
                    turn_output_tokens=150,  # per-turn: 300 total = 150 + 150
                    cumulative_cost_usd=0.60,
                ),
            ]
        )
        _patch_sdk_broker(monkeypatch, fake)

        tid = await broker.spawn_teammate(role="r", name=None, factory=_sdk_factory)
        for i in range(3):
            await broker.send(Envelope(
                id=new_message_id(), seq=0,
                sender=LEAD_ID, recipient=tid, timestamp=0.0,
                payload=f"q{i}",
            ))
        await _wait_lead_msgs_broker(broker, 3)

        # Kill and verify tombstone at-death values
        await broker.kill_teammate(tid)

        info = broker._info[tid]
        assert info.alive is False
        assert info.total_cost_usd_at_death == 0.60, (
            f"expected 0.60 at death (cumulative overwrite), got {info.total_cost_usd_at_death}"
        )
        assert info.total_input_tokens_at_death == 600, (
            f"expected 600 at death (100+200+300 accumulate), got {info.total_input_tokens_at_death}"
        )
        assert info.total_output_tokens_at_death == 300, (
            f"expected 300 at death (50+100+150 accumulate), got {info.total_output_tokens_at_death}"
        )

        # get_teammate_status dead branch must forward the values
        status = broker.get_teammate_status(tid)
        assert status["alive"] is False
        assert status["total_cost_usd"] == 0.60
        assert status["total_input_tokens"] == 600
        assert status["total_output_tokens"] == 300

    async def test_tombstone_with_no_turns_has_zero_at_death_values(
        self, broker: Broker,
    ) -> None:
        """D-7: teammate killed before any turn produces a ResultMessage → zeros, not None.

        Uses StubTeammate (which returns zeros from status_snapshot() per SC-7 / D-5).
        The broker extracts from snap.get("total_*", 0/0/0.0) → numeric zero defaults.
        get_teammate_status dead branch coerces None → 0 on the wire, but here extraction
        succeeds so at-death fields ARE numeric zero (not None).
        """
        # StubTeammate starts immediately with zero tokens/cost — no turns needed.
        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_stub_factory_for_transcript,
        )
        await broker.kill_teammate(tid)

        info = broker._info[tid]
        assert info.alive is False
        assert info.total_input_tokens_at_death == 0, (
            f"expected 0, got {info.total_input_tokens_at_death!r} (should not be None per D-7)"
        )
        assert info.total_output_tokens_at_death == 0
        assert info.total_cost_usd_at_death == 0.0

        status = broker.get_teammate_status(tid)
        assert status["alive"] is False
        assert status["total_input_tokens"] == 0
        assert status["total_output_tokens"] == 0
        assert status["total_cost_usd"] == 0.0

    async def test_get_teammate_status_alive_forwards_token_cost(
        self, broker: Broker, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SC-4 (alive branch): get_teammate_status on a live SdkTeammate reads
        token/cost from the live status_snapshot().

        Drive one turn with cumulative cost 0.25, tokens 200/100.
        Assert the alive status response contains those values.
        """
        fake = FakeSDKClient(
            scripted_responses=[
                text_response_with_usage(
                    "hi",
                    turn_input_tokens=200,
                    turn_output_tokens=100,
                    cumulative_cost_usd=0.25,
                ),
            ]
        )
        _patch_sdk_broker(monkeypatch, fake)

        tid = await broker.spawn_teammate(role="r", name=None, factory=_sdk_factory)
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hello",
        ))
        await _wait_lead_msgs_broker(broker, 1)

        # Teammate is still alive — read from live snap
        status = broker.get_teammate_status(tid)
        assert status["alive"] is True
        assert status["total_input_tokens"] == 200, (
            f"expected 200 (per-turn accumulated), got {status['total_input_tokens']}"
        )
        assert status["total_output_tokens"] == 100, (
            f"expected 100 (per-turn accumulated), got {status['total_output_tokens']}"
        )
        assert status["total_cost_usd"] == 0.25

    async def test_tombstone_when_teammate_already_removed_does_not_crash(
        self, broker: Broker,
    ) -> None:
        """Race path: _tombstone_teammate called after teammate self-removed from _teammates.

        Sentinel-found gap (Phase 4 final review): the else branch when
        teammate is None must initialize all _at_death fields, including the
        F14 token/cost trio. Otherwise dataclasses.replace raises
        UnboundLocalError. Pre-F14 this branch was incomplete in spirit (it
        produced a stale tombstone) but didn't crash; F14 made it crashable.
        """
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)

        # Simulate the race: remove from _teammates BEFORE _tombstone_teammate runs.
        # Concretely this happens if the teammate task finishes its own cleanup
        # before the broker's kill path reaches the snapshot step.
        broker._teammates.pop(tid, None)

        # Now run the tombstone path — must not raise UnboundLocalError.
        await broker._tombstone_teammate(tid, 0, "kill")

        # Verify tombstone is well-formed: alive=False, F14 fields default to None
        # (AttributeError-equivalent path — no live teammate to query).
        info = broker._info[tid]
        assert info.alive is False
        assert info.total_input_tokens_at_death is None
        assert info.total_output_tokens_at_death is None
        assert info.total_cost_usd_at_death is None

        # get_teammate_status must coerce None → numeric zero on the wire.
        status = broker.get_teammate_status(tid)
        assert status["alive"] is False
        assert status["total_input_tokens"] == 0
        assert status["total_output_tokens"] == 0
        assert status["total_cost_usd"] == 0.0


# ---------- T1 (#18): BrokerSnapshot types + Broker.snapshot() ----------


@pytest.mark.asyncio
class TestBrokerSnapshot:
    """T1 BDD: BrokerSnapshot types and Broker.snapshot() method.

    Scenarios (per Phase 3 spec):
      1. snapshot exposes all live teammates with embedded info+status+model
      2. snapshot is isolated from teammate state mutation (deep-copy, D-2)
      3. snapshot is a synchronous method (D-1)
      4. log_limit param honors the cap (D-5)
      5. snapshot includes both alive and tombstoned teammates in `teammates`
      6. tool_events is empty by default (D-10)
    """

    async def test_snapshot_includes_alive_teammates_with_info_status_model(
        self, broker: Broker,
    ) -> None:
        """Scenario 1: two alive teammates → live has length 2, each with info/status/model.

        StubTeammate has no _model, so model is None (D-3 documented behaviour).
        """
        tid_a = await broker.spawn_teammate(role="planner", name="alice", factory=_factory)
        tid_b = await broker.spawn_teammate(role="worker", name="bob", factory=_factory)

        snap = broker.snapshot()

        assert isinstance(snap, BrokerSnapshot)
        assert len(snap.live) == 2

        live_ids = {entry.info.id for entry in snap.live}
        assert live_ids == {tid_a, tid_b}

        for entry in snap.live:
            assert isinstance(entry, LiveTeammateInfo)
            # info references the corresponding TeammateInfo
            assert entry.info.alive is True
            # status is a dict (from status_snapshot)
            assert isinstance(entry.status, dict)
            # StubTeammate / _NoopTeammate have no _model → None
            assert entry.model is None

        # Also verify the info objects match what list_crew returns
        crew_ids = {info.id for info in broker.list_crew()}
        assert live_ids == crew_ids

    async def test_snapshot_isolated_from_teammate_state_mutation(
        self, broker: Broker,
    ) -> None:
        """Scenario 2 (D-2): mutating teammate._last_tool_completed after snapshot
        must NOT change the snapshot's view — proves deepcopy is happening.
        """
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        teammate = broker._teammates[tid]  # type: ignore[attr-defined]

        # Give the teammate a last_tool_completed dict so the snapshot captures it
        original_tool: dict[str, Any] = {
            "tool_name": "Read",
            "outcome": "ok",
            "finished_at_wallclock": time.time() - 1.0,
            "duration_seconds": 0.05,
        }
        teammate._last_tool_completed = original_tool

        snap = broker.snapshot()

        # Confirm snapshot captured the value
        assert snap.live[0].status["last_tool_completed"] == original_tool

        # Mutate the inner dict on the live teammate — the snapshot must not see it
        teammate._last_tool_completed["leaked_key"] = "leak"

        assert "leaked_key" not in snap.live[0].status["last_tool_completed"], (
            "snapshot status dict reflects post-snapshot mutation — deepcopy is not happening"
        )

        # Also mutate by replacing the entire reference — still should not affect snapshot
        teammate._last_tool_completed = {"completely": "different"}

        # The original snapshot value must be stable (it was deep-copied at build time)
        assert snap.live[0].status["last_tool_completed"]["tool_name"] == "Read"

    async def test_snapshot_is_synchronous_method(self, broker: Broker) -> None:
        """Scenario 3 (D-1): Broker.snapshot must be a regular def, not async def."""
        import inspect
        assert not inspect.iscoroutinefunction(Broker.snapshot), (
            "Broker.snapshot() must be synchronous (def), not async"
        )

    async def test_snapshot_log_limit_param(self, broker: Broker) -> None:
        """Scenario 4 (D-5): log_limit=N returns last N envelopes; None returns all."""
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)

        # Inject 500 envelopes directly into _log (bypasses routing)
        for i in range(500):
            broker._log.append(  # type: ignore[attr-defined]
                Envelope(
                    id=new_message_id(),
                    seq=i + 1,
                    sender="lead",
                    recipient=tid,
                    timestamp=float(i),
                    payload=i,
                )
            )

        snap_limited = broker.snapshot(log_limit=200)
        assert len(snap_limited.log) == 200, (
            f"expected 200 entries with log_limit=200, got {len(snap_limited.log)}"
        )

        snap_full = broker.snapshot(log_limit=None)
        # The broker fixture starts clean, but spawn may add envelopes too;
        # we seeded exactly 500 so the full snap must have >= 500
        assert len(snap_full.log) >= 500, (
            f"expected at least 500 entries with log_limit=None, got {len(snap_full.log)}"
        )

    async def test_snapshot_teammates_includes_alive_and_dead(
        self, broker: Broker,
    ) -> None:
        """Scenario 5: teammates tuple includes both alive and tombstoned entries;
        live tuple contains only alive entries.
        """
        tid_a = await broker.spawn_teammate(role="r", name="alive", factory=_factory)
        tid_b = await broker.spawn_teammate(role="r", name="dead", factory=_factory)
        await broker.kill_teammate(tid_b)

        snap = broker.snapshot()

        teammate_ids = {info.id for info in snap.teammates}
        assert tid_a in teammate_ids
        assert tid_b in teammate_ids
        assert len(snap.teammates) == 2

        live_ids = {entry.info.id for entry in snap.live}
        assert live_ids == {tid_a}
        assert len(snap.live) == 1

    async def test_snapshot_tool_events_default_empty(
        self, broker: Broker,
    ) -> None:
        """Scenario 6 (D-10): tool_events field reserved for #19; must be empty tuple."""
        snap = broker.snapshot()
        assert snap.tool_events == (), (
            f"expected empty tool_events tuple, got {snap.tool_events!r}"
        )


class TestF19TombstoneCapture:
    """T2: _tombstone_teammate captures _completed_tool_events into TeammateInfo (D-7 / sentinel F1)."""

    async def test_tombstone_preserves_tool_events_at_death(self, broker: Broker) -> None:
        """SC-4 / sentinel F1: tombstone uses dataclasses.replace (no FrozenInstanceError)
        and includes events appended by _close_open_tools (step 8c runs AFTER 8b)."""
        from claude_crew.teammate import ToolEvent, _ToolUseEntry

        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        teammate = broker._teammates[tid]

        # 3 already-completed tool events.
        for i in range(3):
            teammate._completed_tool_events.append(
                ToolEvent(
                    teammate_id=tid, tool_name="Bash", tool_use_id=f"done-{i}",
                    started_at_wallclock=1.0 + i, finished_at_wallclock=1.5 + i,
                    duration_seconds=0.5, outcome="ok",
                    args_summary=None, error_summary=None, redaction_version="v1",
                )
            )
        # 1 in-flight tool that will be abandoned by _close_open_tools at step 8b.
        teammate._tool_uses["in-flight-1"] = _ToolUseEntry(
            tool_name="Read", tool_use_id="in-flight-1",
            started_at_wallclock=time.time() - 0.1, args_summary=None,
        )

        # Tombstone via the kill path (lifecycle="kill").
        await broker.kill_teammate(tid)
        await asyncio.sleep(0.05)  # allow tombstone task to complete

        info = broker._info[tid]
        assert info.alive is False
        # 3 originally completed + 1 killed by _close_open_tools at step 8b.
        assert info.tool_events_at_death is not None
        assert len(info.tool_events_at_death) == 4
        outcomes = [ev.outcome for ev in info.tool_events_at_death]
        assert outcomes.count("ok") == 3
        assert outcomes.count("killed") == 1
        # Last event is the killed one (appended last by _close_open_tools).
        assert info.tool_events_at_death[-1].outcome == "killed"
        assert info.tool_events_at_death[-1].tool_name == "Read"


class TestF19SnapshotFlatten:
    """T3: Broker.snapshot() flattens per-teammate completed-tool-events into a sorted tuple."""

    async def test_snapshot_tool_events_default_empty_no_regression(self, broker: Broker) -> None:
        """T3 regression guard — no events means () (matches existing assertion)."""
        await broker.spawn_teammate(role="r", name=None, factory=_factory)
        snap = broker.snapshot()
        assert snap.tool_events == ()

    async def test_snapshot_flattens_live_teammate_events(self, broker: Broker) -> None:
        """D-6: live teammates' deques flatten into snapshot.tool_events."""
        from claude_crew.teammate import ToolEvent

        a = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        b = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        for tid in (a, b):
            tm = broker._teammates[tid]
            for i in range(3):
                tm._completed_tool_events.append(ToolEvent(
                    teammate_id=tid, tool_name="Bash", tool_use_id=f"{tid}-{i}",
                    started_at_wallclock=1.0 + i, finished_at_wallclock=1.5 + i,
                    duration_seconds=0.5, outcome="ok",
                    args_summary=None, error_summary=None, redaction_version="v1",
                ))

        snap = broker.snapshot()

        assert len(snap.tool_events) == 6
        assert all(isinstance(ev, ToolEvent) for ev in snap.tool_events)
        teammate_ids = {ev.teammate_id for ev in snap.tool_events}
        assert teammate_ids == {a, b}

    async def test_snapshot_includes_tombstoned_at_death_events(self, broker: Broker) -> None:
        """D-7: tombstoned teammates contribute via tool_events_at_death."""
        from claude_crew.teammate import ToolEvent

        # Alive teammate with 3 events.
        alive = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        for i in range(3):
            broker._teammates[alive]._completed_tool_events.append(ToolEvent(
                teammate_id=alive, tool_name="Bash", tool_use_id=f"a-{i}",
                started_at_wallclock=10.0 + i, finished_at_wallclock=10.5 + i,
                duration_seconds=0.5, outcome="ok",
                args_summary=None, error_summary=None, redaction_version="v1",
            ))

        # Dead teammate with 2 events (populated, then killed).
        dead = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        for i in range(2):
            broker._teammates[dead]._completed_tool_events.append(ToolEvent(
                teammate_id=dead, tool_name="Read", tool_use_id=f"d-{i}",
                started_at_wallclock=5.0 + i, finished_at_wallclock=5.5 + i,
                duration_seconds=0.5, outcome="ok",
                args_summary=None, error_summary=None, redaction_version="v1",
            ))
        await broker.kill_teammate(dead)
        await asyncio.sleep(0.05)

        snap = broker.snapshot()
        assert len(snap.tool_events) == 5  # 3 alive + 2 dead

    async def test_snapshot_tool_events_sorted_stable_by_timestamp(self, broker: Broker) -> None:
        """D-6: stable sort by finished_at_wallclock asc."""
        from claude_crew.teammate import ToolEvent

        a = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        b = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        c = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        # Mixed timestamps across teammates; two events at exactly 2.0.
        plan = [
            (a, 1.0), (b, 3.0), (c, 2.0), (a, 2.0), (b, 1.5),
        ]
        for tid, t in plan:
            broker._teammates[tid]._completed_tool_events.append(ToolEvent(
                teammate_id=tid, tool_name="Bash", tool_use_id=f"{tid}-{t}",
                started_at_wallclock=t - 0.1, finished_at_wallclock=t,
                duration_seconds=0.1, outcome="ok",
                args_summary=None, error_summary=None, redaction_version="v1",
            ))

        snap = broker.snapshot()
        ts = [ev.finished_at_wallclock for ev in snap.tool_events]
        assert ts == [1.0, 1.5, 2.0, 2.0, 3.0]

    async def test_snapshot_construction_under_5ms_at_design_scale(self, broker: Broker) -> None:
        """SC-10 microbenchmark: 5 teammates × 200 events each = 1000-event snapshot.

        Bound: median <5ms, p95 <10ms across 10 calls.
        """
        from claude_crew.teammate import ToolEvent

        for _ in range(5):
            tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
            tm = broker._teammates[tid]
            for i in range(200):
                tm._completed_tool_events.append(ToolEvent(
                    teammate_id=tid, tool_name="Bash", tool_use_id=f"{tid}-{i}",
                    started_at_wallclock=float(i), finished_at_wallclock=float(i) + 0.1,
                    duration_seconds=0.1, outcome="ok",
                    args_summary=None, error_summary=None, redaction_version="v1",
                ))

        durations: list[float] = []
        for _ in range(10):
            t0 = time.perf_counter()
            snap = broker.snapshot()
            durations.append(time.perf_counter() - t0)
            assert len(snap.tool_events) == 1000

        durations.sort()
        median = durations[5]
        p95 = durations[-1]
        # Generous bounds — gives headroom for slow CI without making the test useless.
        assert median < 0.005, f"median snapshot construction {median*1000:.2f}ms > 5ms"
        assert p95 < 0.010, f"p95 snapshot construction {p95*1000:.2f}ms > 10ms"


# ---------- broker config snapshot (ui-agent-transparency) ----------
#
# AT1-5, AT8: verify Broker._snapshot_config, spawn_teammate(agent_def_resolver=...),
# and get_teammate_status config key presence/absence.


import types as _types  # used by _make_agent_def below


def _make_agent_def(**kwargs) -> object:
    """Build a lightweight AgentDefinition stand-in for broker snapshot tests.

    Uses SimpleNamespace so _snapshot_config's getattr calls work without
    importing the real claude_agent_sdk.types.AgentDefinition.  Fields not
    supplied default to the natural absent value (None / empty).
    """
    defaults = {
        "model": None,
        "tools": [],
        "disallowedTools": None,
        "skills": None,
        "permissionMode": None,
        "mcpServers": None,
        "prompt": None,
        "effort": None,
    }
    defaults.update(kwargs)
    return _types.SimpleNamespace(**defaults)


class TestConfigSnapshot:
    """AT1-5, AT8: broker config snapshot via agent_def_resolver."""

    # ---------- AT1 ----------

    async def test_config_block_populated_from_agent_def(self, broker: Broker) -> None:
        """AT1: tools/skills/permissionMode/disallowed_tools round-trip through status."""
        agent_def = _make_agent_def(
            tools=["Bash", "Read"],
            skills=["sdd-workflow"],
            permissionMode="bypassPermissions",
            disallowedTools=["WebFetch"],
        )
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            agent_def_resolver=resolver,
        )
        status = broker.get_teammate_status(tid)

        assert "config" in status
        cfg = status["config"]
        assert cfg["tools"] == ["Bash", "Read"]
        assert cfg["skills"] == ["sdd-workflow"]
        assert cfg["permission_mode"] == "bypassPermissions"
        assert cfg["disallowed_tools"] == ["WebFetch"]

    async def test_permission_mode_kwarg_overrides_agent_def(self, broker: Broker) -> None:
        """AT1 extension: spawn-time kwarg override takes precedence over AgentDefinition.permissionMode."""
        agent_def = _make_agent_def(permissionMode="default")
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            permission_mode="bypassPermissions",
            agent_def_resolver=resolver,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["permission_mode"] == "bypassPermissions"

    async def test_effort_kwarg_overrides_agent_def(self, broker: Broker) -> None:
        """AT1 extension: spawn-time effort kwarg overrides AgentDefinition.effort.

        Also asserts effort provenance fields: dashboard surfaces both
        ``what we asked for`` and ``what actually ran with``.
        """
        agent_def = _make_agent_def(effort="low")
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            effort="high",
            agent_def_resolver=resolver,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["effort"] == "high"
        assert cfg["effort_requested"] == "high"
        assert cfg["effort_pack_default"] == "low"

    async def test_effort_falls_back_to_agent_def_when_no_kwarg(self, broker: Broker) -> None:
        """AT1 extension: AgentDefinition.effort used when no kwarg override supplied."""
        agent_def = _make_agent_def(effort="medium")
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            agent_def_resolver=resolver,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["effort"] == "medium"
        assert cfg["effort_requested"] is None
        assert cfg["effort_pack_default"] == "medium"

    async def test_effort_provenance_null_when_no_pack_default_and_no_kwarg(
        self, broker: Broker
    ) -> None:
        """Effort provenance: both fields null when neither input present."""
        agent_def = _make_agent_def()  # effort=None
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            agent_def_resolver=resolver,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["effort"] is None
        assert cfg["effort_requested"] is None
        assert cfg["effort_pack_default"] is None

    async def test_effort_provenance_kwarg_only_no_pack_default(
        self, broker: Broker
    ) -> None:
        """Effort provenance: kwarg sole source — pack default null, requested == resolved."""
        agent_def = _make_agent_def()  # effort=None
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            effort="high",
            agent_def_resolver=resolver,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["effort"] == "high"
        assert cfg["effort_requested"] == "high"
        assert cfg["effort_pack_default"] is None

    async def test_effort_provenance_kwarg_matches_pack_default(
        self, broker: Broker
    ) -> None:
        """Effort provenance: operator override matches pack default (the
        ``EffortValue`` fallback branch — nothing to flag, render plain).

        All three provenance fields land on the same value.
        """
        agent_def = _make_agent_def(effort="medium")
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            effort="medium",
            agent_def_resolver=resolver,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["effort"] == "medium"
        assert cfg["effort_requested"] == "medium"
        assert cfg["effort_pack_default"] == "medium"

    # ---------- AT2 ----------

    async def test_system_prompt_round_trips_verbatim(self, broker: Broker) -> None:
        """AT2: full system prompt string is preserved in config.system_prompt."""
        full_prompt = "You are a specialist.\n\nDo only this task.\n" * 20  # long-ish
        agent_def = _make_agent_def(prompt=full_prompt)
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            agent_def_resolver=resolver,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["system_prompt"] == full_prompt

    async def test_no_system_prompt_gives_none(self, broker: Broker) -> None:
        """AT2 edge: pack with no prompt → config.system_prompt is None."""
        agent_def = _make_agent_def(prompt=None)
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            agent_def_resolver=resolver,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["system_prompt"] is None

    # ---------- AT3 ----------

    async def test_mcp_servers_bare_string_entries_pass_through(self, broker: Broker) -> None:
        """AT3: bare string MCP server names pass through unchanged."""
        agent_def = _make_agent_def(mcpServers=["github", "jira"])
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            agent_def_resolver=resolver,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["mcp_servers"] == ["github", "jira"]

    async def test_mcp_servers_dict_entry_name_key_only(self, broker: Broker) -> None:
        """AT3: dict-form MCP entries expose only the 'name' key; api_key not present."""
        agent_def = _make_agent_def(
            mcpServers=[
                {"name": "my-server", "type": "stdio", "api_key": "s3cr3t", "command": "mcp-my-server"},
                "plain-server",
            ]
        )
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            agent_def_resolver=resolver,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["mcp_servers"] == ["my-server", "plain-server"]
        # Verify no secret values appear anywhere in the config dict
        import json
        cfg_json = json.dumps(cfg)
        assert "s3cr3t" not in cfg_json

    async def test_mcp_dict_without_name_key_falls_back_to_unnamed(self, broker: Broker) -> None:
        """AT3: dict-form MCP entry with no 'name' key → '<unnamed>'."""
        agent_def = _make_agent_def(
            mcpServers=[{"type": "stdio", "command": "tool", "token": "abc123"}]
        )
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            agent_def_resolver=resolver,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["mcp_servers"] == ["<unnamed>"]
        import json
        assert "abc123" not in json.dumps(cfg)

    # ---------- AT4 ----------

    async def test_empty_tools_list_preserved(self, broker: Broker) -> None:
        """AT4: tools=[] is present as empty list, not absent or None."""
        agent_def = _make_agent_def(tools=[])
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            agent_def_resolver=resolver,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert "tools" in cfg
        assert cfg["tools"] == []

    # ---------- AT5 ----------

    async def test_config_retained_after_kill(self, broker: Broker) -> None:
        """AT5: tombstoned teammate retains its config snapshot."""
        agent_def = _make_agent_def(tools=["Bash"], prompt="stay-after-death")
        resolver = lambda role: agent_def  # noqa: E731

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=_factory,
            agent_def_resolver=resolver,
        )
        alive_cfg = broker.get_teammate_status(tid)["config"]
        assert alive_cfg is not None

        await broker.kill_teammate(tid)

        dead_status = broker.get_teammate_status(tid)
        assert dead_status["alive"] is False
        assert "config" in dead_status
        assert dead_status["config"]["tools"] == ["Bash"]
        assert dead_status["config"]["system_prompt"] == "stay-after-death"

    # ---------- AT8 ----------

    async def test_config_absent_when_no_agent_def_resolver(self, broker: Broker) -> None:
        """AT8: no agent_def_resolver → config key absent from get_teammate_status."""
        tid = await broker.spawn_teammate(
            role="role_not_in_pack", name=None, factory=_factory,
            # no agent_def_resolver supplied
        )
        status = broker.get_teammate_status(tid)
        assert "config" not in status

    async def test_config_absent_when_resolver_returns_none(self, broker: Broker) -> None:
        """AT8: resolver returning None → config key absent (role not in pack)."""
        resolver = lambda role: None  # noqa: E731  # simulates role_not_in_pack

        tid = await broker.spawn_teammate(
            role="role_not_in_pack", name=None, factory=_factory,
            agent_def_resolver=resolver,
        )
        status = broker.get_teammate_status(tid)
        assert "config" not in status

    async def test_config_absent_for_dead_teammate_when_no_resolver(
        self, broker: Broker
    ) -> None:
        """AT8 + AT5 combined: dead teammate with no resolver has no config key."""
        tid = await broker.spawn_teammate(
            role="role_not_in_pack", name=None, factory=_factory,
        )
        await broker.kill_teammate(tid)
        dead_status = broker.get_teammate_status(tid)
        assert dead_status["alive"] is False
        assert "config" not in dead_status

    # ---------- _snapshot_config unit tests ----------

    def test_snapshot_config_returns_none_for_none_agent_def(self, broker: Broker) -> None:
        """_snapshot_config returns None directly when agent_def is None."""
        result = broker._snapshot_config(
            agent_def=None, effort=None, permission_mode=None
        )
        assert result is None

    def test_snapshot_config_all_fields_present(self, broker: Broker) -> None:
        """_snapshot_config builds a complete dict with all expected keys."""
        agent_def = _make_agent_def(
            model="claude-sonnet-4-6",
            tools=["Bash"],
            disallowedTools=["WebFetch"],
            skills=["sdd-workflow"],
            permissionMode="default",
            mcpServers=["github"],
            prompt="Be helpful.",
            effort="medium",
        )
        cfg = broker._snapshot_config(
            agent_def=agent_def, effort=None, permission_mode=None
        )
        assert cfg is not None
        expected_keys = {
            "model", "tools", "disallowed_tools", "skills",
            "permission_mode", "mcp_servers", "system_prompt", "effort",
            "effort_requested", "effort_pack_default",
            "extra_tools", "extra_skills",
        }
        assert set(cfg.keys()) == expected_keys

    async def test_factory_attached_resolver_is_used_when_arg_omitted(
        self, broker: Broker
    ) -> None:
        """Production wiring path: if the caller does not pass agent_def_resolver,
        the broker falls back to factory.agent_def_resolver. Without this,
        production teammates spawned through default_factory get no `config`
        block and dashboard chips render empty.
        """
        agent_def = _make_agent_def(tools=["Bash", "Read"], skills=["sdd"])

        def factory_with_resolver(id, name, role, **kwargs):
            return _factory(id, name, role, **kwargs)

        factory_with_resolver.agent_def_resolver = lambda role: agent_def

        tid = await broker.spawn_teammate(
            role="scout", name=None, factory=factory_with_resolver,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg is not None, "config must be populated via factory-attached resolver"
        assert cfg["tools"] == ["Bash", "Read"]
        assert cfg["skills"] == ["sdd"]

    async def test_explicit_resolver_overrides_factory_attached(
        self, broker: Broker
    ) -> None:
        """Explicit-arg resolver takes precedence over factory-attached resolver."""
        factory_def = _make_agent_def(tools=["Bash"])
        explicit_def = _make_agent_def(tools=["Read", "Write"])

        def factory_with_resolver(id, name, role, **kwargs):
            return _factory(id, name, role, **kwargs)

        factory_with_resolver.agent_def_resolver = lambda role: factory_def

        tid = await broker.spawn_teammate(
            role="scout", name=None, factory=factory_with_resolver,
            agent_def_resolver=lambda role: explicit_def,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["tools"] == ["Read", "Write"]


# ---------- Extra tools / skills (AT-1, AT-2, AT-4, AT-5, AT-6) ----------


class TestExtraToolsAndSkills:
    """Acceptance tests for spawn-time extra_tools and extra_skills.

    AT-1: additive merge + snapshot
    AT-2: dedup (extra already in pack → not doubled, not in net-new)
    AT-4: skills additive merge
    AT-5: backward compat (no extras → identical behavior)
    AT-6: no pack entry + extras → config block returned (not null)
    """

    async def test_extra_tools_are_additive(self, broker: Broker) -> None:
        """AT-1: pack tools + extra_tools → merged effective list; net-new in extra_tools."""
        agent_def = _make_agent_def(tools=["Read", "Grep"])

        tid = await broker.spawn_teammate(
            role="planner", name=None, factory=_factory,
            agent_def_resolver=lambda role: agent_def,
            extra_tools=["mcp__knowledge-graph__repo_map"],
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["tools"] == ["Read", "Grep", "mcp__knowledge-graph__repo_map"], (
            "effective tools must be insertion-order union of pack + extras"
        )
        assert cfg["extra_tools"] == ["mcp__knowledge-graph__repo_map"], (
            "net-new extra_tools must be the tools not already in the pack"
        )

    async def test_extra_tools_dedup_already_in_pack(self, broker: Broker) -> None:
        """AT-2: extra tool already in pack → no duplicate in effective list; empty net-new."""
        agent_def = _make_agent_def(tools=["Read", "Grep"])

        tid = await broker.spawn_teammate(
            role="planner", name=None, factory=_factory,
            agent_def_resolver=lambda role: agent_def,
            extra_tools=["Read"],
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["tools"] == ["Read", "Grep"], (
            "Read was already in pack — no duplicate in effective list"
        )
        assert cfg["extra_tools"] == [], (
            "Read was already in pack; net-new extra_tools must be empty"
        )

    async def test_extra_skills_are_additive(self, broker: Broker) -> None:
        """AT-4: pack skills + extra_skills → merged effective list; net-new in extra_skills."""
        agent_def = _make_agent_def(tools=["Read"], skills=["sdd-workflow"])

        tid = await broker.spawn_teammate(
            role="planner", name=None, factory=_factory,
            agent_def_resolver=lambda role: agent_def,
            extra_skills=["repo-reactor:plan-feature"],
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert "sdd-workflow" in cfg["skills"]
        assert "repo-reactor:plan-feature" in cfg["skills"]
        assert cfg["extra_skills"] == ["repo-reactor:plan-feature"], (
            "net-new extra_skills must contain the new skill"
        )

    async def test_no_extras_backward_compatible(self, broker: Broker) -> None:
        """AT-5: extra_tools=None, extra_skills=None → same behavior as pre-feature spawn."""
        agent_def = _make_agent_def(tools=["Read", "Grep"], skills=["sdd-workflow"])

        tid = await broker.spawn_teammate(
            role="planner", name=None, factory=_factory,
            agent_def_resolver=lambda role: agent_def,
            extra_tools=None,
            extra_skills=None,
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["tools"] == ["Read", "Grep"]
        assert cfg["skills"] == ["sdd-workflow"]
        assert cfg["extra_tools"] == []
        assert cfg["extra_skills"] == []

    async def test_empty_list_extras_backward_compatible(self, broker: Broker) -> None:
        """AT-5 variant: extra_tools=[], extra_skills=[] → same behavior as None."""
        agent_def = _make_agent_def(tools=["Read"])

        tid = await broker.spawn_teammate(
            role="planner", name=None, factory=_factory,
            agent_def_resolver=lambda role: agent_def,
            extra_tools=[],
            extra_skills=[],
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["tools"] == ["Read"]
        assert cfg["extra_tools"] == []
        assert cfg["extra_skills"] == []

    async def test_no_pack_entry_with_extras_returns_config(self, broker: Broker) -> None:
        """AT-6: role not in pack + extra_tools → config block returned (not null)."""
        tid = await broker.spawn_teammate(
            role="unknown-role", name=None, factory=_factory,
            # No agent_def_resolver → broker gets no pack entry for this role
            extra_tools=["Read", "Grep"],
        )
        status = broker.get_teammate_status(tid)
        assert "config" in status, (
            "config block must be present even when role has no pack entry, "
            "as long as extras are provided"
        )
        cfg = status["config"]
        assert cfg["tools"] == ["Read", "Grep"], (
            "tools must equal the provided extras when there is no pack baseline"
        )

    async def test_extra_tools_duplicate_in_request_deduped(self, broker: Broker) -> None:
        """Edge case: duplicate entries in extra_tools → deduped in effective list."""
        agent_def = _make_agent_def(tools=["Bash"])

        tid = await broker.spawn_teammate(
            role="planner", name=None, factory=_factory,
            agent_def_resolver=lambda role: agent_def,
            extra_tools=["Read", "Read"],
        )
        cfg = broker.get_teammate_status(tid)["config"]
        assert cfg["tools"].count("Read") == 1, "duplicate in extra_tools must be deduped"
        assert cfg["extra_tools"].count("Read") == 1
