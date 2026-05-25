"""Tests for claude_crew/local_backend.py — AT 10."""

from claude_crew.local_backend import local_backend_env


class TestLocalBackendEnv:
    """AT 10: local_backend_env() returns the correct three-var dict."""

    def test_default_args(self) -> None:
        """Default call returns expected preset values."""
        result = local_backend_env()
        assert result == {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:3456",
            "ANTHROPIC_API_KEY": "sk-local-no-key-required",
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
        }

    def test_custom_args(self) -> None:
        """Custom base_url and api_key are reflected in the returned dict."""
        result = local_backend_env(
            base_url="http://1.2.3.4:9999",
            api_key="sk-custom",
        )
        assert result == {
            "ANTHROPIC_BASE_URL": "http://1.2.3.4:9999",
            "ANTHROPIC_API_KEY": "sk-custom",
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
        }

    def test_attribution_header_always_zero(self) -> None:
        """CLAUDE_CODE_ATTRIBUTION_HEADER is always '0' regardless of other args."""
        result = local_backend_env(
            base_url="http://other:1234",
            api_key="sk-other",
        )
        assert result["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0"

    def test_returns_dict(self) -> None:
        """Return type is a plain dict."""
        result = local_backend_env()
        assert isinstance(result, dict)

    def test_exactly_three_keys(self) -> None:
        """Returned dict has exactly three keys."""
        result = local_backend_env()
        assert len(result) == 3
