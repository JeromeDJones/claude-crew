# Build Report — dashboard-shape-render — cycle 0

## Task
`dashboard-shape-render` (index 4) of feature `workflow-shape-composition-m0`.

Acceptance test: **AT 13** — graphical mermaid render + XSS guard for the shape-gate panel.

## Playwright Browser
`uv run playwright install chromium` ran successfully. Chromium was already available; the command was a no-op (0 output).

## Slice Test Result
```
uv run pytest tests/test_shape_render.py -v
```
**PASS** — 3/3 tests collected and passed in 22.69s.

```
tests/test_shape_render.py::test_shape_gate_renders_dag_with_visible_labels[chromium] PASSED
tests/test_shape_render.py::test_shape_gate_xss_guard[chromium] PASSED
tests/test_shape_render.py::test_shape_gate_hidden_when_no_pending_proposals[chromium] PASSED
3 passed, 5 warnings in 22.69s
```

## Full Suite Result (non-live tests)
```
uv run pytest --ignore=tests/test_e2e_pack_parity.py --ignore=tests/test_e2e_pack_tool_allowlist.py \
  --ignore=tests/test_fidelity_audit.py --ignore=tests/test_live_sdk.py \
  --ignore=tests/test_live_subagents.py --ignore=tests/test_user_loader_live.py -q
```
**2 failed, 1396 passed, 12 skipped, 1 xfailed** in 189.53s.

The 2 failures are both in `tests/test_shutdown_signals.py` — the pre-existing baseline flakes documented in the task brief ("suite has 2 pre-existing `test_shutdown_signals` baseline flakes — your gate is `tests/test_shape_render.py`"). No regressions introduced.

## Files Changed

```
git diff --name-status HEAD
M   claude_crew/ui/dashboard.html
```

Untracked (new):
```
?   tests/test_shape_render.py
```

## What Was Implemented

### `claude_crew/ui/dashboard.html`

Added two new React components immediately before `MissionControlLayout`:

**`ShapeProposalCard`** — renders one pending proposal:
- Wraps the proposal's `mermaid` source string in `<pre><code class="language-mermaid">`.
- Uses a `useCallback` ref to call the existing `renderMermaidBlocks(el)` after mount — the exact same pipeline the artifact viewer uses (mermaid@11.4.1 + `securityLevel:'strict'` + DOMPurify foreignObject re-sanitization).
- Has `Approve` and `Decline` buttons (`shape-approve-btn` / `shape-decline-btn`) that `POST /shape-approval/{crew_id}/{shape_id}` with `{"decision":"approve"|"decline"}`.
- Hides itself (returns `null`) after a successful resolve so the UI collapses cleanly on decision.

**`ShapeGatePanel`** — container panel shown only when there are pending proposals:
- Filters `proposals` to `status === 'pending'`.
- Returns `null` (hidden) when the list is empty.
- Renders with CSS class `shape-gate-panel` for test-selector targeting.

**`MissionControlLayout`** — insertion point:
- `<ShapeGatePanel proposals={cli.shape_proposals || []} />` inserted between `<InstanceStrip>` and the main content grid so it appears as a banner below the instance tabs.

### `tests/test_shape_render.py` (new)

Three Playwright tests covering AT 13:

1. **`test_shape_gate_renders_dag_with_visible_labels`** — happy path:
   - Creates a broker with a real pending shape proposal (2 nodes: `implementor/builder`, `reviewer/sentinel`; 1 gated edge).
   - Navigates to the dashboard; waits for `.shape-gate-panel` to appear.
   - Waits 3 s for async `mermaid.render()` to complete.
   - Asserts ≥1 SVG inside `.shape-gate-panel`.
   - Asserts `implementor` and `reviewer` are visible in `panel.inner_text()` and/or in SVG foreignObject text nodes — verifies labels render, not black boxes.
   - Asserts `Approve` and `Decline` buttons are present.

2. **`test_shape_gate_xss_guard`** — XSS guard:
   - Creates a shape whose `reviewer` role contains `<script>window.XSS_SHAPE_FIRED=true;</script>` and `<img src=x onerror=window.XSS_SHAPE_FIRED=true>`.
   - `shape_to_mermaid()` incorporates these into the mermaid node label string.
   - Navigates; waits 8 s for mermaid + DOMPurify sanitization.
   - Asserts `window.XSS_SHAPE_FIRED` was never set (no JS executed).
   - Asserts no `<script>` element survives in `.shape-gate-panel`.
   - Asserts no `on*` event-handler attributes survive (DOM attribute query, not innerHTML substring).
   - Asserts the panel and `Approve` button are still present (diagram still renders).

3. **`test_shape_gate_hidden_when_no_pending_proposals`** — absent-data guard:
   - Creates a broker with no proposals.
   - Asserts `.shape-gate-panel` is absent from DOM entirely.

## Design Notes

- **Pipeline reuse, not reimplementation**: `renderMermaidBlocks` already handles the full `mermaid.render()` → DOMPurify/foreignObject pipeline. The shape-gate card feeds the mermaid source through a `pre > code.language-mermaid` wrapper so the function finds it via its existing DOM walk — zero duplication of the XSS-hardening logic.
- **The black-box lesson**: the `ADD_TAGS: ['foreignObject', ...]` in the existing `renderMermaidBlocks` DOMPurify config is load-bearing. Without it, mermaid's `securityLevel:'strict'` labels (rendered as HTML inside `<foreignObject>`) would be stripped and nodes would appear as unlabeled black rectangles. Reusing the function means this is inherited automatically.
- **Approve/Decline POST target**: `/shape-approval/{crew_id}/{shape_id}` (the route added by the `dashboard-shape-state-route` task). The `crew_id` field comes from the `/api/state` proposal entry and is load-bearing for the multi-instance proxy.
