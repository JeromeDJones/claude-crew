ANTHROPIC_CONTEXT_LIMIT = 200_000


def resolve_ctx_window(*, peak_in) -> dict:
    """Resolve the canonical context-window shape for a teammate.

    Uses the peak-invocation input-token count against the Anthropic
    200k context limit. Returns a source-tagged dict the dashboard
    renders without knowing which strategy produced the numbers.
    """
    used = int(peak_in or 0)
    limit = ANTHROPIC_CONTEXT_LIMIT
    if limit <= 0:
        pct = 0.0
    else:
        pct = round(100 * used / limit, 1)
    return {
        "source": "anthropic",
        "used": used,
        "limit": limit,
        "pct": pct,
    }
