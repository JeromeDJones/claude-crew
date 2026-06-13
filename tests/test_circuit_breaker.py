"""Circuit-breaker tests for Broker (AT#6–#7).

AT#6: budget_exceeded — when (N+1) direct messages are sent on an edge with
      CIRCUIT_BREAKER_MAX_EXCHANGES=N, the (N+1)-th routes to LEAD instead of
      the recipient; a {type:"circuit_breaker", reason:"budget_exceeded"}
      control envelope is emitted to LEAD; the edge's effective mode becomes
      "gated" (visible in topology_edge_stats).

AT#7: reciprocal exchanges below budget must NOT trip (no false deadlock) and
      must NOT reach get_messages(LEAD).  Once the per-edge exchange count
      EXCEEDS the budget, the breaker trips with reason:"budget_exceeded" and
      force-inserts the lead.  This is the regression guard for the
      scoped-send-teammate slice's AT#10 (direct ping-pong stays off the lead).

Spec amendment (2026-06-13): 2-node deadlock detection was dropped entirely.
The per-edge exchange budget is now the sole trip condition.

All tests run in stub mode (CLAUDE_CREW_TEAMMATE_MODE=stub, set by conftest).
A real Broker() instance is constructed per test; topology recorded directly
via broker.record_topology() so tests don't depend on instantiate_shape.
"""

from __future__ import annotations

import asyncio
import collections
import time
from typing import Any

import pytest

from claude_crew.broker import (
    CIRCUIT_BREAKER_MAX_EXCHANGES,
    Broker,
    EdgeStat,
    LEAD_ID,
    Topology,
)
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import Teammate


# ---------------------------------------------------------------------------
# Minimal noop teammate (mirrors _NoopTeammate in test_edge_routing.py)
# ---------------------------------------------------------------------------

_SENTINEL: object = object()


class _NoopTeammate(Teammate):
    """Drains its inbox without acting on messages."""

    def __init__(self, id: str, name: str, role: str) -> None:
        self.id = id
        self.name = name
        self.role = role
        self._inbox: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self.received: list[Envelope] = []
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

def _topo(
    edges: list[tuple[str, str, str]],
    slot_to_teammate: dict[str, str],
) -> Topology:
    return Topology(
        shape_name="test",
        edges=tuple(edges),
        slot_to_teammate=slot_to_teammate,
    )


async def _spawn(broker: Broker, role: str = "r") -> str:
    return await broker.spawn_teammate(role=role, name=role, factory=_factory)


def _env(
    sender: str,
    recipient: str,
    payload: Any = None,
) -> Envelope:
    return Envelope(
        id=new_message_id(),
        seq=0,
        sender=sender,
        recipient=recipient,
        timestamp=time.time(),
        payload=payload or {},
    )


def _cb_msgs(lead_msgs: list[Envelope]) -> list[Envelope]:
    """Filter lead messages to only circuit-breaker control envelopes."""
    return [
        m for m in lead_msgs
        if isinstance(m.payload, dict) and m.payload.get("type") == "circuit_breaker"
    ]


# ---------------------------------------------------------------------------
# AT#6 — budget_exceeded trips the circuit breaker on a single directed edge
# ---------------------------------------------------------------------------

class TestBreakerBudget:
    """AT#6: CIRCUIT_BREAKER_MAX_EXCHANGES=N → (N+1)-th message trips the edge."""

    async def test_budget_exceeded_routes_to_lead(self, broker: Broker) -> None:
        """The (N+1)-th direct message routes to LEAD; recipient gets only N."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        budget = 3
        broker._circuit_breaker_max_exchanges = budget
        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))

        # Send N messages: all should reach b directly, nothing to LEAD.
        for i in range(budget):
            await broker.send(_env(a_id, b_id, {"i": i}))

        assert len(broker.get_messages(b_id)) == budget
        assert broker.get_messages(LEAD_ID) == []

        # (N+1)-th message: should trip the breaker.
        await broker.send(_env(a_id, b_id, {"i": budget}))

        # b still has only N messages (the triggering one was rerouted).
        assert len(broker.get_messages(b_id)) == budget

        # LEAD gets: control envelope + gated wrapper for the triggering message.
        lead_msgs = broker.get_messages(LEAD_ID)
        assert len(lead_msgs) == 2

        cb_msgs = _cb_msgs(lead_msgs)
        assert len(cb_msgs) == 1, "expected one circuit_breaker control envelope"
        ctrl = cb_msgs[0]
        assert ctrl.payload["reason"] == "budget_exceeded"
        assert ctrl.payload["edge"] == ["a", "b"]
        assert ctrl.sender == "broker"
        assert ctrl.recipient == LEAD_ID

    async def test_budget_exceeded_edge_becomes_gated(self, broker: Broker) -> None:
        """After a budget trip the edge's effective mode is gated."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        broker._circuit_breaker_max_exchanges = 1
        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))

        await broker.send(_env(a_id, b_id))  # within budget
        await broker.send(_env(a_id, b_id))  # exceeds budget=1

        # Edge override should be "gated".
        assert broker._edge_overrides.get(("a", "b")) == "gated"

        # topology_edge_stats reflects the tripped state.
        snap = broker.snapshot()
        stats = {(e.from_slot, e.to_slot): e for e in snap.topology_edge_stats}
        assert ("a", "b") in stats
        stat = stats[("a", "b")]
        assert stat.mode == "gated"
        assert stat.tripped is True
        assert stat.exchanges == 2

    async def test_budget_exceeded_gated_wrapper_in_lead(self, broker: Broker) -> None:
        """The triggering message is rerouted as a gated wrapper to LEAD."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        broker._circuit_breaker_max_exchanges = 1
        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))

        await broker.send(_env(a_id, b_id, {"x": 1}))  # within budget
        await broker.send(_env(a_id, b_id, {"x": 2}))  # triggers budget

        lead_msgs = broker.get_messages(LEAD_ID)
        # One control envelope + one gated wrapper.
        assert len(lead_msgs) == 2
        gated_wrappers = [
            m for m in lead_msgs
            if isinstance(m.payload, dict) and "gated_for" in m.payload
        ]
        assert len(gated_wrappers) == 1
        wrapper = gated_wrappers[0]
        assert wrapper.payload["gated_for"] == b_id
        assert wrapper.payload["from"] == a_id
        assert wrapper.payload["payload"] == {"x": 2}

    async def test_breaker_idempotent_no_duplicate_control_envelope(
        self, broker: Broker
    ) -> None:
        """Sends after a trip route gated without emitting another control envelope."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        broker._circuit_breaker_max_exchanges = 1
        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))

        await broker.send(_env(a_id, b_id))   # within budget
        await broker.send(_env(a_id, b_id))   # trips: 1 ctrl + 1 wrapper
        await broker.send(_env(a_id, b_id))   # already gated: 1 more wrapper, no new ctrl

        lead_msgs = broker.get_messages(LEAD_ID)
        cb_envelopes = _cb_msgs(lead_msgs)
        # Only ONE control envelope despite two post-budget sends.
        assert len(cb_envelopes) == 1

    async def test_budget_exceeded_on_tee_edge(self, broker: Broker) -> None:
        """Circuit breaker also fires on tee edges, not just direct."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        broker._circuit_breaker_max_exchanges = 2
        broker.record_topology(_topo([("a", "b", "tee")], {"a": a_id, "b": b_id}))

        # Two tee messages: each should land in b's inbox + cc to LEAD.
        for _ in range(2):
            await broker.send(_env(a_id, b_id))

        assert len(broker.get_messages(b_id)) == 2
        # Two cc envelopes to LEAD from the tee delivers (no circuit_breaker yet).
        assert len(_cb_msgs(broker.get_messages(LEAD_ID))) == 0

        # 3rd message: budget exceeded → control + gated wrapper (not tee).
        await broker.send(_env(a_id, b_id, {"trig": True}))

        # b should still have only 2 messages.
        assert len(broker.get_messages(b_id)) == 2

        lead_after = broker.get_messages(LEAD_ID)
        cb = _cb_msgs(lead_after)
        assert len(cb) == 1
        assert cb[0].payload["reason"] == "budget_exceeded"

    async def test_module_constant_default_value(self) -> None:
        """CIRCUIT_BREAKER_MAX_EXCHANGES module constant is 8."""
        assert CIRCUIT_BREAKER_MAX_EXCHANGES == 8

    async def test_broker_default_budget_is_module_constant(self, broker: Broker) -> None:
        """Newly created broker uses CIRCUIT_BREAKER_MAX_EXCHANGES as its budget."""
        assert broker._circuit_breaker_max_exchanges == CIRCUIT_BREAKER_MAX_EXCHANGES

    async def test_exchange_counter_in_edge_stats(self, broker: Broker) -> None:
        """EdgeStat.exchanges reflects how many tee/direct deliveries occurred."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))

        for _ in range(3):
            await broker.send(_env(a_id, b_id))

        snap = broker.snapshot()
        stats = {(e.from_slot, e.to_slot): e for e in snap.topology_edge_stats}
        assert stats[("a", "b")].exchanges == 3
        assert stats[("a", "b")].tripped is False


# ---------------------------------------------------------------------------
# AT#7 — reciprocal exchanges below budget stay off LEAD; above budget trips
# ---------------------------------------------------------------------------

class TestBreakerReciprocalExchanges:
    """AT#7: reciprocal direct edges; budget is the sole trip condition.

    Validates:
    1. A ping-pong sequence below the budget delivers ALL messages to
       recipients' inboxes and NONE to get_messages(LEAD) (no false deadlock).
    2. Once either per-edge counter exceeds the budget, the breaker trips
       with reason:"budget_exceeded" and force-inserts the lead.

    This is also the regression guard for the scoped-send-teammate slice's
    AT#10: direct ping-pong must stay off the lead while under budget.
    """

    async def test_reciprocal_below_budget_stays_off_lead(
        self, broker: Broker
    ) -> None:
        """a→b→a→b sequence below budget: all direct, none reach LEAD."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        budget = 4
        broker._circuit_breaker_max_exchanges = budget
        broker.record_topology(_topo(
            [("a", "b", "direct"), ("b", "a", "direct")],
            {"a": a_id, "b": b_id},
        ))

        # Send 2 hops in each direction (4 total, within budget for both edges).
        await broker.send(_env(a_id, b_id, {"hop": 1}))  # a→b: (a,b) count=1
        await broker.send(_env(b_id, a_id, {"hop": 2}))  # b→a: (b,a) count=1
        await broker.send(_env(a_id, b_id, {"hop": 3}))  # a→b: (a,b) count=2
        await broker.send(_env(b_id, a_id, {"hop": 4}))  # b→a: (b,a) count=2

        # All four messages delivered directly — none to LEAD.
        assert broker.get_messages(LEAD_ID) == [], "no messages should reach LEAD below budget"

        # b received hops 1 and 3 (the a→b messages).
        b_msgs = broker.get_messages(b_id)
        assert len(b_msgs) == 2

        # a received hops 2 and 4 (the b→a messages).
        a_msgs = broker.get_messages(a_id)
        assert len(a_msgs) == 2

    async def test_reciprocal_below_budget_no_breaker_trip(
        self, broker: Broker
    ) -> None:
        """No circuit_breaker envelope should appear below budget."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        broker._circuit_breaker_max_exchanges = 3
        broker.record_topology(_topo(
            [("a", "b", "direct"), ("b", "a", "direct")],
            {"a": a_id, "b": b_id},
        ))

        # 3 hops in each direction — each edge at exactly budget (not exceeded).
        for _ in range(3):
            await broker.send(_env(a_id, b_id))
            await broker.send(_env(b_id, a_id))

        # No circuit-breaker control envelopes at all.
        assert _cb_msgs(broker.get_messages(LEAD_ID)) == []
        # Neither edge has been overridden.
        assert ("a", "b") not in broker._edge_overrides
        assert ("b", "a") not in broker._edge_overrides

    async def test_reciprocal_ab_edge_trips_when_budget_exceeded(
        self, broker: Broker
    ) -> None:
        """a→b edge trips independently once its count exceeds the budget."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        budget = 2
        broker._circuit_breaker_max_exchanges = budget
        broker.record_topology(_topo(
            [("a", "b", "direct"), ("b", "a", "direct")],
            {"a": a_id, "b": b_id},
        ))

        # N a→b messages (within budget).
        for _ in range(budget):
            await broker.send(_env(a_id, b_id))

        assert broker.get_messages(LEAD_ID) == []

        # (N+1)-th a→b message: trips the a→b edge.
        await broker.send(_env(a_id, b_id, {"trip": True}))

        lead_msgs = broker.get_messages(LEAD_ID)
        cb = _cb_msgs(lead_msgs)
        assert len(cb) == 1
        assert cb[0].payload["reason"] == "budget_exceeded"
        assert cb[0].payload["edge"] == ["a", "b"]

        # a→b is gated; b→a is still direct (independent counters).
        assert broker._edge_overrides.get(("a", "b")) == "gated"
        assert ("b", "a") not in broker._edge_overrides

    async def test_reciprocal_ba_edge_trips_independently(
        self, broker: Broker
    ) -> None:
        """b→a edge has its own independent counter and trips separately."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        budget = 2
        broker._circuit_breaker_max_exchanges = budget
        broker.record_topology(_topo(
            [("a", "b", "direct"), ("b", "a", "direct")],
            {"a": a_id, "b": b_id},
        ))

        # N b→a messages (within budget).
        for _ in range(budget):
            await broker.send(_env(b_id, a_id))

        assert _cb_msgs(broker.get_messages(LEAD_ID)) == []

        # (N+1)-th b→a message: trips the b→a edge.
        await broker.send(_env(b_id, a_id, {"trip": True}))

        cb = _cb_msgs(broker.get_messages(LEAD_ID))
        assert len(cb) == 1
        assert cb[0].payload["edge"] == ["b", "a"]
        assert cb[0].payload["reason"] == "budget_exceeded"

        # b→a is gated; a→b is still direct.
        assert broker._edge_overrides.get(("b", "a")) == "gated"
        assert ("a", "b") not in broker._edge_overrides

    async def test_reciprocal_no_pending_state_left_on_broker(
        self, broker: Broker
    ) -> None:
        """After ping-pong, no _edge_pending attribute should exist (was dropped)."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        broker.record_topology(_topo(
            [("a", "b", "direct"), ("b", "a", "direct")],
            {"a": a_id, "b": b_id},
        ))

        await broker.send(_env(a_id, b_id))
        await broker.send(_env(b_id, a_id))

        # Broker must NOT have _edge_pending (it was removed in spec amendment).
        assert not hasattr(broker, "_edge_pending"), (
            "_edge_pending should not exist — deadlock detector was dropped"
        )

    async def test_edge_stats_exchanges_counted_per_direction(
        self, broker: Broker
    ) -> None:
        """topology_edge_stats shows independent exchange counts per edge direction."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")

        broker.record_topology(_topo(
            [("a", "b", "direct"), ("b", "a", "direct")],
            {"a": a_id, "b": b_id},
        ))

        # 3 a→b and 2 b→a deliveries.
        for _ in range(3):
            await broker.send(_env(a_id, b_id))
        for _ in range(2):
            await broker.send(_env(b_id, a_id))

        snap = broker.snapshot()
        stats = {(e.from_slot, e.to_slot): e for e in snap.topology_edge_stats}
        assert stats[("a", "b")].exchanges == 3
        assert stats[("b", "a")].exchanges == 2
        assert stats[("a", "b")].tripped is False
        assert stats[("b", "a")].tripped is False


# ---------------------------------------------------------------------------
# EdgeStat / topology_edge_stats snapshot tests
# ---------------------------------------------------------------------------

class TestEdgeStatSnapshot:
    """Verify BrokerSnapshot.topology_edge_stats is populated correctly."""

    async def test_empty_without_topology(self, broker: Broker) -> None:
        """No topology → topology_edge_stats is empty tuple."""
        snap = broker.snapshot()
        assert snap.topology_edge_stats == ()

    async def test_edge_stats_populated_for_recorded_topology(
        self, broker: Broker
    ) -> None:
        """Edges from a recorded topology appear in topology_edge_stats."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo(
            [("a", "b", "tee"), ("b", "a", "direct")],
            {"a": a_id, "b": b_id},
        ))

        snap = broker.snapshot()
        stats = {(e.from_slot, e.to_slot): e for e in snap.topology_edge_stats}

        assert ("a", "b") in stats
        assert ("b", "a") in stats
        assert stats[("a", "b")].mode == "tee"
        assert stats[("b", "a")].mode == "direct"
        assert stats[("a", "b")].exchanges == 0
        assert stats[("a", "b")].tripped is False

    async def test_edge_stats_reflect_override_mode(self, broker: Broker) -> None:
        """When promote_edge forces gated, EdgeStat.mode is 'gated'."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo(
            [("a", "b", "direct")], {"a": a_id, "b": b_id}
        ))

        broker.promote_edge("a", "b")  # manual promote → not marked as tripped

        snap = broker.snapshot()
        stats = {(e.from_slot, e.to_slot): e for e in snap.topology_edge_stats}
        assert stats[("a", "b")].mode == "gated"
        assert stats[("a", "b")].tripped is False  # manual promote, not auto-trip
