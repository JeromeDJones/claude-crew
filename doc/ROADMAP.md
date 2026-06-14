# Roadmap: claude-crew

**Created**: 2026-06-13 (harvested from `doc/PRODUCT-VISION.md` Feature Pipeline at M2 retro)
**Last Updated**: 2026-06-13

This roadmap is a condensed view of the Workflow Shape Composition arc. For the full feature pipeline (MVP track, post-MVP substrate, deferred items), see `doc/PRODUCT-VISION.md § Feature Pipeline`.

---

## Workflow Shape Composition Arc

The arc delivers **declarative, human-gated, and live-executing crew graphs** — making claude-crew a composable multi-agent platform rather than a fixed-topology runner.

| Milestone | Status | Shipped | Summary |
|-----------|--------|---------|---------|
| **M0** — Shape as first-class data | ✅ done (2026-06-11) | `shapes.py` (NEW), `broker.py` (+6 methods), `server.py` (+2 tools), `ui_server.py` (+1 route), `dashboard.html` (+2 components) | `propose_shape` + `instantiate_shape`. Crew shapes as frozen dataclasses (`Shape` / `ShapeNode` / `ShapeEdge`). Human approval gate (asyncio.Condition). All-or-nothing pre-flight. `Topology` recorded on `BrokerSnapshot`. Graphical DAG in Mission Control. |
| **M1.5** — Async non-blocking gate | ✅ done (2026-06-12) | `server.py` (+2 tools), `broker.py` (lead-notify choke-point), `ui_server.py` (name/summary on proposals), `dashboard.html` (MCTopBar badge + resurfaceable tray) | `propose_shape` non-blocking by default; dual-channel approval (dashboard + `resolve_shape` chat tool); notify-lead-on-resolve; resurfaceable MCTopBar badge that survives instance-switch. 14→16 tools. |
| **M2** — Routing enforcement | ✅ done (2026-06-13) | `broker.py` (routing engine + circuit breaker + `EdgeStat`), `sdk_teammate.py` (in-process `send_to` MCP), `factories.py` (`neighbors=`), `teammate_prompt.py` (neighbor section), `ui_server.py` (+3 routes), `dashboard.html` (`TopologyEdgePanel`) | Edge modes enforced (`gated`/`tee`/`direct`). Scoped `send_to` in-process MCP tool (moat choke-point). Neighbor injection (same `shape.edges` source as authorization — cannot drift). Budget-only circuit breaker (8 exchanges; no deadlock detector). On-graph overlay + promote control in dashboard. |
| **M1** — Blessed shape library | 🔜 next | — | Blessed shape library + lead router/classifier. `micro-fix`, `standard-feature`, `heavy-feature` template shapes in a `shapes/` dir. `propose_shape` accepts a file path. Lead classifies a problem and selects a shape. Independent of M2's routing logic. |
| **M3** — Adaptation algebra | ⏳ deferred | — | `add_node` / `swap` / `augment` / `set_gate` / `drop` verbs + structured `adaptation_diff`. Makes shape adaptation computable. |
| **M4** — RepoReactor as shape | ⏳ deferred | — | Re-author the RR workflow as a declared shape using M1 templates + M2 routing enforcement. |
| **M5** — Memory-informed adaptation | ⏳ deferred | — | Lead-autonomous shape adaptation informed by prior crew memory. |

---

## Next: M1 — Blessed Shape Library

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
