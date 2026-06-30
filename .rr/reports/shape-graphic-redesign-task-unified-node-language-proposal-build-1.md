# Build Report — unified-node-language-proposal — cycle 1

**Verdict:** PASS  
**Date:** 2026-06-29  
**Slice test command:**
```
uv run pytest -m dashboard tests/test_unified_proposal_language.py tests/dashboard/test_dashboard_artifact_xss.py
```

---

## Cycle 1 corrections

The cycle-0 build report incorrectly classified 4 failures as pre-existing by using `git stash` to simulate the baseline. However, the stash baseline was the post-task-1 worktree (which already contained the expand-button SVG introduced by `shared-zoom-pan-modal`), not true master. All 4 failures were introduced by task 1 and needed to be fixed here.

**Root cause (all 4 failures):** Task 1 (`shared-zoom-pan-modal`) added an expand-button SVG icon inside `.rail-topology`. The selector `.rail-topology svg` now matches 2 elements (flowchart SVG + button SVG), causing Playwright strict-mode to raise a `StrictModeViolationError` on `.wait_for(state="attached")` calls. This is a test-locator ambiguity only — the topology rendering behavior is correct and unchanged.

**Fix applied:** The same `#topo-host svg` disambiguation already applied in cycle 0 (to `test_roster_spotlight.py`, `test_unified_proposal_language.py`, `test_topology_zoom_modal.py`) was extended to all remaining occurrences in `test_edge_dashboard.py` and `test_dashboard_render.py`.

---

## Slice test results

```
10 passed, 9 warnings in 30.42s
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

## Full dashboard suite results

```
uv run pytest -m dashboard -q
72 passed, 1613 deselected, 0 failed in 209s
```

**Zero failures.** All 72 `@pytest.mark.dashboard` tests pass.

---

## Files changed (`git diff --name-status HEAD`)

```
M	claude_crew/ui/dashboard.html
M	tests/dashboard/test_roster_spotlight.py
M	tests/dashboard/test_shape_resurface.py
M	tests/test_dashboard_render.py
M	tests/test_edge_dashboard.py
M	tests/test_shape_render.py
```

New file added:
```
A	tests/test_unified_proposal_language.py
```

---

## Implementation summary

### dashboard.html changes (task-2 scope)
- **CSS**: `.nodecard.proposed { border-color: var(--accent-line); border-style: dashed; background: var(--bg-1); }` — dashed-accent variant for "not yet instantiated" proposal nodes
- **`shapeToMermaidUnified(shape, { proposed })`**: Generates mermaid `graph TD` source using `.nodecard` foreignObject labels; gated edges bridged through lead (e.g. sender→lead→receiver); uses `--edge-*` CSS token vocabulary consistent with live topology
- **`openProposalModal(proposal)` / `closeProposalModal()` / `_escListenerProposal`**: Populate `#proposal-modal` with the shared modal substrate, call `bindPanZoom()`, invoke existing `renderInto()` pipeline (mermaid + DOMPurify + edge decoration)
- **`ShapeProposalCard` rewrite**: Replaced static `maxWidth: 420` side-by-side diagram with a `.shape-proposal-thumbnail` clickable div that calls `openProposalModal(proposal)`. Approve/Decline controls preserved. `maxWidth: 420` literal removed (AT-14 structural check).
- **`#proposal-modal` mount div**: Added alongside `#topology-modal`

### New test file
- **`tests/test_unified_proposal_language.py`**: AT-11 through AT-15 (10 test cases)

### Collateral locator fixes (all in `tests/`)

| File | Change |
|---|---|
| `tests/dashboard/test_roster_spotlight.py` | `.rail-topology svg` → `#topo-host svg` (3 locations — done in cycle 0) |
| `tests/dashboard/test_shape_resurface.py` | `test_open_modal_shows_dag`: `.shape-proposal-diagram` → `.shape-proposal-thumbnail` + modal click flow |
| `tests/test_shape_render.py` | `test_shape_gate_renders_dag_with_visible_labels`: SVG check moved from `.shape-gate-panel` to `#proposal-modal` after thumbnail click |
| `tests/test_edge_dashboard.py` | `.rail-topology svg` → `#topo-host svg` at lines 649, 657, 662, 747, 833, 898; JS querySelectorAll `.rail-topology path.flowchart-link` → `#topo-host path.flowchart-link` at line 838 |
| `tests/test_dashboard_render.py` | `.rail-topology svg` → `#topo-host svg` at line 1027; JS querySelectorAll `.rail-topology svg path` → `#topo-host svg path` at line 1040 |

All collateral fixes are within `tests/**` (test-locator disambiguation only) and within scope per the task's `testTouches` coverage.
