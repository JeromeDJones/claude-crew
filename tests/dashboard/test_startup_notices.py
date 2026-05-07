"""Playwright headless-Chromium tests for the StartupNoticesSection panel.

Acceptance tests:
- #8: planted misconfig (one shadow INFO + one unknown-skill WARN) renders
  the panel; WARN visible by default; INFO hidden behind a toggle that
  reveals it on click.
- #9: empty diagnostics → no startup-notices selector in DOM.

These tests drive the dashboard with a synthetic Broker carrying a
hand-built ``startup_diagnostics`` tuple — they exercise the panel
rendering layer without depending on the live capture pipeline (covered
by `factory-capture-wire`'s integration tests).

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

from claude_crew.broker import Broker
from claude_crew.diagnostics import StartupDiagnostic
from claude_crew.ui_server import UIServer


# ── helpers ──────────────────────────────────────────────────────────────────


def _diag(
    level: str,
    message: str,
    source: str,
    timestamp: float,
    category: str,
) -> StartupDiagnostic:
    return StartupDiagnostic(
        level=level,
        message=message,
        source=source,
        timestamp=timestamp,
        category=category,
    )


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


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def misconfig_server_url():
    """Broker carrying one shadow INFO + one unknown-skill WARN."""
    diags = (
        _diag(
            "INFO",
            "explorer.md shadows default-pack explorer",
            "claude_crew.subagents.loader",
            1715000000.0,
            "shadow",
        ),
        _diag(
            "WARNING",
            "extra_skills: skill 'nope-not-real' not found",
            "claude_crew.factories",
            1715000001.0,
            "unknown_skill",
        ),
    )
    broker = Broker(startup_diagnostics=diags)
    url, server, t = _start_server(broker)
    yield url
    server.should_exit = True
    t.join(timeout=3)


@pytest.fixture(scope="module")
def clean_server_url():
    """Broker with no startup diagnostics (empty tuple)."""
    broker = Broker()
    url, server, t = _start_server(broker)
    yield url
    server.should_exit = True
    t.join(timeout=3)


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.dashboard
def test_planted_misconfig_visible_on_dashboard(misconfig_server_url, page):
    """Acceptance #8: panel renders with WARN visible, INFO behind toggle."""
    page.goto(misconfig_server_url)

    # Panel header must appear once WS state arrives.
    header = page.locator(".startup-notices-header")
    header.wait_for(state="visible", timeout=15000)
    header_text = header.inner_text()
    assert "STARTUP NOTICES" in header_text.upper(), (
        f"Expected 'STARTUP NOTICES' in header; got: {header_text!r}"
    )
    assert "2" in header_text, (
        f"Expected total count '2' in header; got: {header_text!r}"
    )

    # WARN row visible by default.
    page.locator(".startup-notice-row").first.wait_for(state="visible", timeout=5000)
    warn_rows = page.locator(".startup-notice-row").filter(
        has=page.locator(".startup-notice-badge-warning")
    )
    assert warn_rows.count() == 1, (
        f"Expected 1 WARN row visible by default; got {warn_rows.count()}"
    )
    warn_text = warn_rows.first.inner_text()
    assert "nope-not-real" in warn_text, (
        f"Expected planted unknown-skill WARN message; got: {warn_text!r}"
    )

    # INFO row hidden by default.
    info_rows = page.locator(".startup-notice-row").filter(
        has=page.locator(".startup-notice-badge-info")
    )
    assert info_rows.count() == 0, (
        f"Expected INFO row hidden by default; got {info_rows.count()} visible"
    )

    # Toggle reveals INFO row.
    toggle = page.locator(".startup-notices-toggle").first
    toggle.wait_for(state="visible", timeout=3000)
    assert "Show INFO" in toggle.inner_text(), (
        f"Expected 'Show INFO' on toggle; got: {toggle.inner_text()!r}"
    )
    toggle.click()

    info_rows = page.locator(".startup-notice-row").filter(
        has=page.locator(".startup-notice-badge-info")
    )
    info_rows.first.wait_for(state="visible", timeout=3000)
    assert info_rows.count() == 1, (
        f"Expected INFO row visible after toggle; got {info_rows.count()}"
    )
    info_text = info_rows.first.inner_text()
    assert "shadows" in info_text.lower(), (
        f"Expected shadow INFO message after toggle; got: {info_text!r}"
    )

    # Toggle label flips.
    assert "Hide INFO" in toggle.inner_text(), (
        f"Expected 'Hide INFO' on toggle after click; got: {toggle.inner_text()!r}"
    )


@pytest.mark.dashboard
def test_panel_hidden_when_empty(clean_server_url, page):
    """Acceptance #9: clean config → no panel rendered."""
    page.goto(clean_server_url)
    # Wait for the dashboard to mount (any standard chrome element).
    page.locator("#root").wait_for(state="attached", timeout=15000)
    # Allow WS state to arrive (bounded sleep — server pushes ~every 1.5s).
    time.sleep(2.5)
    assert page.locator(".startup-notices-section").count() == 0, (
        "Startup notices section must not render when diagnostics list is empty"
    )
    assert page.locator(".startup-notices-header").count() == 0, (
        "Startup notices header must not render when diagnostics list is empty"
    )


@pytest.mark.dashboard
def test_panel_visible_after_refresh(misconfig_server_url, page):
    """Spec design decision: in-memory state only — panel re-renders on refresh.

    Reload the page and assert the panel is once again visible (data is on
    the snapshot, not in localStorage).
    """
    page.goto(misconfig_server_url)
    page.locator(".startup-notices-header").wait_for(state="visible", timeout=15000)
    page.reload()
    page.locator(".startup-notices-header").wait_for(state="visible", timeout=15000)


@pytest.mark.dashboard
def test_visual_class_parity(misconfig_server_url, page):
    """Spec design decision: section uses a sibling/parallel CSS class to
    `terminated-section`. The panel root carries `startup-notices-section`,
    matching the established collapsible visual idiom.
    """
    page.goto(misconfig_server_url)
    section = page.locator(".startup-notices-section")
    section.wait_for(state="visible", timeout=15000)
    assert section.count() == 1
