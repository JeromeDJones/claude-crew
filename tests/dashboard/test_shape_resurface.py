"""Playwright headless-Chromium tests for the resurfaceable shape-gate.

AT5: verifies that pending-gate proposals render a badge/tray entry in the
top bar, that clicking the gate pill opens the modal with the DAG, that
switching the active instance tab does not lose the badge (the M0 bug fix),
and that resolving the proposal clears the badge.

Follows the same harness pattern as test_dashboard_mermaid.py:
spins up a real UIServer (or two for the instance-switch test), navigates
with Playwright, and asserts on the DOM.

Run prerequisite: ``uv run playwright install chromium``.
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from claude_crew.broker import Broker
from claude_crew.instance_registry import InstanceRegistry
from claude_crew.shapes import Shape, ShapeEdge, ShapeNode
from claude_crew.ui_server import UIServer


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_shape(name: str = "test-gate") -> Shape:
    """Minimal two-node shape for proposal testing."""
    return Shape(
        name=name,
        description="A test gate shape",
        nodes=(
            ShapeNode(slot="implementor", role="builder"),
            ShapeNode(slot="reviewer", role="sentinel"),
        ),
        edges=(
            ShapeEdge(from_slot="implementor", to_slot="reviewer", mode="gated"),
        ),
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(
    broker: Broker,
    *,
    port: int | None = None,
    registry: InstanceRegistry | None = None,
) -> tuple[str, object, threading.Thread]:
    """Spin up a UIServer in a daemon thread; return (base_url, server, thread)."""
    if port is None:
        port = _free_port()

    ui = UIServer(broker, port=port, registry=registry)
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


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def gate_server():
    """Single-instance UIServer with one pending proposal."""
    broker = Broker()
    shape = _make_shape()
    shape_id = broker.register_proposal(shape)
    url, server, t = _start_server(broker)
    yield url, broker, shape_id
    server.should_exit = True
    t.join(timeout=3)


@pytest.fixture()
def two_instance_server(tmp_path):
    """Leader + follower UIServers sharing an instance registry.

    The follower has the pending proposal; the leader discovers the follower
    via the shared InstanceRegistry directory and aggregates its state.
    """
    reg_dir = tmp_path / "instances"
    reg_dir.mkdir()

    original_env = os.environ.get("CLAUDE_CREW_INSTANCE_REGISTRY_DIR")
    os.environ["CLAUDE_CREW_INSTANCE_REGISTRY_DIR"] = str(reg_dir)
    server_b = None
    server_a = None
    t_b = None
    t_a = None
    reg_b = None
    reg_a = None
    try:
        # ── Follower: has the pending proposal ──────────────────────────────
        port_b = _free_port()
        broker_b = Broker()
        shape = _make_shape("follower-gate")
        shape_id = broker_b.register_proposal(shape)
        reg_b = InstanceRegistry(crew_id=broker_b.crew_id, port=port_b)
        url_b, server_b, t_b = _start_server(broker_b, port=port_b, registry=reg_b)
        reg_b.register()  # write into shared registry dir so leader discovers it

        # ── Leader: no proposals locally; discovers follower via registry ───
        port_a = _free_port()
        broker_a = Broker()
        reg_a = InstanceRegistry(crew_id=broker_a.crew_id, port=port_a)
        url_a, server_a, t_a = _start_server(broker_a, port=port_a, registry=reg_a)
        reg_a.register()  # also registers itself (leader sees its own entry, skips it)

        yield url_a, broker_b, shape_id
    finally:
        if server_a is not None:
            server_a.should_exit = True
        if server_b is not None:
            server_b.should_exit = True
        if t_a is not None:
            t_a.join(timeout=3)
        if t_b is not None:
            t_b.join(timeout=3)
        if reg_b is not None:
            reg_b.deregister()
        if reg_a is not None:
            reg_a.deregister()
        if original_env is not None:
            os.environ["CLAUDE_CREW_INSTANCE_REGISTRY_DIR"] = original_env
        else:
            os.environ.pop("CLAUDE_CREW_INSTANCE_REGISTRY_DIR", None)


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.dashboard
class TestShapeGateResurface:
    """AT5: Resurfaceable gate — badge, DAG modal, instance-switch persistence, resolve-clears."""

    def test_badge_renders_with_pending_proposal(self, page, gate_server):
        """Badge/pill renders in the top bar when a pending proposal is present."""
        url, broker, shape_id = gate_server
        page.goto(url)
        pill = page.locator('[data-testid="pending-gate-pill"]')
        pill.wait_for(state="visible", timeout=15_000)
        assert pill.is_visible()
        # The pill shows "Gate" label and a non-zero count
        pill_text = pill.inner_text()
        assert "Gate" in pill_text

    def test_open_modal_shows_dag(self, page, gate_server):
        """Clicking the gate pill opens the modal containing the mermaid DAG."""
        url, broker, shape_id = gate_server
        page.goto(url)
        pill = page.locator('[data-testid="pending-gate-pill"]')
        pill.wait_for(state="visible", timeout=15_000)
        pill.click()
        # Shape gate modal is visible
        panel = page.locator(".shape-gate-panel")
        panel.wait_for(state="visible", timeout=5_000)
        # The proposal card with the diagram container is present
        card = panel.locator(".shape-proposal-card")
        card.wait_for(state="visible", timeout=5_000)
        # The diagram div is present (renderMermaidBlocks replaces code → SVG async)
        diagram = panel.locator(".shape-proposal-diagram")
        diagram.wait_for(state="visible", timeout=5_000)
        # Wait for mermaid render to complete (async replacement of code block with SVG)
        page.wait_for_timeout(3_000)
        # Either the rendered SVG or the source code element must be present
        has_svg = panel.locator(".shape-proposal-diagram svg").count() >= 1
        has_code = panel.locator(".shape-proposal-diagram code").count() >= 1
        assert has_svg or has_code, (
            "Expected either rendered SVG or mermaid source code element in the diagram"
        )

    def test_badge_persists_after_modal_closed(self, page, gate_server):
        """Closing the modal leaves the badge in the top bar (not cleared on close)."""
        url, broker, shape_id = gate_server
        page.goto(url)
        pill = page.locator('[data-testid="pending-gate-pill"]')
        pill.wait_for(state="visible", timeout=15_000)
        # Open the modal
        pill.click()
        panel = page.locator(".shape-gate-panel")
        panel.wait_for(state="visible", timeout=5_000)
        # Close the modal via the ✕ button
        close_btn = panel.locator("button").filter(has_text="✕")
        close_btn.wait_for(state="visible", timeout=3_000)
        close_btn.click()
        panel.wait_for(state="hidden", timeout=3_000)
        # Badge must still be visible — proposal is still pending
        assert pill.is_visible()

    def test_persists_across_instance_switch(self, page, two_instance_server):
        """Switching the active instance tab does not lose the pending-gate badge.

        The follower has the pending proposal; the badge is derived from the
        cross-instance flatMap of all instances' shape_proposals, so it
        persists regardless of which tab is active.
        """
        url_a, broker_b, shape_id = two_instance_server
        page.goto(url_a)
        # Wait for the leader to aggregate both instances and show the badge
        pill = page.locator('[data-testid="pending-gate-pill"]')
        pill.wait_for(state="visible", timeout=20_000)
        assert pill.is_visible()
        # Find the InstanceStrip tabs — there should be at least two (leader + follower)
        tabs = page.locator("[data-testid^='instance-tab-']")
        tabs.first.wait_for(state="visible", timeout=5_000)
        tab_count = tabs.count()
        assert tab_count >= 2, (
            f"Expected at least 2 instance tabs; found {tab_count}. "
            "The follower instance may not have been discovered."
        )
        # Click the second tab (the follower's instance) to switch the active instance
        tabs.nth(1).click()
        # After switching, the badge must still be visible (M0 bug: it would vanish)
        assert pill.is_visible()

    def test_clears_on_resolve(self, page, gate_server):
        """Resolving the proposal via POST /shape-approval clears the badge."""
        url, broker, shape_id = gate_server
        page.goto(url)
        pill = page.locator('[data-testid="pending-gate-pill"]')
        pill.wait_for(state="visible", timeout=15_000)
        assert pill.is_visible()
        # Resolve the proposal by approving it through the HTTP API
        resp = httpx.post(
            f"{url}/shape-approval/{broker.crew_id}/{shape_id}",
            json={"decision": "approve"},
            timeout=5,
        )
        assert resp.status_code == 200
        # The dashboard polls /api/state; after the proposal resolves the badge disappears
        pill.wait_for(state="hidden", timeout=15_000)
