from claude_crew.ctx_window import resolve_ctx_window

def test_anthropic_strategy():
    # anthropic: is_local=False, peak_in=50000, local_metrics=None
    # -> {source:"anthropic", used:50000, limit:200000, pct:25.0}; assert no "cache_hit_pct" key.
    res = resolve_ctx_window(is_local=False, peak_in=50000, local_metrics=None)
    assert res == {"source": "anthropic", "used": 50000, "limit": 200000, "pct": 25.0}
    assert "cache_hit_pct" not in res

def test_local_happy():
    # local happy: is_local=True, local_metrics={"ctx_used":25688,"n_ctx":80128,"cache_hit_pct":94.2}
    # -> source "local", used 25688, limit 80128, pct 32.1, cache_hit_pct 94.2.
    res = resolve_ctx_window(
        is_local=True,
        peak_in=None,
        local_metrics={"ctx_used": 25688, "n_ctx": 80128, "cache_hit_pct": 94.2}
    )
    assert res == {
        "source": "local",
        "used": 25688,
        "limit": 80128,
        "pct": 32.1,
        "cache_hit_pct": 94.2
    }

def test_fallback_to_anthropic():
    # fallback: is_local=True, peak_in=1234, local_metrics=None
    # -> source "anthropic", used 1234, limit 200000.
    res = resolve_ctx_window(is_local=True, peak_in=1234, local_metrics=None)
    assert res == {"source": "anthropic", "used": 1234, "limit": 200000, "pct": 0.6} # 1234/200000 = 0.00617 -> 0.6%

def test_div_by_zero():
    # div-by-zero: is_local=True, local_metrics={"ctx_used":10,"n_ctx":0} -> pct 0.0 (no raise).
    res = resolve_ctx_window(
        is_local=True,
        peak_in=None,
        local_metrics={"ctx_used": 10, "n_ctx": 0}
    )
    assert res["pct"] == 0.0

def test_missing_keys():
    # missing keys: is_local=True, local_metrics={} -> used 0, limit 0, pct 0.0, cache_hit_pct 0.0.
    res = resolve_ctx_window(is_local=True, peak_in=None, local_metrics={})
    assert res == {
        "source": "local",
        "used": 0,
        "limit": 0,
        "pct": 0.0,
        "cache_hit_pct": 0.0
    }

def test_peak_in_none():
    # peak_in None: is_local=False, peak_in=None, local_metrics=None -> used 0, pct 0.0.
    res = resolve_ctx_window(is_local=False, peak_in=None, local_metrics=None)
    assert res == {"source": "anthropic", "used": 0, "limit": 200000, "pct": 0.0}
