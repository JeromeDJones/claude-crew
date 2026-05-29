"""Tests for per-teammate env merge in SdkTeammate (AT 1, 2, 3)
and MCP spawn_teammate env validation + preset expansion (AT 7, 8, 9).

Strategy: monkey-patch ClaudeSDKClient with a fake that captures the
ClaudeAgentOptions passed to it, then assert on opts_kwargs["env"] before
the SDK subprocess is ever spawned. No network, no llama.cpp, no ccr.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from claude_crew import sdk_teammate as sdk_module
from claude_crew.broker import Broker, TeammateInfo
from claude_crew.sdk_teammate import CREW_DEFAULTS, SdkTeammate
from claude_crew.server import make_server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_teammate(env=None, **kwargs) -> SdkTeammate:
    """Construct a minimal SdkTeammate with sensible defaults."""
    return SdkTeammate(
        id="t1",
        name="tester",
        role="general",
        env=env,
        **kwargs,
    )


def _patch_sdk_capture(monkeypatch):
    """Patch ClaudeSDKClient so _run() captures opts and exits cleanly.

    Returns a dict that will have ``"options"`` populated when the
    ClaudeAgentOptions constructor is called, AND ``"env"`` populated
    with the value of opts_kwargs["env"] passed into ClaudeAgentOptions.
    """
    captured: dict[str, Any] = {}

    # Capture opts at ClaudeAgentOptions construction time, not at
    # ClaudeSDKClient construction time — the merge happens just before
    # ClaudeAgentOptions(**opts_kwargs) is called.
    original_options_cls = sdk_module.ClaudeAgentOptions

    def _fake_options_cls(**kwargs):
        captured["opts_kwargs"] = dict(kwargs)
        captured["env"] = kwargs.get("env")
        # Return a real options object (or a stub) — we only care about capture.
        obj = MagicMock()
        obj.__dict__.update(kwargs)
        return obj

    monkeypatch.setattr(sdk_module, "ClaudeAgentOptions", _fake_options_cls)

    # Make ClaudeSDKClient a fake async context manager that shuts down
    # immediately so _run() exits cleanly after building opts.
    class _FakeClient:
        def __init__(self, options=None):
            captured["options"] = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(sdk_module, "ClaudeSDKClient", _FakeClient)
    return captured


# ---------------------------------------------------------------------------
# AT 1 — no override preserves crew defaults exactly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_env_override_preserves_crew_defaults(monkeypatch):
    """AT 1: SdkTeammate(env=None) → opts_kwargs['env'] == CREW_DEFAULTS exactly."""
    captured = _patch_sdk_capture(monkeypatch)
    teammate = _make_teammate(env=None)

    # Drive _run() for one iteration then cancel it.
    task = asyncio.create_task(teammate._run())
    # Allow the event loop to run enough to build opts_kwargs.
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert "env" in captured, "ClaudeAgentOptions was not called"
    env = captured["env"]
    assert env == CREW_DEFAULTS, (
        f"Expected env == CREW_DEFAULTS, got {env!r}"
    )
    # Explicitly check all keys and values — no extras.
    assert env == {
        "CLAUDE_CREW_UI_PORT": "0",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "DISABLE_TELEMETRY": "1",
    }


# ---------------------------------------------------------------------------
# AT 2 — three-key caller env merged with crew defaults, caller keys present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_key_caller_env_merged_with_crew_defaults(monkeypatch):
    """AT 2: three caller keys merge with two crew defaults → five-key dict."""
    caller_env = {
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:3456",
        "ANTHROPIC_API_KEY": "sk-local",
        "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
    }
    captured = _patch_sdk_capture(monkeypatch)
    teammate = _make_teammate(env=caller_env)

    task = asyncio.create_task(teammate._run())
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert "env" in captured
    env = captured["env"]

    # All three caller keys must be present with caller-supplied values.
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:3456"
    assert env["ANTHROPIC_API_KEY"] == "sk-local"
    assert env["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0"

    # All crew-default keys must also be present.
    assert env["CLAUDE_CREW_UI_PORT"] == "0"
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert env["DISABLE_TELEMETRY"] == "1"

    # Total: 3 caller + 3 crew defaults = 6 keys.
    assert len(env) == 6


# ---------------------------------------------------------------------------
# AT 3 — caller overrides a crew-default key: caller wins + WARN logged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_caller_override_wins_with_warn(monkeypatch, caplog):
    """AT 3: caller overrides CLAUDE_CREW_UI_PORT → caller value wins; WARN logged."""
    captured = _patch_sdk_capture(monkeypatch)
    teammate = _make_teammate(env={"CLAUDE_CREW_UI_PORT": "9000"})

    with caplog.at_level(logging.WARNING, logger="claude_crew.sdk_teammate"):
        task = asyncio.create_task(teammate._run())
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert "env" in captured
    env = captured["env"]

    # Caller wins.
    assert env["CLAUDE_CREW_UI_PORT"] == "9000", (
        f"Expected caller value '9000', got {env['CLAUDE_CREW_UI_PORT']!r}"
    )
    # Other crew default unaffected.
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"

    # WARN must name the overridden key.
    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "CLAUDE_CREW_UI_PORT" in r.getMessage() for r in warn_records
    ), f"Expected WARN mentioning 'CLAUDE_CREW_UI_PORT'; got: {[r.getMessage() for r in warn_records]}"


# ---------------------------------------------------------------------------
# Validation: non-string env value raises TypeError at __init__
# ---------------------------------------------------------------------------


def test_non_string_env_value_raises_type_error():
    """SdkTeammate(env={'FOO': 123}) must raise TypeError immediately."""
    with pytest.raises(TypeError, match="str"):
        _make_teammate(env={"FOO": 123})


# ---------------------------------------------------------------------------
# Helpers for MCP-boundary tests (AT 7, 8, 9)
# ---------------------------------------------------------------------------


def _make_server_with_spy_broker():
    """Build a make_server() instance with a spy broker that records spawn calls."""


    spawn_calls: list[dict[str, Any]] = []

    class _SpyBroker(Broker):
        async def spawn_teammate(self, **kwargs):  # type: ignore[override]
            spawn_calls.append(dict(kwargs))
            # Return a fake teammate id without actually spawning.
            tid = "spy-t-0001"
            resolved_name = kwargs.get("name") or kwargs.get("role", "spy")
            role = kwargs.get("role", "spy")
            # Populate _info so list_crew() can find the entry (used by server.py).
            self._info[tid] = TeammateInfo(  # type: ignore[attr-defined]
                id=tid,
                name=resolved_name,
                role=role,
                spawned_at=time.time(),
                alive=True,
            )
            return tid

    broker = _SpyBroker()
    # Provide a no-op factory (broker spy intercepts before factory is called).
    factory = MagicMock(return_value=MagicMock())
    server = make_server(broker=broker, factory=factory)
    return server, broker, spawn_calls


async def _call_spawn_tool(server, **kwargs):
    """Call the spawn_teammate MCP tool registered on server."""
    # FastMCP stores tools in _tool_manager; call via the registered handler.
    tool = server._tool_manager.get_tool("spawn_teammate")
    return await tool.fn(**kwargs)


# ---------------------------------------------------------------------------
# AT 7 — non-string env value rejected at MCP boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_non_string_value_rejected_at_mcp_boundary():
    """AT 7: spawn_teammate(env={'FOO': 123}) → ToolError; broker NOT called."""

    server, broker, spawn_calls = _make_server_with_spy_broker()

    with pytest.raises(ToolError, match="FOO"):
        await _call_spawn_tool(server, role="builder", env={"FOO": 123})

    assert spawn_calls == [], "broker.spawn_teammate must not be called on validation error"


# ---------------------------------------------------------------------------
# AT 8 — custom_endpoint preset expands to the three-var Anthropic-shape env
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_endpoint_preset_expands_to_three_vars():
    """AT 8: spawn_teammate(custom_endpoint={"base_url": …}) →
    broker receives the three preset vars."""
    server, broker, spawn_calls = _make_server_with_spy_broker()

    await _call_spawn_tool(
        server,
        role="builder",
        custom_endpoint={"base_url": "http://127.0.0.1:3456"},
    )

    assert len(spawn_calls) == 1
    env = spawn_calls[0].get("env")
    assert env is not None, "broker must receive env kwarg"
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:3456"
    assert env["ANTHROPIC_API_KEY"] == "sk-custom-no-key-required"
    assert env["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0"


# ---------------------------------------------------------------------------
# AT 9 — custom_endpoint preset + explicit env: explicit key wins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_endpoint_with_explicit_env_explicit_wins():
    """AT 9: custom_endpoint preset AND env={'ANTHROPIC_API_KEY': 'sk-override'}
    → explicit wins."""
    server, broker, spawn_calls = _make_server_with_spy_broker()

    await _call_spawn_tool(
        server,
        role="builder",
        custom_endpoint={"base_url": "http://127.0.0.1:3456"},
        env={"ANTHROPIC_API_KEY": "sk-override"},
    )

    assert len(spawn_calls) == 1
    env = spawn_calls[0].get("env")
    assert env is not None
    # Explicit override wins.
    assert env["ANTHROPIC_API_KEY"] == "sk-override"
    # Other preset keys retain preset values.
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:3456"
    assert env["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0"
