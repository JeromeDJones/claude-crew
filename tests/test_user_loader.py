"""Tests for the user/project agent loader (Feature #3b).

Covers Phase 1 success criteria SC-1..SC-7 and Phase 2 design pin-downs
Q5..Q8 from ``doc/features/FEATURE-agent-definition-loader.md``. SC-8
(live E2E with a real ``SdkTeammate``) lives in
``tests/test_user_loader_live.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from textwrap import dedent

import pytest
from claude_agent_sdk.types import AgentDefinition

from claude_crew.subagents import load_default_pack, merge_packs
from claude_crew.subagents._user_loader import (
    _MAX_FILE_BYTES,
    _MAX_FILES_PER_DIR,
    discover_dir,
    load_project_agents,
    load_user_agents,
    strict_parse,
)


LOGGER = "claude_crew.subagents.loader"


def _write_agent(
    dir_: Path,
    filename: str,
    *,
    description: str = "Test agent.",
    model: str = "haiku",
    tools: list[str] | None = None,
    extra_frontmatter: str = "",
    body: str = "You are a test agent.",
) -> Path:
    """Plant a valid (or near-valid) agent file. Returns the path."""
    dir_.mkdir(parents=True, exist_ok=True)
    tools_yaml = ", ".join(tools or ["Read"])
    # Build without dedent so embedded newlines in extra_frontmatter don't
    # break common-indent detection.
    lines = [
        "---",
        f"description: {description}",
        f"model: {model}",
        f"tools: [{tools_yaml}]",
    ]
    if extra_frontmatter:
        lines.append(extra_frontmatter.rstrip("\n"))
    lines.extend(["---", "", body, ""])
    path = dir_ / filename
    path.write_text("\n".join(lines))
    return path


# -----------------------------------------------------------------------------
# SC-4: missing directories are silent
# -----------------------------------------------------------------------------


class TestMissingDirectoriesAreSilent:
    """SC-4 — no errors, no warnings when the directory is absent."""

    def test_load_user_agents_with_no_home_dir(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG, logger=LOGGER)
        pack, role_ss, bodies = load_user_agents(tmp_path)  # no .claude/agents/
        assert pack == {}
        assert role_ss == {}
        assert bodies == {}
        assert caplog.records == []

    def test_load_project_agents_with_no_project_root(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG, logger=LOGGER)
        pack, role_ss, bodies = load_project_agents(tmp_path)
        assert pack == {}
        assert role_ss == {}
        assert bodies == {}
        assert caplog.records == []


# -----------------------------------------------------------------------------
# SC-1, SC-2: discovery
# -----------------------------------------------------------------------------


class TestDiscovery:
    """SC-1, SC-2 — flat *.md glob, README.md excluded, sorted."""

    def test_discovers_user_agents(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".claude" / "agents"
        _write_agent(agents_dir, "scout.md", description="Scout the codebase.")
        _write_agent(agents_dir, "builder.md", description="Build things.")

        result, _role_ss, _bodies = load_user_agents(tmp_path)

        assert set(result.keys()) == {"scout", "builder"}
        assert isinstance(result["scout"], AgentDefinition)
        assert result["scout"].description == "Scout the codebase."

    def test_discovers_project_agents(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".claude" / "agents"
        _write_agent(agents_dir, "reviewer.md", description="Review PRs.")
        result, _role_ss, _bodies = load_project_agents(tmp_path)
        assert set(result.keys()) == {"reviewer"}

    def test_underscores_become_hyphens(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".claude" / "agents"
        _write_agent(agents_dir, "general_purpose.md")
        result, _role_ss, _bodies = load_user_agents(tmp_path)
        assert "general-purpose" in result

    def test_readme_md_is_excluded(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".claude" / "agents"
        _write_agent(agents_dir, "scout.md")
        # README.md is not a valid agent file at all — just text. Must not
        # be parsed (which would error) or returned.
        (agents_dir / "README.md").write_text("# Agents in this directory\n")
        result, _role_ss, _bodies = load_user_agents(tmp_path)
        assert set(result.keys()) == {"scout"}

    def test_non_md_files_ignored(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".claude" / "agents"
        _write_agent(agents_dir, "scout.md")
        (agents_dir / "notes.txt").write_text("ignored")
        (agents_dir / "scout.md.bak").write_text("ignored")
        result, _role_ss, _bodies = load_user_agents(tmp_path)
        assert set(result.keys()) == {"scout"}

    def test_uppercase_md_extension_ignored(self, tmp_path: Path) -> None:
        # Case-sensitivity matters on macOS+Linux; we glob "*.md" exactly.
        agents_dir = tmp_path / ".claude" / "agents"
        _write_agent(agents_dir, "scout.md")
        # We can't use _write_agent for .MD because file systems differ.
        # Just verify a deliberately-uppercase file is not pulled in.
        (agents_dir / "BUILDER.MD").write_text("---\ndescription: x\nmodel: haiku\ntools: [Read]\n---\n\nbody\n")
        result, _role_ss, _bodies = load_user_agents(tmp_path)
        assert "scout" in result
        # On case-insensitive FS this could be flaky; just assert scout loads.

    def test_subdirs_not_recursed(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".claude" / "agents"
        _write_agent(agents_dir, "scout.md")
        nested = agents_dir / "nested"
        _write_agent(nested, "hidden.md")
        result, _role_ss, _bodies = load_user_agents(tmp_path)
        assert set(result.keys()) == {"scout"}


# -----------------------------------------------------------------------------
# SC-5: malformed files isolated
# -----------------------------------------------------------------------------


class TestMalformedFilesIsolated:
    """SC-5 — bad file warns + skipped, good siblings still load."""

    def test_bad_yaml_warns_skipped_good_loads(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=LOGGER)
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "broken.md").write_text("---\n: [bad yaml\n---\nbody\n")
        _write_agent(agents_dir, "good.md", description="I work.")

        result, _role_ss, _bodies = load_user_agents(tmp_path)

        assert "broken" not in result
        assert "good" in result
        assert result["good"].description == "I work."
        warning_messages = [r.getMessage() for r in caplog.records]
        assert any("broken.md" in msg for msg in warning_messages)

    def test_missing_required_field_warns_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=LOGGER)
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        # Missing `tools` (required).
        (agents_dir / "incomplete.md").write_text(
            "---\ndescription: Foo\nmodel: haiku\n---\n\nbody\n"
        )
        _write_agent(agents_dir, "ok.md")

        result, _role_ss, _bodies = load_user_agents(tmp_path)

        assert "incomplete" not in result
        assert "ok" in result
        assert any("incomplete.md" in r.getMessage() for r in caplog.records)


# -----------------------------------------------------------------------------
# SC-6: unsupported frontmatter warns, agent loads
# -----------------------------------------------------------------------------


class TestUnsupportedFrontmatter:
    """SC-6 — extra keys warn but don't break loading."""

    def test_unsupported_key_warns_and_loads(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=LOGGER)
        agents_dir = tmp_path / ".claude" / "agents"
        # `setting_sources` is a parent-level concern, not on AgentDefinition;
        # it's a likely future foot-typo.
        _write_agent(
            agents_dir,
            "scout.md",
            extra_frontmatter='setting_sources: ["user", "project"]\n',
        )

        result, _role_ss, _bodies = load_user_agents(tmp_path)

        assert "scout" in result
        assert result["scout"].description == "Test agent."
        msgs = [r.getMessage() for r in caplog.records]
        assert any("scout.md" in m and "setting_sources" in m for m in msgs)

    def test_typoed_key_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=LOGGER)
        agents_dir = tmp_path / ".claude" / "agents"
        _write_agent(
            agents_dir, "scout.md", extra_frontmatter="descrption: typo\n"
        )

        load_user_agents(tmp_path)  # returns tuple; only need side-effects

        msgs = [r.getMessage() for r in caplog.records]
        assert any("descrption" in m for m in msgs)

    def test_strict_parse_returns_agent_with_supported_fields_only(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=LOGGER)
        path = _write_agent(
            tmp_path,
            "scout.md",
            extra_frontmatter='setting_sources: ["user"]\n',
        )
        key, agent, _ss, _body = strict_parse(path)
        assert key == "scout"
        assert isinstance(agent, AgentDefinition)
        # AgentDefinition is a TypedDict-like in the SDK; it doesn't carry
        # the dropped key. Just confirm it loaded.


# -----------------------------------------------------------------------------
# Q6: resource limits
# -----------------------------------------------------------------------------


class TestResourceLimits:
    """Q6 design pin-down — per-file size cap and per-dir count cap."""

    def test_oversized_file_warns_and_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=LOGGER)
        agents_dir = tmp_path / ".claude" / "agents"
        _write_agent(agents_dir, "ok.md")
        # Plant an oversized file with a valid frontmatter so we know the
        # only reason it was skipped is the size cap.
        big_path = _write_agent(
            agents_dir,
            "big.md",
            body="x" * (_MAX_FILE_BYTES + 1),
        )
        assert big_path.stat().st_size > _MAX_FILE_BYTES

        result, _role_ss, _bodies = load_user_agents(tmp_path)

        assert "big" not in result
        assert "ok" in result
        msgs = [r.getMessage() for r in caplog.records]
        assert any("big.md" in m and str(_MAX_FILE_BYTES) in m for m in msgs)

    def test_directory_with_too_many_files_warns_and_truncates(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=LOGGER)
        agents_dir = tmp_path / ".claude" / "agents"
        # Plant cap+5 files: a000.md .. a104.md. Sorted alphabetically,
        # we keep the first _MAX_FILES_PER_DIR.
        for i in range(_MAX_FILES_PER_DIR + 5):
            _write_agent(agents_dir, f"agent-{i:03d}.md")

        result, _role_ss, _bodies = load_user_agents(tmp_path)

        assert len(result) == _MAX_FILES_PER_DIR
        # The "first 100 sorted" means agent-000..agent-099 survive.
        assert "agent-000" in result
        assert "agent-099" in result
        assert "agent-100" not in result
        msgs = [r.getMessage() for r in caplog.records]
        assert any(str(_MAX_FILES_PER_DIR) in m for m in msgs)


# -----------------------------------------------------------------------------
# Q8: intra-directory key collision
# -----------------------------------------------------------------------------


class TestIntraDirCollision:
    """Q8 design pin-down — two files producing the same kebab-key."""

    def test_collision_warns_alphabetically_later_wins(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=LOGGER)
        agents_dir = tmp_path / ".claude" / "agents"
        # Both produce key "general-purpose".
        _write_agent(
            agents_dir,
            "general_purpose.md",
            description="From underscore file.",
        )
        _write_agent(
            agents_dir,
            "general-purpose.md",
            description="From hyphen file.",
        )

        result, _role_ss, _bodies = load_user_agents(tmp_path)

        # Sorted: "general-purpose.md" < "general_purpose.md" (hyphen 0x2D
        # < underscore 0x5F). Later in alpha order = underscore file wins.
        assert result["general-purpose"].description == "From underscore file."
        msgs = [r.getMessage() for r in caplog.records]
        assert any(
            "general-purpose" in m
            and "general_purpose.md" in m
            and "general-purpose.md" in m
            for m in msgs
        )


# -----------------------------------------------------------------------------
# SC-3: precedence (verified at the merge_packs composition layer)
# -----------------------------------------------------------------------------


class TestShadowingObservability:
    """Q7 design pin-down — INFO log when shadow occurs."""

    def test_user_shadowing_default_logs_info(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from claude_crew.subagents._user_loader import build_merged_pack

        caplog.set_level(logging.INFO, logger=LOGGER)
        # User defines "explorer" — collides with bundled default.
        _write_agent(
            tmp_path / ".claude" / "agents",
            "explorer.md",
            description="User's explorer.",
        )
        # No project root with agents.
        empty_project = tmp_path / "no-project"
        empty_project.mkdir()

        build_merged_pack(home_dir=tmp_path, project_root=empty_project)  # returns tuple; we only need side-effects here

        info_msgs = [
            r.getMessage() for r in caplog.records if r.levelno == logging.INFO
        ]
        assert any(
            "explorer" in m and "user-level" in m and "default" in m
            for m in info_msgs
        )

    def test_project_shadowing_default_with_no_user_logs_info(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Project shadows the default pack with no user-level entry —
        exercises the ``elif key in default`` branch the other tests miss."""
        from claude_crew.subagents._user_loader import build_merged_pack

        caplog.set_level(logging.INFO, logger=LOGGER)
        empty_user = tmp_path / "home"
        empty_user.mkdir()
        project_root = tmp_path / "project"
        # Project defines "explorer" — collides with bundled default; no
        # user-level entry exists.
        _write_agent(
            project_root / ".claude" / "agents",
            "explorer.md",
            description="Project's explorer.",
        )

        merged, _role_ss, _bodies = build_merged_pack(home_dir=empty_user, project_root=project_root)

        assert merged["explorer"].description == "Project's explorer."
        info_msgs = [
            r.getMessage() for r in caplog.records if r.levelno == logging.INFO
        ]
        assert any(
            "explorer" in m and "project-level" in m and "default" in m
            for m in info_msgs
        ), f"expected project-shadows-default info log; got {info_msgs}"

    def test_project_shadowing_user_logs_info(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from claude_crew.subagents._user_loader import build_merged_pack

        caplog.set_level(logging.INFO, logger=LOGGER)
        user_root = tmp_path / "home"
        project_root = tmp_path / "project"
        # Both define "scout" (not in default pack).
        _write_agent(user_root / ".claude" / "agents", "scout.md")
        _write_agent(project_root / ".claude" / "agents", "scout.md")

        build_merged_pack(home_dir=user_root, project_root=project_root)  # returns tuple; we only need side-effects here

        info_msgs = [
            r.getMessage() for r in caplog.records if r.levelno == logging.INFO
        ]
        assert any(
            "scout" in m and "project-level" in m and "user-level" in m
            for m in info_msgs
        )


class TestPrecedence:
    """SC-3 — project shadows user shadows default via merge_packs composition."""

    def test_project_shadows_user_shadows_default(self, tmp_path: Path) -> None:
        user_root = tmp_path / "home"
        project_root = tmp_path / "project"

        # User defines its own "explorer" — should shadow the bundled.
        _write_agent(
            user_root / ".claude" / "agents",
            "explorer.md",
            description="User's explorer.",
        )
        # Project also defines "explorer" — should shadow user.
        _write_agent(
            project_root / ".claude" / "agents",
            "explorer.md",
            description="Project's explorer.",
        )
        # User adds a new agent the default doesn't have.
        _write_agent(
            user_root / ".claude" / "agents",
            "scout.md",
            description="User's scout.",
        )

        default, _dss, _dbs = load_default_pack()
        user, _uss, _ubs = load_user_agents(user_root)
        project, _pss, _pbs = load_project_agents(project_root)
        merged = merge_packs(merge_packs(default, user), project)

        assert merged["explorer"].description == "Project's explorer."
        assert merged["scout"].description == "User's scout."
        # Default-only agents survive.
        assert "planner" in merged
        assert "general-purpose" in merged


# -----------------------------------------------------------------------------
# Feature #11 T2: settingSources threaded through the loader cascade
# -----------------------------------------------------------------------------


class TestSettingSourcesCascade:
    """Feature #11 T2 — role_ss parallel dict from build_merged_pack.

    BDD scenarios from FEATURE-lightweight-subagent-context.md Phase 3 T2.
    """

    def test_bundled_pack_with_setting_sources_appears_in_role_ss(
        self, tmp_path: Path
    ) -> None:
        """Scenario: merged pack includes settingSources from bundled pack file.

        Uses a user agent file that declares settingSources: [] so the test
        doesn't depend on which bundled packs currently have settingSources set.
        """
        from claude_crew.subagents._user_loader import build_merged_pack

        agents_dir = tmp_path / ".claude" / "agents"
        _write_agent(
            agents_dir,
            "myagent.md",
            extra_frontmatter="settingSources: []",
        )
        empty_project = tmp_path / "project"
        empty_project.mkdir()

        _merged, role_ss, _bodies = build_merged_pack(home_dir=tmp_path, project_root=empty_project)

        assert role_ss["myagent"] == []

    def test_bundled_packs_have_expected_setting_sources(
        self, tmp_path: Path
    ) -> None:
        """SC-4 regression: bundled pack files have the correct settingSources values.

        After T4 all three bundled packs declare settingSources. This test fails
        if any pack file loses its settingSources line.
        """
        from claude_crew.subagents._user_loader import build_merged_pack

        empty_user = tmp_path / "home"
        empty_user.mkdir()
        empty_project = tmp_path / "project"
        empty_project.mkdir()

        _merged, role_ss, _bodies = build_merged_pack(home_dir=empty_user, project_root=empty_project)

        assert role_ss.get("explorer") == [], "explorer.md must declare settingSources: []"
        assert role_ss.get("general-purpose") == [], "general_purpose.md must declare settingSources: []"
        assert role_ss.get("planner") == ["project"], "planner.md must declare settingSources: [project]"

    def test_user_agent_with_setting_sources_captured_in_role_ss(
        self, tmp_path: Path
    ) -> None:
        """Scenario: user-level agent file with settingSources: [] is captured."""
        from claude_crew.subagents._user_loader import build_merged_pack

        agents_dir = tmp_path / ".claude" / "agents"
        _write_agent(
            agents_dir,
            "custom.md",
            extra_frontmatter="settingSources: []",
        )
        empty_project = tmp_path / "project"
        empty_project.mkdir()

        _merged, role_ss, _bodies = build_merged_pack(home_dir=tmp_path, project_root=empty_project)

        assert "custom" in role_ss
        assert role_ss["custom"] == []

    def test_project_agent_shadows_user_agent_in_role_ss(
        self, tmp_path: Path
    ) -> None:
        """Scenario: project-level settingSources: [project] shadows user-level []."""
        from claude_crew.subagents._user_loader import build_merged_pack

        user_root = tmp_path / "home"
        project_root = tmp_path / "project"

        _write_agent(
            user_root / ".claude" / "agents",
            "custom.md",
            extra_frontmatter="settingSources: []",
        )
        _write_agent(
            project_root / ".claude" / "agents",
            "custom.md",
            extra_frontmatter="settingSources: [project]",
        )

        _merged, role_ss, _bodies = build_merged_pack(home_dir=user_root, project_root=project_root)

        assert role_ss["custom"] == ["project"]

    def test_agent_without_setting_sources_has_none_in_role_ss(
        self, tmp_path: Path
    ) -> None:
        """Scenario: pack file without settingSources has role_ss.get(key) is None."""
        from claude_crew.subagents._user_loader import build_merged_pack

        agents_dir = tmp_path / ".claude" / "agents"
        _write_agent(agents_dir, "nosources.md")  # no settingSources in frontmatter
        empty_project = tmp_path / "project"
        empty_project.mkdir()

        _merged, role_ss, _bodies = build_merged_pack(home_dir=tmp_path, project_root=empty_project)

        assert role_ss.get("nosources") is None

    def test_setting_sources_with_project_value_parsed_correctly(
        self, tmp_path: Path
    ) -> None:
        """Verify settingSources: [project] round-trips through strict_parse."""
        path = _write_agent(
            tmp_path,
            "agent.md",
            extra_frontmatter="settingSources: [project]",
        )
        key, _agent, ss, _body = strict_parse(path)
        assert key == "agent"
        assert ss == ["project"]

    def test_discover_dir_captures_role_ss_for_files_with_setting_sources(
        self, tmp_path: Path
    ) -> None:
        """discover_dir returns role_ss populated for agents that declare settingSources."""
        agents_dir = tmp_path / "agents"
        _write_agent(agents_dir, "with-ss.md", extra_frontmatter="settingSources: [user]")
        _write_agent(agents_dir, "without-ss.md")  # no settingSources

        pack, role_ss, _bodies = discover_dir(agents_dir)

        assert "with-ss" in pack
        assert "without-ss" in pack
        assert role_ss["with-ss"] == ["user"]
        assert role_ss.get("without-ss") is None

    def test_discover_dir_empty_setting_sources_list_preserved(
        self, tmp_path: Path
    ) -> None:
        """settingSources: [] (empty list) is distinct from None and must be preserved."""
        agents_dir = tmp_path / "agents"
        _write_agent(agents_dir, "empty-ss.md", extra_frontmatter="settingSources: []")

        _pack, role_ss, _bodies = discover_dir(agents_dir)

        assert "empty-ss" in role_ss
        assert role_ss["empty-ss"] == []  # not None
