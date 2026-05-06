"""Tests for installed-plugin agent discovery in the user/project loader.

Covers ``load_plugin_agents`` and the plugin layer in ``build_merged_pack``:
the loader walks ``~/.claude/plugins/installed_plugins.json``, resolves
each install's ``installPath/agents/`` directory, and merges the results
into the precedence cascade between ``default`` and ``user``.

Best-effort behavior: missing/malformed JSON, missing dirs, unknown
scopes — all silent (no exception, empty result), matching the
SC-7-style robustness of the existing user-config loaders.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from textwrap import dedent

import pytest
from claude_agent_sdk.types import AgentDefinition

from claude_crew.subagents._user_loader import (
    _read_installed_plugins,
    build_merged_pack,
    load_plugin_agents,
)


LOGGER = "claude_crew.subagents.loader"


def _write_plugin_manifest(
    home: Path, plugins: dict[str, list[dict]],
) -> Path:
    """Plant ``<home>/.claude/plugins/installed_plugins.json`` with the given map."""
    cfg_dir = home / ".claude" / "plugins"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "installed_plugins.json"
    cfg_path.write_text(json.dumps({"version": 2, "plugins": plugins}))
    return cfg_path


def _plugin_install(home: Path, *segments: str) -> Path:
    """Return a path rooted under ``<home>/.claude/plugins/`` (passes the
    H1 escape guard) made up of the given segments. Does not create it."""
    return home / ".claude" / "plugins" / Path(*segments)


def _write_agent(
    dir_: Path,
    filename: str,
    *,
    description: str = "A plugin-shipped agent.",
    model: str = "haiku",
    tools: list[str] | None = None,
    extra: str = "",
    body: str = "You are a plugin agent.",
) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    tools_yaml = ", ".join(tools or ["Read"])
    lines = [
        "---",
        f"description: {description}",
        f"model: {model}",
        f"tools: [{tools_yaml}]",
    ]
    if extra:
        lines.append(extra.rstrip("\n"))
    lines.extend(["---", "", body, ""])
    path = dir_ / filename
    path.write_text("\n".join(lines))
    return path


# -----------------------------------------------------------------------------
# _read_installed_plugins — JSON parsing, scope filtering
# -----------------------------------------------------------------------------


class TestReadInstalledPlugins:
    """Manifest parsing, scope filter, and graceful degradation."""

    def test_missing_manifest_returns_empty(self, tmp_path: Path) -> None:
        assert _read_installed_plugins(tmp_path, tmp_path) == []

    def test_malformed_json_returns_empty(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / ".claude" / "plugins"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "installed_plugins.json").write_text("{not json")
        assert _read_installed_plugins(tmp_path, tmp_path) == []

    def test_top_level_not_dict_returns_empty(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / ".claude" / "plugins"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "installed_plugins.json").write_text("[1, 2, 3]")
        assert _read_installed_plugins(tmp_path, tmp_path) == []

    def test_missing_plugins_key_returns_empty(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / ".claude" / "plugins"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "installed_plugins.json").write_text('{"version": 2}')
        assert _read_installed_plugins(tmp_path, tmp_path) == []

    def test_user_scope_install_included(self, tmp_path: Path) -> None:
        install = _plugin_install(tmp_path, "cache", "myplugin")
        install.mkdir(parents=True)
        _write_plugin_manifest(tmp_path, {
            "myplugin@m": [{"scope": "user", "installPath": str(install)}],
        })
        pairs = _read_installed_plugins(tmp_path, tmp_path)
        assert pairs == [("myplugin@m", install / "agents")]

    def test_local_scope_install_only_when_project_path_matches(
        self, tmp_path: Path,
    ) -> None:
        install = _plugin_install(tmp_path, "cache", "p")
        install.mkdir(parents=True)
        right_project = tmp_path / "matching"
        right_project.mkdir()
        wrong_project = tmp_path / "other"
        wrong_project.mkdir()

        _write_plugin_manifest(tmp_path, {
            "p@m": [
                {
                    "scope": "local",
                    "installPath": str(install),
                    "projectPath": str(right_project),
                },
            ],
        })
        # project_root matches: included
        assert _read_installed_plugins(tmp_path, right_project) == [
            ("p@m", install / "agents"),
        ]
        # project_root doesn't match: excluded
        assert _read_installed_plugins(tmp_path, wrong_project) == []

    def test_unknown_scope_skipped(self, tmp_path: Path) -> None:
        install = _plugin_install(tmp_path, "cache", "p")
        install.mkdir(parents=True)
        _write_plugin_manifest(tmp_path, {
            "p@m": [{"scope": "team", "installPath": str(install)}],
        })
        assert _read_installed_plugins(tmp_path, tmp_path) == []

    def test_missing_install_path_skipped(self, tmp_path: Path) -> None:
        _write_plugin_manifest(tmp_path, {
            "p@m": [{"scope": "user"}],
            "q@m": [{"scope": "user", "installPath": ""}],
            "r@m": [{"scope": "user", "installPath": 42}],
        })
        assert _read_installed_plugins(tmp_path, tmp_path) == []

    def test_multiple_plugins_sorted_by_key(self, tmp_path: Path) -> None:
        a_install = _plugin_install(tmp_path, "a")
        b_install = _plugin_install(tmp_path, "b")
        a_install.mkdir(parents=True)
        b_install.mkdir(parents=True)
        _write_plugin_manifest(tmp_path, {
            "zeta@m": [{"scope": "user", "installPath": str(b_install)}],
            "alpha@m": [{"scope": "user", "installPath": str(a_install)}],
        })
        pairs = _read_installed_plugins(tmp_path, tmp_path)
        assert [k for k, _ in pairs] == ["alpha@m", "zeta@m"]

    def test_multiple_installs_per_plugin_all_resolved(
        self, tmp_path: Path,
    ) -> None:
        """One plugin with both user and local-scope installs → both included
        when project_root matches."""
        user_install = _plugin_install(tmp_path, "user-install")
        local_install = _plugin_install(tmp_path, "local-install")
        user_install.mkdir(parents=True)
        local_install.mkdir(parents=True)
        project = tmp_path / "proj"
        project.mkdir()
        _write_plugin_manifest(tmp_path, {
            "p@m": [
                {"scope": "user", "installPath": str(user_install)},
                {
                    "scope": "local",
                    "installPath": str(local_install),
                    "projectPath": str(project),
                },
            ],
        })
        pairs = _read_installed_plugins(tmp_path, project)
        assert pairs == [
            ("p@m", user_install / "agents"),
            ("p@m", local_install / "agents"),
        ]


# -----------------------------------------------------------------------------
# load_plugin_agents — discovery + cross-plugin collision
# -----------------------------------------------------------------------------


class TestLoadPluginAgents:
    """Plugin agent discovery, aggregation, and inter-plugin collisions."""

    def test_no_manifest_returns_empty(self, tmp_path: Path) -> None:
        pack, ss, bodies = load_plugin_agents(tmp_path, tmp_path)
        assert pack == {}
        assert ss == {}
        assert bodies == {}

    def test_loads_agents_from_user_scope_plugin(self, tmp_path: Path) -> None:
        install = _plugin_install(tmp_path, "cache", "rr")
        agents_dir = install / "agents"
        _write_agent(agents_dir, "rr-planner.md", description="RR planner")
        _write_plugin_manifest(tmp_path, {
            "repo-reactor@m": [{"scope": "user", "installPath": str(install)}],
        })
        pack, _, bodies = load_plugin_agents(tmp_path, tmp_path)
        # Plugin agents are namespaced as "<plugin_short>:<role>" to match
        # Claude Code's surface form (e.g. the lead's agent list shows
        # "repo-reactor:rr-planner", not bare "rr-planner").
        assert set(pack.keys()) == {"repo-reactor:rr-planner"}
        assert pack["repo-reactor:rr-planner"].description == "RR planner"
        assert "repo-reactor:rr-planner" in bodies

    def test_missing_agents_dir_does_not_raise(self, tmp_path: Path) -> None:
        install = _plugin_install(tmp_path, "cache", "p")
        install.mkdir(parents=True)
        # No agents/ subdir.
        _write_plugin_manifest(tmp_path, {
            "p@m": [{"scope": "user", "installPath": str(install)}],
        })
        pack, _, _ = load_plugin_agents(tmp_path, tmp_path)
        assert pack == {}

    def test_aggregates_across_plugins(self, tmp_path: Path) -> None:
        a = _plugin_install(tmp_path, "a")
        b = _plugin_install(tmp_path, "b")
        _write_agent(a / "agents", "alice.md")
        _write_agent(b / "agents", "bob.md")
        _write_plugin_manifest(tmp_path, {
            "a@m": [{"scope": "user", "installPath": str(a)}],
            "b@m": [{"scope": "user", "installPath": str(b)}],
        })
        pack, _, _ = load_plugin_agents(tmp_path, tmp_path)
        assert set(pack.keys()) == {"a:alice", "b:bob"}

    def test_different_plugins_same_role_name_no_collision(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Two distinct plugins shipping the same bare role name no longer
        collide — namespacing gives them distinct keys
        ``<plugin_short>:<role>``. Both load; no WARN."""
        plugins_root = tmp_path / ".claude" / "plugins"
        a = plugins_root / "a"
        b = plugins_root / "b"
        _write_agent(a / "agents", "shared.md", description="from-a")
        _write_agent(b / "agents", "shared.md", description="from-b")
        _write_plugin_manifest(tmp_path, {
            "alpha@m": [{"scope": "user", "installPath": str(a)}],
            "beta@m": [{"scope": "user", "installPath": str(b)}],
        })
        caplog.set_level(logging.WARNING, logger=LOGGER)
        pack, _, _ = load_plugin_agents(tmp_path, tmp_path)
        assert pack["alpha:shared"].description == "from-a"
        assert pack["beta:shared"].description == "from-b"
        msgs = [r.getMessage() for r in caplog.records]
        assert not any("appears in plugin" in m for m in msgs), (
            f"unexpected collision WARN; got: {msgs}"
        )

    def test_same_plugin_short_collision_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Same ``plugin_short`` from two different marketplaces collides —
        keys both render as ``p:role`` since the marketplace suffix is
        stripped. Lex-later wins, WARN names both plugin_keys and dirs."""
        plugins_root = tmp_path / ".claude" / "plugins"
        a = plugins_root / "a"
        b = plugins_root / "b"
        _write_agent(a / "agents", "shared.md", description="from-a")
        _write_agent(b / "agents", "shared.md", description="from-b")
        _write_plugin_manifest(tmp_path, {
            "p@alpha-mkt": [{"scope": "user", "installPath": str(a)}],
            "p@beta-mkt": [{"scope": "user", "installPath": str(b)}],
        })
        caplog.set_level(logging.WARNING, logger=LOGGER)
        pack, _, _ = load_plugin_agents(tmp_path, tmp_path)
        # Lex-later "p@beta-mkt" wins.
        assert pack["p:shared"].description == "from-b"
        msgs = [r.getMessage() for r in caplog.records]
        assert any(
            "p@alpha-mkt" in m and "p@beta-mkt" in m and "agents" in m
            for m in msgs
        ), f"collision WARN missing plugin keys + paths; got: {msgs}"

    def test_same_plugin_two_installs_collision_warning_has_distinct_paths(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """H2 regression: one plugin with both user and local-scope installs
        that ship the same role — WARN must distinguish via paths, not just
        repeat the plugin key on both sides."""
        plugins_root = tmp_path / ".claude" / "plugins"
        user_install = plugins_root / "user-cache"
        local_install = plugins_root / "local-cache"
        project = tmp_path / "proj"
        project.mkdir()
        _write_agent(user_install / "agents", "dupe.md", description="user-scope")
        _write_agent(local_install / "agents", "dupe.md", description="local-scope")
        _write_plugin_manifest(tmp_path, {
            "p@m": [
                {"scope": "user", "installPath": str(user_install)},
                {
                    "scope": "local",
                    "installPath": str(local_install),
                    "projectPath": str(project),
                },
            ],
        })
        caplog.set_level(logging.WARNING, logger=LOGGER)
        load_plugin_agents(tmp_path, project)
        msgs = [r.getMessage() for r in caplog.records]
        assert any(
            "user-cache" in m and "local-cache" in m
            for m in msgs
        ), f"collision WARN doesn't distinguish install paths; got: {msgs}"


# -----------------------------------------------------------------------------
# H1 — installPath escape guard
# -----------------------------------------------------------------------------


class TestInstallPathEscapeGuard:
    """H1 — installPaths outside ~/.claude/plugins/ are refused with a WARN."""

    def test_install_path_outside_plugins_root_is_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # An attacker-controlled or corrupted manifest that points
        # installPath at some unrelated on-disk location.
        rogue = tmp_path / "rogue"
        _write_agent(rogue / "agents", "evil.md", description="should not load")
        _write_plugin_manifest(tmp_path, {
            "rogue@m": [{"scope": "user", "installPath": str(rogue)}],
        })
        caplog.set_level(logging.WARNING, logger=LOGGER)
        pack, _, _ = load_plugin_agents(tmp_path, tmp_path)
        assert "evil" not in pack
        msgs = [r.getMessage() for r in caplog.records]
        assert any(
            "rogue@m" in m and "outside" in m for m in msgs
        ), f"escape-guard WARN missing; got: {msgs}"

    def test_install_path_inside_plugins_root_is_loaded(
        self, tmp_path: Path,
    ) -> None:
        plugins_root = tmp_path / ".claude" / "plugins"
        good = plugins_root / "cache" / "ok"
        _write_agent(good / "agents", "ok.md")
        _write_plugin_manifest(tmp_path, {
            "ok@m": [{"scope": "user", "installPath": str(good)}],
        })
        pack, _, _ = load_plugin_agents(tmp_path, tmp_path)
        assert "ok:ok" in pack

    def test_install_path_with_traversal_segments_is_rejected(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        plugins_root = tmp_path / ".claude" / "plugins"
        plugins_root.mkdir(parents=True)
        # Path that lexically lives under plugins_root but resolves out via ../
        escape = plugins_root / ".." / ".." / "rogue"
        _write_agent(tmp_path / "rogue" / "agents", "evil.md")
        _write_plugin_manifest(tmp_path, {
            "rogue@m": [{"scope": "user", "installPath": str(escape)}],
        })
        caplog.set_level(logging.WARNING, logger=LOGGER)
        pack, _, _ = load_plugin_agents(tmp_path, tmp_path)
        assert "evil" not in pack
        assert any(
            "outside" in r.getMessage() for r in caplog.records
        )


# -----------------------------------------------------------------------------
# Sentinel coverage gaps: M2, M3, L1
# -----------------------------------------------------------------------------


class TestSentinelCoverageGaps:
    """Targeted tests filling the gaps the sentinel review called out."""

    def test_empty_plugins_dict_is_silent(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """L1 — manifest with `plugins: {}` (no installs) loads cleanly."""
        _write_plugin_manifest(tmp_path, {})
        caplog.set_level(logging.WARNING, logger=LOGGER)
        pack, ss, bodies = load_plugin_agents(tmp_path, tmp_path)
        assert pack == {}
        assert ss == {}
        assert bodies == {}
        # No WARN records — silent empty result.
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    def test_plugin_and_project_bare_role_coexist(
        self, tmp_path: Path,
    ) -> None:
        """Plugin agents are namespaced (`<plugin>:<role>`); a project
        agent file with the same bare role does NOT shadow the plugin —
        both are independently spawnable. Mirrors Claude Code: plugin
        agents and user/project agents are distinct namespaces."""
        install = _plugin_install(tmp_path, "cache", "rr")
        project = tmp_path / "proj"
        _write_agent(
            install / "agents", "rr-planner.md",
            description="plugin", extra="skills: [plan-feature]",
        )
        _write_agent(
            project / ".claude" / "agents", "rr-planner.md",
            description="project override",
        )
        _write_plugin_manifest(tmp_path, {
            "rr@m": [{"scope": "user", "installPath": str(install)}],
        })
        merged, _, _ = build_merged_pack(home_dir=tmp_path, project_root=project)
        assert merged["rr:rr-planner"].description == "plugin"
        assert merged["rr-planner"].description == "project override"

    def test_plugin_agent_with_unknown_skill_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """M3 — _warn_unknown_skills must include plugin-layer agents.
        A plugin agent that declares a skill not on disk should warn."""
        install = _plugin_install(tmp_path, "cache", "rr")
        _write_agent(
            install / "agents", "rr-planner.md",
            extra="skills: [definitely-not-a-real-skill]",
        )
        _write_plugin_manifest(tmp_path, {
            "rr@m": [{"scope": "user", "installPath": str(install)}],
        })
        caplog.set_level(logging.WARNING, logger=LOGGER)
        build_merged_pack(home_dir=tmp_path, project_root=tmp_path / "x")
        msgs = [r.getMessage() for r in caplog.records]
        assert any(
            "rr:rr-planner" in m and "definitely-not-a-real-skill" in m
            for m in msgs
        ), f"unknown-skill WARN didn't surface plugin agent; got: {msgs}"

    def test_plugin_agent_with_unknown_mcp_server_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """M3 — _warn_unknown_mcp_servers must include plugin-layer agents."""
        install = _plugin_install(tmp_path, "cache", "rr")
        _write_agent(
            install / "agents", "rr-planner.md",
            extra="mcpServers: [not-a-registered-server]",
        )
        _write_plugin_manifest(tmp_path, {
            "rr@m": [{"scope": "user", "installPath": str(install)}],
        })
        caplog.set_level(logging.WARNING, logger=LOGGER)
        build_merged_pack(home_dir=tmp_path, project_root=tmp_path / "x")
        msgs = [r.getMessage() for r in caplog.records]
        assert any(
            "rr:rr-planner" in m and "not-a-registered-server" in m
            for m in msgs
        ), f"unknown-mcpServers WARN didn't surface plugin agent; got: {msgs}"


# -----------------------------------------------------------------------------
# build_merged_pack — plugin layer slots between default and user
# -----------------------------------------------------------------------------


class TestBuildMergedPackPluginLayer:
    """Plugin agents are namespaced (`<plugin>:<role>`) and coexist with
    bare-keyed default/user/project entries. To override a plugin agent,
    a user/project file must opt in to the namespaced filename + name."""

    def test_plugin_agents_appear_in_merged_namespaced(
        self, tmp_path: Path,
    ) -> None:
        install = _plugin_install(tmp_path, "cache", "rr")
        _write_agent(
            install / "agents", "rr-planner.md",
            description="from plugin", tools=["Read", "Write"],
        )
        _write_plugin_manifest(tmp_path, {
            "rr@m": [{"scope": "user", "installPath": str(install)}],
        })
        merged, _, bodies = build_merged_pack(
            home_dir=tmp_path, project_root=tmp_path,
        )
        # Plugin entries are namespaced; bare role name absent.
        assert "rr:rr-planner" in merged
        assert "rr-planner" not in merged
        assert merged["rr:rr-planner"].description == "from plugin"
        assert "rr:rr-planner" in bodies

    def test_user_bare_does_not_shadow_plugin_namespaced(
        self, tmp_path: Path,
    ) -> None:
        """A user file at `~/.claude/agents/rr-planner.md` and a plugin
        agent both exist independently — distinct keys, both spawnable."""
        install = _plugin_install(tmp_path, "cache", "rr")
        _write_agent(install / "agents", "rr-planner.md", description="from plugin")
        _write_agent(
            tmp_path / ".claude" / "agents", "rr-planner.md",
            description="from user",
        )
        _write_plugin_manifest(tmp_path, {
            "rr@m": [{"scope": "user", "installPath": str(install)}],
        })
        merged, _, _ = build_merged_pack(
            home_dir=tmp_path, project_root=tmp_path / "no-project",
        )
        assert merged["rr:rr-planner"].description == "from plugin"
        assert merged["rr-planner"].description == "from user"

    def test_plugin_does_not_shadow_default_bare_role(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A plugin shipping `explorer.md` produces key `rr:explorer`,
        which does NOT collide with the bundled bare `explorer` — so
        the default keeps its slot and no shadow-INFO fires."""
        install = _plugin_install(tmp_path, "cache", "rr")
        _write_agent(
            install / "agents", "explorer.md",
            description="plugin's explorer",
        )
        _write_plugin_manifest(tmp_path, {
            "rr@m": [{"scope": "user", "installPath": str(install)}],
        })
        caplog.set_level(logging.INFO, logger=LOGGER)
        merged, _, _ = build_merged_pack(
            home_dir=tmp_path, project_root=tmp_path / "x",
        )
        # Default 'explorer' still bundled; plugin's lives at namespaced key.
        assert "explorer" in merged
        assert "rr:explorer" in merged
        assert merged["rr:explorer"].description == "plugin's explorer"
        msgs = [r.getMessage() for r in caplog.records]
        assert not any("plugin shadows default" in m for m in msgs)

    def test_user_cannot_override_plugin_via_name_frontmatter(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The name validator (``[a-z0-9][a-z0-9-]*``) rejects colons in
        user/project agent ``name`` frontmatter. Plugin agents are
        therefore not overridable from user/project layers — matches
        Claude Code's model, where plugin agents are first-class and
        uninstall is the override path."""
        install = _plugin_install(tmp_path, "cache", "rr")
        _write_agent(install / "agents", "rr-planner.md", description="from plugin")
        _write_agent(
            tmp_path / ".claude" / "agents", "rr-planner-override.md",
            description="from user",
            extra="name: rr:rr-planner",
        )
        _write_plugin_manifest(tmp_path, {
            "rr@m": [{"scope": "user", "installPath": str(install)}],
        })
        caplog.set_level(logging.WARNING, logger=LOGGER)
        merged, _, _ = build_merged_pack(
            home_dir=tmp_path, project_root=tmp_path / "no-project",
        )
        assert merged["rr:rr-planner"].description == "from plugin"
        warn_msgs = [
            r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING
        ]
        assert any(
            "invalid name" in m and "rr:rr-planner" in m for m in warn_msgs
        ), f"name-validator rejection WARN missing at >=WARNING; got: {warn_msgs}"
