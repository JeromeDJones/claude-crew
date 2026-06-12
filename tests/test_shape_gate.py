"""Integration tests for propose_shape, resolve_shape, list_pending_shapes,
and instantiate_shape MCP tools — ATs 1, 2, 6, 8, 9, 10, 14.

AT1:  non-blocking propose — propose_shape returns status:"pending" immediately.
AT2:  chat-channel approval — resolve_shape approve/decline + sad paths.
AT6:  list_pending_shapes — lists pending proposals; empty when none.
AT8:  happy path — propose → approve → instantiate spawns exactly 2 teammates
       and records the topology in broker.snapshot().
AT9:  instantiate on a pending proposal returns ok:False, no spawn.
AT10: declined proposal — resolve → "declined", instantiate ok:False.
AT14: pre-flight role resolution — unresolvable role → ok:False + unresolved_roles,
       zero spawn; also exercises unique-suffix promotion and no-known_roles skip.

Hardening tests:
- Fix 2: shared resolve_role accessor drives pre-flight (unique suffix and ambiguous).
- Fix 4: transactional spawn — partial crew rolled back on mid-loop failure.

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
# AT1 — non-blocking propose
# ---------------------------------------------------------------------------


class TestNonBlockingPropose:
    """AT1: propose_shape returns pending immediately without awaiting resolution."""

    async def test_propose_shape_returns_pending_immediately(self) -> None:
        """AT1: default wait=False → returns {ok:True, status:'pending', shape_id} at once."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )

            assert result["ok"] is True, result
            assert result["status"] == "pending", result
            assert "shape_id" in result

            shape_id = result["shape_id"]
            # Proposal is still pending in the broker immediately after the call.
            proposal = broker.get_proposal(shape_id)
            assert proposal is not None
            assert proposal.status == "pending"

    async def test_propose_shape_parse_error_returns_ok_false(self) -> None:
        """AT1 guard: a malformed shape dict → {ok:False, stage:'parse'}."""
        async with _client() as s:
            await s.initialize()
            result = _content_json(
                await s.call_tool("propose_shape", {"shape": {"name": "x"}})
            )
            assert result["ok"] is False
            assert result["stage"] == "parse"
            assert "error" in result


# ---------------------------------------------------------------------------
# AT2 — chat-channel approval
# ---------------------------------------------------------------------------


class TestChatChannelApproval:
    """AT2: resolve_shape approve/decline + sad paths."""

    async def test_resolve_approve(self) -> None:
        """AT2 happy: resolve_shape approve → status approved, instantiate works."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            assert propose_result["ok"] is True
            shape_id = propose_result["shape_id"]

            resolve_result = _content_json(
                await s.call_tool(
                    "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
                )
            )
            assert resolve_result["ok"] is True, resolve_result
            assert resolve_result["status"] == "approved"
            assert broker.get_proposal(shape_id).status == "approved"

            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            assert inst_result["ok"] is True, inst_result

    async def test_resolve_decline(self) -> None:
        """AT2 happy: resolve_shape decline → status declined, instantiate fails."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            shape_id = propose_result["shape_id"]

            resolve_result = _content_json(
                await s.call_tool(
                    "resolve_shape", {"shape_id": shape_id, "decision": "decline"}
                )
            )
            assert resolve_result["ok"] is True, resolve_result
            assert resolve_result["status"] == "declined"
            assert broker.get_proposal(shape_id).status == "declined"

            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            assert inst_result["ok"] is False

    async def test_resolve_unknown_shape_id(self) -> None:
        """AT2 sad: resolve_shape on unknown shape_id → {ok:False, error}."""
        async with _client() as s:
            await s.initialize()
            result = _content_json(
                await s.call_tool(
                    "resolve_shape",
                    {"shape_id": "does-not-exist", "decision": "approve"},
                )
            )
            assert result["ok"] is False
            assert "error" in result
            assert "unknown shape_id" in result["error"]

    async def test_resolve_already_resolved(self) -> None:
        """AT2 sad: resolve_shape on already-resolved shape_id → {ok:False, error}."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            shape_id = propose_result["shape_id"]
            await s.call_tool(
                "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
            )

            # Second resolve on now-approved proposal → pending-only guard.
            result = _content_json(
                await s.call_tool(
                    "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
                )
            )
            assert result["ok"] is False
            assert "error" in result
            assert "not pending" in result["error"]

    async def test_resolve_invalid_decision(self) -> None:
        """AT2 sad: resolve_shape with invalid decision → {ok:False, error}."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            shape_id = propose_result["shape_id"]

            result = _content_json(
                await s.call_tool(
                    "resolve_shape", {"shape_id": shape_id, "decision": "maybe"}
                )
            )
            assert result["ok"] is False
            assert "error" in result
            assert "approve" in result["error"] or "decline" in result["error"]


# ---------------------------------------------------------------------------
# AT6 — list_pending_shapes
# ---------------------------------------------------------------------------


class TestListPendingShapes:
    """AT6: list_pending_shapes returns pending proposals; empty when none."""

    async def test_two_pending_proposals(self) -> None:
        """AT6: two pending proposals → list returns both with required fields."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            r1 = _content_json(await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE}))
            r2 = _content_json(await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE}))
            assert r1["ok"] and r2["ok"]

            list_result = _content_json(await s.call_tool("list_pending_shapes", {}))
            assert list_result["ok"] is True
            pending = list_result["pending"]
            assert len(pending) == 2, pending

            shape_ids = {p["shape_id"] for p in pending}
            assert r1["shape_id"] in shape_ids
            assert r2["shape_id"] in shape_ids

            for entry in pending:
                assert "shape_id" in entry
                assert "name" in entry
                assert "crew_id" in entry
                assert "mermaid" in entry
                assert "summary" in entry
                assert entry["name"] == "test-crew"

    async def test_no_pending_proposals(self) -> None:
        """AT6: no proposals → list returns empty list."""
        async with _client() as s:
            await s.initialize()
            result = _content_json(await s.call_tool("list_pending_shapes", {}))
            assert result["ok"] is True
            assert result["pending"] == []

    async def test_resolved_not_in_list(self) -> None:
        """AT6: resolved proposal is not listed as pending."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            r = _content_json(await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE}))
            shape_id = r["shape_id"]
            await s.call_tool(
                "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
            )

            result = _content_json(await s.call_tool("list_pending_shapes", {}))
            assert result["ok"] is True
            pending_ids = {p["shape_id"] for p in result["pending"]}
            assert shape_id not in pending_ids


# ---------------------------------------------------------------------------
# AT8 — happy path: propose → approve → instantiate
# ---------------------------------------------------------------------------


class TestProposeApproveInstantiate:
    """AT8: full happy path through the shape gate."""

    async def test_propose_approve_instantiate_spawns_two_teammates(self) -> None:
        """propose → approve (via resolve_shape) → instantiate → 2 teammates + topology."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            # propose_shape returns pending immediately (non-blocking).
            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            assert propose_result["ok"] is True, propose_result
            assert propose_result["status"] == "pending"
            shape_id = propose_result["shape_id"]

            # Approve via resolve_shape (chat channel).
            resolve_result = _content_json(
                await s.call_tool(
                    "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
                )
            )
            assert resolve_result["ok"] is True, resolve_result
            assert resolve_result["status"] == "approved"

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

            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            shape_id = propose_result["shape_id"]
            await s.call_tool(
                "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
            )

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

            # propose_shape returns pending immediately.
            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            assert propose_result["ok"] is True
            shape_id = propose_result["shape_id"]
            assert broker.get_proposal(shape_id).status == "pending"

            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            assert inst_result["ok"] is False
            assert "error" in inst_result

            # No teammates spawned.
            list_result = _content_json(await s.call_tool("list_crew", {}))
            assert list_result["teammates"] == []

            # Clean up: decline so broker state is tidy.
            await s.call_tool(
                "resolve_shape", {"shape_id": shape_id, "decision": "decline"}
            )

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
# AT10 — declined proposal → instantiate refused
# ---------------------------------------------------------------------------


class TestDeclinedProposal:
    """AT10: propose → decline → instantiate ok:False."""

    async def test_decline_aborts_spawn(self) -> None:
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            assert propose_result["ok"] is True
            shape_id = propose_result["shape_id"]

            # Decline via resolve_shape.
            resolve_result = _content_json(
                await s.call_tool(
                    "resolve_shape", {"shape_id": shape_id, "decision": "decline"}
                )
            )
            assert resolve_result["ok"] is True
            assert resolve_result["status"] == "declined"

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

            # Use wait=True with a very short timeout so it times out quickly.
            propose_result = _content_json(
                await s.call_tool(
                    "propose_shape",
                    {"shape": _SHAPE_2NODE, "wait": True, "timeout_seconds": 0.05},
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
    """Ensure stub_factory.known_roles and resolve_role are removed after each AT14 test."""
    yield
    if hasattr(stub_factory, "known_roles"):
        del stub_factory.known_roles  # type: ignore[attr-defined]
    if hasattr(stub_factory, "resolve_role"):
        del stub_factory.resolve_role  # type: ignore[attr-defined]


def _make_resolve_role(known: tuple[str, ...]):
    """Build a standalone _resolve_role function for a given set of known roles.

    Mirrors factories._resolve_role semantics exactly:
    - Exact match → return as-is.
    - Unique ':role' suffix → return promoted key.
    - Multiple candidates or zero → return the original (which will not be in known).
    """
    known_set = set(known)

    def resolve_role(requested: str) -> str:
        if requested in known_set:
            return requested
        candidates = sorted(k for k in known_set if k.endswith(f":{requested}"))
        if len(candidates) == 1:
            return candidates[0]
        return requested

    return resolve_role


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

            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_UNRESOLVABLE})
            )
            shape_id = propose_result["shape_id"]
            await s.call_tool(
                "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
            )

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

            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            shape_id = propose_result["shape_id"]
            await s.call_tool(
                "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
            )

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
            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_UNRESOLVABLE})
            )
            shape_id = propose_result["shape_id"]
            await s.call_tool(
                "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
            )

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

            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            shape_id = propose_result["shape_id"]
            await s.call_tool(
                "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
            )

            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            # "builder" has two candidates → unresolvable; "sentinel" also absent → unresolvable
            assert inst_result["ok"] is False
            assert "unresolved_roles" in inst_result
            assert "builder" in inst_result["unresolved_roles"]

            list_result = _content_json(await s.call_tool("list_crew", {}))
            assert list_result["teammates"] == []


# ---------------------------------------------------------------------------
# Fix 2 — shared resolve_role accessor drives pre-flight
# ---------------------------------------------------------------------------

# 3-node shape: allows testing partial-crew rollback (Fix 4)
_SHAPE_3NODE: dict = {
    "name": "three-node-crew",
    "description": "A three-node shape for rollback testing",
    "nodes": [
        {"slot": "node-a", "role": "builder"},
        {"slot": "node-b", "role": "sentinel"},
        {"slot": "node-c", "role": "builder"},
    ],
    "edges": [],
}


class TestSharedResolveRoleAccessor:
    """Fix 2: factory.resolve_role drives pre-flight when present."""

    async def test_shared_resolver_unique_suffix_promotion_accepted(
        self, clean_stub_known_roles: None
    ) -> None:
        """Fix 2: unique ':role' suffix promotion via shared factory.resolve_role succeeds."""
        broker = Broker()

        known = ("plugin:builder", "plugin:sentinel")
        stub_factory.known_roles = lambda: known  # type: ignore[attr-defined]
        stub_factory.resolve_role = _make_resolve_role(known)  # type: ignore[attr-defined]

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()

            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            shape_id = propose_result["shape_id"]
            await s.call_tool(
                "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
            )

            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            # Unique suffix promotion via shared resolver → pre-flight passes
            assert inst_result["ok"] is True, inst_result
            assert len(inst_result["crew"]) == 2

    async def test_shared_resolver_ambiguous_role_refused(
        self, clean_stub_known_roles: None
    ) -> None:
        """Fix 2: ambiguous promotion via shared factory.resolve_role → ok:False, no spawn."""
        broker = Broker()

        # Two plugins both export "builder" → resolve_role returns original "builder"
        # which is NOT in known → pre-flight refuses.
        known = ("a:builder", "b:builder")
        stub_factory.known_roles = lambda: known  # type: ignore[attr-defined]
        stub_factory.resolve_role = _make_resolve_role(known)  # type: ignore[attr-defined]

        # _SHAPE_2NODE uses "builder" and "sentinel"; both ambiguous/absent here.
        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()

            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            shape_id = propose_result["shape_id"]
            await s.call_tool(
                "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
            )

            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )
            assert inst_result["ok"] is False
            assert "unresolved_roles" in inst_result
            assert "builder" in inst_result["unresolved_roles"]

            # Zero teammates spawned.
            list_result = _content_json(await s.call_tool("list_crew", {}))
            assert list_result["teammates"] == []


# ---------------------------------------------------------------------------
# Fix 4 — transactional spawn: partial crew rolled back on failure
# ---------------------------------------------------------------------------


class TestTransactionalSpawn:
    """Fix 4: mid-loop spawn failure rolls back already-spawned teammates."""

    async def test_spawn_failure_rolls_back_partial_crew(self) -> None:
        """Fix 4: broker.spawn_teammate raises on 2nd call → ok:False, 0 alive, proposal still approved."""
        broker = Broker()

        # Patch spawn_teammate to fail on the 2nd call.
        call_count = 0
        original_spawn = broker.spawn_teammate

        async def failing_spawn(*args: object, **kwargs: object) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("injected spawn failure on call #2")
            return await original_spawn(*args, **kwargs)

        broker.spawn_teammate = failing_spawn  # type: ignore[method-assign]

        async with _client(broker=broker) as s:
            await s.initialize()

            propose_result = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_3NODE})
            )
            shape_id = propose_result["shape_id"]
            await s.call_tool(
                "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
            )

            inst_result = _content_json(
                await s.call_tool("instantiate_shape", {"shape_id": shape_id})
            )

            # The call must fail gracefully.
            assert inst_result["ok"] is False, inst_result
            assert "spawn failed mid-instantiation" in inst_result.get("error", "")

            # Zero live teammates after rollback.
            list_result = _content_json(await s.call_tool("list_crew", {}))
            alive = [t for t in list_result["teammates"] if t["alive"]]
            assert alive == [], f"expected no live teammates, got: {alive}"

            # Proposal must remain 'approved' so a retry can re-attempt.
            proposal = broker.get_proposal(shape_id)
            assert proposal is not None
            assert proposal.status == "approved", (
                f"proposal should remain 'approved' after rollback, got: {proposal.status!r}"
            )
