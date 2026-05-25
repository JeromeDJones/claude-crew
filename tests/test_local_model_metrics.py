import httpx
from claude_crew.local_model_metrics import parse_slot_metrics, fetch_local_slot_metrics

# --- parse_slot_metrics tests ---

def test_parse_slot_metrics_happy():
    payload = [{
        "id": 0,
        "id_task": 123,
        "is_processing": True,
        "n_ctx": 80128,
        "n_prompt_tokens": 25656,
        "n_prompt_tokens_cache": 24160,
        "n_prompt_tokens_processed": 1496,
        "next_token": [{"has_next_token": True, "n_remain": 8160, "n_decoded": 32}]
    }]
    result = parse_slot_metrics(payload)
    assert result is not None
    assert result["n_ctx"] == 80128
    assert result["ctx_used"] == 25656 + 32 # 25688
    assert result["ctx_pct"] == round(100.0 * 25688 / 80128, 1) # 32.1
    assert result["prompt_tokens"] == 25656
    assert result["cache_tokens"] == 24160
    assert result["processed_tokens"] == 1496
    assert result["cache_hit_pct"] == round(100.0 * 24160 / 25656, 1) # ~94.2
    assert result["is_processing"] is True

def test_parse_slot_metrics_n_ctx_zero():
    payload = [{
        "n_ctx": 0,
        "n_prompt_tokens": 100,
        "n_prompt_tokens_cache": 50,
        "next_token": [{"n_decoded": 10}]
    }]
    result = parse_slot_metrics(payload)
    assert result["ctx_pct"] == 0.0
    assert result["cache_hit_pct"] == 50.0

def test_parse_slot_metrics_prompt_tokens_zero():
    payload = [{
        "n_ctx": 1000,
        "n_prompt_tokens": 0,
        "n_prompt_tokens_cache": 0,
        "next_token": [{"n_decoded": 10}]
    }]
    result = parse_slot_metrics(payload)
    assert result["cache_hit_pct"] == 0.0
    assert result["ctx_pct"] == 1.0 # 10/1000 * 100

def test_parse_slot_metrics_missing_next_token():
    payload = [{
        "n_ctx": 1000,
        "n_prompt_tokens": 100,
        "next_token": [] # Empty list
    }]
    result = parse_slot_metrics(payload)
    assert result["ctx_used"] == 100
    assert result["ctx_pct"] == 10.0

def test_parse_slot_metrics_malformed_next_token_element():
    payload = [{
        "n_ctx": 1000,
        "n_prompt_tokens": 100,
        "next_token": [123] # Non-dict element
    }]
    result = parse_slot_metrics(payload)
    assert result["ctx_used"] == 100
    assert result["ctx_pct"] == 10.0

def test_parse_slot_metrics_invalid_inputs():
    assert parse_slot_metrics([]) is None
    assert parse_slot_metrics({}) is None
    assert parse_slot_metrics([123]) is None
    assert parse_slot_metrics(None) is None

# --- fetch_local_slot_metrics tests ---

async def test_fetch_local_slot_metrics_happy():
    payload = [{
        "n_ctx": 1000,
        "n_prompt_tokens": 100,
        "n_prompt_tokens_cache": 50,
        "next_token": [{"n_decoded": 0}]
    }]

    async def handler(request):
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_local_slot_metrics("http://localhost:8080", client=client)
        assert result is not None
        assert result["n_ctx"] == 1000
        assert result["prompt_tokens"] == 100

async def test_fetch_local_slot_metrics_500_error():
    async def handler(request):
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_local_slot_metrics("http://localhost:8080", client=client)
        assert result is None

async def test_fetch_local_slot_metrics_connect_error():
    # To simulate a connect error with MockTransport, we can raise an exception in the handler
    async def handler(request):
        raise httpx.ConnectError("Failed to connect", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_local_slot_metrics("http://localhost:8080", client=client)
        assert result is None

async def test_fetch_local_slot_metrics_bad_json():
    async def handler(request):
        return httpx.Response(200, content=b"not json")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_local_slot_metrics("http://localhost:8080", client=client)
        assert result is None
