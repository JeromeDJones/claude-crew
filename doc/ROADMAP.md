# Roadmap: claude-crew

**Created**: 2026-06-13 (harvested from `doc/PRODUCT-VISION.md` Feature Pipeline at M2 retro)
**Last Updated**: 2026-06-29

This roadmap is a condensed view of the Workflow Shape Composition arc. For the full feature pipeline (MVP track, post-MVP substrate, deferred items), see `doc/PRODUCT-VISION.md § Feature Pipeline`.

---

## Workflow Shape Composition Arc

The arc delivers **declarative, human-gated, and live-executing crew graphs** — making claude-crew a composable multi-agent platform rather than a fixed-topology runner.

| Milestone | Status | Shipped | Summary |
|-----------|--------|---------|---------|
| **M0** — Shape as first-class data | ✅ done (2026-06-11) | `shapes.py` (NEW), `broker.py` (+6 methods), `server.py` (+2 tools), `ui_server.py` (+1 route), `dashboard.html` (+2 components) | `propose_shape` + `instantiate_shape`. Crew shapes as frozen dataclasses (`Shape` / `ShapeNode` / `ShapeEdge`). Human approval gate (asyncio.Condition). All-or-nothing pre-flight. `Topology` recorded on `BrokerSnapshot`. Graphical DAG in Mission Control. |
| **M1.5** — Async non-blocking gate | ✅ done (2026-06-12) | `server.py` (+2 tools), `broker.py` (lead-notify choke-point), `ui_server.py` (name/summary on proposals), `dashboard.html` (MCTopBar badge + resurfaceable tray) | `propose_shape` non-blocking by default; dual-channel approval (dashboard + `resolve_shape` chat tool); notify-lead-on-resolve; resurfaceable MCTopBar badge that survives instance-switch. 14→16 tools. |
| **M2** — Routing enforcement | ✅ done (2026-06-13) | `broker.py` (routing engine + circuit breaker + `EdgeStat`), `sdk_teammate.py` (in-process `send_to` MCP), `factories.py` (`neighbors=`), `teammate_prompt.py` (neighbor section), `ui_server.py` (+3 routes), `dashboard.html` (`TopologyEdgePanel`) | Edge modes enforced (`gated`/`tee`/`direct`). Scoped `send_to` in-process MCP tool (moat choke-point). Neighbor injection (same `shape.edges` source as authorization — cannot drift). Budget-only circuit breaker (8 exchanges; no deadlock detector). On-graph overlay + promote control in dashboard. |
| **M3** — Adaptation algebra | ✅ done (2026-06-17) | `claude_crew/shapes.py` (+algebra), `claude_crew/server.py` (+`adapt_shape` tool), `tests/test_shape_adaptation.py` (45, new), `tests/test_shape_adapt_tool.py` (38, new) | Five pure verb commands (`AddNode`/`Swap`/`Augment`/`SetGate`/`Drop`), `AdaptationDiff` + `render()`, `AdaptationChain` provenance carrier, `shape_to_dict` serializer. `adapt_shape` MCP tool reuses M1.5 gate verbatim (`broker.py` untouched). Pre-instantiation only. 35 ATs; 83 feature tests; 1613 full-suite green. |
| **M3.5** — Reshape crew while running | 🔜 next | — | Adapt a LIVE (already-instantiated) topology: kill/respawn teammates on `swap`/`drop`, rewire live routing mid-flight. Touches the broker live registry + M2 routing engine — materially bigger than M3's pure-data algebra. Carved out 2026-06-17. |
| **M1** — Blessed shape library | ↪ reframed → repo-react | — | Blessed shapes + right-sizing/classification is **policy**, now owned by repo-react (it already is the canonical heavy-feature shape + has the tiering escape hatch). claude-crew provides the *mechanism* (M0/M2/M3); repo-react picks/right-sizes. extensible-roles Slice 1 (FDE v0.16.0) shipped the role-pluggability foundation. |
| **M4** — RepoReactor as shape | ⏳ deferred | — | Re-author the RR workflow as a declared shape using M2 routing + M3 adaptation. The convergence point where repo-react's blessed shapes meet claude-crew's shape substrate. |
| **M5** — Memory-informed adaptation | ⏳ deferred | — | Lead-autonomous shape adaptation informed by prior crew memory. |

---

## Next: M3.5 — Reshape Live Crew

Apply adaptation verbs to an already-instantiated topology: `swap`/`drop` kill and respawn running teammates; rewire live routing mid-flight via the M2 routing engine. Materially larger than M3's pure-data algebra — touches the broker live teammate registry, liveness state, and M2 `send_scoped` authorization. Carved out of M3 scope 2026-06-17.

## Shipped: M3 — Adaptation Algebra

Five pure verb commands (`AddNode`/`Swap`/`Augment`/`SetGate`/`Drop`) in `shapes.py` — each `apply(shape) → (Shape, AdaptationDiff)`. `AdaptationDiff.render()` feeds the existing `adaptation_diff: str` broker channel (gate signature unchanged). `adapt_shape` MCP tool reuses M1.5 gate verbatim (`broker.py` untouched). 35 ATs; 83 feature tests; 1613 full-suite green. Shipped 2026-06-17. Spec: `.rr/specs/m3-adaptation-algebra.md`.

> **Reframe (2026-06-17):** the former "M1 — blessed shape library + classifier" is **policy** and now belongs to **repo-react**, not claude-crew. The substrate (M0/M2/M3) provides the mechanism; repo-react picks and right-sizes shapes. The section below is retained for historical context only — superseded by the table above.

### (superseded) M1 — Blessed Shape Library

**Goal**: a `shapes/` library of reusable template shapes + a classifier so the lead can pick the right crew topology by describing the task, not by constructing a shape from scratch.

**Key deliverables:**
- Template files: `shapes/micro-fix.yaml`, `shapes/standard-feature.yaml`, `shapes/heavy-feature.yaml` (and potentially `shapes/rr-feature.yaml`)
- `propose_shape` extended to accept a file path in addition to an inline dict/YAML string
- Lead classifier: given a task description, returns a ranked list of matching templates with a rationale

**Dependencies:** None on M2's routing logic. Can be designed and shipped in parallel with or after M2. Starts from M0's `parse_shape` / `ShapeEdge` / `ShapeNode` data model unchanged.

**Sizing:** M (estimate from product vision). Ready for SDD handoff.

---

## Milestones completed outside this arc

See `doc/PRODUCT-VISION.md § Product Journal` for the full history. Key substrate milestones that underpin the arc:

| Feature | Shipped | Relevance to arc |
|---------|---------|-----------------|
| Multi-instance registry (#13) | 2026-04-30 | Multi-instance shape approval proxy built on this |
| Broker snapshot API (#18) | 2026-04-30 | `BrokerSnapshot` is the carrier for `topology_edge_stats` |
| Teammate death diagnostics | 2026-06-09 | Teammate liveness monitoring runs alongside routed edges |
| Workflow Shape Composition M0 | 2026-06-11 | Arc foundation |
| Async shape gate M1.5 | 2026-06-12 | Gate interaction model |
| Edge routing M2 | 2026-06-13 | Live execution |
| plan-gate-and-telemetry-hardening | 2026-06-17 | Closes silent safety regression (plan-mode write gate now enforced client-side); restores F7 TNM telemetry (arrival-order correlation); adds forced-crash coverage; fixes shutdown-signal flake. Substrate hardening for M3.5 and beyond. |
| shape-graphic-redesign | 2026-06-29 | Unified roomier shape graphic in Mission Control: shared zoom/pan modal (fitToHost/applyTransform/bindPanZoom/renderInto), `shapeToMermaidUnified` for proposal visual parity with live topology, `.topo-head` control row, `clamp`-based in-rail host growth, responsive `.dash-grid` class, additive `shape` key on `/api/state` proposals. |
