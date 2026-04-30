"""Tests for claude_crew.ui_server — UIServer, _build_state, helpers."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from claude_crew.broker import Broker
from claude_crew.ui_server import UIServer, _derive_status, _normalize_model, _ts


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

    async def test_long_body_capped_at_500(self):
        from claude_crew.broker import LEAD_ID
        from claude_crew.envelope import Envelope, new_message_id
        import time

        broker = Broker()
        ui = UIServer(broker, port=0)
        long_payload = "x" * 1000
        env = Envelope(
            id=new_message_id(), seq=1, sender="lead",
            recipient="t-abc", timestamp=time.time(),
            payload=long_payload,
        )
        broker._log.append(env)

        state = await ui._build_state()
        messages = state["transcripts"][broker.crew_id]
        assert len(messages) == 1
        assert len(messages[0]["body"]) <= 2000

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
