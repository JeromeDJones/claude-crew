"""Tests for PackFrontmatter.settingSources (Feature #11, T1).

Covers the BDD scenarios from FEATURE-lightweight-subagent-context.md Phase 3 T1:

- settingSources: [] → PackFrontmatter.settingSources == [], AgentDefinition unchanged
- settingSources: [project] → PackFrontmatter.settingSources == ["project"]
- No settingSources field → PackFrontmatter.settingSources is None
- Invalid item (bad_value) → PackLoadError naming the invalid item
- Mixed valid+invalid → PackLoadError

All scenarios use parse_pack_text and check both the returned AgentDefinition
(which must not gain a settingSources field) and the returned PackFrontmatter.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from claude_crew.subagents._loader import PackFrontmatter, PackLoadError, parse_pack_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pack_text(extra_frontmatter: str = "", body: str = "You are a test agent.") -> str:
    """Build a minimal valid pack file text with optional extra frontmatter."""
    lines = [
        "---",
        "description: A test agent.",
        "model: haiku",
        "tools: [Read]",
    ]
    if extra_frontmatter:
        lines.append(extra_frontmatter.rstrip("\n"))
    lines.extend(["---", "", body, ""])
    return "\n".join(lines)


def _parse(extra_frontmatter: str = "") -> tuple[str, object, PackFrontmatter, str]:
    """Parse a minimal pack with optional extra frontmatter; return 4-tuple."""
    text = _pack_text(extra_frontmatter)
    path = Path("test_agent.md")
    return parse_pack_text(text, path)


# ---------------------------------------------------------------------------
# Scenario: settingSources: [] is parsed
# ---------------------------------------------------------------------------


class TestSettingSourcesEmpty:
    """Scenario: pack file with settingSources: [] is parsed."""

    def test_frontmatter_setting_sources_is_empty_list(self) -> None:
        """PackFrontmatter.settingSources == [] when frontmatter declares settingSources: []."""
        _, _, fm, _ = _parse("settingSources: []")
        assert fm.settingSources == []

    def test_agent_definition_unchanged(self) -> None:
        """AgentDefinition must not gain a settingSources attribute."""
        _, agent, _, _ = _parse("settingSources: []")
        assert not hasattr(agent, "settingSources"), (
            "settingSources must not be forwarded into AgentDefinition"
        )

    def test_agent_model_unchanged(self) -> None:
        """Other AgentDefinition fields are not affected."""
        _, agent, _, _ = _parse("settingSources: []")
        assert agent.model == "haiku"
        assert agent.tools == ["Read"]


# ---------------------------------------------------------------------------
# Scenario: settingSources: [project] is parsed
# ---------------------------------------------------------------------------


class TestSettingSourcesSingleItem:
    """Scenario: pack file with settingSources: [project] is parsed."""

    def test_frontmatter_setting_sources_is_project(self) -> None:
        """PackFrontmatter.settingSources == ["project"]."""
        _, _, fm, _ = _parse("settingSources: [project]")
        assert fm.settingSources == ["project"]

    def test_all_valid_items_accepted(self) -> None:
        """All three valid values are accepted individually."""
        for source in ("user", "project", "local"):
            _, _, fm, _ = _parse(f"settingSources: [{source}]")
            assert fm.settingSources == [source]

    def test_all_valid_items_together(self) -> None:
        """A list of all three valid sources parses without error."""
        _, _, fm, _ = _parse("settingSources: [user, project, local]")
        assert fm.settingSources == ["user", "project", "local"]


# ---------------------------------------------------------------------------
# Scenario: no settingSources field → default is None
# ---------------------------------------------------------------------------


class TestSettingSourcesAbsent:
    """Scenario: pack file without settingSources preserves default."""

    def test_frontmatter_setting_sources_is_none(self) -> None:
        """PackFrontmatter.settingSources is None when field is omitted."""
        _, _, fm, _ = _parse()
        assert fm.settingSources is None

    def test_key_and_agent_still_returned(self) -> None:
        """The 3-tuple is fully populated even when settingSources is absent."""
        key, agent, fm, _ = _parse()
        assert key == "test-agent"
        assert agent.model == "haiku"
        assert isinstance(fm, PackFrontmatter)


# ---------------------------------------------------------------------------
# Scenario: invalid settingSources item raises PackLoadError
# ---------------------------------------------------------------------------


class TestSettingSourcesInvalidItem:
    """Scenario: invalid settingSources item raises PackLoadError."""

    def test_unknown_item_raises(self, tmp_path: Path) -> None:
        """settingSources: [bad_value] → PackLoadError naming the invalid item."""
        text = _pack_text("settingSources: [bad_value]")
        path = tmp_path / "agent.md"
        with pytest.raises(PackLoadError) as exc:
            parse_pack_text(text, path)
        assert "bad_value" in str(exc.value)

    def test_error_names_the_path(self, tmp_path: Path) -> None:
        """The PackLoadError message includes the file path."""
        text = _pack_text("settingSources: [bad_value]")
        path = tmp_path / "my_agent.md"
        with pytest.raises(PackLoadError) as exc:
            parse_pack_text(text, path)
        assert str(path) in str(exc.value)

    def test_error_names_valid_values(self, tmp_path: Path) -> None:
        """The PackLoadError message mentions valid values."""
        text = _pack_text("settingSources: [bad_value]")
        path = tmp_path / "agent.md"
        with pytest.raises(PackLoadError) as exc:
            parse_pack_text(text, path)
        msg = str(exc.value)
        # At least one of the valid values should appear in the message.
        assert any(v in msg for v in ("user", "project", "local"))


# ---------------------------------------------------------------------------
# Scenario: settingSources with mixed valid and invalid items raises PackLoadError
# ---------------------------------------------------------------------------


class TestSettingSourcesMixedItems:
    """Scenario: settingSources with mixed valid and invalid items raises PackLoadError."""

    def test_valid_then_invalid_raises(self, tmp_path: Path) -> None:
        """[user, invalid] → PackLoadError (stops on first invalid item)."""
        text = _pack_text("settingSources: [user, invalid]")
        path = tmp_path / "agent.md"
        with pytest.raises(PackLoadError) as exc:
            parse_pack_text(text, path)
        assert "invalid" in str(exc.value)

    def test_invalid_then_valid_raises(self, tmp_path: Path) -> None:
        """[bad, project] → PackLoadError naming the invalid item."""
        text = _pack_text("settingSources: [bad, project]")
        path = tmp_path / "agent.md"
        with pytest.raises(PackLoadError) as exc:
            parse_pack_text(text, path)
        assert "bad" in str(exc.value)


# ---------------------------------------------------------------------------
# Feature #23: skills field validation (T1)
# ---------------------------------------------------------------------------


class TestSkillsAllForm:
    """Scenario: skills: all (string form) is accepted (D-1)."""

    def test_skills_all_string_accepted(self, tmp_path: Path) -> None:
        text = _pack_text("skills: all")
        _, agent, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.skills == "all"
        assert agent.skills == "all"


class TestSkillsListForm:
    """Scenario: skills: [foo, bar] is accepted as tuple → list (existing-shape regression)."""

    def test_skills_list_accepted(self, tmp_path: Path) -> None:
        text = _pack_text("skills: [foo, bar]")
        _, agent, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.skills == ("foo", "bar")
        assert agent.skills == ["foo", "bar"]


class TestSkillsEmptyListNoOp:
    """Scenario: skills: [] is accepted as no-op (D-2)."""

    def test_empty_list_does_not_set_agent_skills(self, tmp_path: Path) -> None:
        text = _pack_text("skills: []")
        _, agent, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.skills == ()
        assert agent.skills is None


class TestSkillsSettingSourcesConflict:
    """Scenario: skills + settingSources=[] is rejected (SC-3, D-3)."""

    def test_active_skills_with_explicit_empty_settingsources_raises(self, tmp_path: Path) -> None:
        text = _pack_text("skills: [foo]\nsettingSources: []")
        with pytest.raises(PackLoadError) as exc:
            parse_pack_text(text, tmp_path / "agent.md")
        msg = str(exc.value)
        # Pin to the specific SC-3 code path: must mention both settingSources
        # and the contradiction, not a coincidental other error.
        assert "contradictory" in msg
        assert "settingSources" in msg

    def test_skills_all_with_explicit_empty_settingsources_raises(self, tmp_path: Path) -> None:
        text = _pack_text("skills: all\nsettingSources: []")
        with pytest.raises(PackLoadError) as exc:
            parse_pack_text(text, tmp_path / "agent.md")
        msg = str(exc.value)
        assert "contradictory" in msg
        assert "settingSources" in msg

    def test_empty_skills_with_empty_settingsources_accepted(self, tmp_path: Path) -> None:
        """skills:[] + settingSources:[] is consistent (no-op + no-source)."""
        text = _pack_text("skills: []\nsettingSources: []")
        _, agent, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.skills == ()
        assert fm.settingSources == []
        assert agent.skills is None

    def test_omitted_skills_with_empty_settingsources_accepted(self, tmp_path: Path) -> None:
        text = _pack_text("settingSources: []")
        _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.skills is None
        assert fm.settingSources == []

    def test_skills_with_explicit_user_project_settingsources_accepted(self, tmp_path: Path) -> None:
        text = _pack_text("skills: [foo]\nsettingSources: [user, project]")
        _, agent, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.skills == ("foo",)
        assert fm.settingSources == ["user", "project"]
        assert agent.skills == ["foo"]

    def test_skills_with_omitted_settingsources_accepted(self, tmp_path: Path) -> None:
        """SDK auto-injects ['user','project'] when settingSources is None."""
        text = _pack_text("skills: [foo]")
        _, agent, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.skills == ("foo",)
        assert fm.settingSources is None
        assert agent.skills == ["foo"]


class TestSkillsInvalidShape:
    """Scenario: malformed skills values raise PackLoadError (SC-5, sentinel M-1)."""

    @pytest.mark.parametrize(
        "value",
        [
            '""',                # empty string
            '" all "',           # whitespace-padded
            '"All"',             # wrong case
            "42",                # int
            "{foo: bar}",        # dict
            "[42, foo]",         # mixed types in list
            "[null]",            # list with null element
        ],
    )
    def test_invalid_shape_raises(self, tmp_path: Path, value: str) -> None:
        text = _pack_text(f"skills: {value}")
        with pytest.raises(PackLoadError):
            parse_pack_text(text, tmp_path / "agent.md")
