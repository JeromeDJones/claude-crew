"""Edge-routing tests for Broker.send and Broker.send_scoped (AT#1–#5).

All tests run in stub mode (CLAUDE_CREW_TEAMMATE_MODE=stub, set by conftest).
A real Broker() instance is constructed for each test; topology is recorded
directly via broker.record_topology() so tests don't depend on the full
instantiate_shape machinery.
"""

from __future__ import annotations

import asyncio
import collections
import time
from typing import Any

import pytest

from claude_crew.broker import (
    Broker,
    LEAD_ID,
    Topology,
    UnauthorizedEdgeError,
)
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import Teammate


# ---------------------------------------------------------------------------
# Minimal noop teammate (mirrors the _NoopTeammate pattern in test_broker.py)
# ---------------------------------------------------------------------------

_SENTINEL: object = object()


class _NoopTeammate(Teammate):
    """A teammate that drains its inbox without acting on messages."""

    def __init__(self, id: str, name: str, role: str) -> None:
        self.id = id
        self.name = name
        self.role = role
        self._inbox: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self.received: list[Envelope] = []
        # status_snapshot fields required by the base class contract
        self._last_activity_monotonic = time.monotonic()
        self._last_activity_wallclock = time.time()
        self._current_turn_started_at_wallclock: float | None = None
        self._broker = None
        self._tool_uses: dict = {}
        self._recently_closed_tool_use_ids: collections.deque = collections.deque(
            maxlen=64
        )
        self._last_tool_completed = None
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
                    break
                self.received.append(msg)
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        if self._inbox is not None:
            await self._inbox.put(_SENTINEL)
        if self._task is not None:
            await self._task


def _factory(id: str, name: str, role: str, **_kwargs: Any) -> _NoopTeammate:
    return _NoopTeammate(id=id, name=name, role=role)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def broker() -> Broker:  # type: ignore[misc]
    b = Broker()
    yield b
    await b.shutdown_all()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _topo(edges: list[tuple[str, str, str]], slot_to_teammate: dict[str, str]) -> Topology:
    return Topology(
        shape_name="test",
        edges=tuple(edges),
        slot_to_teammate=slot_to_teammate,
    )


async def _spawn(broker: Broker, role: str = "r") -> str:
    return await broker.spawn_teammate(role=role, name=role, factory=_factory)


def _env(sender: str, recipient: str, payload: Any = None) -> Envelope:
    return Envelope(
        id=new_message_id(),
        seq=0,
        sender=sender,
        recipient=recipient,
        timestamp=time.time(),
        payload=payload or {"x": 1},
    )


# ---------------------------------------------------------------------------
# AT#1 — gated routing
# ---------------------------------------------------------------------------

class TestGatedRouting:
    """AT#1: gated edge routes to LEAD as wrapper; recipient inbox receives nothing."""

    async def test_gated_delivers_wrapper_to_lead(self, broker: Broker) -> None:
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "gated")], {"a": a_id, "b": b_id}))

        msg_payload = {"msg": "hello"}
        await broker.send(_env(a_id, b_id, msg_payload))

        lead_msgs = broker.get_messages(LEAD_ID)
        assert len(lead_msgs) == 1
        wrapper = lead_msgs[0]
        assert wrapper.recipient == LEAD_ID
        assert wrapper.payload["gated_for"] == b_id
        assert wrapper.payload["from"] == a_id
        assert wrapper.payload["payload"] == msg_payload

    async def test_gated_does_not_deliver_to_recipient_log(self, broker: Broker) -> None:
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "gated")], {"a": a_id, "b": b_id}))

        await broker.send(_env(a_id, b_id))

        # get_messages(b_id) scans _log for recipient==b_id; should be empty
        b_msgs = broker.get_messages(b_id)
        assert b_msgs == []

    async def test_gated_wrapper_has_seq_assigned(self, broker: Broker) -> None:
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "gated")], {"a": a_id, "b": b_id}))

        await broker.send(_env(a_id, b_id))

        lead_msgs = broker.get_messages(LEAD_ID)
        assert len(lead_msgs) == 1
        assert lead_msgs[0].seq >= 1


# ---------------------------------------------------------------------------
# AT#2 — tee routing
# ---------------------------------------------------------------------------

class TestTeeRouting:
    """AT#2: tee delivers original to recipient inbox AND cc envelope to LEAD;
    both appear in the broker message log.
    """

    async def test_tee_delivers_to_recipient_log(self, broker: Broker) -> None:
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "tee")], {"a": a_id, "b": b_id}))

        payload = {"msg": "tee-test"}
        await broker.send(_env(a_id, b_id, payload))

        b_msgs = broker.get_messages(b_id)
        assert len(b_msgs) == 1
        assert b_msgs[0].payload == payload

    async def test_tee_delivers_cc_to_lead(self, broker: Broker) -> None:
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "tee")], {"a": a_id, "b": b_id}))

        original_id = new_message_id()
        payload = {"msg": "tee-cc"}
        env = Envelope(
            id=original_id, seq=0, sender=a_id, recipient=b_id,
            timestamp=time.time(), payload=payload,
        )
        await broker.send(env)

        lead_msgs = broker.get_messages(LEAD_ID)
        assert len(lead_msgs) == 1
        cc = lead_msgs[0]
        assert cc.recipient == LEAD_ID
        assert cc.payload["cc_of"] == original_id
        assert cc.payload["from"] == a_id
        assert cc.payload["to"] == b_id
        assert cc.payload["payload"] == payload

    async def test_tee_both_appear_in_broker_log(self, broker: Broker) -> None:
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "tee")], {"a": a_id, "b": b_id}))

        await broker.send(_env(a_id, b_id))

        # Both original (recipient=b) and cc (recipient=LEAD) must be in _log
        all_recipients = {e.recipient for e in broker._log}  # type: ignore[attr-defined]
        assert b_id in all_recipients
        assert LEAD_ID in all_recipients
        assert len(broker._log) == 2  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# AT#3 — direct routing
# ---------------------------------------------------------------------------

class TestDirectRouting:
    """AT#3: direct delivers to recipient inbox and log; NOT lead-bound."""

    async def test_direct_delivers_to_recipient_log(self, broker: Broker) -> None:
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))

        payload = {"msg": "direct"}
        await broker.send(_env(a_id, b_id, payload))

        b_msgs = broker.get_messages(b_id)
        assert len(b_msgs) == 1
        assert b_msgs[0].payload == payload

    async def test_direct_not_lead_bound(self, broker: Broker) -> None:
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))

        await broker.send(_env(a_id, b_id))

        lead_msgs = broker.get_messages(LEAD_ID)
        assert lead_msgs == []

    async def test_direct_in_broker_log(self, broker: Broker) -> None:
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))

        await broker.send(_env(a_id, b_id))

        # Original appears in log with recipient=b
        b_msgs = broker.get_messages(b_id)
        assert len(b_msgs) == 1


# ---------------------------------------------------------------------------
# AT#4 — no-edge gated fallback
# ---------------------------------------------------------------------------

class TestNoEdgeFallback:
    """AT#4: no forward edge → gated fallback; reproduces lead-routed behavior.
    Also covers the no-topology case (Edge Cases in spec).
    """

    async def test_no_edge_routes_gated_when_topology_exists(
        self, broker: Broker
    ) -> None:
        """Topology present but no a→c edge → gated fallback."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        c_id = await _spawn(broker, "c")
        # Topology has a→b edge only; no a→c edge; c IS in slot_to_teammate
        broker.record_topology(
            _topo([("a", "b", "gated")], {"a": a_id, "b": b_id, "c": c_id})
        )

        await broker.send(_env(a_id, c_id))

        # c's log is empty (gated, not delivered to c)
        assert broker.get_messages(c_id) == []

        # LEAD gets gated wrapper
        lead_msgs = broker.get_messages(LEAD_ID)
        assert len(lead_msgs) == 1
        assert lead_msgs[0].payload["gated_for"] == c_id

    async def test_no_topology_routes_gated(self, broker: Broker) -> None:
        """No topology at all → gated fallback for teammate→teammate."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        await broker.send(_env(a_id, b_id))

        # b's log is empty
        assert broker.get_messages(b_id) == []

        # LEAD gets gated wrapper
        lead_msgs = broker.get_messages(LEAD_ID)
        assert len(lead_msgs) == 1
        wrapper = lead_msgs[0]
        assert wrapper.payload["gated_for"] == b_id
        assert wrapper.payload["from"] == a_id

    async def test_gated_fallback_original_id_deduped(self, broker: Broker) -> None:
        """Gated fallback marks the original id as seen; retry is dropped."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        mid = new_message_id()
        env = Envelope(id=mid, seq=0, sender=a_id, recipient=b_id,
                       timestamp=time.time(), payload={"x": 1})
        r1 = await broker.send(env)
        # Same id again — should be deduped (returns None)
        r2 = await broker.send(env)

        assert r1 is not None
        assert r2 is None
        # Only one lead message (the first gated wrapper)
        assert len(broker.get_messages(LEAD_ID)) == 1


# ---------------------------------------------------------------------------
# AT#5 — scoped authorization
# ---------------------------------------------------------------------------

class TestScopedAuthorization:
    """AT#5: send_scoped raises UnauthorizedEdgeError for non-declared recipient;
    authorize_send(sender, LEAD) never raises.
    """

    async def test_send_scoped_rejects_non_declared_recipient(
        self, broker: Broker
    ) -> None:
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        c_id = await _spawn(broker, "c")
        # Topology: a→b only; c is in slot_to_teammate but has no in-edge from a
        broker.record_topology(
            _topo([("a", "b", "direct")], {"a": a_id, "b": b_id, "c": c_id})
        )

        with pytest.raises(UnauthorizedEdgeError):
            await broker.send_scoped(sender_id=a_id, recipient=c_id, payload={"x": 1})

        # Nothing enqueued to c
        assert broker.get_messages(c_id) == []

    async def test_send_scoped_rejects_undeclared_teammate_no_edge(
        self, broker: Broker
    ) -> None:
        """c is not even in the topology's slot_to_teammate."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        c_id = await _spawn(broker, "c")
        broker.record_topology(
            _topo([("a", "b", "direct")], {"a": a_id, "b": b_id})
            # c is NOT in slot_to_teammate at all
        )

        with pytest.raises(UnauthorizedEdgeError):
            await broker.send_scoped(sender_id=a_id, recipient=c_id, payload={"x": 1})

        assert broker.get_messages(c_id) == []

    async def test_authorize_send_to_lead_never_raises(self, broker: Broker) -> None:
        """authorize_send(sender, LEAD_ID) is always a no-op."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(
            _topo([("a", "b", "direct")], {"a": a_id, "b": b_id})
        )
        # Must not raise even though there is no a→LEAD forward edge in topology
        broker.authorize_send(a_id, LEAD_ID)

    async def test_authorize_send_raises_for_no_topology(
        self, broker: Broker
    ) -> None:
        """No topology → authorize_send raises UnauthorizedEdgeError."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        with pytest.raises(UnauthorizedEdgeError):
            broker.authorize_send(a_id, b_id)

    async def test_send_scoped_delivers_to_declared_neighbor(
        self, broker: Broker
    ) -> None:
        """send_scoped succeeds and delivers when forward edge exists."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(
            _topo([("a", "b", "direct")], {"a": a_id, "b": b_id})
        )

        result = await broker.send_scoped(
            sender_id=a_id, recipient=b_id, payload={"msg": "scoped"}
        )
        assert result is not None

        b_msgs = broker.get_messages(b_id)
        assert len(b_msgs) == 1
        assert b_msgs[0].payload == {"msg": "scoped"}

    async def test_send_scoped_resolves_slot_name(self, broker: Broker) -> None:
        """send_scoped accepts a slot name and resolves it to the teammate_id."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(
            _topo([("a", "b", "direct")], {"a": a_id, "b": b_id})
        )

        # Pass slot name "b" instead of b_id
        result = await broker.send_scoped(
            sender_id=a_id, recipient="b", payload={"msg": "slot-resolved"}
        )
        assert result is not None

        b_msgs = broker.get_messages(b_id)
        assert len(b_msgs) == 1
        assert b_msgs[0].payload == {"msg": "slot-resolved"}

    async def test_send_scoped_to_lead_always_allowed(self, broker: Broker) -> None:
        """send_scoped(sender, LEAD_ID, ...) is always permitted."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(
            _topo([("a", "b", "direct")], {"a": a_id, "b": b_id})
        )

        result = await broker.send_scoped(
            sender_id=a_id, recipient=LEAD_ID, payload={"report": "done"}
        )
        assert result is not None
        lead_msgs = broker.get_messages(LEAD_ID)
        assert len(lead_msgs) == 1
        assert lead_msgs[0].payload == {"report": "done"}
