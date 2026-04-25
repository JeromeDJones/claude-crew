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
    def test_default_when_unset_is_sdk(self, monkeypatch) -> None:
        monkeypatch.delenv("CLAUDE_CREW_TEAMMATE_MODE", raising=False)
        assert factories.default_factory() is factories.sdk_factory

    def test_explicit_sdk_mode(self, monkeypatch) -> None:
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
        assert factories.default_factory() is factories.sdk_factory

    def test_explicit_stub_mode(self, monkeypatch) -> None:
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "stub")
        assert factories.default_factory() is factories.stub_factory

    def test_unknown_value_falls_back_to_stub(self, monkeypatch) -> None:
        # Conservative default: anything that isn't "sdk" goes to stub so a
        # typo doesn't accidentally invoke the real model.
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "garbage")
        assert factories.default_factory() is factories.stub_factory


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
