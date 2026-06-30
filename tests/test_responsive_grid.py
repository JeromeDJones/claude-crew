"""
Playwright + structural tests for the responsive .dash-grid layout.
Maps to spec acceptance tests AT 16–18 (task: responsive-grid-1024).

AT 16 (Playwright): At viewport width 1024, left rail <= 260px, two-pane preserved
AT 17 (Playwright): At viewport width 1440, left rail > 260px (320px base track)
AT 18 (structural grep): dash-grid + clamp present; old inline JSX grid style removed

Run prerequisite: ``uv run playwright install chromium`` (one-time).
asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.

Fixture design: each Playwright test has a function-scoped URL fixture (one
UIServer per test).  The module-scoped Playwright page/browser come from
tests/conftest.py.  The structural AT 18 needs no browser at all.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from claude_crew.broker import (
    Broker,
    BrokerSnapshot,
    LiveTeammateInfo,
    TeammateInfo,
)
from claude_crew.ui_server import UIServer

# ── Constants ────────────────────────────────────────────────────────────────

DASHBOARD_HTML = (
    Path(__file__).parent.parent / "claude_crew" / "ui" / "dashboard.html"
)

# ── Private helpers ──────────────────────────────────────────────────────────


def _alive_info(idx: int, role: str = "builder") -> TeammateInfo:
    return TeammateInfo(
        id=f"tid-{idx}",
        name=f"agent-{idx}",
        role=role,
        spawned_at=time.time() - 60,
        alive=True,
    )


def _live_entry(info: TeammateInfo) -> LiveTeammateInfo:
    status: dict[str, Any] = {
        "current_tool_count": 0,
        "current_turn_started_at_wallclock": None,
        "total_input_tokens": 100,
        "total_output_tokens": 50,
        "total_cost_usd": 0.05,
        "current_tools": [],
        "current_tool": None,
        "last_activity_at_wallclock": None,
    }
    return LiveTeammateInfo(info=info, status=status, model="claude-sonnet-4-6")


def _stub_snapshot(
    *,
    crew_id: str = "crew-responsive-test",
    live: tuple[LiveTeammateInfo, ...],
) -> BrokerSnapshot:
    infos = tuple(le.info for le in live)
    return BrokerSnapshot(
        crew_id=crew_id,
        teammates=infos,
        live=live,
        log=(),
        topology_edge_stats=(),
        topology_slot_to_teammate={},
    )


def _patched_broker(snapshot: BrokerSnapshot) -> Broker:
    b = Broker()
    b.snapshot = lambda log_limit=None: snapshot  # type: ignore[method-assign]
    b.crew_id = snapshot.crew_id  # type: ignore[misc]
    return b


def _spin_dashboard(broker: Broker):
    """Start UIServer on a free port in a daemon thread.
    Returns (url, server, thread). Caller must set server.should_exit = True.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    ui = UIServer(broker, port=port)
    app = ui._make_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
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
            resp = httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5)
            if resp.status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        pytest.fail("UIServer did not start within 10 seconds")

    return f"http://127.0.0.1:{port}", server, t


def _make_2_agent_snapshot() -> BrokerSnapshot:
    """Minimal 2-teammate crew sufficient for responsive-grid tests."""
    agents = [_alive_info(i, role=f"role-{i}") for i in range(1, 3)]
    live = tuple(_live_entry(a) for a in agents)
    return _stub_snapshot(crew_id="crew-responsive", live=live)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def simple_dashboard_url():
    """Spin up a minimal dashboard server for responsive-grid Playwright tests."""
    snap = _make_2_agent_snapshot()
    broker = _patched_broker(snap)
    url, server, t = _spin_dashboard(broker)
    yield url
    server.should_exit = True
    t.join(timeout=3)


# ── AT 16: Responsive ≤1024px — rail clamped, two-pane preserved ─────────────


@pytest.mark.dashboard
def test_at16_responsive_1024px_rail_clamped(simple_dashboard_url, page):
    """AT 16: At viewport width 1024, the left rail column computed width is
    <= 260px and the main pane (right column) is still present — two-pane
    is preserved, no drawer, no single-column.
    """
    page.set_viewport_size({"width": 1024, "height": 768})
    page.goto(simple_dashboard_url)
    # Wait for the React app to mount the .dash-grid container
    page.locator(".dash-grid").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(400)

    result = page.evaluate(
        """() => {
          const grid = document.querySelector('.dash-grid');
          if (!grid) return { error: 'no .dash-grid element found — className not applied' };
          const leftRail  = grid.firstElementChild;
          const rightPane = grid.children[1];
          if (!leftRail) return { error: 'no first child in .dash-grid' };
          const leftWidth   = leftRail.getBoundingClientRect().width;
          const rightWidth  = rightPane ? rightPane.getBoundingClientRect().width : 0;
          const rightPresent = rightWidth > 0;
          return { leftWidth, rightPresent, rightWidth };
        }"""
    )

    assert "error" not in result, f"Evaluation error: {result.get('error')}"
    assert result["leftWidth"] <= 260, (
        f"At 1024px viewport, left rail width ({result['leftWidth']:.1f}px) is > 260px — "
        "the @media (max-width:1024px) rule with clamp(220px,22vw,260px) is not active. "
        "Check that the .dash-grid CSS class and @media rule are present in dashboard.html."
    )
    assert result["rightPresent"], (
        f"At 1024px viewport, the main pane (right column) is absent or zero-width "
        f"(rightWidth={result.get('rightWidth', '?'):.1f}px) — "
        "two-pane layout must be preserved (no drawer, no single-column collapse)."
    )


# ── AT 17: Responsive ≥1440px — left rail wider (base 320px track) ───────────


@pytest.mark.dashboard
def test_at17_responsive_1440px_rail_wider(simple_dashboard_url, page):
    """AT 17: At viewport width 1440, the left rail column computed width is
    > 260px (the default 320px base track applies; the clamp is not active).
    """
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(simple_dashboard_url)
    page.locator(".dash-grid").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(400)

    result = page.evaluate(
        """() => {
          const grid = document.querySelector('.dash-grid');
          if (!grid) return { error: 'no .dash-grid element found — className not applied' };
          const leftRail = grid.firstElementChild;
          if (!leftRail) return { error: 'no first child in .dash-grid' };
          const leftWidth = leftRail.getBoundingClientRect().width;
          return { leftWidth };
        }"""
    )

    assert "error" not in result, f"Evaluation error: {result.get('error')}"
    assert result["leftWidth"] > 260, (
        f"At 1440px viewport, left rail width ({result['leftWidth']:.1f}px) is <= 260px — "
        "the narrow-rail clamp must NOT apply at 1440px (expected 320px base track from "
        ".dash-grid base CSS). Check the @media (max-width:1024px) rule is not bleeding over."
    )


# ── AT 18: Structural deletion-detector ──────────────────────────────────────


@pytest.mark.dashboard
def test_at18_structural_dash_grid_clamp_present_inline_removed():
    """AT 18: dashboard.html contains the named literals 'dash-grid' and
    'clamp(220px, 22vw, 260px)', AND no longer contains the old hardcoded
    inline JSX gridTemplateColumns style string.
    """
    text = DASHBOARD_HTML.read_text(encoding="utf-8")

    assert "dash-grid" in text, (
        "dashboard.html does not contain 'dash-grid' — "
        "the .dash-grid CSS class was not added or the className prop was not applied "
        "to the MissionControlLayout two-pane container"
    )
    assert "clamp(220px, 22vw, 260px)" in text, (
        "dashboard.html does not contain 'clamp(220px, 22vw, 260px)' — "
        "the responsive @media (max-width:1024px) clamp rule is missing from the "
        ".dash-grid CSS declaration"
    )
    assert 'gridTemplateColumns: "320px minmax(0, 1fr)"' not in text, (
        "dashboard.html still contains the old hardcoded inline JSX grid style "
        "'gridTemplateColumns: \"320px minmax(0, 1fr)\"' — "
        "this inline style must be replaced by className='dash-grid' on the container div"
    )
