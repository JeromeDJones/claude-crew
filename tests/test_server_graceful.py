"""AT#14 — Server MCP tool threads graceful/flush_timeout to broker.kill_teammate.

The FastMCP kill_teammate tool must:
- Accept graceful (bool, default True) and flush_timeout (float, default 90.0).
- Thread both kwargs through to broker.kill_teammate.
- Default (no args) preserves graceful=True.

Verified with a stub/mocked broker that records call kwargs.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from claude_crew.broker import LEAD_ID, Broker
from claude_crew.server import make_server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content_json(result: Any) -> Any:
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    assert result.content, f"empty content: {result}"
    return json.loads(result.content[0].text)  # type: ignore[union-attr]


@asynccontextmanager
async def _client_with_broker(broker: Broker):
    """Create an in-process MCP client wired to make_server(broker=broker)."""
    async with create_connected_server_and_client_session(
        make_server(broker=broker)
    ) as s:
        await s.initialize()
        yield s


# ---------------------------------------------------------------------------
# Recording broker stub
# ---------------------------------------------------------------------------

class _FakeSink:
    """Minimal transcript sink stub."""

    disabled = True
    path = None

    def write_lifecycle(self, *a, **kw) -> None:
        pass

    def close(self) -> None:
        pass


class _RecordingBroker:
    """Minimal broker stand-in that records kill_teammate call kwargs."""

    def __init__(self, teammate_ids: list[str] | None = None) -> None:
        # Teammate ids that are considered "live" — all others → UnknownTeammateError
        self._live_ids: set[str] = set(teammate_ids or [])
        self.kill_calls: list[dict[str, Any]] = []

        # Minimal stubs so make_server can build without crashing.
        self.crew_id = "test-crew"
        self.startup_diagnostics: tuple = ()  # type: ignore[assignment]
        self._sink = _FakeSink()

    # --- stubs the server references beyond kill_teammate ---
    def list_crew(self):  # noqa: D401
        return []

    def get_messages(self, *, recipient, since_seq, limit):  # noqa: D401
        return []

    def get_teammate_status(self, tid):  # noqa: D401
        return MagicMock(alive=True)

    async def wait_for_lead_message(self, timeout):  # noqa: D401
        pass

    async def spawn_teammate(self, *args, **kwargs):  # noqa: D401
        return "fake-tid"

    async def send(self, envelope):  # noqa: D401
        return envelope

    async def broadcast(self, **kwargs):  # noqa: D401
        return []

    async def shutdown_all(self, **kwargs):  # noqa: D401
        pass

    # --- the method under test ---
    async def kill_teammate(
        self,
        teammate_id: str,
        reason: str = "explicit",
        *,
        graceful: bool = True,
        flush_timeout: float = 90.0,
    ) -> None:
        from claude_crew.broker import TeammateAlreadyDeadError, UnknownTeammateError

        if teammate_id not in self._live_ids:
            raise UnknownTeammateError(teammate_id)

        self.kill_calls.append(
            {
                "teammate_id": teammate_id,
                "reason": reason,
                "graceful": graceful,
                "flush_timeout": flush_timeout,
            }
        )
        self._live_ids.discard(teammate_id)


# ---------------------------------------------------------------------------
# AT#14 tests
# ---------------------------------------------------------------------------


class TestServerKillTeammateGracefulArg:
    """AT#14 — graceful and flush_timeout are threaded to broker.kill_teammate."""

    async def test_default_args_passes_graceful_true(self) -> None:
        """No args → graceful=True is threaded to the broker."""
        broker = _RecordingBroker(teammate_ids=["tm-1"])
        async with _client_with_broker(broker) as s:
            result = _content_json(
                await s.call_tool("kill_teammate", {"teammate_id": "tm-1"})
            )
        assert result == {"ok": True}
        assert len(broker.kill_calls) == 1
        call = broker.kill_calls[0]
        assert call["teammate_id"] == "tm-1"
        assert call["graceful"] is True

    async def test_graceful_false_threaded(self) -> None:
        """graceful=False is forwarded to broker.kill_teammate."""
        broker = _RecordingBroker(teammate_ids=["tm-2"])
        async with _client_with_broker(broker) as s:
            result = _content_json(
                await s.call_tool(
                    "kill_teammate",
                    {"teammate_id": "tm-2", "graceful": False},
                )
            )
        assert result == {"ok": True}
        assert len(broker.kill_calls) == 1
        assert broker.kill_calls[0]["graceful"] is False

    async def test_flush_timeout_threaded(self) -> None:
        """flush_timeout is forwarded to broker.kill_teammate."""
        broker = _RecordingBroker(teammate_ids=["tm-3"])
        async with _client_with_broker(broker) as s:
            result = _content_json(
                await s.call_tool(
                    "kill_teammate",
                    {"teammate_id": "tm-3", "graceful": False, "flush_timeout": 5.0},
                )
            )
        assert result == {"ok": True}
        call = broker.kill_calls[0]
        assert call["graceful"] is False
        assert call["flush_timeout"] == pytest.approx(5.0)

    async def test_default_flush_timeout_is_90(self) -> None:
        """When flush_timeout is omitted, broker receives the 90.0 default."""
        broker = _RecordingBroker(teammate_ids=["tm-4"])
        async with _client_with_broker(broker) as s:
            await s.call_tool("kill_teammate", {"teammate_id": "tm-4"})
        assert broker.kill_calls[0]["flush_timeout"] == pytest.approx(90.0)

    async def test_unknown_teammate_still_returns_error(self) -> None:
        """Unknown id still returns unknown_teammate error (not a crash)."""
        broker = _RecordingBroker(teammate_ids=[])
        async with _client_with_broker(broker) as s:
            result = _content_json(
                await s.call_tool(
                    "kill_teammate",
                    {"teammate_id": "ghost", "graceful": False},
                )
            )
        assert result.get("error") == "unknown_teammate"
        assert broker.kill_calls == []
