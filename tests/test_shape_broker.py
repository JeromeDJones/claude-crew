"""Broker proposal state machine and topology recording — ATs 1, 5, 6, 7, 9.

AT1: BrokerSnapshot.topology_slot_to_teammate aggregated from recorded topologies.
AT5: register_proposal → resolve_proposal("approve"|"decline") updates status.
AT6: await_proposal unblocks when resolve_proposal fires; times out otherwise.
AT7: record_topology surfaces in broker.snapshot().topologies with edges + map intact.
AT9: multi-topology last-write-wins merge for topology_slot_to_teammate.

Hardening tests:
- Fix 1: resolve_proposal source-state guard (cannot re-resolve a terminal proposal).
- Fix 3: Topology.slot_to_teammate is a MappingProxyType (immutable).
- Fix 5: mark_instantiated enforces 'approved'-only transition.

asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import asyncio

import pytest

from claude_crew.broker import LEAD_ID, Broker, ShapeProposal, Topology
from claude_crew.shapes import Shape, ShapeEdge, ShapeNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_shape(name: str = "test-shape") -> Shape:
    return Shape(
        name=name,
        description="A test shape for broker tests",
        nodes=(
            ShapeNode(slot="implementor", role="builder"),
            ShapeNode(slot="reviewer", role="sentinel"),
        ),
        edges=(
            ShapeEdge(from_slot="implementor", to_slot="reviewer", mode="gated"),
        ),
    )


@pytest.fixture
def broker():
    return Broker()


# ---------------------------------------------------------------------------
# AT5 — proposal state machine: approve and decline
# ---------------------------------------------------------------------------

async def test_proposal_initial_status_is_pending(broker: Broker) -> None:
    shape_id = broker.register_proposal(_make_shape("pending-shape"))
    proposal = broker.get_proposal(shape_id)
    assert proposal is not None
    assert proposal.status == "pending"
    assert proposal.shape_id == shape_id


async def test_resolve_approve_sets_status(broker: Broker) -> None:
    shape_id = broker.register_proposal(_make_shape("approve-shape"))
    returned = await broker.resolve_proposal(shape_id, "approve")
    assert returned.status == "approved"
    assert broker.get_proposal(shape_id).status == "approved"


async def test_resolve_decline_sets_status(broker: Broker) -> None:
    shape_id = broker.register_proposal(_make_shape("decline-shape"))
    returned = await broker.resolve_proposal(shape_id, "decline")
    assert returned.status == "declined"
    assert broker.get_proposal(shape_id).status == "declined"


async def test_two_proposals_independent(broker: Broker) -> None:
    """AT5: Two proposals resolved with different decisions stay independent."""
    id_a = broker.register_proposal(_make_shape("shape-a"))
    id_b = broker.register_proposal(_make_shape("shape-b"))

    await broker.resolve_proposal(id_a, "approve")
    await broker.resolve_proposal(id_b, "decline")

    assert broker.get_proposal(id_a).status == "approved"
    assert broker.get_proposal(id_b).status == "declined"


async def test_resolve_unknown_shape_raises(broker: Broker) -> None:
    with pytest.raises(KeyError):
        await broker.resolve_proposal("no-such-id", "approve")


async def test_resolve_invalid_decision_raises(broker: Broker) -> None:
    shape_id = broker.register_proposal(_make_shape())
    with pytest.raises(ValueError, match="approve.*decline"):
        await broker.resolve_proposal(shape_id, "reject")


async def test_get_proposal_unknown_returns_none(broker: Broker) -> None:
    assert broker.get_proposal("no-such-id") is None


# ---------------------------------------------------------------------------
# AT6 — await/unblock + timeout
# ---------------------------------------------------------------------------

async def test_await_proposal_unblocks_on_approve(broker: Broker) -> None:
    """AT6 (unblock): await_proposal returns approved when resolver fires."""
    shape_id = broker.register_proposal(_make_shape("unblock-approve"))

    async def _resolver() -> None:
        await asyncio.sleep(0.05)
        await broker.resolve_proposal(shape_id, "approve")

    resolver_task = asyncio.create_task(_resolver())
    proposal = await broker.await_proposal(shape_id, timeout=5.0)
    await resolver_task

    assert proposal.status == "approved"


async def test_await_proposal_unblocks_on_decline(broker: Broker) -> None:
    """AT6 (unblock): await_proposal returns declined when resolver fires."""
    shape_id = broker.register_proposal(_make_shape("unblock-decline"))

    async def _resolver() -> None:
        await asyncio.sleep(0.05)
        await broker.resolve_proposal(shape_id, "decline")

    resolver_task = asyncio.create_task(_resolver())
    proposal = await broker.await_proposal(shape_id, timeout=5.0)
    await resolver_task

    assert proposal.status == "declined"


async def test_await_proposal_timeout_sets_timed_out(broker: Broker) -> None:
    """AT6 (timeout): unresolved proposal times out and returns timed_out."""
    shape_id = broker.register_proposal(_make_shape("timeout-shape"))

    # Short timeout — no resolver task running
    proposal = await broker.await_proposal(shape_id, timeout=0.05)

    assert proposal.status == "timed_out"
    # get_proposal reflects the updated status
    assert broker.get_proposal(shape_id).status == "timed_out"


async def test_await_already_resolved_returns_immediately(broker: Broker) -> None:
    """If a proposal is already resolved before await, return immediately."""
    shape_id = broker.register_proposal(_make_shape("pre-resolved"))
    await broker.resolve_proposal(shape_id, "approve")

    # Should not block at all (large timeout is fine — it returns instantly)
    proposal = await broker.await_proposal(shape_id, timeout=5.0)
    assert proposal.status == "approved"


async def test_await_unknown_shape_raises(broker: Broker) -> None:
    with pytest.raises(KeyError):
        await broker.await_proposal("no-such-id", timeout=0.1)


# ---------------------------------------------------------------------------
# AT7 — topology recorded + queryable
# ---------------------------------------------------------------------------

async def test_record_topology_surfaces_in_snapshot(broker: Broker) -> None:
    """AT7: record_topology → snapshot.topologies contains the topology."""
    topology = Topology(
        shape_name="my-shape",
        edges=(
            ("implementor", "reviewer", "tee"),  # non-gated mode
        ),
        slot_to_teammate={"implementor": "t-abc001", "reviewer": "t-abc002"},
    )
    broker.record_topology(topology)

    snap = broker.snapshot()
    assert len(snap.topologies) == 1
    recorded = snap.topologies[0]
    assert recorded.shape_name == "my-shape"
    # Edges carry recorded mode (non-gated)
    assert recorded.edges[0] == ("implementor", "reviewer", "tee")
    # slot→teammate map intact
    assert recorded.slot_to_teammate == {
        "implementor": "t-abc001",
        "reviewer": "t-abc002",
    }


async def test_get_topologies_returns_all(broker: Broker) -> None:
    t1 = Topology(
        shape_name="shape-one",
        edges=(("a", "b", "direct"),),
        slot_to_teammate={"a": "t-1", "b": "t-2"},
    )
    t2 = Topology(
        shape_name="shape-two",
        edges=(("x", "y", "tee"),),
        slot_to_teammate={"x": "t-3", "y": "t-4"},
    )
    broker.record_topology(t1)
    broker.record_topology(t2)

    topologies = broker.get_topologies()
    assert len(topologies) == 2
    assert topologies[0].shape_name == "shape-one"
    assert topologies[1].shape_name == "shape-two"


async def test_snapshot_topologies_empty_by_default(broker: Broker) -> None:
    snap = broker.snapshot()
    assert snap.topologies == ()


async def test_snapshot_shape_proposals_empty_by_default(broker: Broker) -> None:
    snap = broker.snapshot()
    assert snap.shape_proposals == ()


async def test_snapshot_includes_registered_proposals(broker: Broker) -> None:
    shape_id = broker.register_proposal(_make_shape("snap-shape"))
    snap = broker.snapshot()
    proposal_ids = {p.shape_id for p in snap.shape_proposals}
    assert shape_id in proposal_ids


async def test_multiple_topologies_in_snapshot(broker: Broker) -> None:
    for i in range(3):
        broker.record_topology(
            Topology(
                shape_name=f"shape-{i}",
                edges=(("src", "dst", "gated"),),
                slot_to_teammate={"src": f"t-s{i}", "dst": f"t-d{i}"},
            )
        )
    snap = broker.snapshot()
    assert len(snap.topologies) == 3
    names = [t.shape_name for t in snap.topologies]
    assert names == ["shape-0", "shape-1", "shape-2"]


# ---------------------------------------------------------------------------
# Fix 1 — resolve_proposal source-state guard
# ---------------------------------------------------------------------------


async def test_resolve_approved_proposal_raises(broker: Broker) -> None:
    """Fix 1: resolving an already-approved proposal raises ValueError."""
    shape_id = broker.register_proposal(_make_shape("already-approved"))
    await broker.resolve_proposal(shape_id, "approve")
    assert broker.get_proposal(shape_id).status == "approved"

    # Second resolve (approve or decline) must raise, not silently flip.
    with pytest.raises(ValueError, match="cannot resolve proposal"):
        await broker.resolve_proposal(shape_id, "approve")


async def test_resolve_declined_proposal_raises(broker: Broker) -> None:
    """Fix 1: resolving an already-declined proposal raises ValueError."""
    shape_id = broker.register_proposal(_make_shape("already-declined"))
    await broker.resolve_proposal(shape_id, "decline")

    with pytest.raises(ValueError, match="cannot resolve proposal"):
        await broker.resolve_proposal(shape_id, "decline")


async def test_resolve_timed_out_proposal_raises(broker: Broker) -> None:
    """Fix 1: resolving a timed_out proposal raises ValueError."""
    shape_id = broker.register_proposal(_make_shape("timed-out-shape"))
    # Force a very short timeout so the proposal transitions to timed_out.
    proposal = await broker.await_proposal(shape_id, timeout=0.05)
    assert proposal.status == "timed_out"

    with pytest.raises(ValueError, match="cannot resolve proposal"):
        await broker.resolve_proposal(shape_id, "approve")


async def test_resolve_state_error_message_includes_state(broker: Broker) -> None:
    """Fix 1: the ValueError message includes the current status."""
    shape_id = broker.register_proposal(_make_shape("state-msg-shape"))
    await broker.resolve_proposal(shape_id, "approve")

    with pytest.raises(ValueError, match="approved"):
        await broker.resolve_proposal(shape_id, "decline")


# ---------------------------------------------------------------------------
# Fix 3 — Topology.slot_to_teammate is a MappingProxyType (immutable)
# ---------------------------------------------------------------------------


async def test_topology_slot_to_teammate_is_immutable(broker: Broker) -> None:
    """Fix 3: in-place write to slot_to_teammate raises TypeError."""
    topology = Topology(
        shape_name="immutable-shape",
        edges=(("a", "b", "gated"),),
        slot_to_teammate={"a": "t-001", "b": "t-002"},
    )
    with pytest.raises(TypeError):
        topology.slot_to_teammate["x"] = "t-999"  # type: ignore[index]


async def test_topology_slot_to_teammate_reads_work(broker: Broker) -> None:
    """Fix 3: reads on the proxy still work correctly."""
    topology = Topology(
        shape_name="readable-shape",
        edges=(),
        slot_to_teammate={"impl": "t-abc", "rev": "t-def"},
    )
    assert topology.slot_to_teammate["impl"] == "t-abc"
    assert topology.slot_to_teammate["rev"] == "t-def"
    assert set(topology.slot_to_teammate.keys()) == {"impl", "rev"}


async def test_topology_snapshot_round_trips_slot_map(broker: Broker) -> None:
    """Fix 3: slot_to_teammate survives record_topology → snapshot() unchanged."""
    original_map = {"p": "t-111", "q": "t-222"}
    topology = Topology(
        shape_name="roundtrip-shape",
        edges=(("p", "q", "tee"),),
        slot_to_teammate=original_map,
    )
    broker.record_topology(topology)

    snap = broker.snapshot()
    assert len(snap.topologies) == 1
    recorded = snap.topologies[0]
    # Content must match; type is MappingProxyType but compares equal to dict.
    assert recorded.slot_to_teammate == original_map
    # Mutation must still be refused on the snapshotted topology.
    with pytest.raises(TypeError):
        recorded.slot_to_teammate["z"] = "t-999"  # type: ignore[index]


async def test_topology_caller_dict_mutation_does_not_affect_proxy(broker: Broker) -> None:
    """Fix 3: mutating the caller's original dict after construction has no effect."""
    caller_dict: dict[str, str] = {"slot1": "t-aaa"}
    topology = Topology(
        shape_name="isolated-shape",
        edges=(),
        slot_to_teammate=caller_dict,
    )
    caller_dict["slot2"] = "t-bbb"  # mutate original after construction
    assert "slot2" not in topology.slot_to_teammate  # proxy owns its own copy


# ---------------------------------------------------------------------------
# Fix 5 — mark_instantiated enforces 'approved'-only transition
# ---------------------------------------------------------------------------


async def test_mark_instantiated_on_pending_raises(broker: Broker) -> None:
    """Fix 5: mark_instantiated on a pending proposal raises ValueError."""
    shape_id = broker.register_proposal(_make_shape("pending-for-instantiate"))
    assert broker.get_proposal(shape_id).status == "pending"

    with pytest.raises(ValueError, match="cannot instantiate proposal"):
        broker.mark_instantiated(shape_id)


async def test_mark_instantiated_on_declined_raises(broker: Broker) -> None:
    """Fix 5: mark_instantiated on a declined proposal raises ValueError."""
    shape_id = broker.register_proposal(_make_shape("declined-for-instantiate"))
    await broker.resolve_proposal(shape_id, "decline")

    with pytest.raises(ValueError, match="cannot instantiate proposal"):
        broker.mark_instantiated(shape_id)


async def test_mark_instantiated_on_unknown_raises(broker: Broker) -> None:
    """Fix 5: mark_instantiated on an unknown shape_id raises KeyError."""
    with pytest.raises(KeyError):
        broker.mark_instantiated("no-such-id")


async def test_mark_instantiated_on_approved_succeeds(broker: Broker) -> None:
    """Fix 5 happy path: approved → instantiated via mark_instantiated."""
    shape_id = broker.register_proposal(_make_shape("approved-for-instantiate"))
    await broker.resolve_proposal(shape_id, "approve")

    broker.mark_instantiated(shape_id)
    assert broker.get_proposal(shape_id).status == "instantiated"


async def test_mark_instantiated_on_already_instantiated_raises(broker: Broker) -> None:
    """Fix 5: mark_instantiated cannot be called twice on the same proposal."""
    shape_id = broker.register_proposal(_make_shape("double-instantiate"))
    await broker.resolve_proposal(shape_id, "approve")
    broker.mark_instantiated(shape_id)

    with pytest.raises(ValueError, match="cannot instantiate proposal"):
        broker.mark_instantiated(shape_id)


# ---------------------------------------------------------------------------
# AT3 — Notify-on-resolve: shape_resolved envelope in lead inbox
# ---------------------------------------------------------------------------


async def test_notify_on_resolve_approve_sends_lead_message(broker: Broker) -> None:
    """AT3 (happy path, approve): resolve_proposal sends shape_resolved to lead."""
    shape_id = broker.register_proposal(_make_shape("notify-approve"))
    await broker.resolve_proposal(shape_id, "approve")

    messages = broker.get_messages(LEAD_ID, since_seq=0)
    shape_resolved = [
        m for m in messages
        if isinstance(m.payload, dict)
        and m.payload.get("type") == "shape_resolved"
        and m.payload.get("shape_id") == shape_id
    ]
    assert len(shape_resolved) == 1
    assert shape_resolved[0].payload["status"] == "approved"


async def test_notify_on_resolve_decline_sends_lead_message(broker: Broker) -> None:
    """AT3 (happy path, decline): resolve_proposal sends shape_resolved to lead."""
    shape_id = broker.register_proposal(_make_shape("notify-decline"))
    await broker.resolve_proposal(shape_id, "decline")

    messages = broker.get_messages(LEAD_ID, since_seq=0)
    shape_resolved = [
        m for m in messages
        if isinstance(m.payload, dict)
        and m.payload.get("type") == "shape_resolved"
        and m.payload.get("shape_id") == shape_id
    ]
    assert len(shape_resolved) == 1
    assert shape_resolved[0].payload["status"] == "declined"


async def test_no_notify_before_resolve(broker: Broker) -> None:
    """AT3 (sad path): no shape_resolved message in lead inbox before resolution."""
    shape_id = broker.register_proposal(_make_shape("pending-no-notify"))

    # Proposal is pending — no shape_resolved envelope should exist yet.
    messages = broker.get_messages(LEAD_ID, since_seq=0)
    shape_resolved = [
        m for m in messages
        if isinstance(m.payload, dict)
        and m.payload.get("type") == "shape_resolved"
        and m.payload.get("shape_id") == shape_id
    ]
    assert len(shape_resolved) == 0


# ---------------------------------------------------------------------------
# AT1 — BrokerSnapshot.topology_slot_to_teammate (AC-6 broker field)
# ---------------------------------------------------------------------------


async def test_snapshot_slot_to_teammate_single_topology(broker: Broker) -> None:
    """AT1 (AC-6 broker field): single topology — snapshot carries its slot map."""
    topology = Topology(
        shape_name="at1-shape",
        edges=(("planner", "impl", "direct"),),
        slot_to_teammate={"planner": "tid-1", "impl": "tid-2"},
    )
    broker.record_topology(topology)

    snap = broker.snapshot()
    assert snap.topology_slot_to_teammate == {"planner": "tid-1", "impl": "tid-2"}


async def test_snapshot_slot_to_teammate_empty_when_no_topologies(broker: Broker) -> None:
    """AT1 (AC-6 broker field, zero case): no topologies → empty map."""
    snap = broker.snapshot()
    assert snap.topology_slot_to_teammate == {}


# ---------------------------------------------------------------------------
# AT9 — multi-topology last-write-wins merge (AC-6 multi-topology)
# ---------------------------------------------------------------------------


async def test_snapshot_slot_to_teammate_last_write_wins(broker: Broker) -> None:
    """AT9 (AC-6 multi-topology): second topology's slot value wins on collision."""
    t1 = Topology(
        shape_name="shape-first",
        edges=(("impl", "rev", "direct"),),
        slot_to_teammate={"impl": "tid-old"},
    )
    t2 = Topology(
        shape_name="shape-second",
        edges=(("impl", "rev", "tee"),),
        slot_to_teammate={"impl": "tid-new"},
    )
    broker.record_topology(t1)
    broker.record_topology(t2)

    snap = broker.snapshot()
    assert snap.topology_slot_to_teammate["impl"] == "tid-new"


async def test_snapshot_slot_to_teammate_non_collision_slots_merged(
    broker: Broker,
) -> None:
    """AT9 (multi-topology): non-colliding slots from both topologies appear."""
    t1 = Topology(
        shape_name="shape-a",
        edges=(),
        slot_to_teammate={"planner": "tid-p"},
    )
    t2 = Topology(
        shape_name="shape-b",
        edges=(),
        slot_to_teammate={"implementor": "tid-i"},
    )
    broker.record_topology(t1)
    broker.record_topology(t2)

    snap = broker.snapshot()
    assert snap.topology_slot_to_teammate == {"planner": "tid-p", "implementor": "tid-i"}


async def test_second_resolve_produces_no_extra_notify(broker: Broker) -> None:
    """AT3: guard fires before notify so double-resolve raises, not second message."""
    shape_id = broker.register_proposal(_make_shape("no-double-notify"))
    await broker.resolve_proposal(shape_id, "approve")

    # Attempt a second resolve — must raise, must not emit a second notification.
    with pytest.raises(ValueError, match="cannot resolve proposal"):
        await broker.resolve_proposal(shape_id, "approve")

    messages = broker.get_messages(LEAD_ID, since_seq=0)
    shape_resolved = [
        m for m in messages
        if isinstance(m.payload, dict)
        and m.payload.get("type") == "shape_resolved"
        and m.payload.get("shape_id") == shape_id
    ]
    # Exactly one message — from the first (successful) resolution only.
    assert len(shape_resolved) == 1
