"""AT 11, 12, 13, 14, 15 — Unified node language for shape proposals.

AT-11 — Unified language: proposal and live share --edge-* stroke tokens.
         Given a live topology with a direct edge AND a proposal whose shape has
         a gated edge, when each is rendered, the live direct path's stroke equals
         var(--edge-direct) and the proposal gated path's stroke equals
         var(--edge-gated).

AT-12 — Proposed variant: proposal nodes carry .nodecard.proposed with dashed border.
         Given a pending proposal rendered in the proposal modal, the proposal node
         cards carry both the nodecard and proposed classes and their computed
         border-style is dashed.

AT-13 — Structural deletion-detector: shapeToMermaidUnified + .nodecard.proposed
         named literals present in dashboard.html.

AT-14 — Proposal card is a preview thumbnail that opens the unified modal.
         The static maxWidth: 420 side-by-side diagram is gone; clicking the preview
         thumbnail opens the shared zoom/pan modal (.modal with .zoom-surface).

AT-15 — Non-regression: securityLevel: 'strict' literal preserved in dashboard.html.
         The existing XSS suite tests/dashboard/test_dashboard_artifact_xss.py
         passes unchanged (covered by the test command invoking that file directly).

Playwright tests carry @pytest.mark.dashboard.
Structural tests (AT-13, AT-15) are pure greps — no browser needed but still
marked @pytest.mark.dashboard so they run under the test command's -m filter.

Run prerequisite: ``uv run playwright install chromium``.
asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
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
    EdgeStat,
    LiveTeammateInfo,
    ShapeProposal,
    TeammateInfo,
)
from claude_crew.shapes import Shape, ShapeEdge, ShapeNode
from claude_crew.ui_server import UIServer


# ---------------------------------------------------------------------------
# Private helpers (mirrors test_unified_topology_keyed_lookup.py pattern)
# ---------------------------------------------------------------------------


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


def _make_proposal(shape_id: str = "test-shape-001") -> ShapeProposal:
    """Return a pending proposal whose shape has one gated edge."""
    shape = Shape(
        name="test-shape",
        description="Proposal used in AT-11/12/14 tests.",
        nodes=(
            ShapeNode(slot="sender", role="builder"),
            ShapeNode(slot="receiver", role="builder"),
        ),
        edges=(
            ShapeEdge(from_slot="sender", to_slot="receiver", mode="gated"),
        ),
    )
    return ShapeProposal(
        shape_id=shape_id,
        shape=shape,
        adaptation_diff=None,
        status="pending",
    )


def _spin_dashboard(broker: Broker) -> tuple[str, Any, threading.Thread]:
    """Start a UIServer on a free port in a daemon thread.

    Returns (url, server, thread).
    """
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


# ---------------------------------------------------------------------------
# Fixture: dashboard with a direct topology edge + pending gated proposal
# ---------------------------------------------------------------------------


@pytest.fixture
def proposal_dashboard_url():
    """Dashboard with:
    - 2 live teammates with a direct edge in the topology (for AT-11 live check)
    - 1 pending shape proposal with a gated edge (for AT-11/12/14)
    """
    a_info = _alive_info(1, role="sender")
    b_info = _alive_info(2, role="receiver")
    live = (_live_entry(a_info), _live_entry(b_info))
    edges = (
        EdgeStat(from_slot="a", to_slot="b", mode="direct", exchanges=5, tripped=False),
    )
    s2t = {"a": "tid-1", "b": "tid-2"}
    proposal = _make_proposal()

    b = Broker()
    snap = BrokerSnapshot(
        crew_id="crew-at11-14",
        teammates=tuple(le.info for le in live),
        live=live,
        log=(),
        topology_edge_stats=edges,
        topology_slot_to_teammate=s2t,
        shape_proposals=(proposal,),
    )
    b.snapshot = lambda log_limit=None: snap  # type: ignore[method-assign]
    b.crew_id = snap.crew_id  # type: ignore[misc]

    url, server, t = _spin_dashboard(b)
    yield url
    server.should_exit = True
    t.join(timeout=3)


# ---------------------------------------------------------------------------
# AT-13: Structural deletion-detector (no browser required)
# ---------------------------------------------------------------------------


@pytest.mark.dashboard
class TestAT13StructuralDeletionDetector:
    """AT-13: dashboard.html contains both shapeToMermaidUnified and .nodecard.proposed."""

    def test_shapeToMermaidUnified_named_literal(self):
        dashboard = (
            Path(__file__).parent.parent / "claude_crew" / "ui" / "dashboard.html"
        )
        src = dashboard.read_text(encoding="utf-8")
        assert "shapeToMermaidUnified" in src, (
            "NAMED LITERAL 'shapeToMermaidUnified' not found in dashboard.html — "
            "shapeToMermaidUnified function may have been removed."
        )

    def test_nodecard_proposed_named_literal(self):
        dashboard = (
            Path(__file__).parent.parent / "claude_crew" / "ui" / "dashboard.html"
        )
        src = dashboard.read_text(encoding="utf-8")
        assert ".nodecard.proposed" in src, (
            "NAMED LITERAL '.nodecard.proposed' not found in dashboard.html — "
            ".nodecard.proposed CSS class or usage may have been removed."
        )


# ---------------------------------------------------------------------------
# AT-15: XSS non-regression structural check (no browser required)
# ---------------------------------------------------------------------------


@pytest.mark.dashboard
class TestAT15SecurityLevelPreserved:
    """AT-15 structural: securityLevel: 'strict' is still present in dashboard.html.

    The browser-level XSS non-regression is covered by running
    tests/dashboard/test_dashboard_artifact_xss.py in the test command.
    """

    def test_security_level_strict_named_literal(self):
        dashboard = (
            Path(__file__).parent.parent / "claude_crew" / "ui" / "dashboard.html"
        )
        src = dashboard.read_text(encoding="utf-8")
        assert "securityLevel: 'strict'" in src, (
            "NAMED LITERAL \"securityLevel: 'strict'\" not found in dashboard.html — "
            "XSS hardening may have been relaxed."
        )


# ---------------------------------------------------------------------------
# AT-11: Unified edge token language — live direct + proposal gated
# ---------------------------------------------------------------------------


@pytest.mark.dashboard
def test_at11_unified_edge_token_language(proposal_dashboard_url, page):
    """AT-11: Both the live topology (direct edge) and the proposal modal (gated edge)
    use the same --edge-* CSS token vocabulary for stroke colors.

    Assertions:
      live topology a→b (direct, healthy) → stroke = var(--edge-direct)
      proposal modal sender→receiver (gated) → stroke = var(--edge-gated)
    """
    page.goto(proposal_dashboard_url)

    # Wait for in-rail topology SVG to render and edges to be decorated.
    page.locator("#topo-host svg").wait_for(state="attached", timeout=15_000)
    page.wait_for_timeout(1200)  # edge-decoration useEffect + mapEdgeStatsToPaths

    # ── Live topology: a→b direct edge must carry var(--edge-direct) stroke ──
    live_stroke = page.evaluate(
        """() => {
          const paths = document.querySelectorAll('.rail-topology path.flowchart-link');
          for (const p of paths) {
            if (p.style.stroke) return p.style.stroke;
          }
          return null;
        }"""
    )
    assert live_stroke == "var(--edge-direct)", (
        f"Live direct edge stroke expected 'var(--edge-direct)', got {live_stroke!r}. "
        "The --edge-* token vocabulary may not be applied to the in-rail topology."
    )

    # ── Open shape gate panel via pending-gate pill ──
    page.locator('[data-testid="pending-gate-pill"]').wait_for(
        state="visible", timeout=10_000
    )
    page.locator('[data-testid="pending-gate-pill"]').click()

    # ── Click the proposal thumbnail to open the proposal modal ──
    page.locator(".shape-proposal-thumbnail").first.wait_for(
        state="visible", timeout=5_000
    )
    page.locator(".shape-proposal-thumbnail").first.click()

    # Wait for proposal modal + mermaid render + edge decoration (renderInto async).
    page.locator("#proposal-modal .modal").wait_for(state="visible", timeout=10_000)
    page.wait_for_timeout(1500)  # mermaid.render() promise + rAF + edge coloring

    # ── Proposal modal: gated edge must carry var(--edge-gated) stroke ──
    proposal_stroke = page.evaluate(
        """() => {
          const modal = document.querySelector('#proposal-modal');
          if (!modal) return null;
          const paths = modal.querySelectorAll('path.flowchart-link');
          for (const p of paths) {
            const s = p.style.stroke;
            if (s && s.includes('edge-gated')) return s;
          }
          return null;
        }"""
    )
    assert proposal_stroke == "var(--edge-gated)", (
        f"Proposal gated edge stroke expected 'var(--edge-gated)', got {proposal_stroke!r}. "
        "shapeToMermaidUnified/openProposalModal may not decorate edges with --edge-* tokens."
    )


# ---------------------------------------------------------------------------
# AT-12: Proposed variant — .nodecard.proposed with dashed border
# ---------------------------------------------------------------------------


@pytest.mark.dashboard
def test_at12_proposed_nodecard_class_and_dashed_border(proposal_dashboard_url, page):
    """AT-12: Proposal nodes rendered in the modal carry .nodecard.proposed
    and their computed border-style is dashed.
    """
    page.goto(proposal_dashboard_url)

    # Open shape gate panel.
    page.locator('[data-testid="pending-gate-pill"]').wait_for(
        state="visible", timeout=10_000
    )
    page.locator('[data-testid="pending-gate-pill"]').click()

    # Open the proposal modal.
    page.locator(".shape-proposal-thumbnail").first.wait_for(
        state="visible", timeout=5_000
    )
    page.locator(".shape-proposal-thumbnail").first.click()

    page.locator("#proposal-modal .modal").wait_for(state="visible", timeout=10_000)
    page.wait_for_timeout(1500)  # mermaid render + DOMPurify sanitize + DOM paint

    # Verify .nodecard.proposed elements exist in the proposal modal.
    proposed_count = page.evaluate(
        "() => document.querySelectorAll('#proposal-modal .nodecard.proposed').length"
    )
    assert proposed_count > 0, (
        "No .nodecard.proposed elements found inside #proposal-modal after render. "
        "shapeToMermaidUnified may not emit the 'proposed' CSS class on nodes."
    )

    # Verify border-style is dashed (the CSS rule .nodecard.proposed applies).
    border_style = page.evaluate(
        """() => {
          const node = document.querySelector('#proposal-modal .nodecard.proposed');
          if (!node) return null;
          return window.getComputedStyle(node).borderStyle;
        }"""
    )
    assert border_style is not None, (
        ".nodecard.proposed element not found when checking computed border-style."
    )
    assert "dashed" in border_style, (
        f"Expected computed border-style to include 'dashed', got {border_style!r}. "
        "The .nodecard.proposed CSS rule may be missing or misapplied."
    )


# ---------------------------------------------------------------------------
# AT-14: Proposal card is a preview thumbnail that opens the unified modal
# ---------------------------------------------------------------------------


@pytest.mark.dashboard
def test_at14_proposal_thumbnail_opens_modal(proposal_dashboard_url, page):
    """AT-14: ShapeProposalCard renders as a clickable thumbnail preview.

    Sub-checks:
    1. NAMED LITERAL 'maxWidth: 420' is absent from dashboard.html (the old
       static side-by-side diagram was removed).
    2. The rendered card has a .shape-proposal-thumbnail element (the thumbnail).
    3. Before clicking, the modal is not visible.
    4. Clicking the thumbnail shows the shared zoom/pan modal (.modal with .zoom-surface).
    """
    # ── Structural sub-check: maxWidth: 420 literal must be gone ──
    dashboard_path = (
        Path(__file__).parent.parent / "claude_crew" / "ui" / "dashboard.html"
    )
    src = dashboard_path.read_text(encoding="utf-8")
    assert "maxWidth: 420" not in src, (
        "NAMED LITERAL 'maxWidth: 420' still present in dashboard.html — "
        "the old side-by-side ShapeProposalCard diagram layout was not removed."
    )

    page.goto(proposal_dashboard_url)

    # Open shape gate panel.
    page.locator('[data-testid="pending-gate-pill"]').wait_for(
        state="visible", timeout=10_000
    )
    page.locator('[data-testid="pending-gate-pill"]').click()

    # Proposal card should render with the thumbnail element.
    page.locator(".shape-proposal-card").first.wait_for(state="visible", timeout=10_000)
    thumbnail = page.locator(".shape-proposal-thumbnail").first
    thumbnail.wait_for(state="visible", timeout=5_000)

    # Modal should not be open yet.
    assert not page.locator("#proposal-modal .modal").is_visible(), (
        "#proposal-modal .modal is visible before the thumbnail was clicked."
    )

    # Click the thumbnail to open the shared zoom/pan modal.
    thumbnail.click()

    # Modal with zoom-surface must become visible.
    page.locator("#proposal-modal .modal").wait_for(state="visible", timeout=10_000)
    page.locator("#proposal-modal .zoom-surface").wait_for(
        state="visible", timeout=5_000
    )
    assert page.locator("#proposal-modal .modal").is_visible(), (
        "#proposal-modal .modal is not visible after clicking the thumbnail."
    )
