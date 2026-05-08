"""End-to-end integration tests for Feature #23 (global skills support).

Exercise the full pipeline through the public loader / factory surface:

  YAML frontmatter → _validate_frontmatter → PackFrontmatter → AgentDefinition
  → build_merged_pack cascade → default_factory closure → SdkTeammate._agents

These tests do NOT make live SDK calls. The opts_kwargs assembly inside
SdkTeammate._run reads `getattr(role_def, "skills", None)` with no
transformation, so asserting on `teammate._agents[role].skills` is the
deterministic proxy for "skills will reach ClaudeAgentOptions correctly."

Live dogfood validation (SC-1b) is a manual gate at Phase 5, not a CI test.
"""

from __future__ import annotations

import logging
from pathlib import Path
from textwrap import dedent

import pytest
from claude_agent_sdk.types import AgentDefinition

from claude_crew import factories
from claude_crew.sdk_teammate import SdkTeammate
from claude_crew.subagents._loader import PackLoadError
from claude_crew.subagents._user_loader import build_merged_pack

LOGGER = "claude_crew.subagents.loader"


def _write_skill(skills_root: Path, name: str) -> Path:
    skill_dir = skills_root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: A test skill.\n---\n\nSkill body.\n"
    )
    return skill_md


def _write_agent(agents_root: Path, filename: str, frontmatter_extra: str = "") -> Path:
    agents_root.mkdir(parents=True, exist_ok=True)
    body = dedent("""\
        ---
        description: Test agent.
        model: haiku
        tools: [Read]
        """).rstrip()
    if frontmatter_extra:
        body = body + "\n" + frontmatter_extra.rstrip()
    body = body + "\n---\n\nYou are a test agent.\n"
    path = agents_root / filename
    path.write_text(body)
    return path


# -----------------------------------------------------------------------------
# Happy paths — full pipeline correctness
# -----------------------------------------------------------------------------


class TestE2EHappyPaths:
    """Skills survive the full pipeline from YAML to SdkTeammate._agents."""

    def test_user_agent_with_skills_loads_end_to_end(
        self, monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        home = tmp_path / "home"
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        _write_skill(home / ".claude" / "skills", "test-skill")
        _write_agent(
            home / ".claude" / "agents",
            "test-role.md",
            frontmatter_extra="skills: [test-skill]",
        )
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            f = factories.default_factory()
            teammate = f("t-e2e-1", "alice", "test-role")

        assert isinstance(teammate, SdkTeammate)
        assert "test-role" in teammate._agents
        assert teammate._agents["test-role"].skills == ["test-skill"]

        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any("declares unknown skills" in m for m in warn_msgs)

    def test_bundled_general_loads_with_no_skills_override_e2e(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Bundled 'general' (formerly 'general-purpose') loads without skills override."""
        home = tmp_path / "home"
        home.mkdir()
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        f = factories.default_factory()
        teammate = f("t-e2e-2", "bob", "general")

        gen = teammate._agents["general"]
        # Bundled general.md has no skills field, so agent.skills is None.
        assert gen.skills is None
        # Sanity: explorer/planner unchanged
        assert teammate._agents["explorer"].skills is None
        assert teammate._agents["planner"].skills is None

    def test_three_layer_cascade_project_wins_e2e(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        # Bundled has general (no skills override).
        # User override → skills: [user-skill]
        _write_agent(
            home / ".claude" / "agents",
            "general.md",
            frontmatter_extra="skills: [user-skill]",
        )
        # Project override → skills: [proj-skill]
        _write_agent(
            cwd / ".claude" / "agents",
            "general.md",
            frontmatter_extra="skills: [proj-skill]",
        )
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        f = factories.default_factory()
        teammate = f("t-e2e-3", "carol", "general")

        # Project wins.
        assert teammate._agents["general"].skills == ["proj-skill"]


# -----------------------------------------------------------------------------
# Sad paths — validation, isolation, observability
# -----------------------------------------------------------------------------


class TestE2ESadPaths:
    """Failure modes: invalid configs, missing skills, sibling-file isolation."""

    def test_unknown_skill_warns_but_pack_loads(
        self, monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """SC-4: WARN at pack-load, role still loads, teammate spawns."""
        home = tmp_path / "home"
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        # No skill dir at all — every declared skill is unknown.
        _write_agent(
            home / ".claude" / "agents",
            "ghost-role.md",
            frontmatter_extra="skills: [does-not-exist]",
        )
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            f = factories.default_factory()
            teammate = f("t-e2e-4", "dan", "ghost-role")

        assert teammate._agents["ghost-role"].skills == ["does-not-exist"]

        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "ghost-role" in m and "does-not-exist" in m
            for m in warn_msgs
        )

    def test_skills_settingsources_conflict_isolates_to_one_file(
        self, monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A bad agent file is skipped with a WARN; siblings still load.

        Per existing strict_parse semantics from #3b, per-file errors are
        isolated. Here we plant ONE bad agent (skills + settingSources=[])
        and ONE good sibling — the merged pack must contain the good sibling
        and not the bad one.
        """
        home = tmp_path / "home"
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        # Bad: skills + settingSources=[] — SC-3 conflict.
        _write_agent(
            home / ".claude" / "agents",
            "bad-role.md",
            frontmatter_extra="skills: [foo]\nsettingSources: []",
        )
        # Good sibling.
        _write_agent(home / ".claude" / "agents", "good-role.md")
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        monkeypatch.chdir(cwd)

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            merged, _, _ = build_merged_pack(home_dir=home, project_root=cwd)

        assert "good-role" in merged
        assert "bad-role" not in merged

    def test_cwd_trap_produces_warn_for_project_skill(
        self, monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A-7: project skill exists at <real-project>/.claude/skills, but
        cwd is set elsewhere → spurious WARN, expected behavior."""
        home = tmp_path / "home"
        home.mkdir()
        real_project = tmp_path / "real-project"
        wrong_cwd = tmp_path / "wrong-cwd"
        wrong_cwd.mkdir()

        _write_skill(real_project / ".claude" / "skills", "proj-skill")
        # User agent declares the project skill.
        _write_agent(
            home / ".claude" / "agents",
            "myrole.md",
            frontmatter_extra="skills: [proj-skill]",
        )

        # Run with project_root pointed at the wrong dir.
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            build_merged_pack(home_dir=home, project_root=wrong_cwd)

        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("proj-skill" in m for m in warn_msgs), (
            "expected spurious WARN under cwd trap (A-7)"
        )
