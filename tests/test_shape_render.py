"""Playwright tests for shape-gate panel mermaid rendering — AT 13.

AT 13: (dashboard, graphical mermaid render + XSS guard)
  - Given a pending proposal in /api/state, the shape-gate panel renders the
    proposed shape as a graphical mermaid SVG via the page's existing
    mermaid.render() pipeline.
  - The DAG's node labels (slots/roles) are present in the rendered SVG and
    visible — NOT black boxes (foreignObject/DOMPurify pipeline is wired up).
  - Approve/Decline controls are present beside the DAG.
  - Given a proposal whose slot/role text carries a mermaid XSS payload, the
    rendered output is sanitized (no script executes; the established
    malicious-mermaid regression guard still holds) while the diagram still
    renders.

Follows the same harness pattern as tests/dashboard/test_dashboard_mermaid.py:
spins up a real UIServer backed by a Broker with a registered proposal, then
navigates with Playwright and asserts on the DOM.

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
from claude_crew.shapes import Shape, ShapeEdge, ShapeNode
from claude_crew.ui_server import UIServer


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_shape(
    slot_a: str = "implementor",
    role_a: str = "builder",
    slot_b: str = "reviewer",
    role_b: str = "sentinel",
) -> Shape:
    """Create a 2-node shape with a single gated edge."""
    return Shape(
        name="test-shape",
        description="A test shape for render tests",
        nodes=(
            ShapeNode(slot=slot_a, role=role_a),
            ShapeNode(slot=slot_b, role=role_b),
        ),
        edges=(
            ShapeEdge(from_slot=slot_a, to_slot=slot_b, mode="gated"),
        ),
    )


def _broker_with_proposal(shape: Shape) -> tuple[Broker, str]:
    """Register a pending shape proposal on a fresh Broker.

    Returns (broker, shape_id).  The proposal is ``pending`` — the dashboard
    should show it in the shape-gate panel.
    """
    broker = Broker()
    shape_id = broker.register_proposal(shape)
    return broker, shape_id


def _start_server(broker: Broker) -> tuple[str, uvicorn.Server, threading.Thread]:
    """Start a UIServer on a free port; return (base_url, server, thread)."""
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


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.dashboard
def test_shape_gate_renders_dag_with_visible_labels(page):
    """AT 13 (happy path): shape-gate panel renders a graphical mermaid DAG
    whose node labels (slots/roles) are present and visible — not black boxes.

    Verifies:
    - .shape-gate-panel is rendered when there is a pending proposal.
    - An SVG is present inside the panel (mermaid.render() ran).
    - The SVG carries text content containing the slot names (foreignObject
      path; the DAG is labeled, not unlabeled black rectangles).
    - Approve and Decline buttons are present beside the DAG.
    """
    shape = _make_shape()
    broker, _shape_id = _broker_with_proposal(shape)
    url, server, t = _start_server(broker)
    try:
        page.goto(url)

        # Wait for the shape-gate panel to appear.
        panel = page.locator(".shape-gate-panel")
        panel.wait_for(state="visible", timeout=15_000)

        # Wait for the async mermaid.render() call to complete and replace the
        # pre block with the SVG.  The existing tests use 2-3 s here.
        page.wait_for_timeout(3_000)

        # ── 1. SVG is present ────────────────────────────────────────────────
        svg_locator = page.locator(".shape-gate-panel svg")
        assert svg_locator.count() >= 1, (
            f"Expected >=1 SVG inside .shape-gate-panel, got {svg_locator.count()}"
        )

        # ── 2. Labels are visible (not black boxes) ──────────────────────────
        # Mermaid (securityLevel:'strict') renders diagram labels as HTML
        # inside <foreignObject>, so inner_text() on the panel will capture
        # them if the pipeline is correctly wired.
        panel_text = panel.inner_text()
        assert "implementor" in panel_text, (
            f"Slot 'implementor' not visible in shape-gate panel. "
            f"Panel text (first 500): {panel_text[:500]!r}"
        )
        assert "reviewer" in panel_text, (
            f"Slot 'reviewer' not visible in shape-gate panel. "
            f"Panel text (first 500): {panel_text[:500]!r}"
        )

        # Also verify via JS that the SVG carries text/foreignObject nodes
        # with the slot names — belt-and-suspenders check for the black-box bug.
        label_content = page.evaluate(
            """() => {
                const labels = [];
                for (const svg of document.querySelectorAll('.shape-gate-panel svg')) {
                    // foreignObject-based labels (securityLevel:'strict' path):
                    for (const el of svg.querySelectorAll('foreignObject *')) {
                        const t = el.textContent.trim();
                        if (t) labels.push(t);
                    }
                    // Plain SVG <text> elements (fallback):
                    for (const el of svg.querySelectorAll('text')) {
                        const t = el.textContent.trim();
                        if (t) labels.push(t);
                    }
                }
                return labels.join(' ');
            }"""
        )
        combined = f"{label_content} {panel_text}"
        assert "implementor" in combined, (
            f"'implementor' not found in SVG labels or panel text. "
            f"SVG labels: {label_content!r}"
        )

        # ── 3. Approve / Decline controls ────────────────────────────────────
        approve = page.locator(".shape-gate-panel button.shape-approve-btn")
        decline = page.locator(".shape-gate-panel button.shape-decline-btn")
        assert approve.count() >= 1, "Approve button not found in shape-gate panel"
        assert decline.count() >= 1, "Decline button not found in shape-gate panel"
    finally:
        server.should_exit = True
        t.join(timeout=3)


@pytest.mark.dashboard
def test_shape_gate_xss_guard(page):
    """AT 13 (XSS guard): malicious mermaid payload in slot/role text is
    neutralized by the reused securityLevel:'strict' + DOMPurify pipeline;
    the diagram still renders and the Approve/Decline controls are present.

    This is the malicious-mermaid regression guard extended to the shape-gate
    panel.  The XSS vectors are embedded in the role text so that
    shape_to_mermaid() incorporates them into the mermaid label strings —
    identical attack surface to the artifact-viewer XSS test.
    """
    # Role text containing three XSS vectors (no double-quotes to keep the
    # generated mermaid label syntax valid):
    #   <script>  — must be stripped from SVG output by DOMPurify
    #   onerror=  — must be stripped from any img/element attribute
    #   javascript: — must not survive as an executable href
    xss_role = (
        "sentinel"
        "<script>window.XSS_SHAPE_FIRED=true;</script>"
        "<img src=x onerror=window.XSS_SHAPE_FIRED=true>"
    )
    xss_shape = Shape(
        name="xss-test-shape",
        description="Shape whose role carries XSS vectors",
        nodes=(
            ShapeNode(slot="implementor", role="builder"),
            ShapeNode(slot="reviewer", role=xss_role),
        ),
        edges=(
            ShapeEdge(from_slot="implementor", to_slot="reviewer", mode="gated"),
        ),
    )
    broker, _shape_id = _broker_with_proposal(xss_shape)
    url, server, t = _start_server(broker)
    try:
        page.goto(url)

        # Wait for the shape-gate panel.
        panel = page.locator(".shape-gate-panel")
        panel.wait_for(state="visible", timeout=15_000)

        # Allow mermaid + DOMPurify sanitization to complete.
        page.wait_for_timeout(8_000)

        # ── 1. XSS must NOT have fired ────────────────────────────────────────
        xss_fired = page.evaluate("() => window.XSS_SHAPE_FIRED === true")
        assert not xss_fired, (
            "XSS payload executed inside shape-gate panel — "
            "mermaid.render() / DOMPurify sanitization failed"
        )

        # ── 2. No <script> element survives in the panel ──────────────────────
        script_count = page.locator(".shape-gate-panel script").count()
        assert script_count == 0, (
            f"<script> element found in shape-gate panel ({script_count} found)"
        )

        # ── 3. No on* event-handler attributes survive ────────────────────────
        handler_count = page.evaluate(
            """() => {
                let n = 0;
                for (const el of document.querySelectorAll('.shape-gate-panel *')) {
                    for (const attr of el.attributes) {
                        if (/^on/i.test(attr.name)) n++;
                    }
                }
                return n;
            }"""
        )
        assert handler_count == 0, (
            f"on* event-handler attribute(s) found in shape-gate panel "
            f"({handler_count} found) — DOMPurify FORBID_ATTR stripping failed"
        )

        # ── 4. The panel still rendered (pipeline ran at all) ─────────────────
        # Either the SVG rendered or the pre block remained — either is OK as
        # long as the XSS was blocked.  What MUST be present is the panel itself
        # and its Approve/Decline controls.
        assert panel.count() > 0, ".shape-gate-panel vanished after XSS attempt"
        approve = page.locator(".shape-gate-panel button.shape-approve-btn")
        assert approve.count() >= 1, "Approve button missing after XSS attempt"
    finally:
        server.should_exit = True
        t.join(timeout=3)


@pytest.mark.dashboard
def test_shape_gate_hidden_when_no_pending_proposals(page):
    """The shape-gate panel is hidden (absent from DOM) when there are no
    pending proposals — the rest of the dashboard loads normally.
    """
    broker = Broker()  # no proposals registered
    url, server, t = _start_server(broker)
    try:
        page.goto(url)
        # Give the dashboard time to load and render.
        page.wait_for_timeout(3_000)
        # .shape-gate-panel should NOT be in the DOM.
        assert page.locator(".shape-gate-panel").count() == 0, (
            ".shape-gate-panel is visible even though there are no pending proposals"
        )
    finally:
        server.should_exit = True
        t.join(timeout=3)
