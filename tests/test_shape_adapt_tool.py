"""Tests for the adapt_shape MCP tool — ATs 23–33.

AT23: gate integration — adapt_shape success → pending proposal with adaptation_diff
AT24: iterative re-gate — adapt → approve → adapt again with base_shape_id
AT25: role resolution resolvable (swap)
AT26: role resolution unresolvable (swap)
AT27: role resolution unresolvable (augment)
AT28: no known_roles → resolution skipped
AT29: base_shape_id unknown → stage:"base"
AT30: base_shape_id not pending/approved → stage:"base"
AT31: neither/both base args → stage:"base"
AT32: unknown verb → stage:"verb"
AT33: verb structural error → stage:"adapt"

asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from claude_crew.broker import Broker
from claude_crew.factories import stub_factory
from claude_crew.server import make_server
from claude_crew.shapes import SetGate, Swap, parse_shape, shape_to_dict


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


# ---------------------------------------------------------------------------
# Shape fixtures
# ---------------------------------------------------------------------------

_SHAPE_2NODE: dict = {
    "name": "test-crew",
    "description": "A two-node shape for adapt_shape testing",
    "nodes": [
        {"slot": "implementor", "role": "builder"},
        {"slot": "reviewer", "role": "sentinel"},
    ],
    "edges": [
        {"from_slot": "implementor", "to_slot": "reviewer"},
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def clean_stub_known_roles():
    """Ensure stub_factory.known_roles and resolve_role are removed after each test."""
    yield
    if hasattr(stub_factory, "known_roles"):
        del stub_factory.known_roles  # type: ignore[attr-defined]
    if hasattr(stub_factory, "resolve_role"):
        del stub_factory.resolve_role  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# AT23 — gate integration
# ---------------------------------------------------------------------------


class TestGateIntegration:
    """AT23: adapt_shape with inline base → pending proposal registered."""

    async def test_inline_base_set_gate_success(self) -> None:
        """AT23: inline base_shape + verb=set_gate → ok:True, proposal registered."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            result = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "set_gate",
                        "params": {
                            "from_slot": "implementor",
                            "to_slot": "reviewer",
                            "mode": "tee",
                        },
                        "base_shape": _SHAPE_2NODE,
                    },
                )
            )

            assert result["ok"] is True, result
            assert result["status"] == "pending"
            assert "shape_id" in result
            assert "diff" in result
            assert "shape" in result

            # diff matches AdaptationDiff.render() for this verb
            base = parse_shape(_SHAPE_2NODE)
            _, expected_diff_obj = SetGate(
                from_slot="implementor", to_slot="reviewer", mode="tee"
            ).apply(base)
            expected_diff_str = expected_diff_obj.render()
            assert result["diff"] == expected_diff_str

            # shape is the shape_to_dict form of the new shape
            new_shape, _ = SetGate(
                from_slot="implementor", to_slot="reviewer", mode="tee"
            ).apply(base)
            assert result["shape"] == shape_to_dict(new_shape)

            # broker has exactly one new pending proposal with adaptation_diff
            snap = broker.snapshot()
            assert len(snap.shape_proposals) == 1
            proposal = snap.shape_proposals[0]
            assert proposal.status == "pending"
            assert proposal.adaptation_diff == expected_diff_str
            assert result["shape_id"] == proposal.shape_id


# ---------------------------------------------------------------------------
# AT24 — iterative re-gate loop
# ---------------------------------------------------------------------------


class TestIterativeReGateLoop:
    """AT24: adapt → approve → adapt again with base_shape_id → new proposal."""

    async def test_iterative_adapt_gate_loop(self) -> None:
        """AT24: swap registers proposal; after approve, set_gate on that id works."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            # First adapt: swap reviewer's role
            r1 = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "swap",
                        "params": {"slot": "reviewer", "role": "security-reviewer"},
                        "base_shape": _SHAPE_2NODE,
                    },
                )
            )
            assert r1["ok"] is True, r1
            shape_id_1 = r1["shape_id"]

            # Approve the first proposal
            approve_r = _content_json(
                await s.call_tool(
                    "resolve_shape",
                    {"shape_id": shape_id_1, "decision": "approve"},
                )
            )
            assert approve_r["ok"] is True
            assert approve_r["status"] == "approved"

            # Second adapt: use the approved proposal's shape_id as base
            r2 = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "set_gate",
                        "params": {
                            "from_slot": "implementor",
                            "to_slot": "reviewer",
                            "mode": "tee",
                        },
                        "base_shape_id": shape_id_1,
                    },
                )
            )
            assert r2["ok"] is True, r2
            assert r2["status"] == "pending"
            shape_id_2 = r2["shape_id"]

            # The two proposals have distinct shape_ids
            assert shape_id_2 != shape_id_1

            # broker now has two proposals
            snap = broker.snapshot()
            assert len(snap.shape_proposals) == 2
            ids = {p.shape_id for p in snap.shape_proposals}
            assert shape_id_1 in ids
            assert shape_id_2 in ids


# ---------------------------------------------------------------------------
# AT25–28 — role resolution (swap & augment)
# ---------------------------------------------------------------------------


class TestRoleResolution:
    """AT25–28: role resolution for swap and augment verbs."""

    async def test_resolvable_swap_role_accepted(
        self, clean_stub_known_roles: None
    ) -> None:
        """AT25: known_roles includes target role → swap succeeds, proposal registered."""
        broker = Broker()
        stub_factory.known_roles = lambda: (  # type: ignore[attr-defined]
            "builder",
            "sentinel",
            "security-reviewer",
        )

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()

            result = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "swap",
                        "params": {"slot": "reviewer", "role": "security-reviewer"},
                        "base_shape": _SHAPE_2NODE,
                    },
                )
            )
            assert result["ok"] is True, result

            snap = broker.snapshot()
            assert len(snap.shape_proposals) == 1

    async def test_unresolvable_swap_role_rejected(
        self, clean_stub_known_roles: None
    ) -> None:
        """AT26: known_roles excludes target role → stage:adapt, no proposal."""
        broker = Broker()
        stub_factory.known_roles = lambda: ("builder", "sentinel")  # type: ignore[attr-defined]

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()

            result = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "swap",
                        "params": {
                            "slot": "reviewer",
                            "role": "nonexistent-role",
                        },
                        "base_shape": _SHAPE_2NODE,
                    },
                )
            )
            assert result["ok"] is False, result
            assert result["stage"] == "adapt"
            assert "unresolved_roles" in result
            assert "nonexistent-role" in result["unresolved_roles"]

            snap = broker.snapshot()
            assert len(snap.shape_proposals) == 0

    async def test_unresolvable_augment_role_rejected(
        self, clean_stub_known_roles: None
    ) -> None:
        """AT27: known_roles excludes augment node role → stage:adapt, no proposal."""
        broker = Broker()
        stub_factory.known_roles = lambda: ("builder", "sentinel")  # type: ignore[attr-defined]

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()

            result = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "augment",
                        "params": {
                            "node": {
                                "slot": "reviewer2",
                                "role": "nonexistent-role",
                            },
                            "edges": [
                                {
                                    "from_slot": "implementor",
                                    "to_slot": "reviewer2",
                                }
                            ],
                        },
                        "base_shape": _SHAPE_2NODE,
                    },
                )
            )
            assert result["ok"] is False, result
            assert result["stage"] == "adapt"
            assert "unresolved_roles" in result
            assert "nonexistent-role" in result["unresolved_roles"]

            snap = broker.snapshot()
            assert len(snap.shape_proposals) == 0

    async def test_no_known_roles_skips_resolution(self) -> None:
        """AT28: no known_roles attribute → resolution skipped, any role is accepted."""
        broker = Broker()
        # Verify stub_factory has no known_roles (its default state after teardown)
        assert not hasattr(stub_factory, "known_roles"), (
            "stub_factory.known_roles must be absent for AT28 — "
            "a previous test may have left it set"
        )

        async with _client(broker=broker, factory=stub_factory) as s:
            await s.initialize()

            result = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "swap",
                        "params": {"slot": "reviewer", "role": "anything"},
                        "base_shape": _SHAPE_2NODE,
                    },
                )
            )
            assert result["ok"] is True, result

            snap = broker.snapshot()
            assert len(snap.shape_proposals) == 1


# ---------------------------------------------------------------------------
# AT29–31 — base resolution failures
# ---------------------------------------------------------------------------


class TestBaseGuards:
    """AT29–31: failures at the base-resolution stage."""

    async def test_unknown_base_shape_id(self) -> None:
        """AT29: base_shape_id that doesn't exist → stage:base, no proposal."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            result = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "drop",
                        "params": {"slot": "x"},
                        "base_shape_id": "does-not-exist",
                    },
                )
            )
            assert result["ok"] is False
            assert result["stage"] == "base"

            snap = broker.snapshot()
            assert len(snap.shape_proposals) == 0

    async def test_instantiated_proposal_rejected(self) -> None:
        """AT30: base_shape_id with status 'instantiated' → stage:base, no new proposal."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            # Propose → approve → instantiate
            propose_r = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            orig_id = propose_r["shape_id"]
            await s.call_tool(
                "resolve_shape", {"shape_id": orig_id, "decision": "approve"}
            )
            await s.call_tool("instantiate_shape", {"shape_id": orig_id})
            assert broker.get_proposal(orig_id).status == "instantiated"

            result = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "swap",
                        "params": {"slot": "reviewer", "role": "sentinel"},
                        "base_shape_id": orig_id,
                    },
                )
            )
            assert result["ok"] is False
            assert result["stage"] == "base"

            # No new proposals beyond the original instantiated one
            snap = broker.snapshot()
            new_proposals = [p for p in snap.shape_proposals if p.shape_id != orig_id]
            assert len(new_proposals) == 0

    async def test_declined_proposal_rejected(self) -> None:
        """AT30: base_shape_id with status 'declined' → stage:base."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            propose_r = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            orig_id = propose_r["shape_id"]
            await s.call_tool(
                "resolve_shape", {"shape_id": orig_id, "decision": "decline"}
            )

            result = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "swap",
                        "params": {"slot": "reviewer", "role": "sentinel"},
                        "base_shape_id": orig_id,
                    },
                )
            )
            assert result["ok"] is False
            assert result["stage"] == "base"

    async def test_timed_out_proposal_rejected(self) -> None:
        """AT30: base_shape_id with status 'timed_out' → stage:base."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            propose_r = _content_json(
                await s.call_tool(
                    "propose_shape",
                    {"shape": _SHAPE_2NODE, "wait": True, "timeout_seconds": 0.05},
                )
            )
            assert propose_r["status"] == "timed_out"
            orig_id = propose_r["shape_id"]

            result = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "swap",
                        "params": {"slot": "reviewer", "role": "sentinel"},
                        "base_shape_id": orig_id,
                    },
                )
            )
            assert result["ok"] is False
            assert result["stage"] == "base"

    async def test_neither_base_arg_supplied(self) -> None:
        """AT31a: neither base_shape_id nor base_shape → stage:base, no proposal."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            result = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "drop",
                        "params": {"slot": "x"},
                    },
                )
            )
            assert result["ok"] is False
            assert result["stage"] == "base"

            snap = broker.snapshot()
            assert len(snap.shape_proposals) == 0

    async def test_both_base_args_supplied(self) -> None:
        """AT31b: both base_shape_id and base_shape supplied → stage:base."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            # Register a proposal to get a valid shape_id
            propose_r = _content_json(
                await s.call_tool("propose_shape", {"shape": _SHAPE_2NODE})
            )
            existing_id = propose_r["shape_id"]

            result = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "drop",
                        "params": {"slot": "x"},
                        "base_shape_id": existing_id,
                        "base_shape": _SHAPE_2NODE,
                    },
                )
            )
            assert result["ok"] is False
            assert result["stage"] == "base"

            # Only the pre-existing proposal, no new one
            snap = broker.snapshot()
            assert len(snap.shape_proposals) == 1


# ---------------------------------------------------------------------------
# AT32 — unknown verb
# ---------------------------------------------------------------------------


class TestVerbGuard:
    """AT32: unknown verb → stage:verb, no proposal."""

    async def test_unknown_verb_rejected(self) -> None:
        """AT32: verb='frobnicate' → stage:verb, no proposal registered."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            result = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "frobnicate",
                        "params": {},
                        "base_shape": _SHAPE_2NODE,
                    },
                )
            )
            assert result["ok"] is False
            assert result["stage"] == "verb"

            snap = broker.snapshot()
            assert len(snap.shape_proposals) == 0


# ---------------------------------------------------------------------------
# AT33 — verb structural error → stage:adapt
# ---------------------------------------------------------------------------


class TestAdaptGuard:
    """AT33: verb's ShapeValidationError surfaces as stage:adapt."""

    async def test_illegal_drop_slot_surfaces_as_adapt(self) -> None:
        """AT33: drop with non-existent slot → stage:adapt, no proposal."""
        broker = Broker()
        async with _client(broker=broker) as s:
            await s.initialize()

            result = _content_json(
                await s.call_tool(
                    "adapt_shape",
                    {
                        "verb": "drop",
                        "params": {"slot": "ghost"},
                        "base_shape": _SHAPE_2NODE,
                    },
                )
            )
            assert result["ok"] is False
            assert result["stage"] == "adapt"

            snap = broker.snapshot()
            assert len(snap.shape_proposals) == 0
