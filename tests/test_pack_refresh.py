"""Tests for the _PackState holder and pack-refresh feature.

AT-3 lives here: resolver reads holder fields live (no refresh_pack() involved).
AT-1, AT-2, AT-4, AT-6, AT-7, AT-8 are added by task 2 (refresh-method-and-diff).
"""

from __future__ import annotations

from textwrap import dedent

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_agent(agents_dir, stem: str, description: str = "Test agent.", prompt: str = "Body.") -> None:
    """Write a minimal valid agent markdown file to agents_dir/<stem>.md."""
    agents_dir.mkdir(parents=True, exist_ok=True)
    content = dedent(f"""\
        ---
        description: {description}
        tools: [Read]
        ---
        {prompt}
        """)
    (agents_dir / f"{stem}.md").write_text(content)


def _make_sdk_factory(monkeypatch, home_dir, project_root):
    """Build a default_factory in sdk mode pointing at isolated temp dirs.

    Returns the factory (holder is accessible via factory._holder).
    """
    from claude_crew import factories

    monkeypatch.setattr("pathlib.Path.home", lambda: home_dir)
    monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
    return factories.default_factory(home_dir=home_dir, project_root=project_root)


# ---------------------------------------------------------------------------
# AT-3: Holder live-read (introduced by task 1 — preserved here)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AT-1: Holder swap — newly-added project agent visible after refresh
# ---------------------------------------------------------------------------


class TestRefreshAddsNewProjectAgent:
    """AT-1: After refresh, a newly-added project agent is resolvable."""

    def test_new_project_agent_visible_after_refresh(self, monkeypatch, tmp_path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        f = _make_sdk_factory(monkeypatch, home, project)

        # Confirm "foo" is not present before any agent files exist
        assert f.agent_def_resolver("foo") is None

        # Write a new project agent file
        agents_dir = project / ".claude" / "agents"
        _write_agent(agents_dir, "foo", description="Foo agent.")

        result = f.refresh_pack()

        assert result["ok"] is True, f"refresh failed: {result}"
        assert "foo" in result["diff"]["added"], (
            f"expected 'foo' in diff.added; got {result['diff']}"
        )
        resolved = f.agent_def_resolver("foo")
        assert resolved is not None, "factory should resolve 'foo' after refresh"
        assert resolved.description == "Foo agent."


# ---------------------------------------------------------------------------
# AT-2: Diff classification — added, removed, changed
# ---------------------------------------------------------------------------


class TestRefreshDiffClassification:
    """AT-2: refresh() computes correct added/removed/changed diff."""

    def test_diff_added_removed_changed(self, monkeypatch, tmp_path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        agents_dir = project / ".claude" / "agents"

        # Initial state: roles "a" and "c" in project
        _write_agent(agents_dir, "a", description="Role A original.", prompt="A-original")
        _write_agent(agents_dir, "c", description="Role C.")

        f = _make_sdk_factory(monkeypatch, home, project)

        # Confirm initial pack has a and c (plus bundled defaults)
        assert f.agent_def_resolver("a") is not None
        assert f.agent_def_resolver("c") is not None

        # Modify "a" (change prompt body)
        _write_agent(agents_dir, "a", description="Role A edited.", prompt="A-edited")
        # Add "b"
        _write_agent(agents_dir, "b", description="Role B.")
        # Remove "c"
        (agents_dir / "c.md").unlink()

        result = f.refresh_pack()

        assert result["ok"] is True, f"refresh failed: {result}"
        diff = result["diff"]

        assert "b" in diff["added"], f"'b' not in added: {diff}"
        assert "c" in diff["removed"], f"'c' not in removed: {diff}"
        assert "a" in diff["changed"], f"'a' not in changed: {diff}"

        # No false positives: bundled defaults should be neither added nor removed
        # (they were present before and after).
        assert "b" not in diff["removed"]
        assert "a" not in diff["added"]
        assert "c" not in diff["added"]


# ---------------------------------------------------------------------------
# AT-4: Atomic-swap on rebuild failure — prior pack untouched
# ---------------------------------------------------------------------------


class TestRefreshFailurePreservesPriorPack:
    """AT-4: When build_merged_pack raises, ok=False and prior pack survives."""

    def test_rebuild_failure_leaves_prior_pack_intact(self, monkeypatch, tmp_path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        agents_dir = project / ".claude" / "agents"
        _write_agent(agents_dir, "prior-role", description="Prior agent.")

        f = _make_sdk_factory(monkeypatch, home, project)

        # Confirm prior role is present
        assert f.agent_def_resolver("prior-role") is not None

        # Monkeypatch build_merged_pack to raise
        def _raise(*args, **kwargs):
            raise RuntimeError("simulated disk I/O failure")

        monkeypatch.setattr(
            "claude_crew.subagents._user_loader.build_merged_pack",
            _raise,
        )

        result = f.refresh_pack()

        assert result["ok"] is False, "expected ok=False on rebuild failure"
        assert result["error"] is not None and len(result["error"]) > 0, (
            "error field must be populated on failure"
        )

        # Prior pack must remain fully serviceable
        assert f.agent_def_resolver("prior-role") is not None, (
            "prior-role must still resolve after failed refresh"
        )


# ---------------------------------------------------------------------------
# AT-6: Refresh uses captured roots, not cwd
# ---------------------------------------------------------------------------


class TestRefreshUsesCapturedRoots:
    """AT-6: refresh() uses home_dir/project_root frozen at startup, not cwd."""

    def test_refresh_uses_project_root_not_cwd(self, monkeypatch, tmp_path) -> None:
        home = tmp_path / "home"
        home.mkdir()

        # Two project dirs: A (startup root), B (some other dir)
        project_a = tmp_path / "A"
        project_a.mkdir()
        project_b = tmp_path / "B"
        project_b.mkdir()

        # Plant agent "alpha" only in A
        _write_agent(project_a / ".claude" / "agents", "alpha", description="Alpha in A.")
        # Plant agent "beta" only in B
        _write_agent(project_b / ".claude" / "agents", "beta", description="Beta in B.")

        # Build factory with project_root=A
        f = _make_sdk_factory(monkeypatch, home, project_a)

        # Patch cwd to B — refresh must NOT read from B
        monkeypatch.chdir(project_b)

        result = f.refresh_pack()

        assert result["ok"] is True, f"refresh failed: {result}"

        # "alpha" should be resolvable (it's in A)
        assert f.agent_def_resolver("alpha") is not None, (
            "alpha (from project_root=A) must be in pack after refresh"
        )
        # "beta" must NOT be in the pack (it's in B / cwd — must be ignored)
        assert f.agent_def_resolver("beta") is None, (
            "beta (from cwd=B) must NOT appear; refresh must use captured project_root=A"
        )


# ---------------------------------------------------------------------------
# AT-7: Full four-layer re-merge — user-level agent picked up
# ---------------------------------------------------------------------------


class TestRefreshPicksUpUserLayerChanges:
    """AT-7: refresh() re-reads the user layer (home_dir/.claude/agents/)."""

    def test_user_layer_agent_added_after_refresh(self, monkeypatch, tmp_path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        f = _make_sdk_factory(monkeypatch, home, project)

        # Confirm "user-role" is absent initially
        assert f.agent_def_resolver("user-role") is None

        # Add an agent to the user layer
        _write_agent(home / ".claude" / "agents", "user-role", description="User-level agent.")

        result = f.refresh_pack()

        assert result["ok"] is True, f"refresh failed: {result}"
        assert "user-role" in result["diff"]["added"], (
            f"user-role not in diff.added after adding user-level file: {result['diff']}"
        )
        assert f.agent_def_resolver("user-role") is not None, (
            "user-role must be resolvable after refresh picked up user layer"
        )


# ---------------------------------------------------------------------------
# AT-8: Sad-path — malformed file isolated, warnings captured
# ---------------------------------------------------------------------------


class TestRefreshMalformedFileIsolated:
    """AT-8: A malformed project agent file is isolated; ok=True; warning references bad.md."""

    def test_malformed_file_produces_warning_and_ok(self, monkeypatch, tmp_path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        agents_dir = project / ".claude" / "agents"
        agents_dir.mkdir(parents=True)

        # Write a valid agent
        _write_agent(agents_dir, "good-role", description="Good agent.")

        # Write a malformed agent (invalid YAML syntax that yaml.safe_load cannot parse)
        (agents_dir / "bad.md").write_text(dedent("""\
            ---
            description: Valid description
            tools: [Read
            ---
            Bad agent body.
            """))

        f = _make_sdk_factory(monkeypatch, home, project)

        result = f.refresh_pack()

        assert result["ok"] is True, (
            f"refresh must succeed (isolating bad.md) but got ok=False: {result}"
        )

        # Warnings must mention bad.md
        warning_messages = [w["message"] for w in result["warnings"]]
        assert any("bad.md" in msg for msg in warning_messages), (
            f"expected a warning referencing 'bad.md'; got warnings: {warning_messages}"
        )

        # Prior valid roles still spawnable
        assert f.agent_def_resolver("good-role") is not None, (
            "good-role must still be resolvable after refresh that isolated bad.md"
        )
