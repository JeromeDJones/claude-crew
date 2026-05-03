"""Playwright headless-Chromium tests for the mission-control dashboard.

AT7: verifies that config chips render correctly in the DOM and the detail
panel infrastructure is in place for opening/closing.

Run prerequisite: uv run playwright install chromium
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn

from claude_crew.broker import Broker, TeammateInfo
from claude_crew.teammate import StubTeammate
from claude_crew.ui_server import UIServer


# ── fixtures ──────────────────────────────────────────────────────────────────

KNOWN_CONFIG = {
    "tools": ["Bash", "Read"],
    "skills": ["sdd-workflow"],
    "permission_mode": "bypassPermissions",
    "disallowed_tools": ["WebFetch"],
    "mcp_servers": ["github"],
    "system_prompt": "You are a builder agent for testing.",
    "model": "claude-sonnet-4-6",
    "effort": "medium",
}


@pytest.fixture(scope="module")
def configured_broker():
    """Broker with one pre-configured teammate (manually registered, no async)."""
    broker = Broker()
    tid = f"t-{uuid.uuid4().hex[:12]}"

    # Create teammate and register directly in broker state
    tm = StubTeammate(id=tid, name="builder", role="builder")

    # Register directly in broker state
    broker._teammates[tid] = tm
    broker._inboxes[tid] = asyncio.Queue()
    broker._info[tid] = TeammateInfo(
        id=tid,
        name="builder",
        role="builder",
        spawned_at=time.time(),
        alive=True,
    )
    broker._configs[tid] = KNOWN_CONFIG

    yield broker, tid


@pytest.fixture(scope="module")
def live_server_url(configured_broker):
    """Start UIServer in a daemon thread; yield base URL."""
    broker, _ = configured_broker

    # Find a free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    ui = UIServer(broker, port=port)
    app = ui._make_app()

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", lifespan="off"
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # don't capture signals in thread

    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())

    t = threading.Thread(target=run, daemon=True)
    t.start()

    # Poll until the server accepts connections (max 10s)
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

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    t.join(timeout=3)


DEAD_CONFIG = {
    "tools": ["Bash"],
    "skills": ["sdd-workflow"],
    "permission_mode": "bypassPermissions",
    "disallowed_tools": [],
    "mcp_servers": ["github"],
    "system_prompt": "You are a dead builder agent for testing.",
    "model": "claude-sonnet-4-6",
    "effort": "medium",
}

EXTRAS_CONFIG = {
    "tools": ["Read", "Grep", "mcp__knowledge-graph__repo_map"],
    "extra_tools": ["mcp__knowledge-graph__repo_map"],
    "extra_skills": [],
    "skills": [],
    "permission_mode": "default",
    "disallowed_tools": [],
    "mcp_servers": [],
    "system_prompt": "You are a planner agent with extras for testing.",
    "model": "claude-sonnet-4-6",
    "effort": "medium",
}


@pytest.fixture(scope="module")
def extras_broker():
    """Broker with one teammate whose config includes extra_tools."""
    broker = Broker()
    tid = f"t-extras-{uuid.uuid4().hex[:10]}"
    tm = StubTeammate(id=tid, name="planner", role="rr-planner")
    broker._teammates[tid] = tm
    broker._inboxes[tid] = asyncio.Queue()
    broker._info[tid] = TeammateInfo(
        id=tid,
        name="planner",
        role="rr-planner",
        spawned_at=time.time(),
        alive=True,
    )
    broker._configs[tid] = EXTRAS_CONFIG
    yield broker, tid


@pytest.fixture(scope="module")
def extras_server_url(extras_broker):
    """Start UIServer backed by an extras-config broker; yield base URL."""
    broker, _ = extras_broker

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
        pytest.fail("UIServer (extras-broker) did not start within 10 seconds")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    t.join(timeout=3)


@pytest.fixture(scope="module")
def dead_broker():
    """Broker with one dead-with-config teammate."""
    broker = Broker()
    tid = f"t-dead-{uuid.uuid4().hex[:10]}"
    broker._info[tid] = TeammateInfo(
        id=tid,
        name="ex-builder",
        role="builder",
        spawned_at=time.time() - 300,
        alive=False,
        died_at_wallclock=time.time() - 10,
        total_cost_usd_at_death=0.01,
        total_input_tokens_at_death=100,
        total_output_tokens_at_death=50,
    )
    broker._configs[tid] = DEAD_CONFIG
    yield broker, tid


@pytest.fixture(scope="module")
def dead_server_url(dead_broker):
    """Start UIServer backed by a dead-with-config broker; yield base URL."""
    broker, _ = dead_broker

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
        pytest.fail("UIServer (dead-broker) did not start within 10 seconds")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    t.join(timeout=3)


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.dashboard
def test_config_chips_visible_after_ws_render(live_server_url, page):
    """AT7 (partial): chips appear after WS state arrives and React renders."""
    page.goto(live_server_url)
    # Wait for at least one config chip to appear (WS data received + React rendered)
    page.locator(".tm-config-chip").first.wait_for(state="visible", timeout=15000)
    chips = page.locator(".tm-config-chip").all()
    chip_texts = [c.inner_text() for c in chips]
    # Should have "2 tools" chip
    assert any("2" in t and "tool" in t for t in chip_texts), (
        f"Expected '2 tools' chip; got: {chip_texts}"
    )
    # Should have "1 skills" chip (skills = ["sdd-workflow"])
    assert any("1" in t and "skill" in t for t in chip_texts), (
        f"Expected '1 skills' chip; got: {chip_texts}"
    )
    # Should have permission_mode chip
    assert any("bypassPermissions" in t for t in chip_texts), (
        f"Expected 'bypassPermissions' chip; got: {chip_texts}"
    )


@pytest.mark.dashboard
def test_config_chip_container_has_click_handler(live_server_url, page):
    """AT7: verify the config chips container exists and can be clicked."""
    page.goto(live_server_url)
    page.locator(".tm-config-chip").first.wait_for(state="visible", timeout=15000)
    # Verify the container exists and has the title attribute (indicates click intention)
    container = page.locator(".tm-config-chips").first
    container.wait_for(state="visible", timeout=5000)
    title = container.get_attribute("title")
    assert title is not None and "config" in title.lower(), (
        f"Expected container to have 'config' in title; got: {title}"
    )


@pytest.mark.dashboard
def test_detail_panel_opens_on_chip_click_and_shows_prompt(live_server_url, page):
    """AT7: clicking config chips opens detail panel with full system prompt."""
    page.goto(live_server_url)
    # Wait for chips (WS data received + React rendered)
    page.locator(".tm-config-chip").first.wait_for(state="visible", timeout=15000)
    # Click the chips container to open the detail panel
    page.locator(".tm-config-chips").first.click()
    # Panel must appear
    panel = page.locator(".tm-detail-panel")
    panel.wait_for(state="visible", timeout=5000)
    # System prompt must be in the panel pre element
    prompt_pre = page.locator(".tm-detail-prompt")
    prompt_pre.wait_for(state="visible", timeout=3000)
    assert KNOWN_CONFIG["system_prompt"] in prompt_pre.inner_text(), (
        f"Expected system_prompt in panel; got: {prompt_pre.inner_text()[:200]}"
    )


@pytest.mark.dashboard
def test_detail_panel_shows_mcp_servers_and_closes_on_esc(live_server_url, page):
    """AT7: detail panel contains MCP server names; Esc closes it."""
    page.goto(live_server_url)
    page.locator(".tm-config-chip").first.wait_for(state="visible", timeout=15000)
    page.locator(".tm-config-chips").first.click()
    panel = page.locator(".tm-detail-panel")
    panel.wait_for(state="visible", timeout=5000)
    # MCP server name must appear in panel
    panel_text = panel.inner_text()
    assert "github" in panel_text, (
        f"Expected 'github' in panel text; got: {panel_text[:300]}"
    )
    # Esc closes the panel
    page.keyboard.press("Escape")
    panel.wait_for(state="hidden", timeout=3000)


@pytest.mark.dashboard
def test_dead_teammate_row_renders_dimmed_with_chips(dead_server_url, page):
    """Edge case: dead-with-config teammate renders a dimmed row with config chips.

    Spec § Edge Cases line 93: after kill_teammate, the dead row stays in the
    dashboard dimmed and config chips/panel remain accessible.
    """
    page.goto(dead_server_url)
    # Wait for WS data — chips must appear for the dead row
    page.locator(".tm-config-chip").first.wait_for(state="visible", timeout=15000)

    # The dead row must carry the tm-row-dead CSS class (dimmed rendering)
    dead_row = page.locator(".tm-row-dead")
    dead_row.wait_for(state="visible", timeout=5000)

    # Chips must still be present (accessible)
    chips_in_dead_row = dead_row.locator(".tm-config-chip").all()
    assert len(chips_in_dead_row) > 0, "Expected config chips inside dead row"

    # Panel must open on click
    dead_row.locator(".tm-config-chips").click()
    panel = page.locator(".tm-detail-panel")
    panel.wait_for(state="visible", timeout=5000)

    # System prompt must be in the panel
    prompt_pre = page.locator(".tm-detail-prompt")
    prompt_pre.wait_for(state="visible", timeout=3000)
    assert DEAD_CONFIG["system_prompt"] in prompt_pre.inner_text(), (
        f"Expected dead agent's system_prompt in panel; got: {prompt_pre.inner_text()[:200]}"
    )


@pytest.mark.dashboard
def test_extras_chip_renders_with_correct_class(extras_server_url, page):
    """AT-11: ConfigChips renders a '+N extras' chip with CSS class tm-config-chip-extra.

    When config.extra_tools is non-empty, a chip with text matching /+N extra(s)/
    must appear alongside the standard tools chip. The extras chip must carry
    CSS class tm-config-chip-extra (visually distinct from tm-config-chip).
    Pre-feature snapshots without extra_tools must not show an extras chip.
    """
    page.goto(extras_server_url)
    # Wait for chips to appear (WS data received + React rendered)
    page.locator(".tm-config-chip").first.wait_for(state="visible", timeout=15000)

    # Assert an extras chip is present
    extras_chips = page.locator(".tm-config-chip-extra").all()
    assert len(extras_chips) == 1, (
        f"Expected exactly 1 extras chip; got {len(extras_chips)}"
    )

    # Assert chip text matches "+1 extra"
    chip_text = extras_chips[0].inner_text()
    assert "+1 extra" in chip_text, (
        f"Expected chip text to contain '+1 extra'; got: {chip_text!r}"
    )

    # Assert the extras chip also carries the base tm-config-chip class
    # (it should have both: tm-config-chip tm-config-chip-extra)
    chip_classes = extras_chips[0].get_attribute("class") or ""
    assert "tm-config-chip" in chip_classes, (
        f"Expected extras chip to have tm-config-chip class; classes: {chip_classes!r}"
    )
    assert "tm-config-chip-extra" in chip_classes, (
        f"Expected extras chip to have tm-config-chip-extra class; classes: {chip_classes!r}"
    )

    # Assert regular tools chip is also present (backward compat: both chips coexist)
    all_chips = page.locator(".tm-config-chip").all()
    chip_texts = [c.inner_text() for c in all_chips]
    assert any("tools" in t for t in chip_texts), (
        f"Expected a 'tools' chip alongside the extras chip; got: {chip_texts}"
    )


@pytest.mark.dashboard
def test_no_extras_chip_when_extra_tools_absent(live_server_url, page):
    """AT-11 backward compat: no extras chip when config lacks extra_tools field.

    Pre-feature snapshots (KNOWN_CONFIG has no extra_tools) must render
    identically to pre-feature behavior — no tm-config-chip-extra chip.
    """
    page.goto(live_server_url)
    page.locator(".tm-config-chip").first.wait_for(state="visible", timeout=15000)

    extras_chips = page.locator(".tm-config-chip-extra").all()
    assert len(extras_chips) == 0, (
        f"Expected no extras chip for pre-feature config; got {len(extras_chips)}: "
        f"{[c.inner_text() for c in extras_chips]}"
    )
