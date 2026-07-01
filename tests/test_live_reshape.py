"""Live regression guards for reshape_crew — AT 17 and AT 18.

These tests gate on CLAUDE_CREW_LIVE_TESTS=1 and spawn real ``claude`` subprocesses.
They prove two real seams that stub-mode tests cannot reach:

AT 17 — no-respawn add (headline regression guard):
    reshape_crew("add_node") adds a reviewer + edge impl→reviewer; the same-id
    implementor (NOT respawned) receives the inform message, calls
    send_to("reviewer") in a real SDK turn, and the reviewer's inbox shows
    message delivery — proving live topology authorization worked end-to-end.

AT 18 — swap routing regression guard:
    reshape_crew("swap") replaces the worker slot; the old teammate is
    tombstoned, the new teammate holds the slot, and send_to("worker") from a
    neighbor resolves to the NEW teammate via the live topology — a real SDK
    turn, not stub-satisfiable structure.

Per project policy (CLAUDE.md "Run live tests before merging"), these tests
are the regression guard for M3.5 reshape-crew and MUST pass before merge:

    CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_reshape.py

Structural gate (no env var): both tests skip cleanly — zero collection errors.
The coordinator runs them live at the validation gate.

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


async def _approve_pending_gate(broker: Broker, *, timeout: float = 30.0) -> str:
    """Poll until a pending proposal appears in the broker, then approve it directly.

    Returns the approved shape_id.  Raises AssertionError on timeout.

    reshape_crew blocks on ``await_proposal`` inside the server while a
    concurrent asyncio Task holds the MCP call.  This helper drives approval
    via ``broker.resolve_proposal`` directly (no second MCP call needed), so
    the reshape Task can proceed.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        pending = [
            p for p in broker.snapshot().shape_proposals
            if p.status == "pending"
        ]
        if pending:
            sid = pending[0].shape_id
            await broker.resolve_proposal(sid, "approve")
            return sid
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"timed out after {timeout}s waiting for reshape_crew to register "
        "a pending gate proposal — did reshape_crew start?"
    )


async def _await_message_in_inbox(
    broker: Broker,
    recipient: str,
    *,
    from_sender: str | None = None,
    timeout: float = 120.0,
) -> Envelope:
    """Poll until recipient's inbox has at least one qualifying message.

    When ``from_sender`` is given, only messages from that sender count.
    Returns the first matching envelope.  Raises AssertionError on timeout.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        msgs = broker.get_messages(recipient=recipient)
        if from_sender is not None:
            msgs = [m for m in msgs if m.sender == from_sender]
        if msgs:
            return msgs[0]
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"timed out after {timeout}s waiting for a message in inbox of "
        f"{recipient!r}"
        + (f" from sender {from_sender!r}" if from_sender else "")
    )


async def _setup_base_crew(
    s: Any,
    broker: Broker,
    shape: dict,
) -> tuple[str, dict[str, str]]:
    """Propose → approve → instantiate a base shape.

    Returns ``(base_shape_id, slot_to_teammate)`` where ``slot_to_teammate``
    maps slot name → teammate_id for the freshly-spawned crew.
    """
    # Propose
    prop = _content_json(await s.call_tool("propose_shape", {"shape": shape}))
    assert prop["ok"], f"propose_shape failed: {prop}"
    shape_id = prop["shape_id"]

    # Approve via resolve_shape (sequential — no concurrency issue here)
    resolve = _content_json(
        await s.call_tool(
            "resolve_shape", {"shape_id": shape_id, "decision": "approve"}
        )
    )
    assert resolve["ok"], f"resolve_shape failed: {resolve}"

    # Instantiate — spawns real SDK subprocesses; each can take 30–90s.
    inst = _content_json(
        await asyncio.wait_for(
            s.call_tool("instantiate_shape", {"shape_id": shape_id}),
            timeout=240.0,
        )
    )
    assert inst["ok"], f"instantiate_shape failed: {inst}"

    slot_to_tm: dict[str, str] = inst["topology"]["slot_to_teammate"]
    return shape_id, slot_to_tm


# ---------------------------------------------------------------------------
# Base shapes
# ---------------------------------------------------------------------------

# AT 17: 1-node base (impl/explorer).  Reshape adds reviewer/general + edge.
# Single node keeps spawn cost minimal; the post-reshape spawn is what we test.
_BASE_SHAPE_17: dict = {
    "name": "live-reshape-add-test",
    "description": "AT17 base: 1-node impl, no edges; reshape adds reviewer",
    "nodes": [{"slot": "impl", "role": "explorer"}],
    "edges": [],
}

# AT 18: 2-node base (sender/explorer → worker/general).  Reshape swaps worker.
# sender→worker edge is preserved after the swap so sender can call send_to.
_BASE_SHAPE_18: dict = {
    "name": "live-reshape-swap-test",
    "description": "AT18 base: sender→worker crew; reshape swaps worker role",
    "nodes": [
        {"slot": "sender", "role": "explorer"},
        {"slot": "worker", "role": "general"},
    ],
    "edges": [{"from_slot": "sender", "to_slot": "worker"}],
}


# ---------------------------------------------------------------------------
# AT 17 — headline no-respawn add: same-id implementor reaches live reviewer
# ---------------------------------------------------------------------------


class TestLiveReshapeNoRespawnAdd:
    """AT 17: reshape_crew add_node — same implementor id, real send_to to reviewer.

    Proves the D0 + D4 contract: a teammate spawned before any out-edge to it
    existed already holds send_to (D0), so a live-added edge needs no respawn
    (D4); the running implementor's accumulated context is preserved.  The
    assertion is a real broker message-log check after a real SDK turn.
    """

    async def test_no_respawn_add(self, monkeypatch: Any) -> None:
        """
        Sequence:
        1. Instantiate a 1-node crew (impl/explorer) via the real SDK factory.
        2. Start reshape_crew("add_node") as an asyncio Task (it blocks on gate).
        3. Approve the gate via broker.resolve_proposal() while the Task waits.
        4. Await reshape result — assert ok:True, impl tid UNCHANGED, impl informed.
        5. Send impl an explicit prompt: call send_to('reviewer', {...}).
        6. Poll reviewer inbox for a message from impl_tid — proves real routing.
        """
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
        real_factory = default_factory()

        # Sanity: bundled roles must be present in the real merged pack.
        known = real_factory.known_roles()
        assert "explorer" in known, f"explorer missing from known_roles: {known}"
        assert "general" in known, f"general missing from known_roles: {known}"

        broker = Broker()
        try:
            async with _client(broker=broker, factory=real_factory) as s:
                await s.initialize()

                # ── Step 1: instantiate 1-node base crew ─────────────────────
                base_shape_id, slot_to_tm = await _setup_base_crew(
                    s, broker, _BASE_SHAPE_17
                )
                impl_tid = slot_to_tm["impl"]
                assert impl_tid, "impl teammate_id must be non-empty"

                # ── Step 2: reshape — add reviewer + edge impl→reviewer ───────
                # reshape_crew blocks inside the server on await_proposal.
                # Run it as a Task and drive gate approval via broker directly
                # (avoids any MCP client half-duplex concurrency constraint).
                reshape_task = asyncio.create_task(
                    s.call_tool(
                        "reshape_crew",
                        {
                            "verb": "add_node",
                            "params": {
                                "node": {"slot": "reviewer", "role": "general"},
                                "edges": [
                                    {"from_slot": "impl", "to_slot": "reviewer"}
                                ],
                            },
                            "base_shape_id": base_shape_id,
                            "gate_timeout": 120.0,
                        },
                    )
                )

                # Yield to let the reshape_crew coroutine start and call
                # register_proposal before we begin polling.
                await asyncio.sleep(0)
                await _approve_pending_gate(broker, timeout=30.0)

                # Await the reshape result; spawning reviewer may take 60–120s.
                reshape_result = _content_json(
                    await asyncio.wait_for(reshape_task, timeout=240.0)
                )
                assert reshape_result["ok"] is True, (
                    f"reshape_crew returned ok:False: {reshape_result}"
                )

                # ── Assert 1: same impl teammate_id (NOT respawned) ───────────
                # The whole point of D0 + D4: existing teammates are never
                # respawned when a new edge is added — their context is preserved.
                new_topo = reshape_result["topology"]["slot_to_teammate"]
                new_impl_tid = new_topo.get("impl")
                assert new_impl_tid == impl_tid, (
                    f"AT17 FAIL — impl was RESPAWNED: "
                    f"before={impl_tid!r} after={new_impl_tid!r}; "
                    "D0/D4 invariant violated"
                )

                # ── Assert 2: impl received the neighbor_added inform message ─
                # reshape_crew sends a crew_reshape/neighbor_added envelope to
                # every already-running source of a new out-edge.
                assert impl_tid in reshape_result["actions"]["informed"], (
                    f"AT17 FAIL — impl NOT in actions.informed; "
                    f"informed={reshape_result['actions']['informed']!r}"
                )

                # ── Assert 3: real SDK turn — impl calls send_to('reviewer') ──
                # reviewer was just spawned; its inbox starts empty.
                reviewer_tid = new_topo["reviewer"]
                assert reviewer_tid, "reviewer teammate_id must be non-empty"
                assert reviewer_tid != impl_tid, (
                    "reviewer and impl must be distinct teammates"
                )

                # Send impl an explicit prompt.  impl must call the send_to
                # MCP tool (crew-send server) with recipient='reviewer'.
                # The broker's live topology (impl→reviewer edge) authorizes
                # the send; _resolve_scoped_recipient maps 'reviewer' →
                # reviewer_tid via the latest recorded Topology.
                await broker.send(
                    Envelope(
                        id=new_message_id(),
                        seq=0,
                        sender=LEAD_ID,
                        recipient=impl_tid,
                        timestamp=0.0,
                        payload=(
                            "Your crew has been reshaped: a reviewer node was added "
                            "and you now have an out-edge to it. "
                            "Use the send_to tool right now to reach it: "
                            "recipient='reviewer', payload={'msg': 'reshapeok'}. "
                            "Call the tool with exactly those arguments."
                        ),
                    )
                )

                # Poll reviewer inbox for a message whose sender is impl_tid.
                # This proves:
                # (a) impl ran a real SDK turn (not a stub echo),
                # (b) send_to('reviewer') was authorized by the live topology,
                # (c) the message was routed to reviewer_tid (not lost or misdirected).
                # A stub would never produce this message.
                msg = await _await_message_in_inbox(
                    broker,
                    recipient=reviewer_tid,
                    from_sender=impl_tid,
                    timeout=120.0,
                )

                assert msg.payload, (
                    f"AT17 FAIL — reviewer received an empty payload from impl; "
                    f"envelope sender={msg.sender!r} payload={msg.payload!r}"
                )

        finally:
            # Cancel any pending tasks before shutting down to avoid
            # 'Task was destroyed but it is pending!' warnings.
            await broker.shutdown_all()


# ---------------------------------------------------------------------------
# AT 18 — swap: slot-name send_to resolves to the replacement (live turn)
# ---------------------------------------------------------------------------


class TestLiveReshapeSwap:
    """AT 18: reshape_crew swap — old teammate dead, send_to('worker') → new mate.

    Proves the D5 invariant: spawn+record_topology BEFORE kill ensures the
    latest Topology's slot→id map points at the live replacement throughout
    the tombstone window.  _resolve_scoped_recipient scans reversed(_topologies),
    so the new mapping wins immediately after record_topology.  This is
    asserted via a real SDK turn where sender calls send_to('worker') and
    the new worker's inbox — not the old dead one — receives the message.
    """

    async def test_swap_routing(self, monkeypatch: Any) -> None:
        """
        Sequence:
        1. Instantiate a 2-node crew (sender/explorer → worker/general).
        2. Start reshape_crew("swap") worker→planner as an asyncio Task.
        3. Approve the gate via broker.resolve_proposal() while the Task waits.
        4. Await reshape result — assert ok:True, old worker dead, new worker id differs.
        5. Send sender an explicit prompt: call send_to('worker', {...}).
        6. Poll NEW worker inbox for a message from sender_tid — proves routing.
        """
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
        real_factory = default_factory()

        known = real_factory.known_roles()
        assert "explorer" in known, f"explorer missing from known_roles: {known}"
        assert "general" in known, f"general missing from known_roles: {known}"
        assert "planner" in known, f"planner missing from known_roles: {known}"

        broker = Broker()
        try:
            async with _client(broker=broker, factory=real_factory) as s:
                await s.initialize()

                # ── Step 1: instantiate 2-node base crew (sender→worker) ──────
                base_shape_id, slot_to_tm = await _setup_base_crew(
                    s, broker, _BASE_SHAPE_18
                )
                sender_tid = slot_to_tm["sender"]
                worker_old_tid = slot_to_tm["worker"]
                assert sender_tid and worker_old_tid, (
                    "sender and worker teammate_ids must be non-empty"
                )
                assert sender_tid != worker_old_tid, (
                    "sender and worker must be distinct teammates"
                )

                # ── Step 2: reshape — swap worker general → planner ───────────
                reshape_task = asyncio.create_task(
                    s.call_tool(
                        "reshape_crew",
                        {
                            "verb": "swap",
                            "params": {"slot": "worker", "role": "planner"},
                            "base_shape_id": base_shape_id,
                            "gate_timeout": 120.0,
                        },
                    )
                )

                await asyncio.sleep(0)
                await _approve_pending_gate(broker, timeout=30.0)

                reshape_result = _content_json(
                    await asyncio.wait_for(reshape_task, timeout=240.0)
                )
                assert reshape_result["ok"] is True, (
                    f"reshape_crew swap returned ok:False: {reshape_result}"
                )

                # ── Assert 1: old worker is dead (tombstoned) ─────────────────
                # D5: kill_teammate(old) fires AFTER record_topology(new) so
                # slot-name routing was never pointing at a dead id.
                crew_by_id = {info.id: info for info in broker.list_crew()}
                old_info = crew_by_id.get(worker_old_tid)
                assert old_info is not None, (
                    f"AT18 FAIL — old worker {worker_old_tid!r} not found "
                    "in broker registry after swap"
                )
                assert not old_info.alive, (
                    f"AT18 FAIL — old worker {worker_old_tid!r} is STILL ALIVE "
                    "after swap; expected tombstoned"
                )

                # ── Assert 2: new worker has a different teammate_id ──────────
                worker_new_tid = reshape_result["topology"]["slot_to_teammate"].get(
                    "worker"
                )
                assert worker_new_tid, "new worker teammate_id must be non-empty"
                assert worker_new_tid != worker_old_tid, (
                    f"AT18 FAIL — swap did NOT change worker teammate_id; "
                    f"old={worker_old_tid!r} new={worker_new_tid!r}"
                )

                # Sanity: new worker must appear in the broker registry as alive.
                new_info = crew_by_id.get(worker_new_tid)
                # Note: list_crew() was captured before the spawn completes in
                # some timing windows, so re-read.
                if new_info is None:
                    crew_by_id = {info.id: info for info in broker.list_crew()}
                    new_info = crew_by_id.get(worker_new_tid)
                assert new_info is not None, (
                    f"AT18 FAIL — new worker {worker_new_tid!r} not found "
                    "in broker registry"
                )
                assert new_info.alive, (
                    f"AT18 FAIL — new worker {worker_new_tid!r} is not alive"
                )

                # ── Assert 3: real SDK turn — send_to('worker') → new teammate ─
                # sender still holds its session; its edge sender→worker is
                # preserved by the swap (slot + edges are kept, only the
                # backing teammate_id changes).
                # When sender calls send_to("worker", ...):
                #   _resolve_scoped_recipient(sender_tid, "worker") scans
                #   reversed(_topologies): new topology (appended last) maps
                #   "worker" → worker_new_tid → correct resolution.
                #   authorize_send(sender_tid, worker_new_tid) finds the edge
                #   in the new topology → passes.
                # A regression (old topology winning) would route to the dead
                # worker, causing UnauthorizedEdgeError or delivery to a dead
                # inbox — the poll would time out and fail the test.
                await broker.send(
                    Envelope(
                        id=new_message_id(),
                        seq=0,
                        sender=LEAD_ID,
                        recipient=sender_tid,
                        timestamp=0.0,
                        payload=(
                            "The worker slot has been swapped to a new teammate. "
                            "Use the send_to tool right now to reach it: "
                            "recipient='worker', payload={'msg': 'swapok'}. "
                            "Call the tool with exactly those arguments."
                        ),
                    )
                )

                # Poll NEW worker inbox for a message from sender_tid.
                # This proves send_to('worker') resolved to worker_new_tid
                # via the live topology — not to the tombstoned old worker.
                # A stub would never produce this; a live SDK turn is required.
                msg = await _await_message_in_inbox(
                    broker,
                    recipient=worker_new_tid,
                    from_sender=sender_tid,
                    timeout=120.0,
                )

                assert msg.payload, (
                    f"AT18 FAIL — new worker received an empty payload from sender; "
                    f"envelope sender={msg.sender!r} payload={msg.payload!r}"
                )

        finally:
            await broker.shutdown_all()
