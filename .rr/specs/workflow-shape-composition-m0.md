# Spec: workflow-shape-composition-m0

## Problem

Today claude-crew has exactly one workflow weight: the lead spawns teammates ad-hoc, one
`spawn_teammate` call at a time, with no declarative description of the *shape* of the crew it is
about to run and no human checkpoint on that shape before agents start burning tokens. RepoReactor
is the only "workflow," and it is one-size-fits-all — overkill for a typo, the right size for a gnarly
feature. M0 delivers the keystone of Workflow Shape Composition: it makes a workflow **shape** a
first-class, declarative, legible data structure, and makes **human approval of the proposed shape the
primary gate** before any teammate spawns. The user-visible outcome: a lead can author a shape (nodes =
agent slots, directed edges with a recorded per-edge mode), call `propose_shape` to surface that graph
to Mission Control as a graphical DAG and block until a human approves or declines it, then call
`instantiate_shape` to spawn exactly the approved crew and record a queryable `Topology` in the broker.
M0 adds **zero new inter-teammate communication** — no teammate `send_to`, no edge enforcement — so it
delivers right-sizing legibility with no new chatter risk.

## Architecture Overview

M0 plugs into five existing seams without adding any external runtime (invariant 5 — native only):

- **`claude_crew/shapes.py`** (NEW) — the shape schema: `Shape`, `ShapeNode`, `ShapeEdge` dataclasses +
  `parse_shape()` validating-parser + `ShapeValidationError` + `shape_to_mermaid()` (emits a `graph TD`
  source for the dashboard renderer). Pure data; no broker/SDK dependency. Uses the already-present
  `pyyaml`.
- **`claude_crew/broker.py`** — new in-broker state: `ShapeProposal` (a pending/approved/declined/
  timed_out/instantiated gate record) and `Topology` (recorded edges + per-edge mode + slot→teammate
  map). New methods `register_proposal`, `await_proposal`, `resolve_proposal`, `get_proposal`,
  `record_topology`, `get_topologies`. Both surfaced on `BrokerSnapshot` (the same threading precedent
  as `startup_diagnostics`). The approval block reuses the `asyncio.Condition` long-poll precedent
  (`_lead_message_condition`).
- **`claude_crew/factories.py`** — the sdk factory already builds a merged agent pack into a live
  `_PackState` holder (`holder.pack: dict[str, AgentDefinition]`, read live at spawn) and already attaches
  read-accessors to the returned factory object (`factory.startup_diagnostics`, factories.py:549). M0 adds
  one more accessor in that same idiom: **`factory.known_roles`** — a zero-arg callable returning the live
  pack keys (`tuple(holder.pack.keys())`) — so the `instantiate_shape` pre-flight can enumerate resolvable
  roles without reaching into a closure. The stub factory does **not** set this attribute by default
  (tests inject it), which makes the pre-flight opt-in and keeps today's stub-spawns-anything behavior.
- **`claude_crew/server.py`** — two new lead MCP tools, `propose_shape` and `instantiate_shape`,
  registered with the existing `@mcp.tool()` closure-over-`broker` pattern, extending the 12-tool
  surface to 14. `instantiate_shape` pre-flight-resolves every node `role` against
  `factory.known_roles()` (when exposed) before spawning any node, then reuses the existing
  `broker.spawn_teammate(role=…, factory=…)` spawn path once per node.
- **`claude_crew/ui_server.py`** + **`claude_crew/ui/dashboard.html`** — pending proposals are added to
  `_build_state` (each carrying `crew_id`, per the multi-instance rule) with a mermaid-syntax source
  string; the dashboard renders the proposed shape as a **graphical mermaid DAG** by reusing the shipped
  `mermaid.render()` + DOMPurify/foreignObject XSS-hardening pipeline already in `dashboard.html`
  (`mermaid@11.4.1`, `mermaid.initialize({securityLevel:'strict'})`, `renderMermaidBlocks`), alongside
  Approve/Decline controls. A NEW `POST /shape-approval/{crew_id}/{shape_id}` route resolves the proposal
  locally when `crew_id == own_crew_id` and otherwise proxies leader→follower (mirroring
  `_proxy_artifact`).

### Call-site survey

The proposal-approval round trip has two structurally distinct call-sites that both resolve a proposal,
named here so the implementor builds one resolution path consumed two ways rather than two divergent paths:

| Call-site | Path | Shape | Notes |
|-----------|------|-------|-------|
| Dashboard POST (single-instance) | `ui_server._handle_shape_approval` | `self._broker.resolve_proposal(shape_id, decision)` direct call | crew_id == own crew → serve locally |
| Dashboard POST (multi-instance) | `ui_server._handle_shape_approval` → `_proxy_shape_approval` | HTTP POST proxied to the follower's `/shape-approval` | crew_id != own crew → proxy; follower then hits its own local path |
| Test-stubbed approval | integration test | `broker.resolve_proposal(...)` direct on the injected broker | the "approve (stubbed)" path for `propose → approve → instantiate` |

Resolution: a single broker method `resolve_proposal(shape_id, decision)` is the one authority; all three
sites funnel through it. The proxy site re-enters the *follower's* local site — it does not duplicate
resolution logic. (Operator in-gate *editing* of the shape — `edited_shape` — is explicitly out of scope
for M0; see Out of Scope. The M0 tweak path is decline-then-re-`propose_shape`.)

## Data / API Contracts

```python
# claude_crew/shapes.py  (NEW)

class ShapeValidationError(ValueError): ...

@dataclass(frozen=True)
class ShapeNode:
    slot: str                       # logical slot name; unique within a shape; adaptation targets this
    role: str                       # resolves to an agent-pack definition at instantiate time
    model: str | None = None        # frontier | local | explicit id (passed through to spawn_teammate)
    extra_tools: tuple[str, ...] | None = None
    extra_skills: tuple[str, ...] | None = None
    cwd: str | None = None          # cwd-policy passed through to spawn_teammate

@dataclass(frozen=True)
class ShapeEdge:
    from_slot: str
    to_slot: str
    mode: str = "gated"             # gated | tee | direct ; M0 RECORDS, does not enforce
    reverse_mode: str | None = None # optional back-edge mode; recorded only

@dataclass(frozen=True)
class Shape:
    name: str
    description: str
    nodes: tuple[ShapeNode, ...]
    edges: tuple[ShapeEdge, ...]
    phases: tuple[dict, ...] = ()   # optional lifecycle metadata; recorded VERBATIM, NOT key-validated, not enforced in M0

def parse_shape(data: dict | str, *, source: str = "<inline>") -> Shape:
    """Accept a dict (MCP tool arg) or a YAML string; validate; raise
    ShapeValidationError loudly on any malformation. No partial Shape returned.
    `phases` entries are recorded verbatim and are exempt from the unknown-key guard."""

def shape_to_mermaid(shape: Shape) -> str:
    """Render a Shape to a mermaid `graph TD` source string: one node per slot
    (label = slot\\nrole), one edge per ShapeEdge with the mode as the edge label.
    Consumed by the dashboard's existing mermaid.render() pipeline."""

# claude_crew/factories.py  (addition — same idiom as factory.startup_diagnostics)
# sdk factory:  factory.known_roles = lambda: tuple(holder.pack.keys())   # read LIVE off the holder
# stub factory: no known_roles attribute by default (tests inject one to exercise the pre-flight)

# claude_crew/broker.py  (additions)

@dataclass(frozen=True)
class Topology:
    shape_name: str
    edges: tuple[tuple[str, str, str], ...]   # (from_slot, to_slot, mode)
    slot_to_teammate: dict[str, str]          # slot -> teammate_id

@dataclass
class ShapeProposal:
    shape_id: str
    shape: Shape
    adaptation_diff: str | None
    status: str            # "pending" | "approved" | "declined" | "timed_out" | "instantiated"

# Broker methods
def register_proposal(self, shape: Shape, adaptation_diff: str | None = None) -> str: ...   # -> shape_id, status="pending"
async def await_proposal(self, shape_id: str, timeout: float) -> ShapeProposal: ...          # blocks until status != "pending" or timeout -> "timed_out"
async def resolve_proposal(self, shape_id: str, decision: str) -> ShapeProposal: ...          # decision in {"approve","decline"}; notifies condition
def get_proposal(self, shape_id: str) -> ShapeProposal | None: ...
def record_topology(self, topology: Topology) -> None: ...
def get_topologies(self) -> tuple[Topology, ...]: ...
# BrokerSnapshot gains: shape_proposals: tuple[ShapeProposal, ...] = ()
#                       topologies: tuple[Topology, ...] = ()

# claude_crew/server.py  (new MCP tools)

async def propose_shape(shape: dict, adaptation_diff: str | None = None,
                        timeout_seconds: float = 600) -> dict[str, Any]:
    # parse_shape(shape) -> on ShapeValidationError return {"ok": False, "stage": "parse", "error": <msg>}
    # register_proposal -> shape_id ; await_proposal(shape_id, timeout_seconds)
    # -> {"ok": True, "shape_id": ..., "status": <approved|declined|timed_out>, "shape": <serialized>}

async def instantiate_shape(shape_id: str) -> dict[str, Any]:
    # get_proposal; refuse (ok:False) if missing / status != "approved" -> NO spawn
    # PRE-FLIGHT (all-or-nothing): enumerate valid roles via known = set(factory.known_roles())
    #   IF the factory exposes known_roles. A node.role resolves if it is in `known` OR exactly one
    #   key in `known` ends with f":{role}" (the bare->plugin-namespaced promotion mirroring
    #   factories._resolve_role). If ANY node.role is unresolvable -> return
    #   {"ok": False, "error": ..., "unresolved_roles": [...]} and spawn NOTHING.
    #   IF the factory does NOT expose known_roles (stub default) -> skip pre-flight, preserving
    #   today's spawn-time resolution behavior.
    # spawn each node via broker.spawn_teammate(role=node.role, name=node.slot, factory=factory,
    #   model=node.model, extra_tools=list(node.extra_tools or ()) or None, ...)  # tuple -> list
    # record_topology(...) ; mark proposal status="instantiated"
    # -> {"ok": True, "shape_id": ..., "crew": [{"slot","teammate_id","role"}...], "topology": {...}}

# claude_crew/ui_server.py  (new route)
# Route("/shape-approval/{crew_id}/{shape_id}", self._handle_shape_approval, methods=["POST"])
# body: {"decision": "approve"|"decline"}     # no edited shape in M0
# /api/state shape_proposals entries carry: {shape_id, crew_id, status, adaptation_diff, mermaid: <source str>}
```

## Design Decisions

- **Shape files are YAML documents (not md+frontmatter)** — *Rationale:* md+frontmatter fits a *single*
  agent (frontmatter config + a prose system-prompt body); a shape is a multi-node graph with no prose
  body, which a single YAML document expresses naturally. `pyyaml` is already a dependency and the pack
  loader already uses `yaml.safe_load`. — *Carried into:* `shapes.parse_shape` accepts dict-or-YAML;
  AT#1.
- **`gated`-first default: an omitted edge mode is `gated`** — *Rationale:* resolved fork (thesis
  §Open decisions #2) — the coordinator stays on every edge by default; loosening is an explicit act.
  — *Carried into:* `ShapeEdge.mode` default `"gated"`; AT#1.
- **`tee`/`direct` modes are accepted and RECORDED but not enforced in M0** — *Rationale:* the idea
  scopes M0 to "the field is declared and recorded, not yet enforced as routing"; routing stays exactly
  as today. — *Carried into:* `Topology.edges` records the mode verbatim; AT#7; Out of Scope.
- **The shape is the gate: `instantiate_shape` refuses any shape whose proposal is not `approved`** —
  *Rationale:* invariant 2 — approval is the authorization; no teammate spawns without it. — *Carried
  into:* `instantiate_shape` status check; AT#9, AT#10.
- **Approval is approve-or-decline of the proposed shape as-is (no in-gate editing in M0)** —
  *Rationale:* operator "tweak" (`edited_shape`) is real branching behavior (accept an edited dict,
  re-validate, replace) that overlaps the M3 adaptation algebra and would ship untested in M0; the M0
  tweak path is decline + re-`propose_shape` with an edited shape, which exercises the same parse/gate
  rails. — *Carried into:* `resolve_proposal(shape_id, decision)` has no `edited_shape` param; POST body
  is `{"decision"}` only; Out of Scope; AT#5, AT#11.
- **`propose_shape` blocks on a bounded long-poll** (mirrors `get_messages(wait_seconds=…)` /
  `_lead_message_condition`) — *Rationale:* gives the lead a clean synchronous approved/declined signal;
  bounded so a hung approval surfaces as `timed_out`, never a process hang. — *Carried into:*
  `broker.await_proposal` + `timeout_seconds`; AT#6, AT#8.
- **Pre-flight role resolution → all-or-nothing spawn, enumerating via `factory.known_roles()`** —
  *Rationale:* `instantiate_shape`'s safety-critical promise (one bad role ⇒ zero teammates spawned)
  needs a concrete enumeration source. `server.spawn_teammate` does **not** pre-validate roles today
  (resolution happens inside the factory at spawn time, factories.py:430/536), so the pre-flight needs an
  API the tool can call. The sdk factory already builds the merged pack into a live holder and already
  attaches accessors to itself (`factory.startup_diagnostics`); M0 adds `factory.known_roles` reading
  `tuple(holder.pack.keys())` live — the same enumeration `list_available_tools` gets from
  `discover_dir(agents_dir).keys()` (server.py:584), but for the *merged* pack. Resolution applies the
  same bare→`*:role` promotion as `factories._resolve_role`. When the factory exposes no `known_roles`
  (stub default), the pre-flight is skipped and spawn-time resolution stands (today's behavior). —
  *Carried into:* `instantiate_shape` pre-flight; `factory.known_roles`; AT#14.
- **`instantiate_shape` is single-use per `shape_id`** (status → `instantiated` after success) —
  *Rationale:* a second call would double-spawn the crew. — *Carried into:* proposal status transition;
  Edge Cases.
- **`Topology` is queryable via `BrokerSnapshot`** (same threading precedent as `startup_diagnostics`)
  — *Rationale:* "observable by construction" (invariant 1) — anything that will eventually route must
  be surfaced; M0 lays the rail by making topology visible on Mission Control even though it routes
  nothing yet. — *Carried into:* `BrokerSnapshot.topologies`; AT#7.
- **The shape-approval endpoint is multi-instance aware (carries `crew_id`, proxies leader→follower)**
  — *Rationale:* the project's hard rule — any new per-instance dashboard endpoint must carry `crew_id`
  and proxy, validated by a multi-instance test, not just a single-instance one. — *Carried into:*
  `_handle_shape_approval` + `_proxy_shape_approval`; AT#12.
- **The proposal renders as a graphical mermaid DAG, reusing the shipped renderer** — *Rationale:* a
  graphical mermaid layer **already ships** in `claude_crew/ui/dashboard.html` — `mermaid@11.4.1` loaded
  globally, `mermaid.initialize({securityLevel:'strict'})`, and `renderMermaidBlocks(...)` which calls
  the general `mermaid.render(id, source)` with the established XSS-hardening (`securityLevel:'strict'` +
  DOMPurify-on-output with `foreignObject`/label-tag allowances). Graphical legibility of the shape is
  the feature's core thesis, the renderer exists, and the broker already emits a mermaid-syntax source
  string into `/api/state`, so the incremental cost is wiring the gate panel to feed that string through
  the existing `mermaid.render()` path (reusing its XSS guard). — *Carried into:*
  `shapes.shape_to_mermaid` + `_build_state` `shape_proposals[].mermaid` + the dashboard gate panel's
  `mermaid.render()` call; AT#11, AT#13.

## Edge Cases

- **Empty shape** (zero nodes) → `parse_shape` raises `ShapeValidationError`. (AT#4)
- **Edge references a non-existent slot** (dangling edge) → raises, naming the offending slot. (AT#2)
- **Duplicate slot name** across two nodes → raises. (AT#3)
- **Edge mode outside `{gated, tee, direct}`** → raises. (AT#4)
- **Node missing `role` (or `slot`)** → raises. (AT#4)
- **Unknown key** at the shape/node/edge level → raises loudly (no silent drop). (AT#4)
- **`phases` entries are NOT key-validated** — they are recorded verbatim as opaque lifecycle metadata;
  the unknown-key guard does **not** apply inside `phases` (lifecycle enforcement is deferred). (L2)
- **Omitted edge mode** → defaults to `gated` (not an error). (AT#1)
- **`tee`/`direct` mode declared** → accepted and recorded; routing behavior unchanged from today.
- **Self-loop edge** (`from_slot == to_slot`) → raises (no M0 semantics for self-consultation).
- **Duplicate identical edge** (same from/to) → raises.
- **`instantiate_shape` on an unknown `shape_id`** → `{"ok": False, "error": ...}`, no spawn.
- **`instantiate_shape` on a `pending` / `declined` / `timed_out` proposal** → refused, no spawn. (AT#9, AT#10)
- **`instantiate_shape` called twice on the same approved shape** → second call refused (status is now
  `instantiated`); no double-spawn.
- **Approved shape, a node `role` does not resolve against `factory.known_roles()`** → the pre-flight
  refuses the **entire** instantiate (`{ok: False, unresolved_roles: […]}`); **zero** teammates spawn.
  (AT#14)
- **`extra_tools` / `extra_skills` are `tuple` on `ShapeNode` but `spawn_teammate` wants `list`** →
  `instantiate_shape` converts tuple→list (`list(node.extra_tools or ()) or None`) when forwarding. (L1)
- **`propose_shape` approval never arrives within `timeout_seconds`** → returns `status: "timed_out"`;
  a subsequent `instantiate_shape` refuses.
- **Approval POST for a `crew_id` not in the `InstanceRegistry`** → 404. (AT#12)
- **Approval POST with a malformed `crew_id`/`shape_id` path param** → 400 (mirrors `_handle_artifact`'s
  `_PATH_PARAM_RE` guard).
- **Malicious mermaid in a shape** (e.g. a `role`/`slot` string crafted to inject script through the
  diagram source) → neutralized by the reused `mermaid.render()` `securityLevel:'strict'` + DOMPurify
  output sanitization; labels still render. (AT#13)
- **`adaptation_diff` omitted** → recorded as `None`; the gate still renders the bare proposed shape DAG.
- **Concurrent proposals** → each `register_proposal` returns an independent `shape_id`; resolving one
  does not affect another.

**If this feature affects displayed data, answer these:**
- *UI when data is absent (no pending proposals):* the shape-gate panel is hidden / shows "no pending
  shape proposals"; the rest of the dashboard is unchanged.
- *UI when data is expired/capped (a proposal already resolved):* a resolved proposal drops out of the
  pending gate (only `pending` proposals render the DAG with Approve/Decline controls);
  resolved/instantiated topologies appear in the topology view.
- *UI when a value is zero vs missing:* a shape with zero edges renders a mermaid DAG of disconnected
  nodes (no wires) — distinct from a missing/absent proposal which renders nothing.

## Validation Contracts at Handoff Boundaries

| Boundary | Preconditions | Failure Behavior | Postconditions | Rollback |
|---|---|---|---|---|
| `propose_shape` → broker proposal | shape parses clean | parse error → `{ok:False, stage:"parse"}`, no proposal registered | `pending` proposal exists with a `shape_id`; surfaced in `/api/state` with a mermaid source | n/a (nothing spawned) |
| dashboard POST → `resolve_proposal` | proposal `pending`; crew owns it (or proxy reaches owner) | unknown crew → 404; unknown shape_id → ok:False; malformed param → 400 | proposal `approved`/`declined`; awaiting `propose_shape` unblocks | n/a |
| `instantiate_shape` → `spawn_teammate` ×N | proposal `approved`; every `role` resolves against `factory.known_roles()` (when exposed) | any unresolvable role → refuse before spawning any (`unresolved_roles`) | exactly N teammates spawned; `Topology` recorded; proposal `instantiated` | pre-flight prevents partial spawn; no rollback needed |

## Acceptance Tests

1. **(schema, happy + mermaid)** Given a well-formed shape (≥2 nodes each with `slot`+`role`, edges
   referencing only existing slots, one edge with `mode: gated` and one edge with `mode` **omitted**),
   when `parse_shape` is called, then it returns a `Shape` whose nodes/edges match, the explicit edge
   records `mode == "gated"`, and the omitted-mode edge **defaults** to `mode == "gated"`; and when
   `shape_to_mermaid(shape)` is called, then it returns a `graph TD` source string containing both slot
   labels and an edge labeled with its mode.
2. **(schema, sad — dangling edge)** Given a shape whose edge `to` references a slot absent from
   `nodes`, when `parse_shape` is called, then it raises `ShapeValidationError` whose message names the
   offending slot, and no `Shape` is produced.
3. **(schema, sad — duplicate slot)** Given a shape with two nodes sharing the same `slot`, when
   `parse_shape` is called, then it raises `ShapeValidationError`.
4. **(schema, sad — malformed family)** Given each of these in turn — (a) a shape with zero nodes,
   (b) an edge `mode` outside `{gated,tee,direct}`, (c) a node missing `role`, (d) an unknown key at the
   shape level, (e) a self-loop edge (`from == to`) — when `parse_shape` is called on each, then each
   raises `ShapeValidationError`. (Separately: a shape carrying a `phases` entry with arbitrary keys
   parses **without** error — `phases` is exempt from the unknown-key guard.)
5. **(broker, proposal state machine)** Given a proposal registered via `register_proposal` (status
   `pending`), when `resolve_proposal(shape_id, "approve")` is called then `get_proposal(shape_id).status
   == "approved"`; and given a second registered proposal, when `resolve_proposal(other_id, "decline")`
   is called then `get_proposal(other_id).status == "declined"`.
6. **(broker, await/unblock + timeout)** Given a `pending` proposal being awaited via
   `await_proposal(shape_id, timeout=…)` in one task, when `resolve_proposal(shape_id, "approve")` is
   called from another task, then `await_proposal` returns the proposal with `status == "approved"`; and
   given a separate `pending` proposal with **no** resolution, when `await_proposal` is called with a
   short timeout, then it returns a proposal with `status == "timed_out"`.
7. **(broker, topology recorded + queryable)** Given `record_topology` is called with a `Topology`
   (edges including a non-`gated` recorded mode, plus a `slot_to_teammate` map), when `broker.snapshot()`
   is taken, then `snapshot.topologies` contains that topology with its edges (each carrying its recorded
   mode) and the slot→teammate mapping intact.
8. **(integration, happy: propose→approve→instantiate)** Given a stub-mode server (`make_server` with an
   injected broker and the default stub factory, which exposes no `known_roles`) and a 2-node shape
   (slots `implementor`, `reviewer`), when `propose_shape` is invoked as a task, the proposal is approved
   via `broker.resolve_proposal(shape_id, "approve")`, and `instantiate_shape(shape_id)` is then called,
   then: `propose_shape` returns `status: "approved"`; exactly two teammates are spawned (`list_crew`
   shows the two declared roles, named by slot); and `broker.snapshot().topologies` records the declared
   edge(s) with their modes and the slot→teammate map.
9. **(integration, sad: unapproved instantiate refused)** Given a shape proposed but **not** approved
   (still `pending`), when `instantiate_shape(shape_id)` is called, then it returns `{ok: False}` with an
   error, and `list_crew` shows **no** teammates spawned.
10. **(integration, sad: decline aborts spawn)** Given a proposed shape that is then declined via
    `resolve_proposal(shape_id, "decline")`, then the awaiting `propose_shape` returns `status:
    "declined"`, and a subsequent `instantiate_shape(shape_id)` returns `{ok: False}` and spawns nothing.
11. **(dashboard, single-instance approval + DAG state)** Given a `UiServer` whose own broker holds a
    `pending` proposal, then `GET /api/state` includes that proposal carrying its `crew_id`, `status`, and
    a non-empty `mermaid` source string (a `graph TD` with one node per slot and one edge per declared
    edge labeled by mode) in a pending-proposals field before approval; and when
    `POST /shape-approval/{own_crew_id}/{shape_id}` with body `{"decision":"approve"}` is made, then the
    response is `ok`, the broker proposal transitions to `approved`, and an `await_proposal` on that
    shape_id unblocks with `status == "approved"`.
12. **(dashboard, multi-instance proxy)** Given a leader `UiServer` and a follower instance registered in
    the `InstanceRegistry`, where the follower's broker holds a `pending` proposal, when
    `POST /shape-approval/{follower_crew_id}/{shape_id}` is made **to the leader**, then the leader proxies
    the POST to the follower, the follower's broker resolves the proposal to `approved`, and the response
    is `ok`; and a POST for a `crew_id` absent from the registry returns 404.
13. **(dashboard, graphical mermaid render + XSS guard)** Given the dashboard loaded in a browser with a
    `pending` proposal in `/api/state`, when the shape-gate panel renders, then the proposed shape appears
    as a graphical mermaid SVG produced by the page's existing `mermaid.render()` pipeline (the DAG's node
    labels — slots/roles — are present in the rendered SVG and visible, not black boxes), with
    Approve/Decline controls beside it; and given a proposal whose slot/role text carries a mermaid XSS
    payload, the rendered output is sanitized (no script executes; the established malicious-mermaid
    regression guard still holds) while the diagram still renders.
14. **(integration, sad: unresolvable role → all-or-nothing refusal)** Given a stub-mode server whose
    injected factory **does** expose `known_roles` returning a fixed set (e.g. `("implementor",
    "reviewer")`), and an **approved** 2-node shape where one node's `role` is `"ghost-role"` (not in the
    set), when `instantiate_shape(shape_id)` is called, then it returns `{ok: False}` naming the
    unresolved role and `list_crew` shows **zero** teammates spawned (the resolvable node is **not**
    spawned either — all-or-nothing).

## Test Command

Prerequisites: the schema, broker, MCP-tool, and approval-endpoint tests need none — every import
(`pytest`, `pyyaml`, `httpx`, `starlette`, `claude_crew`) is already in `pyproject.toml`, and all tests
run in stub mode (`CLAUDE_CREW_TEAMMATE_MODE=stub`, set autouse in `tests/conftest.py`) with no live SDK
calls. The dashboard graphical-render test (AT#13) is a Playwright browser test (the existing
`tests/test_dashboard_render.py` pattern); it and the other pre-existing Playwright suites require a
one-time `uv run playwright install chromium`. The single-/multi-instance approval-endpoint tests (AT#11,
AT#12) exercise the Starlette routes over `httpx`/TestClient and need **no** browser. Run the full suite
(this feature adds optional fields to `BrokerSnapshot` and tools to the MCP surface, both cross-cutting,
so the whole suite is the honest gate per the repo's widely-consumed-behavior rule):

```bash
uv run pytest
```

## Out of Scope

- **Operator in-gate shape editing ("tweak on approve", `edited_shape`)** — M0 approval is approve-or-
  decline of the proposed shape as-is; editing the shape at the gate (accept an edited dict, re-validate,
  replace) overlaps the M3 adaptation algebra and is deferred. The M0 tweak path is decline +
  re-`propose_shape` with an edited shape.
- **Per-edge routing enforcement** (`tee`/`direct` actually changing message flow), scoped teammate
  `send_to`, neighbor adjacency injection, circuit breaker — M2. M0 records edge modes as data only;
  routing is unchanged from today.
- **Blessed shape library + lead router/classifier** (`micro-fix`, `standard-feature`, `heavy-feature`
  templates; loading shapes from a `shapes/` dir; problem→shape classification) — M1. M0's
  `propose_shape` takes a shape object as a tool argument, not a file path.
- **Adaptation algebra verbs** (`add_node`/`swap`/`augment`/`set_gate`/`drop`) and structured diffs —
  M3. `adaptation_diff` in M0 is opaque free-text recorded and rendered, not a computed diff.
- **RepoReactor re-authored as the `heavy-feature` shape** — M4.
- **Memory-informed / lead-autonomous adaptation** — M5.
- **New graph-layout sophistication** — M0 reuses the shipped mermaid renderer as-is with a simple
  `graph TD` emission; advanced layout (phase swim-lanes, edge animation, interactive re-layout, click-
  edge-to-log) is deferred (edge animation/promotion is M2).
- **Phase/lifecycle enforcement** — `phases` metadata is parsed and recorded but does not gate edge
  liveness in M0.
- **Model-tier alias resolution** (`frontier`/`local` → concrete model ids) — passed through to the
  existing `spawn_teammate`, whose alias handling is unchanged.

## Assumptions

- **Shape format is a YAML document** — *Default:* YAML (dict accepted directly from the MCP tool arg;
  YAML string accepted for file-backed shapes in M1). — *Rationale:* a shape is a multi-node graph with
  no prose body; YAML matches that better than per-agent md+frontmatter, and `pyyaml` is already a dep.
- **`propose_shape` default timeout is 600s** — *Default:* `timeout_seconds=600`. — *Rationale:* mirrors
  the established `get_messages` long-poll / `_wait_for_lead` budget; bounded so a hung approval becomes
  `timed_out` rather than a process hang.
- **`shape` is passed to `propose_shape` as a JSON object (dict), not a file path** — *Default:* inline
  object. — *Rationale:* loading blessed shapes from a `shapes/` directory is M1; M0 only needs the
  shape-as-data + gate primitives.
- **The dashboard renders the proposal as a graphical mermaid DAG via the shipped renderer** — *Default:*
  emit a `graph TD` mermaid source in `/api/state` and feed it through the existing
  `mermaid.render()` + DOMPurify/foreignObject XSS pipeline in `dashboard.html`; one node per slot
  (label = slot/role), one edge per declared edge labeled by mode. — *Rationale:* the renderer and its
  XSS-hardening already ship and are reusable; graphical legibility is the feature's core thesis.
- **The pre-flight enumerates valid roles via `factory.known_roles()` and is skipped when the factory
  doesn't expose it** — *Default:* sdk factory exposes `known_roles` (live `holder.pack` keys); stub
  factory omits it (so default stub tests spawn anything, preserving today's behavior); tests exercising
  the unresolvable-role refusal inject a `known_roles` on the stub factory. — *Rationale:* mirrors the
  existing `factory.startup_diagnostics` accessor idiom and the `discover_dir(...).keys()` enumeration,
  gives a deterministic, codebase-grounded resolution source, and the human gate already backstops a
  wrong role.
- **`instantiate_shape` is single-use per `shape_id`** — *Default:* status → `instantiated` after a
  successful spawn; re-calls refused. — *Rationale:* prevents accidental double-spawn of a crew.
- **Per-node `model` is passed through to `spawn_teammate` verbatim; `extra_tools`/`extra_skills` are
  converted tuple→list at the forward** — *Default:* pass-through `model`; `list(node.extra_tools or ())
  or None`. — *Rationale:* `spawn_teammate` already accepts `model` and expects `list[str] | None` for
  the tool/skill lists.
- **Approval POST body is `{"decision": "approve"|"decline"}` only** — *Default:* no `shape` field; the
  proposed shape is approved/declined as-is. — *Rationale:* in-gate editing is out of scope for M0 (see
  Out of Scope); keeps the gate contract minimal and fully tested.

## Open Questions

- (none) — every gap the idea flagged (shape file format, gate render mechanics, role-resolution source)
  is resolvable in-slice and recorded above as a Design Decision or Assumption with a default.

## Validation

After feature-review PASS, exercise the user-visible promise — *author a shape, see it gated as a
graphical DAG on human approval, instantiate exactly the approved crew (refusing any unresolvable role),
and read the recorded topology back* — via the feature's own end-to-end tests (propose → approve →
instantiate → assert crew + topology, the sad-path refusals incl. the unresolvable-role all-or-nothing
case, the multi-instance approval proxy, and the graphical mermaid render). Prereq:
`uv run playwright install chromium` for the dashboard render test (AT#13):

```bash
uv run pytest tests/test_shapes.py tests/test_shape_broker.py tests/test_shape_gate.py tests/test_shape_dashboard.py tests/test_shape_render.py
```

Manual Mission Control check (human-judged, pass/fail): start the server (`uv run claude-crew` with the
dashboard enabled), call `propose_shape` with a 2-node shape, and confirm on the dashboard that (PASS) a
pending shape-gate panel renders the shape as a **graphical mermaid DAG** (two labeled nodes + a wire
labeled with its `gated` mode) with Approve/Decline controls; clicking Approve unblocks the lead's
`propose_shape` and a subsequent `instantiate_shape` spawns exactly those two teammates, which then
appear in the live crew view. FAIL if the DAG never renders (or renders as unlabeled black boxes), the
buttons do not resolve the proposal, or instantiate spawns a crew that differs from the approved shape.

## Task Breakout

```yaml
tasks:
  - name: shape-schema-parser
    description: |
      Create claude_crew/shapes.py with the Shape, ShapeNode, ShapeEdge frozen
      dataclasses, the ShapeValidationError type, parse_shape(data, *, source)
      that accepts a dict or YAML string and validates loudly (non-empty
      name/description, >=1 node, unique non-empty slots, non-empty role per node,
      edges referencing only existing slots, mode in {gated,tee,direct} (omitted ->
      "gated"), no self-loops, no duplicate edges, rejection of unknown keys at
      shape/node/edge level; optional `phases` recorded VERBATIM and EXEMPT from the
      unknown-key guard), and a shape_to_mermaid(shape) helper emitting a `graph TD`
      source (node per slot labeled slot/role, edge per edge labeled by mode) for the
      dashboard renderer to consume.
    dependsOn: []
    acceptanceTests: [1, 2, 3, 4]
    taskTouches: ["claude_crew/shapes.py", "tests/test_shapes.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_shapes.py
  - name: broker-proposals-topology
    description: |
      Add ShapeProposal and Topology to claude_crew/broker.py plus the proposal
      state machine: register_proposal, await_proposal (asyncio.Condition long-poll,
      pending -> approved/declined/timed_out, mirroring _lead_message_condition),
      resolve_proposal(shape_id, decision) (approve/decline ONLY — no edited_shape),
      get_proposal, record_topology, get_topologies. Surface shape_proposals and
      topologies as new defaulted-empty fields on BrokerSnapshot (same threading
      pattern as startup_diagnostics).
    dependsOn: [shape-schema-parser]
    acceptanceTests: [5, 6, 7]
    taskTouches: ["claude_crew/broker.py", "tests/test_shape_broker.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_shape_broker.py
  - name: shape-mcp-tools
    description: |
      Add factory.known_roles to the sdk factory in claude_crew/factories.py
      (lambda: tuple(holder.pack.keys()), read live; stub factory leaves it unset),
      mirroring the factory.startup_diagnostics accessor idiom. Register
      propose_shape(shape, adaptation_diff?, timeout_seconds=600) and
      instantiate_shape(shape_id) as new @mcp.tool() closures in claude_crew/server.py.
      propose_shape parses the shape (parse error -> {ok:False, stage:"parse"}),
      registers a proposal, blocks on await_proposal. instantiate_shape refuses any
      non-approved shape_id (no spawn); when the factory exposes known_roles it
      pre-flight-resolves EVERY node role (exact or unique ":role" suffix promotion)
      and, if any is unresolvable, returns {ok:False, unresolved_roles:[...]} spawning
      NOTHING (all-or-nothing); otherwise spawns one teammate per node via
      broker.spawn_teammate(role, name=slot, extra_tools=list(...)...), records a
      Topology, marks the proposal instantiated (single-use).
    dependsOn: [broker-proposals-topology]
    acceptanceTests: [8, 9, 10, 14]
    taskTouches: ["claude_crew/server.py", "claude_crew/factories.py", "tests/test_shape_gate.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_shape_gate.py
  - name: dashboard-shape-state-route
    description: |
      Backend half of the shape-gate (the /api/state seam). Surface pending proposals
      in ui_server._build_state / _build_local_instance (each carrying crew_id, status,
      adaptation_diff, and a mermaid source string from shapes.shape_to_mermaid). Add
      the POST /shape-approval/{crew_id}/{shape_id} route + _handle_shape_approval:
      validate path params (_PATH_PARAM_RE), parse a {decision} body (approve/decline
      only), resolve locally when crew_id == own_crew_id else proxy leader->follower via
      a new _proxy_shape_approval (mirror _proxy_artifact). Includes the required
      single-instance AND multi-instance httpx/TestClient tests.
    dependsOn: [broker-proposals-topology]
    acceptanceTests: [11, 12]
    taskTouches: ["claude_crew/ui_server.py", "tests/test_shape_dashboard.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_shape_dashboard.py
  - name: dashboard-shape-render
    description: |
      Front-end half of the shape-gate. In claude_crew/ui/dashboard.html render a
      shape-gate panel that draws each pending proposal's mermaid source (from
      /api/state) as a graphical DAG by reusing the shipped mermaid.render() +
      DOMPurify/foreignObject XSS-hardening pipeline (the renderMermaidBlocks pattern),
      with Approve/Decline controls that POST to /shape-approval. Add a Playwright test
      asserting the DAG renders with visible node labels (not black boxes) and that a
      malicious-mermaid payload in slot/role text is neutralized while the diagram
      still renders.
    dependsOn: [dashboard-shape-state-route]
    acceptanceTests: [13]
    taskTouches: ["claude_crew/ui/dashboard.html", "claude_crew/ui/**", "tests/test_shape_render.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_shape_render.py
```

## Design Notes

- **`propose_shape` blocking is testable without a real human:** the integration test (AT#8) runs
  `propose_shape` as an `asyncio` task, calls `broker.resolve_proposal(shape_id, "approve")` on the
  injected broker (the "approve (stubbed)" path), then awaits the task. The MCP harness pattern is the
  existing `create_connected_server_and_client_session(make_server(broker=…))` + `await
  s.call_tool("propose_shape", {...})` used throughout `tests/test_server.py`; inject the broker so the
  test can drive `resolve_proposal` directly.
- **Pre-flight enumeration is the `factory.startup_diagnostics` idiom.** The sdk factory already attaches
  read-accessors to itself (factories.py:549) and reads the merged pack live off `holder.pack`
  (factories.py:426/536). `factory.known_roles` is `lambda: tuple(holder.pack.keys())` in that same
  shape — no closure surgery. `instantiate_shape` reads it via `getattr(factory, "known_roles", None)`;
  the stub default (attribute absent) skips pre-flight so AT#8's happy path spawns, while AT#14 injects
  `stub_factory.known_roles = lambda: ("implementor","reviewer")` to drive the all-or-nothing refusal
  deterministically. Role matching reuses the bare→`*:role` promotion semantics of
  `factories._resolve_role`.
- **Tuple→list at the spawn boundary (L1).** `ShapeNode.extra_tools`/`extra_skills` are `tuple[str,…] |
  None` but `broker.spawn_teammate` wants `list[str] | None`; `instantiate_shape` forwards
  `list(node.extra_tools or ()) or None` (and likewise for skills).
- **`phases` is recorded, not validated (L2).** The unknown-key guard rejects stray keys at
  shape/node/edge level, but `phases` entries are opaque lifecycle metadata recorded verbatim — do NOT
  apply the unknown-key guard inside `phases` (enforcement is M-future).
- **Reuse the shipped mermaid renderer, do not add a new one.** `claude_crew/ui/dashboard.html` already
  loads `mermaid@11.4.1`, calls `mermaid.initialize({securityLevel:'strict'})`, and exposes
  `renderMermaidBlocks(node)` which runs `mermaid.render(id, source)` then sanitizes the SVG with
  DOMPurify (allowing `foreignObject` + label tags so labels aren't dropped) — invoked via a React `ref`
  callback. The gate panel feeds the proposal's `mermaid` source string through this same path; the
  malicious-mermaid XSS test is the existing regression guard AT#13 extends.
- **Tool count goes 12 → 14.** Update any test or doc that asserts the exact 12-tool surface count
  (e.g. a `list_available_tools` / surface-count assertion) — this is anticipated sibling-test collateral
  for the `shape-mcp-tools` task; if such an assertion lives in a shared test file, the implementor edits
  it under that task's touch set.
- **`ui_server` already holds `self._broker` and `_own_crew_id()`** (used by `_handle_artifact` /
  `_handle_tool_output` for the local-vs-proxy decision); `_handle_shape_approval` reuses both. The
  proposal condition and the POST handler run in the same process/event loop as the lead's broker, so a
  local `resolve_proposal` notifies the awaiting `propose_shape` in-process — no IPC needed for the
  single-instance path.
- **Dashboard task split (M2).** The shape-gate is split at the `/api/state` contract:
  `dashboard-shape-state-route` (backend: state emission + POST route + proxy, ATs 11/12, httpx tests)
  and `dashboard-shape-render` (front-end: mermaid panel + XSS, AT 13, Playwright). They touch disjoint
  files (`ui_server.py`+`test_shape_dashboard.py` vs `dashboard.html`+`test_shape_render.py`); the render
  task `dependsOn` the state-route task because it consumes the `mermaid` field that task emits.
- **Observability rail (invariant 1):** even though M0 routes no cross-teammate messages, the recorded
  `Topology` (edges + modes + slot→teammate) is surfaced on `BrokerSnapshot` → `/api/state` so Mission
  Control can show the running shape. This is the rail M2's edge animation will plug into.
- **Native-only (invariant 5):** all five tasks extend existing claude_crew modules; no external
  orchestration runtime, no dependency additions (`pyyaml`, `httpx`, `starlette`, `mermaid` already
  present).
