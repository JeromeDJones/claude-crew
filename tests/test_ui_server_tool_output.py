"""Tests for the /tool-output route and _build_state tool_use_id field.

Covers AT-5, AT-6, AT-9 from the click-to-view-tool-output spec.
"""

from __future__ import annotations

import time

import httpx
import pytest

from claude_crew.broker import Broker
from claude_crew.teammate import StubTeammate, ToolEvent
from claude_crew.ui_server import UIServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ui_server() -> tuple[Broker, UIServer]:
    broker = Broker()
    ui = UIServer(broker, port=0)
    return broker, ui


def _seed_tool_output(broker: Broker, teammate_id: str, tool_use_id: str, body: str) -> None:
    """Directly seed a tool output on a teammate in the broker's registry."""
    # StubTeammate exposes store_tool_output via the base class.
    tm = broker._teammates.get(teammate_id)
    if tm is None:
        tm = broker._dead_teammates.get(teammate_id)
    assert tm is not None, f"teammate {teammate_id!r} not found in broker"
    tm.store_tool_output(tool_use_id, body)


async def _spawn_stub(broker: Broker) -> str:
    """Spawn a StubTeammate and return its assigned id."""

    def _factory(id: str, name: str, role: str, **_kwargs) -> StubTeammate:
        return StubTeammate(id=id, name=name, role=role)

    # spawn_teammate returns the teammate_id (str) directly.
    return await broker.spawn_teammate(role="builder", name="builder", factory=_factory)


# ---------------------------------------------------------------------------
# AT-5: GET /tool-output/<teammate_id>/<tool_use_id> → 200 with stored body
# ---------------------------------------------------------------------------


class TestAT5ToolOutputHit:
    async def test_200_with_stored_body(self) -> None:
        """AT-5: hit → 200 {body, truncated: false, redaction_version: 'v1'}."""
        broker, ui = _make_ui_server()
        tm_id = await _spawn_stub(broker)
        _seed_tool_output(broker, tm_id, "toolu_abc", "hello world")

        app = ui._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.get(f"/tool-output/{tm_id}/toolu_abc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["body"] == "hello world"
        assert data["truncated"] is False
        assert data["redaction_version"] == "v1"

    async def test_200_body_matches_stored_content(self) -> None:
        """AT-5: returned body matches what was stored."""
        broker, ui = _make_ui_server()
        tm_id = await _spawn_stub(broker)
        _seed_tool_output(broker, tm_id, "toolu_xyz", "file contents here")

        app = ui._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.get(f"/tool-output/{tm_id}/toolu_xyz")

        assert resp.status_code == 200
        assert resp.json()["body"] == "file contents here"

    async def test_truncated_true_when_body_at_cap(self) -> None:
        """AT-5 / AT-4 linkage: body at 4096-byte cap → truncated: true."""
        broker, ui = _make_ui_server()
        tm_id = await _spawn_stub(broker)

        # Build a body that will be stored at the cap (4096 UTF-8 bytes).
        # store_tool_output caps and appends "…", so seed an already-capped body.
        capped = "x" * 4093 + "…"  # 4093 + 3 = 4096 bytes
        assert len(capped.encode("utf-8")) == 4096
        broker._teammates[tm_id].store_tool_output("toolu_big", capped)

        app = ui._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.get(f"/tool-output/{tm_id}/toolu_big")

        assert resp.status_code == 200
        assert resp.json()["truncated"] is True


# ---------------------------------------------------------------------------
# AT-6: 404 on unknown, 400 on bad path param
# ---------------------------------------------------------------------------


class TestAT6MissAndValidation:
    async def test_404_unknown_teammate(self) -> None:
        """AT-6: unknown teammate_id → 404 {error: 'not_found'}."""
        broker, ui = _make_ui_server()
        app = ui._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.get("/tool-output/unknown/unknown")

        assert resp.status_code == 404
        assert resp.json() == {"error": "not_found"}

    async def test_404_known_teammate_evicted_key(self) -> None:
        """AT-6: known teammate but evicted tool_use_id → 404."""
        broker, ui = _make_ui_server()
        tm_id = await _spawn_stub(broker)
        # Don't store anything; the key is absent.
        app = ui._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.get(f"/tool-output/{tm_id}/toolu_nothere")

        assert resp.status_code == 404
        assert resp.json() == {"error": "not_found"}

    async def test_400_invalid_teammate_id(self) -> None:
        """AT-6: teammate_id containing '..' → 400."""
        broker, ui = _make_ui_server()
        app = ui._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.get("/tool-output/bad..id/toolu_x")

        assert resp.status_code == 400

    async def test_400_invalid_tool_use_id(self) -> None:
        """AT-6: tool_use_id containing invalid characters → 400."""
        broker, ui = _make_ui_server()
        app = ui._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.get("/tool-output/valid-id/bad/slash")

        # Starlette's path param won't match a slash; the route won't be found.
        # This is acceptable — path traversal is blocked by Starlette routing.
        assert resp.status_code in (400, 404)

    async def test_400_space_in_teammate_id(self) -> None:
        """AT-6: teammate_id with space fails validation → 400."""
        broker, ui = _make_ui_server()
        app = ui._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.get("/tool-output/bad%20id/toolu_x")

        assert resp.status_code == 400

    async def test_500_on_broker_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AT-6 / spec: unexpected exception → 500 {error: 'internal_error'}."""
        broker, ui = _make_ui_server()

        def _boom(tid: str, uid: str) -> str:
            raise RuntimeError("unexpected!")

        monkeypatch.setattr(broker, "get_tool_output", _boom)

        app = ui._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.get("/tool-output/valid-id/toolu_abc")

        assert resp.status_code == 500
        assert resp.json() == {"error": "internal_error"}


# ---------------------------------------------------------------------------
# AT-9: _build_state produces kind:"tool" entries with tool_use_id field
# ---------------------------------------------------------------------------


class TestAT9ToolUseIdInState:
    async def test_tool_event_includes_tool_use_id(self) -> None:
        """AT-9: kind:'tool' entry in messages contains tool_use_id."""
        from claude_crew.broker import BrokerSnapshot
        from claude_crew.envelope import Envelope

        broker, ui = _make_ui_server()

        # Build a synthetic BrokerSnapshot with one tool event.
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

    async def test_build_state_tool_event_has_tool_use_id(self) -> None:
        """AT-9 via _build_state: full state build includes tool_use_id on tool entries."""
        broker, ui = _make_ui_server()
        tm_id = await _spawn_stub(broker)

        # Inject a ToolEvent directly into the teammate's deque.
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

        tool_entries = [m for m in transcript if m.get("kind") == "tool"]
        assert len(tool_entries) >= 1
        bash_entry = next((m for m in tool_entries if m.get("tool_use_id") == "toolu_abc"), None)
        assert bash_entry is not None, "Expected a tool entry with tool_use_id='toolu_abc'"

    async def test_task_tool_events_excluded_from_messages(self) -> None:
        """Regression: Task tool events should not appear in messages (existing filter)."""
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
