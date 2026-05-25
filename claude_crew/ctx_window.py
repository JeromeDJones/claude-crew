ANTHROPIC_CONTEXT_LIMIT = 200_000

def resolve_ctx_window(*, is_local: bool, peak_in, local_metrics: dict | None) -> dict:
    """
    Resolves the context window metrics into a canonical shape.
    """
    # Determine strategy
    is_local_strategy = is_local and local_metrics is not None

    if is_local_strategy:
        used = int(local_metrics.get("ctx_used", 0))
        limit = int(local_metrics.get("n_ctx", 0))
        cache_hit_pct = float(local_metrics.get("cache_hit_pct", 0.0))
        source = "local"
    else:
        used = int(peak_in or 0)
        limit = ANTHROPIC_CONTEXT_LIMIT
        source = "anthropic"
        # cache_hit_pct is not part of the anthropic strategy return shape

    # Compute pct with guard against limit <= 0
    if limit <= 0:
        pct = 0.0
    else:
        pct = round(100 * used / limit, 1)

    result = {
        "source": source,
        "used": used,
        "limit": limit,
        "pct": pct,
    }

    if is_local_strategy:
        result["cache_hit_pct"] = cache_hit_pct

    return result
