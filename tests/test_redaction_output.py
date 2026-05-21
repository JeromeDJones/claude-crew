"""Tests for redact_output (click-to-view-tool-output, AT-2).

Covers:
  - Happy path: plain text with no secrets is returned unchanged.
  - Every secret shape called out in AT-2 is individually redacted.
  - Surrounding context ("BEFORE" / "AFTER") survives redaction.
  - Regression: bare ``sk-...`` (no ant-/proj- prefix) is covered by V1
    pattern 7.
  - Output cap at 4096 bytes.
"""
from __future__ import annotations

import pytest

from claude_crew.redaction import redact_output


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestRedactOutputHappyPath:
    def test_plain_text_unchanged(self) -> None:
        text = "Normal tool output with no secrets."
        assert redact_output(text) == text

    def test_empty_string(self) -> None:
        assert redact_output("") == ""

    def test_short_text_not_capped(self) -> None:
        text = "hello world"
        assert redact_output(text) == text


# ---------------------------------------------------------------------------
# AT-2: each secret shape redacted; context preserved
# ---------------------------------------------------------------------------


class TestRedactOutputSecretShapes:
    """Each secret shape is masked; surrounding text ('BEFORE'/'AFTER') survives."""

    def test_aws_access_key_id(self) -> None:
        text = "BEFORE AKIAIOSFODNN7EXAMPLE AFTER"
        result = redact_output(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "BEFORE" in result
        assert "AFTER" in result

    def test_github_pat(self) -> None:
        # ghp_ + 36 alphanumeric chars — matches V1 pattern 8
        pat = "ghp_" + "a" * 36
        text = f"BEFORE {pat} AFTER"
        result = redact_output(text)
        assert pat not in result
        assert "BEFORE" in result
        assert "AFTER" in result

    def test_anthropic_api_key(self) -> None:
        key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
        text = f"BEFORE {key} AFTER"
        result = redact_output(text)
        assert key not in result
        assert "BEFORE" in result
        assert "AFTER" in result

    def test_bare_sk_key_regression(self) -> None:
        """Regression guard: bare sk-... (no ant-/proj- prefix) matches V1 pattern 7.

        Pattern 7 is ``sk-(?:ant-|proj-)?[A-Za-z0-9_\\-]{20,}`` — the ``?``
        on the prefix group makes it optional, so plain ``sk-`` keys match.
        """
        key = "sk-abcdefghijklmnopqrstuvwxyz01234567"
        text = f"BEFORE {key} AFTER"
        result = redact_output(text)
        assert key not in result
        assert "BEFORE" in result
        assert "AFTER" in result

    def test_jwt(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abc-def_ghi123456"
        text = f"BEFORE {jwt} AFTER"
        result = redact_output(text)
        assert jwt not in result
        assert "BEFORE" in result
        assert "AFTER" in result

    # --- sentinel security-pass gaps (output-only patterns O-3/O-4) ---
    # V1 pattern 8 covers gh[poasu]_ only; the length fallback (pattern 12)
    # cannot rescue gh*_ / sk_ tokens because the underscore defeats its \b
    # anchor. These regression tests pin the output-only patterns that close
    # the credential-leak gaps the sentinel found (2026-05-20).

    def test_github_refresh_token_ghr(self) -> None:
        key = "ghr_" + "a" * 36
        result = redact_output(f"BEFORE {key} AFTER")
        assert key not in result
        assert "BEFORE" in result and "AFTER" in result

    def test_github_enterprise_token_ghe(self) -> None:
        key = "ghe_" + "b" * 36
        result = redact_output(f"BEFORE {key} AFTER")
        assert key not in result
        assert "BEFORE" in result and "AFTER" in result

    def test_stripe_live_secret_key(self) -> None:
        key = "sk_live_" + "c" * 24
        result = redact_output(f"BEFORE {key} AFTER")
        assert key not in result
        assert "BEFORE" in result and "AFTER" in result

    def test_stripe_test_secret_key(self) -> None:
        key = "sk_test_" + "d" * 24
        result = redact_output(f"BEFORE {key} AFTER")
        assert key not in result
        assert "BEFORE" in result and "AFTER" in result

    def test_stripe_restricted_key(self) -> None:
        key = "rk_live_" + "e" * 24
        result = redact_output(f"BEFORE {key} AFTER")
        assert key not in result
        assert "BEFORE" in result and "AFTER" in result

    def test_pem_rsa_private_key_block(self) -> None:
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA1234567890abcdef\n"
            "-----END RSA PRIVATE KEY-----"
        )
        text = f"BEFORE\n{pem}\nAFTER"
        result = redact_output(text)
        assert "BEGIN RSA PRIVATE KEY" not in result
        assert "MIIEowIBAAKCAQEA" not in result
        assert "BEFORE" in result
        assert "AFTER" in result

    def test_aws_session_token_keyword_pair(self) -> None:
        """AWS session token keyword=value is redacted (output-only O-2 pattern)."""
        # 40-char alphanumeric value; \b anchors fire around it so either
        # V1 pattern 12 or the output-only O-2 pattern will catch it.
        token_value = "A" * 40
        text = f"[credentials]\naws_session_token={token_value}\nother=setting"
        result = redact_output(text)
        assert token_value not in result
        assert "[credentials]" in result
        assert "other=setting" in result

    def test_all_secrets_combined(self) -> None:
        """AT-2: all shapes in one response; BEFORE/AFTER survive redaction."""
        aws_key = "AKIAIOSFODNN7EXAMPLE"
        github_pat = "ghp_" + "a" * 36
        sk_key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abc-def_ghi123456"
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA1234567890abcdef\n"
            "-----END RSA PRIVATE KEY-----"
        )

        text = f"BEFORE {aws_key} {github_pat} {sk_key} {jwt}\n{pem}\nAFTER"
        result = redact_output(text)

        # Every secret must be gone
        assert aws_key not in result
        assert github_pat not in result
        assert sk_key not in result
        assert jwt not in result
        assert "BEGIN RSA PRIVATE KEY" not in result

        # Context must be preserved
        assert "BEFORE" in result
        assert "AFTER" in result


# ---------------------------------------------------------------------------
# Cap behaviour
# ---------------------------------------------------------------------------


class TestRedactOutputCap:
    def test_capped_at_4096_bytes(self) -> None:
        long_text = "x" * 8192
        result = redact_output(long_text)
        assert len(result.encode("utf-8")) <= 4096

    def test_cap_preserves_valid_utf8(self) -> None:
        # Multi-byte characters: each '£' is 2 bytes in UTF-8.
        # 3000 × '£' = 6000 bytes → must cap to ≤ 4096 bytes.
        long_text = "£" * 3000
        result = redact_output(long_text)
        encoded = result.encode("utf-8")
        assert len(encoded) <= 4096
        # Confirm the truncated result decodes cleanly (no partial multi-byte)
        assert encoded.decode("utf-8") == result
