"""Tests for the _PackState holder and pack-refresh feature.

AT-3 lives here: resolver reads holder fields live (no refresh_pack() involved).
Subsequent tasks (refresh-method-and-diff, mcp-refresh-agents-tool) will add
AT-1, AT-2, AT-4–AT-11 in this file.
"""

from __future__ import annotations

import pytest


class TestPackStateHolderLiveRead:
    """AT-3: _resolve_agent_def reads holder.pack live at call time.

    Validates that the refactor replaced captured closure-locals with live
    reads off the _PackState holder.  Direct mutation of holder.pack proves
    the indirection works without involving refresh_pack() (task 2).
    """

    @pytest.fixture()
    def sdk_factory_with_holder(self, monkeypatch, tmp_path):
        """Build a default_factory in sdk mode with an empty merged pack.

        Returns (factory, holder) so the test can reach the holder directly.
        """
        from claude_crew import factories

        (tmp_path / "home").mkdir()
        (tmp_path / "cwd").mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        monkeypatch.chdir(tmp_path / "cwd")
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        # Empty pack — no agents planted
        def mock_build_merged_pack():
            return {}, {}, {}

        monkeypatch.setattr(
            "claude_crew.subagents._user_loader.build_merged_pack",
            mock_build_merged_pack,
        )

        f = factories.default_factory()
        holder = f._holder  # type: ignore[attr-defined]
        return f, holder

    def test_resolver_returns_none_before_mutation(self, sdk_factory_with_holder) -> None:
        """agent_def_resolver("role-x") is None when the pack is empty."""
        f, holder = sdk_factory_with_holder
        assert f.agent_def_resolver("role-x") is None

    def test_resolver_returns_agent_def_after_direct_holder_mutation(
        self, sdk_factory_with_holder,
    ) -> None:
        """After holder.pack["role-x"] = <def>, resolver returns it live."""
        from claude_agent_sdk.types import AgentDefinition

        f, holder = sdk_factory_with_holder

        sentinel = AgentDefinition(
            description="live-read sentinel",
            prompt="prove live read",
            tools=["Read"],
        )
        # Direct mutation — no refresh_pack() involved (that's task 2)
        holder.pack["role-x"] = sentinel

        result = f.agent_def_resolver("role-x")
        assert result is not None, (
            "agent_def_resolver must read holder.pack live; returned None after mutation"
        )
        assert result.description == "live-read sentinel", (
            f"expected sentinel AgentDefinition; got {result!r}"
        )

    def test_resolver_reflects_subsequent_removal(self, sdk_factory_with_holder) -> None:
        """Removing a key from holder.pack makes the resolver return None again."""
        from claude_agent_sdk.types import AgentDefinition

        f, holder = sdk_factory_with_holder

        holder.pack["role-y"] = AgentDefinition(
            description="temp", prompt="temp", tools=[],
        )
        assert f.agent_def_resolver("role-y") is not None

        del holder.pack["role-y"]
        assert f.agent_def_resolver("role-y") is None, (
            "resolver must reflect deletion from holder.pack"
        )
