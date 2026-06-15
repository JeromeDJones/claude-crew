# Unified Topology View — UX Spec

**Status:** UX-hardened spec, ready for repo-react build.
**Author:** Builder (frontend-design pass), 2026-06-14.
**Spec input:** `doc/design/unified-topology-view.md` (the brief).
**Mockup:** `doc/design/mockups/unified-topology-view-mock.html` (self-contained, open in browser).
**Scope:** **presentation + one additive serialization** — no changes to routing/breaker behavior, `EdgeStat`, `promote_edge`, `/edge-log`, or `/edge-promote`. One additive field is surfaced on `/api/state`: `slot_to_teammate` (already on the broker's `Topology` dataclass; not currently serialized to the UI). The unified view is a rewrite of `MiniGraph` + `TopologyEdgePanel` into a single component (`TopologyGraph`) that consumes the existing `/api/state` fields plus the newly-surfaced `slot_to_teammate`.

---

## 0. Naming

The unified component replaces both `MiniGraph` (lead-centric SVG roster hub) and `TopologyEdgePanel` (M2 mermaid edge overlay). Working name: **`TopologyGraph`**. The dashboard left-rail header reads **"Topology"** in both the active-topology and roster-fallback states — the header does not change between states, only the subtitle does ("active shape: <label>" vs "roster — no shape instantiated").

---

## 1. The six design decisions

### Q1 — Layout: mermaid `graph TD` with `lead` as a first-class node

**Decision.** Single mermaid `graph TD` rendering. The `lead` is rendered as a first-class node, not implicit. When a topology is instantiated, peer edges go peer→peer; gated edges go `peer --> lead --> peer` (the lead sits visibly on the path). When no topology is instantiated, the graph degrades to `lead --> teammate_a`, `lead --> teammate_b` … — the same star the roster hub renders today, just drawn by mermaid in `graph LR` orientation for left-rail width.

**Rationale.** The operator's mental model from the brief is "watch the crew talk + see gated-through-lead at a glance." A force/radial layout (option 1b) hides edge identity once edges overlap. Implicit lead-routing (option 1c) is invisible — gated edges look identical to direct edges, defeating the point of gating. A DAG with explicit `lead` makes both relationships legible: peer arrows for direct/tee/tripped, `lead` always visibly bridging gated edges.

**Trade-off.** Mermaid auto-layout sometimes produces non-ideal placement (lead drifts to a corner). Acceptable for v1 — readability beats pixel-perfect layout, and the M2 panel already pays this cost.

### Q2 — Node identity: slot label primary, teammate-id secondary, activity dot in corner

**Decision.** A node is rendered as a `foreignObject` HTML card (mermaid v11 supports this — already used in the artifact-md path). Contents:

```
┌──────────────────┐
│ ● planner        │   ← role-coloured activity dot + slot label (bold, 12px)
│   a7b3c2d8       │   ← 8-char teammate-id (mono, 10px, --fg-3)
└──────────────────┘
```

When the role is `lead`, the slot label reads `lead` and there is no teammate-id second line (the lead is the session itself, not a teammate row). When the unified view is in **roster fallback** (no shape), the slot label is the teammate-id and the second line is empty (back-fills the old `p.id.slice(0,8)` text).

**Border colour** encodes activity status (same OKLCH tokens as the dot today): `--st-thinking` amber, `--st-tool` purple, `--st-waiting` blue, `--st-idle` grey. **Border pulse** (1.6s ease-in-out) replays the existing `.dot.thinking` / `.dot.tool-use` animation at the node level.

**Rationale.** Slot-first matches the routing layer's vocabulary (`EdgeStat.from_slot`); id-second preserves the today-hub's "who is this teammate" answer. Same node, both layers. Reconciles the M2 vs hub identity split that forced two graphs.

### Q3 — Activity + routing on one node without overload

**Decision.** Activity and routing live on **different visual channels** of the same node — they never compete for the same pixel.

| Channel             | Layer    | Encodes                                              |
| ------------------- | -------- | ---------------------------------------------------- |
| Node border colour  | activity | thinking / tool-use / waiting / idle                 |
| Node border pulse   | activity | active-turn motion (replaces today's spoke pulses)   |
| Corner dot          | activity | redundant high-affordance status (accessibility)     |
| Edge stroke colour  | routing  | mode: direct/tee/gated/tripped                       |
| Edge stroke width   | routing  | tripped (thicker, 3px) vs normal (2.5px)             |
| Edge label badge    | routing  | mode + exchange count (`tee 4` / `gated ⚡`)         |
| Edge pulse (550ms)  | routing  | exchange-count increment (M2 behaviour, preserved)   |

A node that is `tool-use` (purple border, pulsing) and the endpoint of a tripped `direct` edge (red, thick) reads as **"this teammate is actively using a tool, and one of its routes is in breaker"** — both facts visible at the same glance without one obscuring the other.

**Lead-spoke pulses (today's `animateMotion` along teammate→lead lines) are removed** — they collide with edge-stroke meaning in the unified view (a pulse moving along a peer edge would suggest a message routing on that edge, which is misleading for activity). Activity motion now lives entirely on the node border. This is the deliberate behaviour change vs M2; called out explicitly so the reviewer doesn't flag it as regression.

### Q4 — No-topology fallback

**Decision.** When `topology_edge_stats` is empty or missing, render the same mermaid graph with:

- **Orientation: `graph LR`** for the roster fallback (active topology uses `graph TD`). The roster star is wide-and-shallow — `lead` with N teammate spokes — and the left rail is narrow (~320px). TD stacks the teammates vertically below `lead`, eating rail height; LR places them in a row beside `lead`, fitting the rail's aspect ratio. The active topology stays TD because real shapes (`planner → implementor → reviewer`) read top-down.
- `lead` node (mermaid auto-places).
- One node per live teammate (from `agents`, same source the hub uses today).
- Edges `lead --> teammate_*` in **neutral grey** (`--line`, 1.5px), **no badge**, **no click handler**, **no pulse**.
- Subtitle reads `roster — no shape instantiated`.
- Legend hidden (no modes to legend).
- Selected-edge panel inert (nothing to select).

The same component renders both states — the only branches are (a) the orientation directive (`graph LR` vs `graph TD`), (b) the edge generation, and (c) the legend visibility. This is the explicit empty-state-as-degenerate-state requirement from §2 of the brief.

### Q5 — Multi-instance: preserve `crew_id` on every per-edge fetch

**Decision.** Unchanged from M2 — and called out here so the reviewer enforces it:

- `TopologyGraph` receives `crewId` from its parent (`<TopologyGraph cli={cli} agents={liveAgents}/>`, then `crewId={cli.id}` internally — same as M2 today).
- Every fetch is path-keyed: `GET /edge-log/${crewId}/${from}/${to}` and `POST /edge-promote/${crewId}/${from}/${to}`.
- **No same-origin shortcut** (e.g. `/edge-log/${from}/${to}` without crewId). This is the CLAUDE.md multi-instance trap: leader serves rows from follower brokers; a same-origin fetch hits the leader's broker and 404s for any row belonging to a follower.
- **Acceptance test:** multi-instance e2e — spawn two instances, instantiate a shape on the follower, click an edge in the leader's view, assert the leader proxies to the follower (mirroring `_proxy_tool_output`). This is the same gate M2 had (AT#13); the unified view must not regress it.

### Q6 — Renderer: mermaid + post-render SVG-walk decoration (extends `TopologyEdgePanel`'s base)

**Decision.** Mermaid `graph TD`, sanitized via DOMPurify, decorated post-render by walking the SVG. The decoration layer extends what `TopologyEdgePanel` does today with three additions:

1. **Node decoration** (new): walk `g.node` elements, look up the node's role via id, paint border + corner dot + start pulse animation from per-node activity data.
2. **Edge decoration** (existing M2): walk `path.flowchart-link`, paint stroke colour from mode, attach click handler for `/edge-log`. **But now keyed**, not positional (see §2).
3. **Foreign-object node cards** (new): the slot/id label inside each node is HTML in a `foreignObject`, reusing the same foreignObject legibility fix `renderMermaidBlocks` already applies.

Hand-drawn SVG (today's `MiniGraph`) is dropped — it cannot represent arbitrary peer edges without re-inventing a layout engine. The hand-drawn radial pulses are replaced by node-border pulses (see Q3).

---

## 2. BC-03 — keyed edge mapping (the bug this slice fixes)

**Current bug (M2).** `TopologyEdgePanel` maps `links[i] → edgeStats[i]` positionally. Mermaid v11 reorders edge paths during layout, so in a 3-node topology with a reciprocal back-edge the healthy `tee` back-edge inherited the tripped `direct` edge's stats and rendered red "gated⚡" — operator misreads a healthy edge as runaway.

**Fix — key by `(from_slot, to_slot)` with defence-in-depth lookup.** Mermaid v11 encodes the endpoints of each edge in **two** places on the `path.flowchart-link` element:

- **id** of the form `L_${from}_${to}_${n}` (mermaid has shipped both `_` and `-` separators across recent versions — accept both with `/^L[-_](.+?)[-_](.+?)[-_]\d+$/`).
- **classes** `LS-${from}` and `LE-${to}` (source/target tokens).

The decoration walk:

1. Selects `path.flowchart-link` elements.
2. **Primary lookup:** parses the id with the tolerant regex to recover `(from, to)`.
3. **Fallback lookup:** if the id doesn't match, reads `LS-*` / `LE-*` class tokens.
4. Resolves the `EdgeStat` via a precomputed `Map<"from→to", EdgeStat>`.
5. **Last-resort positional** mapping only if both fail — logged as `console.warn` (signals mermaid changed its emit format; should never fire in practice).

Two independent endpoint signals from mermaid means a future mermaid upgrade that drops one of them still leaves the lookup correct — the keyed mapping survives a single channel of upstream drift.

**Reciprocal edges.** When both `(a, b)` and `(b, a)` are present, mermaid emits **two** edge `<g>` elements with disambiguated ids (`L_a_b_0` and `L_b_a_0`). Each gets its own `EdgeStat` lookup — no aliasing. The mockup state-iii demonstrates this with `implementor → reviewer (direct, 8 exchanges)` and `reviewer → implementor (direct, 1 exchange, tripped)` rendering correctly, never swapping.

**Test surface — green-suite Playwright deletion-detector (required).** The M2 positional bug slipped to master only because no existing test exercised a reciprocal pair. claude-crew already runs pytest-playwright in the green suite (`tests/test_edge_dashboard.py`, `tests/dashboard/test_dashboard_mermaid.py`, etc. drive the live dashboard in a real browser inside `uv run pytest`). The keyed-lookup fix gets a real green-suite Playwright test in the same style. Two test surfaces, both required:

1. **DOM-level deletion-detector (the strong assertion) — Playwright.** New test file `tests/test_unified_topology_keyed_lookup.py`. Renders the dashboard with a stubbed `topology_edge_stats` containing a reciprocal pair carrying **distinct** mode/tripped/exchange values:
   - `(a, b, mode=direct, tripped=false, exchanges=8)`
   - `(b, a, mode=direct, tripped=true,  exchanges=1)`

   Assertions, all by DOM-querying the rendered mermaid SVG:
   - Two `path.flowchart-link` elements exist.
   - The path whose endpoint pair is `(a, b)` has stroke = `--edge-direct` (green) and is NOT thick (≠ 3px); its edge label shows `direct 8`.
   - The path whose endpoint pair is `(b, a)` has stroke = `--edge-tripped` (red) AND is thick (3px); its edge label shows `direct ⚡`.
   - Endpoint identity is resolved by reading the path's id (or `LS-*` / `LE-*` classes), the SAME mechanism the production keyed-lookup uses — so the test asserts the contract the production code relies on.

   **This test fails if anyone reverts to positional indexing** — the reciprocal pair's stats swap, the stroke colors swap, the assertions don't hold. That's the deletion-detector property the brief requires.

2. **Unit-level keyed-lookup (the fast assertion) — `page.evaluate()`.** The keyed-lookup function (`mapEdgeStatsToPaths(svgRoot, edgeStats) → Map<HTMLElement, EdgeStat>` or equivalent) is exposed on `window` in dashboard.html so the Playwright test can call it directly via `page.evaluate()`. Cases: (a) source-order match, (b) mermaid-reordered paths, (c) reciprocal pair, (d) malformed id falls back to `LS-`/`LE-` classes, (e) both fail → returns the positional fallback. Cheap, deterministic, runs in the same Playwright session so it pays no extra browser-spin-up cost.

**What stays out of the green suite (M2 precedent unchanged).** Pixel-level checks (exact color value comparisons, font legibility at small sizes), layout-stability checks (exact mermaid x/y placement), and headless-Chrome visual diffs. The M2 Out-of-Scope precedent covered THOSE — it never exempted mapping correctness. Mapping correctness IS green-suite, via the DOM assertions above. The previous draft of this spec got this wrong; corrected.

---

## 3. Data contract consumed (one additive field)

From `/api/state[cli]`:

| Field                       | Source         | Used for                                            | Status         |
| --------------------------- | -------------- | --------------------------------------------------- | -------------- |
| `cli.id`                    | broker         | `crewId` for per-edge fetches                       | unchanged      |
| `cli.label`                 | broker         | subtitle                                            | unchanged      |
| `cli.topology_edge_stats`   | `EdgeStat[]`   | edge list + per-edge mode/tripped/exchanges         | unchanged      |
| `cli.slot_to_teammate`      | `Topology`     | join activity (per-teammate) onto routing (per-slot) | **ADDITIVE**   |
| `cli.cwd / branch / uptime` | broker         | footer (unchanged)                                  | unchanged      |
| `agents[]` (`liveAgents`)   | `Teammate[]`   | activity status keyed by teammate-id                | unchanged      |

From each `EdgeStat`: `from_slot`, `to_slot`, `mode` (`direct`/`tee`/`gated`), `tripped` (bool), `exchanges` (int).

### 3.1 The new field — `cli.slot_to_teammate`

**Why it's required in this slice (not deferrable):** the unified view's headline behavior (Q3) is fusing activity onto the routing graph at each node. Activity lives on `Teammate.status` keyed by teammate-id; routing lives on `EdgeStat.from_slot/to_slot` keyed by slot. The join requires `slot → teammate_id`. The previously-proposed `agent.role === slot` fallback is fragile: a teammate's pack `role` (e.g. `subagents/implementor.md` → role `implementor`) is not guaranteed to equal the shape's slot label (`Topology` slots are author-defined and can be `impl_a`, `lead_planner`, etc.). When they don't match, activity silently fails to attach on every node of an active topology — the core feature is broken. Surfacing the authoritative mapping is the only correct fix.

**Why it's safe / minimal:**

- The field already exists on the broker's `Topology` dataclass (`broker.py:151`, wrapped in a `MappingProxyType` for read-safety).
- It's already serialized server-side in the `instantiate_shape` response (`server.py:1027`) and used in `_local_edge_log_response` (`ui_server.py:987-988`).
- This slice surfaces it on the existing `/api/state` payload only — no new endpoint, no schema migration, no behavior change. Mirrors the established additive pattern (`topology_edge_stats` was added the same way at `ui_server.py:461`).

**Exact serialization shape.** Add to the per-`cli` payload at `ui_server.py:461` (sibling to `topology_edge_stats`, **not** nested inside each edge dict):

```python
"slot_to_teammate": dict(_merged_slot_to_teammate(snapshot)),
```

Where `_merged_slot_to_teammate(snapshot) → Mapping[str, str]` flattens `snapshot.topology_slot_to_teammate` (a new field on `BrokerSnapshot` mirroring the existing `topology_edge_stats` pattern at `broker.py:1234`). When multiple topologies are recorded, **last-write-wins** is the merge rule for v1 — same slot label across topologies should resolve to the same teammate-id in practice (it's the same role doing the same work); if it doesn't, the last-instantiated topology wins. A regression test covers the multi-topology merge edge case. If multi-topology slot collision later becomes a real surface, the field can be upgraded to `slot_to_teammate_by_topology: list[{shape_name, slot_to_teammate}]` without breaking the v1 consumer.

**Consumer logic in `TopologyGraph`.** For each node (slot) in the active topology:

```js
const teammateId = cli.slot_to_teammate?.[slot];
const teammate   = teammateId ? agents.find(a => a.id === teammateId) : null;
const status     = teammate?.status ?? "idle";  // unknown slot → idle (defensive)
```

For roster fallback (no topology), no join is needed — nodes ARE teammates, so `status` reads directly from `agents[].status`.

---

## 4. States to support (mockup demonstrates all three)

1. **Roster-only fallback** — no shape instantiated. `lead` + 3 teammates, neutral grey edges, no badges, no click handlers, subtitle = "roster — no shape instantiated", legend hidden.
2. **Active 3-node topology** — `planner → implementor → reviewer` chain with a mix of modes, plus the lead as a first-class node bridging a gated edge. Activity: one node thinking, one tool-use (border pulsing), one idle. Includes:
   - A **reciprocal pair** (`implementor ↔ reviewer`, both `direct`, distinct stats — one healthy, one tripped) — the BC-03 fix demonstration.
   - A **gated edge** routed through `lead` (`planner --gated--> lead --gated--> implementor`) showing the operator the gated route visibly bridges the lead.
   - A **tee edge** (`planner --tee--> reviewer`) for observability mode coverage.
   - One **tripped edge** rendered red+thick.
3. **Selected-edge panel** — click any decorated edge → panel opens below with mode/exchange-count header, "Promote to gated" button (hidden for already-gated/tripped edges per M2), and a stubbed message log (10 dummy entries to show the layout).

The mockup ships a state-switcher (three buttons at top) so Jerome can toggle without page reloads.

---

## 5. Acceptance criteria (for the implementor + reviewer)

1. **AC-1 (one graph).** The dashboard left rail renders exactly one graph. The old `TopologyEdgePanel` mount point and the hand-drawn `MiniGraph` SVG are both gone. `MiniGraph` is renamed/replaced by `TopologyGraph`.
2. **AC-2 (no-topology fallback).** With `topology_edge_stats = []`, the graph renders a roster star (`lead` + teammates, neutral edges, no badges). No console errors. No "loading" flicker.
3. **AC-3 (reciprocal edges keyed — green-suite deletion-detector).** A new Playwright test `tests/test_unified_topology_keyed_lookup.py` renders the dashboard with `topology_edge_stats` containing `(a,b, direct, healthy, 8x)` + `(b,a, direct, tripped, 1x)`. It asserts via DOM queries on `path.flowchart-link` that each rendered edge carries its OWN correct stroke color, thickness, and edge-label badge — keyed by parsing the path's id / `LS-`/`LE-` classes (same mechanism as production). The test fails if positional indexing is reintroduced (stats swap → strokes swap → assertions fail). The same test file also calls the keyed-lookup function via `page.evaluate()` for fast unit-level assertions on all four lookup branches (id-parse, `LS-`/`LE-` class fallback, positional last-resort, malformed). This test is part of the green suite — `uv run pytest` must run it.
4. **AC-4 (activity + routing co-exist).** A node with `status = "tool-use"` whose incident edge is `tripped` renders both the border pulse (activity) and the red thick edge (routing) simultaneously. Visual check via mockup; no automated test.
5. **AC-5 (multi-instance preserved).** All `/edge-log` and `/edge-promote` fetches go to `/${crewId}/${from}/${to}`. Multi-instance e2e from M2 (AT#13) is re-run unchanged and still passes.
6. **AC-6 (one additive field, no behavior change).** The unified view consumes `topology_edge_stats`, `agents`, `cli.*`, and the **newly-added** `cli.slot_to_teammate` (see §3.1). No new endpoints; no changes to routing/breaker behavior; no changes to `EdgeStat`, `promote_edge`, `/edge-log`, `/edge-promote`, or the multi-instance proxy contract. The additive field is a serialization of an existing broker dataclass field (`Topology.slot_to_teammate`) — diff visible in `ui_server.py` (the new payload key) and `broker.py` (the new `BrokerSnapshot` field that aggregates the per-topology mappings); nothing else.
7. **AC-7 (mockup parity).** The shipped UI matches the three mockup states for layout, colour, and interaction. Differences must be justified in the PR description.

---

## 6. Non-goals (refined from brief §5)

- No changes to `EdgeStat`, `topology_edge_stats`, `/edge-log`, `/edge-promote`, broker `promote_edge`.
- No new routing/breaker behaviour.
- No pixel-level or visual-diff checks in the green suite (M2 precedent). **Mapping-correctness IS in the green suite** — Playwright DOM assertions per §2/AC-3. The original M2 out-of-scope phrasing covered pixels, never mapping; the round-1 spec mis-broadened it and is corrected here.
- No new `/api/state` endpoint; no new schema. Surfacing `slot_to_teammate` is additive serialization of an existing broker field, not a schema change.

---

## 7. Open tensions — resolved this round

1. **Lead-spoke pulse removal — RESOLVED (drop).** Confirmed: spoke-motion goes away; activity rides node border. No third channel.
2. **Slot→teammate id mapping — RESOLVED (pull into this slice).** Surfaced as additive field on `/api/state`; see §3.1. The presentation/data-contract split was the wrong boundary — the join is intrinsic to the unified view's core behaviour, not a sibling concern.
3. **Roster fallback orientation — RESOLVED (`graph LR`).** Roster uses LR; active topology uses TD. See §Q4. Mockup updated.

No open tensions remaining. Spec is ready for repo-react build.

---

## 8. Sibling files this slice will edit (for the planner's `taskTouches`)

The grep'd footprint, with per-file justification. Listing this here so the repo-react planner's task-touches annotation is accurate from the start — co-declaring same-file dependencies prevents the kind of parallel-task collision the workflow guide warns about.

**Production code (definite edits):**

- `claude_crew/ui/dashboard.html` — replace `MiniGraph` + `TopologyEdgePanel` with `TopologyGraph`; expose the keyed-lookup function on `window` for the Playwright unit-level assertion.
- `claude_crew/ui_server.py` — add `slot_to_teammate` to the per-`cli` payload (around L461, sibling to `topology_edge_stats`).
- `claude_crew/broker.py` — add `topology_slot_to_teammate: Mapping[str, str]` to `BrokerSnapshot` (around L1234, mirroring the existing `topology_edge_stats` aggregation).

**Tests (definite edits, grep-confirmed):**

- `tests/dashboard/test_roster_spotlight.py` — 1× `MiniGraph` ref. Rename forces update; assertions targeting the old radial SVG will need to retarget the unified mermaid graph.
- `tests/test_edge_dashboard.py` — 18× `topology_edge_stats`, 43× edge-log/promote, 2× `slot_to_teammate`. This is the M2 panel test; all DOM assertions that target the M2 panel's selectors need retargeting at the unified component. Highest-impact test edit in this slice.
- `tests/test_circuit_breaker.py` — 13× `topology_edge_stats`, 2× `slot_to_teammate`. Likely asserts on `/api/state` payload shape; the additive `slot_to_teammate` field must be tolerated (if any assertion is dict-equality rather than subset, update).

**Tests (probable edits, Playwright-rendered dashboard tests):**

- `tests/dashboard/test_dashboard_mermaid.py` — 32× `page.`. Renders mermaid blocks. May break if it asserts on graph DOM structure that changes with the unified component.
- `tests/test_dashboard_render.py` — 63× `page.`. The heaviest Playwright dashboard test in the repo; likely needs at least minor updates for the unified component's selectors.

**Tests (NEW file):**

- `tests/test_unified_topology_keyed_lookup.py` — the green-suite Playwright deletion-detector for BC-03 (see §2 / AC-3).

**Tests confirmed NOT touched** (Playwright but no reference to the changed symbols, per per-file grep): `tests/dashboard/test_shape_resurface.py`, `tests/dashboard/test_dashboard_artifact_xss.py`, `tests/dashboard/test_startup_notices.py`, `tests/test_dashboard_tool_output.py`, `tests/test_shape_render.py`. Planner should still cross-check at build time — if any new assertion lands here between now and the build, recheck.

**Same-file dependency graph for parallelization within this slice.** `broker.py` change blocks `ui_server.py` change (the new `BrokerSnapshot` field must exist before the payload reads it); `ui_server.py` change blocks the Playwright tests (the new field must serialize before the tests can stub it). `dashboard.html` change can proceed in parallel with the broker change but must complete before the dashboard Playwright tests are updated. Encode these as task edges, not as "tasks in the same milestone."
