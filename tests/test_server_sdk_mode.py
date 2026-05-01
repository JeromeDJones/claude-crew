"""Integration-level tests for the MCP server in SDK mode.

The in-memory MCP client drives the actual server, with the SDK factory
wired to FakeSDKClient via a monkeypatch on ClaudeSDKClient.

Plus subprocess-level tests that confirm the auth gate fires when the
server is launched without credentials.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from claude_crew import factories, sdk_teammate as sdk_module
from claude_crew.server import make_server
from tests.fakes.sdk import FakeSDKClient, text_response


def _content_json(result: Any) -> Any:
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


def _client_with_sdk_mode():
    return create_connected_server_and_client_session(
        make_server(factory=factories.sdk_factory),
    )


# ---------- in-memory harness driven through MCP tools ----------


class TestSDKModeIntegration:
    async def test_spawn_send_get_messages_with_fake(self, monkeypatch) -> None:
        # Provide a single fake whose canned responses cover up to 4 turns.
        fake = FakeSDKClient(scripted_responses=[
            text_response("4"),
            text_response("paris"),
        ])
        monkeypatch.setattr(sdk_module, "ClaudeSDKClient", lambda options=None: fake)

        async with _client_with_sdk_mode() as s:
            await s.initialize()
            spawn = _content_json(await s.call_tool(
                "spawn_teammate", {"role": "planner"},
            ))
            tid = spawn["teammate_id"]

            send = _content_json(await s.call_tool(
                "send_to", {"teammate_id": tid, "payload": "what is 2+2?"},
            ))
            assert "message_id" in send

            for _ in range(50):
                got = _content_json(await s.call_tool("get_messages", {}))
                if got["messages"]:
                    break
                await asyncio.sleep(0.02)
            assert got["messages"]
            msg = got["messages"][0]
            assert msg["payload"] == {"text": "4", "from": "planner"}

    async def test_list_crew_with_sdk_teammates(self, monkeypatch) -> None:
        fake = FakeSDKClient(scripted_responses=[])
        monkeypatch.setattr(sdk_module, "ClaudeSDKClient", lambda options=None: fake)

        async with _client_with_sdk_mode() as s:
            await s.initialize()
            await s.call_tool("spawn_teammate", {"role": "planner"})
            await s.call_tool("spawn_teammate", {"role": "builder"})
            crew = _content_json(await s.call_tool("list_crew", {}))
            roles = sorted(t["role"] for t in crew["teammates"])
            assert roles == ["builder", "planner"]

    async def test_kill_teammate_in_sdk_mode(self, monkeypatch) -> None:
        fake = FakeSDKClient(scripted_responses=[])
        monkeypatch.setattr(sdk_module, "ClaudeSDKClient", lambda options=None: fake)

        async with _client_with_sdk_mode() as s:
            await s.initialize()
            spawn = _content_json(await s.call_tool(
                "spawn_teammate", {"role": "r"},
            ))
            tid = spawn["teammate_id"]
            kill = _content_json(await s.call_tool(
                "kill_teammate", {"teammate_id": tid},
            ))
            assert kill == {"ok": True}
            send = _content_json(await s.call_tool(
                "send_to", {"teammate_id": tid, "payload": "x"},
            ))
            assert send.get("error") == "teammate_dead"

    async def test_spawn_with_invalid_permission_mode_returns_tool_error(self) -> None:
        """Feature #17 SC-4: invalid permission_mode at MCP boundary → ToolError surfaces.

        Routes through FastMCP tool dispatch (not a direct closure call) so the
        ToolError actually crosses the protocol boundary. FastMCP catches the
        exception and the result has isError=True with our message visible in
        the content text (FastMCP prefixes "Error executing tool spawn_teammate:"
        per mcp/server/fastmcp/tools/base.py:117 — the prefix is observable but
        the substantive content of the message is what we assert on)."""
        async with _client_with_sdk_mode() as s:
            await s.initialize()
            result = await s.call_tool(
                "spawn_teammate", {"role": "planner", "permission_mode": "superuser"},
            )
            assert result.isError is True
            assert result.content
            text = result.content[0].text
            # Substantive content: field name, bad value, accepted set.
            assert "permission_mode" in text
            assert "superuser" in text
            for valid in ("default", "acceptEdits", "plan", "bypassPermissions",
                          "dontAsk", "auto"):
                assert valid in text, f"expected accepted value {valid!r} in error: {text!r}"
            # Validation fired before broker.spawn_teammate — crew length unchanged.
            # (Sentinel H-2: a fresh-broker "no planner" assertion is vacuously true;
            # snapshot-then-compare proves the validation prevented spawn rather than
            # spawn never being attempted in any test run.)
            crew = _content_json(await s.call_tool("list_crew", {}))
            assert len(crew.get("teammates", [])) == 0, (
                f"validation should prevent broker.spawn_teammate; got crew={crew}"
            )

    async def test_spawn_with_valid_permission_mode_passes(self, monkeypatch) -> None:
        """Feature #17 SC-4: valid permission_mode passes validation cleanly."""
        # Reuse the FakeCaptureSDKClient pattern from the next test to avoid real spawn.
        captured: dict = {}

        class FakeCaptureSDKClient:
            def __init__(self, options=None):
                if options is not None:
                    captured["permission_mode"] = getattr(options, "permission_mode", None)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def query(self, prompt, session_id=None):
                pass

            async def receive_response(self):
                return
                yield

        monkeypatch.setattr(sdk_module, "ClaudeSDKClient", FakeCaptureSDKClient)
        async with _client_with_sdk_mode() as s:
            await s.initialize()
            result = await s.call_tool(
                "spawn_teammate", {"role": "planner", "permission_mode": "plan"},
            )
            assert result.isError is False or result.isError is None

    async def test_spawn_with_none_permission_mode_skips_validation(self, monkeypatch) -> None:
        """Feature #17 SC-4: permission_mode=None → no validation triggered, falls back to pack."""

        class FakeCaptureSDKClient:
            def __init__(self, options=None):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def query(self, prompt, session_id=None):
                pass

            async def receive_response(self):
                return
                yield

        monkeypatch.setattr(sdk_module, "ClaudeSDKClient", FakeCaptureSDKClient)
        async with _client_with_sdk_mode() as s:
            await s.initialize()
            result = await s.call_tool("spawn_teammate", {"role": "planner"})
            assert result.isError is False or result.isError is None

    async def test_spawn_with_cwd_and_permission_mode_reaches_options(self, monkeypatch) -> None:
        """Full chain: MCP server → broker → factory → SdkTeammate → ClaudeAgentOptions.
        Monkeypatch ClaudeSDKClient to capture options that were passed."""
        captured_options = {}

        class FakeCaptureSDKClient:
            def __init__(self, options=None):
                if options is not None:
                    captured_options.update({
                        "cwd": getattr(options, "cwd", None),
                        "permission_mode": getattr(options, "permission_mode", None),
                    })

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def query(self, prompt, session_id=None):
                pass

            async def receive_response(self):
                return
                yield  # Make it async generator

        monkeypatch.setattr(sdk_module, "ClaudeSDKClient", FakeCaptureSDKClient)

        async with _client_with_sdk_mode() as s:
            await s.initialize()
            spawn = _content_json(await s.call_tool(
                "spawn_teammate", {
                    "role": "planner",
                    "cwd": "/tmp/proj",
                    "permission_mode": "plan",
                },
            ))
            tid = spawn["teammate_id"]
            assert tid is not None
            # Check that the options were captured with the right values
            assert captured_options.get("cwd") == "/tmp/proj"
            assert captured_options.get("permission_mode") == "plan"


# ---------- subprocess: auth gate ----------


def _spawn_claude_crew(env: dict[str, str], timeout: float = 5.0) -> subprocess.CompletedProcess:
    """Launch the console script with the given environment, no stdin."""
    return subprocess.run(
        ["uv", "run", "claude-crew"],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestAuthGateSubprocess:
    @pytest.mark.skipif(
        os.environ.get("CLAUDE_CREW_SKIP_SUBPROCESS_TESTS") == "1",
        reason="subprocess tests disabled by env var",
    )
    def test_sdk_mode_without_auth_exits_2(self, tmp_path) -> None:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            # SDK mode is the production default; do not set the env var.
        }
        # Strip any inherited auth.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

        result = _spawn_claude_crew(env)
        assert result.returncode == 2, (
            f"expected exit 2, got {result.returncode}; "
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        assert "claude login" in result.stderr
        assert "ANTHROPIC_API_KEY" in result.stderr

    @pytest.mark.skipif(
        os.environ.get("CLAUDE_CREW_SKIP_SUBPROCESS_TESTS") == "1",
        reason="subprocess tests disabled by env var",
    )
    def test_stub_mode_without_auth_serves(self, tmp_path) -> None:
        """Stub mode does not need auth; the process should not exit on
        startup. We start it, give it a moment, then send EOF."""
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "CLAUDE_CREW_TEAMMATE_MODE": "stub",
        }
        env.pop("ANTHROPIC_API_KEY", None)

        proc = subprocess.Popen(
            ["uv", "run", "claude-crew"],
            cwd=str(Path(__file__).resolve().parent.parent),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            # Give the server a beat to start. If it was going to exit
            # on the auth check, it would have already.
            time.sleep(0.5)
            assert proc.poll() is None, (
                f"server exited prematurely; "
                f"returncode={proc.returncode} "
                f"stderr={proc.stderr.read() if proc.stderr else ''}"
            )
        finally:
            proc.stdin.close() if proc.stdin else None
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
