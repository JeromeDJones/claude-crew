"""End-to-end integration tests for Feature #17 (agent definition parity).

Exercises the full pipeline through the public API: pack file on disk →
``build_merged_pack`` → factory → ``spawn_teammate`` MCP tool → captured
``ClaudeAgentOptions``. Distinct from the per-task tests in
``test_pack_loader.py`` / ``test_user_loader.py`` / ``test_sdk_teammate.py``
which exercise individual components.

Covers:
- SC-9 happy path: pack with mcpServers (mixed forms) + memory → all
  flow through correctly; load-time WARNs for unresolvable names; spawn-
  time WARN for memory; INFO breadcrumb for inline-dict pass-through
- SC-4 sad path: invalid permission_mode rejected at MCP boundary;
  broker untouched
- SC-11 sad path: shadow-drop WARN when project pack overwrites user pack
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from claude_crew import sdk_teammate as sdk_module
from claude_crew.broker import Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.server import make_server
from claude_crew.sdk_teammate import SdkTeammate


def _content_json(result: Any) -> Any:
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


def _write_pack_file(dir_: Path, filename: str, *, body: str = "Body.", **fm) -> Path:
    """Write an agent pack file with the given frontmatter dict."""
    dir_.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    fm.setdefault("description", "Test agent.")
    fm.setdefault("model", "haiku")
    fm.setdefault("tools", ["Read"])
    for k, v in fm.items():
        if isinstance(v, list):
            # YAML flow-style for lists of strings; nested dicts use block style
            if v and isinstance(v[0], dict):
                lines.append(f"{k}:")
                for entry in v:
                    first = True
                    for ek, ev in entry.items():
                        prefix = "  - " if first else "    "
                        lines.append(f"{prefix}{ek}: {ev}")
                        first = False
            else:
                inner = ", ".join(str(x) for x in v)
                lines.append(f"{k}: [{inner}]")
        else:
            lines.append(f"{k}: {v}")
    lines.extend(["---", "", body, ""])
    p = dir_ / filename
    p.write_text("\n".join(lines))
    return p


def _write_claude_json(home: Path, mcp_servers: dict | None) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    cfg: dict = {}
    if mcp_servers is not None:
        cfg["mcpServers"] = mcp_servers
    p = home / ".claude.json"
    p.write_text(json.dumps(cfg))
    return p


def _make_factory_for(merged_pack, role_ss, captured: dict, monkeypatch):
    """Build a factory that uses the given merged_pack and captures
    ClaudeAgentOptions via a Fake SDK client."""

    class FakeCaptureSDKClient:
        def __init__(self, options=None):
            if options is not None:
                captured["options"] = options

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

    def factory(
        id, name, role, *, model=None, effort=None, cwd=None, permission_mode=None, extra_tools=None, extra_skills=None
    ):
        return SdkTeammate(
            id=id, name=name, role=role,
            agents=merged_pack,
            cwd=cwd, permission_mode=permission_mode,
            setting_sources=role_ss.get(role),
        )

    factory.requires_auth = False  # type: ignore[attr-defined]
    return factory


class TestPackParityHappyPath:
    """SC-9: full lifecycle — pack with both new fields → spawn → opts."""

    async def test_full_lifecycle_pack_to_options(
        self, tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        home = tmp_path / "home"
        proj = tmp_path / "proj"

        # Plant ~/.claude.json with one MCP server registered.
        _write_claude_json(home, {
            "atlassian": {"type": "http", "url": "https://example.com"},
        })

        # Plant a project pack declaring both forms + memory.
        _write_pack_file(
            proj / ".claude" / "agents", "test-role.md",
            mcpServers=[
                "atlassian",
                {"type": "stdio", "name": "local-x", "command": "uv"},
            ],
            memory="project",
        )

        from claude_crew.subagents._user_loader import build_merged_pack
        with caplog.at_level(logging.WARNING, logger="claude_crew.subagents.loader"):
            merged, role_ss, _ = build_merged_pack(home_dir=home, project_root=proj)

        # Load-time: no unknown-mcp-server WARN (atlassian resolves).
        load_warns = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any(
            "unknown mcpServers" in m and "atlassian" in m for m in load_warns
        ), f"atlassian should resolve; got {load_warns}"

        # Confirm the merged pack carries both new fields.
        assert "test-role" in merged
        td = merged["test-role"]
        assert td.mcpServers == [
            "atlassian", {"type": "stdio", "name": "local-x", "command": "uv"},
        ]
        assert td.memory == "project"

        # Drive through MCP tool dispatch.
        captured: dict = {}
        # _resolve_mcp_servers in _run is called with home_dir=None, so for the
        # spawn-time resolve we monkeypatch _load_user_mcp_servers.
        monkeypatch.setattr(
            sdk_module, "_load_user_mcp_servers",
            lambda home_dir=None: {
                "atlassian": {"type": "http", "url": "https://example.com"},
            },
        )
        factory = _make_factory_for(merged, role_ss, captured, monkeypatch)
        broker = Broker()

        with caplog.at_level(logging.INFO):
            async with create_connected_server_and_client_session(
                make_server(broker=broker, factory=factory)._mcp_server,
            ) as s:
                await s.initialize()
                spawn = _content_json(await s.call_tool(
                    "spawn_teammate", {"role": "test-role"},
                ))
                tid = spawn["teammate_id"]
                # Drive one turn so _run reaches options-builder.
                await broker.send(Envelope(
                    id=new_message_id(), seq=0,
                    sender="lead", recipient=tid, timestamp=0.0, payload="hi",
                ))
                # Wait for the lead to receive the result envelope.
                await broker.wait_for_lead_message(timeout=2.0)

        opts = captured["options"]
        # Both forms reach mcp_servers, name stripped from inline dict.
        assert opts.mcp_servers == {
            "atlassian": {"type": "http", "url": "https://example.com"},
            "local-x": {"type": "stdio", "command": "uv"},
        }
        # Memory WARN fired (D-8).
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "test-role" in m and "memory" in m and "project" in m
            for m in warn_msgs
        ), f"expected memory WARN for test-role; got {warn_msgs}"

        # INFO breadcrumb fired for the inline-dict pass-through (D-13).
        info_msgs = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert any(
            "passing through inline dict" in m and "local-x" in m and "stdio" in m
            for m in info_msgs
        ), f"expected D-13 breadcrumb for local-x; got {info_msgs}"


class TestPackParitySadPaths:
    """SC-4 + SC-11: invalid permission_mode + shadow-drop visibility."""

    async def test_invalid_permission_mode_blocked_at_boundary(
        self, tmp_path: Path,
    ) -> None:
        broker = Broker()
        async with create_connected_server_and_client_session(
            make_server(broker=broker)._mcp_server,
        ) as s:
            await s.initialize()
            # Crew empty before the call.
            crew_before = _content_json(await s.call_tool("list_crew", {}))
            assert crew_before.get("teammates", []) == []

            r = await s.call_tool(
                "spawn_teammate",
                {"role": "planner", "permission_mode": "ultraviolet"},
            )
            assert r.isError is True
            text = r.content[0].text
            assert "permission_mode" in text and "ultraviolet" in text

            # Validation prevented broker.spawn_teammate.
            crew_after = _content_json(await s.call_tool("list_crew", {}))
            assert crew_after.get("teammates", []) == []

    def test_shadow_drop_warn_observed_in_full_merge(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """SC-11: project shadow that drops mcpServers a user pack set → WARN."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_claude_json(home, {"atlassian": {"type": "http"}})

        # User-level pack declares mcpServers.
        _write_pack_file(
            home / ".claude" / "agents", "myrole.md",
            mcpServers=["atlassian"],
        )
        # Project pack shadows the same key without mcpServers.
        _write_pack_file(
            proj / ".claude" / "agents", "myrole.md",
            description="project version",
        )

        from claude_crew.subagents._user_loader import build_merged_pack
        with caplog.at_level(logging.WARNING, logger="claude_crew.subagents.loader"):
            merged, _, _ = build_merged_pack(home_dir=home, project_root=proj)

        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "myrole" in m and "mcpServers" in m and "drops" in m
            and "project-level" in m
            for m in warn_msgs
        ), f"expected shadow-drop WARN naming role + field + layer; got {warn_msgs}"

        # And the merged result reflects the project-level whole-replacement.
        assert merged["myrole"].mcpServers is None
        assert merged["myrole"].description == "project version"
