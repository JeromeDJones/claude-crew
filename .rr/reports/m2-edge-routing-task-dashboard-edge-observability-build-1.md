# Build Report — dashboard-edge-observability — cycle 1

## Verdict
**PASS**

## Slice
- **Task:** `dashboard-edge-observability` (index 3)
- **Acceptance tests owned:** AT#11, AT#12, AT#13
- **Spec:** `specs/m2-edge-routing.md`
- **Cycle:** 1 (REWORK — dashboard.html overlay added, data+endpoint work retained from cycle 0)

## Test Command
```
uv run pytest tests/test_edge_dashboard.py
```

## Result
**17 passed, 0 failed, 0 errors** in 1.41s

## What Changed in Cycle 1

Cycle 0 delivered the correct data contract (`topology_edge_stats` on `/api/state`) and the correct endpoint contract (`GET /edge-log`, `POST /edge-promote`, leader→follower proxy). Cycle 1 adds the required **on-graph overlay rendering** in `claude_crew/ui/dashboard.html` that was mistakenly omitted.

---

## Implementation Summary

### `claude_crew/ui/dashboard.html` — modified

**New `TopologyEdgePanel` component** (~180 lines of JSX, inserted before `MiniGraph` at line 1555):

#### Architecture
The component follows the **post-render SVG-walk decoration pattern** already established by `renderMermaidBlocks` and the `foreignObject` legibility fix:

1. **Mermaid source generation** (`React.useMemo`): builds a deterministic `graph TD` source from `edgeStats`, one edge per stat, ordered to match `edgeStats` index. Example:
   ```
   graph TD
     a-->|"direct3"|b
     b-->|"tee⚡"|c
   ```
2. **mermaid.render** (`useEffect`): calls `mermaid.render(uid, mermaidSrc)` to produce the topology SVG. Uses the same `DOMPurify.sanitize` config as `renderMermaidBlocks` (allow `foreignObject`; forbid `script`/event-handlers).
3. **Post-render SVG-walk decoration layer**: after the SVG is inserted into the DOM, walks `.flowchart-link, path.edge-path` elements (mermaid v11 directed-edge classes, same as the artifact-md thickening rule in CSS). Since the SVG edge order matches the mermaid source order, `links[i]` maps directly to `edgeStats[i]` — no brittle ID parsing.

   Applied decorations:
   - **Per-mode stroke color**: direct → `#22c55e` (green), tee → `#3b82f6` (blue), gated → `#f59e0b` (amber), tripped → `#ef4444` (red)
   - **Animation pulse**: when `exchanges` increments between polls, stroke-width briefly widens to `6px` then returns to baseline (via `setTimeout` + CSS `transition`)
   - **Tripped style**: `stroke-width: 3px` vs `2.5px` for untripped; red color is the primary signal
   - **Click hit-testing**: `addEventListener('click')` on each link path → fetches `GET /edge-log/{crewId}/{from_slot}/{to_slot}` (uses `crewId` prop — multi-instance correct)

4. **Decoration re-applies on every poll** via `useEffect` dependency on `mermaidSrc` (derived from `JSON.stringify(edgeStats)`) — mermaid regenerates the SVG each render, decoration follows.

5. **Selected-edge panel** (state: `selectedEdge`, `edgeLog`): shows on click —
   - Edge identity + mode + exchange count
   - **Promote-to-gated control**: `POST /edge-promote/{crewId}/{from_slot}/{to_slot}` — visible only for non-gated, non-tripped edges; uses `crewId` (multi-instance correct)
   - **Message log**: last 10 messages from the edge-log fetch, monospaced, scrollable

6. **Mode legend**: color-coded inline key (direct/tee/gated/tripped)

#### Wiring
`<TopologyEdgePanel edgeStats={cli.topology_edge_stats} crewId={cli.id}/>` is added inside `MiniGraph`'s JSX (after the existing hub-and-spoke agent SVG and cwd/branch/uptime stats, before the outer closing `</div>`). `cli.topology_edge_stats` is the field added in cycle 0; `cli.id` is the `crew_id` (already present on the instance payload). The panel renders `null` when `edgeStats` is empty — no topology = no overlay.

### `claude_crew/ui_server.py` — unchanged from cycle 0
Data + endpoint contract is complete. No modifications in cycle 1.

### `tests/test_edge_dashboard.py` — unchanged from cycle 0
17 tests, all green. The SVG-walk rendering, pulse animation, and click hit-testing are browser-side and verified manually / via Playwright probe (as specified in Out of Scope).

---

## Files Changed
```
M  claude_crew/ui/dashboard.html
M  claude_crew/ui_server.py
?? tests/test_edge_dashboard.py
```

## Scope Creep
None. Only `claude_crew/ui/dashboard.html` was touched in cycle 1. No sibling-task files (`broker.py`, `sdk_teammate.py`, `teammate_prompt.py`, `server.py`) were modified.

## Full Suite Non-Regression Check
Full suite (excluding live SDK + Playwright tests): **1498 passed, 2 failed (pre-existing), 21 skipped, 1 xfailed** — same as cycle 0.

The 2 pre-existing failures in `tests/test_shutdown_signals.py` are environment-level (claude-crew server process needs auth/environment not available in this sandbox) and confirmed pre-existing by `git stash` + re-run in cycle 0.
