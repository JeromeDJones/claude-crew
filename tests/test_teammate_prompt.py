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
    """SC-2 — all sentinels appear in the documented order.

    SENTINEL_SUBAGENTS was removed 2026-05-17 (its content duplicated the
    framework-injected Agent tool description). The addendum now contains
    Operating context → Delegation.
    """

    def test_assembled_prompt_contains_all_sentinels(
        self, default_pack_agents, default_pack_bodies
    ) -> None:
        body = default_pack_bodies["planner"]
        assembled = build_teammate_prompt("planner", body, default_pack_agents)
        for sentinel in (SENTINEL_CONTEXT, SENTINEL_DELEGATION):
            assert sentinel in assembled, f"Sentinel {sentinel!r} missing from assembled prompt"

    def test_subagent_list_section_is_gone(
        self, default_pack_agents, default_pack_bodies
    ) -> None:
        """The ## Available subagents block is intentionally absent — its
        content duplicated the framework-injected Agent tool description
        (verified by the planner context audit, 2026-05-17)."""
        body = default_pack_bodies["planner"]
        assembled = build_teammate_prompt("planner", body, default_pack_agents)
        assert "## Available subagents" not in assembled, (
            "Subagent list section must NOT appear in assembled prompt — "
            "it duplicated Agent tool description content"
        )

    def test_sentinels_appear_in_documented_order(
        self, default_pack_agents, default_pack_bodies
    ) -> None:
        body = default_pack_bodies["planner"]
        assembled = build_teammate_prompt("planner", body, default_pack_agents)
        ctx_idx = assembled.index(SENTINEL_CONTEXT)
        deleg_idx = assembled.index(SENTINEL_DELEGATION)
        assert ctx_idx < deleg_idx, (
            f"Sentinel order wrong: CONTEXT={ctx_idx}, DELEGATION={deleg_idx}"
        )


# ---------------------------------------------------------------------------
# Removed 2026-05-17: TestSubagentList class. _build_subagent_list was deleted
# along with the ## Available subagents section in the addendum. The framework-
# injected Agent tool description already carries names + per-agent descriptions
# + tool surfaces; repeating any of that in the spawn-time prompt was ~700 tokens
# of pure duplication per LLM invocation. Section absence is guarded by
# TestSentinelOrdering.test_subagent_list_section_is_gone above.
# ---------------------------------------------------------------------------


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
# 2026-05-08  Tool-surface-error recovery hint
# ---------------------------------------------------------------------------


class TestToolSurfaceErrorRecoveryHint:
    """The delegation section advises the parent how to recover when a
    dispatched subagent reports it can't complete the work because of a
    missing tool. Replaces the upfront tools sub-bullet (removed 2026-05-08
    to save context)."""

    def test_recovery_hint_lives_inside_delegation_section(
        self, default_pack_agents
    ) -> None:
        from claude_crew.teammate_prompt import (
            SENTINEL_DELEGATION,
            SENTINEL_MEMORY,
        )
        result = build_teammate_prompt(
            role="some-role",
            pack_body="role body",
            agents=default_pack_agents,
        )
        # Locate the delegation section's text region.
        deleg_start = result.index(SENTINEL_DELEGATION)
        # Memory section may or may not be present; if absent, the
        # delegation region runs to end-of-prompt.
        deleg_end = (
            result.index(SENTINEL_MEMORY, deleg_start)
            if SENTINEL_MEMORY in result[deleg_start:]
            else len(result)
        )
        delegation_section = result[deleg_start:deleg_end]
        # The recovery instruction must appear inside the delegation region,
        # not anywhere else in the assembled prompt.
        assert "lacks a tool" in delegation_section, (
            "Recovery hint missing the failure-mode signal "
            f"(expected 'lacks a tool'). Section:\n{delegation_section}"
        )
        assert (
            "switch to a different subagent" in delegation_section
            or "handle that step directly" in delegation_section
        ), (
            "Recovery hint must name the action the parent should take. "
            f"Section:\n{delegation_section}"
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

        # All sentinels appear (SUBAGENTS removed 2026-05-17 — duplicated Agent tool docs)
        for sentinel in (SENTINEL_CONTEXT, SENTINEL_DELEGATION):
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
