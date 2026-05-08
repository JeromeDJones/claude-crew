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
    """Scenario: skills: all (string form) is now rejected at pack level (D-1 change)."""

    def test_skills_all_string_raises_pack_load_error(self, tmp_path: Path) -> None:
        """skills: 'all' is only valid at session level, not per-agent."""
        text = _pack_text("skills: all")
        with pytest.raises(PackLoadError) as exc:
            parse_pack_text(text, tmp_path / "agent.md")
        msg = str(exc.value)
        assert "skills must be a list of skill names" in msg
        assert "session level" in msg


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
        """Now skills: 'all' is rejected outright, before settingSources check."""
        text = _pack_text("skills: all\nsettingSources: []")
        with pytest.raises(PackLoadError) as exc:
            parse_pack_text(text, tmp_path / "agent.md")
        msg = str(exc.value)
        # Expect rejection of skills: 'all' form, not the settingSources contradiction.
        assert "skills must be a list of skill names" in msg

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


# ---------------------------------------------------------------------------
# T1 (Pillar E): tools / disallowedTools comma-string coercion
#
# Covers SC-5 and SC-14 from FEATURE-claude-code-agent-format-compatibility.md.
# Closes a latent bug where `tools: Read` (single string, no comma) was
# silently iterated into characters by `list(d["tools"])`.
# ---------------------------------------------------------------------------


def _build_pack(frontmatter_lines: list[str], body: str = "You are a test agent.") -> str:
    """Build a pack file text with explicit control over frontmatter lines."""
    return "\n".join(["---", *frontmatter_lines, "---", "", body, ""])


class TestToolsCoercion:
    """SC-5: tools accepts string-or-list YAML polymorphism per Claude Code spec."""

    def test_tools_list_form_unchanged(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: [Read, Write]",
        ])
        _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.tools == ("Read", "Write")

    def test_tools_comma_string(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: Read, Write, Edit, Bash",
        ])
        _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.tools == ("Read", "Write", "Edit", "Bash")

    def test_tools_single_string_no_comma_does_not_iterate_chars(self, tmp_path: Path) -> None:
        """REGRESSION: closes _loader.py:315 latent character-iteration bug.

        Pre-T1: list("Read") returns ["R", "e", "a", "d"]. Silent corruption.
        Post-T1: ["Read"].
        """
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: Read",
        ])
        _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.tools == ("Read",)
        assert fm.tools != ["R", "e", "a", "d"]

    def test_tools_comma_string_with_mcp_prefix(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: Read, mcp__knowledge-graph__search_codebase_definitions",
        ])
        _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.tools == ("Read", "mcp__knowledge-graph__search_codebase_definitions")

    def test_tools_trailing_comma_tolerated(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: Read, Write,",
        ])
        _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.tools == ("Read", "Write")

    def test_tools_double_comma_drops_empty(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: Read,,Write",
        ])
        _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.tools == ("Read", "Write")

    def test_tools_quoted_comma_string(self, tmp_path: Path) -> None:
        """A YAML-quoted single string with internal commas still splits.

        The unbracketed form `tools: "Read", "Write"` is invalid YAML
        (rejected by yaml.safe_load before reaching the coercer). The
        realistic quoted form is `tools: "Read, Write"` — one YAML string
        with an internal comma. Coercer splits it.
        """
        text = _build_pack([
            "description: T",
            "model: haiku",
            'tools: "Read, Write"',
        ])
        _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.tools == ("Read", "Write")

    def test_tools_int_raises(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: 42",
        ])
        with pytest.raises(PackLoadError, match="tools.*expected string or list.*int"):
            parse_pack_text(text, tmp_path / "agent.md")

    def test_tools_list_with_none_element_raises(self, tmp_path: Path) -> None:
        """SC-5 + sentinel L-1: list elements must be strings, not silently coerced."""
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: [null, Read]",
        ])
        with pytest.raises(PackLoadError, match="tools.*list element must be a string"):
            parse_pack_text(text, tmp_path / "agent.md")

    def test_tools_list_with_int_element_raises(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: [42, Read]",
        ])
        with pytest.raises(PackLoadError, match="tools.*list element must be a string"):
            parse_pack_text(text, tmp_path / "agent.md")


class TestDisallowedToolsCoercion:
    """SC-14: disallowedTools accepts string-or-list parity with tools."""

    def test_disallowed_tools_list_form_unchanged(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: [Read]",
            "disallowedTools: [Bash, WebFetch]",
        ])
        _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.disallowedTools == ("Bash", "WebFetch")

    def test_disallowed_tools_comma_string(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: [Read]",
            "disallowedTools: Bash, WebFetch",
        ])
        _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.disallowedTools == ("Bash", "WebFetch")

    def test_disallowed_tools_single_string_no_comma(self, tmp_path: Path) -> None:
        """Parity regression for the same character-iteration bug."""
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: [Read]",
            "disallowedTools: Bash",
        ])
        _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.disallowedTools == ("Bash",)

    def test_disallowed_tools_int_raises(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: [Read]",
            "disallowedTools: 42",
        ])
        with pytest.raises(PackLoadError, match="disallowedTools.*expected string or list.*int"):
            parse_pack_text(text, tmp_path / "agent.md")

    def test_disallowed_tools_list_with_none_raises(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: [Read]",
            "disallowedTools: [null, Bash]",
        ])
        with pytest.raises(PackLoadError, match="disallowedTools.*list element must be a string"):
            parse_pack_text(text, tmp_path / "agent.md")


# ---------------------------------------------------------------------------
# T2 (Pillar C): optional model + tools
#
# Covers SC-3, SC-4, SC-9 from FEATURE-claude-code-agent-format-compatibility.md.
# Per Claude Code spec, model and tools are optional. claude-crew defaults to
# `tools=()` (safe-by-default — no implicit tool access via SDK inherit) and
# `model=None` (SDK applies its own default at spawn). No-tools INFO emitted
# when `tools:` is absent (operator might have forgotten to declare them).
# ---------------------------------------------------------------------------


import logging  # noqa: E402  (lives down here to keep T1 block self-contained)

LOGGER = "claude_crew.subagents._loader"


class TestOptionalModel:
    """SC-3: model is optional at pack-load."""

    def test_pack_without_model_loads(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "tools: [Read]",
        ])
        _, agent, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.model is None
        assert agent.model is None

    def test_pack_with_explicit_model_unchanged(self, tmp_path: Path) -> None:
        """Regression: existing packs declaring model still work."""
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: [Read]",
        ])
        _, agent, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.model == "haiku"
        assert agent.model == "haiku"


class TestOptionalTools:
    """SC-4: tools is optional at pack-load; absent emits no-tools INFO."""

    def test_pack_without_tools_loads(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
        ])
        with caplog.at_level(logging.INFO, logger=LOGGER):
            _, agent, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.tools == ()
        assert agent.tools == []

    def test_pack_without_tools_emits_info(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
        ])
        with caplog.at_level(logging.INFO, logger=LOGGER):
            parse_pack_text(text, tmp_path / "agent.md")
        info_msgs = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert any(
            "no tools declared" in m for m in info_msgs
        ), f"expected no-tools INFO, got {info_msgs}"

    def test_pack_with_explicit_empty_tools_string_silent(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """X.3: explicit `tools: ""` is operator intent — silent.

        Distinguishes "operator forgot to declare tools" (INFO fires) from
        "operator chose no tools" (silent).
        """
        text = _build_pack([
            "description: T",
            "model: haiku",
            'tools: ""',
        ])
        with caplog.at_level(logging.INFO, logger=LOGGER):
            _, agent, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.tools == ()
        info_msgs = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert not any("no tools declared" in m for m in info_msgs), (
            f"explicit empty tools must NOT emit INFO; got {info_msgs}"
        )

    def test_pack_with_tools_silent(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: [Read]",
        ])
        with caplog.at_level(logging.INFO, logger=LOGGER):
            parse_pack_text(text, tmp_path / "agent.md")
        info_msgs = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert not any("no tools declared" in m for m in info_msgs), (
            f"declared tools must NOT emit INFO; got {info_msgs}"
        )

    def test_tools_field_is_tuple(self, tmp_path: Path) -> None:
        """Sentinel M-1: tools is tuple, not list (frozen dataclass immutability)."""
        text = _build_pack([
            "description: T",
            "model: haiku",
            "tools: [Read, Write]",
        ])
        _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert isinstance(fm.tools, tuple)


class TestMinimalPack:
    """SC-8: minimal pack with only description loads cleanly."""

    def test_description_only_pack_loads(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        text = _build_pack(["description: A minimal probe."])
        with caplog.at_level(logging.INFO, logger=LOGGER):
            _, agent, fm, _ = parse_pack_text(text, tmp_path / "minimal-probe.md")
        assert fm.description == "A minimal probe."
        assert fm.model is None
        assert fm.tools == ()
        assert agent.tools == []
        assert agent.model is None


# ---------------------------------------------------------------------------
# T3 (Pillar A+D): canonical name resolution + name/color fields
#
# Covers SC-1, SC-2, SC-2a, SC-6 from FEATURE-claude-code-agent-format-compatibility.md.
# Per Claude Code spec, `name:` is REQUIRED at the format level. claude-crew
# accepts it as canonical role key when present; falls back to file stem
# otherwise. `color:` is UI metadata, captured-and-ignored.
# ---------------------------------------------------------------------------


class TestNameField:
    """SC-1, SC-2, Q-10: name field handling."""

    def test_name_silent_accepted(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        text = _build_pack([
            "description: T",
            "name: senior-reviewer",
            "tools: [Read]",
        ])
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        warns = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any("unsupported" in m and "name" in m for m in warns), warns
        assert fm.name == "senior-reviewer"

    def test_name_overrides_stem_as_canonical_key(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        text = _build_pack([
            "description: T",
            "name: senior-reviewer",
            "tools: [Read]",
        ])
        with caplog.at_level(logging.INFO, logger=LOGGER):
            key, _, _, _ = parse_pack_text(text, tmp_path / "old-file.md")
        assert key == "senior-reviewer"
        info_msgs = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert any(
            "senior-reviewer" in m and "old-file" in m for m in info_msgs
        ), f"expected transition INFO, got {info_msgs}"

    def test_stem_fallback_when_name_absent(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        text = _build_pack([
            "description: T",
            "tools: [Read]",
        ])
        with caplog.at_level(logging.INFO, logger=LOGGER):
            key, _, fm, _ = parse_pack_text(text, tmp_path / "scout.md")
        assert key == "scout"
        assert fm.name is None
        info_msgs = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        # No transition INFO when names match (none here, since name is absent).
        assert not any(
            "declares name" in m for m in info_msgs
        ), info_msgs

    def test_no_transition_info_when_name_matches_stem(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        text = _build_pack([
            "description: T",
            "name: scout",
            "tools: [Read]",
        ])
        with caplog.at_level(logging.INFO, logger=LOGGER):
            key, _, _, _ = parse_pack_text(text, tmp_path / "scout.md")
        assert key == "scout"
        info_msgs = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert not any("declares name" in m for m in info_msgs), info_msgs


class TestNameValidation:
    """Q-10: name validation surface."""

    def test_name_int_raises(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "name: 42",
            "tools: [Read]",
        ])
        with pytest.raises(PackLoadError, match=r"name.*must be a string.*int"):
            parse_pack_text(text, tmp_path / "agent.md")

    def test_name_bool_raises(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "name: true",
            "tools: [Read]",
        ])
        with pytest.raises(PackLoadError, match=r"name.*must be a string.*bool"):
            parse_pack_text(text, tmp_path / "agent.md")

    def test_name_uppercase_raises(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "name: MyRole",
            "tools: [Read]",
        ])
        with pytest.raises(PackLoadError, match=r"invalid name"):
            parse_pack_text(text, tmp_path / "agent.md")

    def test_name_with_space_raises(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            'name: "my role"',
            "tools: [Read]",
        ])
        with pytest.raises(PackLoadError, match=r"invalid name"):
            parse_pack_text(text, tmp_path / "agent.md")

    def test_name_empty_string_raises(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            'name: ""',
            "tools: [Read]",
        ])
        with pytest.raises(PackLoadError, match=r"invalid name"):
            parse_pack_text(text, tmp_path / "agent.md")

    def test_name_null_falls_back_to_stem(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "name: ",
            "tools: [Read]",
        ])
        key, _, fm, _ = parse_pack_text(text, tmp_path / "scout.md")
        assert key == "scout"
        assert fm.name is None

    def test_name_with_digit_start_accepted(self, tmp_path: Path) -> None:
        """Regex allows digit-leading names like `2fa-helper`."""
        text = _build_pack([
            "description: T",
            "name: 2fa-helper",
            "tools: [Read]",
        ])
        key, _, fm, _ = parse_pack_text(text, tmp_path / "old.md")
        assert fm.name == "2fa-helper"
        assert key == "2fa-helper"


class TestColorField:
    """SC-6: color field silent-accepted."""

    def test_color_silent_accepted(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        text = _build_pack([
            "description: T",
            "color: blue",
            "tools: [Read]",
        ])
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        warns = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert not any("unsupported" in m and "color" in m for m in warns), warns
        assert fm.color == "blue"

    def test_color_absent_is_none(self, tmp_path: Path) -> None:
        text = _build_pack([
            "description: T",
            "tools: [Read]",
        ])
        _, _, fm, _ = parse_pack_text(text, tmp_path / "agent.md")
        assert fm.color is None


# ---------------------------------------------------------------------------
# T4 (Pillar B): subagent prompt composition refactor
#
# Covers SC-7, SC-12 from FEATURE-claude-code-agent-format-compatibility.md.
# Substrate guidance leads on the subagent path. The helper is asymmetric
# with build_teammate_prompt (which keeps body-first ordering for peer-list
# injection — deliberate carve-out).
# ---------------------------------------------------------------------------


class TestBuildSubagentPrompt:
    """SC-7: substrate guidance leads, body follows."""

    def test_helper_returns_guidance_then_body(self) -> None:
        from claude_crew.subagents._loader import (
            SUBSTRATE_SUBAGENT_GUIDANCE,
            build_subagent_prompt,
        )
        result = build_subagent_prompt("You are a probe.")
        assert result.startswith(SUBSTRATE_SUBAGENT_GUIDANCE)
        assert result.endswith("You are a probe.")

    def test_helper_strips_trailing_whitespace_from_body(self) -> None:
        from claude_crew.subagents._loader import build_subagent_prompt
        result = build_subagent_prompt("You are a probe.\n\n   \n")
        assert result.endswith("You are a probe.")

    def test_substrate_guidance_is_model_agnostic(self) -> None:
        """X.2: post-#15 model is optional. Guidance text must NOT mention any
        specific model name (Sonnet/Opus/Haiku) — would be inaccurate for
        inherit-default agents."""
        from claude_crew.subagents._loader import SUBSTRATE_SUBAGENT_GUIDANCE
        guidance_lower = SUBSTRATE_SUBAGENT_GUIDANCE.lower()
        for model_name in ("sonnet", "opus", "haiku", "claude-3", "claude-4"):
            assert model_name not in guidance_lower, (
                f"substrate guidance references model {model_name!r} — must be "
                f"model-agnostic per X.2 since model is optional post-#15"
            )

    def test_pack_with_body_yields_guidance_prefixed_prompt(self, tmp_path: Path) -> None:
        from claude_crew.subagents._loader import SUBSTRATE_SUBAGENT_GUIDANCE
        text = _build_pack(
            ["description: T", "tools: [Read]"],
            body="My role-specific body.",
        )
        _, agent, _, raw_body = parse_pack_text(text, tmp_path / "agent.md")
        assert agent.prompt.startswith(SUBSTRATE_SUBAGENT_GUIDANCE)
        assert agent.prompt.endswith("My role-specific body.")
        # raw_body (4th return) is unmodified — no substrate framing.
        assert SUBSTRATE_SUBAGENT_GUIDANCE not in raw_body
        assert raw_body.strip() == "My role-specific body."

    def test_empty_body_raises_before_composition(self, tmp_path: Path) -> None:
        """SC-7 sentinel M-3: existing empty-body guard fires BEFORE composition.
        Whitespace-only bodies cannot reach build_subagent_prompt."""
        text = _build_pack(["description: T", "tools: [Read]"], body="   \n  \n")
        with pytest.raises(PackLoadError, match="empty body"):
            parse_pack_text(text, tmp_path / "agent.md")
