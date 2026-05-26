"""Playwright: ContextWindowBar renders the right strategy per teammate.

A local-backed teammate's bar must reflect the local /slots strategy (its real
window + cache-hit), while an Anthropic teammate's bar reflects the Anthropic
peak-invocation strategy. Both flow through the same agent.ctx_window sink; the
bar is source-agnostic. We assert via the bar's title (tooltip), which names the
strategy.

Drives the dashboard by monkey-patching Broker.snapshot to a synthetic fixture
and (for the local case) faking the /slots probe.

Run prerequisite: ``uv run playwright install chromium``.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from claude_crew.broker import Broker, BrokerSnapshot, LiveTeammateInfo, TeammateInfo
from claude_crew.ui_server import UIServer

_LOCAL_METRICS = {"ctx_used": 25688, "n_ctx": 80128, "cache_hit_pct": 94.2}


def _snapshot(is_local: bool) -> BrokerSnapshot:
    info = TeammateInfo(
        id="t-1", name="worker", role="builder", spawned_at=time.time() - 60, alive=True
    )
    live = LiveTeammateInfo(
        info=info,
        status={
            "current_tool_count": 0,
            "current_turn_started_at_wallclock": None,
            "total_input_tokens": 200,
            "total_output_tokens": 100,
            "total_cost_usd": 0.0,
            "current_tools": [],
            "current_tool": None,
            "last_activity_at_wallclock": None,
            "last_turn_peak_invocation_input_tokens": 50000,
        },
        model="claude-sonnet-4-6",
        is_local=is_local,
    )
    return BrokerSnapshot(crew_id="crew-test", teammates=(info,), live=(live,), log=())


def _patched_broker(snapshot: BrokerSnapshot) -> Broker:
    b = Broker()
    b.snapshot = lambda log_limit=None: snapshot  # type: ignore[method-assign]
    return b


def _start_server(broker: Broker):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    ui = UIServer(broker, port=port)
    app = ui._make_app()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", lifespan="off"
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())

    t = threading.Thread(target=run, daemon=True)
    t.start()

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        pytest.fail("UIServer did not start within 10 seconds")

    return f"http://127.0.0.1:{port}", server, t


@pytest.mark.dashboard
def test_local_agent_bar_uses_local_strategy(monkeypatch, page):
    async def fake(url, *, client=None, timeout=2.0):
        return dict(_LOCAL_METRICS)

    monkeypatch.setenv("CLAUDE_CREW_LOCAL_MODEL_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("CLAUDE_CREW_LOCAL_MODEL_PROBE", "1")
    monkeypatch.setattr("claude_crew.ui_server.fetch_local_slot_metrics", fake)

    url, server, t = _start_server(_patched_broker(_snapshot(is_local=True)))
    try:
        page.goto(url)
        bar = page.locator(".ctx-window-bar").first
        bar.wait_for(state="visible", timeout=15000)
        title = bar.get_attribute("title") or ""
        assert "local model context" in title, title
        assert "cache hit" in title, title
        # Local window (80,128), not the Anthropic 200k.
        assert "80,128" in title, title
    finally:
        server.should_exit = True
        t.join(timeout=3)


@pytest.mark.dashboard
def test_anthropic_agent_bar_uses_anthropic_strategy(monkeypatch, page):
    monkeypatch.delenv("CLAUDE_CREW_LOCAL_MODEL_URL", raising=False)

    url, server, t = _start_server(_patched_broker(_snapshot(is_local=False)))
    try:
        page.goto(url)
        bar = page.locator(".ctx-window-bar").first
        bar.wait_for(state="visible", timeout=15000)
        title = bar.get_attribute("title") or ""
        assert "peak invocation" in title, title
    finally:
        server.should_exit = True
        t.join(timeout=3)
