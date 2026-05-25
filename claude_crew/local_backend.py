"""
Local backend preset helper for claude-crew.

Provides the canonical env-var dict for routing an SDK teammate through a
local model backend (e.g. claude-code-router / ccr / llama.cpp).
"""


def local_backend_env(
    base_url: str = "http://127.0.0.1:3456",
    api_key: str = "sk-local-no-key-required",
) -> dict[str, str]:
    """Return the three-var env dict that routes a teammate to a local backend.

    Args:
        base_url: The base URL of the local backend (default: ccr default).
        api_key: The API key to use (local backends typically accept any value).

    Returns:
        Dict with ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY, and
        CLAUDE_CODE_ATTRIBUTION_HEADER set to "0".
    """
    return {
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_API_KEY": api_key,
        "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
    }
