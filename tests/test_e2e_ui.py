"""E2E tests for the Mission Control UI.

Three scenarios that the unit tests in test_ui_server.py do not cover:

1. State lifecycle — real broker mutations (spawn / kill) flow through _build_state()
   into the HTTP response.  Exercises the full data pipeline under live conditions.

2. WebSocket reflects real state — the initial push from /ws matches actual broker
   state, not a stale snapshot.  Also covers transcript content visibility.

3. Port-bound smoke test — UIServer.serve() binds a real port, responds to HTTP, and
   shuts down cleanly when the asyncio task is cancelled.  The only test that proves
   the anyio co-run doesn't deadlock or corrupt state at startup/shutdown.

No Claude SDK or API key is required — StubTeammate mode throughout.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from typing import AsyncGenerator

import pytest
from starlette.testclient import TestClient

from claude_crew.broker import Broker, LEAD_ID
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.teammate import StubTeammate
from claude_crew.ui_server import UIServer


def _stub_factory(tid: str, name: str, role: str, **kw: object) -> StubTeammate:
    return StubTeammate(tid, name, role)


def _client(broker: Broker) -> TestClient:
    return TestClient(UIServer(broker, port=0)._make_app())


# ── Scenario 1: State lifecycle ──────────────────────────────────────────────


class TestStateLifecycle:
    """Spawn and kill real StubTeammates; verify /api/state tracks every change."""

    # ── empty crew ────────────────────────────────────────────────────────────

    def test_empty_crew_before_any_spawn(self) -> None:
        broker = Broker()
        with _client(broker) as client:
            data = client.get("/api/state").json()
        assert data["instances"][0]["agents"] == []
        assert data["instances"][0]["status"] == "idle"

    # ── single teammate lifecycle ─────────────────────────────────────────────

    async def test_teammate_appears_after_spawn(self) -> None:
        broker = Broker()
        await broker.spawn_teammate(role="builder", name="builder", factory=_stub_factory)
        with _client(broker) as client:
            data = client.get("/api/state").json()
        agents = data["instances"][0]["agents"]
        assert len(agents) == 1
        assert agents[0]["role"] == "builder"

    async def test_agent_fields_present(self) -> None:
        broker = Broker()
        await broker.spawn_teammate(role="builder", name="builder", factory=_stub_factory)
        with _client(broker) as client:
            data = client.get("/api/state").json()
        agent = data["instances"][0]["agents"][0]
        for field in ("id", "role", "model", "status", "uptime", "lastMsg",
                      "cost", "tokens", "tools", "current_tool"):
            assert field in agent, f"missing field: {field}"

    async def test_agent_status_valid(self) -> None:
        broker = Broker()
        await broker.spawn_teammate(role="builder", name="builder", factory=_stub_factory)
        with _client(broker) as client:
            data = client.get("/api/state").json()
        status = data["instances"][0]["agents"][0]["status"]
        assert status in ("idle", "thinking", "tool-use")

    async def test_instance_status_active_with_agents(self) -> None:
        broker = Broker()
        await broker.spawn_teammate(role="builder", name="builder", factory=_stub_factory)
        with _client(broker) as client:
            data = client.get("/api/state").json()
        assert data["instances"][0]["status"] == "active"

    async def test_teammate_disappears_after_kill(self) -> None:
        broker = Broker()
        tid = await broker.spawn_teammate(role="builder", name="builder", factory=_stub_factory)
        await broker.kill_teammate(tid)
        with _client(broker) as client:
            data = client.get("/api/state").json()
        assert data["instances"][0]["agents"] == []

    async def test_instance_status_idle_after_all_killed(self) -> None:
        broker = Broker()
        tid = await broker.spawn_teammate(role="builder", name="builder", factory=_stub_factory)
        await broker.kill_teammate(tid)
        with _client(broker) as client:
            data = client.get("/api/state").json()
        assert data["instances"][0]["status"] == "idle"

    # ── multiple teammates ────────────────────────────────────────────────────

    async def test_multiple_teammates_all_appear(self) -> None:
        broker = Broker()
        for role in ("planner", "builder", "reviewer"):
            await broker.spawn_teammate(role=role, name=role, factory=_stub_factory)
        with _client(broker) as client:
            data = client.get("/api/state").json()
        roles = {a["role"] for a in data["instances"][0]["agents"]}
        assert roles == {"planner", "builder", "reviewer"}

    async def test_surviving_teammates_visible_after_partial_kill(self) -> None:
        broker = Broker()
        tid1 = await broker.spawn_teammate(role="planner", name="planner", factory=_stub_factory)
        await broker.spawn_teammate(role="builder", name="builder", factory=_stub_factory)
        await broker.kill_teammate(tid1)
        with _client(broker) as client:
            data = client.get("/api/state").json()
        agents = data["instances"][0]["agents"]
        assert len(agents) == 1
        assert agents[0]["role"] == "builder"

    # ── instance identity ─────────────────────────────────────────────────────

    def test_instance_id_matches_broker_crew_id(self) -> None:
        broker = Broker()
        with _client(broker) as client:
            data = client.get("/api/state").json()
        assert data["instances"][0]["id"] == broker.crew_id

    def test_transcripts_keyed_by_crew_id(self) -> None:
        broker = Broker()
        with _client(broker) as client:
            data = client.get("/api/state").json()
        assert broker.crew_id in data["transcripts"]


# ── Scenario 2: WebSocket reflects real broker state ─────────────────────────


class TestWebSocketLifecycle:
    """The WebSocket initial push reflects actual live broker state — not stale data."""

    def test_initial_push_empty_agents(self) -> None:
        broker = Broker()
        with _client(broker) as client:
            with client.websocket_connect("/ws") as ws:
                msg = ws.receive_json()
        assert msg["type"] == "state"
        assert msg["data"]["instances"][0]["agents"] == []

    async def test_initial_push_reflects_spawned_teammate(self) -> None:
        broker = Broker()
        await broker.spawn_teammate(role="scout", name="scout", factory=_stub_factory)
        with _client(broker) as client:
            with client.websocket_connect("/ws") as ws:
                msg = ws.receive_json()
        agents = msg["data"]["instances"][0]["agents"]
        assert len(agents) == 1
        assert agents[0]["role"] == "scout"

    async def test_initial_push_excludes_dead_teammate(self) -> None:
        broker = Broker()
        tid = await broker.spawn_teammate(role="scout", name="scout", factory=_stub_factory)
        await broker.kill_teammate(tid)
        with _client(broker) as client:
            with client.websocket_connect("/ws") as ws:
                msg = ws.receive_json()
        assert msg["data"]["instances"][0]["agents"] == []

    def test_crew_id_in_push_matches_broker(self) -> None:
        broker = Broker()
        with _client(broker) as client:
            with client.websocket_connect("/ws") as ws:
                msg = ws.receive_json()
        assert msg["data"]["instances"][0]["id"] == broker.crew_id
        assert broker.crew_id in msg["data"]["transcripts"]

    def test_transcript_message_visible_via_ws(self) -> None:
        """An envelope injected into the broker log appears in the WS transcript."""
        broker = Broker()
        env = Envelope(
            id=new_message_id(), seq=1, sender=LEAD_ID,
            recipient="t-test", timestamp=time.time(),
            payload="integration check",
        )
        broker._log.append(env)
        with _client(broker) as client:
            with client.websocket_connect("/ws") as ws:
                msg = ws.receive_json()
        bodies = [m["body"] for m in msg["data"]["transcripts"][broker.crew_id]]
        assert any("integration check" in b for b in bodies)

    def test_disconnect_does_not_crash_subsequent_http(self) -> None:
        """After a client disconnects, the server still handles HTTP normally."""
        broker = Broker()
        with _client(broker) as client:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
            # ws context exits → disconnects

            resp = client.get("/api/state")
        assert resp.status_code == 200


# ── Scenario 3: Port-bound smoke test ────────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _get(url: str) -> dict:
    """Non-blocking HTTP GET via thread executor so uvicorn can serve the request."""
    import urllib.request

    def _fetch() -> dict:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return {"status": resp.status, "body": resp.read()}

    result = await asyncio.to_thread(_fetch)
    return result


async def test_serve_binds_real_port_and_responds() -> None:
    """UIServer.serve() binds an actual TCP port and answers real HTTP requests."""
    port = _free_port()
    broker = Broker()
    ui = UIServer(broker, port=port)

    task = asyncio.create_task(ui.serve())
    await asyncio.sleep(0.5)

    try:
        result = await _get(f"http://127.0.0.1:{port}/api/state")
        assert result["status"] == 200
        data = json.loads(result["body"])
        assert "instances" in data
        assert data["instances"][0]["id"] == broker.crew_id
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # expected — uvicorn exits cleanly on cancellation


async def test_serve_root_returns_html_on_real_port() -> None:
    """GET / on a real bound port returns the dashboard HTML."""
    port = _free_port()
    broker = Broker()
    ui = UIServer(broker, port=port)

    task = asyncio.create_task(ui.serve())
    await asyncio.sleep(0.5)

    try:
        result = await _get(f"http://127.0.0.1:{port}/")
        assert result["status"] == 200
        assert "claude-crew" in result["body"].decode().lower()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_serve_reflects_post_startup_spawn() -> None:
    """Teammates spawned after UIServer starts appear immediately in /api/state."""
    port = _free_port()
    broker = Broker()
    ui = UIServer(broker, port=port)

    task = asyncio.create_task(ui.serve())
    await asyncio.sleep(0.5)

    try:
        # No agents yet
        result = await _get(f"http://127.0.0.1:{port}/api/state")
        data = json.loads(result["body"])
        assert data["instances"][0]["agents"] == []

        # Spawn
        await broker.spawn_teammate(role="builder", name="builder", factory=_stub_factory)

        # Agent appears immediately on next request (no cache between _build_state calls)
        result = await _get(f"http://127.0.0.1:{port}/api/state")
        data = json.loads(result["body"])
        assert len(data["instances"][0]["agents"]) == 1
        assert data["instances"][0]["agents"][0]["role"] == "builder"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
