"""Tests for per-teammate env merge in SdkTeammate (AT 1, 2, 3).

Strategy: monkey-patch ClaudeSDKClient with a fake that captures the
ClaudeAgentOptions passed to it, then assert on opts_kwargs["env"] before
the SDK subprocess is ever spawned. No network, no llama.cpp, no ccr.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_crew import sdk_teammate as sdk_module
from claude_crew.sdk_teammate import CREW_DEFAULTS, SdkTeammate


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
    # Explicitly check both keys and values — no extras.
    assert env == {
        "CLAUDE_CREW_UI_PORT": "0",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
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

    # Both crew-default keys must also be present.
    assert env["CLAUDE_CREW_UI_PORT"] == "0"
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"

    # Total: 5 keys.
    assert len(env) == 5


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
