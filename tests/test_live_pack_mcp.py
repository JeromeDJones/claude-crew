"""Live SDK tests for Feature #17 (agent definition parity, mcpServers).

Gated by CLAUDE_CREW_LIVE_TESTS=1. These tests cost real money, require
working Claude credentials, AND require the named MCP server (atlassian)
to be registered in ``~/.claude.json`` — the operator's actual config.

Covers SC-5(d): a pack-declared string-form mcpServers entry produces
a reachable server in the teammate session, AND name-collision with
``~/.claude.json`` does not produce a "duplicate server" failure.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.factories import sdk_factory
from claude_crew.subagents._user_loader import _load_user_mcp_server_names


pytestmark = pytest.mark.skipif(
    os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1",
    reason="live API gated; set CLAUDE_CREW_LIVE_TESTS=1 to run",
)


async def _wait_for_lead(broker: Broker, count: int, timeout: float = 90.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(broker.get_messages(recipient=LEAD_ID)) >= count:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"timed out waiting for {count} lead messages; "
        f"got {len(broker.get_messages(recipient=LEAD_ID))}",
    )


@pytest.fixture
async def broker():
    b = Broker()
    yield b
    await b.shutdown_all()


def _has_named_server(name: str) -> bool:
    return name in _load_user_mcp_server_names()


@pytest.mark.skipif(
    not _has_named_server("atlassian"),
    reason="atlassian MCP server not registered in ~/.claude.json — skipping live mcpServers probe",
)
class TestPackMcpServersLive:
    """SC-5(d): pack mcpServers string-name produces a reachable server.

    The probe asks the teammate to list the MCP tools available to it.
    A pack-declared atlassian server should result in atlassian-prefixed
    tools being reachable (e.g., ``mcp__atlassian__getAccessibleAtlassianResources``).

    Name-collision: ~/.claude.json ALSO registers atlassian. The pack
    declaration adds it to ClaudeAgentOptions.mcp_servers; setting-sources
    auto-load loads it again from user-level config. The CLI is expected
    to dedupe by name. The test asserts NO 'duplicate server' / 'server
    registration failed' error surfaces, AND atlassian tools are
    reachable.
    """

    async def test_pack_string_name_produces_reachable_server(
        self, broker: Broker, tmp_path: Path,
    ) -> None:
        # Plant a project-level pack that declares the atlassian server.
        # We must monkeypatch project_root resolution since sdk_factory
        # bakes in Path.cwd() at MCP-server startup. For this live test
        # we'll use the existing default-pack mechanism by adding a
        # custom agent file under the test working directory's .claude/agents/.
        proj_agents = tmp_path / ".claude" / "agents"
        proj_agents.mkdir(parents=True)
        pack_file = proj_agents / "atlassian-probe.md"
        pack_file.write_text(
            "---\n"
            "description: Probe for live atlassian MCP via pack declaration.\n"
            "model: sonnet\n"
            "tools: [Read, Bash]\n"
            "mcpServers:\n  - atlassian\n"
            "---\n"
            "\n"
            "You probe MCP server availability. When asked, run any one\n"
            "atlassian MCP tool (e.g. atlassianUserInfo) and report the\n"
            "response shape. If no atlassian tools are available, say so\n"
            "explicitly.\n"
        )

        # Build the merged pack from the test project root, then construct
        # a custom factory closure to use it.
        from claude_crew.subagents._user_loader import build_merged_pack
        merged, role_ss, _ = build_merged_pack(project_root=tmp_path)
        assert "atlassian-probe" in merged, "pack should be discovered"
        assert merged["atlassian-probe"].mcpServers == ["atlassian"]

        # Spawn via direct factory (bypassing the closure that bakes home_dir/cwd).
        from claude_crew.sdk_teammate import SdkTeammate

        def custom_factory(id, name, role, **kwargs):
            return SdkTeammate(
                id=id, name=name, role=role,
                agents=merged,
                setting_sources=role_ss.get(role),
                **kwargs,
            )

        custom_factory.requires_auth = True

        tid = await broker.spawn_teammate(
            role="atlassian-probe", name=None, factory=custom_factory,
        )

        # Turn 1: ask for atlassian tool availability.
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload=(
                "List the names of MCP tools available to you that start "
                "with mcp__atlassian__. Report just the names, no extra prose."
            ),
        ))
        await _wait_for_lead(broker, 1, timeout=120.0)
        msgs = broker.get_messages(recipient=LEAD_ID)
        result = msgs[-1]
        text = (
            result.payload.get("text", "") if isinstance(result.payload, dict)
            else str(result.payload)
        )

        # Behavioral assertion (sentinel-tightened from "exactly one"):
        # at least one atlassian-prefixed tool is named in the response.
        # This proves the server reached the teammate AND no duplicate-
        # server-registration error short-circuited it.
        assert "mcp__atlassian__" in text, (
            f"expected atlassian-prefixed tool in teammate response; got: {text!r}"
        )

        # Negative scope: response does NOT contain duplicate-server errors
        # that would indicate the name-collision tie-break failed pathologically.
        for bad_substr in (
            "duplicate server", "already registered", "server registration failed",
        ):
            assert bad_substr.lower() not in text.lower(), (
                f"unexpected error substring {bad_substr!r} in response: {text!r}"
            )
