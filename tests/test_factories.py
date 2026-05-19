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
        assert "general" in teammate._agents  # bundled

    def test_default_factory_resolves_namespaced_plugin_role(
        self, monkeypatch, tmp_path,
    ) -> None:
        """A lead spawning by the Claude Code plugin surface form
        (``<plugin>:<role>``) gets the plugin's AgentDefinition resolved
        through the merged pack: model, tools, and prompt come from the
        plugin file, not from the synthetic empty fallback in
        factories.py:158-165 that fires on unknown roles."""
        import json
        from textwrap import dedent

        from claude_crew.sdk_teammate import SdkTeammate

        home = tmp_path / "home"
        plugins_root = home / ".claude" / "plugins"
        install = plugins_root / "cache" / "rr"
        agents_dir = install / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "rr-planner.md").write_text(dedent("""\
            ---
            description: Plugin-shipped planner.
            model: haiku
            tools: [Read, Grep]
            ---

            You are the rr-planner.
            """))
        plugins_root.mkdir(parents=True, exist_ok=True)
        (plugins_root / "installed_plugins.json").write_text(json.dumps({
            "version": 2,
            "plugins": {
                "repo-reactor@m": [
                    {"scope": "user", "installPath": str(install)},
                ],
            },
        }))
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        f = factories.default_factory()
        # Happy path: namespaced role resolves to the plugin's AgentDef.
        teammate = f("t-ns", "alice", "repo-reactor:rr-planner")
        assert isinstance(teammate, SdkTeammate)
        assert "repo-reactor:rr-planner" in teammate._agents
        agent_def = teammate._agents["repo-reactor:rr-planner"]
        assert agent_def.description == "Plugin-shipped planner."
        assert "Read" in (agent_def.tools or [])
        # The bare role name from the plugin file does NOT leak into the
        # merged pack — that would be the pre-fix bug.
        assert "rr-planner" not in teammate._agents

    def test_default_factory_auto_resolves_bare_role_to_plugin_namespace(
        self, monkeypatch, tmp_path, caplog,
    ) -> None:
        """A lead spawning by the bare role name (e.g. ``rr-planner``) when
        only ``repo-reactor:rr-planner`` exists in the pack auto-promotes
        to the namespaced key, logs INFO, and the AgentDef flows through.
        Mitigates the silent-empty-AgentDef regression for legacy callers."""
        import json
        import logging
        from textwrap import dedent

        from claude_crew.sdk_teammate import SdkTeammate

        home = tmp_path / "home"
        plugins_root = home / ".claude" / "plugins"
        install = plugins_root / "cache" / "rr"
        agents_dir = install / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "rr-planner.md").write_text(dedent("""\
            ---
            description: Plugin-shipped planner.
            model: haiku
            tools: [Read]
            ---

            You are the rr-planner.
            """))
        plugins_root.mkdir(parents=True, exist_ok=True)
        (plugins_root / "installed_plugins.json").write_text(json.dumps({
            "version": 2,
            "plugins": {
                "repo-reactor@m": [
                    {"scope": "user", "installPath": str(install)},
                ],
            },
        }))
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        caplog.set_level(logging.INFO, logger="claude_crew.factories")
        f = factories.default_factory()
        teammate = f("t-bare", "alice", "rr-planner")

        assert isinstance(teammate, SdkTeammate)
        # The teammate's role was rewritten to the namespaced form so the
        # SDK's lookup of agents[role] hits the correct AgentDefinition.
        assert teammate.role == "repo-reactor:rr-planner"
        assert teammate._agents["repo-reactor:rr-planner"].description == (
            "Plugin-shipped planner."
        )
        msgs = [r.getMessage() for r in caplog.records]
        assert any(
            "auto-resolving" in m and "rr-planner" in m
            and "repo-reactor:rr-planner" in m
            for m in msgs
        ), f"auto-resolve INFO missing; got: {msgs}"

    def test_default_factory_resolver_for_broker_promotes_bare_role(
        self, monkeypatch, tmp_path,
    ) -> None:
        """The resolver attached to ``factory.agent_def_resolver`` (used by
        the broker for the dashboard config snapshot) must apply the same
        bare-name → namespaced promotion as the spawn path. Otherwise a
        lead spawning ``rr-planner`` gets a correct SDK process but an
        empty dashboard config chip."""
        import json
        from textwrap import dedent

        home = tmp_path / "home"
        plugins_root = home / ".claude" / "plugins"
        install = plugins_root / "cache" / "rr"
        agents_dir = install / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "rr-planner.md").write_text(dedent("""\
            ---
            description: Plugin-shipped planner.
            model: haiku
            tools: [Read]
            ---

            You are the rr-planner.
            """))
        (plugins_root / "installed_plugins.json").write_text(json.dumps({
            "version": 2,
            "plugins": {
                "repo-reactor@m": [
                    {"scope": "user", "installPath": str(install)},
                ],
            },
        }))
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        f = factories.default_factory()
        resolver = f.agent_def_resolver  # type: ignore[attr-defined]
        # Bare name → AgentDef from the namespaced plugin entry.
        agent_def = resolver("rr-planner")
        assert agent_def is not None
        assert agent_def.description == "Plugin-shipped planner."
        # Already-namespaced still works.
        assert resolver("repo-reactor:rr-planner") is agent_def or (
            resolver("repo-reactor:rr-planner").description
            == "Plugin-shipped planner."
        )
        # Unrelated bare names still return None.
        assert resolver("does-not-exist") is None

    def test_default_factory_resolver_does_not_match_partial_suffix(
        self, monkeypatch, tmp_path,
    ) -> None:
        """`endswith(":<role>")` is a boundary anchor: a plugin shipping
        ``foo:my-planner`` must NOT auto-resolve a request for bare
        ``planner``. Verifies the colon prefix prevents substring leaks."""
        import json
        from textwrap import dedent

        home = tmp_path / "home"
        plugins_root = home / ".claude" / "plugins"
        install = plugins_root / "cache" / "foo"
        agents_dir = install / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "my-planner.md").write_text(dedent("""\
            ---
            description: Foo's my-planner.
            model: haiku
            tools: [Read]
            ---

            Foo body.
            """))
        (plugins_root / "installed_plugins.json").write_text(json.dumps({
            "version": 2,
            "plugins": {
                "foo@m": [{"scope": "user", "installPath": str(install)}],
            },
        }))
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        f = factories.default_factory()
        resolver = f.agent_def_resolver  # type: ignore[attr-defined]
        # Bundled `planner` exists in the default pack — bare lookup hits
        # it directly, no promotion to `foo:my-planner`.
        bundled = resolver("planner")
        assert bundled is not None
        assert "Foo" not in (bundled.description or "")
        # `my-planner` (the bare role from the plugin file) auto-promotes.
        assert resolver("my-planner").description == "Foo's my-planner."

    def test_default_factory_ambiguous_bare_role_warns_and_does_not_promote(
        self, monkeypatch, tmp_path, caplog,
    ) -> None:
        """Two plugins shipping the same bare role name — bare lookup is
        ambiguous, so resolver WARNs with both candidates and falls
        through. The teammate spawns with the original (bare) role,
        which won't match the pack and lands on the synthetic empty
        AgentDef path. The WARN is the operator's signal to spawn the
        namespaced form."""
        import json
        import logging
        from textwrap import dedent

        from claude_crew.sdk_teammate import SdkTeammate

        home = tmp_path / "home"
        plugins_root = home / ".claude" / "plugins"
        a = plugins_root / "a"
        b = plugins_root / "b"
        for d, label in ((a, "from-a"), (b, "from-b")):
            (d / "agents").mkdir(parents=True)
            (d / "agents" / "shared.md").write_text(dedent(f"""\
                ---
                description: {label}
                model: haiku
                tools: [Read]
                ---

                Body {label}.
                """))
        (plugins_root / "installed_plugins.json").write_text(json.dumps({
            "version": 2,
            "plugins": {
                "alpha@m": [{"scope": "user", "installPath": str(a)}],
                "beta@m": [{"scope": "user", "installPath": str(b)}],
            },
        }))
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        caplog.set_level(logging.WARNING, logger="claude_crew.factories")
        f = factories.default_factory()
        teammate = f("t-ambig", "alice", "shared")

        assert isinstance(teammate, SdkTeammate)
        # Original bare role kept — no silent promotion to one or the other.
        assert teammate.role == "shared"
        msgs = [r.getMessage() for r in caplog.records]
        assert any(
            "shared" in m and "alpha:shared" in m and "beta:shared" in m
            and "disambiguate" in m
            for m in msgs
        ), f"ambiguous-resolve WARN missing; got: {msgs}"


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


# ---------- MCP server auto-wiring from extra_tools ----------


class TestMcpServerNameFromToolId:
    """Unit tests for the _mcp_server_name_from_tool_id helper."""

    def test_extracts_server_from_mcp_tool(self) -> None:
        from claude_crew.factories import _mcp_server_name_from_tool_id
        assert _mcp_server_name_from_tool_id("mcp__knowledge-graph__repo_map") == "knowledge-graph"

    def test_extracts_hyphenated_server(self) -> None:
        from claude_crew.factories import _mcp_server_name_from_tool_id
        assert _mcp_server_name_from_tool_id("mcp__claude-crew__spawn_teammate") == "claude-crew"

    def test_returns_none_for_builtin_tool(self) -> None:
        from claude_crew.factories import _mcp_server_name_from_tool_id
        assert _mcp_server_name_from_tool_id("Read") is None
        assert _mcp_server_name_from_tool_id("Bash") is None

    def test_returns_none_for_non_mcp_prefix(self) -> None:
        from claude_crew.factories import _mcp_server_name_from_tool_id
        assert _mcp_server_name_from_tool_id("some__other__tool") is None

    def test_returns_none_for_too_few_parts(self) -> None:
        from claude_crew.factories import _mcp_server_name_from_tool_id
        assert _mcp_server_name_from_tool_id("mcp__knowledge-graph") is None


class TestMcpServerAutoWiring:
    """extra_tools with MCP tool IDs must wire the corresponding server into mcpServers."""

    def _make_factory(self, monkeypatch, tmp_path, pack_def):
        """Build a default_factory with a controlled merged pack, capturing sdk_factory calls."""
        import copy
        from claude_crew.factories import default_factory

        (tmp_path / "home").mkdir(exist_ok=True)
        (tmp_path / "cwd").mkdir(exist_ok=True)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        monkeypatch.chdir(tmp_path / "cwd")
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        def mock_build_merged_pack():
            return {"explorer": pack_def}, {"explorer": []}, {}

        monkeypatch.setattr(
            "claude_crew.subagents._user_loader.build_merged_pack",
            mock_build_merged_pack,
        )

        captured: list[dict] = []

        def capturing_sdk_factory(id, name, role, **kwargs):
            agents = kwargs.get("agents", {})
            captured.append(copy.deepcopy(agents))
            return StubTeammate(id, name, role)

        monkeypatch.setattr("claude_crew.factories.sdk_factory", capturing_sdk_factory)
        return default_factory(), captured

    def test_mcp_tool_in_extra_tools_adds_server_to_mcp_servers(
        self, monkeypatch, tmp_path,
    ) -> None:
        """Granting mcp__knowledge-graph__repo_map must add 'knowledge-graph' to mcpServers."""
        from claude_agent_sdk.types import AgentDefinition

        pack_def = AgentDefinition(
            description="explorer", prompt="explore", tools=["Read", "Grep", "Glob"],
        )
        f, captured = self._make_factory(monkeypatch, tmp_path, pack_def)
        f("t-1", "explorer", "explorer", extra_tools=["mcp__knowledge-graph__repo_map"])

        agent_def = captured[0]["explorer"]
        assert "mcp__knowledge-graph__repo_map" in (agent_def.tools or [])
        assert "knowledge-graph" in (agent_def.mcpServers or []), (
            f"expected 'knowledge-graph' in mcpServers; got {agent_def.mcpServers}"
        )

    def test_multiple_mcp_tools_same_server_deduplicated(
        self, monkeypatch, tmp_path,
    ) -> None:
        """Two tools from the same server must produce exactly one mcpServers entry."""
        from claude_agent_sdk.types import AgentDefinition

        pack_def = AgentDefinition(
            description="explorer", prompt="explore", tools=["Read"],
        )
        f, captured = self._make_factory(monkeypatch, tmp_path, pack_def)
        f("t-1", "n", "explorer", extra_tools=[
            "mcp__knowledge-graph__repo_map",
            "mcp__knowledge-graph__search_codebase_definitions",
        ])

        mcp_servers = captured[0]["explorer"].mcpServers or []
        assert mcp_servers.count("knowledge-graph") == 1

    def test_mcp_server_not_duplicated_when_already_in_pack(
        self, monkeypatch, tmp_path,
    ) -> None:
        """If the pack already declares the server, it must not be added twice."""
        from claude_agent_sdk.types import AgentDefinition

        pack_def = AgentDefinition(
            description="explorer", prompt="explore", tools=["Read"],
            mcpServers=["knowledge-graph"],
        )
        f, captured = self._make_factory(monkeypatch, tmp_path, pack_def)
        f("t-1", "n", "explorer", extra_tools=["mcp__knowledge-graph__repo_map"])

        mcp_servers = captured[0]["explorer"].mcpServers or []
        assert mcp_servers.count("knowledge-graph") == 1

    def test_builtin_extra_tools_do_not_add_mcp_servers(
        self, monkeypatch, tmp_path,
    ) -> None:
        """Built-in tools like 'Write' must not add anything to mcpServers."""
        from claude_agent_sdk.types import AgentDefinition

        pack_def = AgentDefinition(
            description="explorer", prompt="explore", tools=["Read"],
        )
        f, captured = self._make_factory(monkeypatch, tmp_path, pack_def)
        f("t-1", "n", "explorer", extra_tools=["Write", "Bash"])

        agent_def = captured[0]["explorer"]
        assert not (agent_def.mcpServers or []), (
            f"expected empty mcpServers for builtin-only extras; got {agent_def.mcpServers}"
        )

    def test_mixed_builtin_and_mcp_extra_tools(
        self, monkeypatch, tmp_path,
    ) -> None:
        """Mix of builtins and MCP tools: only MCP servers are auto-wired."""
        from claude_agent_sdk.types import AgentDefinition

        pack_def = AgentDefinition(
            description="explorer", prompt="explore", tools=["Read"],
        )
        f, captured = self._make_factory(monkeypatch, tmp_path, pack_def)
        f("t-1", "n", "explorer", extra_tools=[
            "Write",
            "mcp__knowledge-graph__repo_map",
            "mcp__claude-crew__list_crew",
        ])

        agent_def = captured[0]["explorer"]
        mcp_servers = agent_def.mcpServers or []
        assert "knowledge-graph" in mcp_servers
        assert "claude-crew" in mcp_servers
        assert len(mcp_servers) == 2


class TestPackEffortPromotion:
    """Pack frontmatter `effort:` must flow to SdkTeammate at top-level spawn,
    mirroring the existing `model:` promotion path. Lead's spawn-time kwarg wins.
    """

    def _make_factory(self, monkeypatch, tmp_path, pack_def):
        """Build a default_factory with a controlled merged pack, capturing sdk_factory kwargs."""
        from claude_crew.factories import default_factory

        (tmp_path / "home").mkdir(exist_ok=True)
        (tmp_path / "cwd").mkdir(exist_ok=True)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        monkeypatch.chdir(tmp_path / "cwd")
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")

        def mock_build_merged_pack():
            return {"explorer": pack_def}, {"explorer": []}, {}

        monkeypatch.setattr(
            "claude_crew.subagents._user_loader.build_merged_pack",
            mock_build_merged_pack,
        )

        captured: list[dict] = []

        def capturing_sdk_factory(id, name, role, **kwargs):
            captured.append(dict(kwargs))
            return StubTeammate(id, name, role)

        monkeypatch.setattr("claude_crew.factories.sdk_factory", capturing_sdk_factory)
        return default_factory(), captured

    def test_pack_effort_promoted_when_kwarg_absent(self, monkeypatch, tmp_path) -> None:
        """Role-pack `effort: medium` reaches SdkTeammate when lead omits effort."""
        from claude_agent_sdk.types import AgentDefinition

        pack_def = AgentDefinition(
            description="explorer", prompt="explore", tools=["Read"],
            effort="medium",
        )
        f, captured = self._make_factory(monkeypatch, tmp_path, pack_def)
        f("t-1", "n", "explorer")  # no effort kwarg

        assert captured[0]["effort"] == "medium", (
            f"expected pack effort 'medium' to be promoted; got {captured[0].get('effort')!r}"
        )

    def test_spawn_kwarg_effort_overrides_pack_effort(self, monkeypatch, tmp_path) -> None:
        """Lead's explicit effort= wins over pack-declared effort."""
        from claude_agent_sdk.types import AgentDefinition

        pack_def = AgentDefinition(
            description="explorer", prompt="explore", tools=["Read"],
            effort="medium",
        )
        f, captured = self._make_factory(monkeypatch, tmp_path, pack_def)
        f("t-1", "n", "explorer", effort="high")

        assert captured[0]["effort"] == "high", (
            f"expected kwarg 'high' to win; got {captured[0].get('effort')!r}"
        )

    def test_no_pack_effort_no_kwarg_passes_none(self, monkeypatch, tmp_path) -> None:
        """When neither pack nor kwarg sets effort, None is passed (SDK default applies)."""
        from claude_agent_sdk.types import AgentDefinition

        pack_def = AgentDefinition(
            description="explorer", prompt="explore", tools=["Read"],
        )
        f, captured = self._make_factory(monkeypatch, tmp_path, pack_def)
        f("t-1", "n", "explorer")

        assert captured[0]["effort"] is None, (
            f"expected None for unset effort; got {captured[0].get('effort')!r}"
        )
