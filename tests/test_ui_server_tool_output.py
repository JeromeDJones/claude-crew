"""Tests for the /tool-output route and _build_state tool_use_id/crew_id fields.

Covers AT-5, AT-6, AT-9 from the click-to-view-tool-output spec, plus the
multi-instance leader→follower proxy (fix/tool-output-multi-instance-proxy):
the dashboard is a leader that aggregates remote instances, so /tool-output is
crew-aware and proxies to the owning instance.
"""

from __future__ import annotations

import time

import httpx
import pytest

from claude_crew.broker import Broker
from claude_crew.redaction import _TOOL_OUTPUT_BYTE_CAP as CAP
from claude_crew.teammate import StubTeammate, ToolEvent
from claude_crew.ui_server import UIServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ui_server() -> tuple[Broker, UIServer]:
    broker = Broker()
    ui = UIServer(broker, port=0)
    return broker, ui


def _client(ui: UIServer) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ui._make_app()),
        base_url="http://testserver",
    )


def _seed_tool_output(broker: Broker, teammate_id: str, tool_use_id: str, body: str) -> None:
    """Directly seed a tool output on a teammate in the broker's registry."""
    tm = broker._teammates.get(teammate_id) or broker._dead_teammates.get(teammate_id)
    assert tm is not None, f"teammate {teammate_id!r} not found in broker"
    tm.store_tool_output(tool_use_id, body)


async def _spawn_stub(broker: Broker) -> str:
    """Spawn a StubTeammate and return its assigned id."""

    def _factory(id: str, name: str, role: str, **_kwargs) -> StubTeammate:
        return StubTeammate(id=id, name=name, role=role)

    return await broker.spawn_teammate(role="builder", name="builder", factory=_factory)


class _FakeRegistry:
    """Minimal InstanceRegistry stand-in returning fixed entries."""

    def __init__(self, entries: list[dict]) -> None:
        self._entries = entries

    def read_all(self) -> list[dict]:
        return self._entries


class _FakeResp:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _FakeHttpClient:
    """Captures proxied GET URLs; returns a canned response or raises."""

    def __init__(self, resp: _FakeResp | None = None, raise_exc: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._resp = resp
        self._raise = raise_exc

    async def get(self, url: str) -> _FakeResp:
        self.calls.append(url)
        if self._raise is not None:
            raise self._raise
        assert self._resp is not None
        return self._resp


# ---------------------------------------------------------------------------
# AT-5: GET /tool-output/<crew>/<teammate>/<tool_use_id> → 200 with stored body
# ---------------------------------------------------------------------------


class TestAT5ToolOutputHit:
    async def test_200_with_stored_body(self) -> None:
        broker, ui = _make_ui_server()
        tm_id = await _spawn_stub(broker)
        _seed_tool_output(broker, tm_id, "toolu_abc", "hello world")

        async with _client(ui) as client:
            resp = await client.get(f"/tool-output/{broker.crew_id}/{tm_id}/toolu_abc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["body"] == "hello world"
        assert data["truncated"] is False
        assert data["redaction_version"] == "v1"

    async def test_200_body_matches_stored_content(self) -> None:
        broker, ui = _make_ui_server()
        tm_id = await _spawn_stub(broker)
        _seed_tool_output(broker, tm_id, "toolu_xyz", "file contents here")

        async with _client(ui) as client:
            resp = await client.get(f"/tool-output/{broker.crew_id}/{tm_id}/toolu_xyz")

        assert resp.status_code == 200
        assert resp.json()["body"] == "file contents here"

    async def test_truncated_true_when_body_at_cap(self) -> None:
        broker, ui = _make_ui_server()
        tm_id = await _spawn_stub(broker)

        capped = "x" * (CAP - 3) + "…"  # exactly CAP bytes (… is 3 UTF-8 bytes)
        assert len(capped.encode("utf-8")) == CAP
        broker._teammates[tm_id].store_tool_output("toolu_big", capped)

        async with _client(ui) as client:
            resp = await client.get(f"/tool-output/{broker.crew_id}/{tm_id}/toolu_big")

        assert resp.status_code == 200
        assert resp.json()["truncated"] is True


# ---------------------------------------------------------------------------
# AT-6: 404 on unknown, 400 on bad path param
# ---------------------------------------------------------------------------


class TestAT6MissAndValidation:
    async def test_404_unknown_teammate(self) -> None:
        broker, ui = _make_ui_server()
        async with _client(ui) as client:
            resp = await client.get(f"/tool-output/{broker.crew_id}/unknown/unknown")

        assert resp.status_code == 404
        assert resp.json() == {"error": "not_found"}

    async def test_404_known_teammate_evicted_key(self) -> None:
        broker, ui = _make_ui_server()
        tm_id = await _spawn_stub(broker)
        async with _client(ui) as client:
            resp = await client.get(f"/tool-output/{broker.crew_id}/{tm_id}/toolu_nothere")

        assert resp.status_code == 404
        assert resp.json() == {"error": "not_found"}

    async def test_400_invalid_crew_id(self) -> None:
        broker, ui = _make_ui_server()
        async with _client(ui) as client:
            resp = await client.get("/tool-output/bad..crew/valid-id/toolu_x")
        assert resp.status_code == 400
        assert resp.json()["param"] == "crew_id"

    async def test_400_invalid_teammate_id(self) -> None:
        broker, ui = _make_ui_server()
        async with _client(ui) as client:
            resp = await client.get(f"/tool-output/{broker.crew_id}/bad..id/toolu_x")
        assert resp.status_code == 400
        assert resp.json()["param"] == "teammate_id"

    async def test_slash_in_tool_use_id_not_routed(self) -> None:
        broker, ui = _make_ui_server()
        async with _client(ui) as client:
            resp = await client.get(f"/tool-output/{broker.crew_id}/valid-id/bad/slash")
        # 4th slash segment → route (3 params) won't match → 404. Path traversal
        # via slash is blocked at the routing layer.
        assert resp.status_code in (400, 404)

    async def test_400_space_in_teammate_id(self) -> None:
        broker, ui = _make_ui_server()
        async with _client(ui) as client:
            resp = await client.get(f"/tool-output/{broker.crew_id}/bad%20id/toolu_x")
        assert resp.status_code == 400

    async def test_500_on_broker_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        broker, ui = _make_ui_server()

        def _boom(tid: str, uid: str) -> str:
            raise RuntimeError("unexpected!")

        monkeypatch.setattr(broker, "get_tool_output", _boom)

        async with _client(ui) as client:
            resp = await client.get(f"/tool-output/{broker.crew_id}/valid-id/toolu_abc")

        assert resp.status_code == 500
        assert resp.json() == {"error": "internal_error"}


# ---------------------------------------------------------------------------
# Multi-instance: leader proxies /tool-output to the owning follower instance
# ---------------------------------------------------------------------------


class TestMultiInstanceProxy:
    async def test_proxy_to_remote_crew_passes_through(self) -> None:
        """Remote crew → leader looks up the port in the registry and proxies."""
        broker, ui = _make_ui_server()
        ui._registry = _FakeRegistry([{"crew_id": "followerxyz", "port": 9999}])
        ui._http_client = _FakeHttpClient(
            resp=_FakeResp(200, {"body": "remote out", "truncated": False, "redaction_version": "v1"})
        )

        async with _client(ui) as client:
            resp = await client.get("/tool-output/followerxyz/t-remote/toolu_r")

        assert resp.status_code == 200
        assert resp.json()["body"] == "remote out"
        # Proxied to the follower with the SAME crew_id (follower serves local).
        assert ui._http_client.calls == [
            "http://127.0.0.1:9999/tool-output/followerxyz/t-remote/toolu_r"
        ]

    async def test_proxy_passes_through_remote_404(self) -> None:
        broker, ui = _make_ui_server()
        ui._registry = _FakeRegistry([{"crew_id": "followerxyz", "port": 9999}])
        ui._http_client = _FakeHttpClient(resp=_FakeResp(404, {"error": "not_found"}))

        async with _client(ui) as client:
            resp = await client.get("/tool-output/followerxyz/t-remote/toolu_gone")

        assert resp.status_code == 404
        assert resp.json() == {"error": "not_found"}

    async def test_unknown_crew_not_in_registry_404(self) -> None:
        broker, ui = _make_ui_server()
        ui._registry = _FakeRegistry([])  # crew not registered

        async with _client(ui) as client:
            resp = await client.get("/tool-output/ghostcrew/t-x/toolu_y")

        assert resp.status_code == 404
        assert resp.json() == {"error": "not_found"}

    async def test_no_registry_remote_crew_404(self) -> None:
        broker, ui = _make_ui_server()  # registry is None
        async with _client(ui) as client:
            resp = await client.get("/tool-output/othercrew/t-x/toolu_y")
        assert resp.status_code == 404

    @pytest.mark.parametrize("bad_port", ["9999/../etc", "abc", 0, 70000, -1, True, None])
    async def test_invalid_registry_port_returns_404(self, bad_port: object) -> None:
        """H-1: a corrupt registry port (non-int / out-of-range / bool) must not
        reach URL construction — return 404, never build a malformed proxy URL."""
        broker, ui = _make_ui_server()
        ui._registry = _FakeRegistry([{"crew_id": "followerxyz", "port": bad_port}])
        # If the guard fails, the proxy would call out; make that observable.
        ui._http_client = _FakeHttpClient(raise_exc=AssertionError("should not proxy"))

        async with _client(ui) as client:
            resp = await client.get("/tool-output/followerxyz/t-remote/toolu_r")

        assert resp.status_code == 404
        assert ui._http_client.calls == []  # never attempted the proxy

    async def test_proxy_failure_returns_502(self) -> None:
        broker, ui = _make_ui_server()
        ui._registry = _FakeRegistry([{"crew_id": "followerxyz", "port": 9999}])
        ui._http_client = _FakeHttpClient(raise_exc=httpx.ConnectError("refused"))

        async with _client(ui) as client:
            resp = await client.get("/tool-output/followerxyz/t-remote/toolu_r")

        assert resp.status_code == 502
        assert resp.json() == {"error": "bad_gateway"}


# ---------------------------------------------------------------------------
# AT-9: _build_state produces kind:"tool" entries with tool_use_id + crew_id
# ---------------------------------------------------------------------------


class TestAT9ToolUseIdInState:
    async def test_tool_event_includes_tool_use_id_and_crew_id(self) -> None:
        from claude_crew.broker import BrokerSnapshot

        broker, ui = _make_ui_server()
        tool_ev = ToolEvent(
            teammate_id="t-1",
            tool_name="Read",
            tool_use_id="toolu_abc",
            started_at_wallclock=time.time() - 1.0,
            finished_at_wallclock=time.time(),
            duration_seconds=1.0,
            outcome="ok",
            args_summary="",
            error_summary=None,
            redaction_version="v1",
        )
        snapshot = BrokerSnapshot(
            crew_id=broker.crew_id,
            teammates=[],
            live=[],
            log=[],
            tool_events=[tool_ev],
            startup_diagnostics=[],
            dead_configs={},
        )

        _, messages = ui._build_local_instance(snapshot)
        tool_msgs = [m for m in messages if m.get("kind") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_use_id"] == "toolu_abc"
        # crew_id lets the dashboard route the fetch to the owning instance.
        assert tool_msgs[0]["crew_id"] == broker.crew_id

    async def test_build_state_tool_event_has_ids(self) -> None:
        broker, ui = _make_ui_server()
        tm_id = await _spawn_stub(broker)

        tm = broker._teammates[tm_id]
        tool_ev = ToolEvent(
            teammate_id=tm_id,
            tool_name="Bash",
            tool_use_id="toolu_abc",
            started_at_wallclock=time.time() - 0.5,
            finished_at_wallclock=time.time(),
            duration_seconds=0.5,
            outcome="ok",
            args_summary="",
            error_summary=None,
            redaction_version="v1",
        )
        tm._completed_tool_events.append(tool_ev)

        state = await ui._build_state()
        transcript = state["transcripts"][broker.crew_id]
        bash_entry = next(
            (m for m in transcript if m.get("tool_use_id") == "toolu_abc"), None
        )
        assert bash_entry is not None
        assert bash_entry["crew_id"] == broker.crew_id

    async def test_task_tool_events_excluded_from_messages(self) -> None:
        from claude_crew.broker import BrokerSnapshot

        broker, ui = _make_ui_server()
        task_ev = ToolEvent(
            teammate_id="t-1",
            tool_name="Task",
            tool_use_id="toolu_task1",
            started_at_wallclock=time.time() - 1.0,
            finished_at_wallclock=time.time(),
            duration_seconds=1.0,
            outcome="ok",
            args_summary="",
            error_summary=None,
            redaction_version="v1",
        )
        snapshot = BrokerSnapshot(
            crew_id=broker.crew_id,
            teammates=[],
            live=[],
            log=[],
            tool_events=[task_ev],
            startup_diagnostics=[],
            dead_configs={},
        )

        _, messages = ui._build_local_instance(snapshot)
        assert not any(m.get("kind") == "tool" for m in messages)
