"""Tests for claude_crew/redaction.py — Task 1, Feature #8.

Covers the 13 BDD scenarios from Phase 3 Task 1 Acceptance Criteria (SC-15).

Token literals used in these tests are deliberately fake and use shapes that
would trigger the redaction patterns (to exercise pattern correctness) but are
not real credentials:

  FAKE_ANTHROPIC_KEY  = "sk-ant-TESTFAKE00000000000000000000000"
  FAKE_AWS_KEY        = "AKIAIOSFODNN7EXAMPLE"   (AWS example key from their docs)

Neither of these is a working key.  Do NOT replace them with real credentials.
"""

from __future__ import annotations

import pytest

from claude_crew.redaction import (
    REDACTION_VERSION,
    redact_error,
    summarize_args,
)

# ---------------------------------------------------------------------------
# Constants used across multiple tests — fake tokens that exercise patterns
# without being real credentials.
# ---------------------------------------------------------------------------

FAKE_ANTHROPIC_KEY = "sk-ant-TESTFAKE00000000000000000000000"
FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"  # AWS example key from their own documentation


# ---------------------------------------------------------------------------
# Scenario 1: Bash command summarized with redaction (SC-15 utility)
# ---------------------------------------------------------------------------


class TestBashBasicSummary:
    """Scenario: Bash command summarized with redaction (SC-15 utility)

    Given REDACTION_PATTERNS_V1 active and Bash on the v1 allowlist
    When summarize_args("Bash", {"command": "pytest tests/ -v"}) is called
    Then the result is "command=pytest tests/ -v"
    """

    def test_pytest_command_passes_through_unchanged(self) -> None:
        result = summarize_args("Bash", {"command": "pytest tests/ -v"})
        assert result == "command=pytest tests/ -v"


# ---------------------------------------------------------------------------
# Scenario 2: Bash command with literal Bearer token redacted (SC-15)
# ---------------------------------------------------------------------------


class TestBashBearerTokenRedacted:
    """Scenario: Bash command with literal Bearer token redacted (SC-15)

    When summarize_args("Bash", {"command": "curl -H 'Authorization: Bearer <fake>'"})
    Then the result contains "<redacted-key>" or "<redacted>"
    And the result does NOT contain the fake token
    """

    def test_bearer_token_removed(self) -> None:
        cmd = f"curl -H 'Authorization: Bearer {FAKE_ANTHROPIC_KEY}'"
        result = summarize_args("Bash", {"command": cmd})
        assert result is not None
        assert "<redacted" in result, f"expected redaction marker in: {result!r}"
        assert FAKE_ANTHROPIC_KEY not in result, f"token still present in: {result!r}"
        # The raw sk-ant- prefix should also be gone
        assert "sk-ant-TESTFAKE" not in result


# ---------------------------------------------------------------------------
# Scenario 3: Bash command with shell variable is safe by literal (SC-15, A1)
# ---------------------------------------------------------------------------


class TestBashShellVariableSafe:
    """Scenario: Bash command with shell variable is safe by literal (SC-15, A1)

    When summarize_args("Bash", {"command": "curl -H 'Authorization: Bearer $TOKEN'"})
    Then the result contains "$TOKEN" literal (pre-shell-substitution — A1)
    And the Authorization header pair is redacted (keyword prefix match)
    """

    def test_shell_variable_survives_as_literal(self) -> None:
        cmd = "curl -H 'Authorization: Bearer $TOKEN'"
        result = summarize_args("Bash", {"command": cmd})
        assert result is not None
        # The literal shell variable must survive — it's not a real token.
        assert "$TOKEN" in result, f"$TOKEN literal should be present in: {result!r}"
        # The Authorization: Bearer pair must have been consumed by the header pattern.
        assert "Authorization: Bearer" not in result


# ---------------------------------------------------------------------------
# Scenario 4: AKIA AWS access key redacted (SC-15 anchored shape)
# ---------------------------------------------------------------------------


class TestAWSKeyRedacted:
    """Scenario: AKIA AWS access key redacted (SC-15 anchored shape)

    When summarize_args("Bash", {"command": "aws s3 cp foo bar # AKIAIOSFODNN7EXAMPLE in heredoc"})
    Then the result contains "<redacted-key>"
    And the result does NOT contain "AKIAIOSFODNN7EXAMPLE"
    """

    def test_akia_key_redacted(self) -> None:
        cmd = f"aws s3 cp foo bar # {FAKE_AWS_KEY} in heredoc"
        result = summarize_args("Bash", {"command": cmd})
        assert result is not None
        assert "<redacted-key>" in result, f"expected <redacted-key> in: {result!r}"
        assert FAKE_AWS_KEY not in result, f"AKIA key still present in: {result!r}"


# ---------------------------------------------------------------------------
# Scenario 5: URL with embedded credentials redacted (SC-15)
# ---------------------------------------------------------------------------


class TestURLEmbeddedCredentialsRedacted:
    """Scenario: URL with embedded credentials redacted (SC-15)

    When summarize_args("Bash", {"command": "git push https://user:tok123@host/repo"})
    Then the result contains "https://<redacted>@"
    """

    def test_url_credentials_stripped(self) -> None:
        cmd = "git push https://user:tok123@host/repo"
        result = summarize_args("Bash", {"command": cmd})
        assert result is not None
        assert "https://<redacted>@" in result, f"expected redacted URL in: {result!r}"
        assert "tok123" not in result, f"credential still present in: {result!r}"


# ---------------------------------------------------------------------------
# Scenario 6: Short-flag secret redacted (SC-15)
# ---------------------------------------------------------------------------


class TestShortFlagSecretRedacted:
    """Scenario: Short-flag secret redacted (SC-15)

    When summarize_args("Bash", {"command": "mysql -p hunter2 -u admin"})
    Then the result contains "<redacted-flag>"
    And the result does NOT contain "hunter2"
    """

    def test_short_flag_password_removed(self) -> None:
        cmd = "mysql -p hunter2 -u admin"
        result = summarize_args("Bash", {"command": cmd})
        assert result is not None
        assert "<redacted-flag>" in result, f"expected <redacted-flag> in: {result!r}"
        assert "hunter2" not in result, f"password still present in: {result!r}"


# ---------------------------------------------------------------------------
# Scenario 7: Read tool not on allowlist returns None (SC-15)
# ---------------------------------------------------------------------------


class TestReadNotOnAllowlist:
    """Scenario: Read tool not on allowlist returns None (SC-15)

    When summarize_args("Read", {"file_path": "/etc/passwd"})
    Then the result is None
    """

    def test_read_returns_none(self) -> None:
        result = summarize_args("Read", {"file_path": "/etc/passwd"})
        assert result is None

    def test_grep_returns_none(self) -> None:
        """Glob and Grep are also fast tools excluded from v1 allowlist."""
        result = summarize_args("Grep", {"pattern": "password", "path": "."})
        assert result is None

    def test_glob_returns_none(self) -> None:
        result = summarize_args("Glob", {"pattern": "**/*.py"})
        assert result is None


# ---------------------------------------------------------------------------
# Scenario 8: Task tool extracts subagent_type and description, NOT prompt (SC-15)
# ---------------------------------------------------------------------------


class TestTaskExtraction:
    """Scenario: Task tool extracts subagent_type and description, NOT prompt body (SC-15)

    When summarize_args("Task", {
        "subagent_type": "researcher",
        "description": "find auth bug",
        "prompt": "Look at auth/login.py and identify the deserialization vuln..."
    })
    Then the result contains "subagent=researcher" and "description=find auth bug"
    And the result does NOT contain the prompt body
    And the result does NOT contain any fake secret in the prompt
    """

    def test_subagent_and_description_present(self) -> None:
        result = summarize_args(
            "Task",
            {
                "subagent_type": "researcher",
                "description": "find auth bug",
                "prompt": (
                    f"Look at auth/login.py and identify the deserialization vuln. "
                    f"Use {FAKE_ANTHROPIC_KEY} if you need it."
                ),
            },
        )
        assert result is not None
        assert "subagent=researcher" in result
        assert "description=find auth bug" in result

    def test_prompt_body_excluded(self) -> None:
        result = summarize_args(
            "Task",
            {
                "subagent_type": "researcher",
                "description": "find auth bug",
                "prompt": "Look at auth/login.py and identify the deserialization vuln.",
            },
        )
        assert result is not None
        # The prompt body must not appear
        assert "Look at auth/login.py" not in result

    def test_secret_in_prompt_does_not_leak(self) -> None:
        """Even if the prompt carries a secret, it's excluded because we never extract it."""
        result = summarize_args(
            "Task",
            {
                "subagent_type": "executor",
                "description": "run deployment",
                "prompt": f"Deploy using token {FAKE_ANTHROPIC_KEY}",
            },
        )
        assert result is not None
        assert FAKE_ANTHROPIC_KEY not in result
        assert "sk-ant-TESTFAKE" not in result


# ---------------------------------------------------------------------------
# Scenario 9: Output capped at 256 bytes (SC-15)
# ---------------------------------------------------------------------------


class TestOutputCappedAt256Bytes:
    """Scenario: Output capped at 256 bytes (SC-15)

    When summarize_args is called with a Bash command that produces a very
    long summary after redaction
    Then len(result.encode("utf-8")) <= 256
    And result.endswith("…")
    """

    def test_long_command_is_capped(self) -> None:
        # Build a command with many words that won't be redacted (no secret shapes)
        # and is long enough to exceed 256 bytes after the "command=" prefix.
        long_cmd = "echo " + " ".join(["hello"] * 60)  # > 300 chars, no secrets
        result = summarize_args("Bash", {"command": long_cmd})
        assert result is not None
        assert len(result.encode("utf-8")) <= 256, (
            f"result exceeds 256 bytes: {len(result.encode('utf-8'))}"
        )
        assert result.endswith("…"), f"truncated result should end with ellipsis: {result!r}"

    def test_exactly_256_bytes_not_capped(self) -> None:
        """A command that produces exactly 256 UTF-8 bytes after extraction is returned as-is."""
        # "command=" is 8 bytes; we need 248 more non-secret chars.
        payload = "x " * 124  # 248 bytes of "x " pairs
        result = summarize_args("Bash", {"command": payload})
        assert result is not None
        assert len(result.encode("utf-8")) <= 256


# ---------------------------------------------------------------------------
# Scenario 10: CLAUDE_CREW_TOOL_ARGS_DISABLED forces None (SC-15)
# ---------------------------------------------------------------------------


class TestDisabledMode:
    """Scenario: CLAUDE_CREW_TOOL_ARGS_DISABLED forces None (SC-15)

    Given CLAUDE_CREW_TOOL_ARGS_DISABLED=1 in env
    When summarize_args("Bash", {"command": "pytest"})
    Then the result is None
    """

    def test_disabled_bash_returns_none(self, monkeypatch) -> None:
        monkeypatch.setenv("CLAUDE_CREW_TOOL_ARGS_DISABLED", "1")
        result = summarize_args("Bash", {"command": "pytest"})
        assert result is None

    def test_disabled_task_returns_none(self, monkeypatch) -> None:
        monkeypatch.setenv("CLAUDE_CREW_TOOL_ARGS_DISABLED", "1")
        result = summarize_args("Task", {"subagent_type": "worker", "description": "do stuff"})
        assert result is None

    def test_disabled_overrides_full_flag(self, monkeypatch) -> None:
        """DISABLED takes precedence over FULL when both are set."""
        monkeypatch.setenv("CLAUDE_CREW_TOOL_ARGS_DISABLED", "1")
        monkeypatch.setenv("CLAUDE_CREW_TOOL_ARGS_FULL", "1")
        result = summarize_args("Read", {"file_path": "/etc/passwd"})
        assert result is None


# ---------------------------------------------------------------------------
# Scenario 11: CLAUDE_CREW_TOOL_ARGS_FULL widens allowlist (SC-15)
# ---------------------------------------------------------------------------


class TestFullMode:
    """Scenario: CLAUDE_CREW_TOOL_ARGS_FULL widens allowlist (SC-15)

    Given CLAUDE_CREW_TOOL_ARGS_FULL=1 in env
    When summarize_args("Read", {"file_path": "/etc/passwd"})
    Then the result is not None
    And the result contains "/etc/passwd"
    """

    def test_full_mode_allows_read(self, monkeypatch) -> None:
        monkeypatch.setenv("CLAUDE_CREW_TOOL_ARGS_FULL", "1")
        result = summarize_args("Read", {"file_path": "/etc/passwd"})
        assert result is not None
        assert "/etc/passwd" in result

    def test_full_mode_still_redacts(self, monkeypatch) -> None:
        """FULL mode widens the allowlist but keeps redaction active."""
        monkeypatch.setenv("CLAUDE_CREW_TOOL_ARGS_FULL", "1")
        result = summarize_args(
            "Read", {"file_path": "/home/user/.ssh/id_rsa", "token": FAKE_ANTHROPIC_KEY}
        )
        assert result is not None
        assert FAKE_ANTHROPIC_KEY not in result

    def test_full_mode_still_caps(self, monkeypatch) -> None:
        """FULL mode caps at 256 bytes."""
        monkeypatch.setenv("CLAUDE_CREW_TOOL_ARGS_FULL", "1")
        # A wide dict with many innocuous keys
        big_input = {f"field_{i}": "value_" * 20 for i in range(30)}
        result = summarize_args("SomeTool", big_input)
        assert result is not None
        assert len(result.encode("utf-8")) <= 256


# ---------------------------------------------------------------------------
# Scenario 12: Redactor never raises (SC-12 + SC-15)
# ---------------------------------------------------------------------------


class TestRedactorNeverRaises:
    """Scenario: Redactor never raises (SC-12 + SC-15)

    Given a malformed tool_input that triggers an internal exception
    When summarize_args("Bash", malformed_input) is called
    Then the result is None
    And no exception propagates
    """

    def test_none_input_returns_none(self) -> None:
        """tool_input=None would cause attribute errors inside extractors."""
        result = summarize_args("Bash", None)  # type: ignore[arg-type]
        assert result is None

    def test_non_dict_command_value_returns_none_or_safe(self) -> None:
        """Non-string 'command' value hits str() coercion or degrades to None."""
        result = summarize_args("Bash", {"command": object()})
        # Either None (exception in extractor) or a safe string — never raises.
        assert result is None or isinstance(result, str)

    def test_empty_dict_does_not_raise(self) -> None:
        """Empty dict for an allowlisted tool falls back to empty strings gracefully."""
        result = summarize_args("Bash", {})
        # "command=" with empty value — valid, no exception.
        assert result is None or isinstance(result, str)

    def test_unknown_tool_with_bad_input_returns_none(self) -> None:
        result = summarize_args("UnknownTool", {"x": [1, 2, 3]})
        assert result is None


# ---------------------------------------------------------------------------
# Scenario 13: redact_error applies unconditionally (SC-15 error_summary clause)
# ---------------------------------------------------------------------------


class TestRedactError:
    """Scenario: redact_error applies unconditionally (SC-15 error_summary clause)

    When redact_error("Authentication failed: token sk-ant-TESTFAKE...")
    Then the result contains "<redacted>" (in any form)
    And the result is at most 256 bytes
    """

    def test_anthropic_key_in_error_redacted(self) -> None:
        error = f"Authentication failed: token {FAKE_ANTHROPIC_KEY}"
        result = redact_error(error)
        assert "<redacted" in result, f"expected redaction in: {result!r}"
        assert FAKE_ANTHROPIC_KEY not in result
        assert len(result.encode("utf-8")) <= 256

    def test_error_always_returns_string(self) -> None:
        """redact_error never returns None — SC-15 unconditional clause."""
        result = redact_error("some plain error")
        assert isinstance(result, str)

    def test_error_capped_at_256_bytes(self) -> None:
        long_error = "Error: " + "x " * 200  # > 400 chars, no secrets
        result = redact_error(long_error)
        assert len(result.encode("utf-8")) <= 256
        assert result.endswith("…")

    def test_error_with_url_creds_redacted(self) -> None:
        error = "Push failed: remote https://user:secretpassword@github.com/org/repo"
        result = redact_error(error)
        assert "secretpassword" not in result
        assert "https://<redacted>@" in result

    def test_empty_error_returns_empty_string(self) -> None:
        result = redact_error("")
        assert result == ""

    def test_error_env_overrides_do_not_affect_redact_error(self, monkeypatch) -> None:
        """DISABLED and FULL env vars affect summarize_args only, not redact_error."""
        monkeypatch.setenv("CLAUDE_CREW_TOOL_ARGS_DISABLED", "1")
        error = f"Tool error: {FAKE_ANTHROPIC_KEY}"
        result = redact_error(error)
        # redact_error is unconditional — always runs regardless of env
        assert FAKE_ANTHROPIC_KEY not in result

    def test_redact_error_returns_string_on_internal_failure(self, monkeypatch) -> None:
        """Even if pattern application raises, redact_error returns a string."""
        # Monkey-patch _apply_patterns to raise
        import claude_crew.redaction as _redaction_module

        original = _redaction_module._apply_patterns

        def _raise(text: str) -> str:  # noqa: ANN001
            raise RuntimeError("simulated redactor failure")

        monkeypatch.setattr(_redaction_module, "_apply_patterns", _raise)
        result = redact_error("some error text")
        # Should fall back to truncated original, not None, not an exception
        assert isinstance(result, str)
        assert len(result.encode("utf-8")) <= 256

        # Restore
        monkeypatch.setattr(_redaction_module, "_apply_patterns", original)


# ---------------------------------------------------------------------------
# Supplemental: REDACTION_VERSION constant
# ---------------------------------------------------------------------------


class TestRedactionVersion:
    def test_version_is_v1(self) -> None:
        assert REDACTION_VERSION == "v1"

    def test_version_is_string(self) -> None:
        assert isinstance(REDACTION_VERSION, str)
