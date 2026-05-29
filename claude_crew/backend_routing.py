"""Backend-routing presets for claude-crew SDK teammates.

The Anthropic-Agent-SDK reads a small set of environment variables to decide
which API endpoint to call. claude-crew's per-teammate env injection
(``spawn_teammate(env=…)`` and ``custom_endpoint=…``) is the substrate that
lets each teammate route to a different backend than the lead.

These helpers return the canonical env dicts for two supported cases:

- :func:`bedrock_env` — Amazon Bedrock. The SDK switches to its Bedrock
  provider when ``CLAUDE_CODE_USE_BEDROCK=1`` is set; AWS credentials are
  picked up from the standard places (env vars, ``~/.aws/credentials``,
  IMDS, etc.). Region is optional and falls back to the AWS default-chain.
- :func:`custom_endpoint_env` — any other endpoint that exposes an
  Anthropic-shape Messages API at a custom base URL (translation proxies,
  gateways, self-hosted routers). Backends that produce a different wire
  shape upstream MUST translate to Anthropic-shape responses before the
  SDK sees them — claude-crew's token / cost attribution path assumes
  that shape uniformly (no dual-shape branching).

Both helpers return a plain ``dict[str, str]`` suitable for direct merge
into ``spawn_teammate(env=…)`` or use as the ``custom_endpoint`` preset.
"""


def bedrock_env(*, aws_region: str | None = None) -> dict[str, str]:
    """Return the env dict that routes a teammate to Amazon Bedrock.

    Args:
        aws_region: Optional AWS region (e.g. ``"us-east-1"``). When
            omitted, the SDK / boto3 follow the default region-resolution
            chain (``AWS_REGION`` env, ``~/.aws/config`` profile, IMDS).

    Returns:
        Env dict carrying ``CLAUDE_CODE_USE_BEDROCK=1`` and, when
        ``aws_region`` is given, ``AWS_REGION=<region>``. AWS credentials
        themselves are NOT injected — they must already be available to
        the teammate subprocess (inherited env, instance role, etc.).
    """
    env: dict[str, str] = {"CLAUDE_CODE_USE_BEDROCK": "1"}
    if aws_region is not None:
        env["AWS_REGION"] = aws_region
    return env


def custom_endpoint_env(
    base_url: str,
    *,
    api_key: str = "sk-custom-no-key-required",
) -> dict[str, str]:
    """Return the env dict that routes a teammate to a custom Anthropic-shape endpoint.

    Use this for any backend reachable at a custom base URL that exposes
    the Anthropic Messages API contract (or a translation proxy that
    does). The teammate's attribution-header is suppressed so the
    upstream gateway can attribute traffic on its own terms.

    Args:
        base_url: The full base URL of the endpoint (e.g. a proxy or
            gateway, ``http://127.0.0.1:3456``). Required — there is no
            sensible default for a "custom" endpoint.
        api_key: API key value sent as ``ANTHROPIC_API_KEY``. Defaults
            to a placeholder for backends that don't validate it
            (self-hosted routers); set explicitly for real gateways.

    Returns:
        Three-var env dict: ``ANTHROPIC_BASE_URL``, ``ANTHROPIC_API_KEY``,
        and ``CLAUDE_CODE_ATTRIBUTION_HEADER=0``.
    """
    return {
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_API_KEY": api_key,
        "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
    }
