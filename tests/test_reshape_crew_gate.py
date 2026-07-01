"""Tests for the reshape_crew MCP tool scaffold — ATs 4, 10, 11, 12, 13, 14, 15, 16.

AT4:  deletion-detector — NAMED LITERAL 'reshape_crew' is registered as an MCP tool.
AT10: gate decline / timeout → {ok:False, stage:'gate'}, crew is COMPLETELY UNTOUCHED.
AT11: gate approve → new proposal status='instantiated', lineage works (shape_id reusable as base).
AT12: unknown base_shape_id → {ok:False, stage:'base'}, no proposal registered.
AT13: non-instantiated base (pending/approved/declined/timed_out) → {ok:False, stage:'base'}.
AT14: unknown verb → {ok:False, stage:'verb'}.
AT15: unresolvable role (swap + augment with factory.known_roles) → {ok:False, stage:'adapt', unresolved_roles}.
AT16: illegal mutation (verb.apply raises ShapeValidationError) → {ok:False, stage:'adapt'}.

asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from claude_crew.broker import Broker, ShapeProposal
from claude_crew.factories import stub_factory
from claude_crew.server import make_server
from claude_crew.shapes import parse_shape


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_json(result: Any) -> Any:
    """Unwrap an MCP tool result to its JSON payload."""
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    assert result.content, f"empty content: {result}"
    return json.loads(result.content[0].text)


def _client(broker: Broker | None = None, factory: Any = None):
    """Return a connected MCP client session context manager."""
    server = make_server(broker=broker, factory=factory)
    return create_connected_server_and_client_session(server)


async def _make_instantiated_base(
    broker: Broker,
    shape_dict: dict | None = None,
) -> str:
    """Register, approve, and mark_instantiated a shape proposal; return shape_id.

    Creates an 'instantiated' proposal in the broker without spawning any real
    teammates — sufficient for testing the reshape_crew scaffold's gate logic.
    """
    if shape_dict is None:
        shape_dict = _BASE_SHAPE
    parsed = parse_shape(shape_dict)
    sid = broker.register_proposal(parsed)
    await broker.resolve_proposal(sid, "approve")
    broker.mark_instantiated(sid)
    return sid


async def _poll_for_new_proposal(
    broker: Broker,
    existing_id: str,
    max_attempts: int = 50,
    delay: float = 0.02,
) -> ShapeProposal:
    """Poll broker snapshots until a proposal OTHER than existing_id appears.

    Used in concurrent gate tests to wait until reshape_crew has registered
    its internal proposal (which it then blocks on via await_proposal).
    """
    for _ in range(max_attempts):
        snap = broker.snapshot()
        for p in snap.shape_proposals:
            if p.shape_id != existing_id:
                return p
        await asyncio.sleep(delay)
    raise AssertionError(
        f"No new proposal appeared after {max_attempts * delay:.2f}s "
        f"(existing base: {existing_id!r})"
    )


# ---------------------------------------------------------------------------
# Shape fixture — two nodes with a 'direct' edge so set_gate has an edge to modify
# ---------------------------------------------------------------------------

_BASE_SHAPE: dict = {
    "name": "running-crew",
    "description": "A two-node running crew for reshape_crew scaffold testing",
    "nodes": [
        {"slot": "impl", "role": "builder"},
        {"slot": "reviewer", "role": "sentinel"},
    ],
    "edges": [
        {"from_slot": "impl", "to_slot": "reviewer", "mode": "direct"},
    ],
}


# ---------------------------------------------------------------------------
# Fixture: clean up stub_factory.known_roles / resolve_role after each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def clean_stub_known_roles():
    """Ensure stub_factory.known_roles / resolve_role are absent after the test."""
    yield
    for attr in ("known_roles", "resolve_role"):
        if hasattr(stub_factory, attr):
            delattr(stub_factory, attr)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AT4 — deletion-detector: reshape_crew registered as MCP tool
# ---------------------------------------------------------------------------


class TestReshapeCrewToolRegistered:
    """AT4: NAMED LITERAL 'reshape_crew' must appear in the server's tool registry.

    Deletion-detector: removing or renaming the tool registration will fail this
    test independently of the live suite, catching silent reversions early.
    """

    async def test_reshape_crew_registered(self) -> None:
        """AT4: enumerate MCP tool names; assert 'reshape_crew' is present."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()
            tools_result = await s.list_tools()
            tool_names = {t.name for t in tools_result.tools}
            assert "reshape_crew" in tool_names, (
                "Named literal 'reshape_crew' not found in registered MCP tools. "
                "Re-introducing the tool registration guard would fail this "
                "deletion-detector independently of the live suite."
            )


# ---------------------------------------------------------------------------
# AT10 — gate decline / timeout → ok:False, stage:'gate', crew untouched
# ---------------------------------------------------------------------------


class TestGateDeclineOrTimeout:
    """AT10: human declines or gate times out → stage:'gate', crew is untouched."""

    async def test_gate_decline_returns_ok_false_stage_gate(self) -> None:
        """AT10 (decline): declined proposal → {ok:False, stage:'gate', status:'declined'}."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)

        async with _client(broker=broker) as s:
            await s.initialize()

            # Run reshape_crew concurrently — it will register a proposal then
            # block on await_proposal until we resolve it below.
            reshape_task = asyncio.create_task(
                s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "set_gate",
                        "params": {
                            "from_slot": "impl",
                            "to_slot": "reviewer",
                            "mode": "tee",
                        },
                        "base_shape_id": base_sid,
                    },
                )
            )

            # Poll until reshape_crew registers its internal gate proposal.
            gate_proposal = await _poll_for_new_proposal(broker, existing_id=base_sid)

            # Decline → reshape_crew should return ok:False.
            await broker.resolve_proposal(gate_proposal.shape_id, "decline")

            result = _content_json(await reshape_task)

        assert result["ok"] is False, result
        assert result["stage"] == "gate", result
        assert result["status"] == "declined", result
        assert result["shape_id"] == gate_proposal.shape_id

    async def test_gate_decline_crew_completely_untouched(self) -> None:
        """AT10 (no mutation on decline): no topology appended, no override written, no spawn."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)

        # Capture pre-call state.
        pre_topologies = len(broker.get_topologies())
        pre_overrides = dict(broker._edge_overrides)  # type: ignore[attr-defined]

        async with _client(broker=broker) as s:
            await s.initialize()

            reshape_task = asyncio.create_task(
                s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "set_gate",
                        "params": {
                            "from_slot": "impl",
                            "to_slot": "reviewer",
                            "mode": "tee",
                        },
                        "base_shape_id": base_sid,
                    },
                )
            )
            gate_proposal = await _poll_for_new_proposal(broker, existing_id=base_sid)
            await broker.resolve_proposal(gate_proposal.shape_id, "decline")
            await reshape_task

        # No new topology was appended.
        assert len(broker.get_topologies()) == pre_topologies
        # No edge override was written.
        assert dict(broker._edge_overrides) == pre_overrides  # type: ignore[attr-defined]
        # No crew member was spawned.
        assert broker.list_crew() == []

    async def test_gate_timeout_returns_ok_false_stage_gate(self) -> None:
        """AT10 (timeout): gate_timeout=0.01 → {ok:False, stage:'gate', status:'timed_out'}."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)

        async with _client(broker=broker) as s:
            await s.initialize()
            result = _content_json(
                await s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "set_gate",
                        "params": {
                            "from_slot": "impl",
                            "to_slot": "reviewer",
                            "mode": "tee",
                        },
                        "base_shape_id": base_sid,
                        "gate_timeout": 0.01,  # very short → always times out
                    },
                )
            )

        assert result["ok"] is False, result
        assert result["stage"] == "gate", result
        assert result["status"] == "timed_out", result

    async def test_gate_timeout_crew_completely_untouched(self) -> None:
        """AT10 (no mutation on timeout): no topology, no override, no spawn."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)
        pre_topologies = len(broker.get_topologies())
        pre_overrides = dict(broker._edge_overrides)  # type: ignore[attr-defined]

        async with _client(broker=broker) as s:
            await s.initialize()
            await s.call_tool(
                "reshape_crew",
                {
                    "verb": "set_gate",
                    "params": {
                        "from_slot": "impl",
                        "to_slot": "reviewer",
                        "mode": "tee",
                    },
                    "base_shape_id": base_sid,
                    "gate_timeout": 0.01,
                },
            )

        assert len(broker.get_topologies()) == pre_topologies
        assert dict(broker._edge_overrides) == pre_overrides  # type: ignore[attr-defined]
        assert broker.list_crew() == []


# ---------------------------------------------------------------------------
# AT11 — gate approve → status:'instantiated' + lineage
# ---------------------------------------------------------------------------


class TestGateApproveLineage:
    """AT11: human approves → new proposal is 'instantiated'; shape_id usable as next base."""

    async def test_approve_returns_ok_true_and_marks_instantiated(self) -> None:
        """AT11 (approve): ok:True, returned shape_id.status='instantiated' in broker."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)

        async with _client(broker=broker) as s:
            await s.initialize()

            reshape_task = asyncio.create_task(
                s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "set_gate",
                        "params": {
                            "from_slot": "impl",
                            "to_slot": "reviewer",
                            "mode": "tee",
                        },
                        "base_shape_id": base_sid,
                    },
                )
            )
            gate_proposal = await _poll_for_new_proposal(broker, existing_id=base_sid)
            await broker.resolve_proposal(gate_proposal.shape_id, "approve")
            result = _content_json(await reshape_task)

        assert result["ok"] is True, result
        assert result["status"] == "instantiated", result
        new_sid = result["shape_id"]

        # The new proposal is marked instantiated in the broker.
        stored = broker.get_proposal(new_sid)
        assert stored is not None
        assert stored.status == "instantiated", stored.status

    async def test_approve_result_shape_id_accepted_as_next_base(self) -> None:
        """AT11 (lineage): result shape_id, when used as base_shape_id, passes base resolution.

        Proves the lineage chain: reshape_crew → new instantiated proposal →
        that proposal's shape_id is valid for a SECOND reshape_crew call.
        We use a very short gate_timeout on the second call so it times out at the
        gate stage (not at base resolution), confirming the lineage was recorded.
        """
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)

        async with _client(broker=broker) as s:
            await s.initialize()

            # ── First reshape: approve. ────────────────────────────────────────
            r1_task = asyncio.create_task(
                s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "set_gate",
                        "params": {
                            "from_slot": "impl",
                            "to_slot": "reviewer",
                            "mode": "tee",
                        },
                        "base_shape_id": base_sid,
                    },
                )
            )
            gate_proposal_1 = await _poll_for_new_proposal(broker, existing_id=base_sid)
            await broker.resolve_proposal(gate_proposal_1.shape_id, "approve")
            r1 = _content_json(await r1_task)
            assert r1["ok"] is True, r1
            new_base_sid = r1["shape_id"]

            # ── Second reshape: use first result as base; let it time out. ─────
            # If base resolution passes (stage gets to 'gate'), lineage is correct.
            # If it fails at 'base', the lineage wasn't recorded.
            r2 = _content_json(
                await s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "set_gate",
                        "params": {
                            "from_slot": "impl",
                            "to_slot": "reviewer",
                            "mode": "direct",
                        },
                        "base_shape_id": new_base_sid,
                        "gate_timeout": 0.01,  # short timeout — will hit gate stage
                    },
                )
            )

        # The failure must be at 'gate' (timed_out), NOT at 'base'.
        assert r2["ok"] is False, r2
        assert r2["stage"] == "gate", (
            f"Expected stage='gate' (lineage confirmed), got stage={r2.get('stage')!r}. "
            "If stage='base', the new shape_id was not recognized as an instantiated "
            "proposal — the lineage chain is broken."
        )
        assert r2["status"] == "timed_out", r2

    async def test_approve_diff_carried_in_gate_proposal(self) -> None:
        """AT11 (diff): gate proposal carries adaptation_diff from verb.apply."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)

        async with _client(broker=broker) as s:
            await s.initialize()

            reshape_task = asyncio.create_task(
                s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "set_gate",
                        "params": {
                            "from_slot": "impl",
                            "to_slot": "reviewer",
                            "mode": "tee",
                        },
                        "base_shape_id": base_sid,
                    },
                )
            )
            gate_proposal = await _poll_for_new_proposal(broker, existing_id=base_sid)

            # The gate proposal must carry adaptation_diff (shown in the dashboard).
            assert gate_proposal.adaptation_diff is not None
            assert len(gate_proposal.adaptation_diff) > 0

            await broker.resolve_proposal(gate_proposal.shape_id, "approve")
            result = _content_json(await reshape_task)

        assert result["ok"] is True, result
        assert "diff" in result
        assert result["diff"] == gate_proposal.adaptation_diff


# ---------------------------------------------------------------------------
# AT12 — unknown base_shape_id → stage:'base', no proposal registered
# ---------------------------------------------------------------------------


class TestUnknownBase:
    """AT12: base_shape_id not in broker → {ok:False, stage:'base'}, no side effects."""

    async def test_unknown_base_returns_stage_base(self) -> None:
        """AT12: non-existent base_shape_id → stage:'base'."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()
            result = _content_json(
                await s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "set_gate",
                        "params": {
                            "from_slot": "a",
                            "to_slot": "b",
                            "mode": "tee",
                        },
                        "base_shape_id": "does-not-exist",
                    },
                )
            )

        assert result["ok"] is False, result
        assert result["stage"] == "base", result

    async def test_unknown_base_no_proposal_registered(self) -> None:
        """AT12: unknown base → broker proposal count remains 0."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()
            await s.call_tool(
                "reshape_crew",
                {
                    "verb": "set_gate",
                    "params": {"from_slot": "a", "to_slot": "b", "mode": "tee"},
                    "base_shape_id": "does-not-exist",
                },
            )

        # No proposal should have been registered by reshape_crew.
        assert broker.snapshot().shape_proposals == ()


# ---------------------------------------------------------------------------
# AT13 — non-instantiated base → stage:'base'
# ---------------------------------------------------------------------------


class TestNonInstantiatedBase:
    """AT13: base proposal exists but status != 'instantiated' → stage:'base'.

    Tests all four non-instantiated statuses: pending, approved, declined, timed_out.
    """

    async def _call_reshape_set_gate(self, s, base_sid: str) -> dict:
        """Helper: call reshape_crew with set_gate on the given base_shape_id."""
        return _content_json(
            await s.call_tool(
                "reshape_crew",
                {
                    "verb": "set_gate",
                    "params": {
                        "from_slot": "impl",
                        "to_slot": "reviewer",
                        "mode": "tee",
                    },
                    "base_shape_id": base_sid,
                    "gate_timeout": 5.0,
                },
            )
        )

    async def test_pending_base_rejected(self) -> None:
        """AT13 (pending): proposal registered but not yet approved → stage:'base'."""
        broker = Broker()
        parsed = parse_shape(_BASE_SHAPE)
        sid = broker.register_proposal(parsed)
        assert broker.get_proposal(sid).status == "pending"

        async with _client(broker=broker) as s:
            await s.initialize()
            result = await self._call_reshape_set_gate(s, sid)

        assert result["ok"] is False, result
        assert result["stage"] == "base", result

    async def test_approved_base_rejected(self) -> None:
        """AT13 (approved): approved but not yet instantiated → stage:'base'."""
        broker = Broker()
        parsed = parse_shape(_BASE_SHAPE)
        sid = broker.register_proposal(parsed)
        await broker.resolve_proposal(sid, "approve")
        assert broker.get_proposal(sid).status == "approved"

        async with _client(broker=broker) as s:
            await s.initialize()
            result = await self._call_reshape_set_gate(s, sid)

        assert result["ok"] is False, result
        assert result["stage"] == "base", result

    async def test_declined_base_rejected(self) -> None:
        """AT13 (declined): declined proposal → stage:'base'."""
        broker = Broker()
        parsed = parse_shape(_BASE_SHAPE)
        sid = broker.register_proposal(parsed)
        await broker.resolve_proposal(sid, "decline")
        assert broker.get_proposal(sid).status == "declined"

        async with _client(broker=broker) as s:
            await s.initialize()
            result = await self._call_reshape_set_gate(s, sid)

        assert result["ok"] is False, result
        assert result["stage"] == "base", result

    async def test_timed_out_base_rejected(self) -> None:
        """AT13 (timed_out): proposal that timed out of its own gate → stage:'base'."""
        broker = Broker()
        parsed = parse_shape(_BASE_SHAPE)
        sid = broker.register_proposal(parsed)
        # Let the proposal time out via a very short await.
        proposal = await broker.await_proposal(sid, timeout=0.01)
        assert proposal.status == "timed_out"

        async with _client(broker=broker) as s:
            await s.initialize()
            result = await self._call_reshape_set_gate(s, sid)

        assert result["ok"] is False, result
        assert result["stage"] == "base", result


# ---------------------------------------------------------------------------
# AT14 — unknown verb → stage:'verb'
# ---------------------------------------------------------------------------


class TestUnknownVerb:
    """AT14: verb not in {add_node, swap, augment, set_gate, drop} → stage:'verb'."""

    async def test_unknown_verb_rejected(self) -> None:
        """AT14: unrecognised verb string → {ok:False, stage:'verb'}."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)

        async with _client(broker=broker) as s:
            await s.initialize()
            result = _content_json(
                await s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "teleport",  # unknown
                        "params": {},
                        "base_shape_id": base_sid,
                    },
                )
            )

        assert result["ok"] is False, result
        assert result["stage"] == "verb", result

    async def test_unknown_verb_no_proposal_registered(self) -> None:
        """AT14: unknown verb → no gate proposal registered (no pre-gate side effect)."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)

        async with _client(broker=broker) as s:
            await s.initialize()
            await s.call_tool(
                "reshape_crew",
                {
                    "verb": "delete_everything",  # unknown
                    "params": {},
                    "base_shape_id": base_sid,
                },
            )

        # Only the base proposal exists; reshape_crew did not register a gate proposal.
        snap = broker.snapshot()
        assert all(p.shape_id == base_sid for p in snap.shape_proposals), (
            "reshape_crew registered a proposal despite failing at the verb stage"
        )


# ---------------------------------------------------------------------------
# AT15 — unresolvable role → stage:'adapt', unresolved_roles list
# ---------------------------------------------------------------------------


class TestUnresolvableRole:
    """AT15: swap/augment with unknown role when factory.known_roles is set → stage:'adapt'."""

    async def test_swap_unresolvable_role_rejected(
        self, clean_stub_known_roles: None
    ) -> None:
        """AT15 (swap): role absent from factory.known_roles → stage:'adapt', unresolved_roles."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)
        stub_factory.known_roles = lambda: ("builder", "sentinel")  # type: ignore[attr-defined]

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()
            result = _content_json(
                await s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "swap",
                        "params": {
                            "slot": "reviewer",
                            "role": "ghost-role",  # not in known_roles
                        },
                        "base_shape_id": base_sid,
                    },
                )
            )

        assert result["ok"] is False, result
        assert result["stage"] == "adapt", result
        assert "unresolved_roles" in result, result
        assert "ghost-role" in result["unresolved_roles"]

    async def test_swap_unresolvable_role_no_proposal(
        self, clean_stub_known_roles: None
    ) -> None:
        """AT15 (swap, no proposal): failed role resolution → no gate proposal registered."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)
        stub_factory.known_roles = lambda: ("builder", "sentinel")  # type: ignore[attr-defined]

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()
            await s.call_tool(
                "reshape_crew",
                {
                    "verb": "swap",
                    "params": {"slot": "reviewer", "role": "ghost-role"},
                    "base_shape_id": base_sid,
                },
            )

        # No gate proposal registered.
        snap = broker.snapshot()
        assert all(p.shape_id == base_sid for p in snap.shape_proposals)

    async def test_augment_unresolvable_role_rejected(
        self, clean_stub_known_roles: None
    ) -> None:
        """AT15 (augment): node role absent from factory.known_roles → stage:'adapt'."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)
        stub_factory.known_roles = lambda: ("builder", "sentinel")  # type: ignore[attr-defined]

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()
            result = _content_json(
                await s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "augment",
                        "params": {
                            "node": {
                                "slot": "qa",
                                "role": "ghost-role",  # not in known_roles
                            },
                            "edges": [
                                {"from_slot": "impl", "to_slot": "qa"},
                            ],
                        },
                        "base_shape_id": base_sid,
                    },
                )
            )

        assert result["ok"] is False, result
        assert result["stage"] == "adapt", result
        assert "unresolved_roles" in result, result
        assert "ghost-role" in result["unresolved_roles"]

    async def test_no_known_roles_skips_resolution(self) -> None:
        """AT15 (no known_roles): absent factory.known_roles → role accepted, reaches gate."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)
        # stub_factory has NO known_roles by default (confirmed by conftest default mode)
        assert not hasattr(stub_factory, "known_roles"), (
            "stub_factory must not have known_roles for this test; "
            "a prior test may have left it set"
        )

        async with _client(broker=broker) as s:
            await s.initialize()
            # Use very short gate_timeout so it times out at the gate stage —
            # if it fails at 'adapt' instead, role resolution ran unexpectedly.
            result = _content_json(
                await s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "swap",
                        "params": {
                            "slot": "reviewer",
                            "role": "any-role-at-all",
                        },
                        "base_shape_id": base_sid,
                        "gate_timeout": 0.01,
                    },
                )
            )

        # Without known_roles, role resolution is skipped → reaches the gate.
        assert result["ok"] is False, result
        assert result["stage"] == "gate", (
            f"Expected stage='gate' (role resolution skipped), "
            f"got stage={result.get('stage')!r}. "
            "Role resolution should be skipped when factory.known_roles is absent."
        )


# ---------------------------------------------------------------------------
# AT16 — illegal mutation: verb.apply raises ShapeValidationError → stage:'adapt'
# ---------------------------------------------------------------------------


class TestIllegalMutation:
    """AT16: verb.apply raises ShapeValidationError → {ok:False, stage:'adapt'}, no mutation."""

    async def test_add_node_duplicate_slot_rejected(self) -> None:
        """AT16 (duplicate slot): add_node with a slot that already exists → stage:'adapt'."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)

        async with _client(broker=broker) as s:
            await s.initialize()
            result = _content_json(
                await s.call_tool(
                    "reshape_crew",
                    {
                        "verb": "add_node",
                        "params": {
                            "node": {
                                "slot": "impl",  # duplicate — already in _BASE_SHAPE
                                "role": "builder",
                            },
                            "edges": [],
                        },
                        "base_shape_id": base_sid,
                    },
                )
            )

        assert result["ok"] is False, result
        assert result["stage"] == "adapt", result

    async def test_add_node_duplicate_slot_no_proposal_registered(self) -> None:
        """AT16: ShapeValidationError → no gate proposal registered."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)

        async with _client(broker=broker) as s:
            await s.initialize()
            await s.call_tool(
                "reshape_crew",
                {
                    "verb": "add_node",
                    "params": {
                        "node": {"slot": "impl", "role": "builder"},
                        "edges": [],
                    },
                    "base_shape_id": base_sid,
                },
            )

        # No gate proposal registered (error is pre-gate).
        snap = broker.snapshot()
        assert all(p.shape_id == base_sid for p in snap.shape_proposals)

    async def test_add_node_duplicate_slot_no_mutation(self) -> None:
        """AT16: ShapeValidationError → no topology appended, no override written."""
        broker = Broker()
        base_sid = await _make_instantiated_base(broker)
        pre_topologies = len(broker.get_topologies())
        pre_overrides = dict(broker._edge_overrides)  # type: ignore[attr-defined]

        async with _client(broker=broker) as s:
            await s.initialize()
            await s.call_tool(
                "reshape_crew",
                {
                    "verb": "add_node",
                    "params": {
                        "node": {"slot": "impl", "role": "builder"},
                        "edges": [],
                    },
                    "base_shape_id": base_sid,
                },
            )

        assert len(broker.get_topologies()) == pre_topologies
        assert dict(broker._edge_overrides) == pre_overrides  # type: ignore[attr-defined]
        assert broker.list_crew() == []
