"""Tests for D0: unconditional send_to wiring in SdkTeammate._run.

Covers AT 1, AT 2, AT 3 from spec/m3-5-reshape-live-crew.md.

AT 1 — behavioral (happy path, no out-edges):
    A SdkTeammate spawned with neighbors=None (no out-edges) now has the
    in-process send_to MCP server wired in its SDK options: _SEND_TO_MCP_SERVER_NAME
    in mcp_servers and _SEND_TO_TOOL_ID in allowed_tools.

AT 2 — structural guard (deletion-detector):
    The NAMED LITERAL substring ``_has_out_edges`` does not appear anywhere
    in claude_crew/sdk_teammate.py. Re-introducing the spawn-time gate re-adds
    the literal and fails this test.

AT 3 — non-regression (with out-edges):
    A SdkTeammate spawned WITH a declared out-edge neighbor also has send_to
    wired — the unconditional path is exercised consistently regardless of
    neighbor configuration.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import claude_crew.sdk_teammate as sdk_module
from claude_crew.broker import Broker
from claude_crew.sdk_teammate import (
    _SEND_TO_MCP_SERVER_NAME,
    _SEND_TO_TOOL_ID,
    SdkTeammate,
)


# ---------------------------------------------------------------------------
# Minimal fake SDK client that captures options and exits without blocking.
# ---------------------------------------------------------------------------


class _CapturingFakeClient:
    """Lightweight ClaudeSDKClient double.

    Captures the ClaudeAgentOptions at construction time, implements the
    async context-manager protocol, and provides receive_response() as an
    async generator that yields nothing — so SdkTeammate._run enters the
    inbox loop quickly without blocking on SDK I/O.
    """

    def __init__(self, options: Any = None) -> None:
        self._options = options

    async def __aenter__(self) -> "_CapturingFakeClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def query(self, prompt: str, session_id: str = "default") -> None:
        pass

    async def receive_response(self) -> Any:
        # Empty async generator — no messages to yield.
        return
        yield  # pragma: no cover — makes this an async generator


def _make_sdk_factory(captured_options: list[Any]) -> Any:
    """Return a factory function that creates SdkTeammate and records options."""

    def factory(
        id: str,
        name: str,
        role: str,
        *,
        neighbors: "list[dict] | None" = None,
        **_kwargs: Any,
    ) -> SdkTeammate:
        # Use empty agents dict so __init__ doesn't load the default pack
        # (avoids disk I/O; role "test-role" has no pack entry, which is fine).
        return SdkTeammate(
            id=id,
            name=name,
            role=role,
            agents={},
            pack_bodies={},
            neighbors=neighbors,
        )

    # Carry extra_skills kwarg (broker.spawn_teammate may pass it)
    return factory


async def _spawn_and_capture(
    monkeypatch: Any,
    neighbors: "list[dict] | None",
) -> tuple[list[Any], str]:
    """Spawn a single SdkTeammate with the given neighbors and return
    (captured_options_list, teammate_id)."""
    captured: list[Any] = []

    class _Fake(_CapturingFakeClient):
        def __init__(self, options: Any = None) -> None:
            super().__init__(options)
            captured.append(options)

    monkeypatch.setattr(sdk_module, "ClaudeSDKClient", _Fake)

    broker = Broker()
    factory = _make_sdk_factory(captured)

    tid = await broker.spawn_teammate(
        role="test-role",
        name="test-role",
        factory=factory,
        neighbors=neighbors,
    )

    # Yield enough event-loop iterations for _run to reach the inbox-wait:
    #   iteration 1: _run builds opts, enters ClaudeSDKClient context (capture!)
    #   iteration 2: _liveness_poll_loop sets _poll_started; _run enters inbox.get()
    await asyncio.sleep(0.05)

    return captured, tid


# ---------------------------------------------------------------------------
# AT 1 — behavioral: no-out-edge teammate has send_to wired
# ---------------------------------------------------------------------------


class TestNoOutEdgesSendToWired:
    """AT 1: send_to is wired even when a teammate has no out-edges."""

    @pytest.mark.asyncio
    async def test_no_neighbors_send_to_in_mcp_servers(
        self, monkeypatch: Any
    ) -> None:
        """neighbors=None → _SEND_TO_MCP_SERVER_NAME present in mcp_servers."""
        captured, _tid = await _spawn_and_capture(monkeypatch, neighbors=None)

        assert captured, "ClaudeSDKClient was never instantiated"
        opts = captured[0]

        mcp_servers: dict = getattr(opts, "mcp_servers", None) or {}
        assert _SEND_TO_MCP_SERVER_NAME in mcp_servers, (
            f"Expected {_SEND_TO_MCP_SERVER_NAME!r} in mcp_servers; "
            f"got keys: {sorted(mcp_servers)}"
        )

    @pytest.mark.asyncio
    async def test_no_neighbors_send_to_in_allowed_tools(
        self, monkeypatch: Any
    ) -> None:
        """neighbors=None → _SEND_TO_TOOL_ID present in allowed_tools."""
        captured, _tid = await _spawn_and_capture(monkeypatch, neighbors=None)

        assert captured, "ClaudeSDKClient was never instantiated"
        opts = captured[0]

        allowed: list = getattr(opts, "allowed_tools", None) or []
        assert _SEND_TO_TOOL_ID in allowed, (
            f"Expected {_SEND_TO_TOOL_ID!r} in allowed_tools; got: {allowed}"
        )

    @pytest.mark.asyncio
    async def test_in_only_neighbors_send_to_wired(
        self, monkeypatch: Any
    ) -> None:
        """neighbors=[direction:in only] → send_to still wired (no out-edges)."""
        in_only = [
            {"direction": "in", "slot": "planner", "role": "planner", "mode": "gated"}
        ]
        captured, _tid = await _spawn_and_capture(monkeypatch, neighbors=in_only)

        assert captured, "ClaudeSDKClient was never instantiated"
        opts = captured[0]

        mcp_servers: dict = getattr(opts, "mcp_servers", None) or {}
        assert _SEND_TO_MCP_SERVER_NAME in mcp_servers, (
            f"Expected {_SEND_TO_MCP_SERVER_NAME!r} in mcp_servers; "
            f"got keys: {sorted(mcp_servers)}"
        )

        allowed: list = getattr(opts, "allowed_tools", None) or []
        assert _SEND_TO_TOOL_ID in allowed, (
            f"Expected {_SEND_TO_TOOL_ID!r} in allowed_tools; got: {allowed}"
        )

    @pytest.mark.asyncio
    async def test_send_to_tool_attr_set_on_instance(
        self, monkeypatch: Any
    ) -> None:
        """_build_send_to_mcp_server side-effect: self._send_to_tool is non-None
        even for a no-neighbors teammate (proving unconditional wiring)."""
        # Use __new__ + direct call to _build_send_to_mcp_server to test the
        # side-effect in isolation (complements the _run-level tests above).
        tm = SdkTeammate.__new__(SdkTeammate)
        tm.id = "t-at1-test"
        tm._broker = None
        tm._send_to_tool = None
        # Simulate no neighbors (the pre-D0 gate would have skipped this call)
        tm._build_send_to_mcp_server()
        assert tm._send_to_tool is not None, (
            "_send_to_tool should be set after _build_send_to_mcp_server(); "
            "got None — unconditional wiring not reached"
        )


# ---------------------------------------------------------------------------
# AT 2 — structural deletion-detector: _has_out_edges absent from sdk_teammate.py
# ---------------------------------------------------------------------------


class TestHasOutEdgesLiteralAbsent:
    """AT 2: the named literal '_has_out_edges' must not appear in sdk_teammate.py.

    This is a deletion-detector: if anyone re-introduces the spawn-time gate,
    the literal re-appears and this test fails immediately — even before any
    behavioral test runs.
    """

    def test_has_out_edges_literal_not_in_sdk_teammate_py(self) -> None:
        """grep-guard: _has_out_edges does not appear anywhere in sdk_teammate.py."""
        source_path = (
            Path(__file__).resolve().parent.parent
            / "claude_crew"
            / "sdk_teammate.py"
        )
        assert source_path.is_file(), (
            f"Source file not found at expected path: {source_path}"
        )
        content = source_path.read_text(encoding="utf-8")
        assert "_has_out_edges" not in content, (
            "Found NAMED LITERAL '_has_out_edges' in claude_crew/sdk_teammate.py — "
            "the spawn-time gate has been re-introduced. "
            "D0 requires unconditional send_to wiring; remove the conditional."
        )


# ---------------------------------------------------------------------------
# AT 3 — non-regression: with-out-edge teammate still has send_to wired
# ---------------------------------------------------------------------------


class TestWithOutEdgesSendToWired:
    """AT 3: the previously-conditional wiring still works when out-edges exist."""

    @pytest.mark.asyncio
    async def test_out_edge_neighbor_send_to_in_mcp_servers(
        self, monkeypatch: Any
    ) -> None:
        """neighbors with direction:out → _SEND_TO_MCP_SERVER_NAME in mcp_servers."""
        out_neighbor = [
            {
                "direction": "out",
                "slot": "reviewer",
                "role": "code-reviewer",
                "mode": "direct",
            }
        ]
        captured, _tid = await _spawn_and_capture(monkeypatch, neighbors=out_neighbor)

        assert captured, "ClaudeSDKClient was never instantiated"
        opts = captured[0]

        mcp_servers: dict = getattr(opts, "mcp_servers", None) or {}
        assert _SEND_TO_MCP_SERVER_NAME in mcp_servers, (
            f"Expected {_SEND_TO_MCP_SERVER_NAME!r} in mcp_servers; "
            f"got keys: {sorted(mcp_servers)}"
        )

    @pytest.mark.asyncio
    async def test_out_edge_neighbor_send_to_in_allowed_tools(
        self, monkeypatch: Any
    ) -> None:
        """neighbors with direction:out → _SEND_TO_TOOL_ID in allowed_tools."""
        out_neighbor = [
            {
                "direction": "out",
                "slot": "reviewer",
                "role": "code-reviewer",
                "mode": "direct",
            }
        ]
        captured, _tid = await _spawn_and_capture(monkeypatch, neighbors=out_neighbor)

        assert captured, "ClaudeSDKClient was never instantiated"
        opts = captured[0]

        allowed: list = getattr(opts, "allowed_tools", None) or []
        assert _SEND_TO_TOOL_ID in allowed, (
            f"Expected {_SEND_TO_TOOL_ID!r} in allowed_tools; got: {allowed}"
        )

    @pytest.mark.asyncio
    async def test_mixed_in_out_edges_send_to_wired(
        self, monkeypatch: Any
    ) -> None:
        """Mixed in+out neighbors → send_to wired (verifying idempotent dedup)."""
        mixed = [
            {"direction": "in", "slot": "planner", "role": "planner", "mode": "gated"},
            {"direction": "out", "slot": "reviewer", "role": "reviewer", "mode": "direct"},
        ]
        captured, _tid = await _spawn_and_capture(monkeypatch, neighbors=mixed)

        assert captured, "ClaudeSDKClient was never instantiated"
        opts = captured[0]

        mcp_servers: dict = getattr(opts, "mcp_servers", None) or {}
        assert _SEND_TO_MCP_SERVER_NAME in mcp_servers

        allowed: list = getattr(opts, "allowed_tools", None) or []
        assert _SEND_TO_TOOL_ID in allowed
        # Verify no duplicates in allowed_tools (dict.fromkeys dedup guarantee).
        assert allowed.count(_SEND_TO_TOOL_ID) == 1, (
            f"_SEND_TO_TOOL_ID appeared {allowed.count(_SEND_TO_TOOL_ID)} times in "
            f"allowed_tools; expected exactly 1 (dedup check)"
        )
