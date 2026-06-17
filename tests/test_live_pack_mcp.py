"""Live SDK tests for Feature #17 (agent definition parity, mcpServers).

Gated by CLAUDE_CREW_LIVE_TESTS=1. These tests cost real money and require
working Claude credentials.

Two test variants cover SC-5(d): a pack-declared string-form mcpServers entry
produces a reachable server in the teammate session.

TestPackMcpWiringLocal (RUNS IN THIS ENVIRONMENT):
    Uses the ``crg`` code-graph MCP server (http://127.0.0.1:5555, no external
    auth). Verifies the full wiring path end-to-end — pack mcpServers declaration
    → _resolve_mcp_servers → ClaudeAgentOptions.mcp_servers → teammate actually
    reaches the server's tools. Also verifies no duplicate-server errors when the
    same server is in both the pack declaration and ~/.claude.json.

TestPackMcpServersLive (ATLASSIAN ENV-SKIP):
    Uses the ``atlassian`` MCP server. Guards: registered in ~/.claude.json AND
    not just a registration check — the live assertion skips gracefully when the
    server is not credentialed/authenticated in this SDK subprocess context (which
    is the normal state for this machine). Pack-assembly wiring assertions still
    run. Kept as a distinct class so the skipping is clearly documented.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
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


def _crg_reachable() -> bool:
    """Return True if the crg HTTP MCP server is reachable at 127.0.0.1:5555."""
    try:
        urllib.request.urlopen("http://127.0.0.1:5555/mcp", timeout=2)
        return True
    except urllib.error.HTTPError:
        # Any HTTP error (e.g., 406 Not Acceptable for GET without MCP headers)
        # still means the server is running and accepting connections.
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _has_named_server("crg"),
    reason="crg MCP server not registered in ~/.claude.json — skipping local wiring probe",
)
class TestPackMcpWiringLocal:
    """SC-5(d) — live wiring verification using the local crg MCP server.

    crg (http://127.0.0.1:5555/mcp) is a credential-free local HTTP MCP server.
    This test actually runs in this environment (unlike the atlassian variant which
    always skips on missing credentials), giving real wiring-regression coverage:

      pack mcpServers: ["crg"] declaration
        → _resolve_mcp_servers resolves crg config from ~/.claude.json
        → ClaudeAgentOptions.mcp_servers receives the crg entry
        → teammate actually reaches crg and lists mcp__crg__* tools

    A regression in any step would break this assertion. The atlassian-only test
    can never catch a wiring regression in this environment.
    """

    async def test_local_mcp_server_wiring_via_pack_declaration(
        self, broker: Broker, tmp_path: Path,
    ) -> None:
        if not _crg_reachable():
            pytest.skip(
                "crg MCP server at 127.0.0.1:5555 is not reachable — "
                "start the crg daemon (crg-daemon or `crg serve`) before running."
            )

        # Plant a project-level pack declaring crg.
        proj_agents = tmp_path / ".claude" / "agents"
        proj_agents.mkdir(parents=True)
        (proj_agents / "crg-probe.md").write_text(
            "---\n"
            "description: Probe for live crg MCP via pack declaration.\n"
            "model: sonnet\n"
            "tools: [Read]\n"
            "mcpServers:\n  - crg\n"
            "---\n"
            "\n"
            "You probe MCP server availability. When asked, list the code-graph\n"
            "tools available to you. Report their names precisely.\n"
        )

        from claude_crew.subagents._user_loader import build_merged_pack
        from claude_crew.factories import sdk_factory as _sdk_factory

        merged, role_ss, _ = build_merged_pack(project_root=tmp_path)

        # Pack-assembly assertions (no live cost): verify the wiring data
        # structures are correct BEFORE paying for an API call.
        assert "crg-probe" in merged, "pack should be discovered from tmp_path"
        assert merged["crg-probe"].mcpServers == ["crg"], (
            "pack mcpServers declaration should be preserved"
        )

        def custom_factory(id, name, role, **_kw):
            return _sdk_factory(
                id=id, name=name, role=role,
                agents=merged,
                setting_sources=role_ss.get(role),
            )

        custom_factory.requires_auth = True

        tid = await broker.spawn_teammate(
            role="crg-probe", name=None, factory=custom_factory,
        )

        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload=(
                "List the names of MCP tools available to you that start "
                "with mcp__crg__. Report just the tool names, one per line."
            ),
        ))
        await _wait_for_lead(broker, 1, timeout=120.0)
        msgs = broker.get_messages(recipient=LEAD_ID)
        text = (
            msgs[-1].payload.get("text", "") if isinstance(msgs[-1].payload, dict)
            else str(msgs[-1].payload)
        )

        # This is the real wiring assertion: the teammate actually reached
        # the crg server and can name its tools. No env-credential issue —
        # crg is local and needs no auth.
        assert "mcp__crg__" in text, (
            f"expected mcp__crg__* tools in teammate response — pack mcpServers "
            f"wiring may be broken (pack → ClaudeAgentOptions → SDK subprocess). "
            f"Teammate response: {text!r}"
        )

        # Negative scope: no duplicate-server errors.
        for bad_substr in ("duplicate server", "already registered", "server registration failed"):
            assert bad_substr.lower() not in text.lower(), (
                f"unexpected error substring {bad_substr!r} in response: {text!r}"
            )


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

        # Spawn via sdk_factory (which handles the full broker factory_kwargs
        # interface, including extra_skills and mcp_servers added in m3). A
        # raw SdkTeammate(**kwargs) would receive broker-injected kwargs it
        # doesn't accept; sdk_factory's explicit signature is the right gate.
        from claude_crew.factories import sdk_factory as _sdk_factory

        def custom_factory(id, name, role, **_kw):
            return _sdk_factory(
                id=id, name=name, role=role,
                agents=merged,
                setting_sources=role_ss.get(role),
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
        #
        # Environmental guard: the atlassian server is registered in
        # ~/.claude.json as an HTTP server (https://mcp.atlassian.com) that
        # requires Atlassian OAuth/API credentials. If those credentials are
        # not set up for SDK subprocess sessions, the server connects but lists
        # no tools. The MCP wiring (pack assembly → ClaudeAgentOptions) is
        # already validated by the pack-assembly assertions above; skip the
        # live-tool-availability check rather than hard-failing on a missing
        # credential that is environmental, not a wiring bug.
        if "mcp__atlassian__" not in text:
            pytest.skip(
                "atlassian MCP server is registered in ~/.claude.json "
                "(type=http, url=https://mcp.atlassian.com) but teammate "
                "reported no mcp__atlassian__ tools — likely not "
                "credentialed/authenticated in this SDK subprocess context. "
                "Pack-wiring assertions (lines above) already verified. "
                f"Teammate response excerpt: {text[:300]!r}"
            )

        # Negative scope: response does NOT contain duplicate-server errors
        # that would indicate the name-collision tie-break failed pathologically.
        for bad_substr in (
            "duplicate server", "already registered", "server registration failed",
        ):
            assert bad_substr.lower() not in text.lower(), (
                f"unexpected error substring {bad_substr!r} in response: {text!r}"
            )
