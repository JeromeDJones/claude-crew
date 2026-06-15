# Build Report: unified-topology-view-task-unify-topology-graph-component (cycle 1)

**Verdict:** PASS
**Cycle:** 1
**Generated:** 2026-06-14

## Tests Run

- **Declared command:** `uv run pytest tests/test_edge_dashboard.py tests/dashboard/test_roster_spotlight.py tests/dashboard/test_dashboard_mermaid.py tests/test_dashboard_render.py -q`
- **Actual command:** `uv run pytest tests/test_edge_dashboard.py tests/dashboard/test_roster_spotlight.py tests/dashboard/test_dashboard_mermaid.py tests/test_dashboard_render.py -q`
- **Divergence reason:** N/A — declared command run verbatim.
- **Exit code:** 0
- **Passed:** 54 / **Failed:** 0 / **Total:** 54

Notes:
- Full-suite sweep (`uv run pytest --ignore=tests/test_shutdown_signals.py`): **1550 passed, 34 skipped, 1 xfailed**. The ignored file's two failures (`test_sigterm_triggers_clean_exit_and_deregister`, `test_sigint_triggers_clean_exit_and_deregister`) are pre-existing and unrelated — they reproduce on a stashed clean tree.

## Failing Tests

_None._

## Rework Addressed (cycle-0 Medium)

**Spec headline behavior — "gated edges route `peer --> lead --> peer`" — now implemented end-to-end.**

The broker records gated edges as PEER-to-PEER (`EdgeStat.from_slot/to_slot` are peers; `lead` is never an endpoint per `broker.py:1216` / `shapes.ShapeEdge`). Cycle-0 rendered every `EdgeStat` as a flat peer edge, which left `lead` structurally disconnected on real broker data. The mockup had hand-authored two dummy EdgeStats with `lead` as an endpoint to fake the bridge — cycle-0 mirrored that naive per-EdgeStat rendering.

**Fix shape (see `displayEdges` in `claude_crew/ui/dashboard.html`):**

1. `displayEdges` (new `React.useMemo`) expands each `mode === "gated"` `EdgeStat` whose endpoints are peers (neither is `lead`) into TWO synthetic mermaid segments: `from_slot → lead` and `lead → to_slot`. Each synthetic carries the SOURCE EdgeStat verbatim on a `_source` annotation. Direct/tee edges (and tripped styling) pass through unchanged — critical for keeping BC-03's reciprocal-pair keyed lookup byte-identical.
2. The mermaid source builder + slot-collection now iterate `displayEdges` (so the slot set includes `lead` whenever any synthetic touches it, and the mermaid emits `planner -->|"gated 3"| lead` + `lead -->|"gated 3"| implementor`).
3. The `window.mapEdgeStatsToPaths` call now receives `displayEdges` — each rendered path resolves to ITS segment record. Decoration unwraps `_source` (when present) so:
   - `setSelectedEdge(source)` — the panel header reads the SOURCE peer endpoints.
   - `fetch('/edge-log/${crewId}/${source.from_slot}/${source.to_slot}')` — clicks on either synthetic segment dispatch against the real peer→peer identity, preserving the multi-instance crew_id-in-path contract (CLAUDE.md trap).
   - Exchange-pulse keyed by SOURCE peer key (`prev[source.from→source.to]`), so a single source-exchange increment doesn't double-pulse the two through-lead segments.
4. **BC-03 / AT-5 / AT-6 unchanged.** `window.mapEdgeStatsToPaths(svgRoot, edgeStats)` still takes `(svgRoot, edgeStats)` and returns `Map<SVGPathElement, EdgeStat>` exactly as before. The synthesis happens in the caller, not the helper, so the AT-5/AT-6 unit-level test (next slice) hits the same code path and the reciprocal-direct keyed test remains byte-identical.

**New green-suite tests (`tests/test_edge_dashboard.py`):**

- `test_gated_edge_bridges_through_lead_with_two_amber_segments` — broker fixture records a single gated `(planner, implementor)` EdgeStat; rendered SVG must contain a `planner → lead` AND a `lead → implementor` path, both with amber `var(--edge-gated)` stroke, and the flat `planner → implementor` peer edge must NOT appear. Structural guard against future regressions to flat rendering. Endpoint parsing uses the production `/^L[-_](.+?)[-_](.+?)[-_]\d+$/` regex (mermaid v11 emits id `L_<from>_<to>_<n>` consistently in our env; LS-/LE- classes were absent in our DOMPurify-sanitized output — the id parse is the load-bearing channel).
- `test_clicking_gated_segment_fetches_source_endpoints_with_crew_id` — clicks each synthetic segment, asserts the resulting fetch URL is `/edge-log/<crew_id>/planner/implementor` (SOURCE peers + crewId in path) and that NO fetch goes to a `lead`-endpoint URL.

Net effect: cycle-0's 4 slice tests + the new 2 = 54 tests in 96s. AT-3/AT-4/AT-7/AT-8 stay green; the headline design decision is now a structural test, not a render hope.

## Uncovered / Partially Covered Tests

- AT-3: covered (Playwright; one-graph, no legacy radial SVG, `lead` first-class). Note: the AT-3 fixture explicitly enumerates `planner→lead` and `lead→reviewer` as discrete gated EdgeStats (one-endpoint-already-lead case), so the gated-bridge synthesis is correctly skipped for them and the test asserts the lead-bridging shape directly.
- AT-4: covered (Playwright; subtitle, hidden legend, neutral grey edges, no console errors, no mode badges, cursor:default).
- AT-7: covered (Playwright; edge click fires `GET /edge-log/${crewId}/${from}/${to}`). The end-to-end leader→follower proxy is exercised unchanged by `tests/test_edge_dashboard.py::TestMultiInstanceEdgeLogProxy` (M2 gate untouched).
- AT-8: covered (Playwright; slot `impl_a` ≠ role `implementor` joins activity via `slot_to_teammate` → tool-use → nodecard `tool` class).
- Gated bridge (spec Design Decision §"gated edges route peer-->lead-->peer"): covered (Playwright; two new tests in `tests/test_edge_dashboard.py`).

## Files Changed

```
M	claude_crew/ui/dashboard.html
M	tests/dashboard/test_roster_spotlight.py
M	tests/test_dashboard_render.py
M	tests/test_edge_dashboard.py
```

## Scope-Creep Entries (this cycle)

_None._

## Blocker Reason

N/A
