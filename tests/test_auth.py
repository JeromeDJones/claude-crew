"""Tests for auth detection and validate_auth_or_exit."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from claude_crew import auth


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)


@pytest.fixture
def fake_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


class TestHasUsableCredential:
    def test_anthropic_api_key_set(self, clean_env, fake_home, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert auth.has_usable_credential() is True

    def test_oauth_token_env_var_set(self, clean_env, fake_home, monkeypatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-test")
        assert auth.has_usable_credential() is True

    def test_credentials_file_exists(self, clean_env, fake_home) -> None:
        creds_dir = fake_home / ".claude"
        creds_dir.mkdir()
        (creds_dir / ".credentials.json").write_text("{}")
        assert auth.has_usable_credential() is True

    def test_no_credentials_anywhere(self, clean_env, fake_home) -> None:
        assert auth.has_usable_credential() is False

    def test_empty_api_key_treated_as_missing(
        self, clean_env, fake_home, monkeypatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        assert auth.has_usable_credential() is False


class TestValidateAuthOrExit:
    def test_passes_silently_when_credentials_present(
        self, clean_env, fake_home, monkeypatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        # Should not raise.
        auth.validate_auth_or_exit()

    def test_exits_with_code_2_when_no_credentials(
        self, clean_env, fake_home, capsys,
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            auth.validate_auth_or_exit()
        assert exc_info.value.code == 2

    def test_stderr_mentions_both_options(
        self, clean_env, fake_home, capsys,
    ) -> None:
        with pytest.raises(SystemExit):
            auth.validate_auth_or_exit()
        err = capsys.readouterr().err
        assert "claude login" in err
        assert "ANTHROPIC_API_KEY" in err
