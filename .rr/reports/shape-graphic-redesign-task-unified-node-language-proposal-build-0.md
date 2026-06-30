# Build Report — unified-node-language-proposal — cycle 0

**Verdict:** PASS  
**Date:** 2026-06-29  
**Slice test command:**
```
uv run pytest -m dashboard tests/test_unified_proposal_language.py tests/dashboard/test_dashboard_artifact_xss.py
```

---

## Slice test results

```
10 passed, 9 warnings in 30.76s
```

All 10 tests pass:

| Test | Result |
|---|---|
| `TestAT13StructuralDeletionDetector::test_shapeToMermaidUnified_named_literal` | PASS |
| `TestAT13StructuralDeletionDetector::test_nodecard_proposed_named_literal` | PASS |
| `TestAT15SecurityLevelPreserved::test_security_level_strict_named_literal` | PASS |
| `test_at11_unified_edge_token_language[chromium]` | PASS |
| `test_at12_proposed_nodecard_class_and_dashed_border[chromium]` | PASS |
| `test_at14_proposal_thumbnail_opens_modal[chromium]` | PASS |
| `test_xss_onerror_attribute_is_stripped[chromium]` | PASS |
| `test_xss_script_tag_is_stripped[chromium]` | PASS |
| `test_xss_javascript_url_href_is_stripped[chromium]` | PASS |
| `test_xss_malicious_mermaid_payload[chromium]` | PASS |

---

## Full suite results (non-live)

```
4 failed, 1641 passed, 26 skipped, 1 xfailed in 240s
```

All 4 failures are **pre-existing** (confirmed present on the baseline before any task-2 changes via `git stash`):

| Pre-existing failure |
|---|
| `test_dashboard_render.py::test_at7_unified_topology_preserves_crew_id_in_edge_log_path[chromium]` |
| `test_edge_dashboard.py::test_at3_unified_topology_renders_single_graph_with_lead_node[chromium]` |
| `test_edge_dashboard.py::test_at8_activity_joins_via_slot_to_teammate[chromium]` |
| `test_edge_dashboard.py::test_gated_edge_bridges_through_lead_with_two_amber_segments[chromium]` |

Zero regressions introduced by this task.

---

## Files changed

| File | Change |
|---|---|
| `claude_crew/ui/dashboard.html` | Added `shapeToMermaidUnified()`, `openProposalModal()`, `closeProposalModal()`, `_escListenerProposal`; CSS `.nodecard.proposed` rule; rewrote `ShapeProposalCard` to thumbnail pattern; added `#proposal-modal` mount div |
| `tests/test_unified_proposal_language.py` | **NEW** — AT-11, AT-12, AT-13, AT-14, AT-15 tests |
| `tests/dashboard/test_roster_spotlight.py` | Collateral fix: `.rail-topology svg` → `#topo-host svg` (expand button SVG added by task 1 caused strict `wait_for` to match 2 elements) |
| `tests/dashboard/test_shape_resurface.py` | Collateral fix: `test_open_modal_shows_dag` updated from `.shape-proposal-diagram` → `.shape-proposal-thumbnail` + modal click flow |
| `tests/test_shape_render.py` | Collateral fix: `test_shape_gate_renders_dag_with_visible_labels` updated to click thumbnail → check modal SVG (SVG no longer rendered inline in gate panel) |

---

## Implementation summary

### `shapeToMermaidUnified(shape, { proposed })` (dashboard.html)
Generates mermaid `graph TD` source using `.nodecard` foreignObject labels with `.nodecard.proposed` variant for "not yet instantiated" nodes. Gated edges are expanded to bridge through `lead` (implementor → lead → receiver pattern). Uses `--edge-*` CSS token vocabulary consistent with the live topology renderer.

### `.nodecard.proposed` CSS
Dashed border variant (`border-style: dashed; border-color: var(--accent-line); background: var(--bg-1)`) applied to proposal nodes inside the zoom/pan modal. Dot opacity lowered to 0.6 to visually distinguish from live nodes. DOMPurify config's `ADD_ATTR` list already included `class` so the class attribute survives mermaid's foreignObject sanitization.

### `openProposalModal(proposal)` / `closeProposalModal()`
Populate the `#proposal-modal` mount div with the shared modal substrate (`.modal-backdrop` → `.modal` → `.zoom-surface`), call `bindPanZoom()` on the host, and invoke the existing `renderInto()` pipeline to render+sanitize+decorate the shape mermaid. Esc key and backdrop click close the modal.

### `ShapeProposalCard` rewrite (thumbnail pattern)
Old: `maxWidth: 420` flexDirection:row side-by-side static diagram rendered via `renderMermaidBlocks`. New: `.shape-proposal-thumbnail` clickable div that calls `openProposalModal(proposal)`. Approve/Decline controls preserved. `maxWidth: 420` literal removed (AT-14 structural check).
