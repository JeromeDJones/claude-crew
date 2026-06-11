"""Integration tests for propose_shape and instantiate_shape MCP tools — ATs 8, 9, 10, 14.

AT8:  happy path — propose → approve → instantiate spawns exactly 2 teammates
       and records the topology in broker.snapshot().
AT9:  instantiate on a pending proposal returns ok:False, no spawn.
AT10: declined proposal — propose_shape returns "declined", instantiate ok:False.
AT14: pre-flight role resolution — unresolvable role → ok:False + unresolved_roles,
       zero spawn; also exercises unique-suffix promotion and no-known_roles skip.

asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from claude_crew.broker import Broker
from claude_crew.factories import stub_factory
from claude_crew.server import make_server


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


async def _poll_proposal(broker: Broker, max_attempts: int = 50, delay: float = 0.02) -> str:
    """Poll broker.snapshot() until a proposal appears; return its shape_id."""
    for _ in range(max_attempts):
        snap = broker.snapshot()
        if snap.shape_proposals:
            return snap.shape_proposals[0].shape_id
        await asyncio.sleep(delay)
    raise AssertionError("proposal was never registered in the broker")


# ---------------------------------------------------------------------------
# Shape fixtures
# ---------------------------------------------------------------------------

# A minimal 2-node, 1-edge shape (nodes use bare role names present in any pack).
_SHAPE_2NODE: dict = {
    "name": "test-crew",
    "description": "A two-node shape for integration testing",
    "nodes": [
        {"slot": "implementor", "role": "builder"},
        {"slot": "reviewer", "role": "sentinel"},
    ],
    "edges": [
        {"from_slot": "implementor", "to_slot": "reviewer"},
    ],
}

# A shape that contains a role that cannot be resolved from a known_roles set.
_SHAPE_UNRESOLVABLE: dict = {
    "name": "bad-role-shape",
    "description": "Shape with a role that does not resolve",
    "nodes": [
        {"slot": "implementor", "role": "builder"},
        {"slot": "mystery-slot", "role": "nonexistent-role"},
    ],
    "edges": [],
}


# ---------------------------------------------------------------------------
# AT8 — happy path: propose → approve → instantiate
# ---------------------------------------------------------------------------


class TestProposeApproveInstantiate:
    """AT8: full happy path through the shape gate."""

    async def test_propose_approve_instantiate_spawns_two_teammates(self) -> None:
        """propose → approve (direct broker call) → instantiate → 2 teammates + topology."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            # Start propose_shape as a background task — it blocks until a decision.
            propose_task = asyncio.create_task(
                s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )

            # Wait for the proposal to appear in the broker, then approve it.
            shape_id = await _poll_proposal(broker)
            await broker.resolve_proposal(shape_id, "approve")

            # propose_shape should unblock and return "approved".
            propose_result = _content_json(await propose_task)
            assert propose_result["ok"] is True, propose_result
            assert propose_result["status"] == "approved"
            assert propose_result["shape_id"] == shape_id

            # Instantiate the approved shape.
            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            assert inst_result["ok"] is True, inst_result
            assert inst_result["shape_id"] == shape_id

            # Exactly 2 crew entries, named by slot.
            crew = inst_result["crew"]
            assert len(crew) == 2, f"expected 2 crew entries, got {crew}"
            slots_in_crew = {c["slot"] for c in crew}
            assert slots_in_crew == {"implementor", "reviewer"}

            by_slot = {c["slot"]: c for c in crew}
            assert by_slot["implementor"]["role"] == "builder"
            assert by_slot["reviewer"]["role"] == "sentinel"
            # Each entry carries a teammate_id
            for entry in crew:
                assert entry["teammate_id"].startswith("t-"), entry

            # list_crew should show both, alive and named by slot.
            list_result = _content_json(await s.call_tool("list_crew", {}))
            alive_names = {t["name"] for t in list_result["teammates"] if t["alive"]}
            assert alive_names == {"implementor", "reviewer"}

            # Topology is recorded in broker snapshot.
            snap = broker.snapshot()
            assert len(snap.topologies) == 1
            topo = snap.topologies[0]
            assert topo.shape_name == "test-crew"
            # Edge recorded with defaulted mode "gated"
            assert len(topo.edges) == 1
            assert topo.edges[0] == ("implementor", "reviewer", "gated")
            # slot_to_teammate maps both slots
            assert set(topo.slot_to_teammate.keys()) == {"implementor", "reviewer"}
            assert topo.slot_to_teammate["implementor"] == by_slot["implementor"]["teammate_id"]
            assert topo.slot_to_teammate["reviewer"] == by_slot["reviewer"]["teammate_id"]

    async def test_propose_shape_parse_error_returns_ok_false(self) -> None:
        """AT8 guard: a malformed shape dict → {ok:False, stage:'parse'}."""
        async with _client() as s:
            await s.initialize()
            result = _content_json(
                await s.call_tool("propose_shape", {"shape": {"name": "x"}})
            )
            assert result["ok"] is False
            assert result["stage"] == "parse"
            assert "error" in result

    async def test_instantiate_marks_proposal_instantiated(self) -> None:
        """After instantiation the proposal status is 'instantiated' (single-use guard)."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            propose_task = asyncio.create_task(
                s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            shape_id = await _poll_proposal(broker)
            await broker.resolve_proposal(shape_id, "approve")
            await propose_task

            await s.call_tool("instantiate_shape", {"shape_id": shape_id})

            assert broker.get_proposal(shape_id).status == "instantiated"

            # Second call must be refused (single-use).
            second = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            assert second["ok"] is False


# ---------------------------------------------------------------------------
# AT9 — unapproved proposal → instantiate refused
# ---------------------------------------------------------------------------


class TestInstantiateBeforeApproval:
    """AT9: attempt instantiate while proposal is still pending → ok:False, no spawn."""

    async def test_pending_proposal_refused(self) -> None:
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            # Start propose_shape with a long timeout so it stays pending.
            propose_task = asyncio.create_task(
                s.call_tool("propose_shape", {"shape": _SHAPE_2NODE, "timeout_seconds": 600.0})
            )

            # Wait for proposal to register, then immediately try to instantiate.
            shape_id = await _poll_proposal(broker)
            assert broker.get_proposal(shape_id).status == "pending"

            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            assert inst_result["ok"] is False
            assert "error" in inst_result

            # No teammates spawned.
            list_result = _content_json(await s.call_tool("list_crew", {}))
            assert list_result["teammates"] == []

            # Clean up: decline the proposal so the background task can finish.
            await broker.resolve_proposal(shape_id, "decline")
            await propose_task

    async def test_unknown_shape_id_refused(self) -> None:
        """Instantiate with a completely unknown shape_id → ok:False."""
        async with _client() as s:
            await s.initialize()
            result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": "does-not-exist"})
            )
            assert result["ok"] is False
            assert "error" in result


# ---------------------------------------------------------------------------
# AT10 — declined proposal → propose returns declined; instantiate refused
# ---------------------------------------------------------------------------


class TestDeclinedProposal:
    """AT10: propose → decline → propose_shape returns 'declined'; instantiate ok:False."""

    async def test_decline_aborts_spawn(self) -> None:
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            propose_task = asyncio.create_task(
                s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )

            shape_id = await _poll_proposal(broker)
            await broker.resolve_proposal(shape_id, "decline")

            # propose_shape should unblock with "declined".
            propose_result = _content_json(await propose_task)
            assert propose_result["ok"] is True
            assert propose_result["status"] == "declined"

            # Subsequent instantiate must refuse.
            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            assert inst_result["ok"] is False
            assert "error" in inst_result

            # Zero teammates spawned.
            list_result = _content_json(await s.call_tool("list_crew", {}))
            assert list_result["teammates"] == []

    async def test_timed_out_proposal_refused(self) -> None:
        """A timed-out proposal is also refused by instantiate_shape."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            # Use a very short timeout so it times out quickly.
            propose_result = _content_json(
                await s.call_tool(
                    "propose_shape", {"shape": _SHAPE_2NODE, "timeout_seconds": 0.05}
                )
            )
            assert propose_result["ok"] is True
            assert propose_result["status"] == "timed_out"

            shape_id = propose_result["shape_id"]
            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            assert inst_result["ok"] is False

            list_result = _content_json(await s.call_tool("list_crew", {}))
            assert list_result["teammates"] == []


# ---------------------------------------------------------------------------
# AT14 — pre-flight role resolution
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def clean_stub_known_roles():
    """Ensure stub_factory.known_roles is removed after each AT14 test."""
    yield
    if hasattr(stub_factory, "known_roles"):
        del stub_factory.known_roles  # type: ignore[attr-defined]


class TestPreflightRoleResolution:
    """AT14: factory.known_roles drives all-or-nothing pre-flight."""

    async def test_unresolvable_role_refuses_all_zero_spawn(
        self, clean_stub_known_roles: None
    ) -> None:
        """AT14 core: factory.known_roles injected; unresolvable role → ok:False, no spawn."""
        broker = Broker()

        # Only builder/sentinel are known; "nonexistent-role" is not.
        stub_factory.known_roles = lambda: ("builder", "sentinel")  # type: ignore[attr-defined]

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()

            propose_task = asyncio.create_task(
                s.call_tool("propose_shape", {"shape": _SHAPE_UNRESOLVABLE})
            )
            shape_id = await _poll_proposal(broker)
            await broker.resolve_proposal(shape_id, "approve")
            await propose_task

            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            assert inst_result["ok"] is False, inst_result
            assert "unresolved_roles" in inst_result
            assert "nonexistent-role" in inst_result["unresolved_roles"]

            # Zero teammates spawned — all-or-nothing.
            list_result = _content_json(await s.call_tool("list_crew", {}))
            assert list_result["teammates"] == []

    async def test_resolvable_via_unique_suffix_promotion_accepted(
        self, clean_stub_known_roles: None
    ) -> None:
        """AT14 extension: bare role resolves via unique ':role' suffix → spawn succeeds."""
        broker = Broker()

        # plugin:builder and plugin:sentinel — bare "builder"/"sentinel" promote uniquely.
        stub_factory.known_roles = lambda: ("plugin:builder", "plugin:sentinel")  # type: ignore[attr-defined]

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()

            propose_task = asyncio.create_task(
                s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            shape_id = await _poll_proposal(broker)
            await broker.resolve_proposal(shape_id, "approve")
            await propose_task

            # Unique suffix promotion → pre-flight passes → spawn succeeds.
            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            assert inst_result["ok"] is True, inst_result
            assert len(inst_result["crew"]) == 2

    async def test_no_known_roles_skips_preflight(self) -> None:
        """AT14 extension: factory without known_roles → pre-flight skipped, spawn succeeds."""
        broker = Broker()

        # Ensure stub_factory has no known_roles (its default state).
        assert not hasattr(stub_factory, "known_roles"), (
            "stub_factory.known_roles must be absent for this test — "
            "a previous test may have left it set"
        )

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()

            # Use _SHAPE_UNRESOLVABLE — without pre-flight it should spawn fine.
            propose_task = asyncio.create_task(
                s.call_tool("propose_shape", {"shape": _SHAPE_UNRESOLVABLE})
            )
            shape_id = await _poll_proposal(broker)
            await broker.resolve_proposal(shape_id, "approve")
            await propose_task

            # No pre-flight → spawn passes through to stub factory unconditionally.
            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            assert inst_result["ok"] is True, inst_result
            assert len(inst_result["crew"]) == 2

    async def test_ambiguous_suffix_matches_refused(
        self, clean_stub_known_roles: None
    ) -> None:
        """AT14 extension: role ambiguous (two ':role' candidates) → unresolved, no spawn."""
        broker = Broker()

        # Two plugins both export "builder" → ambiguous promotion.
        stub_factory.known_roles = lambda: ("alpha:builder", "beta:builder")  # type: ignore[attr-defined]

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()

            propose_task = asyncio.create_task(
                s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            shape_id = await _poll_proposal(broker)
            await broker.resolve_proposal(shape_id, "approve")
            await propose_task

            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            # "builder" has two candidates → unresolvable; "sentinel" also absent → unresolvable
            assert inst_result["ok"] is False
            assert "unresolved_roles" in inst_result
            assert "builder" in inst_result["unresolved_roles"]

            list_result = _content_json(await s.call_tool("list_crew", {}))
            assert list_result["teammates"] == []
