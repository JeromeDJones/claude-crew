from claude_crew.ctx_window import resolve_ctx_window


def test_anthropic_strategy():
    res = resolve_ctx_window(peak_in=50000)
    assert res == {"source": "anthropic", "used": 50000, "limit": 200000, "pct": 25.0}
    assert "cache_hit_pct" not in res


def test_peak_in_none_yields_zero():
    res = resolve_ctx_window(peak_in=None)
    assert res == {"source": "anthropic", "used": 0, "limit": 200000, "pct": 0.0}


def test_peak_in_zero():
    res = resolve_ctx_window(peak_in=0)
    assert res == {"source": "anthropic", "used": 0, "limit": 200000, "pct": 0.0}


def test_peak_in_full_context():
    res = resolve_ctx_window(peak_in=200000)
    assert res == {"source": "anthropic", "used": 200000, "limit": 200000, "pct": 100.0}
