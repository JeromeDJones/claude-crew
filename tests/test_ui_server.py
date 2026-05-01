"""Tests for claude_crew.ui_server — UIServer, _build_state, helpers."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import subprocess

from claude_crew.broker import Broker
from claude_crew.ui_server import (
    UIServer,
    _BRANCH_TTL_SECONDS,
    _derive_status,
    _normalize_model,
    _ts,
    _unreachable_instance,
)


# ── helpers ─────────────────────────────────────────────────────────────────

class TestNormalizeModel:
    def test_opus_in_id(self):
        assert _normalize_model("claude-opus-4-7") == "opus"

    def test_haiku_in_id(self):
        assert _normalize_model("claude-haiku-4-5-20251001") == "haiku"

    def test_sonnet_in_id(self):
        assert _normalize_model("claude-sonnet-4-6") == "sonnet"

    def test_none_falls_back_to_sonnet(self):
        assert _normalize_model(None) == "sonnet"

    def test_empty_string_falls_back_to_sonnet(self):
        assert _normalize_model("") == "sonnet"

    def test_unknown_id_falls_back_to_sonnet(self):
        assert _normalize_model("some-unknown-model-xyz") == "sonnet"

    def test_uppercase_opus(self):
        assert _normalize_model("CLAUDE-OPUS-4") == "opus"


class TestDeriveStatus:
    def test_tool_use_when_count_positive(self):
        snap = {"current_tool_count": 2, "current_turn_started_at_wallclock": None}
        assert _derive_status(snap) == "tool-use"

    def test_tool_use_takes_priority_over_active_turn(self):
        snap = {"current_tool_count": 1, "current_turn_started_at_wallclock": 1000.0}
        assert _derive_status(snap) == "tool-use"

    def test_thinking_when_turn_active_no_tools(self):
        snap = {"current_tool_count": 0, "current_turn_started_at_wallclock": 1000.0}
        assert _derive_status(snap) == "thinking"

    def test_idle_when_no_tools_no_turn(self):
        snap = {"current_tool_count": 0, "current_turn_started_at_wallclock": None}
        assert _derive_status(snap) == "idle"

    def test_idle_with_empty_snap(self):
        assert _derive_status({}) == "idle"


class TestTs:
    def test_ts_produces_iso_format(self):
        import re
        result = _ts(1746000000.0)
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.000Z", result)

    def test_ts_none_returns_current_time_string(self):
        import re
        result = _ts(None)
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.000Z", result)


# ── _build_state ─────────────────────────────────────────────────────────────

class TestBuildStateEmptyCrew:
    async def test_single_instance_in_result(self):
        broker = Broker()
        ui = UIServer(broker, port=0)
        state = await ui._build_state()
        assert len(state["instances"]) == 1

    async def test_instance_id_is_crew_id(self):
        broker = Broker()
        ui = UIServer(broker, port=0)
        state = await ui._build_state()
        assert state["instances"][0]["id"] == broker.crew_id

    async def test_agents_empty_when_no_teammates(self):
        broker = Broker()
        ui = UIServer(broker, port=0)
        state = await ui._build_state()
        assert state["instances"][0]["agents"] == []

    async def test_status_idle_when_no_agents(self):
        broker = Broker()
        ui = UIServer(broker, port=0)
        state = await ui._build_state()
        assert state["instances"][0]["status"] == "idle"

    async def test_uptime_zero_when_no_teammates(self):
        broker = Broker()
        ui = UIServer(broker, port=0)
        state = await ui._build_state()
        assert state["instances"][0]["uptime"] == 0

    async def test_transcripts_keyed_by_crew_id(self):
        broker = Broker()
        ui = UIServer(broker, port=0)
        state = await ui._build_state()
        assert broker.crew_id in state["transcripts"]

    async def test_transcript_empty_when_no_messages(self):
        broker = Broker()
        ui = UIServer(broker, port=0)
        state = await ui._build_state()
        assert state["transcripts"][broker.crew_id] == []

    async def test_local_instance_has_is_local_true(self):
        broker = Broker()
        ui = UIServer(broker, port=0)
        state = await ui._build_state()
        assert state["instances"][0]["is_local"] is True


def _stub_factory(id: str, name: str, role: str, **_kwargs):
    from claude_crew.teammate import StubTeammate
    return StubTeammate(id=id, name=name, role=role)


class TestBuildStateWithTeammates:
    """Spawn real StubTeamates and verify _build_state output."""

    @pytest.fixture
    async def broker_with_teammates(self):
        broker = Broker()
        await broker.spawn_teammate(
            role="builder",
            name="builder",
            factory=_stub_factory,
        )
        await broker.spawn_teammate(
            role="reviewer",
            name="reviewer",
            factory=_stub_factory,
        )
        yield broker
        await broker.shutdown_all()

    async def test_agents_count_matches_alive_teammates(self, broker_with_teammates):
        broker = broker_with_teammates
        ui = UIServer(broker, port=0)
        state = await ui._build_state()
        assert len(state["instances"][0]["agents"]) == 2

    async def test_agent_has_required_fields(self, broker_with_teammates):
        broker = broker_with_teammates
        ui = UIServer(broker, port=0)
        state = await ui._build_state()
        agent = state["instances"][0]["agents"][0]
        for field in ("id", "role", "model", "status", "uptime", "lastMsg", "cost", "tokens", "tools", "current_tool"):
            assert field in agent, f"missing field: {field}"

    async def test_status_active_when_agents_present(self, broker_with_teammates):
        broker = broker_with_teammates
        ui = UIServer(broker, port=0)
        state = await ui._build_state()
        assert state["instances"][0]["status"] == "active"

    async def test_dead_teammate_excluded(self, broker_with_teammates):
        broker = broker_with_teammates
        ui = UIServer(broker, port=0)
        # Get all alive IDs, then kill one
        alive_ids = [info.id for info in broker._info.values() if info.alive]
        kill_id = alive_ids[0]
        await broker.kill_teammate(kill_id)
        state = await ui._build_state()
        agent_ids = [a["id"] for a in state["instances"][0]["agents"]]
        assert kill_id not in agent_ids

    async def test_agent_roles_match(self, broker_with_teammates):
        broker = broker_with_teammates
        ui = UIServer(broker, port=0)
        state = await ui._build_state()
        roles = {a["role"] for a in state["instances"][0]["agents"]}
        assert roles == {"builder", "reviewer"}


class TestBuildStateTranscript:
    async def test_error_envelopes_excluded(self):
        """Envelopes with error payloads should not appear in the transcript."""
        from claude_crew.broker import LEAD_ID
        from claude_crew.envelope import Envelope, new_message_id
        import time

        broker = Broker()
        ui = UIServer(broker, port=0)
        # Manually inject an error envelope into the log
        err_env = Envelope(
            id=new_message_id(), seq=1, sender="t-abc",
            recipient=LEAD_ID, timestamp=time.time(),
            payload={"error": "teammate_dead", "message": "dead"},
        )
        broker._log.append(err_env)

        state = await ui._build_state()
        messages = state["transcripts"][broker.crew_id]
        bodies = [m["body"] for m in messages]
        assert not any("teammate_dead" in b for b in bodies)

    async def test_long_body_capped_at_10000(self):
        from claude_crew.broker import LEAD_ID
        from claude_crew.envelope import Envelope, new_message_id
        import time

        broker = Broker()
        ui = UIServer(broker, port=0)
        long_payload = "x" * 50000  # well above the 10000 cap
        env = Envelope(
            id=new_message_id(), seq=1, sender="lead",
            recipient="t-abc", timestamp=time.time(),
            payload=long_payload,
        )
        broker._log.append(env)

        state = await ui._build_state()
        messages = state["transcripts"][broker.crew_id]
        assert len(messages) == 1
        assert len(messages[0]["body"]) == 10000

    async def test_dict_payload_text_extracted(self):
        """SDK agent responses with {"text": ..., "from": ...} render as plain text."""
        from claude_crew.broker import LEAD_ID
        from claude_crew.envelope import Envelope, new_message_id
        import time

        broker = Broker()
        ui = UIServer(broker, port=0)
        env = Envelope(
            id=new_message_id(), seq=1, sender="t-abc",
            recipient=LEAD_ID, timestamp=time.time(),
            payload={"text": "Hello from agent", "from": "reviewer"},
        )
        broker._log.append(env)

        state = await ui._build_state()
        messages = state["transcripts"][broker.crew_id]
        assert len(messages) == 1
        assert messages[0]["body"] == "Hello from agent"

    async def test_dict_payload_without_text_falls_back_to_json(self):
        from claude_crew.envelope import Envelope, new_message_id
        import time

        broker = Broker()
        ui = UIServer(broker, port=0)
        env = Envelope(
            id=new_message_id(), seq=1, sender="lead",
            recipient="t-abc", timestamp=time.time(),
            payload={"some": "other", "structure": 1},
        )
        broker._log.append(env)

        state = await ui._build_state()
        messages = state["transcripts"][broker.crew_id]
        assert len(messages) == 1
        assert '"some"' in messages[0]["body"]

    async def test_transcript_capped_at_200_messages(self):
        from claude_crew.envelope import Envelope, new_message_id
        import time

        broker = Broker()
        ui = UIServer(broker, port=0)
        for i in range(250):
            env = Envelope(
                id=new_message_id(), seq=i, sender="lead",
                recipient="t-abc", timestamp=time.time(),
                payload=f"msg-{i}",
            )
            broker._log.append(env)

        state = await ui._build_state()
        messages = state["transcripts"][broker.crew_id]
        assert len(messages) <= 200


# ── HTTP endpoints ────────────────────────────────────────────────────────────

class TestHttpEndpoints:
    @pytest.fixture
    def client(self):
        broker = Broker()
        ui = UIServer(broker, port=0)
        app = ui._make_app()
        with TestClient(app) as c:
            yield c

    def test_root_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_root_content_type_html(self, client):
        resp = client.get("/")
        assert "text/html" in resp.headers["content-type"]

    def test_root_contains_claude_crew(self, client):
        resp = client.get("/")
        assert "claude-crew" in resp.text.lower()

    def test_state_returns_200(self, client):
        resp = client.get("/api/state")
        assert resp.status_code == 200

    def test_state_content_type_json(self, client):
        resp = client.get("/api/state")
        assert "application/json" in resp.headers["content-type"]

    def test_state_has_instances_key(self, client):
        resp = client.get("/api/state")
        data = resp.json()
        assert "instances" in data
        assert "transcripts" in data

    def test_state_single_instance(self, client):
        resp = client.get("/api/state")
        data = resp.json()
        assert len(data["instances"]) == 1


class TestWebSocket:
    @pytest.fixture
    def client(self):
        broker = Broker()
        ui = UIServer(broker, port=0)
        app = ui._make_app()
        with TestClient(app) as c:
            yield c

    def test_ws_sends_state_message(self, client):
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "state"
            assert "data" in msg
            assert "instances" in msg["data"]

    def test_ws_state_has_correct_shape(self, client):
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            data = msg["data"]
            assert isinstance(data["instances"], list)
            assert isinstance(data["transcripts"], dict)

    def test_ws_disconnect_does_not_crash_server(self, client):
        """After a client disconnects, subsequent HTTP requests still work."""
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            # disconnect (context manager exit closes the socket)

        # Server should still respond to HTTP
        resp = client.get("/api/state")
        assert resp.status_code == 200


class TestBuildStateTokenCost:
    """T4 — token/cost wired from snap into dashboard payload."""

    @pytest.fixture
    def monkeypatch(self):
        import pytest
        mp = pytest.MonkeyPatch()
        yield mp
        mp.undo()

    async def _spawn_sdk_teammate(self, broker, monkeypatch, scripted_responses):
        from claude_crew import sdk_teammate as sdk_module
        from claude_crew.sdk_teammate import SdkTeammate
        from tests.fakes.sdk import FakeSDKClient

        fake = FakeSDKClient(scripted_responses=scripted_responses)

        def _ctor(options=None):
            fake.options = options
            return fake

        monkeypatch.setattr(sdk_module, "ClaudeSDKClient", _ctor)

        def _factory(id, name, role, **_kwargs):
            return SdkTeammate(id=id, name=name, role=role)

        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory)
        return tid, fake

    async def _wait_for_lead(self, broker, count, timeout=3.0):
        import asyncio
        from claude_crew.broker import LEAD_ID
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if len(broker.get_messages(recipient=LEAD_ID)) >= count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(
            f"timed out waiting for {count} lead messages; "
            f"got {len(broker.get_messages(recipient=LEAD_ID))}"
        )

    async def test_per_agent_cost_reads_from_snap(self, monkeypatch):
        from claude_crew.broker import LEAD_ID, Broker
        from claude_crew.envelope import Envelope, new_message_id
        from tests.fakes.sdk import text_response_with_usage

        broker = Broker()
        try:
            tid, fake = await self._spawn_sdk_teammate(
                broker,
                monkeypatch,
                scripted_responses=[
                    text_response_with_usage(
                        "hello",
                        cumulative_input_tokens=200,
                        cumulative_output_tokens=100,
                        cumulative_cost_usd=0.15,
                    )
                ],
            )
            await broker.send(Envelope(
                id=new_message_id(), seq=0,
                sender=LEAD_ID, recipient=tid, timestamp=0.0,
                payload="hi",
            ))
            await self._wait_for_lead(broker, 1)

            ui = UIServer(broker, port=0)
            instance, _ = ui._build_local_instance(broker.snapshot(log_limit=200))

            assert len(instance["agents"]) == 1
            agent = instance["agents"][0]
            assert agent["cost"] == 0.15
            assert agent["tokens"]["in"] == 200
            assert agent["tokens"]["out"] == 100
        finally:
            await broker.shutdown_all()

    async def test_instance_summary_includes_tombstoned_teammate_cost(self, monkeypatch):
        from claude_crew.broker import LEAD_ID, Broker
        from claude_crew.envelope import Envelope, new_message_id
        from tests.fakes.sdk import text_response_with_usage

        broker = Broker()
        try:
            tid_a, _ = await self._spawn_sdk_teammate(
                broker,
                monkeypatch,
                scripted_responses=[
                    text_response_with_usage(
                        "a",
                        cumulative_input_tokens=100,
                        cumulative_output_tokens=50,
                        cumulative_cost_usd=0.30,
                    )
                ],
            )
            await broker.send(Envelope(
                id=new_message_id(), seq=0,
                sender=LEAD_ID, recipient=tid_a, timestamp=0.0,
                payload="hi",
            ))
            await self._wait_for_lead(broker, 1)

            tid_b, _ = await self._spawn_sdk_teammate(
                broker,
                monkeypatch,
                scripted_responses=[
                    text_response_with_usage(
                        "b",
                        cumulative_input_tokens=200,
                        cumulative_output_tokens=80,
                        cumulative_cost_usd=1.20,
                    )
                ],
            )
            await broker.send(Envelope(
                id=new_message_id(), seq=1,
                sender=LEAD_ID, recipient=tid_b, timestamp=0.0,
                payload="hi",
            ))
            await self._wait_for_lead(broker, 2)

            # Kill B — it should contribute via tombstone
            await broker.kill_teammate(tid_b)

            ui = UIServer(broker, port=0)
            instance, _ = ui._build_local_instance(broker.snapshot(log_limit=200))

            # Agents array: only A (alive), B excluded (D-10)
            agent_ids = [a["id"] for a in instance["agents"]]
            assert tid_b not in agent_ids
            assert len(instance["agents"]) == 1

            # Instance aggregate: A ($0.30) + B tombstone ($1.20) = $1.50
            assert abs(instance["cost"] - 1.50) < 1e-9, (
                f"expected 1.50, got {instance['cost']}"
            )
        finally:
            await broker.shutdown_all()

    async def test_respawn_with_tombstone_present_aggregates_both(self, monkeypatch):
        from claude_crew.broker import LEAD_ID, Broker
        from claude_crew.envelope import Envelope, new_message_id
        from tests.fakes.sdk import text_response_with_usage

        broker = Broker()
        try:
            # First instance: $1.20, then killed
            tid1, _ = await self._spawn_sdk_teammate(
                broker,
                monkeypatch,
                scripted_responses=[
                    text_response_with_usage(
                        "first",
                        cumulative_input_tokens=300,
                        cumulative_output_tokens=100,
                        cumulative_cost_usd=1.20,
                    )
                ],
            )
            await broker.send(Envelope(
                id=new_message_id(), seq=0,
                sender=LEAD_ID, recipient=tid1, timestamp=0.0,
                payload="hi",
            ))
            await self._wait_for_lead(broker, 1)
            await broker.kill_teammate(tid1)

            # Second instance (different UUID, same role): $0.05
            tid2, _ = await self._spawn_sdk_teammate(
                broker,
                monkeypatch,
                scripted_responses=[
                    text_response_with_usage(
                        "second",
                        cumulative_input_tokens=50,
                        cumulative_output_tokens=20,
                        cumulative_cost_usd=0.05,
                    )
                ],
            )
            await broker.send(Envelope(
                id=new_message_id(), seq=1,
                sender=LEAD_ID, recipient=tid2, timestamp=0.0,
                payload="hi",
            ))
            await self._wait_for_lead(broker, 2)

            ui = UIServer(broker, port=0)
            instance, _ = ui._build_local_instance(broker.snapshot(log_limit=200))

            # Only the alive instance in agents array
            assert len(instance["agents"]) == 1
            assert instance["agents"][0]["id"] == tid2

            # Aggregate: tombstone $1.20 + alive $0.05 = $1.25
            assert abs(instance["cost"] - 1.25) < 1e-9, (
                f"expected 1.25, got {instance['cost']}"
            )
        finally:
            await broker.shutdown_all()

    async def test_empty_crew_aggregate_is_zero(self):
        broker = Broker()
        ui = UIServer(broker, port=0)
        instance, _ = ui._build_local_instance(broker.snapshot(log_limit=200))
        assert instance["cost"] == 0.0
        assert instance["tokens"] == {"in": 0, "out": 0}


class TestBindUiSocket:
    """Tests for _bind_ui_socket — the race-free port reservation helper."""

    def test_returns_open_socket_on_success(self):
        from claude_crew.server import _bind_ui_socket

        sock = _bind_ui_socket(0)
        assert sock is not None
        try:
            port = sock.getsockname()[1]
            assert port > 0
        finally:
            sock.close()

    def test_socket_holds_the_port(self):
        """Port must remain bound while the socket is open (the whole point of this helper)."""
        import socket as _socket

        from claude_crew.server import _bind_ui_socket

        sock = _bind_ui_socket(0)
        assert sock is not None
        port = sock.getsockname()[1]
        try:
            # While sock is open, no other process can bind the same port
            probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            try:
                probe.bind(("127.0.0.1", port))
                assert False, "second bind should have failed while socket is open"
            except OSError:
                pass
            finally:
                probe.close()
        finally:
            sock.close()

    def test_falls_back_to_ephemeral_when_preferred_busy(self):
        """If preferred port is taken, returns a socket on an ephemeral port."""
        import socket as _socket

        from claude_crew.server import _bind_ui_socket

        # Hold the preferred port
        blocker = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        preferred = blocker.getsockname()[1]
        try:
            sock = _bind_ui_socket(preferred)
            assert sock is not None
            try:
                got_port = sock.getsockname()[1]
                assert got_port != preferred
                assert got_port > 0
            finally:
                sock.close()
        finally:
            blocker.close()

    def test_two_concurrent_callers_get_different_ports(self):
        """Simulates the race: both callers ask for the same preferred port; each gets a unique port."""
        from claude_crew.server import _bind_ui_socket

        sock_a = _bind_ui_socket(0)
        assert sock_a is not None
        port_a = sock_a.getsockname()[1]

        # With sock_a still open, try the same port
        sock_b = _bind_ui_socket(port_a)
        assert sock_b is not None
        port_b = sock_b.getsockname()[1]

        try:
            assert port_a != port_b, "concurrent callers must get different ports"
        finally:
            sock_a.close()
            sock_b.close()


# ── T2: BrokerSnapshot decoupling ────────────────────────────────────────────

class TestUIServerBrokerDecoupling:
    """SC-2, SC-10: _build_local_instance accepts a BrokerSnapshot, reads no private attrs."""

    def _make_snapshot(
        self,
        *,
        crew_id: str = "crew-test",
        alive_cost: float = 0.0,
        alive_in: int = 0,
        alive_out: int = 0,
        dead_cost: float = 0.0,
        dead_in: int = 0,
        dead_out: int = 0,
        log_count: int = 0,
    ):
        """Build a hand-rolled BrokerSnapshot for use without a live Broker."""
        import time as _time
        from claude_crew.broker import BrokerSnapshot, LiveTeammateInfo, TeammateInfo
        from claude_crew.envelope import Envelope, new_message_id

        now = _time.time()
        info_alive = TeammateInfo(
            id="t-1", name="alice", role="builder",
            spawned_at=now - 100, alive=True,
        )
        info_dead = TeammateInfo(
            id="t-2", name="bob", role="reviewer",
            spawned_at=now - 200, alive=False,
            total_cost_usd_at_death=dead_cost,
            total_input_tokens_at_death=dead_in,
            total_output_tokens_at_death=dead_out,
        )
        live_entry = LiveTeammateInfo(
            info=info_alive,
            status={
                "current_tool_count": 0,
                "current_turn_started_at_wallclock": None,
                "total_input_tokens": alive_in,
                "total_output_tokens": alive_out,
                "total_cost_usd": alive_cost,
                "current_tools": [],
                "current_tool": None,
                "last_activity_at_wallclock": None,
            },
            model="claude-sonnet-4-6",
        )
        log_entries = tuple(
            Envelope(
                id=new_message_id(), seq=i, sender="lead",
                recipient="t-1", timestamp=now,
                payload=f"msg-{i}",
            )
            for i in range(log_count)
        )
        return BrokerSnapshot(
            crew_id=crew_id,
            teammates=(info_alive, info_dead),
            live=(live_entry,),
            log=log_entries,
        )

    def test_build_state_from_synthetic_snapshot(self):
        """SC-10: _build_local_instance works with a synthetic snapshot and no live broker."""
        broker = Broker()
        ui = UIServer(broker=broker, port=0)

        snap = self._make_snapshot(
            crew_id="crew-test",
            alive_cost=0.25,
            alive_in=100,
            alive_out=50,
            dead_cost=0.10,
            dead_in=10,
            dead_out=5,
            log_count=5,
        )

        instance, messages = ui._build_local_instance(snap)

        # Alive teammate in agents array; dead excluded (D-10)
        assert len(instance["agents"]) == 1
        assert instance["agents"][0]["id"] == "t-1"
        assert instance["agents"][0]["cost"] == 0.25
        assert instance["agents"][0]["tokens"] == {"in": 100, "out": 50}

        # Instance-level aggregate: alive + dead (F14 preserved)
        assert abs(instance["cost"] - 0.35) < 1e-9, f"expected 0.35, got {instance['cost']}"
        assert instance["tokens"] == {"in": 110, "out": 55}

        # crew_id sourced from snapshot, not self._broker
        assert instance["id"] == "crew-test"

        # 5 plain-text envelopes → 5 messages
        assert len(messages) == 5

    def test_ui_server_no_broker_private_attr_reads_in_production(self):
        """SC-2: production paths in ui_server.py read zero broker/teammate private attrs.

        The regex `(broker|teammate)\\._\\w+` matches patterns like `broker._info`,
        `teammate._model`, etc. It does NOT match `self._broker` because `self._broker`
        contains no `.<underscore>` sequence on a broker/teammate variable.
        Comment lines are excluded from the check.
        """
        import subprocess
        from pathlib import Path

        repo_root = Path(__file__).parent.parent
        ui_path = repo_root / "claude_crew" / "ui_server.py"

        result = subprocess.run(
            ["grep", "-En", r"(broker|teammate)\._\w+", str(ui_path)],
            capture_output=True, text=True,
        )
        # Filter out comment lines (grep returns the line content after "lineno:")
        production_matches = [
            line for line in result.stdout.splitlines()
            if line.strip() and not line.split(":", 2)[-1].lstrip().startswith("#")
        ]
        assert production_matches == [], (
            f"Found private-attr reads on broker/teammate in ui_server.py:\n"
            + "\n".join(production_matches)
        )

        # Also verify crew_id not accessed directly on broker (must come from snapshot)
        result2 = subprocess.run(
            ["grep", "-En", r"broker\.crew_id", str(ui_path)],
            capture_output=True, text=True,
        )
        crew_id_matches = [
            line for line in result2.stdout.splitlines()
            if line.strip() and not line.split(":", 2)[-1].lstrip().startswith("#")
        ]
        assert crew_id_matches == [], (
            f"Found broker.crew_id reads in ui_server.py:\n"
            + "\n".join(crew_id_matches)
        )

    async def test_dashboard_payload_shape_unchanged_post_refactor(self):
        """Regression guard: top-level key set of dashboard payload is preserved after T2 refactor."""
        from claude_crew.broker import Broker

        broker = Broker()
        ui = UIServer(broker=broker, port=0)

        state = await ui._build_state()

        # Top-level keys
        assert set(state.keys()) >= {"instances", "transcripts"}
        assert isinstance(state["instances"], list)
        assert isinstance(state["transcripts"], dict)

        # Local instance key shape
        instance = state["instances"][0]
        required_instance_keys = {
            "id", "is_local", "label", "cwd", "branch",
            "uptime", "status", "cost", "tokens", "agents",
        }
        assert required_instance_keys <= set(instance.keys()), (
            f"Missing instance keys: {required_instance_keys - set(instance.keys())}"
        )


# ── T3: git branch detection ─────────────────────────────────────────────────

class TestBranchDetection:
    def test_branch_detection_succeeds_in_real_git_repo(self, tmp_path):
        """_get_branch returns the actual branch name when cwd is a git repo."""
        # Init a repo and create a commit so --show-current works reliably
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path, check=True, capture_output=True,
        )
        # Create a commit so the branch name is stable
        (tmp_path / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=tmp_path, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "checkout", "-b", "feat-foo"],
            cwd=tmp_path, check=True, capture_output=True,
        )

        broker = Broker()
        ui = UIServer(broker=broker, port=0, cwd=str(tmp_path))
        assert ui._get_branch() == "feat-foo"

    def test_branch_detection_falls_back_to_main_in_non_git_dir(self, tmp_path):
        """_get_branch returns 'main' and does not raise when cwd is not a git repo."""
        broker = Broker()
        ui = UIServer(broker=broker, port=0, cwd=str(tmp_path))
        result = ui._get_branch()
        assert result == "main"

    def test_branch_cache_ttl_honors_window(self, monkeypatch):
        """Cached branch is returned within TTL; refreshed after expiry."""
        calls = ["alpha", "beta"]

        def fake_detect(cwd: str):
            return calls.pop(0) if calls else "gamma"

        monkeypatch.setattr("claude_crew.ui_server._detect_branch", fake_detect)

        broker = Broker()
        ui = UIServer(broker=broker, port=0, cwd="/fake")
        assert ui._get_branch() == "alpha"
        assert ui._get_branch() == "alpha"  # still cached

        # Expire the cache
        cache_value, cache_expiry = ui._branch_cache
        ui._branch_cache = (cache_value, cache_expiry - _BRANCH_TTL_SECONDS - 1)
        assert ui._get_branch() == "beta"

    def test_branch_subprocess_timeout_falls_back_to_main(self, monkeypatch):
        """TimeoutExpired from subprocess.run is caught by _detect_branch → returns None → 'main'."""
        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=["git"], timeout=2.0)

        monkeypatch.setattr("claude_crew.ui_server.subprocess.run", fake_run)

        broker = Broker()
        ui = UIServer(broker=broker, port=0, cwd="/fake")
        result = ui._get_branch()
        assert result == "main"

    def test_unreachable_instance_branch_unchanged(self):
        """_unreachable_instance always returns branch='main' (SC-12)."""
        result = _unreachable_instance("crew-x")
        assert result["branch"] == "main"
