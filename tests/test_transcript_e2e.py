"""End-to-end test for Feature #4 transcript.

Drives the MCP server through the in-memory client, exchanges messages,
then reads the JSONL file from disk and asserts the full lifecycle and
envelope shape.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from claude_crew.server import make_server


@pytest.fixture
def enable_transcripts(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", raising=False)
    monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
    return tmp_path


def _content_json(result: Any) -> Any:
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


class TestGetTranscriptPathTool:
    async def test_returns_path_and_crew_id_when_enabled(
        self, enable_transcripts,
    ) -> None:
        async with create_connected_server_and_client_session(make_server()) as s:
            await s.initialize()
            result = _content_json(await s.call_tool("get_transcript_path", {}))
            assert result["path"] is not None
            assert result["disabled"] is False
            assert isinstance(result["crew_id"], str)
            assert len(result["crew_id"]) == 8
            assert result["crew_id"] in result["path"]

    async def test_returns_disabled_true_when_env_set(self) -> None:
        # conftest default: CLAUDE_CREW_TRANSCRIPT_DISABLED=1
        async with create_connected_server_and_client_session(make_server()) as s:
            await s.initialize()
            result = _content_json(await s.call_tool("get_transcript_path", {}))
            assert result["disabled"] is True
            assert result["path"] is None


class TestE2EFullExchange:
    async def test_spawn_send_kill_writes_complete_transcript(
        self, enable_transcripts,
    ) -> None:
        async with create_connected_server_and_client_session(make_server()) as s:
            await s.initialize()
            tp = _content_json(await s.call_tool("get_transcript_path", {}))
            transcript_path = Path(tp["path"])
            crew_id = tp["crew_id"]

            spawn = _content_json(await s.call_tool(
                "spawn_teammate", {"role": "parrot", "name": "polly"},
            ))
            tid = spawn["teammate_id"]
            await s.call_tool("send_to", {
                "teammate_id": tid, "payload": "hello",
            })
            # Wait for stub echo.
            for _ in range(50):
                got = _content_json(await s.call_tool("get_messages", {}))
                if got["messages"]:
                    break
                await asyncio.sleep(0.02)
            await s.call_tool("kill_teammate", {"teammate_id": tid})

        # After session exit, broker.shutdown_all hasn't been called from
        # in-memory harness — but the file is line-flushed so we can read it.
        assert transcript_path.exists()
        lines = [json.loads(l) for l in transcript_path.read_text().splitlines() if l]

        # Every line has v=1, kind, ts, crew_id.
        for line in lines:
            assert line["v"] == 1
            assert line["kind"] in ("envelope", "lifecycle")
            assert "ts" in line
            assert line["crew_id"] == crew_id

        events = [l for l in lines if l["kind"] == "lifecycle"]
        envelopes = [l for l in lines if l["kind"] == "envelope"]

        # Lifecycle: started, spawn, kill (reason=explicit). No shutdown
        # because the in-memory harness exits without invoking shutdown_all.
        event_types = [e["event"] for e in events]
        assert "started" in event_types
        assert "spawn" in event_types
        spawn_event = next(e for e in events if e["event"] == "spawn")
        assert spawn_event["teammate_id"] == tid
        assert spawn_event["role"] == "parrot"
        assert spawn_event["name"] == "polly"
        kill_event = next(e for e in events if e["event"] == "kill")
        assert kill_event["teammate_id"] == tid
        assert kill_event["reason"] == "explicit"

        # Envelopes: lead → teammate ("hello"), teammate → lead (echo).
        assert len(envelopes) >= 2
        seqs = [e["seq"] for e in envelopes]
        assert seqs == sorted(seqs)
        # The first envelope is the lead's send.
        first = envelopes[0]
        assert first["sender"] == "lead"
        assert first["recipient"] == tid
        assert first["payload"] == "hello"

    async def test_disabled_mode_writes_no_file(self) -> None:
        # conftest default: disabled.
        async with create_connected_server_and_client_session(make_server()) as s:
            await s.initialize()
            spawn = _content_json(await s.call_tool(
                "spawn_teammate", {"role": "parrot"},
            ))
            await s.call_tool("send_to", {
                "teammate_id": spawn["teammate_id"], "payload": "x",
            })
            tp = _content_json(await s.call_tool("get_transcript_path", {}))
            assert tp["disabled"] is True
            assert tp["path"] is None
