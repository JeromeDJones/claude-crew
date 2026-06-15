# Slice Review: unified-topology-view task=unify-topology-graph-component

**Task:** `unify-topology-graph-component` (index 2) · **Cycle:** 1
**Owned acceptance tests:** AT-3, AT-4, AT-7, AT-8
**Prior report:** `...-slice-review-0.md` (one finding: MED-01 gated-through-lead bridge)
**Verdict:** PASS

## Cross-cycle: prior-finding disposition

- **MED-01 (cycle 0) — `slice.adherence.unimplemented-design-decision`: gated edges not routed peer→lead→peer.** → **ADDRESSED.** A new `displayEdges` memo (dashboard.html L1663-1687) expands each gated peer EdgeStat into two synthetic amber segments (`from_slot → lead`, `lead → to_slot`), each carrying the source EdgeStat on `_source`; the flat `from→to` gated edge is no longer emitted. Not a recurrence.

## Rework verification (the four load-bearing points)

1. **Two-segment gated synthesis** ✅ — `displayEdges` (L1665-1685): `mode === 'gated' && endpoints ≠ lead` → pushes `{from, lead}` and `{lead, to}`, both `mode:'gated'`, both with `_source: es`; else passes through. `slots` built from `displayEdges` so `lead` is structurally present. Flat gated edge gone — confirmed by the guard test's "flat edge absent" assertion.
2. **BC-03 keyed lookup UNCHANGED** ✅ — `window.mapEdgeStatsToPaths(svgRoot, edgeStats)` retains exact signature/return; synthesis is caller-side (decoration passes `displayEdges`). For direct/tee reciprocal pairs `displayEdges === edgeStats` (no split), so AT-5/AT-6 reciprocal-direct keying is byte-identical.
3. **Click routes to SOURCE endpoints + crew_id** ✅ — decoration unwraps `_source`; click fetches `/edge-log/${crewId}/${source.from_slot}/${source.to_slot}`; exchange-pulse keyed by source peers (no double-fire). Multi-instance contract intact.
4. **New guard tests assert structurally** ✅ — `test_gated_edge_bridges_through_lead_with_two_amber_segments` (both segments present, flat edge absent, amber stroke) + `test_clicking_gated_segment_fetches_source_endpoints_with_crew_id` (source peers + crew_id; negatively, no `/lead/` fetch). Real deletion-detectors, both passing.

## Slice adherence

All four owned ATs remain satisfied (AT-3 single-graph + lead; AT-4 roster fallback; AT-7 multi-instance; AT-8 activity join). Gated-bridge work delivers the design-decision behavior MED-01 flagged.

## Scope (taskTouches — Invariant 1)

Changed files within declared globs (`dashboard.html`, `test_edge_dashboard.py`, `test_roster_spotlight.py`, `test_dashboard_render.py`). Clean.

## Non-regression

- Slice command → **54 passed, exit 0** (97s). Matches coordinator ground-truth (+2 vs cycle-0).
- Siblings → **60 passed, exit 0**.
- Known `test_shutdown_signals.py` full-suite load flake excluded.

## Code-quality smoke (fresh adversarial pass)

`displayEdges` is `useMemo`-stable; `useEffect` deps include it; pulse-ref keyed by source. No secrets, no swallowed errors, no dead code.

### Critical / High / Medium
_None identified._

### Low
- [LOW-01] `slice.quality.synthetic-key-collision` — `mapEdgeStatsToPaths` keys `byKey` by `from→to`. When two distinct gated edges share a lead-adjacent endpoint (e.g. `planner→impl` and `reviewer→impl`, both gated → two `lead→impl` synthetic segments with identical key `lead→impl`), `byKey.set` overwrites, so both rendered `lead→impl` paths resolve to the last source EdgeStat — a click on one could dispatch `/edge-log` against the other's source peers. New corner introduced by the bridge synthesis; outside owned ATs and below the human-judged AC-7 gated-routing parity. Non-blocking. Fix: disambiguate synthetic keys (suffix by source index). Deferred to feature-review / backlog.

## Findings Disposition

| finding-id | severity | tag | disposition | rationale |
|------------|----------|-----|-------------|-----------|
| MED-01 (cycle 0) | Medium | slice.adherence.unimplemented-design-decision | addressed | Gated-through-lead bridge implemented; two structural guard tests added and passing; flat-edge regression actively detected. |
| LOW-01 | Low | slice.quality.synthetic-key-collision | deferred-with-rationale | Two gated edges sharing a lead-adjacent endpoint collide in the from→to keyed lookup; rare, outside owned ATs, below AC-7. Tracked as backlog debt; non-blocking. |
