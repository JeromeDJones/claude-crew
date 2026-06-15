"""
AT-5 (AC-3 keyed reciprocal — deletion-detector): DOM-level Playwright test.
    A reciprocal pair (a,b,direct,healthy,8x)+(b,a,direct,tripped,1x) must render
    with independently-keyed strokes — green for a→b, red for b→a.
    A regression that reverts window.mapEdgeStatsToPaths to positional indexing
    would swap the stats, swap the strokes, and fail the stroke-color assertions.

AT-6 (AC-3 keyed-lookup branches): Unit-level page.evaluate() test of all five
    branches of window.mapEdgeStatsToPaths:
      (a) source-order match         (path id resolves directly)
      (b) mermaid-reordered paths    (keyed id survives DOM reorder)
      (c) reciprocal pair            (both directions resolve independently)
      (d) malformed id → LS-/LE-    (class-token fallback)
      (e) both signals fail          (positional last-resort + console.warn)

Run prerequisite: `uv run playwright install chromium` (one-time).
asyncio_mode="auto" (pyproject.toml) — no @pytest.mark.asyncio needed.

Fixture design: each test spins its own UIServer with the reciprocal-pair snapshot
(function-scoped, mirrors test_edge_dashboard.py pattern). The module-scoped
Playwright `page`/`browser` fixtures come from tests/conftest.py — NOT re-declared
here. The stub `/api/state` payload is defined inline; this file does NOT import
`five_agent_url` (module-local to tests/dashboard/test_roster_spotlight.py).
"""
from __future__ import annotations

import asyncio
import re
import socket
import threading
import time
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


# ── Private helpers (mirror of test_edge_dashboard.py; not importable) ───────


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
    crew_id: str = "crew-test",
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
    """Return a Broker whose .snapshot() always yields the given fixture."""
    b = Broker()
    b.snapshot = lambda log_limit=None: snapshot  # type: ignore[method-assign]
    # TopologyGraph reads cli.id as crewId for /edge-log routing.
    b.crew_id = snapshot.crew_id  # type: ignore[misc]
    return b


def _spin_dashboard(broker: Broker):
    """Start a UIServer on a free port in a daemon thread.

    Returns (url, server, thread).  The caller must set ``server.should_exit = True``
    and join the thread at teardown.
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


# ── Fixture: reciprocal-pair dashboard (function-scoped, one server per test) ─


@pytest.fixture
def reciprocal_pair_url():
    """Dashboard serving topology_edge_stats = [
        (a, b, direct, healthy,  8 exchanges),
        (b, a, direct, tripped,  1 exchange),
    ] plus slot_to_teammate = {"a": "tid-1", "b": "tid-2"}.

    Used by both AT-5 (DOM stroke assertions) and AT-6 (unit-level evaluate calls).
    """
    a_info = _alive_info(1, role="sender")
    b_info = _alive_info(2, role="receiver")
    live = (_live_entry(a_info), _live_entry(b_info))
    edges = (
        EdgeStat(from_slot="a", to_slot="b", mode="direct", exchanges=8, tripped=False),
        EdgeStat(from_slot="b", to_slot="a", mode="direct", exchanges=1, tripped=True),
    )
    s2t = {"a": "tid-1", "b": "tid-2"}
    snap = _stub_snapshot(
        crew_id="crew-at5at6",
        live=live,
        edge_stats=edges,
        slot_to_teammate=s2t,
    )
    broker = _patched_broker(snap)
    url, server, t = _spin_dashboard(broker)
    yield url
    server.should_exit = True
    t.join(timeout=3)


# ── AT-5 — DOM-level deletion-detector ───────────────────────────────────────


@pytest.mark.dashboard
def test_at5_reciprocal_pair_keyed_stroke_colors(reciprocal_pair_url, page):
    """AT-5 (BC-03 deletion-detector): each path in a reciprocal pair must carry
    its OWN stroke color, resolved by endpoint identity (not positional index).

    Fixture: topology_edge_stats = [(a→b, direct, healthy, 8x),
                                    (b→a, direct, tripped, 1x)].

    Expected DOM state after post-render decoration:
      a→b path  →  stroke = var(--edge-direct)  [green]   width ≠ 3px
      b→a path  →  stroke = var(--edge-tripped) [red]     width = 3px

    Failure contract:
      If `window.mapEdgeStatsToPaths` is reverted to positional indexing, the
      two reciprocal edges swap their EdgeStat assignment.  The a→b path (first
      in DOM, second in edgeStats) would inherit the tripped stat → red stroke.
      That contradicts the assertion below → test fails → regression caught.
    """
    page.goto(reciprocal_pair_url)
    page.locator(".rail-topology svg").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(1000)  # mermaid render + post-render decoration

    # Extract per-path info: id, class attribute, inline stroke, stroke-width.
    paths_info: list[dict] = page.evaluate(
        """() => {
          const paths = document.querySelectorAll('.rail-topology path.flowchart-link');
          return [...paths].map(p => ({
            id:     p.id || '',
            cls:    p.getAttribute('class') || '',
            stroke: p.style.stroke || '',
            width:  p.style.strokeWidth || '',
          }));
        }"""
    )

    assert len(paths_info) == 2, (
        f"Expected exactly 2 flowchart-link paths for a reciprocal pair; "
        f"got {len(paths_info)}: {paths_info!r}"
    )

    # Mirror the production keyed-lookup regex from window.mapEdgeStatsToPaths
    # (id-parse primary, LS-/LE- class fallback).
    # Mermaid v11 emits L_<from>_<to>_<n>; the regex tolerates `-` and `_`.
    def _endpoints(info: dict) -> tuple[str | None, str | None]:
        m = re.match(r"^L[-_](.+?)[-_](.+?)[-_]\d+$", info["id"])
        if m:
            return m.group(1), m.group(2)
        # Fallback: LS-/LE- class tokens (absent in DOMPurify-sanitised output
        # in our env per task 2 build report, but kept for parity with production).
        cls_tokens = info["cls"].split()
        ls = next((c[3:] for c in cls_tokens if c.startswith("LS-")), None)
        le = next((c[3:] for c in cls_tokens if c.startswith("LE-")), None)
        return ls, le

    endpoint_pairs = [_endpoints(p) for p in paths_info]

    # Locate each path by endpoint identity — NOT by position in the DOM.
    ab_info = next(
        (p for p, ep in zip(paths_info, endpoint_pairs) if ep == ("a", "b")), None
    )
    ba_info = next(
        (p for p, ep in zip(paths_info, endpoint_pairs) if ep == ("b", "a")), None
    )

    assert ab_info is not None, (
        f"Could not identify the a→b path by id/LS-LE class; "
        f"endpoint_pairs: {endpoint_pairs!r}; paths: {paths_info!r}"
    )
    assert ba_info is not None, (
        f"Could not identify the b→a path by id/LS-LE class; "
        f"endpoint_pairs: {endpoint_pairs!r}; paths: {paths_info!r}"
    )

    # ── a→b: healthy (8 exchanges, not tripped) → green, NOT 3px ────────────
    assert ab_info["stroke"] == "var(--edge-direct)", (
        f"a→b edge (healthy) must have green stroke 'var(--edge-direct)'; "
        f"got: {ab_info['stroke']!r}. "
        "Regression indicator: keyed lookup may have reverted to positional indexing."
    )
    assert ab_info["width"] != "3px", (
        f"a→b edge (healthy) must NOT be 3px (reserved for tripped); "
        f"got: {ab_info['width']!r}"
    )

    # ── b→a: tripped (1 exchange, tripped=true) → red, 3px ──────────────────
    assert ba_info["stroke"] == "var(--edge-tripped)", (
        f"b→a edge (tripped) must have red stroke 'var(--edge-tripped)'; "
        f"got: {ba_info['stroke']!r}. "
        "Regression indicator: keyed lookup may have reverted to positional indexing."
    )
    assert ba_info["width"] == "3px", (
        f"b→a edge (tripped) must be 3px thick; got: {ba_info['width']!r}"
    )

    # ── Badge labels: "direct 8" and "direct ⚡" must appear in SVG text ─────
    # Labels are set in the mermaid source as `-->|"direct 8"|` etc. and rendered
    # as foreignObject/edgeLabel elements. textContent collects all nested text.
    svg_text: str = page.evaluate(
        """() => {
          const svg = document.querySelector('.rail-topology svg');
          return svg ? svg.textContent : '';
        }"""
    )
    assert "direct 8" in svg_text, (
        f"Badge 'direct 8' not found in SVG text content; "
        f"excerpt: {svg_text[:500]!r}"
    )
    assert "direct ⚡" in svg_text, (
        f"Badge 'direct ⚡' not found in SVG text content; "
        f"excerpt: {svg_text[:500]!r}"
    )


# ── AT-6 — Unit-level all-branches test ──────────────────────────────────────


@pytest.mark.dashboard
def test_at6_map_edge_stats_to_paths_all_branches(reciprocal_pair_url, page):
    """AT-6: window.mapEdgeStatsToPaths covers all five lookup branches.

    Navigates to the same dashboard (so window.mapEdgeStatsToPaths is defined),
    then calls it via page.evaluate() on minimal synthetic SVG elements that
    exercise each branch in isolation.

    Branches tested:
      (a) Source-order match         — path id `L_x_y_0` resolves to x→y stat.
      (b) Mermaid-reordered paths    — paths in reversed DOM order; keyed by id,
                                       not position → correct stat per path.
      (c) Reciprocal pair            — a→b and b→a each get their own stat (no
                                       aliasing of tripped onto the healthy edge).
      (d) Malformed id → LS-/LE-     — id does not match regex; LS-src / LE-dst
                                       class tokens provide the fallback key.
      (e) Both signals fail          — neither id nor classes match; positional
                                       edgeStats[idx] is used and console.warn
                                       fires with a "keyed lookup miss" message.

    Note on LS-/LE- availability: task 2 build report notes these classes are
    absent in DOMPurify-sanitised mermaid output in our production environment.
    Branch (d) therefore synthesises an element with LS-/LE- classes directly
    (bypassing DOMPurify) to exercise the fallback tier independently.
    """
    page.goto(reciprocal_pair_url)
    page.locator(".rail-topology svg").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(500)  # ensure window.mapEdgeStatsToPaths is defined

    results: dict = page.evaluate(
        """() => {
          // Helper: create a minimal SVG containing <path class="flowchart-link">
          // elements with controlled id / extra class tokens.
          function makeSvg(pathDefs) {
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            pathDefs.forEach(({ id, extra }) => {
              const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
              const cls = 'flowchart-link' + (extra ? ' ' + extra : '');
              p.setAttribute('class', cls);
              if (id) p.id = id;
              svg.appendChild(p);
            });
            // Attach to document so querySelectorAll works inside the helper.
            document.body.appendChild(svg);
            return svg;
          }

          // Convert Map<path, EdgeStat> to a JSON-serialisable array (one entry
          // per path in DOM order).
          function mapToList(map, paths) {
            return paths.map(p => {
              const es = map.get(p);
              return es
                ? { from: es.from_slot, to: es.to_slot, tripped: !!es.tripped }
                : null;
            });
          }

          const out = {};

          // ── (a) Source-order match ──────────────────────────────────────────
          // Path ids are in the SAME order as edgeStats.  Id-parse resolves both.
          {
            const svg = makeSvg([{ id: 'L_x_y_0' }, { id: 'L_p_q_0' }]);
            const edges = [
              { from_slot: 'x', to_slot: 'y', mode: 'direct', exchanges: 1, tripped: false },
              { from_slot: 'p', to_slot: 'q', mode: 'direct', exchanges: 2, tripped: false },
            ];
            const map = window.mapEdgeStatsToPaths(svg, edges);
            const paths = [...svg.querySelectorAll('path.flowchart-link')];
            out.a = mapToList(map, paths);
            svg.remove();
          }

          // ── (b) Mermaid-reordered paths ─────────────────────────────────────
          // DOM path order is reversed vs edgeStats order.  Keyed lookup by id
          // still correctly assigns each path its own stat (not its neighbour's).
          {
            const svg = makeSvg([{ id: 'L_p_q_0' }, { id: 'L_x_y_0' }]);  // reversed
            const edges = [
              { from_slot: 'x', to_slot: 'y', mode: 'direct', exchanges: 3, tripped: false },
              { from_slot: 'p', to_slot: 'q', mode: 'direct', exchanges: 4, tripped: false },
            ];
            const map = window.mapEdgeStatsToPaths(svg, edges);
            const paths = [...svg.querySelectorAll('path.flowchart-link')];
            out.b = mapToList(map, paths);
            svg.remove();
          }

          // ── (c) Reciprocal pair ─────────────────────────────────────────────
          // a→b (healthy) and b→a (tripped) each resolve to their OWN stat.
          // Positional indexing would alias the second stat onto both paths.
          {
            const svg = makeSvg([{ id: 'L_a_b_0' }, { id: 'L_b_a_0' }]);
            const edges = [
              { from_slot: 'a', to_slot: 'b', mode: 'direct', exchanges: 8, tripped: false },
              { from_slot: 'b', to_slot: 'a', mode: 'direct', exchanges: 1, tripped: true  },
            ];
            const map = window.mapEdgeStatsToPaths(svg, edges);
            const paths = [...svg.querySelectorAll('path.flowchart-link')];
            out.c = mapToList(map, paths);
            svg.remove();
          }

          // ── (d) Malformed id → LS-/LE- class fallback ──────────────────────
          // The path id does not match /^L[-_](.+?)[-_](.+?)[-_]\\d+$/.
          // LS-src and LE-dst class tokens provide the secondary key.
          // These are injected directly (not through DOMPurify) to exercise the
          // fallback tier independently of production sanitisation behaviour.
          {
            const svg = makeSvg([{ id: 'malformed-id', extra: 'LS-src LE-dst' }]);
            const edges = [
              { from_slot: 'src', to_slot: 'dst', mode: 'direct', exchanges: 5, tripped: false },
            ];
            const map = window.mapEdgeStatsToPaths(svg, edges);
            const paths = [...svg.querySelectorAll('path.flowchart-link')];
            out.d = mapToList(map, paths);
            svg.remove();
          }

          // ── (e) Both signals fail → positional last-resort + console.warn ──
          // No usable id, no LS-/LE- classes.  edgeStats[0] is returned as the
          // positional fallback; console.warn must include "keyed lookup miss"
          // or "positional" so the operator knows the id format changed.
          {
            const warnLog = [];
            const origWarn = console.warn;
            console.warn = (...args) => {
              warnLog.push(args.join(' '));
              origWarn(...args);
            };
            const svg = makeSvg([{ id: 'malformed-id', extra: 'no-ls-or-le-here' }]);
            const edges = [
              { from_slot: 'fallback', to_slot: 'target', mode: 'direct', exchanges: 2, tripped: false },
            ];
            const map = window.mapEdgeStatsToPaths(svg, edges);
            const paths = [...svg.querySelectorAll('path.flowchart-link')];
            console.warn = origWarn;
            out.e = {
              results: mapToList(map, paths),
              warnFired: warnLog.some(
                m => m.includes('keyed lookup miss') || m.includes('positional')
              ),
            };
            svg.remove();
          }

          return out;
        }"""
    )

    # ── (a) source-order: x→y at DOM[0], p→q at DOM[1] ──────────────────────
    assert results["a"][0] == {"from": "x", "to": "y", "tripped": False}, (
        f"AT-6(a) source-order — DOM[0] should resolve to x→y: {results['a'][0]!r}"
    )
    assert results["a"][1] == {"from": "p", "to": "q", "tripped": False}, (
        f"AT-6(a) source-order — DOM[1] should resolve to p→q: {results['a'][1]!r}"
    )

    # ── (b) mermaid-reordered: DOM is [p→q, x→y]; keyed id must flip correctly ─
    assert results["b"][0] == {"from": "p", "to": "q", "tripped": False}, (
        f"AT-6(b) reordered — DOM[0] (L_p_q_0) should resolve to p→q: {results['b'][0]!r}"
    )
    assert results["b"][1] == {"from": "x", "to": "y", "tripped": False}, (
        f"AT-6(b) reordered — DOM[1] (L_x_y_0) should resolve to x→y: {results['b'][1]!r}"
    )

    # ── (c) reciprocal: a→b healthy (not tripped), b→a tripped ───────────────
    assert results["c"][0] == {"from": "a", "to": "b", "tripped": False}, (
        f"AT-6(c) reciprocal — a→b should be healthy: {results['c'][0]!r}"
    )
    assert results["c"][1] == {"from": "b", "to": "a", "tripped": True}, (
        f"AT-6(c) reciprocal — b→a should be tripped: {results['c'][1]!r}"
    )

    # ── (d) LS-/LE- class fallback ────────────────────────────────────────────
    assert results["d"][0] == {"from": "src", "to": "dst", "tripped": False}, (
        f"AT-6(d) LS-/LE- fallback — should resolve src→dst: {results['d'][0]!r}"
    )

    # ── (e) positional last-resort + warn ─────────────────────────────────────
    assert results["e"]["results"][0] == {
        "from": "fallback",
        "to": "target",
        "tripped": False,
    }, (
        f"AT-6(e) positional fallback — should resolve fallback→target: "
        f"{results['e']['results'][0]!r}"
    )
    assert results["e"]["warnFired"], (
        "AT-6(e) console.warn must fire when positional fallback is used "
        "(expected message containing 'keyed lookup miss' or 'positional')"
    )
