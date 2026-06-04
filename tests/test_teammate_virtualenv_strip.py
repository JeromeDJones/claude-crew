"""Tests for _venv_env_overrides and its wiring into _build_merged_env.

Covers AT 1-5 (impl layer) and AT 6 (integration via _patch_sdk_capture).
Construct teammates with the SdkTeammate(id, name, role, cwd, env) pattern
mirroring tests/test_per_teammate_env.py.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_teammate(env=None, cwd=None, **kwargs) -> SdkTeammate:
    """Construct a minimal SdkTeammate with sensible defaults."""
    return SdkTeammate(
        id="t1",
        name="tester",
        role="general",
        env=env,
        cwd=cwd,
        **kwargs,
    )


def _patch_sdk_capture(monkeypatch):
    """Patch ClaudeSDKClient so _run() captures opts and exits cleanly.

    Returns a dict that will have ``"env"`` populated with the value of
    opts_kwargs["env"] passed into ClaudeAgentOptions.
    """
    captured: dict[str, Any] = {}

    original_options_cls = sdk_module.ClaudeAgentOptions

    def _fake_options_cls(**kwargs):
        captured["opts_kwargs"] = dict(kwargs)
        captured["env"] = kwargs.get("env")
        obj = MagicMock()
        obj.__dict__.update(kwargs)
        return obj

    monkeypatch.setattr(sdk_module, "ClaudeAgentOptions", _fake_options_cls)

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
# AT 1 — Happy / project venv exists (impl layer)
# ---------------------------------------------------------------------------


def test_project_venv_exists_at1(monkeypatch, tmp_path):
    """AT 1: cwd with .venv dir → VIRTUAL_ENV points at that .venv."""
    (tmp_path / ".venv").mkdir()
    teammate = _make_teammate(cwd=str(tmp_path))
    env = teammate._build_merged_env()

    expected_venv = str(tmp_path / ".venv")
    assert env["VIRTUAL_ENV"] == expected_venv
    assert "claude-crew" not in env["VIRTUAL_ENV"]


# ---------------------------------------------------------------------------
# AT 2 — Sad / no project venv (impl layer)
# ---------------------------------------------------------------------------


def test_no_project_venv_at2(monkeypatch, tmp_path):
    """AT 2: cwd without .venv → VIRTUAL_ENV key is present with value ''."""
    teammate = _make_teammate(cwd=str(tmp_path))
    env = teammate._build_merged_env()

    assert "VIRTUAL_ENV" in env
    assert env["VIRTUAL_ENV"] == ""


# ---------------------------------------------------------------------------
# AT 3 — Caller override wins (impl layer)
# ---------------------------------------------------------------------------


def test_caller_override_wins_at3(monkeypatch, tmp_path):
    """AT 3: caller env VIRTUAL_ENV wins over computed venv path."""
    (tmp_path / ".venv").mkdir()
    teammate = _make_teammate(
        cwd=str(tmp_path), env={"VIRTUAL_ENV": "/custom/path"}
    )
    env = teammate._build_merged_env()

    assert env["VIRTUAL_ENV"] == "/custom/path"


# ---------------------------------------------------------------------------
# AT 4 — cwd is None → no override (impl layer)
# ---------------------------------------------------------------------------


def test_cwd_none_no_override_at4(monkeypatch):
    """AT 4: cwd=None → no VIRTUAL_ENV / VIRTUAL_ENV_PROMPT keys; env == CREW_DEFAULTS."""
    teammate = _make_teammate(cwd=None, env=None)
    env = teammate._build_merged_env()

    assert "VIRTUAL_ENV" not in env
    assert "VIRTUAL_ENV_PROMPT" not in env
    assert env == CREW_DEFAULTS


# ---------------------------------------------------------------------------
# AT 5 — VIRTUAL_ENV_PROMPT stripped (impl layer)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("venv_exists", [True, False])
def test_virtual_env_prompt_stripped_at5(monkeypatch, tmp_path, venv_exists):
    """AT 5: cwd set → VIRTUAL_ENV_PROMPT == '' regardless of .venv presence."""
    if venv_exists:
        (tmp_path / ".venv").mkdir()
    teammate = _make_teammate(cwd=str(tmp_path))
    env = teammate._build_merged_env()

    assert env["VIRTUAL_ENV_PROMPT"] == ""


# ---------------------------------------------------------------------------
# AT 6 — Integration / env reaches ClaudeAgentOptions (one layer above)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_reaches_sdk_options_at6(monkeypatch, tmp_path, caplog):
    """AT 6: _run() drives _build_merged_env → captured opts_kwargs["env"]
    has correct VIRTUAL_ENV; CREW_DEFAULTS WARN still fires for caller overrides."""
    (tmp_path / ".venv").mkdir()
    captured = _patch_sdk_capture(monkeypatch)

    # Caller overrides a CREW_DEFAULTS key to verify WARN still fires.
    caller_env = {"DISABLE_TELEMETRY": "0"}
    teammate = _make_teammate(
        cwd=str(tmp_path), env=caller_env
    )

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

    # VIRTUAL_ENV should point at the project's .venv, not claude-crew's.
    expected_venv = str(tmp_path / ".venv")
    assert env["VIRTUAL_ENV"] == expected_venv
    assert "claude-crew" not in env["VIRTUAL_ENV"]

    # CREW_DEFAULTS caller-wins WARN must still fire.
    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "DISABLE_TELEMETRY" in r.getMessage() for r in warn_records
    ), f"Expected WARN mentioning 'DISABLE_TELEMETRY'; got: {[r.getMessage() for r in warn_records]}"
