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
        install = tmp_path / "cache" / "myplugin"
        install.mkdir(parents=True)
        _write_plugin_manifest(tmp_path, {
            "myplugin@m": [{"scope": "user", "installPath": str(install)}],
        })
        pairs = _read_installed_plugins(tmp_path, tmp_path)
        assert pairs == [("myplugin@m", install / "agents")]

    def test_local_scope_install_only_when_project_path_matches(
        self, tmp_path: Path,
    ) -> None:
        install = tmp_path / "cache" / "p"
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
        install = tmp_path / "cache" / "p"
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
        a_install = tmp_path / "a"
        b_install = tmp_path / "b"
        a_install.mkdir()
        b_install.mkdir()
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
        user_install = tmp_path / "user-install"
        local_install = tmp_path / "local-install"
        user_install.mkdir()
        local_install.mkdir()
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
        install = tmp_path / "cache" / "rr"
        agents_dir = install / "agents"
        _write_agent(agents_dir, "rr-planner.md", description="RR planner")
        _write_plugin_manifest(tmp_path, {
            "repo-reactor@m": [{"scope": "user", "installPath": str(install)}],
        })
        pack, _, bodies = load_plugin_agents(tmp_path, tmp_path)
        assert set(pack.keys()) == {"rr-planner"}
        assert pack["rr-planner"].description == "RR planner"
        assert "rr-planner" in bodies

    def test_missing_agents_dir_does_not_raise(self, tmp_path: Path) -> None:
        install = tmp_path / "cache" / "p"
        install.mkdir(parents=True)
        # No agents/ subdir.
        _write_plugin_manifest(tmp_path, {
            "p@m": [{"scope": "user", "installPath": str(install)}],
        })
        pack, _, _ = load_plugin_agents(tmp_path, tmp_path)
        assert pack == {}

    def test_aggregates_across_plugins(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        b = tmp_path / "b"
        _write_agent(a / "agents", "alice.md")
        _write_agent(b / "agents", "bob.md")
        _write_plugin_manifest(tmp_path, {
            "a@m": [{"scope": "user", "installPath": str(a)}],
            "b@m": [{"scope": "user", "installPath": str(b)}],
        })
        pack, _, _ = load_plugin_agents(tmp_path, tmp_path)
        assert set(pack.keys()) == {"alice", "bob"}

    def test_cross_plugin_collision_lex_later_wins_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        a = tmp_path / "a"
        b = tmp_path / "b"
        _write_agent(a / "agents", "shared.md", description="from-a")
        _write_agent(b / "agents", "shared.md", description="from-b")
        _write_plugin_manifest(tmp_path, {
            "alpha@m": [{"scope": "user", "installPath": str(a)}],
            "beta@m": [{"scope": "user", "installPath": str(b)}],
        })
        caplog.set_level(logging.WARNING, logger=LOGGER)
        pack, _, _ = load_plugin_agents(tmp_path, tmp_path)
        # Lex-later "beta@m" wins.
        assert pack["shared"].description == "from-b"
        assert any(
            "alpha@m" in r.getMessage() and "beta@m" in r.getMessage()
            for r in caplog.records
        )


# -----------------------------------------------------------------------------
# build_merged_pack — plugin layer slots between default and user
# -----------------------------------------------------------------------------


class TestBuildMergedPackPluginLayer:
    """Precedence: project > user > plugin > default."""

    def test_plugin_agents_appear_in_merged(self, tmp_path: Path) -> None:
        install = tmp_path / "cache" / "rr"
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
        assert "rr-planner" in merged
        assert merged["rr-planner"].description == "from plugin"
        assert "rr-planner" in bodies

    def test_user_overrides_plugin(self, tmp_path: Path) -> None:
        install = tmp_path / "cache" / "rr"
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
        assert merged["rr-planner"].description == "from user"

    def test_project_overrides_plugin_when_no_user(self, tmp_path: Path) -> None:
        install = tmp_path / "cache" / "rr"
        project = tmp_path / "proj"
        _write_agent(install / "agents", "rr-planner.md", description="from plugin")
        _write_agent(
            project / ".claude" / "agents", "rr-planner.md",
            description="from project",
        )
        _write_plugin_manifest(tmp_path, {
            "rr@m": [{"scope": "user", "installPath": str(install)}],
        })
        merged, _, _ = build_merged_pack(home_dir=tmp_path, project_root=project)
        assert merged["rr-planner"].description == "from project"

    def test_plugin_shadowing_default_logs_info(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        install = tmp_path / "cache" / "rr"
        # Shadow the bundled "explorer" role.
        _write_agent(
            install / "agents", "explorer.md",
            description="plugin's explorer",
        )
        _write_plugin_manifest(tmp_path, {
            "rr@m": [{"scope": "user", "installPath": str(install)}],
        })
        caplog.set_level(logging.INFO, logger=LOGGER)
        build_merged_pack(home_dir=tmp_path, project_root=tmp_path / "x")
        assert any(
            "plugin shadows default" in r.getMessage()
            and "'explorer'" in r.getMessage()
            for r in caplog.records
        )

    def test_user_shadowing_plugin_warns_on_dropped_field(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """User-level redefinition that drops a field set by the plugin
        emits the same shadow-drop WARN as user-shadowing-default."""
        install = tmp_path / "cache" / "rr"
        _write_agent(
            install / "agents", "rr-planner.md",
            description="plugin", extra="skills: [plan-feature]",
        )
        _write_agent(
            tmp_path / ".claude" / "agents", "rr-planner.md",
            description="user override",  # no skills declared → drops
        )
        _write_plugin_manifest(tmp_path, {
            "rr@m": [{"scope": "user", "installPath": str(install)}],
        })
        caplog.set_level(logging.WARNING, logger=LOGGER)
        build_merged_pack(home_dir=tmp_path, project_root=tmp_path / "x")
        # The WARN names the higher layer ("user"), the role, and the
        # dropped field ("skills").
        msgs = [r.getMessage() for r in caplog.records]
        assert any(
            "user-level agent 'rr-planner'" in m
            and "skills" in m
            for m in msgs
        ), f"shadow-drop WARN missing; got: {msgs}"
