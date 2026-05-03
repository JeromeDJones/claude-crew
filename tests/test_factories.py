"""Tests for factory selection and the requires_auth marker."""

from __future__ import annotations

import pytest

from claude_crew import factories
from claude_crew.teammate import StubTeammate


class TestFactoryMarkers:
    def test_stub_factory_does_not_require_auth(self) -> None:
        assert factories.stub_factory.requires_auth is False

    def test_sdk_factory_requires_auth(self) -> None:
        assert factories.sdk_factory.requires_auth is True

    def test_stub_factory_returns_stub_teammate(self) -> None:
        t = factories.stub_factory("t-1", "alice", "planner")
        assert isinstance(t, StubTeammate)
        assert t.id == "t-1"
        assert t.name == "alice"
        assert t.role == "planner"


class TestDefaultFactory:
    """SDK-mode returns a closure that injects the #3b merged pack; tests
    point home/cwd at a tempdir so they don't depend on the developer's
    real ``~/.claude/agents/``."""

    @pytest.fixture(autouse=True)
    def _hermetic_dirs(self, monkeypatch, tmp_path):
        # No agent files planted; loaders return empty dicts → merged pack
        # equals the bundled default pack. Both home and cwd are pointed
        # at fresh tempdirs so Jerome's real ``~/.claude/agents/`` (which
        # has 5 agents) does not leak into these tests.
        (tmp_path / "home").mkdir()
        (tmp_path / "cwd").mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        monkeypatch.chdir(tmp_path / "cwd")

    def test_default_when_unset_is_sdk_mode(self, monkeypatch) -> None:
        monkeypatch.delenv("CLAUDE_CREW_TEAMMATE_MODE", raising=False)
        f = factories.default_factory()
        assert getattr(f, "requires_auth", False) is True

    def test_explicit_sdk_mode(self, monkeypatch) -> None:
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
        f = factories.default_factory()
        assert getattr(f, "requires_auth", False) is True

    def test_explicit_stub_mode(self, monkeypatch) -> None:
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "stub")
        assert factories.default_factory() is factories.stub_factory

    def test_unknown_value_falls_back_to_stub(self, monkeypatch) -> None:
        # Conservative default: anything that isn't "sdk" goes to stub so a
        # typo doesn't accidentally invoke the real model.
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "garbage")
        assert factories.default_factory() is factories.stub_factory


class TestSdkFactoryAgentInjection:
    """SC-3, SC-7 — the merged pack reaches SdkTeammate via the existing
    ``agents`` kwarg, with no new constructor surface and the right
    precedence."""

    def test_default_factory_in_sdk_mode_passes_merged_pack_to_sdk_teammate(
        self, monkeypatch, tmp_path,
    ) -> None:
        """Plant a user-level agent, build the closure factory, spawn one
        teammate via that closure, and assert the SdkTeammate's ``agents``
        dict contains both the bundled pack and the planted one."""
        from textwrap import dedent

        from claude_crew.sdk_teammate import SdkTeammate

        home = tmp_path / "home"
        agents_dir = home / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "scout.md").write_text(dedent("""\
            ---
            description: Test scout.
            model: haiku
            tools: [Read]
            ---

            You are a scout.
            """))
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        f = factories.default_factory()
        teammate = f("t-1", "alice", "planner")

        assert isinstance(teammate, SdkTeammate)
        # The agents kwarg reaches SdkTeammate; bundled pack + planted user
        # agent both present.
        assert "scout" in teammate._agents  # planted
        assert "explorer" in teammate._agents  # bundled
        assert "planner" in teammate._agents  # bundled
        assert "general-purpose" in teammate._agents  # bundled


class TestSpawnChainParams:
    """Feature #10-T3: cwd and permission_mode thread through the spawn chain
    (server → broker → factory → SdkTeammate)."""

    def test_stub_factory_accepts_cwd_and_permission_mode(self) -> None:
        """stub_factory signature accepts cwd/permission_mode without TypeError."""
        t = factories.stub_factory(
            "t-1", "n", "r",
            cwd="/tmp", permission_mode="plan",
        )
        assert isinstance(t, StubTeammate)
        assert t.id == "t-1"

    def test_sdk_factory_forwards_cwd_to_sdk_teammate(self, monkeypatch) -> None:
        """sdk_factory forwards cwd to SdkTeammate constructor."""
        from claude_crew.sdk_teammate import SdkTeammate

        captured_kwargs = {}

        def mock_sdk_init(self, id, name, role, **kwargs):
            captured_kwargs.update(kwargs)
            # Call parent __init__ equivalent to avoid full init
            self.id = id
            self.name = name
            self.role = role
            self._model = kwargs.get("model", "claude-sonnet-4-6")
            self._effort = kwargs.get("effort")
            self._cwd = kwargs.get("cwd")
            self._permission_mode = kwargs.get("permission_mode")
            self._agents = {}

        monkeypatch.setattr(SdkTeammate, "__init__", mock_sdk_init)

        t = factories.sdk_factory("t-1", "n", "r", cwd="/tmp/proj")
        assert isinstance(t, SdkTeammate)
        assert captured_kwargs.get("cwd") == "/tmp/proj"

    def test_sdk_factory_forwards_permission_mode_to_sdk_teammate(self, monkeypatch) -> None:
        """sdk_factory forwards permission_mode to SdkTeammate constructor."""
        from claude_crew.sdk_teammate import SdkTeammate

        captured_kwargs = {}

        def mock_sdk_init(self, id, name, role, **kwargs):
            captured_kwargs.update(kwargs)
            self.id = id
            self.name = name
            self.role = role
            self._model = kwargs.get("model", "claude-sonnet-4-6")
            self._effort = kwargs.get("effort")
            self._cwd = kwargs.get("cwd")
            self._permission_mode = kwargs.get("permission_mode")
            self._agents = {}

        monkeypatch.setattr(SdkTeammate, "__init__", mock_sdk_init)

        t = factories.sdk_factory("t-1", "n", "r", permission_mode="plan")
        assert isinstance(t, SdkTeammate)
        assert captured_kwargs.get("permission_mode") == "plan"

    def test_default_factory_closure_forwards_cwd_and_permission_mode(
        self, monkeypatch, tmp_path,
    ) -> None:
        """The inner factory closure in default_factory() forwards cwd and
        permission_mode to sdk_factory.

        We verify this by patching sdk_factory at the module level before
        calling default_factory(), which captures the reference at that point."""
        import inspect

        (tmp_path / "home").mkdir()
        (tmp_path / "cwd").mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        monkeypatch.chdir(tmp_path / "cwd")

        # Patch build_merged_pack to avoid loading real agents
        def mock_build_merged_pack():
            return {}, {}, {}

        monkeypatch.setattr(
            "claude_crew.subagents._user_loader.build_merged_pack",
            mock_build_merged_pack,
        )

        # Patch sdk_factory in the module BEFORE calling default_factory
        # so the closure captures our mock
        captured_call_kwargs = {}

        def capturing_sdk_factory(id, name, role, **kwargs):
            captured_call_kwargs.update(kwargs)
            # Return a StubTeammate to avoid needing full SdkTeammate init
            return StubTeammate(id, name, role)

        # Replace in the module namespace before default_factory() is called
        monkeypatch.setattr("claude_crew.factories.sdk_factory", capturing_sdk_factory)

        # Now import and call default_factory with our patched sdk_factory
        # We need to reload to get the patched version
        import importlib
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
        # Create a new default_factory with the patched function
        from claude_crew.factories import default_factory
        f = default_factory()

        # Call the factory closure with cwd and permission_mode
        result = f("t-1", "n", "r", cwd="/tmp/proj", permission_mode="bypassPermissions")

        # Verify the closure passed the parameters through
        assert captured_call_kwargs.get("cwd") == "/tmp/proj"
        assert captured_call_kwargs.get("permission_mode") == "bypassPermissions"
        assert isinstance(result, StubTeammate)


class TestSettingSources:
    """T3 BDD: setting_sources threads through the factory chain."""

    def test_stub_factory_accepts_setting_sources_without_error(self) -> None:
        """stub_factory accepts setting_sources param and returns StubTeammate."""
        t = factories.stub_factory(
            "t-1", "alice", "planner",
            setting_sources=[],
        )
        assert isinstance(t, StubTeammate)
        assert t.id == "t-1"

    def test_sdk_factory_passes_setting_sources_to_sdk_teammate(self, monkeypatch) -> None:
        """sdk_factory forwards setting_sources=[] to SdkTeammate constructor."""
        from claude_crew.sdk_teammate import SdkTeammate

        captured_kwargs: dict = {}

        def mock_sdk_init(self, id, name, role, **kwargs):
            captured_kwargs.update(kwargs)
            self.id = id
            self.name = name
            self.role = role
            self._model = kwargs.get("model", "claude-sonnet-4-6")
            self._effort = kwargs.get("effort")
            self._cwd = kwargs.get("cwd")
            self._permission_mode = kwargs.get("permission_mode")
            self._agents = {}

        monkeypatch.setattr(SdkTeammate, "__init__", mock_sdk_init)

        factories.sdk_factory("t-1", "alice", "explorer", setting_sources=[])
        assert captured_kwargs.get("setting_sources") == []

    def test_sdk_factory_omits_setting_sources_kwarg_when_none(self, monkeypatch) -> None:
        """sdk_factory with setting_sources=None must NOT pass setting_sources to SdkTeammate.

        This guards the None-vs-[] invariant: None means 'use SDK default' (kwarg absent),
        [] means 'no sources' (kwarg present as empty list). A truthiness check would
        incorrectly treat [] as falsy and skip it.
        """
        from claude_crew.sdk_teammate import SdkTeammate

        captured_kwargs: dict = {}

        def mock_sdk_init(self, id, name, role, **kwargs):
            captured_kwargs.update(kwargs)
            self.id = id
            self.name = name
            self.role = role
            self._model = kwargs.get("model", "claude-sonnet-4-6")
            self._effort = kwargs.get("effort")
            self._cwd = kwargs.get("cwd")
            self._permission_mode = kwargs.get("permission_mode")
            self._agents = {}

        monkeypatch.setattr(SdkTeammate, "__init__", mock_sdk_init)

        factories.sdk_factory("t-1", "alice", "explorer", setting_sources=None)
        assert "setting_sources" not in captured_kwargs

    def test_default_factory_closure_passes_role_ss_to_sdk_factory(
        self, monkeypatch, tmp_path,
    ) -> None:
        """Inner factory closure looks up role_ss[role] and passes it to sdk_factory.

        role_ss={"explorer": []} → spawning "explorer" → sdk_factory gets setting_sources=[].
        """
        (tmp_path / "home").mkdir()
        (tmp_path / "cwd").mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        monkeypatch.chdir(tmp_path / "cwd")

        mock_agent_def = object()  # stand-in; not inspected

        def mock_build_merged_pack():
            return {"explorer": mock_agent_def}, {"explorer": []}, {}

        monkeypatch.setattr(
            "claude_crew.subagents._user_loader.build_merged_pack",
            mock_build_merged_pack,
        )

        captured_call_kwargs: dict = {}

        def capturing_sdk_factory(id, name, role, **kwargs):
            captured_call_kwargs.update(kwargs)
            return StubTeammate(id, name, role)

        monkeypatch.setattr("claude_crew.factories.sdk_factory", capturing_sdk_factory)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        from claude_crew.factories import default_factory
        f = default_factory()
        f("t-1", "alice", "explorer")

        assert captured_call_kwargs.get("setting_sources") == []

    def test_default_factory_closure_passes_none_for_unknown_role(
        self, monkeypatch, tmp_path,
    ) -> None:
        """Role not in role_ss → sdk_factory receives setting_sources=None (SDK default)."""
        (tmp_path / "home").mkdir()
        (tmp_path / "cwd").mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        monkeypatch.chdir(tmp_path / "cwd")

        mock_agent_def = object()

        def mock_build_merged_pack():
            return {"explorer": mock_agent_def}, {"explorer": []}, {}

        monkeypatch.setattr(
            "claude_crew.subagents._user_loader.build_merged_pack",
            mock_build_merged_pack,
        )

        captured_call_kwargs: dict = {}

        def capturing_sdk_factory(id, name, role, **kwargs):
            captured_call_kwargs.update(kwargs)
            return StubTeammate(id, name, role)

        monkeypatch.setattr("claude_crew.factories.sdk_factory", capturing_sdk_factory)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        from claude_crew.factories import default_factory
        f = default_factory()
        f("t-1", "bob", "planner")

        # "planner" not in role_ss → role_ss.get("planner") is None
        assert captured_call_kwargs.get("setting_sources") is None


class TestMakeServerAuthGate:
    """make_server() must call validate_auth_or_exit only when the selected
    factory has requires_auth=True. Stub mode skips the check."""

    def test_sdk_factory_without_auth_exits(
        self, monkeypatch, tmp_path,
    ) -> None:
        from claude_crew.server import make_server

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            make_server(factory=factories.sdk_factory)
        assert exc_info.value.code == 2

    def test_stub_factory_without_auth_succeeds(
        self, monkeypatch, tmp_path,
    ) -> None:
        from claude_crew.server import make_server

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        # Should not raise.
        server = make_server(factory=factories.stub_factory)
        assert server is not None

    def test_factory_with_requires_auth_attribute_is_gated(
        self, monkeypatch, tmp_path,
    ) -> None:
        """A custom factory with requires_auth=True is also gated, proving
        we use the attribute, not factory identity."""
        from claude_crew.server import make_server
        from claude_crew.teammate import StubTeammate

        def custom_factory(id, name, role, **_kwargs):
            return StubTeammate(id, name, role)
        custom_factory.requires_auth = True

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        with pytest.raises(SystemExit):
            make_server(factory=custom_factory)


class TestDefaultFactoryAgentDefResolver:
    """default_factory must expose the merged pack to the broker via
    `factory.agent_def_resolver`. Without this the broker has no AgentDefinition
    to snapshot at spawn time and the per-teammate `config` block is empty —
    breaking dashboard chips and the click-to-open detail panel.
    """

    def test_default_factory_attaches_agent_def_resolver(
        self, monkeypatch, tmp_path,
    ) -> None:
        from textwrap import dedent

        home = tmp_path / "home"
        agents_dir = home / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "scout.md").write_text(dedent("""\
            ---
            description: Test scout.
            model: haiku
            tools: [Read, Grep]
            ---

            You are a scout.
            """))
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        f = factories.default_factory()

        assert hasattr(f, "agent_def_resolver"), (
            "default_factory must attach agent_def_resolver so the broker "
            "can snapshot config at spawn time"
        )
        resolver = f.agent_def_resolver

        scout_def = resolver("scout")
        assert scout_def is not None
        assert getattr(scout_def, "tools", None) == ["Read", "Grep"]
        assert resolver("definitely-not-a-real-role") is None


# ---------- Extra tools — factory does not mutate merged_pack (AT-10, AT-16) ----------


class TestExtraToolsFactoryClosure:
    """AT-10 (sequential) and AT-16 (concurrent): factory closure must not mutate
    the original merged_pack when extra_tools / extra_skills are provided."""

    def _hermetic_setup(self, monkeypatch, tmp_path):
        """Set up a hermetic sdk factory with a known merged pack."""
        from textwrap import dedent

        home = tmp_path / "home"
        agents_dir = home / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "planner.md").write_text(dedent("""\
            ---
            description: Test planner.
            tools: [Read, Grep]
            ---

            You are a planner.
            """))
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

    def test_factory_does_not_mutate_merged_pack_sequential(
        self, monkeypatch, tmp_path,
    ) -> None:
        """AT-10: two sequential spawns with different extras; original pack unchanged.

        Uses mock_build_merged_pack so pack baseline is known and extras are
        distinct from pack contents.  Pack has only ["Bash"].
        """
        import copy
        from claude_agent_sdk.types import AgentDefinition
        from claude_crew.factories import default_factory

        (tmp_path / "home").mkdir()
        (tmp_path / "cwd").mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        monkeypatch.chdir(tmp_path / "cwd")
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        planner_def = AgentDefinition(
            description="test planner",
            prompt="You plan.",
            tools=["Bash"],
        )

        def mock_build_merged_pack():
            return {"planner": planner_def}, {"planner": []}, {}

        monkeypatch.setattr(
            "claude_crew.subagents._user_loader.build_merged_pack",
            mock_build_merged_pack,
        )

        # Capture the agents dict passed to sdk_factory
        captured_agents: list[dict] = []

        def capturing_sdk_factory(id, name, role, **kwargs):
            agents = kwargs.get("agents", {})
            captured_agents.append(copy.deepcopy(agents))
            return StubTeammate(id, name, role)

        monkeypatch.setattr("claude_crew.factories.sdk_factory", capturing_sdk_factory)

        f = default_factory()
        original_tools = list(f.agent_def_resolver("planner").tools or [])

        # First spawn with extra_tools=["Write"]
        f("t-1", "n", "planner", extra_tools=["Write"])
        # Second spawn with extra_tools=["WebFetch"]
        f("t-2", "n", "planner", extra_tools=["WebFetch"])

        # Original pack must be unchanged
        post_spawn_tools = list(f.agent_def_resolver("planner").tools or [])
        assert post_spawn_tools == original_tools, (
            f"merged_pack[planner].tools was mutated: before={original_tools}, "
            f"after={post_spawn_tools}"
        )

        # First spawn: Bash (from pack) + Write (extra); no WebFetch
        assert "Bash" in captured_agents[0]["planner"].tools
        assert "Write" in captured_agents[0]["planner"].tools
        assert "WebFetch" not in captured_agents[0]["planner"].tools
        # Second spawn: Bash (from pack) + WebFetch (extra); no Write
        assert "Bash" in captured_agents[1]["planner"].tools
        assert "WebFetch" in captured_agents[1]["planner"].tools
        assert "Write" not in captured_agents[1]["planner"].tools

    async def test_factory_does_not_mutate_merged_pack_concurrent(
        self, monkeypatch, tmp_path,
    ) -> None:
        """AT-16: two concurrent spawns via asyncio.gather; each sees only its own extras.

        Uses mock_build_merged_pack so the pack baseline is known and extras
        are distinct from what's already in the pack.  Pack has only ["Bash"];
        t1 gets extra_tools=["Write"], t2 gets extra_tools=["WebFetch"].
        After gather: t1 must have ["Bash","Write"] only; t2 must have
        ["Bash","WebFetch"] only; original pack must still be ["Bash"].
        """
        import asyncio
        from claude_agent_sdk.types import AgentDefinition
        from claude_crew.broker import Broker
        from claude_crew.factories import default_factory

        (tmp_path / "home").mkdir()
        (tmp_path / "cwd").mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        monkeypatch.chdir(tmp_path / "cwd")
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        # Inject a controlled merged pack — planner has only ["Bash"]
        planner_def = AgentDefinition(
            description="test planner",
            prompt="You plan.",
            tools=["Bash"],
        )

        def mock_build_merged_pack():
            return {"planner": planner_def}, {"planner": []}, {}

        monkeypatch.setattr(
            "claude_crew.subagents._user_loader.build_merged_pack",
            mock_build_merged_pack,
        )

        # Capture the agents dict passed to sdk_factory on each call
        captured: dict[str, list[str]] = {}

        def capturing_sdk_factory(id, name, role, **kwargs):
            agents = kwargs.get("agents", {})
            agent_def = agents.get(role)
            captured[id] = list(getattr(agent_def, "tools", None) or [])
            return StubTeammate(id, name, role)

        monkeypatch.setattr("claude_crew.factories.sdk_factory", capturing_sdk_factory)
        capturing_sdk_factory.requires_auth = True

        f = default_factory()
        original_tools = list(f.agent_def_resolver("planner").tools or [])

        b = Broker()
        try:
            t1_id, t2_id = await asyncio.gather(
                b.spawn_teammate(
                    role="planner", name="p1", factory=f, extra_tools=["Write"]
                ),
                b.spawn_teammate(
                    role="planner", name="p2", factory=f, extra_tools=["WebFetch"]
                ),
            )

            # Original pack must be unchanged
            post_tools = list(f.agent_def_resolver("planner").tools or [])
            assert post_tools == original_tools, (
                f"merged_pack mutated after concurrent spawns: {original_tools} → {post_tools}"
            )

            # t1 must have only Bash + Write (not WebFetch from t2)
            assert "Bash" in captured[t1_id], f"t1 must inherit pack Bash; got {captured[t1_id]}"
            assert "Write" in captured[t1_id], f"t1 must have Write; got {captured[t1_id]}"
            assert "WebFetch" not in captured[t1_id], (
                f"t1 must NOT have WebFetch (t2's extra); got {captured[t1_id]}"
            )

            # t2 must have only Bash + WebFetch (not Write from t1)
            assert "Bash" in captured[t2_id], f"t2 must inherit pack Bash; got {captured[t2_id]}"
            assert "WebFetch" in captured[t2_id], f"t2 must have WebFetch; got {captured[t2_id]}"
            assert "Write" not in captured[t2_id], (
                f"t2 must NOT have Write (t1's extra); got {captured[t2_id]}"
            )
        finally:
            await b.shutdown_all()
