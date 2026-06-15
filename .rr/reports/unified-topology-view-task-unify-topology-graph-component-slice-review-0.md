# Slice Review: unified-topology-view task=unify-topology-graph-component

**Task:** `unify-topology-graph-component` (index 2) · **Cycle:** 0
**Owned acceptance tests:** AT-3 (one graph), AT-4 (roster fallback), AT-7 (multi-instance preserved), AT-8 (activity join via slot_to_teammate)
**Verdict:** PASS (reviewer) — but coordinator escalated MED-01 to a rework (see note)

## Slice adherence

All four owned automated ATs satisfied with matching passing tests in the 52-passing slice run:

- **AT-3** — `test_at3_unified_topology_renders_single_graph_with_lead_node`. `MiniGraph`/`TopologyEdgePanel` function defs removed; 5 remaining refs are comments. Mount retargeted to `<TopologyGraph cli={cli} agents={liveAgents}/>`. `lead` declared first-class; header reads `Topology`. ✅
- **AT-4** — `test_at4_roster_fallback_*`. `graph LR`, `lead --> teammate_*` neutral edges (no badge/click), subtitle `roster — no shape instantiated`, legend gated behind `!isRoster`. ✅
- **AT-7** — `test_at7_unified_topology_preserves_crew_id_in_edge_log_path` + multi-instance proxy suite. `/edge-log/${crewId}/...` retains crew_id. ✅
- **AT-8** — `test_at8_activity_joins_via_slot_to_teammate`. Join is `slot → slotToTeammate[slot] → agents.find(id).status`, NOT `role === slot`. Defensive `?? 'idle'`. ✅

## Scope (taskTouches — Invariant 1)

`git diff --name-only HEAD` → dashboard.html, test_edge_dashboard.py, test_roster_spotlight.py, test_dashboard_render.py. All match declared globs; test_dashboard_mermaid.py declared but unmodified (fewer-than-declared is fine). ✅

## Non-regression

- Slice command → **52 passed, exit 0** (90.8s). Matches coordinator ground-truth.
- Sibling task-0/task-1 → **60 passed, exit 0**.
- 2 `test_shutdown_signals.py` full-suite load flakes are documented pre-existing backlog items, outside slice commands — correctly excluded. ✅

## Code-quality smoke

`window.mapEdgeStatsToPaths` (L1968-2010): correct three-tier keyed lookup (tolerant id regex → `LS-`/`LE-` class fallback → positional last-resort + single `console.warn`). Returns `Map<path, EdgeStat>`. Sound BC-03 fix. DOMPurify mirrors `renderMermaidBlocks` (foreignObject allowed, scripts/handlers forbidden). No secrets, no swallowed exceptions, no dead code.

### Medium
- [MED-01] `slice.adherence.unimplemented-design-decision` — active-topology edge generation: spec's *"gated edges route `peer --> lead --> peer`"* and the task description are NOT implemented — every edge drawn as a single `from_slot -->|badge| to_slot`, so the first-class `lead` node renders disconnected in a typical active topology. Not covered by any owned automated AT (AT-3 only requires lead present). Reviewer disposition: deferred to the human AC-7 mockup-parity step.

## Coordinator note (2026-06-14)

MED-01 is the feature's headline behavior (gated-through-lead legibility, approved via mockup). Coordinator verified the root cause: real broker data records gated edges peer-to-peer (lead never an endpoint); the implementor mirrored the mockup's data-faked naive rendering. **Coordinator did NOT defer to AC-7** — directed a cycle-1 rework: split gated edges through lead with the keyed lookup mapping both synthetic segments to the source EdgeStat, plus a structural green-suite guard test. Re-review at cycle 1.
