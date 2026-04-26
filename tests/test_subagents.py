"""Tests for the default subagent pack (Feature #3a).

Covers Task 1 (loader + pack files + security doc) ACs from
`doc/features/FEATURE-default-subagent-pack.md` Phase 3 — i.e.,
SC-3 (per-subagent budgets pinned), SC-4 (default models), SC-5
(hermetic prompts), SC-7 (determinism), SC-11 (security section regex),
plus loader sad paths and merge_packs semantics.

Tasks 2–4 add their own classes to this file; this module is the
single home for #3a regression coverage.
"""

from __future__ import annotations

import re
from pathlib import Path
from textwrap import dedent

import pytest
from claude_agent_sdk.types import AgentDefinition

from claude_crew.subagents import (
    PACK_MEMBERS,
    PackLoadError,
    load_default_pack,
    merge_packs,
)
from claude_crew.subagents._loader import PackFrontmatter, parse_pack_file


PACK_DIR = Path(__file__).parent.parent / "claude_crew" / "subagents"


class TestPackContents:
    """SC-3, SC-4, SC-7 — the bundled pack matches its declared contract."""

    def test_keys_are_exactly_the_three_pack_members(self) -> None:
        pack = load_default_pack()
        assert set(pack.keys()) == {"explorer", "planner", "general-purpose"}
        assert PACK_MEMBERS == ("explorer", "planner", "general-purpose")

    def test_explorer_contract(self) -> None:
        pack = load_default_pack()
        explorer = pack["explorer"]
        assert isinstance(explorer, AgentDefinition)
        assert explorer.model == "haiku"
        assert explorer.tools == ["Read", "Grep", "Glob"]
        assert explorer.effort == "low"
        assert explorer.maxTurns == 10

    def test_planner_contract(self) -> None:
        pack = load_default_pack()
        planner = pack["planner"]
        assert planner.model == "sonnet"
        assert planner.tools == ["Read", "Grep", "Glob", "Write"]
        assert planner.effort == "high"
        assert planner.maxTurns == 20
        # The structural scope-creep guard is the initialPrompt.
        assert planner.initialPrompt is not None
        assert "acceptance criteria" in planner.initialPrompt.lower()

    def test_general_purpose_contract(self) -> None:
        pack = load_default_pack()
        gp = pack["general-purpose"]
        assert gp.model == "sonnet"
        assert gp.effort == "medium"
        assert gp.maxTurns == 20
        # Network access yes, shell and recursion no.
        assert "WebFetch" in gp.tools
        assert "WebSearch" in gp.tools
        assert "Bash" not in gp.tools
        assert "Task" not in gp.tools

    def test_no_pack_member_has_task_tool(self) -> None:
        """Subagents are leaves. None of them get Task — locked by Phase 1."""
        pack = load_default_pack()
        for name, agent in pack.items():
            assert "Task" not in (agent.tools or []), (
                f"{name} must not have Task — subagents are leaves"
            )

    def test_load_default_pack_is_deterministic(self) -> None:
        """SC-7 — two calls produce identical pack."""
        a = load_default_pack()
        b = load_default_pack()
        assert a == b


class TestPackHermeticity:
    """SC-5 — pack content is in-repo, prompt body is the literal file body."""

    def test_explorer_prompt_matches_file_body(self) -> None:
        pack = load_default_pack()
        body = _read_body(PACK_DIR / "explorer.md")
        assert pack["explorer"].prompt == body

    def test_planner_prompt_matches_file_body(self) -> None:
        pack = load_default_pack()
        body = _read_body(PACK_DIR / "planner.md")
        assert pack["planner"].prompt == body

    def test_general_purpose_prompt_matches_file_body(self) -> None:
        pack = load_default_pack()
        body = _read_body(PACK_DIR / "general_purpose.md")
        assert pack["general-purpose"].prompt == body


class TestParsePackFile:
    """parse_pack_file happy paths and sad paths."""

    def test_happy_path_full_frontmatter(self, tmp_path: Path) -> None:
        f = tmp_path / "explorer.md"
        f.write_text(dedent("""\
            ---
            description: A reader.
            model: haiku
            tools: [Read, Grep, Glob]
            effort: low
            maxTurns: 10
            initialPrompt: Begin by stating what you'll search for.
            ---

            # Role
            You are an explorer.
            """))
        key, agent = parse_pack_file(f)
        assert key == "explorer"
        assert agent.model == "haiku"
        assert agent.tools == ["Read", "Grep", "Glob"]
        assert agent.effort == "low"
        assert agent.maxTurns == 10
        assert agent.initialPrompt == "Begin by stating what you'll search for."
        assert "You are an explorer." in agent.prompt

    def test_filename_underscore_to_kebab_in_key(self, tmp_path: Path) -> None:
        f = tmp_path / "general_purpose.md"
        f.write_text(dedent("""\
            ---
            description: Catch-all.
            model: sonnet
            tools: [Read]
            ---

            body
            """))
        key, _ = parse_pack_file(f)
        assert key == "general-purpose"

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "broken.md"
        f.write_text(dedent("""\
            ---
            description: Missing tools.
            model: haiku
            ---

            body
            """))
        with pytest.raises(PackLoadError) as exc:
            parse_pack_file(f)
        assert "tools" in str(exc.value)
        assert str(f) in str(exc.value)

    def test_empty_body_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.md"
        f.write_text(dedent("""\
            ---
            description: A reader.
            model: haiku
            tools: [Read]
            ---

            """))
        with pytest.raises(PackLoadError) as exc:
            parse_pack_file(f)
        assert str(f) in str(exc.value)

    def test_no_frontmatter_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "no_fm.md"
        f.write_text("just some markdown\n")
        with pytest.raises(PackLoadError):
            parse_pack_file(f)

    def test_extra_unknown_frontmatter_field_is_ignored(self, tmp_path: Path) -> None:
        """Forward-compat: unknown fields are dropped, not errored."""
        f = tmp_path / "fwd.md"
        f.write_text(dedent("""\
            ---
            description: A reader.
            model: haiku
            tools: [Read]
            future_field: some value
            ---

            body here
            """))
        key, agent = parse_pack_file(f)
        assert key == "fwd"
        assert agent.model == "haiku"


class TestMergePacks:
    """Phase 1 contract: per-key override at whole-AgentDefinition level."""

    def _agent(self, **overrides) -> AgentDefinition:
        defaults = {"description": "x", "prompt": "y", "model": "sonnet"}
        return AgentDefinition(**{**defaults, **overrides})

    def test_user_wins_on_collision_whole_definition(self) -> None:
        default = {"planner": self._agent(model="sonnet", maxTurns=20)}
        user = {"planner": self._agent(model="opus", maxTurns=5)}
        result = merge_packs(default, user)
        # User's full AgentDefinition replaces default's — no field merge.
        assert result["planner"].model == "opus"
        assert result["planner"].maxTurns == 5

    def test_user_adds_non_conflicting_key(self) -> None:
        default = {"explorer": self._agent()}
        user = {"reviewer": self._agent(description="reviews")}
        result = merge_packs(default, user)
        assert set(result.keys()) == {"explorer", "reviewer"}

    def test_none_user_returns_default(self) -> None:
        default = {"explorer": self._agent()}
        assert merge_packs(default, None) == default

    def test_empty_user_returns_default(self) -> None:
        default = {"explorer": self._agent()}
        assert merge_packs(default, {}) == default


class TestSecurityDoc:
    """SC-11 — the pack README documents CLAUDE.md visibility."""

    def test_readme_has_security_section_heading(self) -> None:
        readme = PACK_DIR / "README.md"
        text = readme.read_text()
        # Heading regex per Phase 1: Security[: ].*CLAUDE\.md
        assert re.search(r"Security[: ].*CLAUDE\.md", text), (
            "Pack README must contain a section heading matching Security[: ].*CLAUDE\\.md"
        )

    def test_readme_names_network_capable_member(self) -> None:
        readme = PACK_DIR / "README.md"
        text = readme.read_text()
        assert "general-purpose" in text
        assert "WebFetch" in text or "WebSearch" in text

    def test_readme_recommends_audit(self) -> None:
        readme = PACK_DIR / "README.md"
        text = readme.read_text().lower()
        assert "audit" in text


def _read_body(path: Path) -> str:
    """Return the markdown body (everything after the closing `---`)."""
    text = path.read_text()
    # Frontmatter delimited by --- on its own line.
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        raise AssertionError(f"{path} does not have YAML frontmatter")
    return parts[2]
