## Task

Produce a combined spec+breakout artifact for the following idea. Write it to:
`/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign/.rr/specs/shape-graphic-redesign.md`

The artifact must conform to the schema in `doc/templates/spec-template.md` (read it once before
writing — it lives in the plugin install alongside this prompt). It must include all required
spec sections AND a `## Task Breakout` section with a fenced ```yaml tasks block that decomposes
the spec into a buildable task DAG. One artifact, one file, one review gate.

## Idea

SHAPE-GRAPHIC REDESIGN — claude-crew Mission Control dashboard

GOAL
Improve the "shape graphic" in claude-crew's Mission Control dashboard, and make the
dashboard work better on narrower desktop widths. Two parts:

  1. A better, UNIFIED shape graphic — the mermaid-rendered crew topology / workflow
     shape — across BOTH surfaces that render a shape today: the live in-rail
     TopologyGraph and the shape-gate proposal DAG. The headline problem (Jerome's
     words) is that it is TOO CRAMPED. The headline fix is "room to breathe": an
     expand-to-fullscreen zoom/pan modal so the whole shape is legible at any crew
     size, plus a roomier in-rail rendering. Today the rail topology is jammed in a
     ~240px box and the proposal DAG is capped at maxWidth 420px.

  2. Light-touch responsive behavior at ~1024px: KEEP the two-pane layout, stop the
     fixed 320px left rail from dominating narrow widths. NO drawer-collapse, NO
     single-column, NO mobile/phone layout.

TARGET FILE
claude_crew/ui/dashboard.html — a single-file React (Babel-standalone) + mermaid
11.4.1 dashboard, light theme. No build step. Keep it a single file.

APPROVED DESIGN = THE MOCKUP (source of truth)
The design is already hardened, reviewed, and approved by Jerome. It is fully
specified in a faithful standalone mockup committed in this worktree:
  doc/design/mockups/shape-graphic-redesign-mock.html
The mockup uses the REAL design tokens + real mermaid, so it translates ~1:1 into
dashboard.html. Its notes drawer (the bottom-right "i" button content, in the
#notes-drawer element) documents the exact dashboard.html component-to-line deltas.
The implementor must MATCH the mockup's behavior and visual language. The planner and
implementor should READ the mockup — especially its CSS for .zoom-surface, .modal,
.modal-body, .modal-foot, .nodecard / .nodecard.proposed, and the JS for
fitToHost / applyTransform / bindPanZoom / openTopologyModal / openProposalModal /
renderInto.

THE FOUR DESIGN MOVES (from the approved mockup)
  - Roomier in-rail topology. Add a topo-head row (title + subtitle + zoom controls
    "minus / fit / plus" + an expand button) above the topology host. Host grows with
    crew size (about 220 to 340px; was a static 240). In-place wheel-zoom + drag-pan.
  - Expand-to-modal IS the real fix. ONE shared zoom/pan modal used by BOTH the live
    topology and the proposal DAG. Wheel = zoom, drag = pan, plus/minus/fit buttons,
    esc-to-close, and on-open AUTO-FIT so the WHOLE shape shows immediately. The
    mockup's critical correctness points: the modal body must NOT carry the in-rail
    height-cap class (.topology-host) — it gets its own .zoom-surface class so
    .modal-body flex:1 fills the modal (the bug we fixed: body stuck at 340px inside a
    754px modal, footer not pinned, graph clipped). Drop the fitToHost floor so a large
    crew can fit. Use double-requestAnimationFrame so the flex layout settles before
    fit is computed. The zoom label must reflect the ACTUAL zoom percentage, not a
    hardcoded value.
  - One node language. The proposal DAG uses the SAME .nodecard foreignObject HTML
    labels and the SAME --edge-* mode colors as the live topology, plus a
    .nodecard.proposed variant (dashed accent border) for the not-yet-instantiated
    state. The shape-gate card becomes a small preview/thumbnail that opens the unified
    modal on click, instead of rendering a fixed side-by-side diagram in place.
  - Light responsive at 1024px. Replace the hardcoded grid "320px minmax(0,1fr)" with a
    class whose columns become clamp(220px, 22vw, 260px) minmax(0,1fr) under a
    max-width:1024px media query; .nodecard min-width drops to about 72; roster padding
    tightens. Two-pane stays. NO drawer, NO single-column.

EDGE MODE COLORS (reuse EXACTLY — both surfaces)
  --edge-direct  #22c55e
  --edge-tee     #3b82f6
  --edge-gated   #f59e0b
  --edge-tripped #ef4444

LOAD-BEARING LOGIC THAT MUST BE PRESERVED (do not regress)
  - window.mapEdgeStatsToPaths — the keyed (from_slot,to_slot) edge-stat lookup
    (id-parse primary, LS-/LE- class-token fallback, positional last-resort + warn).
    The deletion-detector Playwright tests (tests/test_unified_topology_keyed_lookup.py
    AT-5 / AT-6) assert this and MUST stay green.
  - displayEdges gated-bridge expansion — each gated peer-to-peer EdgeStat is split into
    two synthetic peer->lead->peer segments carrying _source back-refs; non-gated edges
    stay single. Keep byte-compatible so AT-5/AT-6 reciprocal-pair keying holds.
  - The per-edge /edge-log/<crewId>/<from>/<to> fetch, the selected-edge panel, and the
    promote-to-gated control.
  - The edge-decoration useEffect (status border color + 1.6s pulse + per-mode stroke).
    It runs on the rendered SVG regardless of any pan/zoom transform applied to the host.
  - Multi-instance LEADER awareness (see CLAUDE.md "Dashboard is a multi-instance
    LEADER"): the leader aggregates follower instances; any per-instance lazy-fetch
    endpoint must carry crew_id and proxy to the owning instance. The expand modal must
    NOT introduce a new per-instance fetch that 404s under multi-instance — it reuses
    the topology data already on /api/state and the existing crew_id-keyed /edge-log.

OPEN DESIGN QUESTION (planner to investigate, plan-reviewer to gate)
The proposal currently renders from a SERVER-provided pre-rendered string
(proposal.mermaid, produced by shapes.py shape_to_mermaid). To get the unified
.nodecard.proposed visual and client-side consistency, EITHER:
  (a) generate the proposal's mermaid CLIENT-SIDE via a new shared helper
      (e.g. shapeToMermaidUnified(shape, {proposed:true})) that emits the same nodecard
      labels + --edge-* colors as the live topology — this requires the shape-proposal
      payload on /api/state to carry the STRUCTURED shape (nodes / edges / modes), not
      just the pre-rendered string; OR
  (b) extend server-side shape_to_mermaid in shapes.py to emit the proposed variant.
The planner MUST check what the shape-proposal payload actually contains today before
choosing (read shapes.py shape_to_mermaid, the propose_shape/ShapeProposal surfacing in
server.py / broker.py, and the /api/state proposal serialization in ui_server.py).
Recommendation: prefer (a) for true visual unification IF structured shape data is
already (or cheaply) on the wire; otherwise (b) may be the smaller surface. Whichever
is chosen, the live topology and the proposal MUST end up visually unified. If (a)
requires adding structured shape data to the payload, that is in scope; do it with a
test.

CONSTRAINTS / NON-NEGOTIABLES
  - Match the existing (light) theme tokens already in dashboard.html. Do NOT restyle
    the rest of the dashboard. Scope = the shape graphic + the responsive grid only.
  - Preserve mermaid securityLevel:'strict' + the DOMPurify sanitize config (XSS tests
    in tests/dashboard/test_dashboard_artifact_xss.py must stay green).
  - No new runtime dependencies. Reuse the pinned mermaid 11.4.1 + existing React/Babel.
  - Single file, no build step.

VALIDATION / TESTS (Playwright headless chromium)
Follow the existing harness: tests/test_unified_topology_keyed_lookup.py +
tests/conftest.py — a _spin_dashboard(broker) UIServer on a free port against a fixture
BrokerSnapshot, page.goto, DOM assertions + page.evaluate. New coverage required:
  - Expand modal (BOTH the live-topology modal AND the proposal modal): on open, the
    WHOLE graph fits inside the modal body — the top-most and bottom-most node bounding
    rects fall within [body.top, body.bottom]; the z-label reflects the actual zoom
    percentage; the footer is pinned to the modal bottom (body fills; assert
    foot.bottom is approximately modal.bottom, no dead band). Happy + sad (e.g. a large
    crew still fits; an empty/roster topology degrades gracefully).
  - Unified language: a proposal DAG and a live topology both render .nodecard labels
    with the same class structure and the same --edge-* stroke tokens — assert computed
    stroke colors equal the tokens (deletion-detector style, like AT-5).
  - Responsive: at viewport width <= 1024 the rail column is clamped (<= 260px) and the
    two-pane grid is preserved (main pane present, not collapsed); at width >= 1440 the
    rail is wider. Use page.set_viewport_size.
  - Non-regression: keyed-lookup AT-5/AT-6, gated-bridge displayEdges, and the XSS
    sanitize tests stay green. The validation command must run the FULL dashboard test
    suite (not a keyword-filtered subset) since this touches widely-consumed rendering.

OUT OF SCOPE (do not spend cycles)
  - No dark theme. No mobile/phone single-column. No drawer-collapse rail.
  - No changes to broker/server message-routing semantics (M2 edge-routing) beyond
    possibly adding structured shape data to the proposal payload IF option (a) above is
    chosen.
  - No new graph/visualization library — stay on mermaid 11.4.1.
  - No redesign of the roster rows, agent columns, top bar, instance strip, or artifact
    drawer beyond what the responsive grid clamp strictly requires.

POINTERS FOR THE PLANNER TO READ
  - doc/design/mockups/shape-graphic-redesign-mock.html — the approved design + the
    notes-drawer deltas (component-to-line map for dashboard.html).
  - claude_crew/ui/dashboard.html — TopologyGraph (~1612-2024), ShapeProposalCard
    (~2631-2726), ShapeGatePanel (~2730-2774), MissionControlLayout grid (the
    "320px minmax(0,1fr)" at ~2906), nodecard CSS (~49-89), mermaid.initialize theme
    (~539).
  - claude_crew/shapes.py (shape_to_mermaid), claude_crew/server.py + broker.py
    (propose_shape / ShapeProposal), claude_crew/ui_server.py (/api/state proposal
    serialization) — for the open design question.
  - CLAUDE.md "Dashboard is a multi-instance LEADER" — the multi-instance invariant.
  - tests/test_unified_topology_keyed_lookup.py + tests/conftest.py — the Playwright
    harness pattern to mirror.

## Cycle

Cycle: 0
Prior review report (empty on cycle 0): 

On cycle ≥ 1, read the prior report first. Address every Critical and High finding by name in the revised spec. Medium and Low findings are advisory.

## Repository Context

Repository path: `/home/jerome/dev/claude-crew`

Gather context before writing the spec:
- Read the repository README.
- Scan the top-level directory layout.
- Check `.rr/specs/` for prior specs (if any exist, avoid duplicating their scope).
- **Before claiming any UI/frontend capability is absent, survey BOTH the backend modules
  AND the frontend assets explicitly.** A grep of server-side code alone is not sufficient to
  conclude a UI capability does not exist — rendering pipelines, loaded libraries, and view
  components typically live in frontend files (`*.html`, `*.js`, `*.jsx`, `*.ts`, `*.tsx`,
  `*.vue`, template dirs), not in the server modules. Search the frontend asset files directly
  before writing an "X does not exist" claim into the spec.

### Reference Artifacts

Spec template (read before writing — includes Task Breakout schema in comments):
`/home/jerome/.claude/plugins/cache/repo-reactor/repo-reactor/0.16.1/doc/templates/spec-template.md`

Existing specs in this worktree:
_None._

### Architecture Context

Architecture doc: `doc/ARCHITECTURE.md`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your spec; align your spec with the architecture it
describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/shape-graphic-redesign`

Change to this directory before all file operations.

## Instructions

- The artifact must include all required spec sections: `## Problem`, `## Design Decisions`, `## Edge Cases`, `## Acceptance Tests`, `## Test Command`, `## Out of Scope`, `## Assumptions`, `## Open Questions`, `## Validation`, AND `## Task Breakout`.
- `## Test Command` must contain a non-empty `bash` or `sh` fenced code block with a runnable command.
- `## Task Breakout` must contain a fenced ```yaml block with a `tasks:` list. Every numbered acceptance test must be claimed by exactly one task. Each task needs `name`, `description`, `dependsOn`, `acceptanceTests`, `taskTouches`, and `implementationKind`.
- Scope to the smallest deliverable that satisfies the idea. Defer anything not required.
- Before finalizing `## Test Command`, you **must** be able to name every package the test files import. Cross-check each against the project's dependency manifest (`pyproject.toml`, `package.json`, `Cargo.toml`, etc.). If any import is not in the manifest — or if the tests require system-level setup (browser binaries, running services, env vars, compiled extensions) — state the prerequisite install command in prose above the fenced block. "No prerequisites" is only valid if you have confirmed every import is already in the manifest.
- Run `bin/spec-schema-check.sh` on the artifact before finalizing. Fix every reported gap.
- Write the combined artifact file only. Do not implement any code.
- **Deletion-detection mandate:** For every deliverable whose primary verification is deferred out of the green suite (live-SDK turn, browser/Playwright render, manual check), you MUST author at least one green-suite deletion-detecting acceptance test — a structural test (grep, file-existence check, or import-graph probe) that exits non-zero when the deliverable is absent. An `## Out of Scope` clause may defer a deliverable's behavioral test but may never defer all green-suite coverage of its existence/wiring.
- **Grep-guard anchors must be NAMED LITERALs:** When specifying the anchor string for a deletion-detector guard test, state the exact literal substring the guard greps for — not a description of it. Write `anchor: "deletion-detect"` (the NAMED LITERAL), never `anchor: "the deletion-detect phrase"` or `anchor: (description of what the clause says)`. The NAMED LITERAL appears verbatim in both the clause and the test.
- **AT/Out-of-Scope authoring rules:** For the canonical authoring-rules home on acceptance-test and out-of-scope authoring conventions, see the `plan-feature` SKILL (canonical authoring-rules home for AT/OoS rules).
- **Per-validator negative ATs:** When the spec includes discrete input-validation, schema-enforcement, or permission-gate checks, enumerate a negative AT for each validator check — one AT per check, testing its rejection path in isolation, so a removed or broken check produces a directly-attributable failure. A single combined negative AT that exercises several checks at once does not satisfy this; each gate gets its own AT.
