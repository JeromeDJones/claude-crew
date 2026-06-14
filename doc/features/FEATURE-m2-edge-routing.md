# Feature: m2-edge-routing — Workflow Shape Composition M2

**Status:** SHIPPED — 2026-06-13
**Validation:** PASS — 1537 passed, 34 skipped, 1 xfailed, 52 warnings (181.43s full-suite run)
**Feature tests:** +75 (edge-routing 19, circuit-breaker 17, scoped-send 22, edge-dashboard 17)
**Cycles:** 4 tasks × up to 2 review cycles each

---

## Purpose

M2 makes the crew graph **execute**. The `Topology` recorded by M0's `instantiate_shape` becomes the live routing rail: messages between teammates are authorized, filtered, and observable according to their declared edge mode. This is the payoff of the Workflow Shape Composition arc — the approved graph is no longer just a legible data structure; it constrains behavior at runtime.

---

## Acceptance Tests

| # | Description | Task |
|---|-------------|------|
| AT#1 | `direct` edge: message delivers to recipient inbox only, no lead notification | broker-edge-routing |
| AT#2 | `tee` edge: message delivers to recipient inbox AND a CC copy to lead | broker-edge-routing |
| AT#3 | `gated` edge: message wraps to lead only, not in recipient inbox | broker-edge-routing |
| AT#4 | No-topology / no-declared-edge fallback: routes as gated | broker-edge-routing |
| AT#5 | `promote_edge` overrides mode at runtime; subsequent sends honor the override | broker-edge-routing |
| AT#6 | Circuit breaker trips after `CIRCUIT_BREAKER_MAX_EXCHANGES` on a `tee`/`direct` edge: edge auto-promotes to gated, control envelope to lead, triggering message rerouted as gated wrapper. Idempotent. | broker-circuit-breaker |
| AT#7 | Reciprocal direct exchanges below budget do NOT trip the breaker. Budget-exceeded is the sole trip condition (no deadlock detector). | broker-circuit-breaker |
| AT#8 | Neighbor injection: each teammate's system prompt contains its declared neighbors derived from `shape.edges` | scoped-send-teammate |
| AT#9 | `send_scoped` authorization: rejects sends to non-declared neighbors; allows send to `LEAD_ID` always; resolves slot names and teammate-ids | scoped-send-teammate |
| AT#10 | Reciprocal direct ping-pong (A→B→A→B) routes off-lead at every hop; breaker does not trip below budget | scoped-send-teammate |
| AT#11 | `/api/state` carries `topology_edge_stats` per crew: `{from_slot, to_slot, mode, exchanges, tripped, crew_id}` | dashboard-edge-observability |
| AT#12 | `GET /edge-log` + `POST /edge-promote` endpoints present and functional; promote changes subsequent routing | dashboard-edge-observability |
| AT#13 | Multi-instance: leader proxies edge-log and edge-promote to the correct follower by `crew_id` | dashboard-edge-observability |

*AT#13 Playwright (on-graph overlay visual verification) deferred to live/Playwright tier per spec.*

---

## Tasks

### Task 1: `broker-edge-routing` (PASS, cycle 0)

**Scope:** Core routing engine in `broker.py`.

**Deliverables:**
- `UnauthorizedEdgeError` exception class
- `Broker._edge_overrides: dict[tuple[str,str], str]` state
- `Broker.send()` short-circuit for teammate→teammate messages → `_send_routed()`
- `Broker._send_routed()`: `direct` / `tee` / `gated` dispatch
- Private helpers: `_active_topology_for`, `_id_to_slot`, `_edge_mode`, `_resolve_routing_mode`, `_resolve_scoped_recipient`
- Public API: `authorize_send`, `send_scoped`, `promote_edge`
- Tests: `tests/test_edge_routing.py` (new, 19 tests); `tests/test_broker.py` (additions)

**Note:** `tests/test_broker.py` was not in `taskTouches` (adjudicated Info — legitimate under-declaration; same class as subsequent factory gap in task 3).

### Task 2: `broker-circuit-breaker` (PASS, cycle 1 after spec amendment)

**Scope:** Circuit breaker in `broker.py`.

**Spec amendment (coordinator, 2026-06-13):** The spec-proposed 2-node deadlock detector (`_edge_pending` flags, AT#7 original) was dropped. "A waits B waits A" is not well-defined at a message-bus level; reply-clearing makes both-pending unreachable (dead code). AT#7 redefined: reciprocal-below-budget does NOT trip; budget-exceeded is the sole trip.

**Deliverables:**
- `CIRCUIT_BREAKER_MAX_EXCHANGES = 8` (module constant)
- `EdgeStat` frozen dataclass (`from_slot`, `to_slot`, `mode`, `exchanges`, `tripped`)
- `BrokerSnapshot.topology_edge_stats: tuple[EdgeStat, ...]`
- `Broker._apply_circuit_breaker()` — budget-only, idempotent
- `_edge_exchanges`, `_edge_tripped` state; `_edge_pending` **NOT present** (removed by amendment)
- Tests: `tests/test_circuit_breaker.py` (new, 17 tests)

### Task 3: `scoped-send-teammate` (PASS, cycle 1 after REQUEST-CHANGES rework)

**Scope:** In-process MCP `send_to` tool on `SdkTeammate`; neighbor injection; `factories.py` `neighbors=` threading.

**Cycle 0 failure (learning):** Implementor shipped green ATs 8–10 without building the central deliverable (the `send_to` MCP tool), reasoning that the live-SDK integration test being deferred meant the deliverable could wait. Slice-reviewer flipped REQUEST-CHANGES (High). See `m2-edge-routing-debrief-implementor-task-scoped-send-teammate.md` for the post-mortem heuristic (now in BACKLOG BC-01).

**Deliverables:**
- `sdk_teammate.py`: `_build_send_to_mcp_server()` returning `McpSdkServerConfig`; `_send_to_tool` handler calling `broker.send_scoped`; MCP server attached at spawn when `neighbors` non-empty
- `teammate_prompt.py`: `build_teammate_prompt` injects "Neighbors" section when neighbors provided
- `factories.py`: `neighbors=` kwarg threaded through `stub_factory` / `sdk_factory` / `default_factory`
- Tests: `tests/test_scoped_send.py` (new, 22 tests including `TestSendToToolRegistration` structural test)

**Key invariant:** `neighbors=` is derived from `shape.edges` at spawn time — the same edges used for `authorize_send`. System-prompt neighbor list and authorization cannot drift.

### Task 4: `dashboard-edge-observability` (PASS, cycle 0)

**Scope:** `/api/state` edge stats; edge-log / edge-promote endpoints; `TopologyEdgePanel` SVG-walk overlay.

**Deliverables:**
- `ui_server.py`: `topology_edge_stats` on `/api/state` local payload; `GET /edge-log/{crew_id}/{from}/{to}`; `POST /edge-promote/{crew_id}/{from}/{to}`; multi-instance proxy (`_proxy_edge_log` / `_proxy_edge_promote`) following `_proxy_artifact` / `_proxy_shape_approval` pattern; `_PATH_PARAM_RE` guards; leader→follower proxy test (`test_leader_proxies_edge_log_to_follower` — real follower `UIServer.serve()` on free port)
- `dashboard.html`: `TopologyEdgePanel` React component — SVG-walks `.flowchart-link` post-`mermaid.render`; per-mode color; pulse animation on traffic; selected-edge log panel; promote-to-gated control (gated on non-gated/non-tripped edges); re-applies after each render via `useEffect([mermaidSrc, crewId])`; `fetch` URLs carry `crewId` (multi-instance correct)
- Tests: `tests/test_edge_dashboard.py` (new, 17 tests)

---

## Files Changed

| File | Change |
|------|--------|
| `claude_crew/broker.py` | Routing engine + circuit breaker + `send_scoped` + `EdgeStat` + `BrokerSnapshot.topology_edge_stats` |
| `claude_crew/sdk_teammate.py` | In-process `send_to` FastMCP server (`_build_send_to_mcp_server`, `_send_to_tool`) |
| `claude_crew/factories.py` | `neighbors=` kwarg threaded through all three factory paths |
| `claude_crew/teammate_prompt.py` | `build_teammate_prompt` gains `neighbors` param + "Neighbors" section |
| `claude_crew/ui_server.py` | `topology_edge_stats` on `/api/state`; `GET /edge-log`; `POST /edge-promote`; multi-instance proxy |
| `claude_crew/ui/dashboard.html` | `TopologyEdgePanel` React component (SVG-walk overlay) |
| `tests/test_edge_routing.py` | New — 19 tests (AT#1–#5) |
| `tests/test_circuit_breaker.py` | New — 17 tests (AT#6–#7 amended) |
| `tests/test_scoped_send.py` | New — 22 tests (AT#8–#10 + structural registration) |
| `tests/test_edge_dashboard.py` | New — 17 tests (AT#11–#13) |
| `tests/test_broker.py` | Additions (auth-guard helpers) |

---

## Design Decisions

### Budget-only circuit breaker (no deadlock detector)

The original spec proposed a 2-node deadlock detector based on `_edge_pending` flags: if both A→B and B→A were simultaneously "in flight," the second would be rerouted. This was removed by coordinator adjudication (Jerome + Kael, 2026-06-13) because:

1. "A waits B waits A" is not well-defined at a message-bus level — the broker cannot distinguish a blocked peer from one that simply hasn't responded yet.
2. Reply-clearing makes the both-pending state unreachable in practice (dead code).
3. Any non-clearing implementation contradicts the reciprocal-peer-conversation contract (AT#10 verifies that reciprocal direct sends below budget route normally).

**The per-edge exchange budget is the sole runaway guard.** At 8 exchanges, a runaway loop is caught and the lead is notified before it consumes meaningful context.

### Gated fallback is always the default

When no topology exists, or when a topology exists but no edge is declared between two teammates, routing falls back to `gated`. This means the coordinator stays on the wire until an edge is explicitly declared, approved, and instantiated. There is no way for a teammate to accidentally bypass the coordinator without an explicit shape edge.

### Scoped `send_to` as choke-point moat

The in-process MCP server approach (FastMCP on `SdkTeammate`) was chosen over exposing a direct broker API because:
- It is the only way to inject a tool into the teammate's SDK tool surface without granting general broker access.
- The MCP tool surface is already the teammate's sole external interface; adding one tool to it is consistent with the existing abstraction.
- The handler calls `broker.send_scoped` which enforces authorization — the moat is at the broker boundary, not the MCP boundary.

---

## Known Gaps / Backlog

See `doc/BACKLOG.md` [2026-06-13] for the three retro findings:

- **[High, prompt/skill]** Green-untestable-deliverable skip pattern — planner/implementor contract missing structural test mandate for deferred-verification deliverables.
- **[Low, process]** `taskTouches` under-declaration — test files and factory wiring chain.
- **[Low, tooling]** Dashboard `links[i]→edgeStats[i]` positional edge mapping — brittle to mermaid reorder.

The Playwright `TopologyEdgePanel` visual verification (AT#13 deferred) remains unexercised in the green suite.
