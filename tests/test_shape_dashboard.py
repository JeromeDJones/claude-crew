"""Dashboard shape-gate route tests — ATs 11, 12.

AT11: (dashboard, single-instance approval + DAG state)
  - GET /api/state includes pending proposals carrying crew_id, status, and
    a non-empty mermaid source string.
  - POST /shape-approval/{own_crew_id}/{shape_id} with {"decision":"approve"}
    returns ok, broker proposal transitions to approved.
  - Validation: bad path params → 400; invalid decision → 400; unknown
    shape_id → 404.

AT12: (dashboard, multi-instance proxy)
  - Leader proxies POST /shape-approval/{follower_crew_id}/{shape_id} to the
    follower's own endpoint; follower's broker resolves to approved.
  - crew_id absent from registry → 404.

asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import asyncio
import json
import socket

import httpx
import pytest

from claude_crew.broker import Broker
from claude_crew.instance_registry import InstanceRegistry
from claude_crew.shapes import Shape, ShapeEdge, ShapeNode, shape_to_mermaid
from claude_crew.ui_server import UIServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shape(name: str = "test-shape") -> Shape:
    return Shape(
        name=name,
        description="A test shape for dashboard tests",
        nodes=(
            ShapeNode(slot="implementor", role="builder"),
            ShapeNode(slot="reviewer", role="sentinel"),
        ),
        edges=(
            ShapeEdge(from_slot="implementor", to_slot="reviewer", mode="gated"),
        ),
    )


def _make_ui(
    broker: Broker | None = None,
    registry: InstanceRegistry | None = None,
) -> tuple[Broker, UIServer]:
    b = broker or Broker()
    ui = UIServer(b, port=0, registry=registry)
    return b, ui


def _client(ui: UIServer) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ui._make_app()),
        base_url="http://testserver",
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# AT11 — single-instance: /api/state shape_proposals field
# ---------------------------------------------------------------------------


class TestStateShapeProposals:
    """GET /api/state includes pending proposals with required fields."""

    async def test_state_has_shape_proposals_key(self) -> None:
        broker = Broker()
        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        assert resp.status_code == 200
        state = resp.json()
        instance = state["instances"][0]
        assert "shape_proposals" in instance

    async def test_no_proposals_returns_empty_list(self) -> None:
        broker = Broker()
        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        state = resp.json()
        instance = state["instances"][0]
        assert instance["shape_proposals"] == []

    async def test_pending_proposal_appears_in_state(self) -> None:
        broker = Broker()
        shape = _make_shape("gate-shape")
        shape_id = broker.register_proposal(shape)

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        state = resp.json()
        instance = state["instances"][0]
        proposals = instance["shape_proposals"]

        assert len(proposals) == 1
        p = proposals[0]
        assert p["shape_id"] == shape_id
        assert p["status"] == "pending"
        assert p["crew_id"] == broker.crew_id

    async def test_proposal_carries_mermaid_source(self) -> None:
        broker = Broker()
        shape = _make_shape("mermaid-shape")
        broker.register_proposal(shape)

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        state = resp.json()
        p = state["instances"][0]["shape_proposals"][0]

        mermaid_src = p["mermaid"]
        assert mermaid_src  # non-empty
        assert "graph TD" in mermaid_src
        # Each slot label appears in the mermaid source
        assert "implementor" in mermaid_src
        assert "reviewer" in mermaid_src
        # Edge mode appears
        assert "gated" in mermaid_src

    async def test_proposal_mermaid_matches_shape_to_mermaid(self) -> None:
        broker = Broker()
        shape = _make_shape("mermaid-match-shape")
        broker.register_proposal(shape)

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        state = resp.json()
        p = state["instances"][0]["shape_proposals"][0]

        assert p["mermaid"] == shape_to_mermaid(shape)

    async def test_proposal_carries_name_and_summary(self) -> None:
        broker = Broker()
        shape = _make_shape("named-shape")
        broker.register_proposal(shape)

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        state = resp.json()
        p = state["instances"][0]["shape_proposals"][0]

        assert p["name"] == "named-shape"
        assert p["summary"] == "A test shape for dashboard tests"

    async def test_proposal_adaptation_diff_none_when_omitted(self) -> None:
        broker = Broker()
        shape = _make_shape("nodiff-shape")
        broker.register_proposal(shape)  # no adaptation_diff

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        state = resp.json()
        p = state["instances"][0]["shape_proposals"][0]
        assert p["adaptation_diff"] is None

    async def test_proposal_adaptation_diff_surfaced_when_set(self) -> None:
        broker = Broker()
        shape = _make_shape("diff-shape")
        broker.register_proposal(shape, adaptation_diff="added reviewer node")

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        state = resp.json()
        p = state["instances"][0]["shape_proposals"][0]
        assert p["adaptation_diff"] == "added reviewer node"

    async def test_multiple_proposals_all_appear(self) -> None:
        broker = Broker()
        id_a = broker.register_proposal(_make_shape("shape-a"))
        id_b = broker.register_proposal(_make_shape("shape-b"))

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        state = resp.json()
        proposals = state["instances"][0]["shape_proposals"]

        ids = {p["shape_id"] for p in proposals}
        assert id_a in ids
        assert id_b in ids

    async def test_approved_proposal_still_appears(self) -> None:
        """Proposals remain in state after approval (status changes)."""
        broker = Broker()
        shape_id = broker.register_proposal(_make_shape("approved-shape"))
        await broker.resolve_proposal(shape_id, "approve")

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        state = resp.json()
        proposals = state["instances"][0]["shape_proposals"]

        match = next((p for p in proposals if p["shape_id"] == shape_id), None)
        assert match is not None
        assert match["status"] == "approved"


# ---------------------------------------------------------------------------
# AT11 — single-instance: POST /shape-approval/{crew_id}/{shape_id}
# ---------------------------------------------------------------------------


class TestShapeApprovalSingleInstance:
    """POST /shape-approval/{crew_id}/{shape_id} resolves a local proposal."""

    async def test_approve_decision_returns_ok(self) -> None:
        broker = Broker()
        shape_id = broker.register_proposal(_make_shape("approve-me"))
        _, ui = _make_ui(broker)

        async with _client(ui) as client:
            resp = await client.post(
                f"/shape-approval/{broker.crew_id}/{shape_id}",
                json={"decision": "approve"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["shape_id"] == shape_id
        assert body["status"] == "approved"

    async def test_decline_decision_returns_ok(self) -> None:
        broker = Broker()
        shape_id = broker.register_proposal(_make_shape("decline-me"))
        _, ui = _make_ui(broker)

        async with _client(ui) as client:
            resp = await client.post(
                f"/shape-approval/{broker.crew_id}/{shape_id}",
                json={"decision": "decline"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["status"] == "declined"

    async def test_approve_mutates_broker_proposal(self) -> None:
        """Broker proposal transitions to 'approved' after POST."""
        broker = Broker()
        shape_id = broker.register_proposal(_make_shape("mutate-check"))
        _, ui = _make_ui(broker)

        async with _client(ui) as client:
            await client.post(
                f"/shape-approval/{broker.crew_id}/{shape_id}",
                json={"decision": "approve"},
            )
        assert broker.get_proposal(shape_id).status == "approved"

    async def test_approve_unblocks_await_proposal(self) -> None:
        """await_proposal should unblock after the POST resolves the proposal."""
        broker = Broker()
        shape_id = broker.register_proposal(_make_shape("unblock-check"))
        _, ui = _make_ui(broker)

        # Start await_proposal in background
        waiter = asyncio.create_task(broker.await_proposal(shape_id, timeout=5.0))
        await asyncio.sleep(0)  # yield so waiter parks on the condition

        async with _client(ui) as client:
            await client.post(
                f"/shape-approval/{broker.crew_id}/{shape_id}",
                json={"decision": "approve"},
            )

        proposal = await asyncio.wait_for(waiter, timeout=3.0)
        assert proposal.status == "approved"

    async def test_bad_path_param_crew_id_returns_400(self) -> None:
        broker = Broker()
        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.post(
                "/shape-approval/bad crew id!/someshape",
                json={"decision": "approve"},
            )
        assert resp.status_code == 400
        assert "invalid_param" in resp.json()["error"]

    async def test_bad_path_param_shape_id_returns_400(self) -> None:
        broker = Broker()
        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.post(
                f"/shape-approval/{broker.crew_id}/bad shape id!",
                json={"decision": "approve"},
            )
        assert resp.status_code == 400
        assert "invalid_param" in resp.json()["error"]

    async def test_invalid_decision_returns_400(self) -> None:
        broker = Broker()
        shape_id = broker.register_proposal(_make_shape("invalid-dec"))
        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.post(
                f"/shape-approval/{broker.crew_id}/{shape_id}",
                json={"decision": "edited"},  # not allowed in M0
            )
        assert resp.status_code == 400
        assert "invalid_decision" in resp.json()["error"]

    async def test_missing_decision_returns_400(self) -> None:
        broker = Broker()
        shape_id = broker.register_proposal(_make_shape("no-dec"))
        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.post(
                f"/shape-approval/{broker.crew_id}/{shape_id}",
                json={},  # no decision key
            )
        assert resp.status_code == 400

    async def test_unknown_shape_id_returns_404(self) -> None:
        broker = Broker()
        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.post(
                f"/shape-approval/{broker.crew_id}/unknownshapex",
                json={"decision": "approve"},
            )
        assert resp.status_code == 404

    async def test_malformed_json_body_returns_400(self) -> None:
        broker = Broker()
        shape_id = broker.register_proposal(_make_shape("malformed"))
        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.post(
                f"/shape-approval/{broker.crew_id}/{shape_id}",
                content=b"not json{{",
                headers={"content-type": "application/json"},
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# AT12 — multi-instance proxy: leader → follower
# ---------------------------------------------------------------------------


class TestShapeApprovalMultiInstance:
    """Leader proxies POST /shape-approval to the owning follower.

    Multi-instance trap from CLAUDE.md: any new per-instance endpoint must
    carry crew_id and proxy to the owning follower — validated by a real
    multi-instance test, not just a single-instance one.
    """

    async def test_leader_proxies_approval_to_follower(
        self, tmp_path, monkeypatch
    ) -> None:
        """POST to leader for a follower's crew_id proxies; follower resolves."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker_a = Broker()  # leader
        broker_b = Broker()  # follower with the proposal

        shape = _make_shape("proxy-shape")
        shape_id = broker_b.register_proposal(shape)

        port_b = _free_port()
        reg_b = InstanceRegistry(crew_id=broker_b.crew_id, port=port_b)
        ui_b = UIServer(broker_b, port=port_b, registry=reg_b)

        # Start follower on a real TCP port
        task_b = asyncio.create_task(ui_b.serve())
        await asyncio.sleep(0.5)
        reg_b.register()

        reg_a = InstanceRegistry(crew_id=broker_a.crew_id, port=0)
        _, ui_a = _make_ui(broker_a, registry=reg_a)

        # POST to leader using ASGI transport; leader proxies to real follower
        async with _client(ui_a) as leader_client:
            resp = await leader_client.post(
                f"/shape-approval/{broker_b.crew_id}/{shape_id}",
                json={"decision": "approve"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["status"] == "approved"

        # Follower's broker should reflect the approval
        assert broker_b.get_proposal(shape_id).status == "approved"

        task_b.cancel()
        try:
            await task_b
        except asyncio.CancelledError:
            pass

    async def test_leader_proxies_decline_to_follower(
        self, tmp_path, monkeypatch
    ) -> None:
        """Decline decision is also proxied correctly."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker_a = Broker()
        broker_b = Broker()

        shape_id = broker_b.register_proposal(_make_shape("proxy-decline"))

        port_b = _free_port()
        reg_b = InstanceRegistry(crew_id=broker_b.crew_id, port=port_b)
        ui_b = UIServer(broker_b, port=port_b, registry=reg_b)

        task_b = asyncio.create_task(ui_b.serve())
        await asyncio.sleep(0.5)
        reg_b.register()

        reg_a = InstanceRegistry(crew_id=broker_a.crew_id, port=0)
        _, ui_a = _make_ui(broker_a, registry=reg_a)

        async with _client(ui_a) as leader_client:
            resp = await leader_client.post(
                f"/shape-approval/{broker_b.crew_id}/{shape_id}",
                json={"decision": "decline"},
            )

        assert resp.status_code == 200
        assert broker_b.get_proposal(shape_id).status == "declined"

        task_b.cancel()
        try:
            await task_b
        except asyncio.CancelledError:
            pass

    async def test_unknown_crew_id_returns_404(
        self, tmp_path, monkeypatch
    ) -> None:
        """crew_id absent from registry → 404 (AT#12 explicit requirement)."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker_a = Broker()
        reg_a = InstanceRegistry(crew_id=broker_a.crew_id, port=0)
        _, ui_a = _make_ui(broker_a, registry=reg_a)

        async with _client(ui_a) as leader_client:
            resp = await leader_client.post(
                "/shape-approval/unknowncrew123/fakeshapeid",
                json={"decision": "approve"},
            )

        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    async def test_no_registry_returns_404_for_remote_crew(self) -> None:
        """With registry=None, any non-local crew_id → 404."""
        broker_a = Broker()
        # No registry
        _, ui_a = _make_ui(broker_a, registry=None)

        async with _client(ui_a) as leader_client:
            resp = await leader_client.post(
                "/shape-approval/remotecrew123/someshapeid",
                json={"decision": "approve"},
            )

        assert resp.status_code == 404

    async def test_local_crew_served_directly_not_proxied(
        self, tmp_path, monkeypatch
    ) -> None:
        """crew_id == own broker → served locally (no proxy needed)."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker = Broker()
        shape_id = broker.register_proposal(_make_shape("local-direct"))

        port = _free_port()
        reg = InstanceRegistry(crew_id=broker.crew_id, port=port)
        _, ui = _make_ui(broker, registry=reg)

        async with _client(ui) as client:
            resp = await client.post(
                f"/shape-approval/{broker.crew_id}/{shape_id}",
                json={"decision": "approve"},
            )

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert broker.get_proposal(shape_id).status == "approved"
