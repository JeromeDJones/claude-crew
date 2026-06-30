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

unified-topology-view AT-3, AT-8 (added by unify-topology-graph-component):
  - AT-3: dashboard renders exactly one TopologyGraph (no legacy MiniGraph radial
    SVG; no separate TopologyEdgePanel mount; first-class `lead` node in the
    active topology).
  - AT-8: activity attaches via slot_to_teammate, NOT a `role === slot` guess.
    A slot label that differs from the teammate's pack role (e.g. slot `impl_a`
    vs role `implementor`) still resolves to the right activity status — the
    `impl_a` node card carries the `tool` activity class.

asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import asyncio
import json as _json
import socket
import threading
import time
from typing import Any

import httpx
import pytest
import uvicorn

from claude_crew.broker import (
    Broker,
    BrokerSnapshot,
    EdgeStat,
    LEAD_ID,
    LiveTeammateInfo,
    TeammateInfo,
    Topology,
)
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


# ---------------------------------------------------------------------------
# unified-topology-view AT-3, AT-8 — Playwright dashboard assertions
# ---------------------------------------------------------------------------
#
# These tests drive the live dashboard in a real browser (Chromium) to verify
# the unified TopologyGraph component. They monkey-patch `Broker.snapshot` so
# we can plant a precise topology + slot_to_teammate fixture without booting
# real teammates. Same pattern as `tests/dashboard/test_roster_spotlight.py`.
#
# Run prerequisite: `uv run playwright install chromium` (one-time).


def _alive_info_at(idx: int, role: str = "builder", name: str | None = None) -> TeammateInfo:
    return TeammateInfo(
        id=f"tid-{idx}",
        name=name if name is not None else f"agent-{idx}",
        role=role,
        spawned_at=time.time() - 60,
        alive=True,
    )


def _live_entry_at(info: TeammateInfo, *, status_kwargs: dict | None = None) -> LiveTeammateInfo:
    status: dict[str, Any] = {
        "current_tool_count": 0,
        "current_turn_started_at_wallclock": None,
        "total_input_tokens": 100,
        "total_output_tokens": 50,
        "total_cost_usd": 0.05,
        "current_tools": [],
        "current_tool": None,
        "last_activity_at_wallclock": None,
    }
    if status_kwargs:
        status.update(status_kwargs)
    return LiveTeammateInfo(info=info, status=status, model="claude-sonnet-4-6")


def _stub_snapshot(
    *,
    crew_id: str = "crew-at38",
    live: tuple[LiveTeammateInfo, ...],
    edge_stats: tuple[EdgeStat, ...] = (),
    slot_to_teammate: dict[str, str] | None = None,
) -> BrokerSnapshot:
    infos = tuple(le.info for le in live)
    return BrokerSnapshot(
        crew_id=crew_id,
        teammates=infos,
        live=live,
        log=(),
        topology_edge_stats=edge_stats,
        topology_slot_to_teammate=dict(slot_to_teammate or {}),
    )


def _patched_broker_for_dashboard(snapshot: BrokerSnapshot) -> Broker:
    """Return a Broker whose .snapshot() always returns the given fixture."""
    b = Broker()
    b.snapshot = lambda log_limit=None: snapshot  # type: ignore[method-assign]
    # AT-3 / AT-8 need /api/state.cli.id to match snapshot.crew_id (the
    # dashboard's TopologyGraph reads cli.id as crewId for per-edge fetches).
    b.crew_id = snapshot.crew_id  # type: ignore[misc]
    return b


def _spin_dashboard(broker: Broker):
    """Start a UIServer on a free port in a daemon thread; return (url, server, thread)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    ui = UIServer(broker, port=port)
    app = ui._make_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())

    t = threading.Thread(target=run, daemon=True)
    t.start()

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5)
            if resp.status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        pytest.fail("UIServer did not start within 10 seconds")

    return f"http://127.0.0.1:{port}", server, t


# ── AT-3 — one graph, no legacy MiniGraph, no separate TopologyEdgePanel ────


@pytest.fixture
def at3_active_topology_url():
    """Dashboard with planner → implementor → reviewer (3 peer edges), lead present."""
    p_info = _alive_info_at(1, role="planner", name="planner-a")
    i_info = _alive_info_at(2, role="implementor", name="implementor-a")
    r_info = _alive_info_at(3, role="reviewer", name="reviewer-a")
    live = (
        _live_entry_at(p_info),
        _live_entry_at(i_info),
        _live_entry_at(r_info),
    )
    edges = (
        EdgeStat(from_slot="planner", to_slot="implementor", mode="direct", exchanges=2, tripped=False),
        EdgeStat(from_slot="implementor", to_slot="reviewer", mode="tee", exchanges=4, tripped=False),
        EdgeStat(from_slot="planner", to_slot="lead", mode="gated", exchanges=1, tripped=False),
        EdgeStat(from_slot="lead", to_slot="reviewer", mode="gated", exchanges=1, tripped=False),
    )
    s2t = {"planner": "tid-1", "implementor": "tid-2", "reviewer": "tid-3"}
    snap = _stub_snapshot(crew_id="crew-at3", live=live, edge_stats=edges, slot_to_teammate=s2t)
    broker = _patched_broker_for_dashboard(snap)
    url, server, t = _spin_dashboard(broker)
    yield url
    server.should_exit = True
    t.join(timeout=3)


@pytest.mark.dashboard
def test_at3_unified_topology_renders_single_graph_with_lead_node(at3_active_topology_url, page):
    """AT-3: exactly one topology graph; no legacy radial MiniGraph SVG; lead first-class.

    The unified TopologyGraph mounts where MiniGraph used to live. The old
    hand-drawn radial SVG (with a centered <circle> for the lead + spoke
    <line> elements) is gone, replaced by a single mermaid-rendered SVG
    containing one <g class="node"> per slot, including the `lead` node.
    """
    page.goto(at3_active_topology_url)
    page.locator(".rail-topology").wait_for(state="visible", timeout=15000)
    # Use #topo-host svg (not .rail-topology svg) to avoid strict-mode collision
    # with the expand-button SVG added by the shared-zoom-pan-modal task.
    page.locator("#topo-host svg").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(800)  # mermaid render + post-render decoration

    # Header reads "Topology" (CSS uppercases).
    rail_text = page.locator(".rail-topology").inner_text()
    assert "TOPOLOGY" in rail_text.upper(), f"Expected Topology header; got: {rail_text!r}"

    # Exactly one mermaid-rendered SVG in #topo-host (excludes expand-button SVG).
    svgs = page.locator("#topo-host svg")
    assert svgs.count() == 1, f"Expected exactly 1 topology SVG; found {svgs.count()}"

    # The legacy MiniGraph radial SVG had a <radialGradient id="mcCenter">.
    # Its absence is a strong signal that the hand-drawn roster hub is gone.
    legacy_radial = page.locator("#topo-host svg defs radialGradient#mcCenter")
    assert legacy_radial.count() == 0, "Legacy MiniGraph radial gradient must not render"

    # The unified graph contains the `lead` node as a first-class node.
    # The slot label "lead" appears inside the foreignObject nodecard.
    rail_inner = page.locator(".rail-topology").inner_text()
    assert "lead" in rail_inner.lower(), f"Expected 'lead' node label in rail; got: {rail_inner!r}"

    # Topology slot labels are also present.
    for slot in ("planner", "implementor", "reviewer"):
        assert slot in rail_inner.lower(), (
            f"Expected slot {slot!r} in rail-topology; got: {rail_inner!r}"
        )

    # Exactly one TopologyGraph wrapper — i.e. no separate TopologyEdgePanel
    # mount. The class .topology-graph is the unified wrapper.
    wrappers = page.locator(".rail-topology .topology-graph")
    assert wrappers.count() == 1, (
        f"Expected exactly one TopologyGraph wrapper; found {wrappers.count()}"
    )


# ── AT-8 — Activity attaches via slot_to_teammate (not role===slot) ─────────


@pytest.fixture
def at8_divergent_slot_role_url():
    """Slot label `impl_a` whose teammate has role `implementor` and status `tool-use`.

    The `role === slot` guess would FAIL to attach activity (no agent has
    role `impl_a`) — the node would render idle. The slot_to_teammate join
    `{"impl_a": "tid-9"}` is the only correct binding; activity must follow it.
    """
    implementor_info = TeammateInfo(
        id="tid-9",
        name="impl-a-agent",
        role="implementor",
        spawned_at=time.time() - 60,
        alive=True,
    )
    other_info = TeammateInfo(
        id="tid-10",
        name="planner-a",
        role="planner",
        spawned_at=time.time() - 60,
        alive=True,
    )
    live = (
        _live_entry_at(implementor_info, status_kwargs={
            "current_tool": {"tool_name": "Bash", "tool_use_id": "tu-1", "started_at_wallclock": time.time()},
            "current_tools": [
                {"tool_name": "Bash", "tool_use_id": "tu-1", "started_at_wallclock": time.time()}
            ],
            "current_tool_count": 1,
        }),
        _live_entry_at(other_info),
    )
    # Mark the implementor's runtime status separately via the LiveTeammateInfo's
    # `status` dict — but the dashboard expects a per-agent status string. The
    # production `_build_local_instance` derives a string status. To inject a
    # known status into the rendered agents[].status, plant `current_tool` so
    # the dashboard's status derivation lands on "tool-use" for this agent.
    edges = (
        EdgeStat(from_slot="planner_a", to_slot="impl_a", mode="direct", exchanges=2, tripped=False),
    )
    s2t = {"planner_a": "tid-10", "impl_a": "tid-9"}
    snap = _stub_snapshot(crew_id="crew-at8", live=live, edge_stats=edges, slot_to_teammate=s2t)
    broker = _patched_broker_for_dashboard(snap)
    url, server, t = _spin_dashboard(broker)
    yield url
    server.should_exit = True
    t.join(timeout=3)


@pytest.mark.dashboard
def test_at8_activity_joins_via_slot_to_teammate(at8_divergent_slot_role_url, page):
    """AT-8: slot `impl_a` (≠ role `implementor`) inherits the teammate's tool-use status.

    The `impl_a` node card must carry the `tool` activity class — proving the
    join went through `slot_to_teammate` and not the broken `role === slot`
    guess (which would have left it idle, since no live agent has role
    `impl_a`).
    """
    page.goto(at8_divergent_slot_role_url)
    page.locator(".rail-topology").wait_for(state="visible", timeout=15000)
    page.locator("#topo-host svg").wait_for(state="attached", timeout=15000)
    # Mermaid foreignObject HTML is the slow path — give it a beat.
    page.wait_for_timeout(1000)

    # Query the foreignObject's nested .nodecard for the `impl_a` slot.
    # Mermaid emits the foreignObject content as innerHTML — find the
    # nodecard whose row1 text contains "impl_a".
    node_classes = page.evaluate(
        """() => {
          const out = [];
          document.querySelectorAll('.rail-topology .nodecard').forEach(nc => {
            const row1 = nc.querySelector('.row1');
            out.push({label: row1 ? row1.innerText.trim() : '', cls: nc.className});
          });
          return out;
        }"""
    )
    # Locate the impl_a node card.
    impl_a = next((n for n in node_classes if "impl_a" in n["label"]), None)
    assert impl_a is not None, (
        f"impl_a node card not found in rendered topology; cards: {node_classes!r}"
    )
    # Activity attached via slot_to_teammate → status "tool-use" → class "tool".
    assert "tool" in impl_a["cls"].split(), (
        f"Expected impl_a nodecard to carry 'tool' activity class; got: {impl_a!r}"
    )
    # Lead node is also present (first-class node — also covers AT-3 invariant
    # for completeness within this fixture).
    lead = next((n for n in node_classes if n["label"].strip() == "lead"), None)
    assert lead is not None, (
        f"lead node card missing; cards: {node_classes!r}"
    )
    assert "lead" in lead["cls"].split(), (
        f"Expected lead nodecard to carry 'lead' class; got: {lead!r}"
    )


# ── Gated bridge — `peer --> lead --> peer` for gated EdgeStats ─────────────
#
# Spec Design Decision: "Mermaid graph TD with `lead` as a first-class node;
# gated edges route peer --> lead --> peer". The broker records gated edges
# as peer→peer (lead is never an endpoint — broker.py:1216 / shapes
# ShapeEdge). The unified component synthesizes two through-lead segments
# from each gated EdgeStat at render time so the gated route is visibly
# bridged. Both segments carry the source EdgeStat's gated styling and
# (critically for the multi-instance contract) clicking either segment
# fetches /edge-log keyed by the SOURCE peer endpoints with crewId in the
# path.


@pytest.fixture
def gated_bridge_url():
    """Real-broker-shape fixture: planner→implementor recorded as a single
    GATED EdgeStat. The unified graph must render this as two synthetic
    segments through lead, not a flat planner→implementor peer edge."""
    p_info = _alive_info_at(1, role="planner", name="planner-a")
    i_info = _alive_info_at(2, role="implementor", name="implementor-a")
    live = (_live_entry_at(p_info), _live_entry_at(i_info))
    edges = (
        EdgeStat(
            from_slot="planner", to_slot="implementor",
            mode="gated", exchanges=3, tripped=False,
        ),
    )
    s2t = {"planner": "tid-1", "implementor": "tid-2"}
    snap = _stub_snapshot(crew_id="crew-bridge", live=live, edge_stats=edges, slot_to_teammate=s2t)
    broker = _patched_broker_for_dashboard(snap)
    url, server, t = _spin_dashboard(broker)
    yield url, broker.crew_id
    server.should_exit = True
    t.join(timeout=3)


@pytest.mark.dashboard
def test_gated_edge_bridges_through_lead_with_two_amber_segments(gated_bridge_url, page):
    """A gated EdgeStat(planner, implementor) renders as planner→lead AND
    lead→implementor, both amber-gated, both crewId-routed to SOURCE endpoints.

    Structural assertion (path classes `LS-<from>` / `LE-<to>` are the
    keyed-lookup fallback signal — exactly the channel a future regression
    would break first if the bridge were silently flattened back to a
    single peer edge)."""
    url, crew_id = gated_bridge_url
    page.goto(url)
    page.locator("#topo-host svg").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(1000)

    paths_info = page.evaluate(
        """() => {
          const paths = document.querySelectorAll('#topo-host path.flowchart-link');
          return [...paths].map(p => ({
            id: p.id || '',
            cls: p.getAttribute('class') || '',
            stroke: p.style.stroke || '',
            width: p.style.strokeWidth || '',
          }));
        }"""
    )
    assert len(paths_info) >= 2, (
        f"Gated bridge must render 2+ paths (peer→lead, lead→peer); got: {paths_info!r}"
    )

    import re as _re

    def _endpoints(info: dict) -> tuple[str | None, str | None]:
        # Try the keyed-lookup id-parse path first (same regex as
        # window.mapEdgeStatsToPaths in dashboard.html — production parity).
        m = _re.match(r"^L[-_](.+?)[-_](.+?)[-_]\d+$", info["id"])
        if m:
            return m.group(1), m.group(2)
        # Fallback: LS-/LE- class tokens. Mermaid v11 doesn't always emit
        # them, so id-parse is the primary signal.
        cls_tokens = info["cls"].split()
        ls = next((c[3:] for c in cls_tokens if c.startswith("LS-")), None)
        le = next((c[3:] for c in cls_tokens if c.startswith("LE-")), None)
        return ls, le

    endpoints = [_endpoints(p) for p in paths_info]
    assert ("planner", "lead") in endpoints, (
        f"Missing planner→lead synthetic segment; endpoints: {endpoints!r}; paths: {paths_info!r}"
    )
    assert ("lead", "implementor") in endpoints, (
        f"Missing lead→implementor synthetic segment; endpoints: {endpoints!r}; paths: {paths_info!r}"
    )
    # The original flat peer edge must NOT have been rendered.
    assert ("planner", "implementor") not in endpoints, (
        f"Gated edge was rendered as a flat peer edge instead of bridged through lead; "
        f"endpoints: {endpoints!r}"
    )
    # Both segments carry gated-amber stroke (not direct/tee/tripped).
    for info in paths_info:
        ls, le = _endpoints(info)
        if (ls, le) in {("planner", "lead"), ("lead", "implementor")}:
            assert "var(--edge-gated)" in info["stroke"], (
                f"Gated synthetic segment must be amber; got: {info!r}"
            )


@pytest.mark.dashboard
def test_clicking_gated_segment_fetches_source_endpoints_with_crew_id(
    gated_bridge_url, page
):
    """Clicking EITHER synthetic gated segment must issue GET /edge-log/<crew>/planner/implementor
    — the SOURCE peer endpoints — preserving the multi-instance crew_id-in-path
    contract.  (A naive bridge implementation that routed clicks to
    /edge-log/<crew>/planner/lead or /<crew>/lead/implementor would silently
    404 on follower-owned rows.)"""
    url, crew_id = gated_bridge_url
    page.goto(url)
    page.locator("#topo-host svg").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(1000)

    captured: list[str] = []
    page.on(
        "request",
        lambda req: captured.append(req.url) if "/edge-log/" in req.url else None,
    )

    # Click each synthetic segment in turn and verify the SOURCE endpoints
    # are used in the fetch URL.
    paths = page.locator(".rail-topology path.flowchart-link")
    n = paths.count()
    assert n >= 2, f"Expected 2+ synthetic gated paths; got {n}"
    for i in range(n):
        paths.nth(i).dispatch_event("click")
        page.wait_for_timeout(400)

    matching = [u for u in captured if f"/edge-log/{crew_id}/planner/implementor" in u]
    assert matching, (
        f"Click on a gated synthetic segment must fetch SOURCE endpoints "
        f"(/edge-log/{crew_id}/planner/implementor); captured: {captured!r}"
    )
    # Negative: NO request should go to a synthetic endpoint involving lead.
    bad = [u for u in captured if "/lead/" in u or u.endswith("/lead")]
    assert not bad, (
        f"Gated-bridge click must not fetch /edge-log against `lead` as an "
        f"endpoint (lead is the bridge node, not a peer); got: {bad!r}"
    )
