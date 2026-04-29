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


def _parse(extra_frontmatter: str = "") -> tuple[str, object, PackFrontmatter]:
    """Parse a minimal pack with optional extra frontmatter; return 3-tuple."""
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
        _, _, fm = _parse("settingSources: []")
        assert fm.settingSources == []

    def test_agent_definition_unchanged(self) -> None:
        """AgentDefinition must not gain a settingSources attribute."""
        _, agent, _ = _parse("settingSources: []")
        assert not hasattr(agent, "settingSources"), (
            "settingSources must not be forwarded into AgentDefinition"
        )

    def test_agent_model_unchanged(self) -> None:
        """Other AgentDefinition fields are not affected."""
        _, agent, _ = _parse("settingSources: []")
        assert agent.model == "haiku"
        assert agent.tools == ["Read"]


# ---------------------------------------------------------------------------
# Scenario: settingSources: [project] is parsed
# ---------------------------------------------------------------------------


class TestSettingSourcesSingleItem:
    """Scenario: pack file with settingSources: [project] is parsed."""

    def test_frontmatter_setting_sources_is_project(self) -> None:
        """PackFrontmatter.settingSources == ["project"]."""
        _, _, fm = _parse("settingSources: [project]")
        assert fm.settingSources == ["project"]

    def test_all_valid_items_accepted(self) -> None:
        """All three valid values are accepted individually."""
        for source in ("user", "project", "local"):
            _, _, fm = _parse(f"settingSources: [{source}]")
            assert fm.settingSources == [source]

    def test_all_valid_items_together(self) -> None:
        """A list of all three valid sources parses without error."""
        _, _, fm = _parse("settingSources: [user, project, local]")
        assert fm.settingSources == ["user", "project", "local"]


# ---------------------------------------------------------------------------
# Scenario: no settingSources field → default is None
# ---------------------------------------------------------------------------


class TestSettingSourcesAbsent:
    """Scenario: pack file without settingSources preserves default."""

    def test_frontmatter_setting_sources_is_none(self) -> None:
        """PackFrontmatter.settingSources is None when field is omitted."""
        _, _, fm = _parse()
        assert fm.settingSources is None

    def test_key_and_agent_still_returned(self) -> None:
        """The 3-tuple is fully populated even when settingSources is absent."""
        key, agent, fm = _parse()
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
