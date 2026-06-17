"""Live SDK tests for the adapt_shape MCP tool and adapt→approve→instantiate flow — M3.

These tests prove two real seams that the existing stub-mode tests cannot reach:
(a) adapt_shape role-resolution against the REAL merged pack's factory.known_roles().
(b) An adapted shape, once approved, actually instantiates a real crew via the cloud API.

Gated by CLAUDE_CREW_LIVE_TESTS=1 — they spawn real ``claude`` subprocesses against
the cloud Anthropic API.  That is intentional.  Do not run in CI without the gate set.

asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.factories import default_factory
from claude_crew.server import make_server

# ---------------------------------------------------------------------------
# Gate — skip the entire module unless CLAUDE_CREW_LIVE_TESTS=1
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1",
    reason="live API gated; set CLAUDE_CREW_LIVE_TESTS=1 to run",
)

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


async def _wait_for_lead(broker: Broker, count: int, timeout: float = 120.0) -> None:
    """Poll until the lead inbox has at least ``count`` messages or timeout expires."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if len(broker.get_messages(recipient=LEAD_ID)) >= count:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"timed out waiting for {count} lead message(s); "
        f"got {len(broker.get_messages(recipient=LEAD_ID))}"
    )


async def _send_and_wait(broker: Broker, tid: str, prompt: str, expected_count: int) -> Envelope:
    """Send a prompt to teammate ``tid`` and wait for ``expected_count`` lead replies."""
    await broker.send(Envelope(
        id=new_message_id(), seq=0,
        sender=LEAD_ID, recipient=tid, timestamp=0.0,
        payload=prompt,
    ))
    await _wait_for_lead(broker, expected_count)
    msgs = broker.get_messages(recipient=LEAD_ID)
    return msgs[-1]


async def _await_reply_from(
    broker: Broker, tid: str, timeout: float = 120.0
) -> Envelope:
    """Wait for any message in the lead inbox that was sent BY ``tid``.

    This is safer than counting total messages because broker system
    notifications (shape_resolved, shape_instantiated, etc.) also land in the
    lead inbox and would cause a generic count-based poll to return early with
    the wrong envelope.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        for msg in broker.get_messages(recipient=LEAD_ID):
            if msg.sender == tid:
                return msg
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"timed out after {timeout}s waiting for a reply from teammate {tid!r}; "
        f"lead inbox: {[m.sender for m in broker.get_messages(recipient=LEAD_ID)]}"
    )


# ---------------------------------------------------------------------------
# Shape fixtures
# ---------------------------------------------------------------------------

# 2-node base shape using only bundled roles (explorer, general, planner).
# These are always present on any checkout and carry no Bash/Task tools,
# making them cheap to instantiate.  Edge defaults to "gated" mode.
_BASE_SHAPE: dict = {
    "name": "live-test-crew",
    "description": "Base shape for live M3 adaptation tests",
    "nodes": [
        {"slot": "explorer", "role": "explorer"},
        {"slot": "general", "role": "general"},
    ],
    "edges": [
        {"from_slot": "explorer", "to_slot": "general"},
    ],
}

# ---------------------------------------------------------------------------
# AT-LIVE-1 — swap with a resolvable role resolves against real factory
# ---------------------------------------------------------------------------


class TestSwapRoleResolvesAgainstRealFactory:
    """AT-LIVE-1: adapt_shape swap with a resolvable bundled role → ok:True, pending."""

    async def test_swap_role_resolves_against_real_factory(self, monkeypatch: Any) -> None:
        """Swap general→planner via real factory.known_roles() succeeds.

        The conftest autouse fixture pins CLAUDE_CREW_TEAMMATE_MODE=stub; we
        override it to "sdk" so default_factory() builds the real merged pack
        with known_roles/resolve_role attached.  No actual teammate is spawned
        (adapt_shape registers a proposal, not a crew).
        """
        # Override the conftest autouse stub-mode pin.
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
        real_factory = default_factory()

        # Sanity: the merged pack must carry the three bundled roles.
        known = real_factory.known_roles()
        assert "explorer" in known, f"explorer not in known_roles: {known}"
        assert "general" in known, f"general not in known_roles: {known}"
        assert "planner" in known, f"planner not in known_roles: {known}"

        broker = Broker()
        try:
            async with _client(broker=broker, factory=real_factory) as s:
                await s.initialize()

                result = _content_json(
                    await s.call_tool(
                        "adapt_shape",
                        {
                            "verb": "swap",
                            "params": {"slot": "general", "role": "planner"},
                            "base_shape": _BASE_SHAPE,
                        },
                    )
                )

                assert result["ok"] is True, f"expected ok:True, got: {result}"
                assert result["status"] == "pending"
                assert "shape_id" in result
                assert "diff" in result
                assert "shape" in result

                shape_id = result["shape_id"]

                # The broker snapshot must contain a pending proposal for the adapted shape.
                snap = broker.snapshot()
                pending = [p for p in snap.shape_proposals if p.status == "pending"]
                assert len(pending) == 1, (
                    f"expected exactly 1 pending proposal, got: {snap.shape_proposals}"
                )
                assert pending[0].shape_id == shape_id
        finally:
            await broker.shutdown_all()


# ---------------------------------------------------------------------------
# AT-LIVE-2 — swap with a bogus role is rejected by real factory
# ---------------------------------------------------------------------------


class TestSwapUnresolvableRoleRejectedLive:
    """AT-LIVE-2: adapt_shape swap with an unresolvable role → ok:False, no proposal."""

    async def test_swap_unresolvable_role_rejected_live(self, monkeypatch: Any) -> None:
        """Swap with a role absent from the real merged pack → stage:'adapt'.

        This is the real-factory analogue of the stub-based AT26 test.  The
        factory.known_roles() set contains only bundled + user-level agents;
        "definitely-not-a-real-role-xyz" is guaranteed absent.
        """
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
        real_factory = default_factory()

        broker = Broker()
        try:
            async with _client(broker=broker, factory=real_factory) as s:
                await s.initialize()

                result = _content_json(
                    await s.call_tool(
                        "adapt_shape",
                        {
                            "verb": "swap",
                            "params": {
                                "slot": "general",
                                "role": "definitely-not-a-real-role-xyz",
                            },
                            "base_shape": _BASE_SHAPE,
                        },
                    )
                )

                assert result["ok"] is False, f"expected ok:False, got: {result}"
                assert result["stage"] == "adapt"
                assert "unresolved_roles" in result
                assert "definitely-not-a-real-role-xyz" in result["unresolved_roles"]

                # No proposal must be registered on a resolution failure.
                snap = broker.snapshot()
                assert not snap.shape_proposals, (
                    f"expected no proposals after adapt failure, "
                    f"got: {snap.shape_proposals}"
                )
        finally:
            await broker.shutdown_all()


# ---------------------------------------------------------------------------
# AT-LIVE-3 — end-to-end: adapt → approve → instantiate real crew
# ---------------------------------------------------------------------------


class TestAdaptApproveInstantiateRealCrew:
    """AT-LIVE-3: full end-to-end flow — adapted shape spawns a real crew."""

    async def test_adapt_approve_instantiate_real_crew(self, monkeypatch: Any) -> None:
        """adapt_shape swap → pending; approve → approved; instantiate → real crew.

        Verifies:
        - adapt_shape returns ok:True with a shape_id.
        - resolve_shape(approve) transitions proposal to approved.
        - instantiate_shape spawns exactly 2 teammates with real teammate_ids.
        - Slot names are preserved from the base shape; the swapped slot carries
          the new role (slot "general" → role "planner").
        - broker.snapshot().topologies records the adapted shape with correct
          slot→teammate mapping.
        """
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
        real_factory = default_factory()

        broker = Broker()
        try:
            async with _client(broker=broker, factory=real_factory) as s:
                await s.initialize()

                # ── Step 1: adapt — swap general slot's role from "general" → "planner" ──
                adapt_result = _content_json(
                    await s.call_tool(
                        "adapt_shape",
                        {
                            "verb": "swap",
                            "params": {"slot": "general", "role": "planner"},
                            "base_shape": _BASE_SHAPE,
                        },
                    )
                )
                assert adapt_result["ok"] is True, (
                    f"adapt_shape failed at step 1: {adapt_result}"
                )
                shape_id = adapt_result["shape_id"]
                assert adapt_result["status"] == "pending"

                # ── Step 2: approve the pending proposal ────────────────────────────────
                resolve_result = _content_json(
                    await s.call_tool(
                        "resolve_shape",
                        {"shape_id": shape_id, "decision": "approve"},
                    )
                )
                assert resolve_result["ok"] is True, (
                    f"resolve_shape failed at step 2: {resolve_result}"
                )
                assert resolve_result["status"] == "approved"

                # ── Step 3: instantiate — spawns real SDK subprocesses ───────────────────
                # Allow generous timeout: two SDK subprocess spawns can each take 30–60s.
                inst_result = _content_json(
                    await asyncio.wait_for(
                        s.call_tool("instantiate_shape", {"shape_id": shape_id}),
                        timeout=180.0,
                    )
                )
                assert inst_result["ok"] is True, (
                    f"instantiate_shape failed at step 3: {inst_result}"
                )
                assert inst_result["shape_id"] == shape_id

                # ── Crew structure ──────────────────────────────────────────────────────
                crew = inst_result["crew"]
                assert len(crew) == 2, f"expected 2 crew entries, got: {crew}"

                by_slot = {c["slot"]: c for c in crew}

                # Slot names are preserved from the base shape (swap changes role, not slot).
                assert set(by_slot.keys()) == {"explorer", "general"}, (
                    f"unexpected slots: {set(by_slot.keys())}"
                )
                # "explorer" slot keeps its original role.
                assert by_slot["explorer"]["role"] == "explorer"
                # "general" slot now has role "planner" (the swapped role).
                assert by_slot["general"]["role"] == "planner"

                # Each entry carries a real teammate_id assigned by the broker.
                for entry in crew:
                    assert entry["teammate_id"].startswith("t-"), (
                        f"teammate_id has unexpected format: {entry['teammate_id']}"
                    )

                # ── Step 4: prove the adapted crew ACTUALLY WORKS ───────────────────────
                # Send a trivial prompt to the "general" slot teammate (role: planner)
                # and await a REAL model response.  This distinguishes a functioning
                # live crew from a fire-and-forget spawn that merely handed back
                # synchronous crew metadata.
                #
                # Important: resolve_shape (and possibly instantiate_shape) emit
                # broker notifications into the lead inbox.  Baseline the count
                # NOW so we wait for one message BEYOND the pre-existing set —
                # that new message is the teammate's actual reply.
                #
                # A stub teammate would echo-reply almost instantly (<1s); a real
                # SDK subprocess needs 10–60s to launch, run the model, and return.
                # Send prompt to the "general" slot teammate (role: planner).
                general_tid = by_slot["general"]["teammate_id"]
                await broker.send(Envelope(
                    id=new_message_id(), seq=0,
                    sender=LEAD_ID, recipient=general_tid, timestamp=0.0,
                    payload="Reply with exactly the word READY and nothing else.",
                ))
                # Wait specifically for a reply FROM the teammate (not from a
                # system notification like shape_resolved / shape_instantiated).
                # A real SDK subprocess takes 10–90s to launch, run the model,
                # and return; the timeout is set generously at 120s.
                reply = await _await_reply_from(broker, general_tid, timeout=120.0)

                # Assert the reply is a real, non-empty model response.
                # SdkTeammate payloads are dicts with a "text" key;
                # guard against any unexpected shape defensively.
                reply_text = (
                    reply.payload.get("text", "")
                    if isinstance(reply.payload, dict)
                    else str(reply.payload)
                )
                assert reply_text.strip(), (
                    f"expected a non-empty model reply from adapted crew member "
                    f"(role=planner, id={general_tid!r}), "
                    f"got payload: {reply.payload!r}"
                )

                # ── Topology recorded in broker snapshot ────────────────────────────────
                snap = broker.snapshot()
                assert len(snap.topologies) == 1, (
                    f"expected 1 topology, got: {snap.topologies}"
                )
                topo = snap.topologies[0]
                assert topo.shape_name == "live-test-crew"

                # Both slots must appear in the slot→teammate map.
                assert set(topo.slot_to_teammate.keys()) == {"explorer", "general"}, (
                    f"unexpected topology slots: {set(topo.slot_to_teammate.keys())}"
                )
                # The map values must match the teammate_ids from the crew listing.
                assert (
                    topo.slot_to_teammate["explorer"]
                    == by_slot["explorer"]["teammate_id"]
                )
                assert (
                    topo.slot_to_teammate["general"]
                    == by_slot["general"]["teammate_id"]
                )
        finally:
            # await shutdown so all background tasks (liveness loops, subprocess
            # waiters) are cancelled and joined before the event loop closes.
            # Without this, exiting while SdkTeammate background tasks are
            # pending produces "Task was destroyed but it is pending!" warnings.
            await broker.shutdown_all()
