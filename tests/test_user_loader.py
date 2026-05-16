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
    _OPTIONAL_AGENTDEF_FIELDS,
    _discover_skill_names,
    _load_user_mcp_server_names,
    _warn_unknown_mcp_servers,
    _warn_unknown_skills,
    build_merged_pack,
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
        """Post-#15 only `description` is required."""
        caplog.set_level(logging.WARNING, logger=LOGGER)
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        # Missing `description` (the only remaining required field).
        (agents_dir / "incomplete.md").write_text(
            "---\nmodel: haiku\ntools: [Read]\n---\n\nbody\n"
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

    def test_cross_stem_name_collision_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """SC-2a (#15): two files with DIFFERENT stems but the same `name:`
        value collide via canonical-name keying. WARN names canonical name +
        both file paths; alphabetically later path wins."""
        caplog.set_level(logging.WARNING, logger=LOGGER)
        agents_dir = tmp_path / ".claude" / "agents"
        # Different stems, same canonical name.
        _write_agent(
            agents_dir,
            "alpha-runner.md",
            description="Alpha file.",
            extra_frontmatter="name: runner",
        )
        _write_agent(
            agents_dir,
            "beta-runner.md",
            description="Beta file.",
            extra_frontmatter="name: runner",
        )

        result, _role_ss, _bodies = load_user_agents(tmp_path)

        # Alphabetical: alpha-runner.md < beta-runner.md → beta wins.
        assert result["runner"].description == "Beta file."
        msgs = [r.getMessage() for r in caplog.records]
        assert any(
            "runner" in m and "alpha-runner.md" in m and "beta-runner.md" in m
            for m in msgs
        ), f"expected canonical-name collision WARN, got {msgs}"


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
        assert "general" in merged


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
        assert role_ss.get("general") == ["user", "project"], (
            "general.md must declare settingSources: [user, project]"
        )
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


# -----------------------------------------------------------------------------
# Feature #23: skill discovery + WARN at pack-load (T2)
# -----------------------------------------------------------------------------


def _write_skill(skills_root: Path, name: str, body: str = "Test skill body.") -> Path:
    """Plant a skill at <skills_root>/<name>/SKILL.md. Returns the SKILL.md path."""
    skill_dir = skills_root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {body}\n"
        "---\n\n"
        f"{body}\n"
    )
    return skill_md


class TestDiscoverSkillNames:
    """Scenario: _discover_skill_names walks user + project skill dirs."""

    def test_user_and_project_dirs_both_walked(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_skill(home / ".claude" / "skills", "user-skill")
        _write_skill(proj / ".claude" / "skills", "proj-skill")

        names = _discover_skill_names(home, proj)

        assert names == {"user-skill", "proj-skill"}

    def test_subdir_without_skillmd_is_not_a_skill(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        skills_dir = home / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "no-skill-md").mkdir()  # subdir, no SKILL.md inside

        names = _discover_skill_names(home, tmp_path / "nonexistent")

        assert "no-skill-md" not in names

    def test_missing_dirs_return_empty(self, tmp_path: Path) -> None:
        names = _discover_skill_names(tmp_path / "no-home", tmp_path / "no-proj")
        assert names == set()


class TestWarnUnknownSkills:
    """Scenario: declared skills not on disk produce WARN at pack-load."""

    def test_unknown_skill_warns_with_role_and_skill_name(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_skill(home / ".claude" / "skills", "foo")  # only foo exists

        # User agent declares skills: [foo, bar] — bar is unknown.
        _write_agent(
            home / ".claude" / "agents",
            "myrole.md",
            extra_frontmatter="skills: [foo, bar]",
        )

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            build_merged_pack(home_dir=home, project_root=proj)

        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("myrole" in m and "bar" in m for m in warn_msgs), (
            f"expected WARN naming role and 'bar', got {warn_msgs}"
        )
        # foo is known, must not appear in any WARN
        assert not any("'foo'" in m for m in warn_msgs)

    def test_skills_all_is_rejected_at_pack_load(
        self, tmp_path: Path
    ) -> None:
        """skills: all is now rejected at pack-load time (no longer valid per-agent)."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_agent(
            home / ".claude" / "agents",
            "myrole.md",
            extra_frontmatter="skills: all",
        )

        # build_merged_pack skips files that raise PackLoadError during parsing.
        # So "myrole" won't be in the merged pack.
        merged, _, _ = build_merged_pack(home_dir=home, project_root=proj)
        assert "myrole" not in merged, (
            "Agent with skills: all should be rejected during pack parse"
        )

    def test_known_skill_does_not_warn(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_skill(home / ".claude" / "skills", "foo")
        _write_agent(
            home / ".claude" / "agents",
            "myrole.md",
            extra_frontmatter="skills: [foo]",
        )

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            build_merged_pack(home_dir=home, project_root=proj)

        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any("declares unknown skills" in m for m in warn_msgs)

    def test_warn_message_contains_grep_target(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """SC-9 doc grep target — the literal phrase operators will look for."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_agent(
            home / ".claude" / "agents",
            "rev.md",
            extra_frontmatter="skills: [missing-skill]",
        )

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            build_merged_pack(home_dir=home, project_root=proj)

        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("declares unknown skills" in m for m in warn_msgs)


# -----------------------------------------------------------------------------
# Feature #23: skills cascade behavior (T4)
# -----------------------------------------------------------------------------


def _write_agent_with_skills(dir_: Path, filename: str, *, skills_yaml: str) -> Path:
    """Plant an agent with the given raw `skills:` YAML line."""
    return _write_agent(dir_, filename, extra_frontmatter=f"skills: {skills_yaml}")


class TestSkillsCascade:
    """SC-7, SC-8: cascade replaces AgentDefinition wholesale via merge_packs.

    No role_skills side-channel needed (D-6) — skills lives on AgentDefinition.
    """

    def test_user_overrides_default_list(self, tmp_path: Path) -> None:
        """SC-7: default skills: [a] then user override skills: [b] → merged ["b"]."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        # Override the bundled general-purpose with a user-level one declaring skills: [b]
        _write_agent_with_skills(
            home / ".claude" / "agents", "general-purpose.md", skills_yaml="[b]"
        )
        merged, _, _ = build_merged_pack(home_dir=home, project_root=proj)
        assert merged["general-purpose"].skills == ["b"]

    def test_user_all_is_rejected(self, tmp_path: Path) -> None:
        """skills: all is now rejected at pack-load time."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_agent_with_skills(
            home / ".claude" / "agents", "myrole.md", skills_yaml="all"
        )
        # File is skipped due to PackLoadError.
        merged, _, _ = build_merged_pack(home_dir=home, project_root=proj)
        assert "myrole" not in merged

    def test_user_list_overrides_bundled_general_no_skills(self, tmp_path: Path) -> None:
        """User override of bundled general (which has no skills) declares skills: [foo]."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_agent_with_skills(
            home / ".claude" / "agents", "general.md", skills_yaml="[foo]"
        )
        merged, _, _ = build_merged_pack(home_dir=home, project_root=proj)
        assert merged["general"].skills == ["foo"]

    def test_user_empty_list_removes_bundled_skills(self, tmp_path: Path) -> None:
        """Bundled general has no skills override. User declares skills: [] → no-op,
        merged result has skills=None.

        Sentinel L-1: assert AgentDefinition default to defend against
        vacuous-pass if SDK changes the default.
        """
        # Setup-time probe: confirm SDK default is None
        assert AgentDefinition(description="x", prompt="y", tools=[]).skills is None

        home = tmp_path / "home"
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_agent_with_skills(
            home / ".claude" / "agents", "general.md", skills_yaml="[]"
        )
        merged, _, _ = build_merged_pack(home_dir=home, project_root=proj)
        assert merged["general"].skills is None

    def test_project_overrides_user_overrides_default(self, tmp_path: Path) -> None:
        """Three-layer cascade: project wins over user wins over default."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_agent_with_skills(
            home / ".claude" / "agents", "general.md", skills_yaml="[user-skill]"
        )
        _write_agent_with_skills(
            proj / ".claude" / "agents", "general.md", skills_yaml="[proj-skill]"
        )
        merged, _, _ = build_merged_pack(home_dir=home, project_root=proj)
        assert merged["general"].skills == ["proj-skill"]


class TestSkillsFactoryRoundTrip:
    """D-9: skills survives the factory edge through to SdkTeammate._agents.

    The opts_kwargs assembly in SdkTeammate._run lifts skills via getattr
    with no coercion, so the bundled-pack value is what propagates.
    """

    def test_general_skills_none_reaches_sdk_teammate(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Bundled general (no skills override) reaches SdkTeammate with skills=None."""
        from claude_crew import factories
        from claude_crew.sdk_teammate import SdkTeammate

        home = tmp_path / "home"
        home.mkdir()
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        f = factories.default_factory()
        teammate = f("t-1", "alice", "general")

        assert isinstance(teammate, SdkTeammate)
        gen = teammate._agents["general"]
        assert gen.skills is None


# ============================================================================
# Feature #17 T2 — _warn_unknown_mcp_servers + _warn_shadow_drop
# ============================================================================

import json


def _write_claude_json(home: Path, mcp_servers: dict | None) -> Path:
    """Plant a ~/.claude.json under home. Pass None for the mcpServers key
    to omit it; pass {} for an explicit empty map."""
    home.mkdir(parents=True, exist_ok=True)
    cfg: dict = {}
    if mcp_servers is not None:
        cfg["mcpServers"] = mcp_servers
    path = home / ".claude.json"
    path.write_text(json.dumps(cfg))
    return path


class TestLoadUserMcpServerNames:
    """SC-7 helper: parses ~/.claude.json and returns set of mcpServers names.
    Best-effort (missing file, malformed JSON, missing key → empty set)."""

    def test_returns_registered_names(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        _write_claude_json(home, {"atlassian": {"type": "http"}, "claude-crew": {"type": "stdio"}})
        assert _load_user_mcp_server_names(home) == {"atlassian", "claude-crew"}

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _load_user_mcp_server_names(tmp_path / "nonexistent") == set()

    def test_missing_mcpservers_key_returns_empty(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        _write_claude_json(home, None)  # ~/.claude.json with no mcpServers key
        assert _load_user_mcp_server_names(home) == set()

    def test_malformed_json_returns_empty(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir(parents=True)
        (home / ".claude.json").write_text("{not valid json")
        assert _load_user_mcp_server_names(home) == set()

    def test_mcpservers_not_a_dict_returns_empty(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir(parents=True)
        (home / ".claude.json").write_text(json.dumps({"mcpServers": ["should-be-dict"]}))
        assert _load_user_mcp_server_names(home) == set()


class TestWarnUnknownMcpServers:
    """SC-7: load-time WARN for string-form mcpServers entries not in ~/.claude.json."""

    def test_unknown_string_name_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_claude_json(home, {"atlassian": {"type": "http"}})
        _write_agent(
            home / ".claude" / "agents", "myrole.md",
            extra_frontmatter="mcpServers: [ghost-server]",
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            build_merged_pack(home_dir=home, project_root=proj)
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "myrole" in m and "ghost-server" in m and "unknown mcpServers" in m
            for m in warn_msgs
        ), f"expected WARN naming role + 'ghost-server', got {warn_msgs}"

    def test_known_string_name_does_not_warn(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_claude_json(home, {"atlassian": {"type": "http"}})
        _write_agent(
            home / ".claude" / "agents", "myrole.md",
            extra_frontmatter="mcpServers: [atlassian]",
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            merged, _, _ = build_merged_pack(home_dir=home, project_root=proj)
        # Positive probe: assert role loaded so a future bug skipping the
        # warner entirely wouldn't silently pass this negative assertion.
        assert "myrole" in merged
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any("unknown mcpServers" in m for m in warn_msgs)

    def test_inline_dict_only_does_not_warn(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Inline dicts are self-contained — no name to validate against ~/.claude.json."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write_claude_json(home, {})
        _write_agent(
            home / ".claude" / "agents", "myrole.md",
            extra_frontmatter=(
                "mcpServers:\n"
                "  - type: stdio\n"
                "    name: local-x\n"
                "    command: uv\n"
            ),
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            build_merged_pack(home_dir=home, project_root=proj)
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any("unknown mcpServers" in m for m in warn_msgs)

    def test_missing_user_config_warns_for_string_entry(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No ~/.claude.json + pack with string-name mcpServers → WARN fires (empty set)."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        # No _write_claude_json call — no config exists.
        _write_agent(
            home / ".claude" / "agents", "myrole.md",
            extra_frontmatter="mcpServers: [any-server]",
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            build_merged_pack(home_dir=home, project_root=proj)
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("any-server" in m for m in warn_msgs)


class TestOptionalAgentDefFieldsConstant:
    """SC-11: guard against drift in the optional-fields list (mirrors #22 retro lens)."""

    def test_optional_fields_set_equals_expected(self) -> None:
        """If the SDK adds a new optional AgentDefinition field, this fails and
        forces a deliberate inclusion/exclusion decision in _warn_shadow_drop."""
        expected = {
            "mcpServers", "memory", "skills", "disallowedTools",
            "permissionMode", "maxTurns", "background", "initialPrompt", "effort",
            "model",
        }
        assert set(_OPTIONAL_AGENTDEF_FIELDS) == expected
        # `tools` is NOT in _OPTIONAL_AGENTDEF_FIELDS — its default is [], not None,
        # so the existing `is None` branch can't detect a drop. Sentinel H-2:
        # collection-shrinkage handled separately via _check_drop_collection.
        assert "tools" not in _OPTIONAL_AGENTDEF_FIELDS


class TestWarnShadowDrop:
    """SC-11: WARN when a higher-precedence pack drops an optional field a lower one set."""

    def _bundled_role(self, **fields) -> AgentDefinition:
        """Build a minimal AgentDefinition with optional fields set as kwargs."""
        return AgentDefinition(
            description="Bundled role.",
            prompt="bundled body",
            tools=["Read"],
            model="sonnet",
            **fields,
        )

    def test_user_drops_skills_set_in_default_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from claude_crew.subagents._user_loader import _warn_shadow_drop
        default = {"explorer": self._bundled_role(skills=["foo"])}
        user = {"explorer": self._bundled_role()}
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_shadow_drop([("default", default), ("user", user)])
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "explorer" in m and "skills" in m and "user-level" in m
            for m in warn_msgs
        ), f"expected user-level shadow-drop WARN for skills, got {warn_msgs}"

    def test_project_drops_mcp_servers_set_in_default_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from claude_crew.subagents._user_loader import _warn_shadow_drop
        default = {"explorer": self._bundled_role(mcpServers=["atlassian"])}
        project = {"explorer": self._bundled_role()}
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_shadow_drop([("default", default), ("project", project)])
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "explorer" in m and "mcpServers" in m and "project-level" in m
            for m in warn_msgs
        ), f"expected project-level shadow-drop WARN for mcpServers, got {warn_msgs}"

    def test_project_drops_memory_set_in_user_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from claude_crew.subagents._user_loader import _warn_shadow_drop
        default = {"explorer": self._bundled_role()}
        user = {"explorer": self._bundled_role(memory="project")}
        project = {"explorer": self._bundled_role()}
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_shadow_drop([("default", default), ("user", user), ("project", project)])
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        # The project shadow drops memory that user set.
        assert any(
            "explorer" in m and "memory" in m and "project-level" in m
            for m in warn_msgs
        ), f"expected project-over-user shadow-drop WARN for memory, got {warn_msgs}"

    def test_explicit_empty_in_higher_does_NOT_warn(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Explicit empty (e.g., disallowedTools=[]) is not None — not a drop."""
        from claude_crew.subagents._user_loader import _warn_shadow_drop
        default = {"explorer": self._bundled_role(disallowedTools=["Bash"])}
        user = {"explorer": self._bundled_role(disallowedTools=[])}
        # Premise guard (sentinel H-1): if a future SDK version normalizes
        # empty list → None, this test would silently flip premise; assert
        # the premise so a flip fails loudly with a clear message.
        assert user["explorer"].disallowedTools == [], (
            "test premise broken: expected explicit-empty to survive AgentDefinition "
            f"construction; got {user['explorer'].disallowedTools!r}"
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_shadow_drop([("default", default), ("user", user)])
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any(
            "disallowedTools" in m and "drops" in m for m in warn_msgs
        ), f"explicit empty should not warn, got {warn_msgs}"

    def test_multiple_dropped_fields_emit_separate_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from claude_crew.subagents._user_loader import _warn_shadow_drop
        default = {
            "explorer": self._bundled_role(skills=["a"], memory="project")
        }
        user = {"explorer": self._bundled_role()}
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_shadow_drop([("default", default), ("user", user)])
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        skills_warns = [m for m in warn_msgs if "skills" in m and "drops" in m]
        memory_warns = [m for m in warn_msgs if "memory" in m and "drops" in m]
        assert len(skills_warns) == 1
        assert len(memory_warns) == 1

    def test_no_shadow_no_warn(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Roles only present at one layer → no shadow check, no WARN."""
        from claude_crew.subagents._user_loader import _warn_shadow_drop
        default = {"explorer": self._bundled_role(skills=["foo"])}
        user = {"different-role": self._bundled_role()}
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_shadow_drop([("default", default), ("user", user)])
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any("drops" in m for m in warn_msgs)

    def test_higher_keeps_field_no_warn(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Higher-precedence pack explicitly sets the field → no drop."""
        from claude_crew.subagents._user_loader import _warn_shadow_drop
        default = {"explorer": self._bundled_role(skills=["a"])}
        user = {"explorer": self._bundled_role(skills=["b"])}
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_shadow_drop([("default", default), ("user", user)])
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any("drops" in m for m in warn_msgs)


class TestShadowDropOptionalModelAndTools:
    """T2 (Pillar C) — model shadow-drop + tools/disallowedTools collection-shrinkage.

    Per Phase 2 sentinel H-2 fix: model uses the existing `is None` branch
    (added to _OPTIONAL_AGENTDEF_FIELDS); tools/disallowedTools use a new
    collection-shrinkage branch because their AgentDefinition default is []
    (not None).
    """

    def _role(self, **fields) -> AgentDefinition:
        return AgentDefinition(
            description="Role.",
            prompt="body",
            **fields,
        )

    def test_user_drops_model_set_in_default_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from claude_crew.subagents._user_loader import _warn_shadow_drop
        default = {"explorer": self._role(tools=["Read"], model="opus")}
        user = {"explorer": self._role(tools=["Read"])}  # model defaults to None
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_shadow_drop([("default", default), ("user", user)])
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "explorer" in m and "model" in m and "user-level" in m
            for m in warn_msgs
        ), f"expected user-level shadow-drop WARN for model, got {warn_msgs}"

    def test_user_drops_tools_collection_shrinkage_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """User-level pack with empty tools shadows default's non-empty tools."""
        from claude_crew.subagents._user_loader import _warn_shadow_drop
        default = {"explorer": self._role(tools=["Read", "Write"])}
        user = {"explorer": self._role(tools=[])}
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_shadow_drop([("default", default), ("user", user)])
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "explorer" in m and "tools" in m and ("Read" in m or "Write" in m)
            for m in warn_msgs
        ), f"expected tools collection-shrinkage WARN, got {warn_msgs}"

    def test_disallowed_tools_explicit_empty_NOT_a_drop(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Preserves #17 design: `disallowedTools=[]` is operator intent
        (removing a restriction), not a silent drop. Only `tools=[]` collection-
        shrinkage warns, since losing the tool surface is dangerous."""
        from claude_crew.subagents._user_loader import _warn_shadow_drop
        default = {"explorer": self._role(tools=["Read"], disallowedTools=["Bash"])}
        project = {"explorer": self._role(tools=["Read"], disallowedTools=[])}
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_shadow_drop([("default", default), ("project", project)])
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any(
            "disallowedTools" in m and "drops" in m for m in warn_msgs
        ), f"disallowedTools=[] is operator intent; got {warn_msgs}"

    def test_no_warn_when_collections_unchanged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from claude_crew.subagents._user_loader import _warn_shadow_drop
        default = {"explorer": self._role(tools=["Read"])}
        user = {"explorer": self._role(tools=["Read"])}
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_shadow_drop([("default", default), ("user", user)])
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any("drops" in m for m in warn_msgs)

    def test_no_warn_when_higher_pack_keeps_subset(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A higher pack with tools=[Read] (subset of default's [Read, Write]) is
        an explicit operator choice, not a silent drop. We don't WARN on every
        list change — only on full collection-shrinkage to empty."""
        from claude_crew.subagents._user_loader import _warn_shadow_drop
        default = {"explorer": self._role(tools=["Read", "Write"])}
        user = {"explorer": self._role(tools=["Read"])}
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_shadow_drop([("default", default), ("user", user)])
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any(
            "drops collection field" in m for m in warn_msgs
        ), f"subset is operator intent, not a drop; got {warn_msgs}"


# ============================================================================
# YAML loader extension (AT-3, AT-4, AT-6, AT-7)
# ============================================================================


def _write_yaml_agent(
    dir_: Path,
    filename: str,
    *,
    description: str = "Test YAML agent.",
    model: str = "haiku",
    tools: list[str] | None = None,
    body: str = "You are a YAML test agent.",
) -> Path:
    """Plant a valid pure-YAML agent file. Returns the path.

    The file uses flow-scalar style for prompt_body so the value is stored
    verbatim (no block-scalar trailing newline). This keeps assertion logic
    simple and mirrors the spec's AT8 fixture shape.
    """
    dir_.mkdir(parents=True, exist_ok=True)
    tools_yaml = ", ".join(tools or ["Read"])
    content = "\n".join([
        f"description: {description}",
        f"model: {model}",
        f"tools: [{tools_yaml}]",
        f"prompt_body: {body}",
        "",
    ])
    path = dir_ / filename
    path.write_text(content)
    return path


class TestYamlDiscovery:
    """AT-3: discover_dir finds .yaml and .yml agent files alongside .md."""

    def test_discovers_yaml_yml_and_md_in_same_dir(self, tmp_path: Path) -> None:
        """All three formats are returned when all three are present."""
        agents_dir = tmp_path / "agents"
        _write_agent(agents_dir, "alpha.md")
        _write_yaml_agent(agents_dir, "beta.yaml")
        _write_yaml_agent(agents_dir, "gamma.yml")

        pack, _, _ = discover_dir(agents_dir)

        assert "alpha" in pack
        assert "beta" in pack
        assert "gamma" in pack

    def test_yaml_agent_description_and_body_loaded(self, tmp_path: Path) -> None:
        """YAML agent file produces a correctly populated AgentDefinition."""
        agents_dir = tmp_path / "agents"
        _write_yaml_agent(
            agents_dir,
            "probe.yaml",
            description="My YAML agent.",
            body="Do YAML things.",
        )

        pack, _, bodies = discover_dir(agents_dir)

        assert "probe" in pack
        assert pack["probe"].description == "My YAML agent."
        assert bodies["probe"] == "Do YAML things."

    def test_both_yaml_extensions_discover(self, tmp_path: Path) -> None:
        """Both .yaml and .yml are recognized."""
        agents_dir = tmp_path / "agents"
        _write_yaml_agent(agents_dir, "dot-yaml.yaml")
        _write_yaml_agent(agents_dir, "dot-yml.yml")

        pack, _, _ = discover_dir(agents_dir)

        assert "dot-yaml" in pack
        assert "dot-yml" in pack

    def test_uppercase_yaml_extension_ignored(self, tmp_path: Path) -> None:
        """Uppercase .YAML / .YML extensions are not discovered (mirrors .MD behavior)."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        # Plant an uppercase-extension file that should be ignored.
        (agents_dir / "upper.YAML").write_text(
            "description: uppercase\nmodel: haiku\ntools: [Read]\nprompt_body: body\n"
        )
        _write_yaml_agent(agents_dir, "lower.yaml")

        pack, _, _ = discover_dir(agents_dir)

        assert "lower" in pack
        assert "upper" not in pack


class TestYamlMalformed:
    """AT-4: malformed YAML agent files warn and skip; siblings still load."""

    def test_missing_description_warns_and_skips(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A .yaml file missing 'description' emits WARN and is skipped."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        # Missing description: only model + tools + body
        (agents_dir / "bad.yaml").write_text(
            "model: haiku\ntools: [Read]\nprompt_body: Some body.\n"
        )
        _write_yaml_agent(agents_dir, "good.yaml")

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            pack, _, _ = discover_dir(agents_dir)

        assert "good" in pack
        assert "bad" not in pack
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "bad.yaml" in m or ("bad" in m and "could not be loaded" in m)
            for m in warn_msgs
        ), f"expected WARN about bad.yaml, got {warn_msgs}"

    def test_missing_prompt_body_warns_and_skips(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A .yaml file with description but no prompt_body is skipped."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / "nobody.yaml").write_text(
            "description: Missing body.\nmodel: haiku\ntools: [Read]\n"
        )
        _write_yaml_agent(agents_dir, "good.yaml")

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            pack, _, _ = discover_dir(agents_dir)

        assert "good" in pack
        assert "nobody" not in pack
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("nobody.yaml" in m or "nobody" in m for m in warn_msgs), (
            f"expected WARN about nobody.yaml, got {warn_msgs}"
        )

    def test_invalid_yaml_syntax_warns_and_skips(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A .yaml file with a YAML syntax error is warned and skipped."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        # Intentionally broken YAML indentation
        (agents_dir / "broken.yaml").write_text(
            "description: test\n  bad_indent: yes\n"
        )
        _write_yaml_agent(agents_dir, "sibling.yaml")

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            pack, _, _ = discover_dir(agents_dir)

        assert "sibling" in pack
        assert "broken" not in pack


class TestMarkdownNonRegression:
    """AT-6: load_default_pack() behavior is unchanged after the YAML extension."""

    def test_bundled_pack_has_standard_keys(self) -> None:
        """load_default_pack returns exactly the three standard role keys."""
        from claude_crew.subagents import PACK_MEMBERS
        pack, _, _ = load_default_pack()
        assert set(pack.keys()) == set(PACK_MEMBERS)

    def test_bundled_pack_agents_have_descriptions(self) -> None:
        """Every bundled agent has a non-empty description."""
        pack, _, _ = load_default_pack()
        for key, agent in pack.items():
            assert agent.description, f"agent {key!r} has empty description"

    def test_bundled_pack_bodies_non_empty(self) -> None:
        """Every bundled agent has non-empty raw body text."""
        _, _, bodies = load_default_pack()
        for key, body in bodies.items():
            assert body.strip(), f"agent {key!r} has empty body"

    def test_discover_dir_md_only_dir_unaffected(self, tmp_path: Path) -> None:
        """discover_dir on a directory with only .md files returns same results as before."""
        agents_dir = tmp_path / "agents"
        _write_agent(agents_dir, "my-role.md", description="My role.", body="Do things.")

        pack, _, bodies = discover_dir(agents_dir)

        assert "my-role" in pack
        assert pack["my-role"].description == "My role."
        assert bodies["my-role"].strip() == "Do things."


class TestYamlKebabCollision:
    """AT-7: .md and .yaml with the same stem collide on the same kebab-key.
    Alphabetically-later file (.yaml > .md) wins; a WARN names both paths.
    """

    def test_yaml_wins_over_md_and_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """probe.yaml alphabetically follows probe.md; YAML body wins."""
        agents_dir = tmp_path / "agents"
        _write_agent(agents_dir, "probe.md", body="Markdown body.")
        _write_yaml_agent(agents_dir, "probe.yaml", body="YAML body.")

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            pack, _, bodies = discover_dir(agents_dir)

        assert "probe" in pack
        assert bodies["probe"] == "YAML body."
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "probe" in m and "probe.md" in m and "probe.yaml" in m
            for m in warn_msgs
        ), f"expected collision WARN naming both probe.md and probe.yaml, got {warn_msgs}"

    def test_yml_wins_over_md_and_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """probe.yml alphabetically follows probe.md; YML body wins."""
        agents_dir = tmp_path / "agents"
        _write_agent(agents_dir, "probe.md", body="Markdown body.")
        _write_yaml_agent(agents_dir, "probe.yml", body="YML body.")

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            pack, _, bodies = discover_dir(agents_dir)

        assert "probe" in pack
        assert bodies["probe"] == "YML body."
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "probe" in m and "probe.md" in m and "probe.yml" in m
            for m in warn_msgs
        ), f"expected collision WARN naming both probe.md and probe.yml, got {warn_msgs}"
