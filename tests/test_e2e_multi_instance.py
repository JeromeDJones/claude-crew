"""E2E integration tests for multi-instance registry and aggregation.

Exercises the full pipeline: registry write → registry read → HTTP fanout
→ merged WebSocket payload. No live SDK calls — StubTeammate and real
UIServer instances running in-process.

Test taxonomy:
  Happy path  — two instances both appear; deregistration on cancel
  Sad path    — dead entry excluded; unreachable remote; corrupt file
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from pathlib import Path

import pytest

from claude_crew.broker import Broker, LEAD_ID
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.instance_registry import InstanceRegistry
from claude_crew.teammate import StubTeammate
from claude_crew.ui_server import UIServer


# ── helpers ──────────────────────────────────────────────────────────────────


def _stub_factory(tid: str, name: str, role: str, **kw: object) -> StubTeammate:
    return StubTeammate(tid, name, role)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _http_get(url: str) -> dict:
    import urllib.request
    def _fetch() -> dict:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return {"status": resp.status, "body": resp.read()}
    return await asyncio.to_thread(_fetch)


def _make_ui(broker: Broker, registry: InstanceRegistry, port: int = 0) -> UIServer:
    return UIServer(broker, port=port, registry=registry)


# ── Happy path: two instances, both appear ────────────────────────────────────


class TestMultiInstanceAggregation:
    """Two UIServers with separate brokers; one reads the other via registry."""

    async def test_two_instances_both_appear_in_build_state(self, tmp_path, monkeypatch):
        """Aggregated _build_state() returns entries from both instances."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker_a = Broker()
        broker_b = Broker()

        port_b = _free_port()
        reg_a = InstanceRegistry(crew_id=broker_a.crew_id, port=_free_port())
        reg_b = InstanceRegistry(crew_id=broker_b.crew_id, port=port_b)

        ui_b = UIServer(broker_b, port=port_b, registry=reg_b)

        # Start UIServer B on a real port
        task_b = asyncio.create_task(ui_b.serve())
        await asyncio.sleep(0.5)

        # Register B in the shared registry dir
        reg_b.register()

        # Build state from A's perspective (A knows about B via registry)
        ui_a = UIServer(broker_a, port=0, registry=reg_a)
        state = await ui_a._build_state()

        ids = {inst["id"] for inst in state["instances"]}
        assert broker_a.crew_id in ids, "local (A) missing from instances"
        assert broker_b.crew_id in ids, "remote (B) missing from instances"

        task_b.cancel()
        try:
            await task_b
        except asyncio.CancelledError:
            pass

    async def test_local_instance_has_is_local_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))
        broker = Broker()
        reg = InstanceRegistry(crew_id=broker.crew_id, port=0)
        ui = UIServer(broker, port=0, registry=reg)
        state = await ui._build_state()
        local = next(i for i in state["instances"] if i["id"] == broker.crew_id)
        assert local["is_local"] is True

    async def test_remote_instance_has_is_local_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker_a = Broker()
        broker_b = Broker()

        port_b = _free_port()
        reg_a = InstanceRegistry(crew_id=broker_a.crew_id, port=0)
        reg_b = InstanceRegistry(crew_id=broker_b.crew_id, port=port_b)

        ui_b = UIServer(broker_b, port=port_b, registry=reg_b)
        task_b = asyncio.create_task(ui_b.serve())
        await asyncio.sleep(0.5)
        reg_b.register()

        ui_a = UIServer(broker_a, port=0, registry=reg_a)
        state = await ui_a._build_state()

        remote = next((i for i in state["instances"] if i["id"] == broker_b.crew_id), None)
        assert remote is not None
        assert remote["is_local"] is False

        task_b.cancel()
        try:
            await task_b
        except asyncio.CancelledError:
            pass

    async def test_remote_transcript_included(self, tmp_path, monkeypatch):
        """Transcripts from a remote instance appear keyed by its crew_id."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker_a = Broker()
        broker_b = Broker()

        # Plant a message in broker_b's log
        env = Envelope(
            id=new_message_id(), seq=1, sender=LEAD_ID,
            recipient="t-test", timestamp=time.time(),
            payload="hello from b",
        )
        broker_b._log.append(env)

        port_b = _free_port()
        reg_a = InstanceRegistry(crew_id=broker_a.crew_id, port=0)
        reg_b = InstanceRegistry(crew_id=broker_b.crew_id, port=port_b)

        ui_b = UIServer(broker_b, port=port_b, registry=reg_b)
        task_b = asyncio.create_task(ui_b.serve())
        await asyncio.sleep(0.5)
        reg_b.register()

        ui_a = UIServer(broker_a, port=0, registry=reg_a)
        state = await ui_a._build_state()

        assert broker_b.crew_id in state["transcripts"]
        bodies = [m["body"] for m in state["transcripts"][broker_b.crew_id]]
        assert any("hello from b" in b for b in bodies)

        task_b.cancel()
        try:
            await task_b
        except asyncio.CancelledError:
            pass

    async def test_remote_agent_appears_in_aggregated_state(self, tmp_path, monkeypatch):
        """An agent spawned in instance B is visible in instance A's dashboard."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker_a = Broker()
        broker_b = Broker()
        await broker_b.spawn_teammate(role="explorer", name="explorer", factory=_stub_factory)

        port_b = _free_port()
        reg_a = InstanceRegistry(crew_id=broker_a.crew_id, port=0)
        reg_b = InstanceRegistry(crew_id=broker_b.crew_id, port=port_b)

        ui_b = UIServer(broker_b, port=port_b, registry=reg_b)
        task_b = asyncio.create_task(ui_b.serve())
        await asyncio.sleep(0.5)
        reg_b.register()

        ui_a = UIServer(broker_a, port=0, registry=reg_a)
        state = await ui_a._build_state()

        remote_inst = next(i for i in state["instances"] if i["id"] == broker_b.crew_id)
        assert len(remote_inst["agents"]) == 1
        assert remote_inst["agents"][0]["role"] == "explorer"

        task_b.cancel()
        try:
            await task_b
        except asyncio.CancelledError:
            pass


# ── Happy path: deregistration on serve() cancellation ───────────────────────


class TestDeregistrationOnCancel:
    async def test_registry_file_absent_after_serve_cancelled(self, tmp_path, monkeypatch):
        """UIServer.serve() removes the registry file when the task is cancelled."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker = Broker()
        port = _free_port()
        reg = InstanceRegistry(crew_id=broker.crew_id, port=port)
        ui = UIServer(broker, port=port, registry=reg)

        task = asyncio.create_task(ui.serve())
        await asyncio.sleep(0.3)

        # File should be present while serving
        assert (tmp_path / f"{broker.crew_id}.json").exists()

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # File must be gone after cancellation
        assert not (tmp_path / f"{broker.crew_id}.json").exists()

    async def test_read_all_does_not_return_cancelled_instance(self, tmp_path, monkeypatch):
        """After deregistration, read_all() does not include the stopped instance."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        broker = Broker()
        port = _free_port()
        reg = InstanceRegistry(crew_id=broker.crew_id, port=port)
        ui = UIServer(broker, port=port, registry=reg)

        task = asyncio.create_task(ui.serve())
        await asyncio.sleep(0.3)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        entries = reg.read_all()
        ids = [e["crew_id"] for e in entries]
        assert broker.crew_id not in ids


# ── Sad path: dead registry entry ────────────────────────────────────────────


class TestDeadEntryExclusion:
    async def test_dead_pid_entry_not_in_build_state(self, tmp_path, monkeypatch):
        """A registry entry with a dead PID is not shown in _build_state()."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        dead_entry = {"crew_id": "deadcrew", "port": 9999, "pid": 2**22, "started_at": 0.0}
        (tmp_path / "deadcrew.json").write_text(json.dumps(dead_entry))

        broker = Broker()
        reg = InstanceRegistry(crew_id=broker.crew_id, port=0)
        ui = UIServer(broker, port=0, registry=reg)
        state = await ui._build_state()

        ids = {inst["id"] for inst in state["instances"]}
        assert "deadcrew" not in ids

    async def test_dead_pid_file_deleted_by_read_all(self, tmp_path, monkeypatch):
        """read_all() removes the dead entry's file from disk."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        dead_entry = {"crew_id": "deadcrew", "port": 9999, "pid": 2**22, "started_at": 0.0}
        (tmp_path / "deadcrew.json").write_text(json.dumps(dead_entry))

        broker = Broker()
        reg = InstanceRegistry(crew_id=broker.crew_id, port=0)
        ui = UIServer(broker, port=0, registry=reg)
        await ui._build_state()

        assert not (tmp_path / "deadcrew.json").exists()


# ── Sad path: unreachable remote ─────────────────────────────────────────────


class TestUnreachableRemote:
    async def test_unreachable_remote_shown_with_status_unreachable(self, tmp_path, monkeypatch):
        """A registry entry whose /api/state is unreachable shows status='unreachable'."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        # Write a registry entry pointing at a port nothing is listening on
        ghost_port = _free_port()
        ghost_entry = {"crew_id": "ghost", "port": ghost_port, "pid": os.getpid(), "started_at": time.time()}
        (tmp_path / "ghost.json").write_text(json.dumps(ghost_entry))

        broker = Broker()
        reg = InstanceRegistry(crew_id=broker.crew_id, port=0)
        ui = UIServer(broker, port=0, registry=reg)
        state = await ui._build_state()

        ghost_inst = next((i for i in state["instances"] if i["id"] == "ghost"), None)
        assert ghost_inst is not None
        assert ghost_inst["status"] == "unreachable"
        assert ghost_inst["agents"] == []

    async def test_unreachable_remote_does_not_affect_local(self, tmp_path, monkeypatch):
        """A failed remote fanout does not corrupt the local instance entry."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        ghost_port = _free_port()
        ghost_entry = {"crew_id": "ghost", "port": ghost_port, "pid": os.getpid(), "started_at": time.time()}
        (tmp_path / "ghost.json").write_text(json.dumps(ghost_entry))

        broker = Broker()
        await broker.spawn_teammate(role="builder", name="b", factory=_stub_factory)
        reg = InstanceRegistry(crew_id=broker.crew_id, port=0)
        ui = UIServer(broker, port=0, registry=reg)
        state = await ui._build_state()

        local = next(i for i in state["instances"] if i["id"] == broker.crew_id)
        assert local["is_local"] is True
        assert len(local["agents"]) == 1

    async def test_unreachable_remote_returns_empty_agents(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        ghost_port = _free_port()
        ghost_entry = {"crew_id": "ghost2", "port": ghost_port, "pid": os.getpid(), "started_at": time.time()}
        (tmp_path / "ghost2.json").write_text(json.dumps(ghost_entry))

        broker = Broker()
        reg = InstanceRegistry(crew_id=broker.crew_id, port=0)
        ui = UIServer(broker, port=0, registry=reg)
        state = await ui._build_state()

        ghost = next(i for i in state["instances"] if i["id"] == "ghost2")
        assert ghost["agents"] == []
        assert ghost["is_local"] is False


# ── Sad path: corrupt registry file ──────────────────────────────────────────


class TestCorruptRegistryFile:
    async def test_corrupt_file_does_not_crash_build_state(self, tmp_path, monkeypatch):
        """A corrupt JSON file in the registry is skipped without raising."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        (tmp_path / "corrupt.json").write_text("not json {{{")

        broker = Broker()
        reg = InstanceRegistry(crew_id=broker.crew_id, port=0)
        ui = UIServer(broker, port=0, registry=reg)
        state = await ui._build_state()

        # No crash; local instance still present
        assert len(state["instances"]) >= 1
        local = next(i for i in state["instances"] if i["id"] == broker.crew_id)
        assert local["is_local"] is True

    async def test_corrupt_file_deleted_after_build_state(self, tmp_path, monkeypatch):
        """The corrupt file is removed from disk during aggregation."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        (tmp_path / "corrupt.json").write_text("not json {{{")

        broker = Broker()
        reg = InstanceRegistry(crew_id=broker.crew_id, port=0)
        ui = UIServer(broker, port=0, registry=reg)
        await ui._build_state()

        assert not (tmp_path / "corrupt.json").exists()

    async def test_corrupt_file_crew_not_in_instances(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        (tmp_path / "corrupt.json").write_text("not json {{{")

        broker = Broker()
        reg = InstanceRegistry(crew_id=broker.crew_id, port=0)
        ui = UIServer(broker, port=0, registry=reg)
        state = await ui._build_state()

        ids = {i["id"] for i in state["instances"]}
        assert "corrupt" not in ids


# ── Sad path: startup race (remote returns empty instances list) ──────────────


class TestStartupRace:
    async def test_empty_instances_list_treated_as_unreachable(self, tmp_path, monkeypatch):
        """A remote /api/state returning instances=[] is treated as unreachable (SC-6)."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        # Simulate a remote that returns empty instances list
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        import uvicorn

        async def handle_state(request: Request) -> JSONResponse:
            return JSONResponse({"instances": [], "transcripts": {}})

        app = Starlette(routes=[Route("/api/state", handle_state)])

        port = _free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
        srv = uvicorn.Server(config)
        task = asyncio.create_task(srv.serve())
        await asyncio.sleep(0.5)

        # Write a registry entry pointing at this stub
        stub_entry = {"crew_id": "starting", "port": port, "pid": os.getpid(), "started_at": time.time()}
        (tmp_path / "starting.json").write_text(json.dumps(stub_entry))

        broker = Broker()
        reg = InstanceRegistry(crew_id=broker.crew_id, port=0)
        ui = UIServer(broker, port=0, registry=reg)
        state = await ui._build_state()

        starting_inst = next((i for i in state["instances"] if i["id"] == "starting"), None)
        # The instance MUST appear — the registry entry has a live PID (ours).
        # It must be shown as unreachable because the remote returned instances=[].
        assert starting_inst is not None, "startup-race instance should appear as unreachable, not be dropped"
        assert starting_inst["status"] == "unreachable"
        assert starting_inst["agents"] == []

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── Sad path: 2-second timeout bound (SC-9 / D6) ─────────────────────────────


class TestRemoteTimeout:
    async def test_slow_remote_does_not_block_beyond_timeout(self, tmp_path, monkeypatch):
        """A remote /api/state that hangs > 2s is marked unreachable; call completes fast."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))

        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        import uvicorn

        async def handle_slow(request: Request) -> JSONResponse:
            await asyncio.sleep(10)  # far exceeds the 2s timeout
            return JSONResponse({"instances": [], "transcripts": {}})

        app = Starlette(routes=[Route("/api/state", handle_slow)])
        port = _free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
        srv = uvicorn.Server(config)
        task = asyncio.create_task(srv.serve())
        await asyncio.sleep(0.5)

        slow_entry = {"crew_id": "slowcrew", "port": port, "pid": os.getpid(), "started_at": time.time()}
        (tmp_path / "slowcrew.json").write_text(json.dumps(slow_entry))

        broker = Broker()
        reg = InstanceRegistry(crew_id=broker.crew_id, port=0)
        ui = UIServer(broker, port=0, registry=reg)

        start = time.monotonic()
        state = await ui._build_state()
        elapsed = time.monotonic() - start

        # Must complete well under 5s (the 2s timeout + overhead)
        assert elapsed < 5.0, f"_build_state() took {elapsed:.1f}s — slow remote blocked the push cycle"

        slow_inst = next((i for i in state["instances"] if i["id"] == "slowcrew"), None)
        assert slow_inst is not None
        assert slow_inst["status"] == "unreachable"

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── Payload shape regression: no-registry path unchanged ─────────────────────


class TestNoRegistryPath:
    """With registry=None, _build_state() behaves exactly as before this feature."""

    async def test_single_instance_with_no_registry(self):
        broker = Broker()
        ui = UIServer(broker, port=0, registry=None)
        state = await ui._build_state()
        assert len(state["instances"]) == 1
        assert state["instances"][0]["id"] == broker.crew_id
        assert state["instances"][0]["is_local"] is True

    async def test_transcripts_keyed_correctly_with_no_registry(self):
        broker = Broker()
        ui = UIServer(broker, port=0, registry=None)
        state = await ui._build_state()
        assert broker.crew_id in state["transcripts"]

    async def test_no_registry_does_not_call_read_all(self, tmp_path, monkeypatch):
        """registry=None means no filesystem I/O at all."""
        monkeypatch.setenv("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", str(tmp_path))
        broker = Broker()
        ui = UIServer(broker, port=0, registry=None)
        state = await ui._build_state()
        # Registry dir never touched — no files created by _build_state
        assert list(tmp_path.iterdir()) == []
