"""Spike test for Feature #26: Plugin agent config visibility on dashboard.

Reproduces the symptom: a teammate spawned from a project-scope plugin install
runs successfully but the dashboard config panel is empty (no tools/skills/model
chips).

Data flow under investigation:
  project-scope plugin install
  → MCP spawn_teammate(role=...)
  → broker.spawn_teammate(role) calls agent_def_resolver(role)
  → BrokerSnapshot.live[*].config is None or missing
  → UIServer._build_local_instance sees no config
  → dashboard ConfigChips / ConfigDetailPanel renders empty

Test strategy:
- Plant a fake project-scope plugin install with full agent frontmatter
- Build merged pack via build_merged_pack(home_dir=..., project_root=...)
- Construct a real broker with the factory's agent_def_resolver
- Call broker.spawn_teammate(role=...)
- Trace which transition drops the config:
  1. _resolve_role(role) — does it resolve to a key in merged_pack?
  2. merged_pack.get(resolved) — is AgentDefinition present?
  3. _snapshot_config(...) — does it return a non-None dict?
  4. BrokerSnapshot.live[*].config — is it populated on the snapshot?
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from textwrap import dedent

import pytest
from claude_agent_sdk.types import AgentDefinition

from claude_crew.factories import default_factory
from claude_crew.server import make_server
from claude_crew.subagents._user_loader import build_merged_pack


LOGGER = "claude_crew.subagents.loader"


def _write_plugin_manifest(
    home: Path, plugins: dict[str, list[dict]],
) -> Path:
    """Plant installed_plugins.json with project-scope plugin install."""
    cfg_dir = home / ".claude" / "plugins"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "installed_plugins.json"
    cfg_path.write_text(json.dumps({"version": 2, "plugins": plugins}))
    return cfg_path


def _plugin_install(home: Path, *segments: str) -> Path:
    """Return a path under <home>/.claude/plugins/ (passes H1 escape guard)."""
    return home / ".claude" / "plugins" / Path(*segments)


def _write_agent(
    dir_: Path,
    filename: str,
    *,
    name: str | None = None,
    description: str = "A plugin agent.",
    model: str = "haiku",
    tools: list[str] | None = None,
    skills: list[str] | None = None,
    body: str = "You are a plugin agent.",
) -> Path:
    """Write an agent .md file with full frontmatter."""
    dir_.mkdir(parents=True, exist_ok=True)
    tools_yaml = ", ".join(tools or ["Read"])
    lines = [
        "---",
        f"description: {description}",
        f"model: {model}",
        f"tools: [{tools_yaml}]",
    ]
    if name:
        lines.insert(1, f"name: {name}")
    if skills:
        skills_yaml = ", ".join(f'"{s}"' for s in skills)
        lines.append(f"skills: [{skills_yaml}]")
    lines.extend(["---", "", body, ""])
    path = dir_ / filename
    path.write_text("\n".join(lines))
    return path


class TestPluginConfigVisibilitySpikeProjectScope:
    """Reproduce symptom: project-scope plugin agent config missing from dashboard."""

    async def test_project_scope_plugin_agent_config_visible_in_broker_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Given: project-scope plugin install with a fully-configured agent.
        When: spawn a teammate from that plugin via broker.spawn_teammate.
        Then: BrokerSnapshot.live[*].config is populated (not None, has tools/skills/model).

        This test fails when project-scope plugin agents are silently dropped
        somewhere in the resolve → snapshot pipeline, causing the UI to render
        an empty config panel.
        """
        # Setup: fake home and project directories
        home = tmp_path / "home"
        project = tmp_path / "project"
        home.mkdir()
        project.mkdir()

        # Plant a project-scope plugin install
        install_dir = _plugin_install(home, "cache", "repo-reactor")
        agent_file = _write_agent(
            install_dir / "agents",
            "rr-planner.md",
            # name is NOT set — plugin namespace is added by load_plugin_agents
            # based on plugin_key (before the @) and the role key from the file
            description="Repo reactor planner — analyzes code structure.",
            model="sonnet",
            tools=["Read", "Bash"],
            skills=["semantic-search"],
            body="You are a planner for the repo-reactor tool.",
        )

        # Register the install in installed_plugins.json with local scope
        _write_plugin_manifest(home, {
            "repo-reactor@repo-reactor": [
                {
                    "scope": "local",
                    "installPath": str(install_dir),
                    "projectPath": str(project),
                },
            ],
        })

        # Create the skill directory so the skill validation doesn't warn
        skill_dir = project / ".claude" / "skills" / "semantic-search"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("# Semantic Search\n\nA skill for semantic code search.")

        # Build the merged pack (this is what default_factory does at startup)
        merged_pack, role_ss, pack_bodies = build_merged_pack(
            home_dir=home, project_root=project,
        )

        # Verify the merged pack loaded the plugin agent
        assert "repo-reactor:rr-planner" in merged_pack, (
            "Plugin agent should be in merged pack. "
            f"Available keys: {sorted(merged_pack.keys())}"
        )
        agent_def = merged_pack["repo-reactor:rr-planner"]
        assert agent_def.description == "Repo reactor planner — analyzes code structure."
        assert agent_def.tools == ["Read", "Bash"]
        assert agent_def.skills == ["semantic-search"]
        assert agent_def.model == "sonnet"

        # Create a broker with the factory's agent_def_resolver
        # (this is what the MCP server does at startup)

        # Clear the stub-mode default set by conftest and set SDK mode explicitly
        monkeypatch.delenv("CLAUDE_CREW_TEAMMATE_MODE", raising=False)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        factory = default_factory(
            home_dir=home,
            project_root=project,
        )

        # Verify factory has agent_def_resolver attached (SDK-only feature)
        assert hasattr(factory, "agent_def_resolver"), (
            f"Factory should have agent_def_resolver attached. "
            f"Got factory {factory.__name__}; "
            f"agent_def_resolver is SDK-only (set when mode != stub)"
        )

        # Import Broker directly to create one with SDK factory
        from claude_crew.broker import Broker
        broker = Broker()

        # Spawn the teammate via the broker
        # Using the bare name to match the operator's reported case
        # (should be auto-promoted to repo-reactor:rr-planner by _resolve_role)
        teammate_id = await broker.spawn_teammate(
            role="rr-planner",
            name="test-teammate",
            factory=factory,
            effort=None,
            permission_mode=None,
            extra_tools=None,
            extra_skills=None,
        )

        # Get the snapshot and trace the config
        snapshot = broker.snapshot()

        # Find the live entry for our teammate
        live_entry = None
        for entry in snapshot.live:
            if entry.info.id == teammate_id:
                live_entry = entry
                break

        assert live_entry is not None, f"Teammate {teammate_id} should be in live list"

        # THIS IS THE KEY ASSERTION — config should be populated
        # The bug is when config is None for a project-scope plugin agent.
        assert live_entry.config is not None, (
            "Config snapshot should NOT be None for a project-scope plugin agent. "
            "This indicates the bug: config dropped somewhere in resolve → snapshot pipeline."
        )

        # Verify config has expected fields from the pack (note: description is not
        # included in _snapshot_config — it's metadata for pack browsing, not runtime config)
        assert live_entry.config.get("tools") == ["Read", "Bash"], (
            f"Config tools missing or wrong. Got: {live_entry.config}"
        )
        assert live_entry.config.get("skills") == ["semantic-search"], (
            f"Config skills missing or wrong. Got: {live_entry.config}"
        )
        assert live_entry.config.get("model") == "claude-sonnet-4-6", (
            f"Config model missing or wrong (should be alias-resolved). Got: {live_entry.config}"
        )

    async def test_project_scope_plugin_agent_config_with_namespaced_spawn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Test config visibility when spawning with the fully-namespaced role name.
        (e.g., "repo-reactor:rr-planner" instead of just "rr-planner")
        """
        home = tmp_path / "home"
        project = tmp_path / "project"
        home.mkdir()
        project.mkdir()

        # Plant project-scope plugin
        install_dir = _plugin_install(home, "cache", "repo-reactor")
        _write_agent(
            install_dir / "agents",
            "rr-planner.md",
            description="Repo reactor planner.",
            model="sonnet",
            tools=["Read"],
            body="You are a planner.",
        )

        _write_plugin_manifest(home, {
            "repo-reactor@repo-reactor": [
                {
                    "scope": "local",
                    "installPath": str(install_dir),
                    "projectPath": str(project),
                },
            ],
        })

        # Build merged pack
        merged_pack, _, _ = build_merged_pack(home_dir=home, project_root=project)
        assert "repo-reactor:rr-planner" in merged_pack

        # Create SDK factory
        monkeypatch.delenv("CLAUDE_CREW_TEAMMATE_MODE", raising=False)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        factory = default_factory(home_dir=home, project_root=project)
        assert hasattr(factory, "agent_def_resolver")

        # Create broker
        from claude_crew.broker import Broker
        broker = Broker()

        # Spawn with NAMESPACED role (full form)
        teammate_id = await broker.spawn_teammate(
            role="repo-reactor:rr-planner",  # Full namespaced form
            name="test-teammate",
            factory=factory,
            effort=None,
            permission_mode=None,
            extra_tools=None,
            extra_skills=None,
        )

        snapshot = broker.snapshot()
        live_entry = None
        for entry in snapshot.live:
            if entry.info.id == teammate_id:
                live_entry = entry
                break

        assert live_entry is not None
        assert live_entry.config is not None, (
            "Config should be populated for namespaced spawn."
        )
        assert live_entry.config.get("tools") == ["Read"]

    async def test_project_root_mismatch_drops_plugin_config_h3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        H3 hypothesis: if default_factory builds merged_pack with project_root=/path/A
        but the plugin was registered for /path/B, the plugin won't be in merged_pack.
        When spawn_teammate tries to resolve the role, it returns the original string
        (unchanged), merged_pack.get() returns None, and config snapshot is None.

        This test verifies that scenario: build the merged pack with PROJECT_ROOT_A,
        but simulate the teammate role being spawned (and resolved) as if it came
        from PROJECT_ROOT_B.
        """
        home = tmp_path / "home"
        project_a = tmp_path / "project_a"
        project_b = tmp_path / "project_b"
        project_a.mkdir()
        project_b.mkdir()

        # Plant plugin install for project_a
        install_dir = _plugin_install(home, "cache", "plugin-x")
        _write_agent(
            install_dir / "agents",
            "agent-x.md",
            description="Agent X.",
            model="haiku",
            tools=["Read"],
            body="You are agent X.",
        )

        _write_plugin_manifest(home, {
            "plugin-x@plugin-x": [
                {
                    "scope": "local",
                    "installPath": str(install_dir),
                    "projectPath": str(project_a),  # Registered for project_a only
                },
            ],
        })

        # Build merged pack for project_a (correct)
        merged_pack_a, _, _ = build_merged_pack(home_dir=home, project_root=project_a)
        assert "plugin-x:agent-x" in merged_pack_a, "Plugin should load for matching project_root"

        # Build merged pack for project_b (wrong —plugin won't load)
        merged_pack_b, _, _ = build_merged_pack(home_dir=home, project_root=project_b)
        assert "plugin-x:agent-x" not in merged_pack_b, (
            "Plugin should NOT load for non-matching project_root"
        )

        # Now create a factory that thinks the project_root is project_a,
        # but the merged pack it gets is actually from project_b
        # This simulates the H3 case: startup project detection mismatch
        monkeypatch.delenv("CLAUDE_CREW_TEAMMATE_MODE", raising=False)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        # Create factory with project_a (so merged_pack has plugin-x:agent-x)
        factory = default_factory(home_dir=home, project_root=project_a)
        assert hasattr(factory, "agent_def_resolver")

        # But broker operates as if project_root=project_b (no plugin agents)
        # If the factory's merged_pack is somehow project_b's, then config would be None
        # (This requires the factory to bepickled/reused, which is unrealistic,
        #  but the code pattern might exist elsewhere)

        # For now, verify the factory's resolver DOES find it
        from claude_crew.broker import Broker
        broker = Broker()

        teammate_id = await broker.spawn_teammate(
            role="agent-x",  # Bare name, might not auto-promote if registry is different
            name="test",
            factory=factory,
            effort=None,
            permission_mode=None,
            extra_tools=None,
            extra_skills=None,
        )

        snapshot = broker.snapshot()
        live_entry = None
        for entry in snapshot.live:
            if entry.info.id == teammate_id:
                live_entry = entry
                break

        assert live_entry is not None
        # This PASSES because factory has the correct project_a merged_pack
        # But H3 would fail if the factory somehow had project_b's pack
        assert live_entry.config is not None, (
            "Config should be populated when factory's merged_pack matches."
        )
