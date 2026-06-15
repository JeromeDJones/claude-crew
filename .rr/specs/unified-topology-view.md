# Spec: unified-topology-view

## Problem

The Mission Control dashboard left rail stacks two visually-redundant, structurally-incompatible graphs: the pre-M2 lead-centric roster hub (`MiniGraph`, a hand-drawn SVG with the lead at center and teammates as activity-pulsing spokes) and the M2 edge-routing overlay (`TopologyEdgePanel`, a mermaid `graph TD` of the recorded shape topology with mode-colored peer edges, exchange badges, and click→edge-log / promote→gated controls). The hub shows *activity* but cannot represent peer edges; the panel shows *routing* but not per-node activity. An operator must read two graphs to answer one question — "who is doing what, and how are they wired?". This slice collapses them into a single `TopologyGraph` whose nodes are the crew and which carries the activity layer (on node borders) and the routing layer (on edges) at once, degrading to a roster star when no shape is instantiated. It also fixes BC-03: the M2 panel maps edge-stats to rendered paths *positionally*, so mermaid's layout reordering makes a healthy back-edge inherit a tripped edge's red "gated⚡" decoration — the operator misreads a healthy edge as runaway.

## Architecture Overview

Presentation rewrite plus exactly one additive serialization field. Three production files change:

- **`claude_crew/broker.py`** — `BrokerSnapshot` gains `topology_slot_to_teammate: Mapping[str, str]`, aggregated from all recorded topologies (last-write-wins per slot, mirroring the existing `topology_edge_stats` aggregation at ~L1204–1234). No routing/breaker change.
- **`claude_crew/ui_server.py`** — the per-`cli` payload built in `_build_local_instance` (~L461, sibling to `topology_edge_stats`) gains `"slot_to_teammate": dict(...)`. Additive serialization only; `/edge-log` and `/edge-promote` handlers and the leader→follower proxy are untouched.
- **`claude_crew/ui/dashboard.html`** — `MiniGraph` (~L1770) and `TopologyEdgePanel` (~L1565) are replaced by one `TopologyGraph` component mounted where `MiniGraph` is today (~L2735, `topologyNode={...}`). It reuses the existing `renderMermaidBlocks` mermaid→DOMPurify→foreignObject pipeline (~L1055) and extends `TopologyEdgePanel`'s post-render SVG-walk decoration with node decoration, foreignObject node cards, and a **keyed** (not positional) edge-stat lookup exposed on `window` for unit-level test assertions.

The data flow: broker aggregates `slot_to_teammate` → `BrokerSnapshot` → `ui_server` serializes onto `/api/state[cli]` → `TopologyGraph` joins per-teammate activity (`agents[].status`, keyed by teammate-id) onto per-slot routing nodes (`EdgeStat.from_slot/to_slot`, keyed by slot) via `slot_to_teammate[slot] → teammate_id`.

### Call-site survey

`slot_to_teammate` is read at three structurally-distinct sites, so the survey is warranted:

| Call-site | Path | Shape | Notes |
|-----------|------|-------|-------|
| `_local_edge_log_response` | `claude_crew/ui_server.py:987-988` | reads `topo.slot_to_teammate.get(slot)` off a single `Topology` | existing; unchanged by this slice |
| broker routing resolution | `claude_crew/broker.py:1538-1540` | iterates `topo.slot_to_teammate` per-topology | existing; unchanged |
| **new** snapshot aggregation | `claude_crew/broker.py` (~L1204 region) | flattens **all** recorded topologies → one `Mapping[str,str]` | NEW; last-write-wins merge |

Resolution: the new aggregation is a **separate** read shape (flatten-all-topologies) from the two existing per-topology reads. It does not replace them — it adds a snapshot-level rollup mirroring `topology_edge_stats`. No merge of existing sites.

## Data / API Contracts

```python
# claude_crew/broker.py — BrokerSnapshot (additive field)
@dataclass
class BrokerSnapshot:
    ...
    topology_edge_stats: tuple[EdgeStat, ...] = ()
    topology_slot_to_teammate: Mapping[str, str] = field(default_factory=dict)
    # Aggregated from recorded topologies. For each topology in record order,
    # merge slot_to_teammate into the accumulator: LAST topology wins on slot
    # collision (same iteration order as topology_edge_stats' "latest wins").
```

```jsonc
// /api/state  →  state.instances[].cli  (additive key, sibling to topology_edge_stats)
{
  "id": "crew-abc",                       // unchanged — crewId for per-edge fetches
  "label": "...",                          // unchanged — subtitle
  "topology_edge_stats": [                 // unchanged
    {"from_slot": "...", "to_slot": "...", "mode": "direct|tee|gated",
     "exchanges": 0, "tripped": false, "crew_id": "crew-abc"}
  ],
  "slot_to_teammate": {"planner": "a7b3c2d8...", "implementor": "e1f4..."}  // ADDITIVE
}
```

```js
// claude_crew/ui/dashboard.html — keyed-lookup contract, exposed on window
// window.mapEdgeStatsToPaths(svgRoot, edgeStats) -> Map<SVGPathElement, EdgeStat>
//   For each path.flowchart-link in svgRoot:
//     1. primary:  parse path.id  /^L[-_](.+?)[-_](.+?)[-_]\d+$/  -> (from, to)
//     2. fallback: read class tokens  LS-<from>  /  LE-<to>
//     3. resolve EdgeStat via Map<"from→to", EdgeStat>
//     4. last-resort positional links[i]->edgeStats[i] ONLY if 1&2 both fail;
//        emit console.warn (signals mermaid emit-format drift; never in practice)
```

## Design Decisions

- **One component, two layers on disjoint visual channels** — *Rationale:* activity (node-border color + 1.6s pulse + corner dot) and routing (edge stroke color/width + label badge + 550ms exchange pulse) never compete for the same pixel, so a tool-using node on a tripped edge reads both facts at one glance. — *Carried into:* `TopologyGraph` in `dashboard.html`; AT-3, AT-8; mockup states (ii).
- **Mermaid `graph TD` with `lead` as a first-class node; gated edges route `peer --> lead --> peer`** — *Rationale:* explicit lead node makes gated-through-lead visibly distinct from direct edges; a force/radial layout hides edge identity on overlap. — *Carried into:* `TopologyGraph` edge-generation branch; AT-3.
- **Roster fallback is the same component with three branches** — *Rationale:* empty-state-as-degenerate-state; only (a) orientation `graph LR` vs `graph TD`, (b) edge generation (`lead --> teammate_*` neutral grey, no badge/click), (c) legend visibility differ. — *Carried into:* `TopologyGraph` fallback branch; AT-4.
- **BC-03: edge decoration keyed by `(from_slot, to_slot)`, never positional** — *Rationale:* mermaid v11 reorders edge paths during layout; positional `links[i]→edgeStats[i]` aliases reciprocal pairs. Two independent endpoint signals (path id + `LS-`/`LE-` classes) survive single-channel upstream drift. — *Carried into:* `window.mapEdgeStatsToPaths`; AT-5, AT-6; `tests/test_unified_topology_keyed_lookup.py`.
- **One additive field `slot_to_teammate` surfaced on `/api/state`** — *Rationale:* the activity↔routing join needs an authoritative `slot → teammate_id`; the `agent.role === slot` guess is unreliable because author-defined slots (`impl_a`, `lead_planner`) need not equal pack roles. — *Carried into:* `BrokerSnapshot.topology_slot_to_teammate`; `ui_server.py` payload key; AT-1, AT-2, AT-8.
- **Multi-topology slot collision: last-write-wins** — *Rationale:* same slot label across topologies is the same role doing the same work in practice; a richer `slot_to_teammate_by_topology` list is explicitly deferred. — *Carried into:* `BrokerSnapshot` aggregation; AT-9.
- **Lead-spoke motion pulses removed; activity moves to node border** — *Rationale:* a pulse moving along a peer edge would read as message routing (a routing-channel meaning), colliding with the edge layer. This is a deliberate behavior change, not a regression. — *Carried into:* `TopologyGraph` node-border pulse; AT-3 (no radial-SVG assertion).
- **Every per-edge fetch keeps `crew_id` in the path** — *Rationale:* the leader serves rows belonging to follower brokers; a same-origin `/edge-log/<from>/<to>` hits the leader's broker and 404s for follower rows (the CLAUDE.md multi-instance trap). — *Carried into:* `GET /edge-log/${crewId}/${from}/${to}`, `POST /edge-promote/${crewId}/${from}/${to}`; AT-7.

## Edge Cases

- **Empty / missing `topology_edge_stats`** → roster fallback: `graph LR`, `lead` + one node per live teammate from `agents`, neutral grey edges (`--line`, 1.5px), no badges, no click handlers, no pulse, legend hidden, subtitle `roster — no shape instantiated`. No console error, no loading flicker. (AT-4)
- **Slot present in `topology_edge_stats` but absent from `slot_to_teammate`** → `teammateId` undefined → `status` defaults to `"idle"` (grey border), node still renders. Defensive: `cli.slot_to_teammate?.[slot]` with `?? "idle"`. (AT-8)
- **`teammate_id` present in `slot_to_teammate` but no matching live `agents[]` entry** (teammate tombstoned/dead) → `agents.find(...)` returns undefined → status `"idle"`. No throw.
- **`lead` node** → slot label reads `lead`, no teammate-id second line (lead is the session, not a teammate row).
- **Reciprocal pair `(a,b)` + `(b,a)`** → mermaid emits two `<g>` with disambiguated ids (`L_a_b_0`, `L_b_a_0`); each resolves its own `EdgeStat`; strokes/badges never swap. (AT-5)
- **Malformed / unexpected path id** (mermaid changed separator or format) → fallback to `LS-`/`LE-` class tokens; if those also fail, positional last-resort with a single `console.warn`. (AT-6)
- **Multiple recorded topologies sharing a slot label** → last topology wins in `topology_slot_to_teammate`. (AT-9)
- **Zero live teammates, no topology** → roster renders `lead` alone, no spokes, no error (existing `test_zero_live_agents_renders_safely` invariant must still hold).
- **Multi-instance: clicking an edge belonging to a follower in the leader's view** → fetch carries the row's `crew_id`; leader proxies to the follower (mirrors `_proxy_tool_output`). (AT-7)

**Displayed-data answers:**
- *Data absent (no shape):* roster star (degenerate state), not a blank panel or spinner.
- *Data expired/capped (edge tripped past budget):* edge renders red + thick (3px) + `gated ⚡` badge — the tripped state is shown, not hidden.
- *Zero vs missing:* `exchanges: 0` renders the mode badge with count `0` (e.g. `tee 0`); a *missing* edge simply isn't drawn. A slot with no resolvable teammate renders an idle (grey) node, not a hidden one.

**Consumers of the changed data (read from code, not names):**
- `topology_edge_stats` is read by `TopologyEdgePanel` (being replaced) and asserted in `tests/test_edge_dashboard.py`, `tests/test_circuit_breaker.py`. The new `slot_to_teammate` payload key is additive — any consumer doing dict-equality (not subset) assertion on the `cli` payload must tolerate the new key (update to subset assertion).

## Validation Contracts at Handoff Boundaries

| Boundary | Preconditions | Failure Behavior | Postconditions | Rollback |
|---|---|---|---|---|
| broker → `BrokerSnapshot` | ≥0 recorded topologies | empty topologies → `topology_slot_to_teammate = {}` | snapshot carries flattened last-write-wins map | N/A (pure read) |
| `BrokerSnapshot` → `/api/state` | snapshot built | missing field → KeyError surfaced loudly (fail-fast); never silently omitted | `cli.slot_to_teammate` present (possibly `{}`) | N/A |
| `/api/state` → `TopologyGraph` | payload received | missing/empty `topology_edge_stats` → roster fallback; missing `slot_to_teammate` → all nodes idle | one graph rendered, no console error | re-render on next poll |

## Acceptance Tests

1. **(AC-6 broker field)** Given a broker with one recorded `Topology` whose `slot_to_teammate = {"planner": "tid-1", "impl": "tid-2"}`, when `BrokerSnapshot` is built, then `snapshot.topology_slot_to_teammate == {"planner": "tid-1", "impl": "tid-2"}`. With zero recorded topologies the field is `{}`. (broker-level unit test in `tests/test_shape_broker.py`)
2. **(AC-6 serialization)** Given the broker from AT-1, when `/api/state` is built, then the per-`cli` payload contains `slot_to_teammate` equal to the broker's `topology_slot_to_teammate`, sibling to (not nested inside) `topology_edge_stats`, and the existing `topology_edge_stats` serialization is byte-for-byte unchanged. (ui_server payload test in `tests/test_circuit_breaker.py`)
3. **(AC-1 one graph)** Given the dashboard rendered with a non-empty `topology_edge_stats` (`planner → implementor → reviewer`, lead present), when the left rail renders, then exactly one topology graph exists: there is no hand-drawn `MiniGraph` radial SVG and no separate `TopologyEdgePanel` mount; the single graph contains a first-class `lead` node and the header reads `Topology`. (Playwright, sibling-updated assertions in `tests/test_edge_dashboard.py`)
4. **(AC-2 roster fallback)** Given the dashboard rendered with `topology_edge_stats = []` and 3 live teammates, when the left rail renders, then it shows a `graph LR` roster star (`lead` + 3 teammate nodes, neutral grey edges, **no** mode badges, **no** edge click handlers), the subtitle reads `roster — no shape instantiated`, the legend is hidden, and the browser console reports no errors. (Playwright, `tests/dashboard/test_roster_spotlight.py`)
5. **(AC-3 keyed reciprocal — deletion-detector)** Given the dashboard rendered with `topology_edge_stats` containing a reciprocal pair `(a, b, mode=direct, tripped=false, exchanges=8)` and `(b, a, mode=direct, tripped=true, exchanges=1)`, when the mermaid SVG renders, then DOM queries on `path.flowchart-link` show: two link elements; the path whose endpoint pair is `(a,b)` has stroke `--edge-direct` (green), is NOT thick (stroke-width ≠ 3px), and its label shows `direct 8`; the path whose endpoint pair is `(b,a)` has stroke `--edge-tripped` (red), IS thick (3px), and its label shows `direct ⚡`. Endpoint identity is recovered by parsing each path's id / `LS-`/`LE-` classes (the production mechanism). Reverting to positional indexing swaps the stats → swaps the strokes → fails this test. (NEW Playwright file `tests/test_unified_topology_keyed_lookup.py`)
6. **(AC-3 keyed-lookup branches)** Given `window.mapEdgeStatsToPaths` is exposed, when called via `page.evaluate()` on a rendered SVG for each case, then it returns the correct `EdgeStat` per path for: (a) source-order match, (b) mermaid-reordered paths, (c) reciprocal pair, (d) malformed id falling back to `LS-`/`LE-` classes, (e) both signals fail → positional last-resort returned with a `console.warn`. (NEW `tests/test_unified_topology_keyed_lookup.py`, same Playwright session)
7. **(AC-5 multi-instance preserved)** Given two dashboard instances with a shape instantiated on the follower, when an edge belonging to the follower is clicked in the leader's view, then the fetch path is `/edge-log/${crewId}/${from}/${to}` (crewId present) and the leader proxies to the follower (mirroring `_proxy_tool_output`); the M2 multi-instance gate passes unchanged. (Playwright, existing multi-instance assertions in `tests/test_dashboard_render.py`)
8. **(AC-6 activity join via slot_to_teammate)** Given an active topology where a slot label differs from the teammate's pack role (e.g. slot `impl_a`, teammate role `implementor`) and `slot_to_teammate = {"impl_a": "tid-9"}` with `agents[]` carrying `{id: "tid-9", status: "tool-use"}`, when the graph renders, then the `impl_a` node's border encodes `tool-use` (purple) — proving activity attaches via the `slot_to_teammate` join and NOT via a `role === slot` guess (which would leave the node idle). (Playwright, `tests/test_edge_dashboard.py`)
9. **(AC-6 multi-topology last-write-wins)** Given a broker with two recorded topologies, the first mapping `{"impl": "tid-old"}` and the second `{"impl": "tid-new"}`, when `BrokerSnapshot` is built, then `topology_slot_to_teammate["impl"] == "tid-new"`. (broker-level unit test in `tests/test_shape_broker.py`)

## Test Command

Prerequisite (one-time, not in the manifest as a binary): the Playwright browser must be installed. `pytest`, `pytest-asyncio`, and `pytest-playwright` are already declared in `pyproject.toml` (dev group), but the Chromium binary is not — install it before the first run:

```bash
uv run playwright install chromium
```

Then run the full suite (this is a widely-consumed dashboard change; a `-k` subset would mask cross-cutting regressions):

```bash
uv run pytest
```

## Out of Scope

- No new routing/breaker behavior; no changes to `EdgeStat`, `promote_edge`, `/edge-log`, `/edge-promote`, or the `_send_routed` / `_apply_circuit_breaker` paths.
- No new `/api/state` endpoint and no schema migration — surfacing `slot_to_teammate` is additive serialization of an existing broker field.
- No `slot_to_teammate_by_topology` per-topology list — multi-topology slot collision uses last-write-wins (explicitly deferred).
- No pixel-level, font-legibility, exact-color-value, or layout x/y-placement checks in the green suite (M2 precedent). **Mapping correctness IS in the green suite** (AT-5/AT-6 DOM assertions) — the M2 out-of-scope covered pixels, never mapping.
- AC-4 (activity + routing co-exist) and AC-7 (full mockup parity for layout/color/interaction) are human-judged via the mockup in the `## Validation` step — not numbered automated ATs.

## Assumptions

- **The keyed-lookup helper is exposed on `window` as `window.mapEdgeStatsToPaths(svgRoot, edgeStats)`** — *Default:* that exact name/signature returning `Map<SVGPathElement, EdgeStat>`. — *Rationale:* the UX spec (§2.2) requires a `window`-exposed function for `page.evaluate()`; fixing the name now lets AT-6 be written before the component.
- **Broker snapshot field name is `topology_slot_to_teammate`** — *Default:* that name (mirrors `topology_edge_stats`), serialized to the UI as `slot_to_teammate`. — *Rationale:* the UX spec names both explicitly (§3.1, §8).
- **Roster fallback teammate set is sourced from `agents` (`liveAgents`)** — *Default:* same source `MiniGraph` uses today. — *Rationale:* preserves the current roster's membership semantics.
- **`--edge-direct` / `--edge-tripped` are existing OKLCH tokens already in `dashboard.html`** — *Default:* reuse the M2 `TopologyEdgePanel` color tokens (direct=green, tee=blue, gated=amber, tripped=red); the deletion-detector asserts the computed stroke against these tokens. — *Rationale:* §1 Q6 says the decoration layer extends `TopologyEdgePanel`'s base; no new palette.
- **The NEW `tests/test_unified_topology_keyed_lookup.py` defines its own stubbed-`/api/state` fixture inline; it does NOT import `five_agent_url`** — *Default:* option (a) from the review — the new file constructs its own dashboard URL serving a stubbed `/api/state` payload (reciprocal-pair `topology_edge_stats` + the `slot_to_teammate` field) inside the test module, using the same module-scoped Playwright `page` fixture from `tests/conftest.py`. It does NOT depend on `five_agent_url`, which is module-local to `tests/dashboard/test_roster_spotlight.py:149` (not in `conftest.py`) and therefore not importable. — *Rationale:* keeps the slice footprint to the single new file (no promotion of a fixture to shared `conftest.py`, no edit to `test_roster_spotlight.py`); the keyed-lookup test needs a bespoke reciprocal-pair payload anyway, which the existing roster fixture does not provide.
- **Sibling Playwright tests in tasks 2 and 3 reuse their own module-local fixtures** — *Default:* the additive `slot_to_teammate` field is injected through whatever stubbed-`/api/state` mechanism each existing test module already uses (e.g. the module-local `five_agent_url` in `test_roster_spotlight.py`); no fixture is relocated. — *Rationale:* matches the established per-module dashboard-test pattern and avoids touching shared files.

## Open Questions

- (none)

## Validation

Automated end-to-end gate — the full suite must pass with the Playwright browser installed:

```bash
uv run pytest
```

Human-judged mockup-parity step (AC-4 + AC-7), run after feature-review PASS. Open `doc/design/mockups/unified-topology-view-mock.html` in a browser and compare against the shipped dashboard left rail across the mockup's three states. **Pass criteria:** (1) roster-only state renders `graph LR` lead+spokes with neutral edges and no badges; (2) active 3-node state renders the `planner→implementor→reviewer` chain with the lead bridging the gated edge, the reciprocal `implementor↔reviewer` pair correctly colored (one green/healthy, one red/thick/tripped — BC-03), one tee edge, and at least one node with a pulsing activity border while another is idle; (3) clicking a decorated edge opens the selected-edge panel with mode/exchange header, a promote control (hidden for already-gated/tripped edges), and a message log. **Fail criteria:** any swapped reciprocal-edge decoration, a second/leftover graph in the rail, a roster state that errors or spins, or activity not attaching to an active-topology node. Differences from the mockup must be justified in the PR description.

## Task Breakout

```yaml
tasks:
  - name: broker-snapshot-slot-mapping
    description: |
      Add `topology_slot_to_teammate: Mapping[str, str]` to `BrokerSnapshot`
      in claude_crew/broker.py, aggregated from all recorded topologies with
      last-write-wins on slot collision — mirroring the existing
      topology_edge_stats aggregation (~L1204-1234). Pure read rollup; no
      routing/breaker change. Add broker-level unit tests in
      tests/test_shape_broker.py for the single-topology map (AT-1) and the
      multi-topology last-write-wins merge (AT-9).
    dependsOn: []
    acceptanceTests: [1, 9]
    taskTouches:
      - "claude_crew/broker.py"
      - "tests/test_shape_broker.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_shape_broker.py -q
  - name: serialize-slot-to-teammate
    description: |
      Surface the aggregated mapping on the /api/state per-cli payload in
      claude_crew/ui_server.py `_build_local_instance` (~L461), as a key
      `"slot_to_teammate": dict(snapshot.topology_slot_to_teammate)` sibling to
      `topology_edge_stats` (NOT nested inside each edge dict). Leave the
      topology_edge_stats serialization byte-for-byte unchanged. Update
      tests/test_circuit_breaker.py payload assertions to tolerate/verify the
      additive key (subset, not dict-equality). Claims AT-2.
    dependsOn: [broker-snapshot-slot-mapping]
    acceptanceTests: [2]
    taskTouches:
      - "claude_crew/ui_server.py"
      - "tests/test_circuit_breaker.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_circuit_breaker.py -q
  - name: unify-topology-graph-component
    description: |
      Replace MiniGraph (~L1770) and TopologyEdgePanel (~L1565) in
      claude_crew/ui/dashboard.html with a single TopologyGraph component
      mounted at the current MiniGraph site (~L2735). Active topology: mermaid
      `graph TD`, lead as a first-class node, gated edges routed
      peer-->lead-->peer; foreignObject node cards (slot label + 8-char
      teammate-id) with activity on the node BORDER (color + 1.6s pulse + corner
      dot) joined via cli.slot_to_teammate, and routing on the EDGES (stroke
      color/width + badge + 550ms exchange pulse) via a KEYED lookup
      `window.mapEdgeStatsToPaths(svgRoot, edgeStats)` (id-parse primary,
      LS-/LE- class fallback, positional last-resort+console.warn). Roster
      fallback (empty topology_edge_stats): same component, `graph LR` star,
      lead-->teammate_* neutral grey edges, no badges/click, legend hidden,
      subtitle "roster - no shape instantiated". All /edge-log and /edge-promote
      fetches keep crew_id in the path. Remove lead-spoke motion pulses
      (deliberate). Retarget broken sibling Playwright assertions in
      test_edge_dashboard.py (AT-3 one-graph, AT-8 activity-join),
      test_roster_spotlight.py (AT-4 roster fallback), test_dashboard_mermaid.py
      and test_dashboard_render.py (AT-7 multi-instance + general selectors).
      Claims AT-3, AT-4, AT-7, AT-8.
    dependsOn: [serialize-slot-to-teammate]
    acceptanceTests: [3, 4, 7, 8]
    taskTouches:
      - "claude_crew/ui/dashboard.html"
      - "tests/test_edge_dashboard.py"
      - "tests/dashboard/test_roster_spotlight.py"
      - "tests/dashboard/test_dashboard_mermaid.py"
      - "tests/test_dashboard_render.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_edge_dashboard.py tests/dashboard/test_roster_spotlight.py tests/dashboard/test_dashboard_mermaid.py tests/test_dashboard_render.py -q
  - name: keyed-lookup-deletion-detector-test
    description: |
      Author the NEW green-suite Playwright file
      tests/test_unified_topology_keyed_lookup.py. The file defines its OWN
      stubbed-/api/state fixture inline (a reciprocal-pair topology_edge_stats
      payload plus the slot_to_teammate field) — it does NOT import
      five_agent_url (module-local to test_roster_spotlight.py, not importable);
      it reuses only the module-scoped Playwright `page` fixture from
      tests/conftest.py. (1) DOM-level deletion-detector (AT-5): render with a
      reciprocal pair (a,b,direct,healthy,8x)+(b,a,direct,tripped,1x), query
      path.flowchart-link, assert each rendered edge carries its OWN stroke
      color (green vs red), thickness (not-3px vs 3px), and badge (`direct 8`
      vs `direct ⚡`), keyed by parsing path id / LS-/LE- classes. (2)
      Unit-level (AT-6): call window.mapEdgeStatsToPaths via page.evaluate()
      across all five branches (source-order, mermaid-reordered, reciprocal,
      malformed-id->class-fallback, both-fail->positional+warn) in the same
      Playwright session. Claims AT-5, AT-6.
    dependsOn: [unify-topology-graph-component]
    acceptanceTests: [5, 6]
    taskTouches:
      - "tests/test_unified_topology_keyed_lookup.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_unified_topology_keyed_lookup.py -q
```

## Design Notes

- The new Playwright file and all sibling dashboard tests require `uv run playwright install chromium` once; the module-scoped Playwright `page`/`browser` fixtures live in `tests/conftest.py` (~L83-153) and are reused — do not introduce a new browser fixture. Per-module `/api/state`-stub fixtures (e.g. `five_agent_url`) are module-local, NOT in conftest; the new keyed-lookup test defines its own inline stub rather than importing one.
- BC-03 regression guard: the deletion-detector (AT-5) is the load-bearing test. It must assert resolved-by-endpoint identity, never trust path order. If a future mermaid upgrade changes the id separator, the `/^L[-_](.+?)[-_](.+?)[-_]\d+$/` tolerant regex plus the `LS-`/`LE-` class fallback keep the lookup correct through single-channel drift.
- Same-file serialization order is encoded as task edges: `broker-snapshot-slot-mapping` → `serialize-slot-to-teammate` → `unify-topology-graph-component` → `keyed-lookup-deletion-detector-test`. The four tasks touch disjoint file sets, so no two write the same file.
