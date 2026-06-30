# Plan Review: shape-graphic-redesign

**Verdict:** PASS

**Cycle:** 0 (no prior report)

## Summary

A well-scoped, mockup-anchored dashboard redesign translating a committed mockup
into `dashboard.html` plus one additive backend payload key. All required spec
sections are present, clear, and testable. The Task Breakout cleanly partitions
all 18 acceptance tests across 4 tasks with correct same-file serialization and no
spurious edges. Every provenance claim ("existing", "currently emits", "already
exists") was verified against the repository tree and holds. One Low advisory on
non-regression coverage of the `/edge-log` + promote-to-gated control; it does not
block.

## Provenance Verification (all PASS)

| Claim | Verified |
|-------|----------|
| `shape_to_dict` already exists in `shapes.py`, JSON-ready inverse of `parse_shape` | ✓ ARCHITECTURE.md §shapes.py |
| `ui_server.py` proposal block "currently emits shape_id, crew_id, status, adaptation_diff, mermaid, name, summary" | ✓ exact match, `ui_server.py:445-454`; `shape` key genuinely absent (additive) |
| Existing live structures (`TopologyGraph`, `ShapeProposalCard`, `window.mapEdgeStatsToPaths`, `maxWidth: 420`, `320px minmax(0, 1fr)`, `securityLevel: 'strict'`, `_source`) present in `dashboard.html` | ✓ all non-zero counts |
| New literals (`topo-head`, `zoom-surface`, `shapeToMermaidUnified`, `dash-grid`, `openTopologyModal`, `openProposalModal`, `.nodecard.proposed`) not yet present | ✓ all 0 — deletion-detectors will be meaningful |
| Existing tests `test_unified_topology_keyed_lookup.py`, `dashboard/test_dashboard_artifact_xss.py`, mockup `shape-graphic-redesign-mock.html` exist | ✓ |
| `tests/test_shape_proposal_payload.py` to be authored by Task 1 | ✓ correctly absent now |

## Spec Section Review

- **Required sections** — Problem, Architecture Overview, Data/API Contracts,
  Design Decisions, Edge Cases, Acceptance Tests, Test Command, Out of Scope,
  Assumptions, Open Questions, Validation, Task Breakout: all present and concrete.
- **Test Command** — `uv run pytest -m dashboard && uv run pytest tests/test_shape_proposal_payload.py`, gated behind documented `uv sync` + `uv run playwright install chromium` prereqs. Runnable as written once the tasks author the tests; the `dashboard` marker matches the existing harness convention.
- **Architecture alignment** — Consistent with ARCHITECTURE.md: preserves the BC-03 keyed `mapEdgeStatsToPaths` lookup, the `displayEdges` gated-bridge `_source` expansion, `securityLevel:'strict'` + DOMPurify, and the multi-instance LEADER invariant (proposal modal adds NO new per-instance fetch; reuses the already-aggregated `shape` key). The new `shapeToMermaidUnified` emits `.nodecard` foreignObject labels, sidestepping the known `shape_to_mermaid` `\n`-literal defect (ARCHITECTURE.md §shapes.py) — no contradiction.
- **Quantitative consistency** — Host clamp for 6 teammates = `clamp(220, 240+18·3, 340)` = 294px, satisfying AT 1 (`>240 ∧ ≤340`). Responsive: 320px default track (>260, AT 17) vs `clamp(220,22vw,260)`→225px at 1024 (≤260, AT 16). Internally consistent.

## Task Breakout Review

**AT coverage (inverse + forward):** All 18 ATs claimed exactly once, no gaps, no duplicates:
- Task 1 `proposal-payload-structured-shape` → AT 9, 10
- Task 2 `shared-zoom-pan-modal` → AT 1–8
- Task 3 `unified-node-language-proposal` → AT 11–15
- Task 4 `responsive-grid-1024` → AT 16–18

**Dependency edges (all justified, none spurious):**
- Tasks 1 and 2 both `dependsOn: []` and touch disjoint files (`ui_server.py`+new test vs `dashboard.html`) — correctly parallelizable, not falsely serialized.
- Task 3 `dependsOn: [1, 2]` — needs both the structured payload (1) and the shared modal substrate (2); also same-file with 2.
- Task 4 `dependsOn: [3]` — same-file serialization on `dashboard.html`.
- The 2→3→4 chain correctly serializes all `dashboard.html` writers per the "serialize same-file work" rule.

**Mega-task check:** Task 2 carries 8 ATs and the most surface, but it is one cohesive design surface (the shared zoom/pan substrate + live-topology expand path, all in one file region) — the modal cannot be tested without its substrate. Not a mega-task. PASS.

**Concreteness:** Descriptions name specific functions, CSS classes, the clamp formula, edge-mode tokens, claimed ATs, and `taskTouches`. Combined with the committed mockup (explicitly cited as source of truth), an implementor can build without re-deriving the design.

## Findings

### Low

1. `[spec.deliverable.untested]` — Task 2's description includes "Preserve ... the
   `/edge-log` fetch + selected-edge panel + promote-to-gated control ...
   byte-compatibly." AT 8 (the only new non-regression deletion-detector for Task 2)
   greps for `window.mapEdgeStatsToPaths` and the `_source` token only — it would NOT
   fail if the `/edge-log` selected-edge panel or the promote-to-gated control were
   accidentally removed. The edge-decoration `useEffect`'s per-mode stroke IS covered
   by AT 11 (asserts live `direct` path stroke == `var(--edge-direct)`), and the spec
   leans on the existing `tests/test_unified_topology_keyed_lookup.py` AT-5/AT-6 staying
   green (run in Task 2's `testCommand`). This is a conventional non-regression strategy
   (existing suite as coverage), so it is advisory, not blocking. **Optional hardening:**
   add a one-line structural deletion-detector to AT 8 for an `/edge-log` and a
   promote-control literal, or confirm the existing keyed-lookup suite already asserts
   the promote control.

## Empty Sections

- **Critical:** none
- **High:** none
- **Medium:** none

## Note

The template referenced in the invocation
(`doc/templates/plan-review-report-template.md`) does not exist anywhere in the
worktree tree; this report follows the standard `review-plan` skill schema instead.
