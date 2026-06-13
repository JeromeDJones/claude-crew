"""Tests for M2 scoped-send-teammate slice.

Covers AT#8, AT#9, and AT#10 from specs/m2-edge-routing.md.

AT#8 — neighbor adjacency injected at spawn (prompt injection via system_prompt_override)
AT#9 — broker.send_scoped authorizes a neighbor, rejects a non-neighbor
AT#10 — direct ping-pong stays off the lead inbox (integration)
"""

from __future__ import annotations

import asyncio
import collections
import time
from typing import Any

import pytest

from claude_crew.broker import (
    LEAD_ID,
    Broker,
    Topology,
    UnauthorizedEdgeError,
)
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import Teammate, _tool_events_maxlen
from claude_crew.teammate_prompt import (
    SENTINEL_NEIGHBORS,
    build_teammate_prompt,
)


# ---------------------------------------------------------------------------
# Minimal noop teammate (mirrors _NoopTeammate pattern from test_edge_routing.py)
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


class _NeighborTeammate(_NoopTeammate):
    """Extends _NoopTeammate to build a real system prompt from neighbors.

    Used by AT#8 to verify that the neighbors section appears in the
    assembled system prompt — which is what broker._snapshot_config captures
    as system_prompt_override.
    """

    def __init__(
        self,
        id: str,
        name: str,
        role: str,
        *,
        neighbors: "list[dict] | None" = None,
        **_kwargs: Any,
    ) -> None:
        super().__init__(id=id, name=name, role=role)
        # Build a real system prompt using build_teammate_prompt so the
        # SENTINEL_NEIGHBORS section appears when neighbors is non-empty.
        self._system_prompt: str | None = build_teammate_prompt(
            role,
            f"Pack body for {role}.",
            {},  # empty agents dict — no explorer, still generates the section
            neighbors=neighbors,
        )


def _neighbor_factory(
    id: str, name: str, role: str, **kwargs: Any
) -> _NeighborTeammate:
    """Factory that produces _NeighborTeammate instances, accepting neighbors kwarg."""
    return _NeighborTeammate(id=id, name=name, role=role, **kwargs)


def _plain_factory(
    id: str, name: str, role: str, **_kwargs: Any
) -> _NoopTeammate:
    """Plain factory that ignores all kwargs."""
    return _NoopTeammate(id=id, name=name, role=role)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def broker() -> Broker:
    # Transcript is disabled by conftest's _disable_transcripts fixture
    # (CLAUDE_CREW_TRANSCRIPT_DISABLED=1), so Broker() uses a noop sink.
    return Broker()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _topo(
    edges: list[tuple[str, str, str]], slot_to_teammate: dict[str, str]
) -> Topology:
    return Topology(
        shape_name="test",
        edges=tuple(edges),
        slot_to_teammate=slot_to_teammate,
    )


async def _spawn(
    broker: Broker,
    role: str = "r",
    factory: Any = None,
    **kwargs: Any,
) -> str:
    f = factory if factory is not None else _plain_factory
    return await broker.spawn_teammate(role=role, name=role, factory=f, **kwargs)


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
# AT#8 — neighbor adjacency injected at spawn
# ---------------------------------------------------------------------------


class TestNeighborInjection:
    """AT#8: neighbor adjacency is injected into the system prompt at spawn.

    Given a shape with edges planner→implementor (gated) and
    implementor→reviewer (direct), when the implementor is spawned with the
    computed neighbor list, its assembled system prompt (the
    system_prompt_override captured in broker._configs) contains
    SENTINEL_NEIGHBORS and names its out-edge reviewer (direct) and in-edge
    planner (gated).
    """

    def test_build_teammate_prompt_no_neighbors_no_section(self) -> None:
        """Unit: omitting neighbors produces no SENTINEL_NEIGHBORS section."""
        prompt = build_teammate_prompt("implementor", "Pack body.", {})
        assert SENTINEL_NEIGHBORS not in prompt

    def test_build_teammate_prompt_with_neighbors_has_section(self) -> None:
        """Unit: neighbors kwarg produces SENTINEL_NEIGHBORS section with out/in edges."""
        neighbors = [
            {"direction": "out", "slot": "reviewer", "role": "code-reviewer", "mode": "direct"},
            {"direction": "in", "slot": "planner", "role": "planner", "mode": "gated"},
        ]
        prompt = build_teammate_prompt("implementor", "Pack body.", {}, neighbors=neighbors)

        assert SENTINEL_NEIGHBORS in prompt
        # out-edge: reviewer (direct)
        assert "reviewer" in prompt
        assert "direct" in prompt
        # in-edge: planner (gated)
        assert "planner" in prompt
        assert "gated" in prompt

    def test_build_teammate_prompt_neighbors_empty_no_section(self) -> None:
        """Unit: empty neighbors list produces no SENTINEL_NEIGHBORS section."""
        prompt = build_teammate_prompt("implementor", "Pack body.", {}, neighbors=[])
        assert SENTINEL_NEIGHBORS not in prompt

    def test_build_teammate_prompt_out_only(self) -> None:
        """Unit: only out-edges appear when no in-edges declared."""
        neighbors = [
            {"direction": "out", "slot": "reviewer", "role": "reviewer", "mode": "direct"},
        ]
        prompt = build_teammate_prompt("implementor", "Pack body.", {}, neighbors=neighbors)
        assert SENTINEL_NEIGHBORS in prompt
        assert "reviewer" in prompt
        assert "direct" in prompt
        assert "In-edges" not in prompt

    def test_build_teammate_prompt_in_only(self) -> None:
        """Unit: only in-edges appear when no out-edges declared."""
        neighbors = [
            {"direction": "in", "slot": "planner", "role": "planner", "mode": "gated"},
        ]
        prompt = build_teammate_prompt("implementor", "Pack body.", {}, neighbors=neighbors)
        assert SENTINEL_NEIGHBORS in prompt
        assert "planner" in prompt
        assert "gated" in prompt
        assert "Out-edges" not in prompt

    async def test_spawn_with_neighbors_captured_in_config_snapshot(
        self, broker: Broker
    ) -> None:
        """Integration: neighbors flow from broker.spawn_teammate → system_prompt_override.

        Uses extra_tools=["Read"] to force a non-None config snapshot even
        without an agent_def_resolver, so system_prompt appears in the snapshot.
        """
        neighbors = [
            {"direction": "out", "slot": "reviewer", "role": "code-reviewer", "mode": "direct"},
            {"direction": "in", "slot": "planner", "role": "planner", "mode": "gated"},
        ]

        tid = await _spawn(
            broker,
            role="implementor",
            factory=_neighbor_factory,
            extra_tools=["Read"],  # forces non-None minimal snapshot
            neighbors=neighbors,
        )

        config = broker._configs.get(tid)
        assert config is not None, "expected non-None config snapshot"
        system_prompt = config.get("system_prompt")
        assert system_prompt is not None, "expected system_prompt in config snapshot"
        assert SENTINEL_NEIGHBORS in system_prompt
        assert "reviewer" in system_prompt
        assert "direct" in system_prompt
        assert "planner" in system_prompt
        assert "gated" in system_prompt

    async def test_spawn_without_neighbors_no_section_in_prompt(
        self, broker: Broker
    ) -> None:
        """Integration: spawning without neighbors yields no SENTINEL_NEIGHBORS."""
        tid = await _spawn(
            broker,
            role="implementor",
            factory=_neighbor_factory,
            extra_tools=["Read"],
            # No neighbors kwarg
        )

        config = broker._configs.get(tid)
        assert config is not None
        system_prompt = config.get("system_prompt")
        assert system_prompt is not None
        assert SENTINEL_NEIGHBORS not in system_prompt


# ---------------------------------------------------------------------------
# AT#9 — broker.send_scoped authorizes a neighbor, rejects a non-neighbor
# ---------------------------------------------------------------------------


class TestScopedSendAuthorize:
    """AT#9: send_scoped resolves slot names and enforces edge authorization.

    Given a topology where a's only out-edge is a→b, send_scoped(a, "b", ...)
    delivers to b (resolving slot "b" → b's teammate id), and
    send_scoped(a, "c", ...) raises UnauthorizedEdgeError delivering nothing.
    """

    async def test_send_scoped_delivers_to_declared_neighbor_by_slot_name(
        self, broker: Broker
    ) -> None:
        """send_scoped with slot name "b" resolves and delivers to b's inbox."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(
            _topo([("a", "b", "direct")], {"a": a_id, "b": b_id})
        )

        result = await broker.send_scoped(
            sender_id=a_id, recipient="b", payload={"msg": "hello"}
        )
        assert result is not None

        # direct routing → b's inbox receives the message
        b_msgs = broker.get_messages(b_id)
        assert len(b_msgs) == 1
        assert b_msgs[0].payload == {"msg": "hello"}

        # direct routing → lead does NOT receive the message
        lead_msgs = broker.get_messages(LEAD_ID)
        assert lead_msgs == []

    async def test_send_scoped_delivers_to_declared_neighbor_by_teammate_id(
        self, broker: Broker
    ) -> None:
        """send_scoped with teammate_id directly (as opposed to slot name) also works."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(
            _topo([("a", "b", "direct")], {"a": a_id, "b": b_id})
        )

        result = await broker.send_scoped(
            sender_id=a_id, recipient=b_id, payload={"msg": "by-id"}
        )
        assert result is not None

        b_msgs = broker.get_messages(b_id)
        assert len(b_msgs) == 1
        assert b_msgs[0].payload == {"msg": "by-id"}

    async def test_send_scoped_rejects_non_declared_neighbor(
        self, broker: Broker
    ) -> None:
        """send_scoped raises UnauthorizedEdgeError for a non-declared recipient."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        c_id = await _spawn(broker, "c")
        broker.record_topology(
            _topo([("a", "b", "direct")], {"a": a_id, "b": b_id, "c": c_id})
        )

        with pytest.raises(UnauthorizedEdgeError):
            await broker.send_scoped(
                sender_id=a_id, recipient="c", payload={"msg": "forbidden"}
            )

        # Nothing enqueued to c
        assert broker.get_messages(c_id) == []

    async def test_send_scoped_rejects_teammate_not_in_topology(
        self, broker: Broker
    ) -> None:
        """send_scoped raises UnauthorizedEdgeError when recipient not in topology."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        c_id = await _spawn(broker, "c")
        # c is NOT in slot_to_teammate
        broker.record_topology(
            _topo([("a", "b", "direct")], {"a": a_id, "b": b_id})
        )

        with pytest.raises(UnauthorizedEdgeError):
            await broker.send_scoped(
                sender_id=a_id, recipient=c_id, payload={"msg": "forbidden"}
            )

        assert broker.get_messages(c_id) == []

    async def test_send_scoped_to_lead_always_succeeds(
        self, broker: Broker
    ) -> None:
        """send_scoped to LEAD_ID bypasses the edge guard (always authorized)."""
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

    async def test_send_scoped_gated_edge_routes_via_lead(
        self, broker: Broker
    ) -> None:
        """send_scoped on a gated edge still routes through lead (gated wrapper)."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(
            _topo([("a", "b", "gated")], {"a": a_id, "b": b_id})
        )

        await broker.send_scoped(
            sender_id=a_id, recipient="b", payload={"msg": "gated-msg"}
        )

        # gated → b's inbox receives nothing
        assert broker.get_messages(b_id) == []

        # gated → lead inbox receives a wrapper
        lead_msgs = broker.get_messages(LEAD_ID)
        assert len(lead_msgs) == 1
        assert lead_msgs[0].payload.get("gated_for") == b_id


# ---------------------------------------------------------------------------
# AT#10 — direct ping-pong stays off the lead inbox (integration)
# ---------------------------------------------------------------------------


class TestDirectPingPong:
    """AT#10: reciprocal direct exchanges stay off the lead inbox.

    Given a shape with reciprocal direct edges a→b and b→a instantiated with
    two StubTeammates, when a sequence of teammate-initiated send_scoped
    exchanges below the breaker budget runs a→b→a→b, then none of those
    messages appear in get_messages(LEAD) but all appear in the broker log.
    """

    async def test_reciprocal_direct_below_budget_stays_off_lead(
        self, broker: Broker
    ) -> None:
        """a→b→a→b ping-pong (4 messages) does not appear in LEAD inbox."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(
            _topo(
                [("a", "b", "direct"), ("b", "a", "direct")],
                {"a": a_id, "b": b_id},
            )
        )

        # Simulate 4-message ping-pong: a→b, b→a, a→b, b→a (below budget of 8)
        payloads = [
            {"turn": 1, "from": "a", "to": "b"},
            {"turn": 2, "from": "b", "to": "a"},
            {"turn": 3, "from": "a", "to": "b"},
            {"turn": 4, "from": "b", "to": "a"},
        ]
        for p in payloads[:2]:  # a→b, b→a
            sender = a_id if p["from"] == "a" else b_id
            recipient_slot = p["to"]  # slot name
            await broker.send_scoped(
                sender_id=sender, recipient=recipient_slot, payload=p
            )
        for p in payloads[2:]:  # a→b, b→a
            sender = a_id if p["from"] == "a" else b_id
            recipient_slot = p["to"]
            await broker.send_scoped(
                sender_id=sender, recipient=recipient_slot, payload=p
            )

        # None of the 4 direct messages should appear in the LEAD inbox
        lead_msgs = broker.get_messages(LEAD_ID)
        assert lead_msgs == [], (
            f"Expected 0 lead messages from direct ping-pong, got {len(lead_msgs)}"
        )

        # All 4 messages should be in the broker log (direct = logged)
        direct_msgs = [
            m for m in broker._log  # type: ignore[attr-defined]
            if m.sender in (a_id, b_id) and m.recipient in (a_id, b_id)
        ]
        assert len(direct_msgs) == 4, (
            f"Expected 4 direct messages in log, got {len(direct_msgs)}"
        )

    async def test_reciprocal_direct_all_messages_appear_in_log(
        self, broker: Broker
    ) -> None:
        """Verify the broker log captures each direct message in turn order."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(
            _topo(
                [("a", "b", "direct"), ("b", "a", "direct")],
                {"a": a_id, "b": b_id},
            )
        )

        await broker.send_scoped(a_id, "b", {"seq": 1})
        await broker.send_scoped(b_id, "a", {"seq": 2})
        await broker.send_scoped(a_id, "b", {"seq": 3})
        await broker.send_scoped(b_id, "a", {"seq": 4})

        direct_msgs = [
            m for m in broker._log  # type: ignore[attr-defined]
            if m.sender in (a_id, b_id) and m.recipient in (a_id, b_id)
        ]
        seqs = [m.payload["seq"] for m in direct_msgs]
        assert seqs == [1, 2, 3, 4], f"Wrong message order or count: {seqs}"

    async def test_single_direct_edge_delivery_to_inbox(
        self, broker: Broker
    ) -> None:
        """Direct edge: message appears in recipient's inbox."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(
            _topo([("a", "b", "direct")], {"a": a_id, "b": b_id})
        )

        await broker.send_scoped(a_id, "b", {"hello": "world"})

        # b's inbox should have the message (direct delivery)
        b_msgs = broker.get_messages(b_id)
        assert len(b_msgs) == 1
        assert b_msgs[0].payload == {"hello": "world"}

        # lead should have nothing
        assert broker.get_messages(LEAD_ID) == []

    async def test_ping_pong_does_not_trip_breaker_below_budget(
        self, broker: Broker
    ) -> None:
        """4-message reciprocal exchange below budget(8) does not trip breaker."""
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(
            _topo(
                [("a", "b", "direct"), ("b", "a", "direct")],
                {"a": a_id, "b": b_id},
            )
        )

        # 4 exchanges = 2 per directed edge, well below the default budget of 8
        for _ in range(2):
            await broker.send_scoped(a_id, "b", {"ping": True})
            await broker.send_scoped(b_id, "a", {"pong": True})

        # Breaker should NOT have tripped — all messages still direct (off lead)
        lead_msgs = broker.get_messages(LEAD_ID)
        assert lead_msgs == [], (
            f"Breaker tripped prematurely — {len(lead_msgs)} messages leaked to lead"
        )


# ---------------------------------------------------------------------------
# AT#11 (implicit) — send_to tool is registered and its handler delegates
# to broker.send_scoped without a live SDK.
# ---------------------------------------------------------------------------


class TestSendToToolRegistration:
    """Verify the in-process send_to MCP tool in SdkTeammate.

    These tests use SdkTeammate.__new__ to bypass __init__ (which would
    load the default agent pack) and directly exercise _build_send_to_mcp_server
    and the resulting handler in isolation — no live SDK required.
    """

    def test_send_to_mcp_server_shape(self) -> None:
        """_build_send_to_mcp_server returns a McpSdkServerConfig with correct shape."""
        from claude_crew.sdk_teammate import SdkTeammate, _SEND_TO_MCP_SERVER_NAME

        tm = SdkTeammate.__new__(SdkTeammate)
        tm.id = "t-test-001"
        tm._broker = None
        tm._send_to_tool = None

        server = tm._build_send_to_mcp_server()

        assert server["type"] == "sdk", f"expected type 'sdk', got {server['type']!r}"
        assert server["name"] == _SEND_TO_MCP_SERVER_NAME
        # Side-effect: _send_to_tool is set
        assert tm._send_to_tool is not None
        assert tm._send_to_tool.name == "send_to"

    @pytest.mark.asyncio
    async def test_send_to_handler_delegates_to_send_scoped(self) -> None:
        """Handler calls broker.send_scoped(self.id, recipient, payload) on success."""
        from unittest.mock import AsyncMock

        from claude_crew.sdk_teammate import SdkTeammate

        tm = SdkTeammate.__new__(SdkTeammate)
        tm.id = "t-test-001"
        tm._send_to_tool = None
        mock_broker = AsyncMock()
        mock_broker.send_scoped = AsyncMock(return_value=None)
        tm._broker = mock_broker

        tm._build_send_to_mcp_server()
        result = await tm._send_to_tool.handler(
            {"recipient": "reviewer", "payload": {"msg": "hello"}}
        )

        mock_broker.send_scoped.assert_called_once_with(
            "t-test-001", "reviewer", {"msg": "hello"}
        )
        assert result.get("is_error") is not True
        assert "reviewer" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_send_to_handler_surfaces_unauthorized_as_error(self) -> None:
        """UnauthorizedEdgeError from send_scoped → is_error=True in tool result."""
        from unittest.mock import AsyncMock

        from claude_crew.broker import UnauthorizedEdgeError
        from claude_crew.sdk_teammate import SdkTeammate

        tm = SdkTeammate.__new__(SdkTeammate)
        tm.id = "t-test-001"
        tm._send_to_tool = None
        mock_broker = AsyncMock()
        mock_broker.send_scoped = AsyncMock(
            side_effect=UnauthorizedEdgeError("not a declared neighbor")
        )
        tm._broker = mock_broker

        tm._build_send_to_mcp_server()
        result = await tm._send_to_tool.handler({"recipient": "stranger", "payload": {}})

        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "send_to rejected" in text
        assert "not a declared neighbor" in text

    @pytest.mark.asyncio
    async def test_send_to_handler_when_broker_is_none(self) -> None:
        """Handler returns is_error=True gracefully when broker is not yet set."""
        from claude_crew.sdk_teammate import SdkTeammate

        tm = SdkTeammate.__new__(SdkTeammate)
        tm.id = "t-test-001"
        tm._send_to_tool = None
        tm._broker = None

        tm._build_send_to_mcp_server()
        result = await tm._send_to_tool.handler({"recipient": "anyone", "payload": {}})

        assert result.get("is_error") is True
        assert "broker not available" in result["content"][0]["text"]

    def test_send_to_tool_id_constant(self) -> None:
        """_SEND_TO_TOOL_ID follows the mcp__<server>__<tool> naming convention."""
        from claude_crew.sdk_teammate import (
            _SEND_TO_MCP_SERVER_NAME,
            _SEND_TO_TOOL_ID,
        )

        expected = f"mcp__{_SEND_TO_MCP_SERVER_NAME}__send_to"
        assert _SEND_TO_TOOL_ID == expected
