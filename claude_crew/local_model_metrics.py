import httpx

def parse_slot_metrics(slots: object) -> dict | None:
    """
    Parses the first slot from the /slots JSON payload and returns normalized metrics.
    """
    if not isinstance(slots, list) or not slots or not isinstance(slots[0], dict):
        return None

    slot = slots[0]

    n_ctx = slot.get("n_ctx", 0)
    n_prompt_tokens = slot.get("n_prompt_tokens", 0)
    n_prompt_tokens_cache = slot.get("n_prompt_tokens_cache", 0)
    n_prompt_tokens_processed = slot.get("n_prompt_tokens_processed", 0)
    is_processing = slot.get("is_processing", False)

    # Calculate n_decoded from next_token
    next_token_list = slot.get("next_token")
    n_decoded = 0
    if isinstance(next_token_list, list) and next_token_list and isinstance(next_token_list[0], dict):
        n_decoded = next_token_list[0].get("n_decoded", 0)

    ctx_used = n_prompt_tokens + n_decoded

    # Handle division by zero traps
    ctx_pct = 0.0
    if n_ctx > 0:
        ctx_pct = round(100.0 * ctx_used / n_ctx, 1)

    cache_hit_pct = 0.0
    if n_prompt_tokens > 0:
        cache_hit_pct = round(100.0 * n_prompt_tokens_cache / n_prompt_tokens, 1)

    return {
        "n_ctx": n_ctx,
        "ctx_used": ctx_used,
        "ctx_pct": ctx_pct,
        "prompt_tokens": n_prompt_tokens,
        "cache_tokens": n_prompt_tokens_cache,
        "processed_tokens": n_prompt_tokens_processed,
        "cache_hit_pct": cache_hit_pct,
        "is_processing": is_processing,
    }


async def fetch_local_slot_metrics(base_url: str, *, client: httpx.AsyncClient | None = None, timeout: float = 2.0) -> dict | None:
    """
    Fetches local model slot metrics from the llama.cpp server.
    """
    url = base_url.rstrip("/") + "/slots"

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url)
        else:
            response = await client.get(url)

        if response.status_code != 200:
            return None

        return parse_slot_metrics(response.json())

    except (httpx.HTTPError, ValueError):
        return None
