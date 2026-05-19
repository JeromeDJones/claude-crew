"""Playwright headless-Chromium tests for the roster + spotlight layout.

The roster + spotlight dashboard layout (#22 followup):
- Left rail renders one ``.roster-row`` per live agent and pins the
  Topology widget at the bottom.
- Right pane (spotlight) renders detail columns ONLY for the agents the
  operator has selected. Default selection: all live agents.
- Terminated section + Startup Notices moved from right pane into the
  left rail (below the roster).

These tests drive the dashboard by monkey-patching ``Broker.snapshot``
to return a synthetic ``BrokerSnapshot`` so we can plant a precise
agent fixture without booting real teammates.

Run prerequisite: ``uv run playwright install chromium``.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import Any

import httpx
import pytest
import uvicorn

from claude_crew.broker import Broker, BrokerSnapshot, LiveTeammateInfo, TeammateInfo
from claude_crew.ui_server import UIServer


# ── helpers ──────────────────────────────────────────────────────────────────


def _alive_info(idx: int, role: str = "builder", name: str | None = None) -> TeammateInfo:
    return TeammateInfo(
        id=f"t-{idx}",
        name=name if name is not None else f"agent-{idx}",
        role=role,
        spawned_at=time.time() - 60,
        alive=True,
    )


def _live_entry(info: TeammateInfo, *, cost: float = 0.10) -> LiveTeammateInfo:
    return LiveTeammateInfo(
        info=info,
        status={
            "current_tool_count": 0,
            "current_turn_started_at_wallclock": None,
            "total_input_tokens": 200,
            "total_output_tokens": 100,
            "total_cost_usd": cost,
            "current_tools": [],
            "current_tool": None,
            "last_activity_at_wallclock": None,
        },
        model="claude-sonnet-4-6",
    )


def _make_snapshot(n_alive: int, n_dead: int = 0) -> BrokerSnapshot:
    infos: list[TeammateInfo] = []
    lives: list[LiveTeammateInfo] = []
    dead_configs: dict[str, dict[str, Any]] = {}
    for i in range(n_alive):
        info = _alive_info(i)
        infos.append(info)
        lives.append(_live_entry(info))
    for j in range(n_dead):
        dead_id = f"d-{j}"
        infos.append(TeammateInfo(
            id=dead_id, name=f"dead-{j}", role="reviewer",
            spawned_at=time.time() - 600, alive=False,
            total_cost_usd_at_death=0.0,
            total_input_tokens_at_death=0,
            total_output_tokens_at_death=0,
        ))
        # Dead agents only surface as terminated rows when a config snapshot
        # was retained at death. Plant a minimal one so TerminatedSection renders.
        dead_configs[dead_id] = {
            "tools": (), "extras": {}, "skills": (),
            "permission_mode": "default", "mcp_servers": (),
        }
    return BrokerSnapshot(
        crew_id="crew-test",
        teammates=tuple(infos),
        live=tuple(lives),
        log=(),
        dead_configs=dead_configs,
    )


def _patched_broker(snapshot: BrokerSnapshot) -> Broker:
    """Return a Broker whose .snapshot() always returns the given fixture."""
    b = Broker()
    b.snapshot = lambda log_limit=None: snapshot  # type: ignore[method-assign]
    return b


def _start_server(broker: Broker):
    """Spin up a UIServer on a free port; return (url, server, thread)."""
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
            resp = httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5)
            if resp.status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        pytest.fail("UIServer did not start within 10 seconds")

    return f"http://127.0.0.1:{port}", server, t


def _spin(broker: Broker):
    """Context-manager-ish helper: starts a server and yields the url; tears down."""
    url, server, t = _start_server(broker)
    return url, server, t


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def five_agent_url():
    broker = _patched_broker(_make_snapshot(n_alive=5))
    url, server, t = _spin(broker)
    yield url
    server.should_exit = True
    t.join(timeout=3)


@pytest.fixture
def two_with_one_dead_url():
    broker = _patched_broker(_make_snapshot(n_alive=2, n_dead=1))
    url, server, t = _spin(broker)
    yield url
    server.should_exit = True
    t.join(timeout=3)


@pytest.fixture
def zero_agent_url():
    broker = _patched_broker(_make_snapshot(n_alive=0))
    url, server, t = _spin(broker)
    yield url
    server.should_exit = True
    t.join(timeout=3)


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.dashboard
def test_roster_renders_one_row_per_live_agent(five_agent_url, page):
    """Impl-layer happy path: N live agents → N roster rows in the left rail."""
    page.goto(five_agent_url)
    page.locator(".roster-row").first.wait_for(state="visible", timeout=15000)
    assert page.locator(".roster-row").count() == 5

    # Each row exposes the agent name.
    names = page.locator(".roster-row .roster-row-name").all_inner_texts()
    assert sorted(names) == [f"agent-{i}" for i in range(5)]


@pytest.mark.dashboard
def test_default_all_renders_every_spotlight_column(five_agent_url, page):
    """Integration-layer happy path: on load every live agent is spotlighted.

    The spotlight pane contains one ``AgentStreamColumn`` per agent, each
    keyed by ``agent.id`` showing avatar + name. With default-all selection
    we expect 5 columns rendered alongside the 5 roster rows.
    """
    page.goto(five_agent_url)
    page.locator(".roster-row").first.wait_for(state="visible", timeout=15000)
    # AgentStreamColumn renders the agent.id inside a mono span; count
    # those occurrences inside the spotlight pane via a stable proxy: the
    # tool-chip-row exists exactly once per spotlight column.
    assert page.locator(".tool-chip-row").count() == 5


@pytest.mark.dashboard
def test_clicking_roster_row_removes_agent_from_spotlight(five_agent_url, page):
    """Integration-layer toggle: clicking a roster row hides that column."""
    page.goto(five_agent_url)
    page.locator(".roster-row").first.wait_for(state="visible", timeout=15000)
    assert page.locator(".tool-chip-row").count() == 5

    # Toggle the first roster row off.
    page.locator(".roster-row").first.click()
    # One column should disappear; the row should now read .unselected.
    page.locator(".roster-row.unselected").first.wait_for(state="visible", timeout=3000)
    assert page.locator(".tool-chip-row").count() == 4
    assert page.locator(".roster-row").count() == 5  # roster unchanged


@pytest.mark.dashboard
def test_clear_then_empty_state(five_agent_url, page):
    """Sad path: clearing every selection shows the spotlight-empty placeholder."""
    page.goto(five_agent_url)
    page.locator(".roster-row").first.wait_for(state="visible", timeout=15000)
    # Click "Clear" — second action button in the section header.
    page.locator(".roster-section-action", has_text="Clear").click()
    page.locator(".spotlight-empty").wait_for(state="visible", timeout=3000)
    assert page.locator(".tool-chip-row").count() == 0
    # Roster still shows all 5 rows, all .unselected.
    assert page.locator(".roster-row").count() == 5
    assert page.locator(".roster-row.unselected").count() == 5


@pytest.mark.dashboard
def test_all_button_restores_default_all(five_agent_url, page):
    """Clicking All after Clear puts every agent back into the spotlight."""
    page.goto(five_agent_url)
    page.locator(".roster-row").first.wait_for(state="visible", timeout=15000)
    page.locator(".roster-section-action", has_text="Clear").click()
    page.locator(".spotlight-empty").wait_for(state="visible", timeout=3000)
    page.locator(".roster-section-action", has_text="All").click()
    # Empty state should disappear, spotlight columns return.
    page.locator(".tool-chip-row").first.wait_for(state="visible", timeout=3000)
    assert page.locator(".tool-chip-row").count() == 5
    assert page.locator(".roster-row.selected").count() == 5


@pytest.mark.dashboard
def test_zero_live_agents_renders_safely(zero_agent_url, page):
    """Sad path: zero live agents — no roster rows, no crash, roster shows empty msg."""
    page.goto(zero_agent_url)
    page.locator(".roster-empty").wait_for(state="visible", timeout=15000)
    assert page.locator(".roster-row").count() == 0
    # Spotlight pane shows the "No teammates spawned yet." placeholder
    # from StreamColumns (zero spotlighted agents AND zero live agents).
    assert page.locator(".spotlight-empty").count() == 0


@pytest.mark.dashboard
def test_topology_pinned_in_left_rail(five_agent_url, page):
    """Roster + spotlight: the Topology widget renders inside ``.rail-topology``."""
    page.goto(five_agent_url)
    page.locator(".rail-topology").wait_for(state="visible", timeout=15000)
    # The MiniGraph renders an SVG with the "Topology" label inside.
    # CSS text-transform: uppercase may surface this as "TOPOLOGY" in inner_text.
    rail_text = page.locator(".rail-topology").inner_text()
    assert "TOPOLOGY" in rail_text.upper(), (
        f"Expected Topology label in rail; got: {rail_text!r}"
    )
    # SVG present.
    assert page.locator(".rail-topology svg").count() == 1


@pytest.mark.dashboard
def test_terminated_section_relocated_to_left_rail(two_with_one_dead_url, page):
    """Roster + spotlight: TerminatedSection now lives in the rail, not the right pane.

    The rail container has a fixed structure; the right pane is now exclusively
    the spotlight. Confirm the terminated section's nearest ancestor is the
    roster rail (we identify the rail by its sibling ``.roster-section``).
    """
    page.goto(two_with_one_dead_url)
    page.locator(".roster-row").first.wait_for(state="visible", timeout=15000)
    section = page.locator(".terminated-section")
    section.wait_for(state="visible", timeout=3000)
    # The terminated section's parent should also contain the roster-section.
    sibling_roster_count = section.locator(
        "xpath=../*[contains(@class, 'roster-section')]"
    ).count()
    assert sibling_roster_count == 1, (
        "terminated section should live in the same parent as the roster-section"
    )
