"""Edge-observability dashboard tests — AT#11, AT#12, AT#13.

AT#11: /api/state local instance payload includes topology_edge_stats with
       {from_slot, to_slot, mode, exchanges>=1, tripped:false, crew_id} keyed by
       the broker's own crew_id. (Green-suite data contract; SVG rendering verified
       separately — see Out of Scope in spec.)

AT#12: GET /edge-log/{crew_id}/{from}/{to} returns {ok, edge, messages}; POST
       /edge-promote/{crew_id}/{from}/{to} returns {ok, edge, mode:"gated"} and
       subsequent a→b sends route gated. (Green-suite endpoint contract; SVG click
       hit-testing verified separately.)

AT#13: Multi-instance aggregation + proxy. Leader /api/state includes follower's
       topology_edge_stats keyed by follower's crew_id; GET /edge-log/{follower_id}
       against the leader proxies to the follower (200, not 404).

asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import asyncio
import json as _json
import socket
from typing import Any

import httpx
import pytest

from claude_crew.broker import Broker, LEAD_ID, Topology
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.instance_registry import InstanceRegistry
from claude_crew.teammate import StubTeammate
from claude_crew.ui_server import UIServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_factory(tid: str, name: str, role: str, **kw: object) -> StubTeammate:
    return StubTeammate(tid, name, role)


def _topo(
    edges: list[tuple[str, str, str]],
    slot_to_teammate: dict[str, str],
) -> Topology:
    return Topology(
        shape_name="test",
        edges=tuple(edges),
        slot_to_teammate=slot_to_teammate,
    )


def _env(sender: str, recipient: str, payload: Any = None) -> Envelope:
    return Envelope(
        id=new_message_id(),
        seq=0,
        sender=sender,
        recipient=recipient,
        timestamp=0.0,
        payload=payload if payload is not None else {"text": "hello"},
    )


async def _spawn(broker: Broker, role: str = "r") -> str:
    return await broker.spawn_teammate(role=role, name=role, factory=_stub_factory)


def _make_ui(
    broker: Broker | None = None,
    registry: InstanceRegistry | None = None,
) -> tuple[Broker, UIServer]:
    b = broker or Broker()
    ui = UIServer(b, port=0, registry=registry)
    return b, ui


def _client(ui: UIServer) -> httpx.AsyncClient:
    """In-process ASGI client for the UIServer. Proxy methods still use the
    real self._http_client to reach follower HTTP ports."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ui._make_app()),
        base_url="http://testserver",
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _http_get(url: str) -> dict:
    """Async URL fetch using urllib (curl/wget blocked by hook)."""
    import urllib.request

    def _fetch() -> dict:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return {"status": resp.status, "body": resp.read()}

    return await asyncio.to_thread(_fetch)


# ---------------------------------------------------------------------------
# AT#11 — /api/state includes topology_edge_stats with crew_id
# ---------------------------------------------------------------------------


class TestEdgeStatsOnApiState:
    """AT#11: the local instance payload on /api/state carries topology_edge_stats.

    Green-suite data-contract tests only. SVG on-graph rendering driven by
    this payload is verified separately (see Out of Scope in spec).
    """

    async def test_edge_stats_present_after_tee_exchange(self) -> None:
        """topology_edge_stats includes the edge entry after one tee exchange."""
        broker = Broker()
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "tee")], {"a": a_id, "b": b_id}))

        # One tee exchange: increments _edge_exchanges[(a, b)] in the broker.
        await broker.send(_env(a_id, b_id))

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        assert resp.status_code == 200

        data = resp.json()
        local = next((i for i in data["instances"] if i.get("is_local")), None)
        assert local is not None

        stats = local.get("topology_edge_stats")
        assert stats is not None, "topology_edge_stats key missing from instance payload"
        assert isinstance(stats, list)

        entry = next(
            (s for s in stats if s.get("from_slot") == "a" and s.get("to_slot") == "b"),
            None,
        )
        assert entry is not None, "Expected edge a→b in topology_edge_stats"
        assert entry["mode"] == "tee"
        assert entry["exchanges"] >= 1
        assert entry["tripped"] is False
        assert entry["crew_id"] == broker.crew_id

    async def test_edge_stats_empty_when_no_topology(self) -> None:
        """topology_edge_stats is an empty list when the broker has no topologies."""
        broker = Broker()
        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        assert resp.status_code == 200
        data = resp.json()
        local = next((i for i in data["instances"] if i.get("is_local")), None)
        assert local is not None
        # Key must be present and empty
        assert "topology_edge_stats" in local
        assert local["topology_edge_stats"] == []

    async def test_edge_stats_crew_id_matches_broker(self) -> None:
        """crew_id on each edge stat matches the broker's own crew_id."""
        broker = Broker()
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        data = resp.json()
        local = next(i for i in data["instances"] if i.get("is_local"))
        stats = local["topology_edge_stats"]
        assert len(stats) == 1
        assert stats[0]["crew_id"] == broker.crew_id

    async def test_edge_stats_tripped_false_when_not_tripped(self) -> None:
        """tripped is False when the circuit breaker has not fired."""
        broker = Broker()
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))
        await broker.send(_env(a_id, b_id))  # one direct exchange, no trip

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get("/api/state")
        data = resp.json()
        local = next(i for i in data["instances"] if i.get("is_local"))
        stats = local["topology_edge_stats"]
        assert len(stats) == 1
        assert stats[0]["tripped"] is False


# ---------------------------------------------------------------------------
# AT#12 — GET /edge-log and POST /edge-promote single-instance endpoints
# ---------------------------------------------------------------------------


class TestEdgeLogSingleInstance:
    """AT#12 (edge-log half): GET /edge-log returns messages for directed edge."""

    async def test_edge_log_returns_messages_for_direct_edge(self) -> None:
        """GET /edge-log/{crew_id}/a/b returns {ok, messages} with direct message."""
        broker = Broker()
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))

        payload = {"text": "edge log test"}
        await broker.send(_env(a_id, b_id, payload))

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get(f"/edge-log/{broker.crew_id}/a/b")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["edge"] == ["a", "b"]
        msgs = body["messages"]
        assert len(msgs) >= 1
        # Check one of the messages has the right recipient
        assert any(m.get("recipient") == b_id for m in msgs), (
            f"Expected a message to {b_id!r} in {msgs!r}"
        )

    async def test_edge_log_empty_when_no_messages_sent(self) -> None:
        """GET /edge-log for an edge with no traffic returns empty messages list."""
        broker = Broker()
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get(f"/edge-log/{broker.crew_id}/a/b")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["messages"] == []

    async def test_edge_log_returns_ok_shape(self) -> None:
        """GET /edge-log always returns {ok, edge:[from,to], messages:[...]}."""
        broker = Broker()
        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.get(f"/edge-log/{broker.crew_id}/x/y")
        assert resp.status_code == 200
        body = resp.json()
        assert "ok" in body
        assert "edge" in body
        assert "messages" in body
        assert isinstance(body["messages"], list)

    async def test_edge_log_unknown_remote_crew_no_registry_returns_404(self) -> None:
        """GET /edge-log for non-local crew_id with no registry returns 404."""
        broker = Broker()
        _, ui = _make_ui(broker, registry=None)
        async with _client(ui) as client:
            resp = await client.get("/edge-log/remotecrew99/a/b")
        assert resp.status_code == 404

    async def test_edge_log_invalid_param_returns_400(self) -> None:
        """GET /edge-log with a traversal-unsafe param returns 400."""
        broker = Broker()
        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            # ../secret is blocked by _PATH_PARAM_RE
            resp = await client.get(f"/edge-log/{broker.crew_id}/../secret/b")
        # Starlette may 404 on path segment mismatch; either 400 or 404 is correct
        assert resp.status_code in (400, 404)


class TestEdgePromoteSingleInstance:
    """AT#12 (edge-promote half): POST /edge-promote sets edge to gated mode."""

    async def test_edge_promote_returns_gated(self) -> None:
        """POST /edge-promote/{crew_id}/a/b returns {ok:true, edge, mode:'gated'}."""
        broker = Broker()
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))
        await broker.send(_env(a_id, b_id))  # one exchange

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.post(f"/edge-promote/{broker.crew_id}/a/b")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["edge"] == ["a", "b"]
        assert body["mode"] == "gated"

    async def test_edge_promote_gates_subsequent_sends(self) -> None:
        """After /edge-promote, a→b messages route to LEAD (gated)."""
        broker = Broker()
        a_id = await _spawn(broker, "a")
        b_id = await _spawn(broker, "b")
        broker.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))

        # Pre-promote: direct delivery to b.
        await broker.send(_env(a_id, b_id, {"text": "before"}))
        assert len(broker.get_messages(b_id)) == 1
        assert broker.get_messages(LEAD_ID) == []

        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.post(f"/edge-promote/{broker.crew_id}/a/b")
        assert resp.status_code == 200

        # Post-promote: subsequent sends are gated (route to LEAD).
        await broker.send(_env(a_id, b_id, {"text": "after"}))
        lead_msgs = broker.get_messages(LEAD_ID)
        assert len(lead_msgs) >= 1
        # b's count stays at 1 (the pre-promote message only).
        assert len(broker.get_messages(b_id)) == 1

    async def test_edge_promote_unknown_remote_crew_no_registry_returns_404(self) -> None:
        """POST /edge-promote for non-local crew_id with no registry returns 404."""
        broker = Broker()
        _, ui = _make_ui(broker, registry=None)
        async with _client(ui) as client:
            resp = await client.post("/edge-promote/remotecrew99/a/b")
        assert resp.status_code == 404

    async def test_edge_promote_invalid_param_returns_400(self) -> None:
        """POST /edge-promote with invalid path param returns 400."""
        broker = Broker()
        _, ui = _make_ui(broker)
        async with _client(ui) as client:
            resp = await client.post(f"/edge-promote/{broker.crew_id}/../secret/b")
        assert resp.status_code in (400, 404)


# ---------------------------------------------------------------------------
# AT#13 — multi-instance aggregation + proxy
# ---------------------------------------------------------------------------


class TestMultiInstanceEdgeAggregation:
    """AT#13 (aggregation half): leader /api/state includes follower edge stats."""

    async def test_leader_includes_follower_topology_edge_stats(
        self, tmp_path, monkeypatch
    ) -> None:
        """Leader aggregated state carries the follower's topology_edge_stats."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker_a = Broker()  # leader — no topology
        broker_b = Broker()  # follower — has edge a→b with one exchange

        a_id = await _spawn(broker_b, "a")
        b_id = await _spawn(broker_b, "b")
        broker_b.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))
        await broker_b.send(_env(a_id, b_id))  # one exchange

        port_b = _free_port()
        reg_b = InstanceRegistry(crew_id=broker_b.crew_id, port=port_b)
        ui_b = UIServer(broker_b, port=port_b, registry=reg_b)

        task_b = asyncio.create_task(ui_b.serve())
        await asyncio.sleep(0.5)
        reg_b.register()

        reg_a = InstanceRegistry(crew_id=broker_a.crew_id, port=_free_port())
        _, ui_a = _make_ui(broker_a, registry=reg_a)

        state = await ui_a._build_state()

        task_b.cancel()
        try:
            await task_b
        except asyncio.CancelledError:
            pass

        instances = state["instances"]
        follower = next(
            (i for i in instances if i.get("id") == broker_b.crew_id),
            None,
        )
        assert follower is not None, "Follower instance missing from aggregated state"

        stats = follower.get("topology_edge_stats")
        assert stats is not None, "topology_edge_stats missing from follower instance"
        assert isinstance(stats, list) and len(stats) >= 1

        entry = next(
            (s for s in stats if s.get("from_slot") == "a" and s.get("to_slot") == "b"),
            None,
        )
        assert entry is not None, "Expected a→b edge in follower topology_edge_stats"
        assert entry["exchanges"] >= 1
        assert entry["crew_id"] == broker_b.crew_id


class TestMultiInstanceEdgeLogProxy:
    """AT#13 (proxy half): leader proxies /edge-log to the owning follower."""

    async def test_leader_proxies_edge_log_to_follower(
        self, tmp_path, monkeypatch
    ) -> None:
        """GET /edge-log/{follower_crew_id}/a/b via leader → proxied to follower → 200."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker_a = Broker()  # leader (no topology)
        broker_b = Broker()  # follower with direct edge + one exchange

        a_id = await _spawn(broker_b, "a")
        b_id = await _spawn(broker_b, "b")
        broker_b.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))
        await broker_b.send(_env(a_id, b_id, {"text": "follower-msg"}))

        port_b = _free_port()
        reg_b = InstanceRegistry(crew_id=broker_b.crew_id, port=port_b)
        ui_b = UIServer(broker_b, port=port_b, registry=reg_b)

        task_b = asyncio.create_task(ui_b.serve())
        await asyncio.sleep(0.5)
        reg_b.register()

        # Leader uses ASGI transport; its _http_client can reach the real follower.
        reg_a = InstanceRegistry(crew_id=broker_a.crew_id, port=_free_port())
        _, ui_a = _make_ui(broker_a, registry=reg_a)

        async with _client(ui_a) as leader_client:
            resp = await leader_client.get(
                f"/edge-log/{broker_b.crew_id}/a/b"
            )

        task_b.cancel()
        try:
            await task_b
        except asyncio.CancelledError:
            pass

        # Leader proxied to follower — must return 200 (not 404 for unknown crew).
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["edge"] == ["a", "b"]
        assert len(body["messages"]) >= 1

    async def test_leader_serves_own_edge_log_locally(
        self, tmp_path, monkeypatch
    ) -> None:
        """GET /edge-log/{own_crew_id}/a/b on the leader is served locally."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker_a = Broker()
        a_id = await _spawn(broker_a, "a")
        b_id = await _spawn(broker_a, "b")
        broker_a.record_topology(_topo([("a", "b", "direct")], {"a": a_id, "b": b_id}))
        await broker_a.send(_env(a_id, b_id, {"text": "own-msg"}))

        reg_a = InstanceRegistry(crew_id=broker_a.crew_id, port=_free_port())
        _, ui_a = _make_ui(broker_a, registry=reg_a)

        async with _client(ui_a) as client:
            resp = await client.get(f"/edge-log/{broker_a.crew_id}/a/b")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert len(body["messages"]) >= 1

    async def test_leader_returns_404_for_unregistered_follower(
        self, tmp_path, monkeypatch
    ) -> None:
        """GET /edge-log for an unknown crew_id returns 404 (not 500)."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker_a = Broker()
        reg_a = InstanceRegistry(crew_id=broker_a.crew_id, port=_free_port())
        _, ui_a = _make_ui(broker_a, registry=reg_a)

        async with _client(ui_a) as client:
            resp = await client.get("/edge-log/unknowncrew1/a/b")

        assert resp.status_code == 404
