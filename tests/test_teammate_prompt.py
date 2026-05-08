"""Tests for Feature #21 — Teammate Prompt Parity.

Covers SC-1 through SC-8 deterministically.

SC-1  TestSdkTeammateIntegration  (end-to-end via spawn path)
SC-2  TestSentinelOrdering        (four sentinels present & ordered)
SC-3  (loader / pack-file edits — covered in test_subagents.py)
SC-4  TestLeafSuffixOnSubagentPath
SC-5  (delegation enablement — covered by TestExplorerHint + TestPeerList)
SC-6  TestStaticContradictionLint
SC-7  (user-pack responsibility — documented in feature spec; no test)
SC-8  (full suite regression — covered by test_subagents.py continuing green)
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from claude_crew.subagents import load_default_pack
from claude_crew.subagents._loader import SUBSTRATE_SUBAGENT_GUIDANCE
from claude_crew.teammate_prompt import (
    NEGATIVE_PATTERNS,
    SENTINEL_CONTEXT,
    SENTINEL_DELEGATION,
    SENTINEL_SUBAGENTS,
    _build_subagent_list,
    _explorer_hint,
    build_teammate_prompt,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def default_pack_data():
    """Load the bundled pack once for the entire module."""
    pack, _role_ss, bodies = load_default_pack()
    return pack, bodies


@pytest.fixture(scope="module")
def default_pack_agents(default_pack_data):
    pack, _bodies = default_pack_data
    return pack


@pytest.fixture(scope="module")
def default_pack_bodies(default_pack_data):
    _pack, bodies = default_pack_data
    return bodies


# ---------------------------------------------------------------------------
# SC-6  Static contradiction-lint
# ---------------------------------------------------------------------------


class TestStaticContradictionLint:
    """SC-6 — assembled teammate prompts must not contain negative patterns."""

    ROLES = ["explorer", "planner", "general"]

    def test_assembled_teammate_prompt_for_each_bundled_role_has_no_negative_patterns(
        self,
        default_pack_agents,
        default_pack_bodies,
    ) -> None:
        for role in self.ROLES:
            body = default_pack_bodies[role]
            assembled = build_teammate_prompt(role, body, default_pack_agents)
            for pattern in NEGATIVE_PATTERNS:
                idx = assembled.find(pattern)
                assert idx == -1, (
                    f"role={role!r}: negative pattern {pattern!r} found at "
                    f"position {idx} in assembled prompt.\n"
                    f"Surrounding context: {assembled[max(0, idx-40):idx+60]!r}"
                )

    def test_pack_bodies_themselves_have_no_negative_patterns(
        self,
        default_pack_bodies,
    ) -> None:
        for role in self.ROLES:
            body = default_pack_bodies[role]
            for pattern in NEGATIVE_PATTERNS:
                idx = body.find(pattern)
                assert idx == -1, (
                    f"role={role!r}: pack body contains negative pattern {pattern!r} "
                    f"at position {idx}. The leaf-language must live in SUBSTRATE_SUBAGENT_GUIDANCE "
                    f"only.\nSurrounding context: {body[max(0, idx-40):idx+60]!r}"
                )


# ---------------------------------------------------------------------------
# SC-2  Sentinel ordering
# ---------------------------------------------------------------------------


class TestSentinelOrdering:
    """SC-2 — all sentinels appear in the documented order."""

    def test_assembled_prompt_contains_all_sentinels(
        self, default_pack_agents, default_pack_bodies
    ) -> None:
        body = default_pack_bodies["planner"]
        assembled = build_teammate_prompt("planner", body, default_pack_agents)
        for sentinel in (
            SENTINEL_CONTEXT,
            SENTINEL_SUBAGENTS,
            SENTINEL_DELEGATION,
        ):
            assert sentinel in assembled, f"Sentinel {sentinel!r} missing from assembled prompt"

    def test_sentinels_appear_in_documented_order(
        self, default_pack_agents, default_pack_bodies
    ) -> None:
        body = default_pack_bodies["planner"]
        assembled = build_teammate_prompt("planner", body, default_pack_agents)
        ctx_idx = assembled.index(SENTINEL_CONTEXT)
        sub_idx = assembled.index(SENTINEL_SUBAGENTS)
        deleg_idx = assembled.index(SENTINEL_DELEGATION)
        assert ctx_idx < sub_idx < deleg_idx, (
            f"Sentinel order wrong: CONTEXT={ctx_idx}, SUBAGENTS={sub_idx}, "
            f"DELEGATION={deleg_idx}"
        )


# ---------------------------------------------------------------------------
# D-3 / R-1 / R-2  Peer list correctness
# ---------------------------------------------------------------------------


class TestSubagentList:
    """Tests for _build_subagent_list: self-exclusion, sort order, description handling."""

    def test_subagent_list_excludes_self(self, default_pack_agents) -> None:
        result = _build_subagent_list("planner", default_pack_agents)
        # Should not appear as a peer list entry
        assert "- **planner**" not in result, (
            "planner must not list itself as a peer"
        )

    def test_subagent_list_sorted_by_name(self, default_pack_agents) -> None:
        # Use a role not in the pack so all agents appear as peers.
        result = _build_subagent_list("nonexistent-role", default_pack_agents)
        # Extract names from lines matching "- **<name>**"
        names = [
            line.split("**")[1]
            for line in result.splitlines()
            if line.startswith("- **")
        ]
        assert names == sorted(names), (
            f"Peer list is not sorted alphabetically: {names}"
        )

    def test_subagent_list_includes_description_when_present(
        self, default_pack_agents
    ) -> None:
        # explorer has a known description; exclude it from self so it appears
        result = _build_subagent_list("nonexistent-role", default_pack_agents)
        explorer_defn = default_pack_agents["explorer"]
        expected_line = f"- **explorer** — {explorer_defn.description.strip()}"
        assert expected_line in result, (
            f"Expected description line {expected_line!r} not found in peer list:\n{result}"
        )

    def test_subagent_list_falls_back_to_name_only_when_description_missing(self) -> None:
        ns = types.SimpleNamespace(description=None)
        fake_agents: dict[str, Any] = {"alpha": ns}
        result = _build_subagent_list("nonexistent-role", fake_agents)
        assert "- **alpha**" in result
        assert "—" not in result, "Name-only entry must not contain em-dash"

    def test_subagent_list_falls_back_to_name_only_when_description_is_non_string(
        self,
    ) -> None:
        ns = types.SimpleNamespace(description=123)
        fake_agents: dict[str, Any] = {"beta": ns}
        result = _build_subagent_list("nonexistent-role", fake_agents)
        assert "- **beta**" in result
        assert "—" not in result, "Non-string description must be treated as missing"

    def test_subagent_list_includes_tools_sub_bullet(self, default_pack_agents) -> None:
        """Each peer entry lists its tool surface as an indented sub-bullet.

        Surfacing tools to the parent prevents mis-routing tasks to subagents
        that lack the required tool (BACKLOG 2026-05-01: parent dispatched a
        Bash task to general, which has no Bash, and the subagent
        fabricated). The sub-bullet alone doesn't enforce routing — that's a
        separate hook-based fix — but it gives the parent the data to route
        correctly and the operator a way to see what each peer can do.
        """
        result = _build_subagent_list("nonexistent-role", default_pack_agents)
        gp_tools = ", ".join(default_pack_agents["general"].tools)
        assert f"  - tools: {gp_tools}" in result, (
            f"general peer entry missing tools sub-bullet. Got:\n{result}"
        )
        explorer_tools = ", ".join(default_pack_agents["explorer"].tools)
        assert f"  - tools: {explorer_tools}" in result, (
            f"explorer peer entry missing tools sub-bullet. Got:\n{result}"
        )

    def test_subagent_list_omits_tools_sub_bullet_when_tools_missing(self) -> None:
        """Malformed user packs without a tools attribute fall back to
        name-and-description-only, no sub-bullet, no crash."""
        ns = types.SimpleNamespace(description="thing", tools=None)
        fake_agents: dict[str, Any] = {"gamma": ns}
        result = _build_subagent_list("nonexistent-role", fake_agents)
        assert "- **gamma** — thing" in result
        assert "tools:" not in result, (
            f"tools sub-bullet rendered despite tools=None: {result!r}"
        )


# ---------------------------------------------------------------------------
# EC-11 / R-7  Explorer hint
# ---------------------------------------------------------------------------


class TestExplorerHint:
    """Tests for _explorer_hint: dynamic explorer mention when present / absent."""

    def test_delegation_section_names_explorer_when_present(
        self, default_pack_agents
    ) -> None:
        hint = _explorer_hint(default_pack_agents)
        assert "explorer" in hint.lower(), (
            "explorer hint must mention 'explorer' when explorer is in agents dict"
        )
        # Also assert description-style mention — the hint should be more than just
        # the word "explorer" in passing; it should contextualise the role.
        assert len(hint) > len("explorer"), "Hint must provide descriptive context, not just the name"

    def test_delegation_section_omits_explorer_when_absent(self) -> None:
        agents_without_explorer: dict[str, Any] = {
            "planner": types.SimpleNamespace(description="Spec writer"),
        }
        hint = _explorer_hint(agents_without_explorer)
        assert "explorer" not in hint.lower(), (
            "explorer must not appear in hint when explorer is absent from agents dict"
        )


# ---------------------------------------------------------------------------
# SC-4  Leaf suffix preserved on subagent path
# ---------------------------------------------------------------------------


class TestSubstrateGuidanceOnSubagentPath:
    """SC-4 / #15 SC-7 — the loader's subagent path leads with SUBSTRATE_SUBAGENT_GUIDANCE."""

    def test_assembled_subagent_prompt_starts_with_substrate_guidance(
        self, default_pack_agents
    ) -> None:
        # SUBSTRATE_SUBAGENT_GUIDANCE is prepended by the loader; the
        # AgentDefinition.prompt must start with it for all bundled roles.
        for role, agent in default_pack_agents.items():
            assert agent.prompt.startswith(SUBSTRATE_SUBAGENT_GUIDANCE), (
                f"AgentDefinition for role {role!r} must start with SUBSTRATE_SUBAGENT_GUIDANCE. "
                f"First 200 chars: {agent.prompt[:200]!r}"
            )


# ---------------------------------------------------------------------------
# SC-1  SdkTeammate integration — system prompt assembled at spawn time
# ---------------------------------------------------------------------------


class TestSdkTeammateIntegration:
    """SC-1 / D-7 / edge-case 2 — end-to-end via SdkTeammate.__init__."""

    def _make_teammate(self, role: str, **kwargs) -> Any:
        """Construct a SdkTeammate without a broker or SDK — init only."""
        from claude_crew.sdk_teammate import SdkTeammate
        return SdkTeammate(id="test-id", name="test-name", role=role, **kwargs)

    def test_spawned_general_purpose_teammate_has_assembled_prompt(
        self,
        default_pack_bodies,
    ) -> None:
        teammate = self._make_teammate("general")
        prompt = teammate._system_prompt

        # Pack body identity line appears
        gp_body = default_pack_bodies["general"]
        # The first meaningful content line from the body should be in the prompt
        first_content = next(
            line for line in gp_body.splitlines() if line.strip()
        )
        assert first_content in prompt, (
            f"Pack body content {first_content!r} not found in system prompt"
        )

        # All sentinels appear
        for sentinel in (
            SENTINEL_CONTEXT,
            SENTINEL_SUBAGENTS,
            SENTINEL_DELEGATION,
        ):
            assert sentinel in prompt, (
                f"Sentinel {sentinel!r} missing from general teammate prompt"
            )

        # No negative patterns
        for pattern in NEGATIVE_PATTERNS:
            assert pattern not in prompt, (
                f"Negative pattern {pattern!r} found in general teammate prompt"
            )

    def test_spawned_teammate_with_unknown_role_uses_default_fallback(self) -> None:
        teammate = self._make_teammate("quux-the-undefined")
        expected = "You are a quux-the-undefined. Help the lead with quux-the-undefined-level work."
        assert teammate._system_prompt == expected, (
            f"Unknown role should fall back to _default_system_prompt. "
            f"Got: {teammate._system_prompt!r}"
        )

    def test_spawned_teammate_with_explicit_system_prompt_uses_it(self) -> None:
        custom = "Custom override prompt"
        teammate = self._make_teammate("general-purpose", system_prompt=custom)
        assert teammate._system_prompt == custom, (
            f"Explicit system_prompt must win over assembled prompt. "
            f"Got: {teammate._system_prompt!r}"
        )
