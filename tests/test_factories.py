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
            return {}, {}

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
            return {"explorer": mock_agent_def}, {"explorer": []}

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
            return {"explorer": mock_agent_def}, {"explorer": []}

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
