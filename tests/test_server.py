"""Integration-level tests: tools driven through the actual MCP server.

These wire a client to a server in-process via the mcp SDK's memory
harness. Every tool is exercised through the real MCP request/response
path — no broker shortcuts.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

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
                lambda: s.call_tool("get_messages", {"wait_seconds": 0.1}),
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
            result = _content_json(
                await s.call_tool("get_messages", {"wait_seconds": 0.1}),
            )
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
                lambda: s.call_tool("get_messages", {"wait_seconds": 0.1}),
                lambda r: len(_content_json(r)["messages"]) == 3,
            )
            body = _content_json(result)
            assert len(body["messages"]) == 3
            first_seq = body["messages"][0]["seq"]
            filtered = _content_json(await s.call_tool(
                "get_messages", {"since_seq": first_seq, "wait_seconds": 0.1},
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
      1. wait_seconds=0 is rejected (long-poll required) (SC-1)
      2. messages already in _log → return immediately even with large wait_seconds (SC-2)
      3. block-and-wake: long-poll returns when a LEAD-bound send fires mid-wait (SC-3)
      4. timeout: no message arrives → empty list, correct shape, no error (SC-4)
      5. server-layer cap: wait_seconds=9999 → broker helper called with 600.0 (SC-6)
    """

    async def test_wait_seconds_zero_is_rejected(self) -> None:
        """SC-1: wait_seconds=0 → ToolError (long-poll is required)."""
        async with _client() as s:
            await s.initialize()
            result = await s.call_tool("get_messages", {"wait_seconds": 0})
            assert result.isError, "expected ToolError for wait_seconds=0"
            assert "must be > 0" in str(result.content[0].text)

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

    async def test_negative_wait_seconds_is_rejected(self) -> None:
        """Negative wait_seconds → ToolError (long-poll is required)."""
        async with _client() as s:
            await s.initialize()
            result = await s.call_tool(
                "get_messages", {"since_seq": 0, "wait_seconds": -5},
            )
            assert result.isError, "expected ToolError for negative wait_seconds"
            assert "must be > 0" in str(result.content[0].text)


# ---------- extra_tools / extra_skills spawn guard (AT-3) ----------


class TestSpawnExtrasGuard:
    """AT-3: Task guard fires at server boundary before any broker state is mutated."""

    async def test_task_in_extra_tools_raises_tool_error(self) -> None:
        """AT-3: extra_tools=["Task"] → ToolError; no new teammate in list_crew."""
        async with _client() as s:
            await s.initialize()

            result = await s.call_tool(
                "spawn_teammate", {"role": "planner", "extra_tools": ["Task"]},
            )
            assert result.isError is True, "expected ToolError for extra_tools containing Task"
            assert result.content
            text = result.content[0].text
            assert "Task tool cannot be granted" in text, (
                f"error message must name the constraint; got: {text!r}"
            )
            assert "leaf nodes" in text, (
                f"error message must reference leaf-node invariant; got: {text!r}"
            )

            # No teammate should have been spawned
            crew = _content_json(await s.call_tool("list_crew", {}))
            assert crew["teammates"] == [], (
                "ToolError must fire before broker.spawn_teammate; "
                "list_crew must remain empty"
            )

    async def test_task_among_other_extras_still_raises(self) -> None:
        """Task mixed with legitimate tools → still raises ToolError."""
        async with _client() as s:
            await s.initialize()

            result = await s.call_tool(
                "spawn_teammate",
                {"role": "planner", "extra_tools": ["Read", "Task", "Grep"]},
            )
            assert result.isError is True

    async def test_extra_tools_without_task_succeeds(self) -> None:
        """Legitimate extra_tools (no Task) → spawn succeeds."""
        async with _client() as s:
            await s.initialize()

            result = await s.call_tool(
                "spawn_teammate",
                {"role": "planner", "extra_tools": ["Read", "mcp__kg__repo_map"]},
            )
            assert not result.isError, (
                f"spawn with legitimate extras must succeed; got error: {result.content}"
            )
            body = _content_json(result)
            assert body["teammate_id"].startswith("t-")


# ---------- list_available_tools (AT-7, AT-8, AT-13, AT-14, AT-15) ----------


def _client_with(home_dir: Path | None = None, project_root: Path | None = None):
    """Create a connected client/server pair with injected home_dir / project_root."""
    return create_connected_server_and_client_session(
        make_server(home_dir=home_dir, project_root=project_root)
    )


class TestListAvailableTools:
    """AT-7, AT-8, AT-13, AT-14, AT-15: list_available_tools MCP tool."""

    async def test_shape_and_required_keys(self, tmp_path: Path) -> None:
        """AT-7: response has required keys, Task not in builtins, no env/command/args."""
        async with _client_with(home_dir=tmp_path, project_root=tmp_path) as s:
            await s.initialize()
            result = _content_json(await s.call_tool("list_available_tools", {}))

        assert "builtins" in result
        assert "mcp_servers" in result
        assert "skills" in result
        assert "plugins" in result
        assert "project_root" in result

        # Task must NOT appear in builtins
        assert "Task" not in result["builtins"], (
            "Task must not be in builtins (leaf-node invariant)"
        )

        # project_root must be a non-empty string
        assert isinstance(result["project_root"], str)
        assert result["project_root"], "project_root must be a non-empty string"

        # No MCP server entry may contain command, args, or env
        for entry in result["mcp_servers"]:
            assert "command" not in entry, f"command must not be serialized: {entry}"
            assert "args" not in entry, f"args must not be serialized: {entry}"
            assert "env" not in entry, f"env must not be serialized: {entry}"

    async def test_reuses_user_loader_for_mcp_servers(self, tmp_path: Path) -> None:
        """AT-8: mocked ~/.claude.json with one server name → single entry with running=null.
        Verifies _load_user_mcp_server_names is called (not reimplemented)."""
        # Plant a ~/.claude.json in tmp_path
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "test-server": {
                    "command": "npx",
                    "args": ["-y", "test-server-pkg"],
                    "env": {"SECRET_KEY": "hunter2"},
                }
            }
        }))

        called_with: list[Any] = []
        from claude_crew import server as server_module
        from claude_crew.subagents._user_loader import _load_user_mcp_server_names as _orig

        def _spy(home_dir=None):
            called_with.append(home_dir)
            return _orig(home_dir)

        with patch.object(server_module, "_load_user_mcp_server_names", side_effect=_spy):
            async with _client_with(home_dir=tmp_path, project_root=tmp_path) as s:
                await s.initialize()
                result = _content_json(await s.call_tool("list_available_tools", {}))

        assert result["mcp_servers"] == [{"name": "test-server", "running": None}], (
            f"expected single entry with running=null; got {result['mcp_servers']!r}"
        )
        assert called_with, "_load_user_mcp_server_names must have been called"
        # No command/args/env exposed
        entry = result["mcp_servers"][0]
        assert "command" not in entry
        assert "args" not in entry
        assert "env" not in entry

    async def test_missing_claude_json_returns_empty_mcp_servers(
        self, tmp_path: Path
    ) -> None:
        """AT-13: home dir with no ~/.claude.json → mcp_servers == []. No exception."""
        async with _client_with(home_dir=tmp_path, project_root=tmp_path) as s:
            await s.initialize()
            result = _content_json(await s.call_tool("list_available_tools", {}))

        assert result["mcp_servers"] == [], (
            f"expected empty mcp_servers when .claude.json is absent; got {result['mcp_servers']!r}"
        )

    async def test_no_skill_dirs_returns_empty_skills(self, tmp_path: Path) -> None:
        """AT-14: no ~/.claude/skills/ and no project skill dir → skills == []. No exception."""
        async with _client_with(home_dir=tmp_path, project_root=tmp_path) as s:
            await s.initialize()
            result = _content_json(await s.call_tool("list_available_tools", {}))

        assert result["skills"] == [], (
            f"expected empty skills when no skill dirs exist; got {result['skills']!r}"
        )

    async def test_absent_installed_plugins_returns_empty_plugins(
        self, tmp_path: Path
    ) -> None:
        """AT-15: no installed_plugins.json → plugins == []. No exception."""
        async with _client_with(home_dir=tmp_path, project_root=tmp_path) as s:
            await s.initialize()
            result = _content_json(await s.call_tool("list_available_tools", {}))

        assert result["plugins"] == [], (
            f"expected empty plugins when installed_plugins.json is absent; got {result['plugins']!r}"
        )
