"""Integration-level tests: tools driven through the actual MCP server.

These wire a client to a server in-process via the mcp SDK's memory
harness. Every tool is exercised through the real MCP request/response
path — no broker shortcuts.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session

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
    async def test_kill_then_send_returns_unknown(self) -> None:
        async with _client() as s:
            await s.initialize()
            spawn = _content_json(await s.call_tool("spawn_teammate", {"role": "r"}))
            tid = spawn["teammate_id"]
            kill = _content_json(await s.call_tool("kill_teammate", {"teammate_id": tid}))
            assert kill == {"ok": True}
            send = _content_json(await s.call_tool(
                "send_to", {"teammate_id": tid, "payload": "x"},
            ))
            assert send.get("error") == "unknown_teammate"

    async def test_kill_unknown_returns_error(self) -> None:
        async with _client() as s:
            await s.initialize()
            result = _content_json(await s.call_tool(
                "kill_teammate", {"teammate_id": "ghost"},
            ))
            assert result.get("error") == "unknown_teammate"
