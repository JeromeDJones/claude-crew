<!-- vars: SLUG=m2-edge-routing -->

# Spec: m2-edge-routing

## Problem

claude-crew already records a crew's wiring at instantiate time — `broker.py`'s
`Topology` holds `(from_slot, to_slot, mode)` edge triples plus a `slot_to_teammate`
map — but that record is **decorative**. Message routing ignores it entirely:
`broker.send` routes purely by `env.recipient`, teammates hold no send tools and can
only auto-reply to whoever last messaged them (`recipient=env.sender`, hardcoded), and
the per-edge `mode` (`gated`/`tee`/`direct`) recorded since M0 changes nothing. M2 makes
the recorded topology **govern** message flow: per-edge mode is honored by the broker;
teammates can message each other but only along their declared out-edges; and an
autonomous-chatter circuit breaker force-inserts the lead before any peer loop runs away.
The operator can watch edges light up on the dashboard, read an edge's message log, and
step back onto an edge mid-run. The coordinator's moat is preserved by construction: every
cross-teammate message still flows through the broker (sequenced, logged, surfaced), and
the lead can be removed from *relay* but never from *gate*.

## Architecture Overview

The change is concentrated in four existing seams, no new orchestration runtime:

- **`broker.py` — routing + authorization + circuit breaker.** `send` gains a routing
  resolver that consults the active `Topology` to pick a delivery target by edge mode.
  A new `authorize_send` / `send_scoped` path enforces out-edge scoping (loud rejection of
  non-declared recipients). Per-edge exchange counters + a 2-node deadlock detector trip a
  breaker that force-inserts the lead. All logic is broker-level and fully exercisable in
  stub mode — no live SDK needed for the core contract.
- **`sdk_teammate.py` + `teammate_prompt.py` — scoped `send_to` + neighbor injection.**
  Teammates gain an in-process SDK MCP `send_to(recipient, payload)` tool whose handler
  calls `broker.send_scoped`. Adjacency text ("you may consult `reviewer` (direct);
  verdicts arrive from `planner` (gated)") is injected into the system prompt at spawn via
  a new `neighbors=` parameter on `build_teammate_prompt`.
- **`server.py` — wiring.** `instantiate_shape` computes per-node adjacency from the
  shape's edges and threads it (and the neighbor list) into `broker.spawn_teammate`.
- **`ui_server.py` + `ui/dashboard.html` — observability.** `/api/state` emits each
  instance's topology edges + per-edge exchange counts (carrying `crew_id`); a new
  per-instance `GET /edge-log/{crew_id}/{from}/{to}` endpoint (multi-instance proxied) and
  `POST /edge-promote/{crew_id}/{from}/{to}` control flip an edge to `gated`. Live edge
  state is painted **on the rendered mermaid topology SVG** (an on-graph overlay) via a
  post-render SVG-walk decoration layer — edges color by mode, pulse on count increment,
  show a tripped style, and are clickable to open their log.

### Call-site survey

| Call-site | Path | Shape | Notes |
|-----------|------|-------|-------|
| `broker.send` (lead→teammate) | `server.py` `send_to`, `broadcast` | `sender=LEAD_ID` | Must remain unchanged — coordinator may message anyone; bypasses the edge gate. |
| `broker.send` (teammate→lead auto-reply) | `sdk_teammate.py` ~1706 | `recipient=env.sender` (lead) | Must remain unchanged — reports always reach the coordinator; bypasses the edge gate. |
| `broker.send` (teammate→teammate) | NEW via `send_scoped` from the in-process tool | both ids are teammates | The only path the edge gate governs. No-edge → gated fallback; non-declared via scoped tool → rejected. |

Resolution: **one routing resolver in `broker.send`**, gated on whether *both*
endpoints are non-lead teammates. Lead-origin and lead-bound sends short-circuit to
today's behavior. A separate `authorize_send` check guards the teammate-initiated scoped
path so the routing resolver itself never rejects (preserving all existing send semantics
and tests).

## Data / API Contracts

```python
# claude_crew/broker.py — new error + routing/authorization/breaker surface

class UnauthorizedEdgeError(Exception):
    """Raised when a teammate's scoped send targets a non-declared recipient."""

# --- routing resolution (internal) ---
def _active_topology_for(self, sender_id: str, recipient_id: str) -> "Topology | None":
    """Latest recorded Topology whose slot_to_teammate contains BOTH ids; else None."""

def _edge_mode(self, topo: "Topology", from_slot: str, to_slot: str) -> "str | None":
    """Mode of the forward edge (from_slot→to_slot), honoring _edge_overrides
    and breaker trips; None when no such forward edge exists."""

# --- send (existing signature unchanged); new routing applied inside ---
# Routing rule applied to every envelope, AFTER existing dedup/tombstone/unknown checks:
#   recipient == LEAD_ID            → lead channel (UNCHANGED)
#   sender    == LEAD_ID            → recipient inbox (UNCHANGED)
#   else (teammate→teammate):
#     governing forward edge mode:
#       "gated"  → deliver a lead-bound wrapper envelope {gated_for, from, payload}
#                  to LEAD; NOT to recipient inbox
#       "tee"    → deliver original to recipient inbox AND a derived cc envelope
#                  {cc_of, from, to, payload} (new id, recipient=LEAD) to LEAD;
#                  both appended to _log
#       "direct" → deliver original to recipient inbox; appended to _log;
#                  NOT lead-bound
#     no governing forward edge → gated fallback (lead-bound wrapper), today's behavior
#   The circuit breaker may override any tee/direct decision → force gated (see below).

# --- scoped authorization (teammate-initiated directed send) ---
async def send_scoped(self, sender_id: str, recipient: str, payload: Any,
                      *, id: str | None = None) -> "Envelope | None":
    """recipient may be a slot name OR teammate_id OR LEAD_ID.
    Resolves slot→teammate_id via the active topology. Calls authorize_send first
    (raises UnauthorizedEdgeError if recipient is a teammate that is NOT a declared
    out-edge neighbor of sender). On success builds the Envelope and calls send()."""

def authorize_send(self, sender_id: str, recipient_id: str) -> None:
    """No-op when recipient_id == LEAD_ID. Else raises UnauthorizedEdgeError unless
    a forward edge (sender_slot→recipient_slot) exists in the active topology."""

# --- circuit breaker state (per active topology, per directed edge) ---
# _edge_exchanges: dict[tuple[str, str], int]      # (from_slot,to_slot) → count
# _edge_overrides: dict[tuple[str, str], str]      # forced "gated" after a trip / promote
# _edge_pending:   dict[tuple[str, str], bool]     # an unanswered direct/tee exchange in flight
# CIRCUIT_BREAKER_MAX_EXCHANGES: int = 8           # default per-edge budget (configurable arg)
#
# On a tee/direct delivery: increment _edge_exchanges[(f,t)]. When it exceeds the budget,
# OR a 2-node deadlock is detected (_edge_pending[(A,B)] and _edge_pending[(B,A)] both True),
# set _edge_overrides[(f,t)]="gated", route the triggering message to LEAD, and deliver a
# control envelope {type:"circuit_breaker", edge:[f,t], reason:"budget_exceeded"|"deadlock"}
# to LEAD. The edge stays gated until promote_edge resets/confirms it.

def promote_edge(self, from_slot: str, to_slot: str) -> None:
    """Force an edge to 'gated' (operator steps back onto it). Sets _edge_overrides."""

# --- snapshot surface for the dashboard ---
@dataclass(frozen=True)
class EdgeStat:
    from_slot: str
    to_slot: str
    mode: str            # effective mode (honors overrides/trips)
    exchanges: int
    tripped: bool
# BrokerSnapshot gains: topology_edge_stats: tuple[EdgeStat, ...] = ()
```

```python
# claude_crew/teammate_prompt.py
def build_teammate_prompt(role, pack_body, agents, memory_section=None,
                          neighbors: "list[dict] | None" = None) -> str:
    """neighbors entries: {"direction":"out"|"in", "slot":..., "role":..., "mode":...}.
    When non-empty, a SENTINEL_NEIGHBORS section is appended describing the teammate's
    declared out-edges (who it may send_to + mode) and in-edges (who messages it + mode)."""

# claude_crew/broker.py spawn_teammate gains:  neighbors: list[dict] | None = None
#   (threaded to the factory → SdkTeammate → build_teammate_prompt)
```

```
# claude_crew/ui_server.py — new per-instance dashboard endpoints (Starlette routes)
GET  /edge-log/{crew_id}/{from_slot}/{to_slot}
     → local when crew_id == _own_crew_id(); else _proxy_edge_log leader→follower.
       Returns {ok, edge:[from,to], messages:[...]} — broker _log filtered to that
       directed edge (and its cc/gated wrappers). _PATH_PARAM_RE guards every param.
POST /edge-promote/{crew_id}/{from_slot}/{to_slot}
     → local broker.promote_edge OR _proxy_edge_promote leader→follower.
       Returns {ok, edge:[from,to], mode:"gated"}.
# /api/state instance payload gains "topology_edge_stats":[{from,to,mode,exchanges,
#   tripped, crew_id}] — crew_id load-bearing for the multi-instance proxy.

# claude_crew/ui/dashboard.html — on-graph overlay (post-render SVG-walk decoration layer)
#   After mermaid.render() produces the topology graph TD SVG (mermaid@11.4.1, the same
#   renderer the shape-gate proposal card uses), walk the rendered SVG's .flowchart-link /
#   edge-path elements (mermaid v11's SVG edge class names — already referenced for
#   edge-thickening). Key each link to its (from_slot,to_slot) and decorate from
#   topology_edge_stats: per-mode stroke color, an animation pulse when exchanges
#   increments since last render, a tripped/gated style when tripped, click hit-testing
#   that fetches GET /edge-log and renders the log, and a promote control that POSTs
#   /edge-promote. Decoration re-applies on every render (mermaid regenerates the SVG).
```

## Design Decisions

- **The edge gate applies only to teammate→teammate sends** — *Rationale:* the coordinator
  must always be able to message anyone (lead-origin) and always receive reports
  (lead-bound); gating those would break the substrate. *Carried into:* `broker.send`
  routing rule (the two short-circuit branches); test AT#1–#4.
- **Routing never rejects; authorization rejects** — *Rationale:* reconciles the idea's two
  rules ("no-edge falls back to gated" for routing vs. "non-declared recipient rejected
  loudly" for scoped send). `send` always delivers somewhere (gated fallback for no-edge);
  `authorize_send`/`send_scoped` is the only path that raises `UnauthorizedEdgeError`.
  *Carried into:* `authorize_send`, `UnauthorizedEdgeError`, AT#4 (fallback) vs. AT#5/#9
  (reject).
- **Forward-edge governance only; `reverse_mode` stays recorded-only** — *Rationale:*
  `Topology.edges` records forward `(from,to,mode)` triples; honoring `reverse_mode` would
  require changing M0's topology recording and breaking existing `test_shape_broker`
  assertions. Reciprocal traffic (e.g. ping-pong) is expressed by declaring **both**
  directed edges in the shape. *Carried into:* `_edge_mode` (forward lookup), Assumption #1,
  AT#10 (ping-pong shape declares both directions).
- **`gated`/`tee` produce derived lead-bound envelopes, not recipient rewrites** —
  *Rationale:* keeps `get_messages(LEAD)` (filters `recipient==LEAD`) working unchanged; the
  lead sees a wrapper carrying the intended recipient + original payload. *Carried into:*
  `send` routing rule (wrapper `{gated_for,...}` / cc `{cc_of,...}`), AT#1, AT#2.
- **Circuit breaker is non-negotiable and ships with direct/tee** — *Rationale:* the
  autonomous-chatter guard is the precondition for peer comms existing at all. *Carried
  into:* `_edge_exchanges`, `CIRCUIT_BREAKER_MAX_EXCHANGES`, `promote_edge`, AT#6, AT#7.
- **Breaker budget is measured in message exchanges (+ 2-node deadlock), not token counts**
  — *Rationale:* the broker has no token visibility mid-exchange (tokens roll up at
  end-of-turn per the verified SDK invariant); exchange count is the enforceable proxy.
  *Carried into:* `_edge_exchanges` counter; Assumption #2.
- **Scoped `send_to` is an in-process SDK MCP tool whose handler calls the broker** —
  *Rationale:* honors "every cross-teammate message routes through the broker" (no off-broker
  channel); the broker stays the single authorization + observability choke point. *Carried
  into:* `send_scoped`, the `sdk_teammate.py` tool registration; AT#9 verifies the broker
  contract in stub mode.
- **Topology + edge stats carry `crew_id` on the dashboard payload** — *Rationale:* the
  load-bearing multi-instance leader/follower rule (CLAUDE.md): any per-instance endpoint
  must carry `crew_id` and proxy leader→follower. *Carried into:* `topology_edge_stats[].crew_id`,
  `_proxy_edge_log`/`_proxy_edge_promote`, AT#13.
- **Edge observability is an ON-GRAPH OVERLAY on the mermaid topology SVG, not a side
  edge-list table** — *Rationale:* operator design decision (Jerome) — live edge state is
  legible only when painted on the topology the operator is already reading. Live state
  (per-mode coloring, an animation pulse on exchange-count increment, a distinct
  tripped/gated style, click-an-edge→its message log, a promote-to-gated control reachable
  from the selected edge) renders directly on the rendered mermaid `graph TD` SVG — the same
  mermaid@11.4.1 renderer the shape-gate proposal card already uses. Because mermaid
  regenerates the SVG on every render, this requires a **post-render SVG-walk decoration
  layer** (the same architectural pattern as the existing foreignObject legibility fix):
  after `mermaid.render`, walk the SVG's `.flowchart-link` / edge-path elements (how mermaid
  v11 names SVG edges — the dashboard already references these for edge-thickening), key each
  to its from/to slots, and apply coloring, pulse animation, tripped style, click
  hit-testing, and the promote affordance. *Carried into:* `dashboard-edge-observability`
  task description; the on-graph render is browser-side and verified manually / via a
  Playwright probe (see Out of Scope), while the data + endpoint contract that drives it is
  the green-suite gate (AT#11–#13).

## Edge Cases

- **No topology instantiated yet:** a teammate→teammate send has no active topology →
  gated fallback (lead-bound). A scoped `send_to` from a topology-less teammate → rejected
  (no declared out-edges).
- **Recipient slot not in the active topology / unknown teammate:** `send_scoped` raises
  `UnauthorizedEdgeError` (non-declared) or the existing `UnknownTeammateError` (no such id).
- **Multiple recorded topologies containing both endpoints:** the *latest* recorded
  topology containing both ids governs (Assumption #3).
- **Self-send (slot == its own slot):** shapes forbid self-loops at parse time; a runtime
  `send_scoped` to self → `UnauthorizedEdgeError`.
- **Duplicate message id on a tee edge:** the original is deduped by `_seen_ids`; the
  derived cc envelope uses a fresh id, so dedup of the original suppresses the cc too (no
  orphan cc).
- **Tombstoned / terminating recipient:** existing `TeammateAlreadyDeadError` path fires
  before routing; no edge-stat increment for an undelivered message.
- **Breaker already tripped (edge overridden to gated):** further sends route gated; the
  exchange counter stops incrementing for tee/direct (it's gated now); a second trip emits
  no duplicate control envelope (idempotent on `_edge_overrides`).
- **Deadlock false-positive:** a 2-node cycle where one side has actually replied →
  `_edge_pending` for that direction is cleared on delivery of the reply, so no trip.
- **`promote_edge` on an already-gated or unknown edge:** idempotent no-op for gated;
  unknown edge → recorded override is harmless (no edge ever matches it).

**If this feature affects displayed data, answer these:**
- **No topology / no edges:** the dashboard shows the existing crew view with no edge
  overlay (empty `topology_edge_stats`); the SVG-walk decoration layer finds no matching
  links and paints nothing — no edge animation, no edge-log affordance.
- **Edge with zero exchanges:** the on-graph link is painted in its declared mode color,
  count `0`, not pulsed (the pulse triggers only on count increment).
- **Tripped edge:** the on-graph link carries a distinct "tripped/gated" style; `exchanges`
  shows the count at trip time; clicking it still fetches and renders the message log.

## Validation Contracts at Handoff Boundaries

| Boundary | Preconditions | Failure Behavior | Postconditions | Rollback |
|---|---|---|---|---|
| teammate scoped tool → `broker.send_scoped` | sender is a topology member | `UnauthorizedEdgeError` (non-declared) surfaced to teammate as tool error | envelope authorized | none (nothing enqueued) |
| `broker.send_scoped` → `broker.send` | recipient resolved to id | existing dead/unknown errors propagate | envelope routed by edge mode | dedup suppresses retry |
| `broker.send` routing → inbox/lead | governing edge resolved | breaker may force gated | delivered + logged + (if gated/tee) lead-notified | dedup on id |
| leader `/edge-log` → follower | `crew_id` in `InstanceRegistry` | 404 unknown crew / 502 unreachable | follower-local message log returned | none (read-only) |

## Acceptance Tests

All ATs run in **stub mode** (`CLAUDE_CREW_TEAMMATE_MODE=stub`) against a real `Broker`
(constructed as `Broker()`, the established pattern in `tests/test_broker.py`) and, for
dashboard ATs, real in-process `UIServer` instances + `httpx` (the pattern in
`tests/test_e2e_multi_instance.py`). Where an AT needs a topology, construct a `Shape`,
record a `Topology` directly via `broker.record_topology(...)` (or drive
`instantiate_shape`) with an explicit `slot_to_teammate` map binding spawned StubTeammate
ids to slots.

1. **gated routing.** Given a recorded topology with edge `a→b` mode `gated` and StubTeammates
   bound to slots `a`,`b`, when `broker.send` delivers a teammate-`a`→teammate-`b` envelope,
   then `get_messages(LEAD)` returns a lead-bound wrapper carrying `gated_for=b` and the
   original payload, and teammate-`b`'s inbox receives nothing.
2. **tee routing.** Given edge `a→b` mode `tee`, when `a`→`b` is sent, then teammate-`b`'s
   inbox receives the original envelope AND `get_messages(LEAD)` returns a derived cc
   envelope (`cc_of` = original id), and both appear in the broker message log.
3. **direct routing.** Given edge `a→b` mode `direct`, when `a`→`b` is sent, then
   teammate-`b`'s inbox receives the original envelope and it appears in the broker message
   log, but `get_messages(LEAD)` does NOT return it.
4. **no-edge gated fallback.** Given a topology with no edge between `a` and `c` (both
   teammates), when `broker.send` delivers an `a`→`c` envelope, then it is routed gated
   (lead-bound wrapper in `get_messages(LEAD)`), reproducing today's lead-routed behavior;
   `c`'s inbox receives nothing.
5. **scoped authorization rejects (broker level).** Given a topology where `a`'s only
   out-edge is `a→b`, when `broker.send_scoped(sender=a, recipient=c, ...)` is called for a
   non-declared teammate `c`, then it raises `UnauthorizedEdgeError` and nothing is enqueued
   to `c`; `broker.authorize_send(a, LEAD)` returns without raising.
6. **breaker budget force-inserts the lead.** Given edge `a→b` mode `direct` and
   `CIRCUIT_BREAKER_MAX_EXCHANGES=N`, when N+1 direct `a`→`b` messages are sent, then the
   (N+1)-th routes to LEAD instead of `b`, a `{type:"circuit_breaker", edge:["a","b"],
   reason:"budget_exceeded"}` control envelope reaches `get_messages(LEAD)`, and the edge's
   effective mode is now `gated`.
7. **deadlock detection force-inserts the lead.** Given reciprocal direct edges `a→b` and
   `b→a`, when an `a`→`b` direct exchange is in flight unanswered AND a `b`→`a` direct
   exchange is in flight unanswered simultaneously, then the breaker trips, a
   `{type:"circuit_breaker", reason:"deadlock"}` control envelope reaches `get_messages(LEAD)`,
   and the involved edge is forced to `gated`.
8. **neighbor adjacency injected at spawn.** Given a shape with edges `planner→implementor`
   (gated) and `implementor→reviewer` (direct), when the crew is instantiated, then the
   `implementor` teammate's assembled system prompt (the `system_prompt_override` captured
   in its config snapshot) contains a neighbors section naming its out-edge `reviewer`
   (role, mode `direct`) and its in-edge `planner` (role, mode `gated`).
9. **scoped `send_to` authorizes a neighbor, rejects a non-neighbor.** Given a topology
   where `a`'s out-edges are `{a→b}`, when `broker.send_scoped(a, "b", payload)` is called
   it delivers to `b` (resolving slot `b`→its teammate id), and when
   `broker.send_scoped(a, "c", payload)` is called it raises `UnauthorizedEdgeError` and
   delivers nothing.
10. **direct ping-pong stays off the lead inbox (integration).** Given a shape with
    reciprocal direct edges `a→b` and `b→a` instantiated with two StubTeammates, when a
    sequence of teammate-initiated `send_scoped` exchanges below the breaker budget runs
    `a→b→a→b`, then none of those messages appear in `get_messages(LEAD)` but all appear in
    the broker message log (`broker.get_messages(b_id)` / `_log`).
11. **dashboard emits topology edge stats with crew_id.** Given a broker with a recorded
    topology and one tee exchange, when `/api/state` is fetched from its in-process
    `UIServer`, then the local instance payload includes `topology_edge_stats` with an entry
    `{from_slot,to_slot,mode,exchanges>=1,tripped:false,crew_id}` matching the broker's
    `crew_id`. (Green-suite data contract; the on-graph SVG render driven by this payload is
    verified separately — see Out of Scope.)
12. **click-edge log + promote-edge control.** Given a topology with a direct edge `a→b`
    carrying ≥1 exchange, when `GET /edge-log/{crew_id}/a/b` is fetched it returns
    `{ok:true, messages:[...]}` containing that edge's messages; and when
    `POST /edge-promote/{crew_id}/a/b` is posted, it returns `{ok:true, mode:"gated"}` and a
    subsequent `a`→`b` `send` routes gated (lead-bound). (Green-suite endpoint contract; the
    SVG click hit-testing / promote affordance that calls these endpoints is verified
    separately — see Out of Scope.)
13. **multi-instance edge aggregation + proxy.** Given a leader `UIServer` and a follower
    `UIServer` (registered in a shared `InstanceRegistry`, the
    `tests/test_e2e_multi_instance.py` setup) where the follower's broker holds a topology
    with edge `a→b`, when the leader's `/api/state` is fetched it includes the follower's
    `topology_edge_stats` (keyed by the follower's `crew_id`), and `GET
    /edge-log/{follower_crew_id}/a/b` against the **leader** proxies to the follower and
    returns that edge's message log (200, not 404).

## Test Command

Prerequisite: `uv sync` (installs `pytest`, `pytest-asyncio`, `httpx`, and the
`starlette`/`uvicorn` stack pulled in transitively by `mcp[cli]`). Every test import
(`claude_crew.broker`, `claude_crew.ui_server`, `claude_crew.teammate`, `httpx`,
`pytest`) resolves from the project manifest — **no browser binary or running service is
required**: the dashboard ATs (AT#11–#13) exercise the data + endpoint contract via
in-process `UIServer` + `httpx` (the `test_e2e_multi_instance.py` pattern), NOT the
Playwright `dashboard`-marked path. The on-graph SVG rendering is verified outside the
green suite (see Out of Scope). The suite runs in stub mode (`conftest.py` auto-sets
`CLAUDE_CREW_TEAMMATE_MODE=stub`).

```bash
uv run pytest
```

## Out of Scope

- Honoring `reverse_mode` as a distinct back-edge routing mode — reciprocal traffic is
  expressed by declaring both directed edges; `reverse_mode` stays recorded-only (M0 behavior).
- The adaptation algebra verbs (`add_node`/`swap`/`augment`/`set_gate`/`drop`) — that is M3.
- The blessed shape library + lead right-sizing classifier — that is M1.
- Re-authoring RepoReactor as a native heavy shape — that is M4.
- Autonomous / memory-driven edge adaptation or widening lead latitude — that is M5.
- Token-count-based edge budgets (exchange-count is the M2 proxy; see Assumption #2).
- Deadlock cycles longer than 2 nodes (only the A⇄B 2-cycle is detected in M2).
- A live-SDK end-to-end test of the in-process `send_to` MCP tool firing inside a real
  teammate turn (the broker contract is covered in stub mode; live verification follows the
  existing `CLAUDE_CREW_LIVE_TESTS` gating and is not part of the default green suite).
- **A green-suite test of the rendered topology SVG itself** — the on-graph overlay
  rendering (per-mode edge coloring, the animation pulse, the tripped style, and click
  hit-testing on `.flowchart-link` / edge-path elements) is browser-side and is verified
  **manually / via a Playwright probe**, NOT in the default `uv run pytest` green suite. No
  headless-browser dependency is added to the green suite or the Test Command. The
  green-suite gate for `dashboard-edge-observability` is the **data + endpoint contract that
  drives the render** (`topology_edge_stats` on `/api/state` with `crew_id`; `GET /edge-log`;
  `POST /edge-promote`; the multi-instance proxy) — AT#11–#13.

## Assumptions

- **Forward-edge governance** — *Default:* only forward `(from_slot→to_slot)` edges in
  `Topology.edges` govern routing and authorization; `reverse_mode` remains recorded-only.
  *Rationale:* honoring it would mutate M0's topology recording and break existing
  `test_shape_broker` assertions; declaring both directed edges is the legible, additive way
  to express reciprocal traffic.
- **Breaker budget unit = message exchanges** — *Default:* `CIRCUIT_BREAKER_MAX_EXCHANGES=8`
  per directed edge, plus 2-node deadlock detection. *Rationale:* the broker cannot see token
  counts mid-exchange (they roll up at end-of-turn per the verified SDK invariant); exchange
  count is the enforceable, testable proxy. The constant is a `send`/spawn-time configurable
  arg so it can be tuned without a contract change.
- **Latest matching topology governs** — *Default:* when more than one recorded topology
  contains both endpoints, the most recently recorded one wins. *Rationale:* the common case
  is a single instantiated shape per crew; "latest" is deterministic and matches operator
  intent after a re-instantiate.
- **Scoped `send_to` recipient accepts a slot name or teammate id** — *Default:* the handler
  resolves a slot name to a teammate id via the active topology's `slot_to_teammate`, falling
  back to treating the argument as a literal id. *Rationale:* neighbor adjacency is injected
  as slot/role text, so slots are the natural address the teammate knows.
- **Breaker trip forces the edge to `gated` (not `drop`)** — *Default:* a tripped edge keeps
  flowing but through the lead. *Rationale:* invariant — remove the lead from relay, never
  drop the message; gated is the conservative resting state the operator can re-confirm.
- **On-graph overlay reuses the existing mermaid renderer + SVG-walk pattern** — *Default:*
  the edge overlay is built on the dashboard's existing mermaid@11.4.1 render pipeline and the
  same post-render SVG-walk decoration pattern as the foreignObject legibility fix, keyed to
  the `.flowchart-link` / edge-path classes already used for edge-thickening. *Rationale:* no
  new viz dependency; the renderer and edge-decoration seam already exist, so the overlay is
  additive and the browser-side render is verified manually / via a Playwright probe rather
  than the green suite.

## Open Questions

- (none)

## Validation

The user-visible promise: **a crew wired with a `direct` edge can have its two teammates
talk to each other without the lead in the loop, the operator can watch it on the topology
graph (edges color by mode, pulse as messages flow, show when a breaker trips) and click an
edge to read its log, and a runaway loop is force-inserted back to the lead — all while
every message stays observable through the broker.** Because M2 changes widely-consumed
broker/teammate behavior, the validation gate runs the **full** suite (per the repo standard
— a `-k` subset can let a cross-cutting regression merge undetected):

```bash
uv run pytest
```

Pass criteria: exit 0 with the new edge-routing, circuit-breaker, scoped-send, and
multi-instance edge-dashboard ATs green AND no regression in the existing broker, shape,
ui_server, and multi-instance suites. The on-graph SVG render (coloring, pulse, click) is a
separate manual / Playwright-probe check against the data contract those ATs already pin —
it is not part of this green-suite command.

## Task Breakout

```yaml
tasks:
  - name: broker-edge-routing
    description: |
      In claude_crew/broker.py, add per-edge routing to broker.send keyed on the active
      Topology: gated (lead-bound wrapper {gated_for,...}), tee (recipient inbox + derived
      cc {cc_of,...} to LEAD), direct (recipient inbox, logged, not lead-bound), and
      gated-fallback for teammate→teammate sends with no governing forward edge. Add the
      UnauthorizedEdgeError class, authorize_send, send_scoped (slot/id resolution +
      authorization), promote_edge, and the _active_topology_for/_edge_mode helpers and
      _edge_overrides map. Lead-origin and lead-bound sends must short-circuit to today's
      behavior (unchanged). Author tests/test_edge_routing.py covering ATs 1–5.
    dependsOn: []
    acceptanceTests: [1, 2, 3, 4, 5]
    taskTouches:
      - "claude_crew/broker.py"
      - "tests/test_edge_routing.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_edge_routing.py
  - name: broker-circuit-breaker
    description: |
      In claude_crew/broker.py, add the per-edge circuit breaker on tee/direct edges:
      _edge_exchanges counters with CIRCUIT_BREAKER_MAX_EXCHANGES budget, _edge_pending
      2-node deadlock detection, force-insert-the-lead behavior (override edge to gated +
      emit a {type:"circuit_breaker", edge, reason} control envelope to LEAD), and the
      EdgeStat dataclass surfaced on BrokerSnapshot.topology_edge_stats. Consumes the
      routing resolver and _edge_overrides from broker-edge-routing. Author
      tests/test_circuit_breaker.py covering ATs 6–7.
    dependsOn: [broker-edge-routing]
    acceptanceTests: [6, 7]
    taskTouches:
      - "claude_crew/broker.py"
      - "tests/test_circuit_breaker.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_circuit_breaker.py
  - name: scoped-send-teammate
    description: |
      Wire the teammate-facing scoped send + neighbor injection. Add a neighbors= param to
      build_teammate_prompt (claude_crew/teammate_prompt.py) emitting a SENTINEL_NEIGHBORS
      section; thread neighbors through broker.spawn_teammate (claude_crew/broker.py) and
      SdkTeammate (claude_crew/sdk_teammate.py, including an in-process SDK MCP send_to tool
      whose handler calls broker.send_scoped); have server.instantiate_shape
      (claude_crew/server.py) compute per-node out/in adjacency from shape.edges and pass it
      at spawn. Author tests/test_scoped_send.py covering AT 8 (prompt injection via the
      captured system_prompt_override), AT 9 (broker.send_scoped authorize/reject), and AT 10
      (direct ping-pong stays off the lead inbox). Depends on broker-circuit-breaker to
      serialize broker.py edits.
    dependsOn: [broker-circuit-breaker]
    acceptanceTests: [8, 9, 10]
    taskTouches:
      - "claude_crew/broker.py"
      - "claude_crew/sdk_teammate.py"
      - "claude_crew/teammate_prompt.py"
      - "claude_crew/server.py"
      - "tests/test_scoped_send.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_scoped_send.py
  - name: dashboard-edge-observability
    description: |
      Deliver edge observability as an ON-GRAPH OVERLAY, not a side edge-list table. In
      claude_crew/ui_server.py: emit topology_edge_stats (carrying crew_id) on the /api/state
      instance payload; add GET /edge-log/{crew_id}/{from}/{to} and POST
      /edge-promote/{crew_id}/{from}/{to} as per-instance endpoints with _PATH_PARAM_RE
      guards and leader→follower proxies (_proxy_edge_log/_proxy_edge_promote mirroring
      _proxy_shape_approval). In claude_crew/ui/dashboard.html: paint live edge state
      directly on the rendered mermaid `graph TD` topology SVG (mermaid@11.4.1, the same
      renderer the shape-gate proposal card uses) via a POST-RENDER SVG-WALK DECORATION LAYER
      — the same pattern as the existing foreignObject legibility fix. After mermaid.render,
      walk the SVG's .flowchart-link / edge-path elements (mermaid v11's edge class names,
      already used for edge-thickening), key each to its (from,to) slots, and apply: per-mode
      coloring (gated/tee/direct), an animation pulse when the exchange count increments, a
      distinct tripped/gated style, click hit-testing that opens the GET /edge-log result,
      and a promote-to-gated control (POST /edge-promote) reachable from the selected edge.
      Author tests/test_edge_dashboard.py covering AT 11 (single-instance edge stats), AT 12
      (edge-log + promote JSON), and AT 13 (multi-instance aggregation + proxy, mirroring
      test_e2e_multi_instance.py) — the GREEN-SUITE data + endpoint contract, tested via
      in-process UIServer + httpx. The on-graph SVG rendering itself (coloring, pulse, click
      hit-testing) is verified MANUALLY / via a Playwright probe and is NOT in the default
      green suite — do not add Playwright to the green suite or the spec Test Command.
      Depends on broker-circuit-breaker for the EdgeStat snapshot surface and promote_edge;
      touches only ui_server.py + dashboard.html so it runs parallel to scoped-send-teammate.
    dependsOn: [broker-circuit-breaker]
    acceptanceTests: [11, 12, 13]
    taskTouches:
      - "claude_crew/ui_server.py"
      - "claude_crew/ui/dashboard.html"
      - "tests/test_edge_dashboard.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_edge_dashboard.py
```

## Design Notes

- **Non-breaking invariant for the implementor:** the routing resolver must be a pure
  *addition* inside `broker.send` gated on `sender != LEAD_ID and recipient != LEAD_ID and a
  governing topology exists`. Existing tests send with `sender=LEAD_ID` or
  `recipient=LEAD_ID` and must be untouched. Run the full `uv run pytest` after the broker
  changes — broker behavior is widely consumed (the `multi-scope-agent-memory` regression in
  CLAUDE.md is the cautionary tale of a `-k` subset hiding a cross-suite break).
- **Breaker idempotence:** a second trip on an already-overridden edge must not emit a
  duplicate control envelope — guard on `_edge_overrides` membership.
- **cc/gated wrapper ids:** derived lead-bound envelopes need fresh ids
  (`new_message_id()`) so they are not suppressed by the original's `_seen_ids` entry — but
  a deduped *original* (duplicate id) must produce no wrapper at all (return before
  routing).
- **Multi-instance trap (CLAUDE.md):** `topology_edge_stats[].crew_id` and the
  `/edge-log` + `/edge-promote` handlers must resolve locally when `crew_id ==
  _own_crew_id()` and proxy to the follower otherwise; AT#13 is the test that would catch a
  same-origin-relative-URL regression that single-instance tests miss.
- **On-graph overlay = post-render SVG-walk:** mermaid regenerates the topology SVG on every
  render, so edge decoration (coloring, pulse, tripped style), click hit-testing, and the
  promote affordance must be (re-)applied by walking the rendered SVG's `.flowchart-link` /
  edge-path elements after each `mermaid.render`, keyed to from/to slots — never by editing
  mermaid's source or assuming a stable DOM across renders. This mirrors the existing
  foreignObject legibility decoration already in the dashboard.
