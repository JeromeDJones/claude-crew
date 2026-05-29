"""Tests for claude_crew/backend_routing.py — bedrock_env + custom_endpoint_env presets."""

import pytest

from claude_crew.backend_routing import bedrock_env, custom_endpoint_env


class TestCustomEndpointEnv:
    """custom_endpoint_env() returns the three-var Anthropic-shape preset."""

    def test_default_api_key(self) -> None:
        """base_url required; api_key defaults to placeholder."""
        result = custom_endpoint_env("http://127.0.0.1:3456")
        assert result == {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:3456",
            "ANTHROPIC_API_KEY": "sk-custom-no-key-required",
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
        }

    def test_explicit_api_key(self) -> None:
        """api_key kwarg flows through verbatim."""
        result = custom_endpoint_env(
            "http://gateway.example:9999",
            api_key="sk-real-gateway-key",
        )
        assert result == {
            "ANTHROPIC_BASE_URL": "http://gateway.example:9999",
            "ANTHROPIC_API_KEY": "sk-real-gateway-key",
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
        }

    def test_attribution_header_always_suppressed(self) -> None:
        """CLAUDE_CODE_ATTRIBUTION_HEADER=0 regardless of other args."""
        result = custom_endpoint_env("http://x:1", api_key="sk-y")
        assert result["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0"

    def test_base_url_is_required(self) -> None:
        """custom_endpoint_env() with no base_url raises TypeError."""
        with pytest.raises(TypeError):
            custom_endpoint_env()  # type: ignore[call-arg]

    def test_returns_three_key_dict(self) -> None:
        """Return type is a plain dict with exactly three keys."""
        result = custom_endpoint_env("http://x:1")
        assert isinstance(result, dict)
        assert len(result) == 3


class TestBedrockEnv:
    """bedrock_env() returns the Bedrock-routing env preset."""

    def test_default_no_region(self) -> None:
        """No aws_region → only CLAUDE_CODE_USE_BEDROCK is set."""
        result = bedrock_env()
        assert result == {"CLAUDE_CODE_USE_BEDROCK": "1"}

    def test_with_region(self) -> None:
        """aws_region kwarg adds AWS_REGION alongside the SDK switch."""
        result = bedrock_env(aws_region="us-east-1")
        assert result == {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": "us-east-1",
        }

    def test_does_not_inject_credentials(self) -> None:
        """Bedrock credentials are picked up from the env / instance role —
        bedrock_env() never sets AWS_ACCESS_KEY_ID or similar."""
        result = bedrock_env(aws_region="eu-west-2")
        forbidden_keys = {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_PROFILE",
        }
        assert forbidden_keys.isdisjoint(result.keys())

    def test_returns_dict(self) -> None:
        result = bedrock_env()
        assert isinstance(result, dict)
