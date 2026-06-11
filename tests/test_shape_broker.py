"""Broker proposal state machine and topology recording — ATs 5, 6, 7.

AT5: register_proposal → resolve_proposal("approve"|"decline") updates status.
AT6: await_proposal unblocks when resolve_proposal fires; times out otherwise.
AT7: record_topology surfaces in broker.snapshot().topologies with edges + map intact.

asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import asyncio

import pytest

from claude_crew.broker import Broker, ShapeProposal, Topology
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
