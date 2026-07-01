# Spec: m3-5-reshape-live-crew

## Problem

M3 made shape adaptation *computable* — five pure verb commands
(`AddNode`/`Swap`/`Augment`/`SetGate`/`Drop` in `claude_crew/shapes.py`), each
`Shape → (Shape, AdaptationDiff)`, exposed via `adapt_shape`. But `adapt_shape`
is **pre-instantiation only**: it produces a *new proposed shape* that goes
through the human gate and then `instantiate_shape` spawns a **fresh** crew.
There is no way for an operator to reshape a crew that is *already running* —
to add a reviewer to a live crew, drop a stalled node, swap a role, or rewire an
edge mode mid-flight — without tearing the whole crew down and losing every
teammate's accumulated turn context. M3.5 closes that gap: apply the same five
verbs to an **instantiated** crew, mutating the live topology in place, behind
the existing M1.5 human gate. Additions never respawn (context preserved);
removals/replacements kill by nature; edge-mode changes are instant data writes.
claude-crew owns the *mechanism*; the policy layer (repo-react) owns *which*
reshape to apply.

## Architecture Overview

The change plugs into four existing seams and adds one new MCP tool. Nothing in
the routing engine, the gate state machine, or the kill machinery is
rewritten — the whole design rests on the verified fact that **routing reads
live broker state at delivery time** (`_send_routed → _resolve_routing_mode →
_active_topology_for` scans `reversed(broker._topologies)`, and `_edge_mode`
consults `broker._edge_overrides` first). So a live reshape is a *data update*,
not a crew rebuild.

- **`claude_crew/sdk_teammate.py`** (D0) — remove the spawn-time `_has_out_edges`
  conditional in `_run` (lines ~1542–1560) so the in-process `send_to` MCP server
  and its allowed-tools entry are wired for **every** `SdkTeammate`. This is
  foundational: a teammate spawned before any edge to it existed must already
  hold `send_to` so a *later* live edge-addition needs no respawn (a running
  subprocess cannot have tools injected — the SDK bakes `--allowedTools` at
  launch). Safety is unchanged: `authorize_send` (broker.py:1560), not the tool's
  presence, is the security boundary.
- **`claude_crew/broker.py`** (D2) — three tiny additive helpers the tool needs;
  reuse `record_topology`, `send`, `kill_teammate`, `_tombstone_teammate`,
  `_edge_overrides`, `get_topologies` verbatim.
- **`claude_crew/server.py`** (D1) — the new `reshape_crew` MCP tool: verb
  discriminator (reuses the `shapes.py` verb command objects), resolves the base
  **instantiated** proposal's `Shape`, applies the verb to produce
  `(new_shape, diff)`, registers the diff through the **M1.5 gate verbatim**
  (`register_proposal` → `await_proposal`), and on approval applies the live
  mutation per the verb table, records the reshaped `Topology`, marks the new
  proposal instantiated (lineage), and returns a structured result.
- **The M1.5 gate** (`register_proposal`/`await_proposal`/`resolve_proposal`) and
  the `adaptation_diff:str` channel are reused **without signature change**.

### Call-site survey

The live verb application and the informing-message send are the only new
helper with multiple internal branches; the branches differ in shape per verb,
so they are named here to prevent a "one uniform handler" framing that would
fragment silently at code-write time.

| Call-site | Path | Shape | Notes |
|-----------|------|-------|-------|
| SetGate application | `server.py` reshape_crew | `broker.set_edge_override(f,t,mode)` | No topology append, no spawn, no message |
| AddNode / Augment application | `server.py` reshape_crew | `spawn_teammate(...)` + `record_topology(...)` + `broker.send(inform)` | New node spawned with correct neighbors; existing source informed; no respawn |
| Drop application | `server.py` reshape_crew | `record_topology(minus)` + `kill_teammate(graceful)` + `remove_edge_overrides(...)` + `broker.send(inform)` | Kills dropped node; cleans stale overrides |
| Swap application | `server.py` reshape_crew | `spawn_teammate(...)` + `record_topology(...)` **then** `kill_teammate(old)` | Spawn+record BEFORE kill (no dead-slot window) |

Resolution: **one dispatch function** (`_apply_live_reshape(verb, diff, new_shape, base_topology)`)
with a per-verb branch — not five public helpers. It is an internal function of
the `reshape_crew` tool closure, mirroring how `adapt_shape` constructs verb
commands inline.

## Data / API Contracts

```python
# NEW MCP tool — claude_crew/server.py
@mcp.tool()
async def reshape_crew(
    verb: str,                      # add_node | swap | augment | set_gate | drop
    params: dict,                   # same params shape adapt_shape accepts per verb
    base_shape_id: str,             # MUST resolve to an INSTANTIATED proposal (a running crew)
    gate_timeout: float = 600.0,    # await_proposal timeout; mirrors propose_shape(wait=True)
) -> dict[str, Any]: ...

# Success return:
# {
#   "ok": True,
#   "shape_id": <new proposal id, now status "instantiated">,
#   "verb": <verb>,
#   "diff": <AdaptationDiff.render() string>,
#   "status": "instantiated",
#   "actions": {
#     "spawned":  [{"slot": s, "teammate_id": t, "role": r}, ...],
#     "killed":   [<teammate_id>, ...],
#     "informed": [<teammate_id>, ...],
#     "edge_overrides_set":     [[from_slot, to_slot, mode], ...],
#     "edge_overrides_removed": [[from_slot, to_slot], ...],
#   },
#   "topology": {"shape_name": ..., "edges": [[f,t,mode],...], "slot_to_teammate": {...}},
# }
#
# Failure envelopes (NO proposal registered / NO live mutation on any failure path):
#   {"ok": False, "stage": "base",  "error": ...}   # unknown base_shape_id, OR
#                                                    #   base status != "instantiated"
#   {"ok": False, "stage": "verb",  "error": ...}   # verb not one of the five
#   {"ok": False, "stage": "adapt", "error": ..., ["unresolved_roles": [...]]}
#                                                    # role unresolvable (swap/augment) OR
#                                                    #   verb.apply raised ShapeValidationError/KeyError/TypeError
#   {"ok": False, "stage": "gate", "status": "declined"|"timed_out", "shape_id": ...}
#                                                    # human declined / gate timed out → crew UNTOUCHED

# Informing-message payload (broker.send, sender=LEAD_ID, recipient=running teammate_id):
# {
#   "type": "crew_reshape",
#   "event": "neighbor_added" | "neighbor_removed",
#   "neighbor_slot": <slot>,
#   "neighbor_role": <role>,
#   "reachable_via": "send_to",   # for neighbor_added
# }

# NEW broker helpers — claude_crew/broker.py (all additive, minimal):
def latest_topology(self) -> "Topology | None":
    """Return self._topologies[-1] if any else None (the crew's current topology)."""

def set_edge_override(self, from_slot: str, to_slot: str, mode: str) -> None:
    """Write self._edge_overrides[(from_slot, to_slot)] = mode. Generalizes
    promote_edge (which forces 'gated') to any mode ∈ {gated, tee, direct}."""

def remove_edge_overrides(self, pairs: "Iterable[tuple[str, str]]") -> None:
    """Delete each (from_slot, to_slot) key from self._edge_overrides.
    Idempotent — absent keys are silently skipped."""
```

## Design Decisions

- **[D0 — `send_to` wired unconditionally at spawn]** — *Rationale:* a running
  subprocess cannot have tools injected (the SDK bakes `--allowedTools` at
  launch), so live edge-addition can only be respawn-free if every teammate
  already holds `send_to`; safety is unchanged because `authorize_send` — not the
  tool's presence — is the moat. — *Carried into:* removal of the `_has_out_edges`
  block in `sdk_teammate.py:_run`; AT 1 (teammate with no out-edges still has
  `send_to` wired), AT 2 (grep-guard: NAMED LITERAL `_has_out_edges` absent from
  `sdk_teammate.py`), AT 3 (out-edge teammate non-regression).
- **[D1 — distinct `reshape_crew` MCP tool; do NOT overload `adapt_shape`]** —
  *Rationale:* live process side-effects (kill/respawn/authorize) have different
  semantics than a pure-data proposal; overloading invites "did this kill a
  process?" confusion. Verb algebra is shared; only the *application* differs. —
  *Carried into:* new `reshape_crew` tool in `server.py`; AT 4 (deletion-detector:
  NAMED LITERAL `reshape_crew` registered as MCP tool); `adapt_shape` untouched.
- **[D2 — broker changes minimal and additive]** — *Rationale:* the routing
  engine already reads live state; reshape only needs to *write* topology/overrides
  and reuse existing send/kill. — *Carried into:* `latest_topology`,
  `set_edge_override`, `remove_edge_overrides`; AT 19. `record_topology`, `send`,
  `kill_teammate`, `_tombstone_teammate` reused verbatim.
- **[D3 — live reshape is human-gated; reuse the M1.5 gate verbatim]** —
  *Rationale:* killing/replacing a running teammate is exactly the high-consequence
  act the gate exists for; coordinator-in-the-loop is the moat. — *Carried into:*
  `reshape_crew` calls `register_proposal(new_shape, adaptation_diff=diff.render())`
  then `await_proposal(shape_id, gate_timeout)`; AT 10 (decline → no mutation),
  AT 11 (approve → mutation + lineage).
- **[D4 — additions never respawn: authorize + inform]** — *Rationale:* preserves
  accumulated turn context (operator's explicit priority); enabled by D0 + free-form
  `send_to` recipient + runtime message superseding the stale spawn-time neighbor
  prompt. — *Carried into:* AddNode/Augment application appends a `Topology` and
  sends a `crew_reshape/neighbor_added` message to the running source; AT 6, AT 7,
  AT 17 (live: same-id implementor reaches new reviewer).
- **[D5 — Swap/Drop kill by nature; Swap ordering is spawn+record THEN kill]** —
  *Rationale:* removed/replaced membership has no context worth preserving;
  spawning the replacement and recording the new topology *before* tombstoning the
  old teammate closes the kill→record race so slot-name sends resolve to the live
  replacement throughout — no dead-slot window. — *Carried into:* AT 8 (Drop),
  AT 9 (Swap ordering assertion), AT 18 (live swap).
- **[D6 — stale `_edge_overrides` cleanup on edge removal]** — *Rationale:*
  overrides are keyed by slot strings and survive topology replacement; a stale
  override would shadow the new topology's declared mode. — *Carried into:*
  Drop/Swap application calls `remove_edge_overrides` for every removed
  `(from_slot,to_slot)`; AT 8.
- **[D7 — reshape blocks on the gate; result carries the applied-actions record]**
  — *Rationale:* the reshape must observe the human decision before touching any
  process (mirrors `propose_shape(wait=True)` / instantiation flow); a structured
  `actions` record makes the mutation auditable and testable. — *Carried into:*
  `reshape_crew` return contract; AT 11.

## Edge Cases

- **Base is not a running crew** — `base_shape_id` resolves to a `pending`,
  `approved`, `declined`, or `timed_out` proposal (never instantiated): reject with
  `stage:"base"`; no mutation. (AT 13)
- **Unknown `base_shape_id`** — no such proposal: reject `stage:"base"`. (AT 12)
- **Unknown verb** — not one of the five: reject `stage:"verb"`. (AT 14)
- **Unresolvable role on swap/augment** — role absent from `factory.known_roles`:
  reject `stage:"adapt"` with `unresolved_roles`; no spawn. (AT 15)
- **Illegal mutation** — `verb.apply` raises `ShapeValidationError` (e.g. AddNode
  duplicate slot, Drop of a node with live edges) or `KeyError`/`TypeError` on
  malformed `params`: reject `stage:"adapt"`; no mutation. (AT 16)
- **Human declines / gate times out** — `await_proposal` returns `declined` or
  `timed_out`: return `stage:"gate"` with the status; **zero** processes touched,
  no topology appended, no override written. (AT 10)
- **AddNode/Augment whose new out-edge source is the NEW node itself** — the new
  node's out-edges are baked at *its own* launch (correct neighbors passed to
  `spawn_teammate`); only edges whose source is an **already-running** node trigger
  an informing message. No message for the freshly-spawned node. (AT 6, AT 7)
- **Drop of a node with in/out edges** — `Drop.apply` rejects nodes with live
  edges at the pure-data layer (`stage:"adapt"`); a droppable node has already had
  its edges removed by prior verbs, so live Drop only ever removes an edgeless
  node plus any dangling override keys. (AT 8, AT 16)
- **Swap does not remove edges** — Swap preserves incident edges and the slot
  string, so `_edge_overrides` keyed by those slots stay valid; no override cleanup
  for a standard swap. (AT 9)
- **Concurrency (topology append during routing)** — single asyncio loop + GIL make
  `reversed(_topologies)` during `list.append` safe; a message that resolved routing
  just before the append uses the old topology, just after uses the new — both
  deterministic. No lock added (documented for a future multi-threaded broker).
- **SetGate reuses the circuit-breaker override channel** — writing
  `_edge_overrides[(f,t)] = mode` is the same channel the breaker uses; a later
  breaker trip on the same edge will overwrite it with `"gated"` (expected —
  the runaway guard wins). Documented, not defended against. (AT 5)
- **Informing message to a teammate mid-graceful-flush** — `broker.send` bounces
  sends to a `_terminating`/tombstoned recipient with `TeammateAlreadyDeadError`;
  reshape swallows it per-recipient (best-effort inform) and records only
  successfully-informed ids in `actions.informed`. (AT 8)

## Specification

`reshape_crew(verb, params, base_shape_id, gate_timeout=600.0)` executes in order:

1. **Base resolution.** `proposal = broker.get_proposal(base_shape_id)`. If `None`
   → `stage:"base"`. If `proposal.status != "instantiated"` → `stage:"base"`.
   Else `base_shape = proposal.shape`.
2. **Verb guard.** `verb ∉ {add_node, swap, augment, set_gate, drop}` →
   `stage:"verb"`.
3. **Role resolution** (swap/augment only) — reuse the exact `adapt_shape` seam:
   `getattr(factory, "known_roles", None)` + `factory.resolve_role` (fallback to
   `*:role` unique-suffix promotion). Unresolvable → `stage:"adapt"` +
   `unresolved_roles`. Skipped when `known_roles` is absent (stub mode).
4. **Apply verb (pure).** Construct the verb command from `params` exactly as
   `adapt_shape` does (`_node_from_dict`, `ShapeEdge(**e)`, etc.), then
   `new_shape, diff = command.apply(base_shape)`. `ShapeValidationError` /
   `KeyError` / `TypeError` → `stage:"adapt"`.
5. **Gate (M1.5 verbatim).** `sid = broker.register_proposal(new_shape,
   adaptation_diff=diff.render())`; `resolved = await broker.await_proposal(sid,
   gate_timeout)`. If `resolved.status != "approved"` → `{ok:False, stage:"gate",
   status:resolved.status, shape_id:sid}`; **no live mutation**.
6. **Apply live mutation** via `_apply_live_reshape(verb, diff, new_shape,
   base_topology=broker.latest_topology())`:
   - **set_gate:** `broker.set_edge_override(from, to, mode)` for the changed edge.
     No topology append, no spawn, no message.
   - **add_node / augment:** spawn the new node via `broker.spawn_teammate(role,
     name=slot, factory, model, extra_tools, extra_skills, cwd, neighbors=<derived
     from new_shape.edges as instantiate_shape does>)`; build the reshaped
     `Topology` = base `slot_to_teammate` ∪ `{new_slot: new_id}`, `edges =
     new_shape.edges`; `broker.record_topology(topo)`; for each new edge whose
     **source slot maps to an already-running teammate**, `broker.send` a
     `neighbor_added` inform to that teammate.
   - **drop:** build reshaped `Topology` = base map minus dropped slot,
     `edges = new_shape.edges`; `broker.record_topology(topo)`;
     `broker.kill_teammate(dropped_id, graceful=True)`;
     `broker.remove_edge_overrides([...removed (from,to) pairs...])`; `broker.send`
     a `neighbor_removed` inform to each affected surviving neighbor.
   - **swap:** spawn replacement (`name=slot`, new id, neighbors from
     `new_shape.edges`); build reshaped `Topology` = base map with `[slot]=new_id`,
     `edges = new_shape.edges`; `broker.record_topology(topo)` — **THEN**
     `broker.kill_teammate(old_id, graceful=True)`.
7. **Lineage + return.** `broker.mark_instantiated(sid)`; return the success
   envelope with the `actions` record and the reshaped `topology`.

## Validation Contracts at Handoff Boundaries

| Boundary | Preconditions | Failure Behavior | Postconditions | Rollback |
|---|---|---|---|---|
| tool → gate | base is instantiated; verb valid; roles resolve; `verb.apply` succeeds | staged `ok:False` before `register_proposal` | a pending proposal carrying `diff` exists | none needed (no proposal registered on pre-gate failure) |
| gate → live mutation | `await_proposal` returned `approved` | `stage:"gate"` on declined/timed_out; crew untouched | exactly the verb's live effect applied; new topology recorded; proposal `instantiated` | additions leave a spawned teammate only after `record_topology`; swap kills old only after replacement recorded |

## Acceptance Tests

1. Given a `SdkTeammate` spawned with **no out-edges** (`neighbors=None` or a
   neighbor list with only `direction:"in"` entries), when `_run` builds its SDK
   options, then the in-process `send_to` MCP server is wired
   (`self._send_to_tool` is set / `_SEND_TO_MCP_SERVER_NAME` present in
   `mcp_servers`) and `_SEND_TO_TOOL_ID` is in `allowed_tools`. (D0 mechanism,
   happy path)
2. Given the built codebase, when a stub test greps `claude_crew/sdk_teammate.py`,
   then the NAMED LITERAL substring `_has_out_edges` does **not** appear anywhere
   in the file (deletion-detector: re-introducing the spawn-time gate re-adds the
   literal and fails this test). (D0 structural guard)
3. Given a `SdkTeammate` spawned **with** an out-edge (`neighbors` containing a
   `direction:"out"` entry), when a topology authorizing that edge is recorded and
   the teammate calls `send_to(<neighbor slot>, ...)`, then the message is
   delivered (non-regression: the previously-conditional wiring still works when an
   out-edge exists). (D0 integration)
4. Given a constructed server, when the registered MCP tool names are enumerated,
   then the NAMED LITERAL `reshape_crew` is present (deletion-detector: removing the
   tool fails this test). (D1 structural guard)
5. Given a running crew (stub teammates) with an instantiated proposal and a
   `direct` edge `a→b`, when `reshape_crew("set_gate", {from_slot:"a",
   to_slot:"b", mode:"gated"}, base_shape_id)` is approved, then
   `broker._edge_overrides[("a","b")] == "gated"`, no teammate is spawned or
   killed, no informing message is sent, and no new `Topology` is appended.
   (SetGate live, happy path)
6. Given a running crew (stub) with instantiated slot `impl` and an existing
   proposal, when `reshape_crew("add_node", {node:{slot:"reviewer",role:...},
   edges:[{from_slot:"impl",to_slot:"reviewer"}]}, base_shape_id)` is approved,
   then (a) a new teammate is spawned for `reviewer`, (b) a new `Topology` is
   appended whose `slot_to_teammate` contains **both** `impl`'s original teammate_id
   and the new `reviewer` id, (c) `impl`'s teammate_id is **unchanged** (not
   respawned), and (d) `impl` receives a `{type:"crew_reshape",
   event:"neighbor_added"}` message. (AddNode live)
7. Given a running crew (stub) with instantiated slot `impl`, when
   `reshape_crew("augment", {node:{slot:"reviewer",role:...},
   edges:[{from_slot:"impl",to_slot:"reviewer"}]}, base_shape_id)` is approved,
   then the new edge is authorized via an appended `Topology`, the running `impl`
   is informed (same teammate_id, not respawned), and the new node is spawned.
   (Augment live)
8. Given a running crew (stub) with instantiated slots `a` and `b`, an edge whose
   removal is legal, and an override `_edge_overrides[("a","b")]` present, when
   `reshape_crew("drop", {slot:"b"}, base_shape_id)` is approved (after the edge is
   already removed), then (a) a new `Topology` minus `b` is appended, (b) `b`'s
   teammate is graceful-killed (dead in the registry), (c) the stale
   `_edge_overrides[("a","b")]` entry is removed, and (d) surviving affected
   neighbors receive a `neighbor_removed` message. (Drop live + D6 cleanup)
9. Given a running crew (stub) with instantiated slot `worker`, when
   `reshape_crew("swap", {slot:"worker", role:<new resolvable role>},
   base_shape_id)` is approved, then the replacement is spawned and the new
   `Topology` (slot→new id) is `record_topology`'d **before** the old teammate's
   `kill_teammate` is called (ordering asserted via call-order capture — no
   window where `worker` maps to a dead id), the old teammate is dead, and the new
   one occupies the slot. (Swap live + D5 ordering)
10. Given a valid reshape whose proposal the human **declines**, when
    `await_proposal` returns `declined`, then `reshape_crew` returns `{ok:False,
    stage:"gate", status:"declined"}` and **no** teammate was spawned or killed, no
    `Topology` was appended, and no `_edge_overrides` entry was written. (Gate
    decline, sad path)
11. Given a valid reshape whose proposal the human **approves**, when it completes,
    then the new proposal's status is `instantiated`, its topology is the latest
    recorded, and the returned `shape_id` can be passed as `base_shape_id` to a
    second `reshape_crew` call (lineage — the reshaped shape is the new base).
    (Gate approve + lineage)
12. Given `base_shape_id` that matches no proposal, when `reshape_crew` is called,
    then it returns `{ok:False, stage:"base"}` and no proposal is registered.
    (Negative: unknown base id)
13. Given `base_shape_id` that resolves to a proposal whose status is **not**
    `instantiated` (test each of `pending`, `approved`, `declined`, `timed_out`),
    when `reshape_crew` is called, then it returns `{ok:False, stage:"base"}` and
    no mutation occurs. (Negative: base not a running crew)
14. Given `verb` not in `{add_node, swap, augment, set_gate, drop}`, when
    `reshape_crew` is called against a valid instantiated base, then it returns
    `{ok:False, stage:"verb"}`. (Negative: unknown verb)
15. Given `verb="swap"` (and separately `verb="augment"`) with a `role` absent
    from `factory.known_roles`, when `reshape_crew` is called against a valid
    instantiated base with a factory exposing `known_roles`, then it returns
    `{ok:False, stage:"adapt", unresolved_roles:[...]}` and no teammate is spawned.
    (Negative: unresolvable role)
16. Given `verb="add_node"` whose `apply` raises `ShapeValidationError` (e.g. the
    node's slot duplicates an existing slot), when `reshape_crew` is called against
    a valid instantiated base, then it returns `{ok:False, stage:"adapt"}` and no
    mutation occurs. (Negative: illegal mutation)
17. **LIVE** (`CLAUDE_CREW_LIVE_TESTS=1`): given a real instantiated crew where an
    implementor node has `send_to` wired, when `reshape_crew("add_node", ...)` adds
    a `reviewer` + edge `impl→reviewer` and is approved, then the **same still-alive
    implementor** (identical teammate_id, NOT respawned) receives the informing
    message, calls `send_to("reviewer", ...)` in a **real teammate turn**, and the
    reviewer receives the message. (D3 headline regression guard)
18. **LIVE** (`CLAUDE_CREW_LIVE_TESTS=1`): given a real instantiated crew, when
    `reshape_crew("swap", ...)` swaps a slot's role and is approved, then the old
    teammate is dead, the new teammate occupies the slot, and a slot-name
    `send_to` resolves to the **new** teammate (real turn). (D3 swap regression
    guard)
19. Given a `Broker`, when the additive helpers are exercised, then:
    `latest_topology()` returns `None` on an empty broker and the most-recently
    recorded `Topology` after `record_topology`; `set_edge_override("a","b","tee")`
    writes `_edge_overrides[("a","b")] == "tee"`; `remove_edge_overrides([("a","b"),
    ("x","y")])` deletes present keys and silently skips absent keys (idempotent).
    (D2 helper unit)
20. Given the docs, when `doc/ARCHITECTURE.md` and `CLAUDE.md` are read, then the
    `reshape_crew` tool and the D0 unconditional-`send_to` contract flip are
    documented, and a doc-staleness grep confirms no surviving prose asserts that
    `send_to` is wired **only** for teammates with out-edges (NAMED LITERAL guard:
    the phrase `_has_out_edges` does not appear in `doc/ARCHITECTURE.md`).
    (Documentation)

## Test Command

The stub suite requires only the project's declared dependencies (`pytest`,
`pytest-asyncio`, `mcp`, `claude-agent-sdk` — all in `pyproject.toml`) and needs
no network, no auth, and no manual setup (`conftest.py` forces
`CLAUDE_CREW_TEAMMATE_MODE=stub` and disables the transcript sink). Run from the
worktree root:

```bash
uv run pytest
```

The two **LIVE** acceptance tests (AT 17, AT 18) are gated and skipped by the
stub suite. They spawn real `claude` subprocesses against the cloud Anthropic
API — they cost tokens and take minutes, and require a logged-in SDK
(`~/.claude/.credentials.json`). Per project policy (CLAUDE.md "Run live tests
before merging") they are the regression guard for this feature and MUST pass
before merge:

```bash
CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_reshape.py
```

## Out of Scope

- Live *tool-set* mutation of a running subprocess beyond what D0 pre-wires — the
  SDK bakes `--allowedTools` at launch; this is exactly why D0 pre-wires `send_to`
  rather than injecting it live.
- Autonomous (ungated) reshape — that is M5 (memory-informed adaptation). M3.5
  stays human-gated.
- RepoReactor-as-shape (M4).
- Dashboard/UI changes beyond the existing `adaptation_diff` gate channel (a
  reshape diff already flows to Mission Control via the reused proposal). No new
  UI, no new endpoint.
- A non-blocking two-call reshape variant (propose then apply separately) —
  `reshape_crew` blocks on `await_proposal` in one call.
- Multi-crew disambiguation — a single broker per server has one active topology
  lineage; `base_shape_id` identifies the crew.

## Assumptions

- **[reshape_crew blocks on the gate with a 600s default timeout]** — *Default:*
  `gate_timeout=600.0`, matching `propose_shape(wait=True)`'s M0 blocking path. —
  *Rationale:* the mutation must observe the human decision before touching any
  process; 600s is the established gate timeout.
- **[Base is identified by the instantiated proposal's `shape_id`]** — *Default:*
  `base_shape_id` must be the `shape_id` returned by `instantiate_shape`/prior
  `reshape_crew`, whose proposal status is `instantiated`. — *Rationale:*
  `instantiate_shape` marks the proposal `instantiated` and the proposal retains
  the source `Shape` — the only place the live crew's node roles/models are
  recoverable (a `Topology` records edges + slot→id but not node roles).
- **[The reshaped shape becomes the new instantiated base (lineage)]** —
  *Default:* on approval, `reshape_crew` calls `mark_instantiated(new_shape_id)`
  after `record_topology`, so chained reshapes each advance the lineage. —
  *Rationale:* mirrors `instantiate_shape`'s single-use "shape is the gate"
  invariant and lets AT 11 chain reshapes.
- **[Informing-message payload shape]** — *Default:* `{type:"crew_reshape",
  event:"neighbor_added"|"neighbor_removed", neighbor_slot, neighbor_role,
  reachable_via:"send_to"}` sent via `broker.send` with `sender=LEAD_ID`. —
  *Rationale:* enqueues into the running teammate's inbox as a normal turn (the
  same path the lead's `send_to` tool uses); the model reads it and can then call
  `send_to`. Field names are additive and not consumed by any existing code.
- **[SetGate writes `_edge_overrides` rather than appending a topology]** —
  *Default:* per the locked verb table, SetGate = `set_edge_override(from,to,mode)`.
  — *Rationale:* `_edge_mode` checks overrides first, so the change is instant and
  respawn-free; no membership change means no topology append is warranted.
- **[Neighbors for spawned nodes derived from `new_shape.edges`]** — *Default:*
  reuse `instantiate_shape`'s per-slot neighbor construction (out/in dicts with
  `direction`/`slot`/`role`/`mode`) so a spawned node launches with correct
  neighbors and its own out-edges baked in. — *Rationale:* single source of truth
  with the authorization check; cannot drift.
- **[`remove_edge_overrides` and `set_edge_override` are the D2 surface; `send`
  is reused for informing]** — *Default:* no dedicated `inform_teammate` broker
  method; the tool calls `broker.send` directly. — *Rationale:* keeps broker
  changes minimal and additive per the D2 constraint.

## Open Questions

- (none)

## Validation

After feature-review PASS, the coordinator runs the full stub suite (must be
green) and then the two live regression guards (which only manifest live). The
live command hits the real cloud API and requires `CLAUDE_CREW_LIVE_TESTS=1` and
a logged-in SDK:

```bash
uv run pytest && CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_reshape.py
```

Pass criteria: (1) full stub suite exits 0; (2) `tests/test_live_reshape.py`
exits 0 with both live tests executed (not skipped) — proving the headline
no-respawn add (same-id implementor reaches a live-added reviewer) and the live
swap (slot-name `send_to` resolves to the replacement) both work against real
teammate turns.

## Task Breakout

```yaml
tasks:
  - name: d0-unconditional-send-to
    description: |
      Remove the spawn-time `_has_out_edges` conditional in
      `claude_crew/sdk_teammate.py:_run` (lines ~1542-1560) so the in-process
      `send_to` MCP server + `_SEND_TO_TOOL_ID` allowed-tools/catalog entry are
      wired for EVERY SdkTeammate regardless of out-edges. Flip the documented
      contract in-code and update any existing test that asserts a no-out-edge
      teammate lacks `send_to`. Foundational: reshape's respawn-free edge
      addition depends on it.
    dependsOn: []
    acceptanceTests: [1, 2, 3]
    taskTouches:
      - "claude_crew/sdk_teammate.py"
      - "tests/test_scoped_send.py"
      - "tests/test_sdk_teammate.py"
      - "tests/test_d0_send_to_unconditional.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_d0_send_to_unconditional.py tests/test_scoped_send.py
  - name: broker-live-reshape-helpers
    description: |
      Add three minimal additive broker helpers in `claude_crew/broker.py`:
      `latest_topology()` (most-recent Topology or None), `set_edge_override(from,
      to, mode)` (generalizes promote_edge to any mode), and
      `remove_edge_overrides(pairs)` (idempotent delete). No changes to existing
      methods.
    dependsOn: []
    acceptanceTests: [19]
    taskTouches:
      - "claude_crew/broker.py"
      - "tests/test_reshape_broker_helpers.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_reshape_broker_helpers.py
  - name: reshape-crew-scaffold
    description: |
      Author the `reshape_crew` MCP tool skeleton in `claude_crew/server.py`:
      registration, base resolution (must be an INSTANTIATED proposal), verb
      guard, swap/augment role resolution (reusing the adapt_shape seam), pure
      verb.apply to produce (new_shape, diff), and M1.5 gate integration
      (register_proposal + await_proposal). Implements the decline/timeout path
      (no mutation) and the approve path's lineage (mark_instantiated +
      record_topology of the reshaped topology). Leaves the per-verb live-effect
      dispatch as a stub for reshape-crew-verbs to fill (this task creates
      server.py's reshape_crew; the verbs task appends the `_apply_live_reshape`
      branch bodies). All validator/negative branches land here.
    dependsOn: [broker-live-reshape-helpers]
    acceptanceTests: [4, 10, 11, 12, 13, 14, 15, 16]
    taskTouches:
      - "claude_crew/server.py"
      - "tests/test_reshape_crew_gate.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_reshape_crew_gate.py
  - name: reshape-crew-verbs
    description: |
      Fill in the `_apply_live_reshape` per-verb live mutation dispatch inside
      `reshape_crew` in `claude_crew/server.py` (this task appends the branch
      bodies to the function reshape-crew-scaffold created): set_gate
      (set_edge_override), add_node/augment (spawn + record_topology + inform
      running source via broker.send), drop (record minus-topology + graceful
      kill + remove_edge_overrides + inform neighbors), swap (spawn + record
      THEN kill, ordering preserved). Populate the `actions` result record.
    dependsOn: [reshape-crew-scaffold, d0-unconditional-send-to]
    acceptanceTests: [5, 6, 7, 8, 9]
    taskTouches:
      - "claude_crew/server.py"
      - "tests/test_reshape_crew_verbs.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_reshape_crew_verbs.py
  - name: live-reshape-regression-tests
    description: |
      Author the two gated live regression guards in
      `tests/test_live_reshape.py` (skipif CLAUDE_CREW_LIVE_TESTS != 1, mirroring
      tests/test_live_shape_adaptation.py): the headline no-respawn add (same-id
      implementor reaches a live-added reviewer via a real send_to turn) and the
      live swap (slot-name send_to resolves to the replacement). Both assert real
      teammate turns, not stub-satisfiable structure.
    dependsOn: [reshape-crew-verbs, d0-unconditional-send-to]
    acceptanceTests: [17, 18]
    taskTouches:
      - "tests/test_live_reshape.py"
    implementationKind: behavior-change
    testCommand: |
      CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_reshape.py
  - name: reshape-docs-sync
    description: |
      Document the `reshape_crew` MCP tool and the D0 unconditional-`send_to`
      contract flip in `doc/ARCHITECTURE.md` and `CLAUDE.md`; remove/rewrite any
      prose claiming send_to is wired only for out-edge teammates. Includes a
      doc-staleness grep asserting `_has_out_edges` no longer appears in
      `doc/ARCHITECTURE.md`.
    dependsOn: [reshape-crew-verbs, d0-unconditional-send-to]
    acceptanceTests: [20]
    taskTouches:
      - "doc/ARCHITECTURE.md"
      - "CLAUDE.md"
      - "tests/test_reshape_docs_staleness.py"
    implementationKind: documentation
    testCommand: |
      uv run pytest tests/test_reshape_docs_staleness.py
```

## Design Notes

- **The whole design rests on one verified fact:** routing resolves against live
  broker state at delivery time (`_active_topology_for` scans
  `reversed(_topologies)`; `_edge_mode` reads `_edge_overrides` first). A reshape
  is therefore a data write, never a crew rebuild. Every implementor branch should
  preserve this — never rebuild routing state, only append/write.
- **Reuse over rebuild:** `register_proposal`/`await_proposal`/`resolve_proposal`
  (gate), `record_topology`/`get_topologies` (topology), `spawn_teammate`,
  `kill_teammate`/`_tombstone_teammate` (lifecycle), and `send` (inform) are all
  reused verbatim. The only new broker code is the three tiny D2 helpers.
- **Swap ordering is load-bearing** (AT 9): spawn replacement + `record_topology`
  BEFORE `kill_teammate(old)` — the new topology (appended last) wins in
  `reversed(_topologies)`, so slot-name resolution points at the live replacement
  before the old teammate is tombstoned. Assert via call-order capture.
- **Deletion-detectors are green-suite structural guards** (AT 2 grep for
  `_has_out_edges` absence; AT 4 assert `reshape_crew` registered; AT 20 grep for
  `_has_out_edges` absence in `doc/ARCHITECTURE.md`) — they fail if a deliverable
  is silently reverted, independent of the live suite.
- **`shapes.py` is untouched** — M3.5 shares the M3 verb command objects; the
  algebra is not modified.
