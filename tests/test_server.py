"""Integration-level tests: tools driven through the actual MCP server.

These wire a client to a server in-process via the mcp SDK's memory
harness. Every tool is exercised through the real MCP request/response
path — no broker shortcuts.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session

from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.server import make_server


def _content_text(result: Any) -> str:
    assert result.content, f"empty content: {result}"
    return result.content[0].text  # type: ignore[union-attr]


def _content_json(result: Any) -> Any:
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    return json.loads(_content_text(result))


def _client():
    return create_connected_server_and_client_session(make_server())


async def _poll_until(coro_factory, predicate, attempts: int = 50, delay: float = 0.02):
    """Call coro_factory() repeatedly until predicate(result) returns True."""
    for _ in range(attempts):
        result = await coro_factory()
        if predicate(result):
            return result
        await asyncio.sleep(delay)
    return result


# ---------- spawn_teammate ----------

class TestSpawnTool:
    async def test_spawn_returns_id_name_role(self) -> None:
        async with _client() as s:
            await s.initialize()
            body = _content_json(await s.call_tool(
                "spawn_teammate", {"role": "planner", "name": "alice"},
            ))
            assert body["role"] == "planner"
            assert body["name"] == "alice"
            assert body["teammate_id"].startswith("t-")

    async def test_spawn_without_name_defaults_to_role(self) -> None:
        async with _client() as s:
            await s.initialize()
            body = _content_json(await s.call_tool("spawn_teammate", {"role": "explorer"}))
            assert body["name"] == "explorer"


# ---------- send_to ----------

class TestSendToTool:
    async def test_send_returns_message_id_and_seq(self) -> None:
        async with _client() as s:
            await s.initialize()
            spawn = _content_json(await s.call_tool("spawn_teammate", {"role": "r"}))
            send = _content_json(await s.call_tool(
                "send_to",
                {"teammate_id": spawn["teammate_id"], "payload": {"hi": 1}},
            ))
            assert "message_id" in send
            assert send["seq"] >= 1

    async def test_send_to_unknown_teammate_returns_error(self) -> None:
        async with _client() as s:
            await s.initialize()
            result = _content_json(await s.call_tool(
                "send_to", {"teammate_id": "ghost", "payload": "x"},
            ))
            assert result.get("error") == "unknown_teammate"

    async def test_duplicate_id_returns_error(self) -> None:
        async with _client() as s:
            await s.initialize()
            spawn = _content_json(await s.call_tool("spawn_teammate", {"role": "r"}))
            tid = spawn["teammate_id"]
            first = _content_json(await s.call_tool(
                "send_to", {"teammate_id": tid, "payload": "a", "id": "fixed-id"},
            ))
            assert "message_id" in first
            second = _content_json(await s.call_tool(
                "send_to", {"teammate_id": tid, "payload": "a", "id": "fixed-id"},
            ))
            assert second.get("error") == "duplicate"


# ---------- broadcast ----------

class TestBroadcastTool:
    async def test_broadcast_to_two_teammates(self) -> None:
        async with _client() as s:
            await s.initialize()
            await s.call_tool("spawn_teammate", {"role": "r"})
            await s.call_tool("spawn_teammate", {"role": "r"})
            result = _content_json(await s.call_tool("broadcast", {"payload": "hello"}))
            assert result["delivered_to"] == 2
            assert len(result["message_ids"]) == 2

    async def test_broadcast_to_empty_crew(self) -> None:
        async with _client() as s:
            await s.initialize()
            result = _content_json(await s.call_tool("broadcast", {"payload": "hi"}))
            assert result["delivered_to"] == 0
            assert result["message_ids"] == []


# ---------- get_messages ----------

class TestGetMessagesTool:
    async def test_lead_receives_stub_echo(self) -> None:
        async with _client() as s:
            await s.initialize()
            spawn = _content_json(await s.call_tool("spawn_teammate", {"role": "parrot"}))
            await s.call_tool(
                "send_to", {"teammate_id": spawn["teammate_id"], "payload": "hello"},
            )
            result = await _poll_until(
                lambda: s.call_tool("get_messages", {}),
                lambda r: bool(_content_json(r)["messages"]),
            )
            body = _content_json(result)
            assert len(body["messages"]) == 1
            msg = body["messages"][0]
            assert msg["sender"] == spawn["teammate_id"]
            assert msg["recipient"] == "lead"
            assert msg["payload"]["echo"] == "hello"
            assert msg["payload"]["from"] == "parrot"
            assert body["next_seq"] == msg["seq"]

    async def test_get_messages_empty_returns_no_messages(self) -> None:
        async with _client() as s:
            await s.initialize()
            result = _content_json(await s.call_tool("get_messages", {}))
            assert result["messages"] == []
            assert result["next_seq"] == 0

    async def test_since_seq_filters(self) -> None:
        async with _client() as s:
            await s.initialize()
            spawn = _content_json(await s.call_tool("spawn_teammate", {"role": "parrot"}))
            for i in range(3):
                await s.call_tool(
                    "send_to", {"teammate_id": spawn["teammate_id"], "payload": i},
                )
            result = await _poll_until(
                lambda: s.call_tool("get_messages", {}),
                lambda r: len(_content_json(r)["messages"]) == 3,
            )
            body = _content_json(result)
            assert len(body["messages"]) == 3
            first_seq = body["messages"][0]["seq"]
            filtered = _content_json(await s.call_tool(
                "get_messages", {"since_seq": first_seq},
            ))
            assert len(filtered["messages"]) == 2


# ---------- list_crew ----------

class TestListCrewTool:
    async def test_empty_crew(self) -> None:
        async with _client() as s:
            await s.initialize()
            result = _content_json(await s.call_tool("list_crew", {}))
            assert result["teammates"] == []

    async def test_lists_spawned_teammates(self) -> None:
        async with _client() as s:
            await s.initialize()
            await s.call_tool("spawn_teammate", {"role": "planner"})
            await s.call_tool("spawn_teammate", {"role": "builder"})
            result = _content_json(await s.call_tool("list_crew", {}))
            roles = sorted(t["role"] for t in result["teammates"])
            assert roles == ["builder", "planner"]


# ---------- kill_teammate ----------

class TestKillTeammateTool:
    async def test_kill_then_send_returns_teammate_dead(self) -> None:
        async with _client() as s:
            await s.initialize()
            spawn = _content_json(await s.call_tool("spawn_teammate", {"role": "r"}))
            tid = spawn["teammate_id"]
            kill = _content_json(await s.call_tool("kill_teammate", {"teammate_id": tid}))
            assert kill == {"ok": True}
            send = _content_json(await s.call_tool(
                "send_to", {"teammate_id": tid, "payload": "x"},
            ))
            assert send.get("error") == "teammate_dead"

    async def test_kill_unknown_returns_error(self) -> None:
        async with _client() as s:
            await s.initialize()
            result = _content_json(await s.call_tool(
                "kill_teammate", {"teammate_id": "ghost"},
            ))
            assert result.get("error") == "unknown_teammate"


# ---------- get_teammate_status ----------

class TestGetTeammateStatusTool:
    async def test_unknown_id_returns_unknown_teammate_error(self) -> None:
        async with _client() as s:
            await s.initialize()
            result = _content_json(await s.call_tool(
                "get_teammate_status", {"teammate_id": "ghost"},
            ))
            assert result.get("error") == "unknown_teammate"
            assert "ghost" in result.get("message", "")

    async def test_alive_teammate_returns_full_status(self) -> None:
        async with _client() as s:
            await s.initialize()
            spawn = _content_json(await s.call_tool(
                "spawn_teammate", {"role": "planner", "name": "alice"},
            ))
            tid = spawn["teammate_id"]
            status = _content_json(await s.call_tool(
                "get_teammate_status", {"teammate_id": tid},
            ))
            assert status["alive"] is True
            assert status["teammate_id"] == tid
            assert status["role"] == "planner"
            assert status["name"] == "alice"
            assert status["died_at_wallclock"] is None
            assert status["exit_code"] is None
            assert "idle_seconds" in status
            # F8 additions (T4): tool-tracking fields must be present
            assert "current_tools" in status
            assert "current_tool" in status
            assert "current_tool_count" in status
            assert "last_tool_completed" in status
            assert "redaction_version" in status
            assert status["current_tools"] == []
            assert status["current_tool"] is None
            assert status["current_tool_count"] == 0
            assert status["last_tool_completed"] is None

    async def test_killed_teammate_returns_dead_status(self) -> None:
        """D11: get_teammate_status after kill returns alive=False with death record."""
        async with _client() as s:
            await s.initialize()
            spawn = _content_json(await s.call_tool("spawn_teammate", {"role": "r"}))
            tid = spawn["teammate_id"]
            await s.call_tool("kill_teammate", {"teammate_id": tid})
            status = _content_json(await s.call_tool(
                "get_teammate_status", {"teammate_id": tid},
            ))
            assert status["alive"] is False
            assert status["died_at_wallclock"] is not None
            assert status["exit_code"] is None
            assert status["current_turn_started_at_wallclock"] is None


# ---------- broadcast with skipped_dead ----------

class TestBroadcastSkippedDead:
    async def test_broadcast_skipped_dead_in_response(self) -> None:
        """D12: killed teammate is listed in skipped_dead, not delivered_to."""
        async with _client() as s:
            await s.initialize()
            spawn_a = _content_json(await s.call_tool("spawn_teammate", {"role": "r"}))
            spawn_b = _content_json(await s.call_tool("spawn_teammate", {"role": "r"}))
            spawn_c = _content_json(await s.call_tool("spawn_teammate", {"role": "r"}))
            tid_c = spawn_c["teammate_id"]
            await s.call_tool("kill_teammate", {"teammate_id": tid_c})

            result = _content_json(await s.call_tool("broadcast", {"payload": "hi"}))
            assert result["delivered_to"] == 2
            assert len(result["message_ids"]) == 2
            assert tid_c in result["skipped_dead"]


# ---------- F9: get_messages long-poll (wait_seconds parameter) ----------


class TestGetMessagesLongPollTool:
    """F9 SC-1/2/3/4/6 — wait_seconds parameter on the get_messages MCP tool.

    Scenarios:
      1. explicit wait_seconds=0 → bit-for-bit identical (SC-1)
      2. messages already in _log → return immediately even with large wait_seconds (SC-2)
      3. block-and-wake: long-poll returns when a LEAD-bound send fires mid-wait (SC-3)
      4. timeout: no message arrives → empty list, correct shape, no error (SC-4)
      5. server-layer cap: wait_seconds=9999 → broker helper called with 600.0 (SC-6)
    """

    async def test_wait_seconds_zero_preserves_existing_behavior(self) -> None:
        """SC-1: explicit wait_seconds=0 → same response shape as the default."""
        async with _client() as s:
            await s.initialize()
            result = _content_json(
                await s.call_tool("get_messages", {"wait_seconds": 0}),
            )
        assert result["messages"] == []
        assert result["next_seq"] == 0

    async def test_messages_present_returns_immediately(self) -> None:
        """SC-2: wait_seconds>0 but messages already in _log → no blocking."""
        b = Broker()
        # Inject a message to LEAD directly on the broker before the tool call.
        env = Envelope(
            id=new_message_id(), seq=0, sender="t-injected", recipient=LEAD_ID,
            timestamp=time.time(), payload={"pre": "loaded"},
        )
        await b.send(env)

        start = time.monotonic()
        async with create_connected_server_and_client_session(make_server(broker=b)) as s:
            await s.initialize()
            result = _content_json(
                await s.call_tool("get_messages", {"since_seq": 0, "wait_seconds": 5.0}),
            )
        elapsed = time.monotonic() - start

        assert len(result["messages"]) == 1
        assert result["messages"][0]["payload"] == {"pre": "loaded"}
        assert elapsed < 0.5, f"should return immediately, got {elapsed:.3f} s"
        await b.shutdown_all()

    async def test_blocks_until_message_arrives(self) -> None:
        """SC-3: long-poll blocks, then returns when a LEAD-bound send fires."""
        b = Broker()

        async def _send_after_delay() -> None:
            await asyncio.sleep(0.2)
            env = Envelope(
                id=new_message_id(), seq=0, sender=LEAD_ID, recipient=LEAD_ID,
                timestamp=time.time(), payload={"woke": True},
            )
            await b.send(env)

        start = time.monotonic()
        async with create_connected_server_and_client_session(make_server(broker=b)) as s:
            await s.initialize()
            send_task = asyncio.create_task(_send_after_delay())
            result = _content_json(
                await s.call_tool("get_messages", {"since_seq": 0, "wait_seconds": 5.0}),
            )
            await send_task
        elapsed = time.monotonic() - start

        assert len(result["messages"]) == 1
        assert result["messages"][0]["payload"] == {"woke": True}
        assert 0.1 <= elapsed <= 0.8, f"expected ~0.2 s wake, got {elapsed:.3f} s"
        await b.shutdown_all()

    async def test_timeout_returns_empty_cleanly(self) -> None:
        """SC-4: no message within wait_seconds → empty list, unchanged next_seq, no error."""
        async with _client() as s:
            await s.initialize()
            start = time.monotonic()
            result = _content_json(
                await s.call_tool("get_messages", {"since_seq": 0, "wait_seconds": 0.2}),
            )
            elapsed = time.monotonic() - start

        assert result["messages"] == []
        assert result["next_seq"] == 0
        assert 0.15 <= elapsed <= 0.6, f"expected ~0.2 s, got {elapsed:.3f} s"

    async def test_wait_seconds_capped_at_max(self, monkeypatch) -> None:
        """SC-6: wait_seconds=9999 → broker.wait_for_lead_message called with 600.0."""
        b = Broker()
        captured_timeouts: list[float] = []

        async def _mock_wait(timeout: float) -> None:
            captured_timeouts.append(timeout)
            # Return immediately so the test finishes without waiting 10 minutes.

        monkeypatch.setattr(b, "wait_for_lead_message", _mock_wait)

        async with create_connected_server_and_client_session(make_server(broker=b)) as s:
            await s.initialize()
            result = _content_json(
                await s.call_tool("get_messages", {"since_seq": 0, "wait_seconds": 9999}),
            )

        assert captured_timeouts == [600.0], (
            f"expected broker helper called with [600.0], got {captured_timeouts}"
        )
        assert result["messages"] == []
        await b.shutdown_all()

    async def test_negative_wait_seconds_treated_as_zero(self) -> None:
        """M-1 (sentinel): wait_seconds=-5 → no blocking, returns immediately."""
        async with _client() as s:
            await s.initialize()
            start = time.monotonic()
            result = _content_json(
                await s.call_tool("get_messages", {"since_seq": 0, "wait_seconds": -5}),
            )
            elapsed = time.monotonic() - start

        assert result["messages"] == []
        assert result["next_seq"] == 0
        assert elapsed < 0.2, f"negative wait_seconds should return immediately, got {elapsed:.3f} s"
