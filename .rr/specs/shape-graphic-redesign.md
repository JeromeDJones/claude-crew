# Spec: shape-graphic-redesign

## Problem

The "shape graphic" in claude-crew's Mission Control dashboard is too cramped to be
legible. Two surfaces render a crew topology / workflow shape today — the live in-rail
`TopologyGraph` (jammed into a static ~240px host) and the shape-gate proposal DAG
(capped at `maxWidth: 420px` and rendered with a different visual idiom) — and neither
gives the operator room to comprehend the whole shape at non-trivial crew sizes.
Additionally, at narrower desktop widths (~1024px) the fixed 320px left rail dominates
the layout. The operator needs a unified, roomier shape graphic with an
expand-to-fullscreen zoom/pan modal shared by both surfaces, a single node-and-edge
visual language across live and proposal views, and light-touch responsive behavior at
1024px that keeps the two-pane layout. The approved design is fully specified by the
committed mockup `doc/design/mockups/shape-graphic-redesign-mock.html`; this spec
translates that mockup into `claude_crew/ui/dashboard.html` (single file, no build step)
plus the one backend payload change unification requires.

## Architecture Overview

The change is localized to the dashboard view layer plus one additive backend field:

- **`claude_crew/ui/dashboard.html`** (single-file React + Babel-standalone + mermaid
  11.4.1, light theme) — the entire shape graphic lives here:
  - `TopologyGraph` (~1612–2024): the live in-rail topology, its edge-decoration
    `useEffect` (~1770–1865), `window.mapEdgeStatsToPaths` (~1981, the keyed edge lookup),
    the `displayEdges` gated-bridge expansion memo, and the `/edge-log/<crew>/<from>/<to>`
    fetch + selected-edge panel + promote-to-gated control.
  - `ShapeProposalCard` (~2631–2726) and `ShapeGatePanel` (~2730–2774): the shape-gate
    proposal render (currently consumes the server `proposal.mermaid` string at ~2681,
    static diagram capped `maxWidth: 420` at ~2678).
  - `MissionControlLayout` grid (~2906): the hardcoded
    `gridTemplateColumns: "320px minmax(0, 1fr)"`.
  - `mermaid.initialize({ securityLevel: 'strict', ... })` (~540) and the DOMPurify
    `sanitize` config (~1779 region) — XSS hardening that MUST be preserved.
- **`claude_crew/ui_server.py`** — the `/api/state` shape-proposal serialization
  (`shape_proposals[...]`, currently emits `shape_id, crew_id, status, adaptation_diff,
  mermaid, name, summary`). One additive key (`shape: shape_to_dict(p.shape)`) puts the
  structured nodes/edges/modes on the wire so the dashboard can render the proposal in the
  unified node language client-side.
- **`claude_crew/shapes.py`** — `shape_to_dict(shape)` already exists (JSON-ready inverse
  of `parse_shape`, serializes tuples→lists, preserves edge `mode`/`reverse_mode`). No
  change required; it is reused by `ui_server.py`.

The new shared zoom/pan modal, the roomier in-rail host, the `shapeToMermaidUnified`
client helper, the `.nodecard.proposed` variant, and the responsive `.dash-grid` class
all live inside `dashboard.html`, faithful to the mockup's `.zoom-surface` / `.modal` /
`fitToHost` / `applyTransform` / `bindPanZoom` / `openTopologyModal` / `openProposalModal`
/ `renderInto` implementations.

### Call-site survey

The shared zoom/pan modal has two structurally-distinct entry points; naming them up
front prevents a "one modal, silently forked" shape at code-write time.

| Call-site | Path | Shape | Notes |
|-----------|------|-------|-------|
| Live topology expand | `dashboard.html` `TopologyGraph` → `openTopologyModal` | renders live `mermaidSrc`; edges decorated from `topology_edge_stats` via `window.mapEdgeStatsToPaths`; clickable edges open `/edge-log`; tripped state possible | already-rendered live data; reuses keyed lookup + gated-bridge `displayEdges` unchanged |
| Proposal expand | `dashboard.html` `ShapeProposalCard` → `openProposalModal` | renders `shapeToMermaidUnified(proposal.shape, {proposed:true})`; edges colored by declared `mode` only (no tripped); `.nodecard.proposed` dashed nodes; no per-edge `/edge-log` | structured shape from the new `/api/state` `shape` key |

Resolution: **one shared zoom/pan substrate, two thin openers.** `fitToHost`,
`applyTransform`, `bindPanZoom`, the `.modal` / `.zoom-surface` DOM, esc-to-close, and the
auto-fit-on-open (double-`requestAnimationFrame`) are a single shared implementation
(matching the mockup). `openTopologyModal` and `openProposalModal` differ only in the
mermaid source they feed and the edge-decoration mode (live vs. proposed). The proposal
opener does NOT introduce any new per-instance fetch — it consumes the structured shape
already delivered on `/api/state`, preserving the multi-instance LEADER invariant.

## Data / API Contracts

New additive key on each `/api/state` shape-proposal entry (serialized in
`ui_server.py`; `shape_to_dict` already in `shapes.py`):

```
# /api/state -> shape_proposals[]  (existing keys unchanged; one key ADDED)
{
  "shape_id": str,
  "crew_id": str,
  "status": str,                 # pending | approved | declined | timed_out | instantiated
  "adaptation_diff": str | None,
  "mermaid": str,                # UNCHANGED — kept for back-compat; no consumer removed
  "name": str,
  "summary": str,
  "shape": {                     # ADDED — shape_to_dict(p.shape)
    "name": str,
    "description": str,
    "nodes": [ { "slot": str, "role": str, ... } ],
    "edges": [ { "from_slot": str, "to_slot": str, "mode": str, "reverse_mode"?: str } ],
    "phases": [ ... ]
  }
}
```

New client-side helper in `dashboard.html` (true visual unification — option (a)):

```
# shapeToMermaidUnified(shape, opts) -> mermaid `graph TD` source string
#   shape: the structured proposal.shape object above
#   opts.proposed: bool  (true => node labels carry the .nodecard.proposed class)
# Emits the SAME .nodecard foreignObject HTML node labels as the live topology and
# encodes each edge's `mode` so the post-render SVG-walk decoration colors paths with
# the same --edge-* tokens the live topology uses. Renders through the existing
# mermaid securityLevel:'strict' + DOMPurify pipeline — no new renderer, no relaxed
# sanitize config.
```

Shared zoom/pan modal substrate in `dashboard.html` (faithful to the mockup):

```
# fitToHost(hostEl)        measures SVG at scale=1, computes largest zoom that fits
#                          (w-24, h-24); floor 0.05 (NOT the old 0.3) so a large crew
#                          fits inside the modal body; writes dataset.zoom/tx/ty;
#                          applyTransform.
# applyTransform(hostEl)   transform: translate(tx,ty) scale(z) on .pan-layer; updates
#                          the zoom %-label to Math.round(z*100)+'%' (ACTUAL zoom).
# bindPanZoom(hostEl)      wheel=zoom (clamp 0.25..4), drag=pan; no-anim during gesture.
# renderInto(host,src,mode) render mermaid -> wrap SVG in .pan-layer -> decorate edges by
#                          mode -> auto-fit via requestAnimationFrame(() =>
#                          requestAnimationFrame(() => fitToHost(host))) (double-rAF so the
#                          modal flex layout settles before fit is computed).
# .modal-body uses class .zoom-surface (flex:1 fills the modal) and MUST NOT carry the
#   in-rail .topology-host height cap (the 340px-stuck-in-754px-modal bug).
```

## Design Decisions

- **Unify via option (a): client-side `shapeToMermaidUnified` fed by structured shape on the wire** — *Rationale:* the proposal currently renders a server `proposal.mermaid` string in a different idiom; rendering both surfaces from the same `.nodecard` HTML labels + `--edge-*` tokens client-side is the only path to true visual unification. Investigation confirmed `shape_to_dict` already exists and is JSON-ready, so adding the structured `shape` to the payload is a one-line additive change, not new serialization machinery. — *Carried into:* `/api/state` `shape_proposals[].shape` key (AT 9, AT 10); `shapeToMermaidUnified` helper (AT 11, AT 13).
- **One shared zoom/pan modal substrate, two thin openers** — *Rationale:* the live topology and proposal must look and behave identically when expanded; a single `fitToHost`/`applyTransform`/`bindPanZoom`/`.modal`/`.zoom-surface` implementation prevents drift. — *Carried into:* `openTopologyModal` / `openProposalModal` (AT 3, AT 14); `zoom-surface` structural guard (AT 7).
- **`.modal-body` uses `.zoom-surface`, never `.topology-host`** — *Rationale:* the in-rail height cap (max 340px) stuck the modal body at 340px inside a 754px modal, unpinned the footer, and clipped the graph; `.zoom-surface` lets `flex:1` fill the modal. — *Carried into:* modal-fits-whole-graph + footer-pinned tests (AT 3, AT 4); `zoom-surface` structural guard (AT 7).
- **Drop the `fitToHost` floor to 0.05 and auto-fit on open via double-rAF** — *Rationale:* a large crew must fit inside the modal on open; the old 0.3 floor prevented full fit, and a single rAF reads the host before its flex height settles. — *Carried into:* large-crew-fits test (AT 3); double-rAF structural guard (AT 7).
- **Zoom %-label reflects the ACTUAL zoom** — *Rationale:* a hardcoded label lies after wheel/fit; `applyTransform` computes `Math.round(z*100)+'%'`. — *Carried into:* zoom-label test (AT 5).
- **Roomier in-rail host that grows with crew size (220→340px) + a `topo-head` control row** — *Rationale:* the static 240px box is the cramp; the host should grow `clamp(220, 240 + 18·max(0, agents−3), 340)` and gain a header row (title + subtitle + −/fit/+ + expand). — *Carried into:* roomier-rail test (AT 1); `topo-head` structural guard (AT 2).
- **Proposal card becomes a preview thumbnail that opens the unified modal** — *Rationale:* replace the in-place 420px side-by-side diagram with a small preview tile that opens the shared zoom modal on click, matching the live topology's expand affordance. — *Carried into:* preview-opens-modal test (AT 14, asserts no static `maxWidth: 420` diagram).
- **`.nodecard.proposed` dashed-accent variant for not-yet-instantiated state** — *Rationale:* the proposal should read as a future state of the topology, distinguished only by a dashed accent border. — *Carried into:* proposed-variant test (AT 12); structural guard (AT 13).
- **Light responsive `.dash-grid` class at 1024px; two-pane preserved** — *Rationale:* replace the hardcoded `320px minmax(0,1fr)` with a class whose columns become `clamp(220px, 22vw, 260px) minmax(0,1fr)` under `@media (max-width:1024px)`; no drawer, no single-column. — *Carried into:* responsive tests (AT 16, AT 17); `dash-grid` + removed-hardcoded-grid structural guard (AT 18).
- **Preserve all load-bearing routing/edge logic byte-compatibly** — *Rationale:* `window.mapEdgeStatsToPaths` keyed lookup, `displayEdges` gated-bridge expansion (`_source` back-refs), the `/edge-log` fetch + selected-edge panel + promote-to-gated control, and the edge-decoration `useEffect` (status border + 1.6s pulse + per-mode stroke) must not regress; the modal reuses them on the same rendered SVG regardless of pan/zoom transform. — *Carried into:* non-regression structural guard (AT 8); existing `tests/test_unified_topology_keyed_lookup.py` AT-5/AT-6 stay green.
- **Preserve mermaid `securityLevel: 'strict'` + DOMPurify sanitize config** — *Rationale:* the new client render path must not relax XSS hardening. — *Carried into:* XSS non-regression guard (AT 15); existing `tests/dashboard/test_dashboard_artifact_xss.py` stays green.
- **Keep `proposal.mermaid` on the wire (additive `shape`, not a replacement)** — *Rationale:* no consumer of the existing `mermaid` string is removed; the new `shape` key is purely additive, so any reader (chat `list_pending_shapes`, back-compat) is unaffected. — *Carried into:* payload contract (AT 9 asserts both `mermaid` and `shape` present).

## Edge Cases

- **Empty / roster topology (no `topology_edge_stats`)** — the rail renders the existing
  `graph LR` roster star ("roster — no shape instantiated"); opening the expand modal must
  render that roster graph fully within the body without throwing (AT 6).
- **Single-node shape, no edges** — `shapeToMermaidUnified` emits one `.nodecard` and no
  edges; modal fits and the footer stays pinned.
- **Large crew (8+ nodes)** — host grows to its 340px cap in-rail; the modal auto-fit
  (floor 0.05) shrinks the graph so the top-most and bottom-most nodes both fall inside
  the modal body (AT 3).
- **Gated edges in a proposal** — declared `mode: "gated"` colors amber via `--edge-gated`;
  the proposal view shows declared modes only (no `tripped`/red state, which is a runtime
  circuit-breaker concept absent pre-instantiation).
- **Edge with `reverse_mode`** — `shape_to_dict` preserves it; the proposal render colors
  the forward edge by `mode` (reverse-mode rendering is out of scope — not in the mockup).
- **Zoom/pan during render** — `no-anim` is applied during wheel/drag; auto-fit fires once
  on open via double-rAF after layout settles.
- **Multi-instance LEADER** — the proposal modal introduces NO new per-instance fetch; it
  reads the structured `shape` already aggregated onto `/api/state` and the existing
  `crew_id`-keyed `/edge-log`. No new endpoint that could 404 under follower aggregation.

**If this feature affects displayed data, answer these:**
- *Data absent (no topology):* roster star with "roster — no shape instantiated"; expand
  modal still opens and fits the roster graph.
- *Data expired / capped:* not applicable — the shape graphic renders current state; a
  `tripped` edge (runtime) renders red via `--edge-tripped` in the live view only.
- *Zero vs. missing:* a crew with zero teammates shows the roster fallback; a proposal with
  zero edges shows nodes only (no edge paths) — both render without error.

**If this feature retires, expires, or caps data, answer these:**
- *Consumers of `proposal.mermaid`:* the dashboard `ShapeProposalCard` (was the sole
  renderer) and the chat-channel `list_pending_shapes` tool (`server.py`, returns
  `mermaid`). The dashboard switches to rendering from the new `shape` key; the `mermaid`
  key is RETAINED on the wire so `list_pending_shapes` is unaffected. No consumer loses a
  field.
- *Per-consumer behavior on capped/expired record:* not applicable — no record is
  capped/expired by this feature.
- *Filtering/aggregation assuming live records:* the leader's `/api/state` aggregation
  merges `shape_proposals` across instances unchanged; the added `shape` key flows through
  the same aggregation path as the existing keys.

## Acceptance Tests

All Playwright tests follow the existing harness (`tests/conftest.py` `_spin_dashboard` /
free-port + `BrokerSnapshot` builder + `page.goto` + `page.evaluate`) and carry
`@pytest.mark.dashboard`. "Structural" tests are green-suite deletion-detectors that grep
the artifact and need no browser. Computed-stroke assertions mirror AT-5 in
`tests/test_unified_topology_keyed_lookup.py` (read `path.style.stroke`, compare to the
`var(--edge-*)` token string).

1. **Roomier in-rail topology + topo-head controls** — Given a 6-teammate crew snapshot
   with `topology_edge_stats`, when the dashboard renders, then a `.topo-head` row exists
   above the topology host containing three zoom controls (−/fit/+) and an expand button,
   and the `.topology-host` rendered height is `> 240px` and `<= 340px`.
2. **topo-head structural deletion-detector** — `dashboard.html` contains the NAMED LITERAL
   `topo-head` and the modal opener `openTopologyModal`. (grep; fails if the redesign is
   absent.)
3. **Topology expand modal fits the whole graph, including a large crew** — Given an
   8-teammate crew, when the expand button is clicked, then the modal opens and the
   bounding rects of the top-most and bottom-most node cards both fall within
   `[modalBody.top, modalBody.bottom]` (the whole graph is visible after auto-fit).
4. **Modal footer pinned + body fills via zoom-surface** — Given the topology modal open,
   then `.modal-body` carries class `zoom-surface` and NOT `topology-host`, and the footer
   bottom is approximately the modal bottom (`abs(foot.bottom - modal.bottom) <= 2px`, no
   dead band).
5. **Zoom label reflects the actual zoom percentage** — Given the modal open and
   auto-fitted, when the `+` zoom control is clicked, then the zoom label text equals
   `Math.round(actualZoom*100)+'%'` for the actual applied transform scale and is not equal
   to its pre-click value (not hardcoded).
6. **Modal sad path: empty/roster topology degrades gracefully** — Given a crew snapshot
   with empty `topology_edge_stats` (roster fallback), when the expand modal is opened, then
   no JS error is raised and the roster graph renders fully within the modal body.
7. **zoom-surface + double-rAF structural deletion-detector** — `dashboard.html` contains
   the NAMED LITERALs `zoom-surface`, `fitToHost`, and the double-`requestAnimationFrame`
   auto-fit anchor `requestAnimationFrame(() => requestAnimationFrame(`. (grep.)
8. **Non-regression structural: keyed lookup + gated-bridge preserved** — `dashboard.html`
   still contains the NAMED LITERALs `window.mapEdgeStatsToPaths` and the gated-bridge
   `_source` back-ref token, proving the keyed edge lookup and `displayEdges` expansion were
   not deleted by the redesign. (grep.)
9. **Proposal payload carries structured shape with edge modes** — Given a `BrokerSnapshot`
   with one pending shape proposal whose shape has a `gated` edge, when `/api/state` is
   fetched, then `shape_proposals[0]` contains both `mermaid` (unchanged) and a `shape`
   object whose `nodes` is non-empty and whose `edges[0]` carries a `mode` field equal to
   `"gated"`. (pytest backend, no browser.)
10. **shape_to_dict wired in proposal serialization structural** — `claude_crew/ui_server.py`
    references the NAMED LITERAL `shape_to_dict` within the shape-proposal serialization.
    (grep; fails if the structured-shape payload wiring is removed.)
11. **Unified language: proposal and live share `--edge-*` stroke tokens** — Given a live
    topology with a `direct` edge AND a proposal whose shape has a `gated` edge, when each is
    rendered, then the live `direct` path's `style.stroke` equals `var(--edge-direct)` and
    the proposal `gated` path's `style.stroke` equals `var(--edge-gated)` — both surfaces use
    the same `--edge-*` tokens (AT-5-style deletion-detector).
12. **Proposed variant: proposal nodes carry `.nodecard.proposed`** — Given a pending
    proposal rendered in the proposal modal, then the proposal node cards carry both the
    `nodecard` and `proposed` classes and their computed `border-style` is `dashed`.
13. **shapeToMermaidUnified + nodecard.proposed structural deletion-detector** —
    `dashboard.html` contains the NAMED LITERALs `shapeToMermaidUnified` and
    `.nodecard.proposed`. (grep.)
14. **Proposal card is a preview thumbnail that opens the unified modal** — Given a pending
    proposal, then the `ShapeProposalCard` renders a small preview (the static side-by-side
    diagram with inline `maxWidth: 420` is gone — assert that NAMED LITERAL `maxWidth: 420`
    is absent from the proposal card region), and clicking the preview opens the shared
    zoom/pan modal (`.modal` with a `.zoom-surface` body becomes visible).
15. **Non-regression: XSS securityLevel strict + DOMPurify preserved** — `dashboard.html`
    still contains the NAMED LITERAL `securityLevel: 'strict'`, and the existing XSS suite
    `tests/dashboard/test_dashboard_artifact_xss.py` passes unchanged (the new
    `shapeToMermaidUnified` render path does not relax sanitization).
16. **Responsive ≤1024px: rail clamped, two-pane preserved** — Given the dashboard at
    viewport width 1024, then the left rail column computed width is `<= 260px` and the main
    pane element is still present (the two-pane grid is preserved, not collapsed to a single
    column or drawer).
17. **Responsive ≥1440px: rail is wider** — Given the dashboard at viewport width 1440, then
    the left rail column computed width is `> 260px` (the default `320px` track, not the
    clamped narrow track).
18. **dash-grid + media-query clamp structural; hardcoded grid removed** —
    `dashboard.html` contains the NAMED LITERALs `dash-grid` and
    `clamp(220px, 22vw, 260px)` AND no longer contains the old hardcoded inline grid string
    `320px minmax(0, 1fr)`. (grep deletion-detector for the swap.)

## Test Command

Prerequisites (the Playwright dashboard tests need a Chromium binary; all imported
packages — `pytest`, `pytest-playwright>=0.7.2`, `starlette`/`uvicorn` — are already in
`pyproject.toml`):

```bash
uv sync
uv run playwright install chromium
```

Single command that exercises every acceptance test (the full dashboard suite via the
`dashboard` marker, plus the non-browser backend payload test; default-skipped live SDK
tests are unaffected):

```bash
uv run pytest -m dashboard && uv run pytest tests/test_shape_proposal_payload.py
```

## Out of Scope

- No dark theme. No mobile/phone single-column layout. No drawer-collapse rail.
- No restyle of the roster rows, agent columns, top bar, instance strip, or artifact
  drawer beyond the responsive grid clamp's strict requirements.
- No changes to broker/server message-routing semantics (M2 edge routing, circuit breaker)
  beyond adding the structured `shape` key to the proposal payload.
- No new runtime dependency and no new graph/visualization library — stay on the pinned
  mermaid 11.4.1 + existing React/Babel.
- No build step; `dashboard.html` stays a single file.
- No removal of the existing `proposal.mermaid` payload key (kept for back-compat).
- No rendering of edge `reverse_mode` in the proposal view (not in the approved mockup).
- No server-side `shape_to_mermaid` variant flag (option (b)) — superseded by the
  client-side `shapeToMermaidUnified` decision.

## Assumptions

- **Unification approach is option (a) (client-side render from structured shape)** —
  *Default:* add `shape: shape_to_dict(p.shape)` to the `/api/state` proposal payload and
  render the proposal via a new `shapeToMermaidUnified` client helper. *Rationale:*
  investigation confirmed `shape_to_dict` already exists and is JSON-ready, so the payload
  add is one line; option (a) is the only path to true `.nodecard` visual unification, which
  the idea names as the goal.
- **In-rail host growth formula** — *Default:* `clamp(220, 240 + 18·max(0, agents−3), 340)`
  px, per the mockup notes-drawer. *Rationale:* directly specified by the approved mockup.
- **Auto-fit behavior** — *Default:* auto-fit on shape change and on modal open; manual
  (user-controlled) after first interaction. *Rationale:* the mockup notes-drawer resolves
  this open question this way.
- **New-proposal-while-topology-modal-open behavior** — *Default:* do nothing intrusive; the
  existing `MCTopBar` pending-gate pill already signals the new proposal (no auto-switch, no
  toast added). *Rationale:* the mockup notes-drawer chose the implicit pill path.
- **Narrow-width (≤1024px) rail still renders the topology in-rail** — *Default:* keep the
  topology in the rail with the expand affordance (do not collapse it to a topbar pill).
  *Rationale:* the mockup keeps it in-rail; losing the at-a-glance preview hurts more than
  the squeeze.
- **Selected-edge panel placement at narrow widths** — *Default:* unchanged (stays in-place
  below the host in the rail; the modal foot hosts the edge panel only when an edge is
  clicked from inside the modal). *Rationale:* mockup default.
- **New Playwright test files carry `@pytest.mark.dashboard`** — *Default:* mark every new
  browser test so `uv run pytest -m dashboard` collects it. *Rationale:* matches the
  existing harness convention; the suite-level gate selects by this marker.
- **Backend payload test lives in a new non-browser file
  `tests/test_shape_proposal_payload.py`** — *Default:* a pytest module that builds a
  `BrokerSnapshot` with a proposal and asserts `/api/state` serialization. *Rationale:*
  keeps the non-browser deletion-detector out of the `dashboard` marker set and runnable
  without Chromium.

## Open Questions

- (none)

## Validation

The end-to-end promised outcome — a unified, roomier, expandable shape graphic across both
surfaces plus light responsive behavior, with no regression to routing/XSS — is exercised
by running the full default test suite (dashboard Playwright tests run; live SDK tests
skip by default). Prerequisite: `uv run playwright install chromium` (see Test Command).

```bash
uv run pytest
```

## Task Breakout

```yaml
tasks:
  - name: proposal-payload-structured-shape
    description: |
      Add the structured shape to the /api/state shape-proposal serialization in
      claude_crew/ui_server.py: emit an additive "shape": shape_to_dict(p.shape) key
      alongside the existing keys (mermaid, name, summary, etc. — none removed). Author
      tests/test_shape_proposal_payload.py: a non-browser pytest that builds a
      BrokerSnapshot with one pending proposal whose shape has a gated edge, fetches
      /api/state, and asserts the proposal entry carries both "mermaid" and a "shape"
      object whose edges[0].mode == "gated" (AT 9), plus a grep that ui_server.py
      references shape_to_dict in the serialization (AT 10).
    dependsOn: []
    acceptanceTests: [9, 10]
    taskTouches: ["claude_crew/ui_server.py", "tests/test_shape_proposal_payload.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_shape_proposal_payload.py
  - name: shared-zoom-pan-modal
    description: |
      In claude_crew/ui/dashboard.html, build the shared zoom/pan substrate faithful to
      the mockup (fitToHost with floor 0.05, applyTransform with actual-% zoom label,
      bindPanZoom wheel/drag, renderInto with double-requestAnimationFrame auto-fit, the
      .modal/.zoom-surface DOM, esc-to-close) and the live-topology expand path
      (openTopologyModal). Add the .topo-head control row (title + subtitle + −/fit/+ +
      expand button) above the topology host and grow the host
      clamp(220, 240 + 18*max(0, agents-3), 340)px. The .modal-body MUST use .zoom-surface,
      NOT the .topology-host height cap. Preserve window.mapEdgeStatsToPaths, the
      displayEdges gated-bridge (_source) expansion, the /edge-log fetch + selected-edge
      panel + promote-to-gated control, and the edge-decoration useEffect byte-compatibly.
      First editor of dashboard.html. Author Playwright tests (marked @pytest.mark.dashboard)
      for AT 1,3,4,5,6 and structural deletion-detectors AT 2,7,8.
    dependsOn: []
    acceptanceTests: [1, 2, 3, 4, 5, 6, 7, 8]
    taskTouches: ["claude_crew/ui/dashboard.html", "tests/**"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest -m dashboard tests/test_topology_zoom_modal.py tests/test_unified_topology_keyed_lookup.py
  - name: unified-node-language-proposal
    description: |
      In claude_crew/ui/dashboard.html, add the shapeToMermaidUnified(shape, {proposed})
      client helper that emits the same .nodecard foreignObject labels and --edge-* edge
      colors as the live topology, plus the .nodecard.proposed dashed-accent CSS variant.
      Convert ShapeProposalCard from the static maxWidth:420 side-by-side diagram into a
      preview thumbnail that opens the shared zoom/pan modal (openProposalModal) built by
      shared-zoom-pan-modal, consuming the structured shape delivered by
      proposal-payload-structured-shape. Render through the existing mermaid
      securityLevel:'strict' + DOMPurify pipeline unchanged. Depends on both prior tasks
      (needs the structured payload AND the shared modal; serialized after shared-zoom-pan-modal
      because both edit dashboard.html). Author Playwright tests (marked @pytest.mark.dashboard)
      for AT 11,12,14, structural deletion-detector AT 13, and the XSS non-regression guard
      AT 15 (assert securityLevel:'strict' present + run the existing XSS suite).
    dependsOn: [proposal-payload-structured-shape, shared-zoom-pan-modal]
    acceptanceTests: [11, 12, 13, 14, 15]
    taskTouches: ["claude_crew/ui/dashboard.html", "tests/**"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest -m dashboard tests/test_unified_proposal_language.py tests/dashboard/test_dashboard_artifact_xss.py
  - name: responsive-grid-1024
    description: |
      In claude_crew/ui/dashboard.html, replace the MissionControlLayout hardcoded inline
      gridTemplateColumns "320px minmax(0, 1fr)" with a .dash-grid className whose columns
      become clamp(220px, 22vw, 260px) minmax(0, 1fr) under a @media (max-width:1024px)
      rule (also tightening .nodecard min-width to ~72 and roster padding under the
      breakpoint). Two-pane stays; no drawer, no single-column. Serialized after
      unified-node-language-proposal because it edits the same dashboard.html. Author
      Playwright tests (marked @pytest.mark.dashboard) for AT 16,17 and the structural
      swap deletion-detector AT 18.
    dependsOn: [unified-node-language-proposal]
    acceptanceTests: [16, 17, 18]
    taskTouches: ["claude_crew/ui/dashboard.html", "tests/**"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest -m dashboard tests/test_responsive_grid.py
```

## Design Notes

- The mockup `doc/design/mockups/shape-graphic-redesign-mock.html` is the source of truth;
  its `#notes-drawer` carries the exact component-to-line deltas for `dashboard.html`. The
  implementor should read the mockup's CSS for `.zoom-surface`, `.modal`, `.modal-body`,
  `.modal-foot`, `.nodecard` / `.nodecard.proposed`, `.topo-head`, and the JS for
  `fitToHost` / `applyTransform` / `bindPanZoom` / `openTopologyModal` /
  `openProposalModal` / `renderInto` before writing code.
- Edge mode color tokens (reuse EXACTLY, both surfaces): `--edge-direct #22c55e`,
  `--edge-tee #3b82f6`, `--edge-gated #f59e0b`, `--edge-tripped #ef4444`.
- The edge-decoration `useEffect` operates on the rendered SVG regardless of any pan/zoom
  transform applied to the `.pan-layer` host — pan/zoom is a CSS transform on a wrapper, so
  status border color + 1.6s pulse + per-mode stroke continue to work in both rail and modal.
- Multi-instance LEADER invariant: the proposal modal reuses the structured `shape` already
  on `/api/state` and the existing `crew_id`-keyed `/edge-log` — it must NOT add any new
  per-instance fetch that would 404 under follower aggregation (see CLAUDE.md "Dashboard is
  a multi-instance LEADER").
- The same `dashboard.html` file is edited by three serialized tasks
  (shared-zoom-pan-modal → unified-node-language-proposal → responsive-grid-1024); the
  dependency edges exist solely to serialize same-file writes and to satisfy the structured-
  payload contract dependency. `tests/**` is declared in `taskTouches` for those tasks
  because editing the widely-consumed `dashboard.html` may require collateral selector
  updates in sibling dashboard test files.
