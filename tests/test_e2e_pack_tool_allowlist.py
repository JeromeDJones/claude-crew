"""BDD-style end-to-end tests for honor-pack-tools-allowlist + deny-MCP-by-default.

Spec: doc/ideas/honor-pack-tools-allowlist.md

Drives the public MCP-tool surface (`spawn_teammate`) end-to-end and asserts
on the captured `ClaudeAgentOptions` to prove the resolved tool / MCP surface
matches the contract. Each test is one Given/When/Then scenario.

These tests are written FIRST (BDD): they fail against current behavior and
pass after the implementation lands.

Contract under test:
  TOOLS
    - pack `tools: [A, B]`                       → opts.allowed_tools == [A, B]
    - pack `tools: [A, B]` + extra_tools=[C]     → opts.allowed_tools == [A, B, C] (union, dedup)
    - pack omits `tools:`                        → opts has NO allowed_tools key (inherit-all)
    - pack `tools: []` (explicit empty)          → opts.allowed_tools == []
  MCP
    - pack omits `mcpServers:` AND spawn omits   → opts.mcp_servers == {}  (explicit empty,
                                                    NOT absent — denies CLI auto-inherit)
    - pack `mcpServers: [name]`                  → opts.mcp_servers includes name's config
    - spawn mcp_servers=[name]                   → opts.mcp_servers includes name's config
    - pack [A] + spawn [B]                       → both present (union)
    - spawn mcp_servers=[unknown]                → unknown skipped, WARN logged, spawn ok
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


# --------------------------------------------------------------------------
# Helpers (mirrors test_e2e_pack_parity.py patterns).
# --------------------------------------------------------------------------


def _content_json(result: Any) -> Any:
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


def _write_pack_file(dir_: Path, filename: str, *, body: str = "Body.", **fm) -> Path:
    """Write a YAML-frontmatter pack file. Lists rendered flow-style for str-only,
    block-style for list-of-dicts (matches pack_parity helper)."""
    dir_.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    fm.setdefault("description", "Test agent.")
    fm.setdefault("model", "haiku")
    for k, v in fm.items():
        if isinstance(v, list):
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
        elif v is None:
            lines.append(f"{k}: null")
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


def _make_capturing_factory(merged_pack, role_ss, captured: dict, monkeypatch):
    """Build a factory that captures ClaudeAgentOptions via a fake SDK client.

    Accepts the new `mcp_servers` spawn-time grant kwarg (this is what the
    implementation will add)."""

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
            yield  # noqa: unreachable — mark as async generator

    monkeypatch.setattr(sdk_module, "ClaudeSDKClient", FakeCaptureSDKClient)

    def factory(
        id, name, role, *,
        model=None, effort=None, cwd=None, permission_mode=None,
        extra_tools=None, extra_skills=None,
        mcp_servers=None,  # the new spawn-time grant we're adding
    ):
        # extra_skills is consumed in factories.default_factory (mutates pack);
        # SdkTeammate doesn't take it as a constructor kwarg — drop on the floor
        # for this test factory's narrower role.
        return SdkTeammate(
            id=id, name=name, role=role,
            agents=merged_pack,
            cwd=cwd, permission_mode=permission_mode,
            extra_tools=extra_tools,
            mcp_servers_grant=mcp_servers,
            setting_sources=role_ss.get(role),
        )

    factory.requires_auth = False  # type: ignore[attr-defined]
    return factory


async def _spawn_and_drain_one_turn(
    broker: Broker, factory, role: str, **spawn_args
) -> dict:
    """Spawn a teammate via the MCP tool surface, drive one turn so the
    options-builder runs, return the spawn result. Caller inspects captured
    options via the captured dict the factory writes to."""
    async with create_connected_server_and_client_session(
        make_server(broker=broker, factory=factory)._mcp_server,
    ) as s:
        await s.initialize()
        raw = await s.call_tool("spawn_teammate", {"role": role, **spawn_args})
        if raw.isError:
            raise AssertionError(f"spawn_teammate errored: {raw.content[0].text!r}")
        spawn = _content_json(raw)
        tid = spawn["teammate_id"]
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender="lead", recipient=tid, timestamp=0.0, payload="hi",
        ))
        await broker.wait_for_lead_message(timeout=2.0)
        return spawn


# --------------------------------------------------------------------------
# TOOLS — pack `tools:` resolves to opts.allowed_tools.
# --------------------------------------------------------------------------


class TestPackToolsAsAllowlist:
    """The pack's `tools:` frontmatter becomes the teammate's allowed_tools.
    Mirrors Claude Code subagent semantics, applied at the top-level teammate."""

    async def test_pack_with_tools_yields_exact_allowed_tools(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Given a pack with `tools: [Read, Grep, Glob]`,
        When a teammate is spawned with that role,
        Then opts.allowed_tools == [Read, Grep, Glob] (no extras silently injected)."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_pack_file(
            proj / ".claude" / "agents", "explorer-like.md",
            tools=["Read", "Grep", "Glob"],
        )
        from claude_crew.subagents._user_loader import build_merged_pack
        merged, role_ss, _ = build_merged_pack(home_dir=home, project_root=proj)

        captured: dict = {}
        factory = _make_capturing_factory(merged, role_ss, captured, monkeypatch)
        await _spawn_and_drain_one_turn(Broker(), factory, "explorer-like")

        opts = captured["options"]
        # `tools` is the wire-level catalog (what the model sees);
        # `allowed_tools` is pre-approval (no permission prompt). Both must
        # reflect the pack list — see honor-pack-tools-allowlist.md.
        assert list(opts.tools) == ["Read", "Grep", "Glob"], (
            f"expected exact pack tools in catalog, got {opts.tools!r}"
        )
        assert list(opts.allowed_tools) == ["Read", "Grep", "Glob"], (
            f"expected exact pack tools in allowlist, got {opts.allowed_tools!r}"
        )

    async def test_pack_tools_unioned_with_extra_tools_dedup(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Given a pack with `tools: [Read, Grep]` and spawn `extra_tools=[Bash, Read]`,
        When the teammate is spawned,
        Then opts.allowed_tools is the deduped union [Read, Grep, Bash]
        (extras add on top; duplicates collapse)."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_pack_file(
            proj / ".claude" / "agents", "narrow.md",
            tools=["Read", "Grep"],
        )
        from claude_crew.subagents._user_loader import build_merged_pack
        merged, role_ss, _ = build_merged_pack(home_dir=home, project_root=proj)

        captured: dict = {}
        factory = _make_capturing_factory(merged, role_ss, captured, monkeypatch)
        await _spawn_and_drain_one_turn(
            Broker(), factory, "narrow", extra_tools=["Bash", "Read"],
        )

        opts = captured["options"]
        # Catalog (--tools) and allowlist (--allowedTools) both carry the union.
        assert list(opts.tools) == ["Read", "Grep", "Bash"], (
            f"expected deduped union in catalog, got {opts.tools!r}"
        )
        assert list(opts.allowed_tools) == ["Read", "Grep", "Bash"], (
            f"expected deduped union in allowlist, got {opts.allowed_tools!r}"
        )

    async def test_pack_without_tools_key_yields_inherit_all(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Given a pack that OMITS the `tools:` key entirely (inherit-all),
        When the teammate is spawned without extras,
        Then opts.tools is None (no --tools flag passed → CLI uses full
        default catalog → model sees all tools). Mirrors Claude Code
        subagent semantics."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_pack_file(
            proj / ".claude" / "agents", "wide-open.md",
            # tools: deliberately omitted
        )
        from claude_crew.subagents._user_loader import build_merged_pack
        merged, role_ss, _ = build_merged_pack(home_dir=home, project_root=proj)

        captured: dict = {}
        factory = _make_capturing_factory(merged, role_ss, captured, monkeypatch)
        await _spawn_and_drain_one_turn(Broker(), factory, "wide-open")

        opts = captured["options"]
        # ClaudeAgentOptions.tools default is None; we do NOT set --tools when
        # pack omits. The CLI receives no --tools flag → inherit-all default.
        assert opts.tools is None, (
            f"expected None (no --tools flag → inherit-all), got {opts.tools!r}"
        )

    async def test_pack_with_empty_tools_yields_true_no_tools_surface(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Given a pack with `tools: []` (explicit empty),
        When the teammate is spawned,
        Then opts.tools == [] → SDK passes `--tools ""` → CLI restricts the
        catalog to empty → the model sees NO tools at all (true no-tools
        surface, mirrors the subagent contract)."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_pack_file(
            proj / ".claude" / "agents", "no-tools.md",
            tools=[],
        )
        from claude_crew.subagents._user_loader import build_merged_pack
        merged, role_ss, _ = build_merged_pack(home_dir=home, project_root=proj)

        captured: dict = {}
        factory = _make_capturing_factory(merged, role_ss, captured, monkeypatch)
        await _spawn_and_drain_one_turn(Broker(), factory, "no-tools")

        opts = captured["options"]
        assert opts.tools == [], (
            f"expected empty list (--tools '' → empty catalog), got {opts.tools!r}"
        )

    async def test_default_factory_extra_tools_no_double_grant(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Production path (default_factory) flows `extra_tools` two ways into
        SdkTeammate: (a) baked into the patched AgentDefinition.tools via the
        pack_tools ∪ extra_tools union at the factory level, AND (b) passed
        again as `extra_tools=` to SdkTeammate constructor. The downstream
        union+dedup in options assembly MUST collapse the duplication so
        `allowed_tools` shows each tool exactly once."""
        from claude_crew import factories
        from claude_crew.subagents._user_loader import build_merged_pack

        # conftest forces CLAUDE_CREW_TEAMMATE_MODE=stub autouse; clear it so
        # default_factory returns the sdk-mode factory (where the double-flow
        # under test lives). Without this, default_factory returns stub_factory
        # and SdkTeammate's options-builder never runs.
        monkeypatch.delenv("CLAUDE_CREW_TEAMMATE_MODE", raising=False)

        home = tmp_path / "home"
        proj = tmp_path / "proj"
        # Use the bundled explorer (tools: [Read, Grep, Glob]) so the test
        # exercises the actual production merge cascade, not a synthetic pack.
        merged, role_ss, bodies = build_merged_pack(home_dir=home, project_root=proj)
        assert "explorer" in merged

        captured: dict = {}

        class FakeCaptureSDKClient:
            def __init__(self, options=None):
                if options is not None:
                    captured["options"] = options

            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def query(self, prompt, session_id=None): pass
            async def receive_response(self):
                return
                yield  # noqa
        monkeypatch.setattr(sdk_module, "ClaudeSDKClient", FakeCaptureSDKClient)

        # Drive through the REAL default_factory (not a hand-rolled test factory).
        prod_factory = factories.default_factory(
            home_dir=home, project_root=proj,
        )
        # default_factory requires auth in production; bypass for the fake SDK path.
        prod_factory.requires_auth = False  # type: ignore[attr-defined]

        broker = Broker()
        async with create_connected_server_and_client_session(
            make_server(broker=broker, factory=prod_factory)._mcp_server,
        ) as s:
            await s.initialize()
            spawn = _content_json(await s.call_tool(
                "spawn_teammate",
                {"role": "explorer", "extra_tools": ["Bash", "Read"]},
            ))
            tid = spawn["teammate_id"]
            await broker.send(Envelope(
                id=new_message_id(), seq=0,
                sender="lead", recipient=tid, timestamp=0.0, payload="hi",
            ))
            await broker.wait_for_lead_message(timeout=2.0)

        opts = captured["options"]
        # Pack: [Read, Grep, Glob]. Extras: [Bash, Read]. Union, dedup, order
        # preserved: pack-first then extras. Catalog AND allowlist both reflect
        # the union (so wire prompt restricts AND tools are pre-approved).
        assert list(opts.tools) == ["Read", "Grep", "Glob", "Bash"], (
            f"expected deduped union catalog via production factory, "
            f"got {opts.tools!r}"
        )
        assert list(opts.allowed_tools) == ["Read", "Grep", "Glob", "Bash"]
        assert len(opts.tools) == len(set(opts.tools))

    async def test_pack_tools_restrict_subprocess_cli_args(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Wire-level guarantee: when a pack declares `tools: [A, B, C]`,
        the CLI subprocess command line MUST receive `--tools A,B,C` (the
        catalog restriction). Without --tools, the model sees the full default
        catalog regardless of --allowedTools — verified empirically on
        2026-05-26 when the first cut of this fix set only allowed_tools and
        the Qwen wire prompt stayed at 107 KB / 35 tools.

        Asserts on the ARGV the SDK would build for the CLI, so the test
        survives changes in how the model receives tool definitions."""
        from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
        from claude_agent_sdk.types import ClaudeAgentOptions

        # Build the same options shape SdkTeammate would for the bundled explorer.
        opts = ClaudeAgentOptions(
            tools=["Read", "Grep", "Glob"],
            allowed_tools=["Read", "Grep", "Glob"],
            mcp_servers={},
        )
        # _build_command is internal; we drive a transport just to capture its
        # argv. cli_path is normally resolved during connect() — set directly
        # so we can call _build_command without spawning the real subprocess.
        transport = SubprocessCLITransport(options=opts, prompt="hi")
        transport._cli_path = "/usr/bin/true"  # placeholder; never executed
        cmd = transport._build_command()
        # Find the --tools flag and its value.
        assert "--tools" in cmd, (
            f"--tools (catalog) MUST be passed to the CLI to restrict the wire "
            f"prompt; got cmd={cmd!r}"
        )
        idx = cmd.index("--tools")
        tools_arg = cmd[idx + 1]
        assert set(tools_arg.split(",")) == {"Read", "Grep", "Glob"}, (
            f"--tools value mismatch; got {tools_arg!r}"
        )
        # --allowedTools should ALSO appear (pre-approval, no permission prompt).
        assert "--allowedTools" in cmd

    async def test_bundled_explorer_role_ships_with_tight_tools(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The bundled `explorer` pack declares `tools: [Read, Grep, Glob]`.
        This proves the ship-default does the right thing — a downstream user
        installing claude-crew and spawning explorer gets the narrow surface."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        from claude_crew.subagents._user_loader import build_merged_pack
        merged, role_ss, _ = build_merged_pack(home_dir=home, project_root=proj)
        assert "explorer" in merged, "explorer must be a bundled role"

        captured: dict = {}
        factory = _make_capturing_factory(merged, role_ss, captured, monkeypatch)
        await _spawn_and_drain_one_turn(Broker(), factory, "explorer")

        opts = captured["options"]
        assert list(opts.tools) == ["Read", "Grep", "Glob"]
        assert list(opts.allowed_tools) == ["Read", "Grep", "Glob"]


# --------------------------------------------------------------------------
# MCP — deny by default; opt-in via pack OR spawn.
# --------------------------------------------------------------------------


class TestMcpDenyByDefault:
    """No pack-declared and no spawn-granted MCP servers → no MCP at all.
    Always sets `mcp_servers` on opts (even to `{}`) to deny the CLI's
    `~/.claude.json` auto-inheritance."""

    async def test_no_pack_mcp_and_no_spawn_grant_yields_empty_dict(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Given a pack without `mcpServers:` AND a ~/.claude.json that
        registers several MCP servers (the operator has them configured),
        When a teammate is spawned without spawn-time `mcp_servers`,
        Then opts.mcp_servers == {} (NOT absent / NOT inherited from
        ~/.claude.json — explicit deny so the CLI doesn't fall back to its
        own discovery)."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        # Operator HAS MCP servers registered; pre-fix we'd silently inherit them.
        _write_claude_json(home, {
            "atlassian": {"type": "http", "url": "https://example.com"},
            "notion": {"type": "http", "url": "https://notion.example.com"},
        })
        _write_pack_file(
            proj / ".claude" / "agents", "clean.md",
            tools=["Read"],  # tools given so the test isn't about tools
            # no mcpServers
        )
        from claude_crew.subagents._user_loader import build_merged_pack
        merged, role_ss, _ = build_merged_pack(home_dir=home, project_root=proj)

        # spawn-time _resolve_mcp_servers consults ~/.claude.json with home_dir=None;
        # monkeypatch _load_user_mcp_servers to return the planted servers.
        monkeypatch.setattr(
            sdk_module, "_load_user_mcp_servers",
            lambda home_dir=None: {
                "atlassian": {"type": "http", "url": "https://example.com"},
                "notion": {"type": "http", "url": "https://notion.example.com"},
            },
        )

        captured: dict = {}
        factory = _make_capturing_factory(merged, role_ss, captured, monkeypatch)
        await _spawn_and_drain_one_turn(Broker(), factory, "clean")

        opts = captured["options"]
        assert opts.mcp_servers == {}, (
            f"expected explicit empty dict (deny inheritance), got {opts.mcp_servers!r}"
        )

    async def test_pack_declared_mcp_resolves_to_opts(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Given a pack with `mcpServers: [atlassian]` and ~/.claude.json
        registering atlassian,
        When the teammate spawns,
        Then opts.mcp_servers contains atlassian's resolved config."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_claude_json(home, {
            "atlassian": {"type": "http", "url": "https://example.com"},
        })
        _write_pack_file(
            proj / ".claude" / "agents", "with-mcp.md",
            tools=["Read"],
            mcpServers=["atlassian"],
        )
        from claude_crew.subagents._user_loader import build_merged_pack
        merged, role_ss, _ = build_merged_pack(home_dir=home, project_root=proj)

        monkeypatch.setattr(
            sdk_module, "_load_user_mcp_servers",
            lambda home_dir=None: {
                "atlassian": {"type": "http", "url": "https://example.com"},
            },
        )

        captured: dict = {}
        factory = _make_capturing_factory(merged, role_ss, captured, monkeypatch)
        await _spawn_and_drain_one_turn(Broker(), factory, "with-mcp")

        opts = captured["options"]
        assert opts.mcp_servers == {
            "atlassian": {"type": "http", "url": "https://example.com"},
        }

    async def test_spawn_grant_attaches_named_mcp_server(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Given a pack with no mcpServers and a ~/.claude.json registering notion,
        When the spawn call passes `mcp_servers=["notion"]`,
        Then opts.mcp_servers contains notion's resolved config."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_claude_json(home, {
            "notion": {"type": "http", "url": "https://notion.example.com"},
        })
        _write_pack_file(
            proj / ".claude" / "agents", "spawn-grant.md",
            tools=["Read"],
        )
        from claude_crew.subagents._user_loader import build_merged_pack
        merged, role_ss, _ = build_merged_pack(home_dir=home, project_root=proj)

        monkeypatch.setattr(
            sdk_module, "_load_user_mcp_servers",
            lambda home_dir=None: {
                "notion": {"type": "http", "url": "https://notion.example.com"},
            },
        )

        captured: dict = {}
        factory = _make_capturing_factory(merged, role_ss, captured, monkeypatch)
        await _spawn_and_drain_one_turn(
            Broker(), factory, "spawn-grant", mcp_servers=["notion"],
        )

        opts = captured["options"]
        assert opts.mcp_servers == {
            "notion": {"type": "http", "url": "https://notion.example.com"},
        }

    async def test_pack_mcp_unioned_with_spawn_grant(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Given a pack `mcpServers: [atlassian]` and spawn `mcp_servers=[notion]`,
        When the teammate spawns,
        Then opts.mcp_servers contains BOTH (union — spawn cannot drop pack-declared,
        pack cannot drop spawn-granted)."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_claude_json(home, {
            "atlassian": {"type": "http", "url": "https://example.com"},
            "notion": {"type": "http", "url": "https://notion.example.com"},
        })
        _write_pack_file(
            proj / ".claude" / "agents", "union.md",
            tools=["Read"],
            mcpServers=["atlassian"],
        )
        from claude_crew.subagents._user_loader import build_merged_pack
        merged, role_ss, _ = build_merged_pack(home_dir=home, project_root=proj)

        monkeypatch.setattr(
            sdk_module, "_load_user_mcp_servers",
            lambda home_dir=None: {
                "atlassian": {"type": "http", "url": "https://example.com"},
                "notion": {"type": "http", "url": "https://notion.example.com"},
            },
        )

        captured: dict = {}
        factory = _make_capturing_factory(merged, role_ss, captured, monkeypatch)
        await _spawn_and_drain_one_turn(
            Broker(), factory, "union", mcp_servers=["notion"],
        )

        opts = captured["options"]
        assert opts.mcp_servers == {
            "atlassian": {"type": "http", "url": "https://example.com"},
            "notion": {"type": "http", "url": "https://notion.example.com"},
        }

    async def test_spawn_mcp_unknown_name_skipped_with_warn(
        self, tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Given spawn `mcp_servers=["does-not-exist"]` and an empty registry,
        When the teammate spawns,
        Then the unknown name is skipped (not in opts), a WARN is logged,
        and spawn still succeeds (failure-tolerant resolution)."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_claude_json(home, {})  # empty registry
        _write_pack_file(
            proj / ".claude" / "agents", "spawn-unknown.md",
            tools=["Read"],
        )
        from claude_crew.subagents._user_loader import build_merged_pack
        merged, role_ss, _ = build_merged_pack(home_dir=home, project_root=proj)

        monkeypatch.setattr(
            sdk_module, "_load_user_mcp_servers", lambda home_dir=None: {},
        )

        captured: dict = {}
        factory = _make_capturing_factory(merged, role_ss, captured, monkeypatch)

        with caplog.at_level(logging.WARNING):
            spawn = await _spawn_and_drain_one_turn(
                Broker(), factory, "spawn-unknown",
                mcp_servers=["does-not-exist"],
            )
        assert "teammate_id" in spawn  # spawn succeeded

        opts = captured["options"]
        assert opts.mcp_servers == {}, (
            f"unknown name must not appear in opts, got {opts.mcp_servers!r}"
        )
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("does-not-exist" in m for m in warn_msgs), (
            f"expected WARN naming the unknown server; got {warn_msgs!r}"
        )
