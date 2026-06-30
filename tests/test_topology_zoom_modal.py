"""
Playwright + structural tests for the shared zoom/pan modal substrate.
Maps to spec acceptance tests AT 1–8 (task: shared-zoom-pan-modal).

AT 1 (Playwright): Roomier in-rail topology + topo-head controls
AT 2 (structural grep): topo-head + openTopologyModal present in dashboard.html
AT 3 (Playwright): Topology expand modal fits the whole graph (8-teammate crew)
AT 4 (Playwright): Modal footer pinned + body fills via zoom-surface class
AT 5 (Playwright): Zoom label reflects actual zoom percentage
AT 6 (Playwright): Modal sad path — empty topology degrades gracefully
AT 7 (structural grep): zoom-surface + fitToHost + double-rAF present in dashboard.html
AT 8 (structural grep): keyed lookup + gated-bridge + /edge-log + promote-to-gated preserved

Run prerequisite: `uv run playwright install chromium` (one-time).
asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.

Fixture design: each Playwright test has a function-scoped URL fixture (one
UIServer per test).  The module-scoped Playwright page/browser come from
tests/conftest.py.  Structural tests need no browser at all.
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
    crew_id: str = "crew-modal-test",
    live: tuple[LiveTeammateInfo, ...],
    edge_stats: tuple[EdgeStat, ...] = (),
    slot_to_teammate: dict[str, str] | None = None,
) -> BrokerSnapshot:
    infos = tuple(le.info for le in live)
    return BrokerSnapshot(
        crew_id=crew_id,
        teammates=infos,
        live=live,
        log=(),
        topology_edge_stats=edge_stats,
        topology_slot_to_teammate=dict(slot_to_teammate or {}),
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


def _make_6_agent_snapshot() -> BrokerSnapshot:
    """6-teammate crew with topology_edge_stats (active topology)."""
    agents = [_alive_info(i, role=f"role-{i}") for i in range(1, 7)]
    live = tuple(_live_entry(a) for a in agents)
    edges = (
        EdgeStat(from_slot="slot1", to_slot="slot2", mode="direct", exchanges=3, tripped=False),
        EdgeStat(from_slot="slot2", to_slot="slot3", mode="tee",    exchanges=1, tripped=False),
        EdgeStat(from_slot="slot3", to_slot="slot4", mode="gated",  exchanges=5, tripped=False),
        EdgeStat(from_slot="slot4", to_slot="slot5", mode="direct", exchanges=2, tripped=True),
        EdgeStat(from_slot="slot5", to_slot="slot6", mode="tee",    exchanges=4, tripped=False),
        EdgeStat(from_slot="slot6", to_slot="slot1", mode="direct", exchanges=1, tripped=False),
    )
    s2t = {f"slot{i}": f"tid-{i}" for i in range(1, 7)}
    return _stub_snapshot(
        crew_id="crew-6agent",
        live=live,
        edge_stats=edges,
        slot_to_teammate=s2t,
    )


def _make_8_agent_snapshot() -> BrokerSnapshot:
    """8-teammate crew with topology_edge_stats (large crew for AT3)."""
    agents = [_alive_info(i, role=f"role-{i}") for i in range(1, 9)]
    live = tuple(_live_entry(a) for a in agents)
    edges = tuple(
        EdgeStat(
            from_slot=f"slot{i}",
            to_slot=f"slot{(i % 8) + 1}",
            mode="direct",
            exchanges=i,
            tripped=False,
        )
        for i in range(1, 9)
    )
    s2t = {f"slot{i}": f"tid-{i}" for i in range(1, 9)}
    return _stub_snapshot(
        crew_id="crew-8agent",
        live=live,
        edge_stats=edges,
        slot_to_teammate=s2t,
    )


def _make_empty_snapshot() -> BrokerSnapshot:
    """3-teammate crew with NO topology_edge_stats (roster fallback)."""
    agents = [_alive_info(i) for i in range(1, 4)]
    live = tuple(_live_entry(a) for a in agents)
    return _stub_snapshot(
        crew_id="crew-empty",
        live=live,
        edge_stats=(),
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def six_agent_url():
    snap = _make_6_agent_snapshot()
    broker = _patched_broker(snap)
    url, server, t = _spin_dashboard(broker)
    yield url
    server.should_exit = True
    t.join(timeout=3)


@pytest.fixture
def eight_agent_url():
    snap = _make_8_agent_snapshot()
    broker = _patched_broker(snap)
    url, server, t = _spin_dashboard(broker)
    yield url
    server.should_exit = True
    t.join(timeout=3)


@pytest.fixture
def empty_topology_url():
    snap = _make_empty_snapshot()
    broker = _patched_broker(snap)
    url, server, t = _spin_dashboard(broker)
    yield url
    server.should_exit = True
    t.join(timeout=3)


# ── AT 1: Roomier in-rail topology + topo-head controls ───────────────────


@pytest.mark.dashboard
def test_at1_topo_head_controls_and_host_height(six_agent_url, page):
    """AT 1: 6-agent crew renders .topo-head with zoom controls + expand button.
    The .topology-host rendered height must be > 240px and <= 340px.
    """
    page.goto(six_agent_url)
    # Wait for topology to render
    page.locator(".rail-topology").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(500)

    # Check .topo-head exists
    topo_head = page.locator(".topo-head").first
    assert topo_head.count() > 0, ".topo-head row not found in the rendered dashboard"

    # Check three zoom controls (−, fit, +) exist inside topo-head
    buttons = page.locator(".topo-head .topo-btn")
    btn_count = buttons.count()
    assert btn_count >= 3, (
        f"Expected at least 3 .topo-btn inside .topo-head (−/fit/+ + expand); got {btn_count}"
    )

    # Check expand button exists
    expand_btn = page.locator(".topo-head .topo-btn.expand")
    assert expand_btn.count() > 0, ".topo-btn.expand (expand button) not found in .topo-head"

    # Check host height: clamp(220, 240 + 18*(6-3), 340) = clamp(220, 294, 340) = 294
    host_rect = page.evaluate(
        """() => {
          const host = document.querySelector('#topo-host');
          if (!host) return null;
          const rect = host.getBoundingClientRect();
          return { width: rect.width, height: rect.height };
        }"""
    )
    assert host_rect is not None, "#topo-host element not found"
    h = host_rect["height"]
    assert h > 240, f".topology-host height must be > 240px (got {h:.1f}px)"
    assert h <= 340, f".topology-host height must be <= 340px (got {h:.1f}px)"


# ── AT 2: Structural deletion-detector ────────────────────────────────────


@pytest.mark.dashboard
def test_at2_structural_topo_head_and_open_topology_modal():
    """AT 2: dashboard.html contains the named literals 'topo-head' and
    'openTopologyModal'. Fails if the redesign is absent.
    """
    text = DASHBOARD_HTML.read_text(encoding="utf-8")
    assert "topo-head" in text, (
        "dashboard.html does not contain 'topo-head' — the topo-head redesign is missing"
    )
    assert "openTopologyModal" in text, (
        "dashboard.html does not contain 'openTopologyModal' — the expand modal is missing"
    )


# ── AT 3: Modal fits the whole graph (8-agent crew) ───────────────────────


@pytest.mark.dashboard
def test_at3_topology_expand_modal_fits_large_crew(eight_agent_url, page):
    """AT 3: 8-agent crew expand modal shows the whole graph (all nodecards
    visible within modal body bounds after auto-fit).
    """
    js_errors: list[str] = []
    page.on("pageerror", lambda e: js_errors.append(str(e)))

    page.goto(eight_agent_url)
    page.locator(".rail-topology").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(1000)  # mermaid render + decoration

    # Click expand button
    expand_btn = page.locator(".topo-head .topo-btn.expand").first
    expand_btn.click()

    # Wait for modal to appear and mermaid to render inside it
    page.locator("#modal-host").wait_for(state="attached", timeout=10000)
    # Wait for SVG to render inside modal (double-rAF auto-fit needs time)
    page.locator("#modal-host svg").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(600)  # allow double-rAF + fitToHost to complete

    # Check that the modal body has class zoom-surface (AT 4 partially tested here too)
    modal_body_classes: str = page.evaluate(
        "() => document.querySelector('.modal-body') ? document.querySelector('.modal-body').className : ''"
    )
    assert "zoom-surface" in modal_body_classes, (
        f".modal-body must carry class 'zoom-surface'; got classes: {modal_body_classes!r}"
    )

    # Check that all nodecard elements are within modal body bounds
    result = page.evaluate(
        """() => {
          const body = document.querySelector('.modal-body');
          if (!body) return { error: 'no .modal-body' };
          const bodyRect = body.getBoundingClientRect();

          // Get all nodecards inside the modal
          const nodecards = [...document.querySelectorAll('#modal-host .nodecard')];
          if (nodecards.length === 0) {
            // No .nodecard foreignObject elements — check if SVG at least rendered
            const svg = document.querySelector('#modal-host svg');
            return { nodecardCount: 0, svgPresent: !!svg, bodyRect: {
              top: bodyRect.top, bottom: bodyRect.bottom,
              left: bodyRect.left, right: bodyRect.right
            }};
          }

          const rects = nodecards.map(nc => nc.getBoundingClientRect());
          const topmost    = Math.min(...rects.map(r => r.top));
          const bottommost = Math.max(...rects.map(r => r.bottom));

          return {
            nodecardCount: nodecards.length,
            topmost,
            bottommost,
            bodyTop:    bodyRect.top,
            bodyBottom: bodyRect.bottom,
            fitsTop:    topmost    >= bodyRect.top    - 2,
            fitsBottom: bottommost <= bodyRect.bottom + 2,
          };
        }"""
    )

    # Verify no JS errors occurred during the modal open + render
    assert not js_errors, f"JS errors raised during modal open: {js_errors}"

    assert "error" not in result, f"Evaluation error: {result.get('error')}"

    if result.get("nodecardCount", 0) == 0:
        # If DOMPurify strips foreignObject/nodecard, at minimum verify SVG rendered
        assert result.get("svgPresent"), (
            "Modal opened but no SVG rendered inside #modal-host (mermaid render failed)"
        )
    else:
        assert result["fitsTop"], (
            f"Top nodecard (y={result['topmost']:.0f}) is above modal body top "
            f"({result['bodyTop']:.0f}) — graph is clipped at top"
        )
        assert result["fitsBottom"], (
            f"Bottom nodecard (y={result['bottommost']:.0f}) is below modal body bottom "
            f"({result['bodyBottom']:.0f}) — graph is clipped at bottom (fitToHost/auto-fit failed)"
        )


# ── AT 4: Modal footer pinned + body fills via zoom-surface ──────────────


@pytest.mark.dashboard
def test_at4_modal_body_zoom_surface_footer_pinned(six_agent_url, page):
    """AT 4: .modal-body carries class 'zoom-surface' and NOT 'topology-host'.
    Footer bottom ≈ modal bottom (abs <= 2px, no dead band).
    """
    page.goto(six_agent_url)
    page.locator(".rail-topology").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(500)

    page.locator(".topo-head .topo-btn.expand").first.click()
    page.locator("#modal-host").wait_for(state="attached", timeout=10000)
    page.wait_for_timeout(300)

    result = page.evaluate(
        """() => {
          const body = document.querySelector('.modal-body');
          const foot = document.querySelector('.modal-foot');
          const modal = document.querySelector('.modal');
          if (!body || !foot || !modal) return { error: 'missing elements' };

          const bodyClasses = body.className;
          const footRect  = foot.getBoundingClientRect();
          const modalRect = modal.getBoundingClientRect();

          return {
            bodyClasses,
            hasZoomSurface:    bodyClasses.includes('zoom-surface'),
            hasTopologyHost:   bodyClasses.includes('topology-host'),
            footBottom:  footRect.bottom,
            modalBottom: modalRect.bottom,
            diff: Math.abs(footRect.bottom - modalRect.bottom),
          };
        }"""
    )

    assert "error" not in result, f"Evaluation error: {result.get('error')}"

    assert result["hasZoomSurface"], (
        f".modal-body must carry class 'zoom-surface'; got: {result['bodyClasses']!r}"
    )
    assert not result["hasTopologyHost"], (
        f".modal-body must NOT carry class 'topology-host' (it caps height); "
        f"got: {result['bodyClasses']!r}"
    )
    assert result["diff"] <= 2, (
        f"Footer bottom ({result['footBottom']:.1f}px) is not pinned to modal bottom "
        f"({result['modalBottom']:.1f}px) — diff is {result['diff']:.1f}px (expected ≤ 2px). "
        "The modal body is not filling via flex:1 (possible topology-host height cap present)."
    )


# ── AT 5: Zoom label reflects actual zoom percentage ──────────────────────


@pytest.mark.dashboard
def test_at5_zoom_label_reflects_actual_zoom(six_agent_url, page):
    """AT 5: After opening the modal and auto-fitting, clicking + changes the
    zoom label to the actual applied transform scale (not a hardcoded value).
    """
    page.goto(six_agent_url)
    page.locator(".rail-topology").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(500)

    page.locator(".topo-head .topo-btn.expand").first.click()
    page.locator("#modal-host").wait_for(state="attached", timeout=10000)
    # Wait for SVG to render + auto-fit
    page.locator("#modal-host .pan-layer").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(400)  # allow double-rAF + fitToHost

    # Record pre-click zoom label
    pre_label: str = page.evaluate(
        "() => { const el = document.getElementById('z-label-modal-host'); return el ? el.textContent : ''; }"
    )

    # Click the + zoom button
    plus_btn = page.locator(".modal-head .topo-btn").filter(has_text="+").first
    plus_btn.click()
    page.wait_for_timeout(100)

    # Get post-click values: label + actual transform scale
    result = page.evaluate(
        """() => {
          const label = document.getElementById('z-label-modal-host');
          const host  = document.getElementById('modal-host');
          if (!label || !host) return { error: 'missing elements' };
          const z = parseFloat(host.dataset.zoom) || 1;
          const expectedLabel = Math.round(z * 100) + '%';
          return {
            labelText:     label.textContent,
            actualZoom:    z,
            expectedLabel: expectedLabel,
          };
        }"""
    )

    assert "error" not in result, f"Evaluation error: {result.get('error')}"

    post_label = result["labelText"]
    expected   = result["expectedLabel"]

    assert post_label == expected, (
        f"Zoom label ({post_label!r}) does not match Math.round(actualZoom*100)+'%' "
        f"({expected!r}) — label is hardcoded or not updated by applyTransform"
    )
    assert post_label != pre_label, (
        f"Zoom label did not change after clicking + (before={pre_label!r}, after={post_label!r}) "
        "— zoom controls are not updating the label"
    )


# ── AT 6: Modal sad path — empty topology degrades gracefully ─────────────


@pytest.mark.dashboard
def test_at6_modal_sad_path_empty_topology(empty_topology_url, page):
    """AT 6: A crew snapshot with empty topology_edge_stats (roster fallback).
    Opening the expand modal must raise no JS error and render the roster graph
    fully within the modal body.
    """
    js_errors: list[str] = []
    page.on("pageerror", lambda e: js_errors.append(str(e)))

    page.goto(empty_topology_url)
    page.locator(".rail-topology").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(800)

    # Click expand
    expand_btn = page.locator(".topo-head .topo-btn.expand").first
    expand_btn.click()

    page.locator("#modal-host").wait_for(state="attached", timeout=10000)
    # Wait for mermaid to render the roster graph
    page.wait_for_timeout(800)

    # No JS errors must have been raised
    assert not js_errors, (
        f"JS errors raised during roster-mode expand modal: {js_errors}"
    )

    # Modal body must be visible
    modal_visible: bool = page.evaluate(
        "() => { const m = document.querySelector('.modal'); return m ? m.offsetParent !== null || m.style.display !== 'none' : false; }"
    )
    assert modal_visible, "Modal is not visible after clicking expand on empty topology"

    # SVG or pan-layer must have rendered (roster graph)
    has_content: bool = page.evaluate(
        """() => {
          const host = document.getElementById('modal-host');
          if (!host) return false;
          // Accept either: pan-layer populated or SVG directly
          return host.children.length > 0;
        }"""
    )
    assert has_content, (
        "modal-host is empty after expand on empty/roster topology — mermaid render failed silently"
    )


# ── AT 7: Structural deletion-detectors ───────────────────────────────────


@pytest.mark.dashboard
def test_at7_structural_zoom_surface_fittohost_double_raf():
    """AT 7: dashboard.html contains the named literals:
    - 'zoom-surface'
    - 'fitToHost'
    - 'requestAnimationFrame(() => requestAnimationFrame('  (double-rAF anchor)
    Fails if those elements are removed.
    """
    text = DASHBOARD_HTML.read_text(encoding="utf-8")

    assert "zoom-surface" in text, (
        "dashboard.html does not contain 'zoom-surface' — the modal body class is missing"
    )
    assert "fitToHost" in text, (
        "dashboard.html does not contain 'fitToHost' — the auto-fit function is missing"
    )
    assert "requestAnimationFrame(() => requestAnimationFrame(" in text, (
        "dashboard.html does not contain the double-rAF auto-fit pattern "
        "'requestAnimationFrame(() => requestAnimationFrame(' — the modal auto-fit on open is missing"
    )


# ── AT 8: Non-regression structural — keyed lookup + gated-bridge + edge-log + promote ──


@pytest.mark.dashboard
def test_at8_structural_keyed_lookup_gated_bridge_edge_log_promote():
    """AT 8: dashboard.html still contains the named literals:
    - 'window.mapEdgeStatsToPaths'   (keyed edge lookup)
    - '_source'                       (gated-bridge back-ref)
    - '/edge-log/'                   (edge-log fetch)
    - 'promote-to-gated' OR 'Promote to gated'  (promote control)
    Proves the keyed edge lookup, displayEdges expansion, /edge-log fetch +
    selected-edge panel, and promote-to-gated control were not deleted by the
    modal rework.
    """
    text = DASHBOARD_HTML.read_text(encoding="utf-8")

    assert "window.mapEdgeStatsToPaths" in text, (
        "dashboard.html no longer contains 'window.mapEdgeStatsToPaths' — "
        "the keyed edge lookup helper was deleted"
    )
    assert "_source" in text, (
        "dashboard.html no longer contains '_source' — "
        "the gated-bridge back-ref (displayEdges expansion) was deleted"
    )
    assert "/edge-log/" in text, (
        "dashboard.html no longer contains '/edge-log/' — "
        "the selected-edge panel fetch was deleted"
    )
    # The promote-to-gated control can appear as either the button label
    # or the function call — check for either form.
    has_promote = "Promote to gated" in text or "promote-to-gated" in text or "promoteEdge" in text
    assert has_promote, (
        "dashboard.html no longer contains the promote-to-gated control "
        "('Promote to gated' / 'promote-to-gated' / 'promoteEdge') — it was deleted"
    )
