# Architecture: claude-crew

**Created**: 2026-06-09 (harvested from project `CLAUDE.md` + `teammate-death-diagnostics` feature retro)
**Last Updated**: 2026-06-17

claude-crew is a local multi-agent orchestrator. A Claude Code session (the **lead**) drives a crew of Agent-SDK teammates through an MCP server that acts as supervisor, message bus, and observability surface. Teammates can recursively spawn their own subagents.

---

## Module Roles

### `claude_crew/server.py`

FastMCP server. The only surface the lead touches. Exposes **17** MCP tools:

| Tool | Purpose |
|------|---------|
| `spawn_teammate` | Spawn a new SDK teammate from the pack |
| `send_to` | Route an envelope to a specific teammate |
| `broadcast` | Send a message to all live teammates |
| `get_messages` | Long-poll for inbound messages (lead's inbox) |
| `get_wait_endpoint` | Non-blocking message-wait URL (avoids blocking `get_messages`) |
| `list_crew` | Snapshot of all live and tombstoned teammates |
| `kill_teammate` | Terminate a specific teammate |
| `get_teammate_status` | Per-teammate status payload (includes death-record fields) |
| `get_transcript_path` | Path to the crew JSONL transcript |
| `list_available_tools` | Available tool names for a teammate |
| `refresh_agents` | Reload agent definitions from disk; future-spawns-only |
| `surface_document` | Push a markdown artifact to Mission Control |
| `propose_shape` | Register a `Shape` as a pending human-approval gate; **non-blocking by default** (`wait=False`): parse → register → surface → return `{ok, shape_id, status:"pending", shape}` immediately; `wait=True` retains the M0 blocking path via `await_proposal` (600s default timeout) |
| `resolve_shape` | Chat-channel approve/decline: `decision ∈ {"approve","decline"}` → `broker.resolve_proposal`; returns `{ok:True, shape_id, status}` or `{ok:False, error}` on unknown id / non-pending / invalid decision |
| `list_pending_shapes` | Read pending proposals: returns `{ok:True, pending:[{shape_id, name, crew_id, mermaid, summary}]}`; empty list when none pending |
| `instantiate_shape` | Spawn exactly the approved crew; pre-flight role resolution all-or-nothing via `factory.known_roles`; records a `Topology`; single-use per `shape_id` |
| `adapt_shape` | Apply one adaptation verb (`add_node`/`swap`/`augment`/`set_gate`/`drop`) to a pre-instantiation base shape (pending/approved proposal or inline dict); resolves swap/augment roles via the same `factory.known_roles`/`resolve_role` seam as `instantiate_shape`; on success registers a new pending proposal with `adaptation_diff=diff.render()` — reusing the M1.5 gate verbatim; returns `{ok:True, shape_id, status:"pending", diff, shape}` or a staged `{ok:False, stage:...}` failure envelope; no proposal registered on any failure path |

### `claude_crew/shapes.py`

Shape schema. Added in `workflow-shape-composition-m0` (2026-06-11). Pure data module — no broker or SDK dependency.

| Symbol | Kind | Notes |
|--------|------|-------|
| `Shape` | frozen dataclass | `name`, `description`, `nodes: tuple[ShapeNode, ...]`, `edges: tuple[ShapeEdge, ...]`, `phases: tuple[dict, ...]` |
| `ShapeNode` | frozen dataclass | `slot`, `role`, `model`, `extra_tools`, `extra_skills`, `cwd` |
| `ShapeEdge` | frozen dataclass | `from_slot`, `to_slot`, `mode="gated"` (`gated`/`tee`/`direct`), `reverse_mode` |
| `ShapeValidationError` | `ValueError` subclass | Raised on any malformation — no partial `Shape` returned |
| `parse_shape(data, *, source)` | function | Accepts dict or YAML string; validates loudly (empty shape, dangling edges, duplicate slots, invalid mode, unknown keys at shape/node/edge level, self-loops, duplicate edges); `phases` recorded verbatim and exempt from the unknown-key guard |
| `shape_to_mermaid(shape)` | function | Emits a `graph TD` source string (one node per slot labeled `slot\nrole`, one edge per `ShapeEdge` labeled by mode) for the dashboard's `mermaid.render()` pipeline. **Note:** the `\n` separator renders as a literal backslash-n in the browser label rather than a line break; `<br>` is the correct Mermaid syntax — tracked in BACKLOG (pre-existing M0 defect, fast-follow fix) |

**Adaptation algebra additions (M3, `m3-adaptation-algebra` 2026-06-17):** `Shape`/`ShapeNode`/`ShapeEdge` are **unchanged**; the algebra is purely additive.

| Symbol | Kind | Notes |
|--------|------|-------|
| `_UNSET` | module sentinel | Distinguishes "not supplied" from explicit `None` in `Swap`/`SetGate` optional fields; never crosses the public API surface |
| `AdaptationDiff` | frozen dataclass | `verb: str`, `target: str`, `before: dict`, `after: dict` (`before`/`after` excluded from hash so the frozen dataclass stays hashable); `render() -> str` emits the human-readable gate string per verb (format defined in spec; powers AT 7 / AT 35 golden tests) |
| `ShapeAdaptation` | ABC | Abstract base: `apply(shape: Shape) -> tuple[Shape, AdaptationDiff]` — pure, never mutates input, returns a new frozen `Shape` or raises `ShapeValidationError`; no silent no-op, no partial shape |
| `AddNode` | frozen verb command | Appends a node (+ optional wiring edges) to a shape; rejects duplicate slot, dangling edge endpoints, self-loops, duplicate edges |
| `Swap` | frozen verb command | Replaces a slot's `role` and optionally `model`/`extra_tools`/`extra_skills` (supplied → replace; omitted → retain via `_UNSET`); preserves incident edges; role resolved by `adapt_shape` in `server.py` via `factory.known_roles` before `apply` is called |
| `Augment` | frozen verb command | Adds a node alongside an existing one with ≥1 wiring edge (`mode` defaults to `"gated"`); rejects if edge-list is empty (an edgeless augment is just `add_node`); role resolved at server layer |
| `SetGate` | frozen verb command | Changes `mode` (and optionally `reverse_mode`) of an existing edge; validates mode ∈ `{gated, tee, direct}`; retains existing `reverse_mode` when omitted |
| `Drop` | frozen verb command | Removes a node; rejects: slot absent, node has live in/out edges, result would have zero nodes |
| `AdaptationStep` | frozen dataclass | `diff: AdaptationDiff`, `shape: Shape` — the shape produced by one step |
| `AdaptationChain` | frozen dataclass | `base: Shape`, `steps: tuple[AdaptationStep, ...]`; `current` property = `steps[-1].shape` or `base`; `adapt(adaptation)` returns a NEW chain; errors propagate naturally (prior chain left untouched); in-process only — not persisted |
| `shape_to_dict(shape)` | function | Inverse of `parse_shape`: `parse_shape(shape_to_dict(s)) == s` for all valid `Shape`s; preserves `phases`, node `cwd`/`model`/`extra_tools`/`extra_skills`, edge `reverse_mode` (omits `None`/empty where `parse_shape` reconstructs identically); serializes tuples→lists for JSON compatibility |

### `claude_crew/broker.py`

Single source of truth for team state. Owns the teammate registry, append-only message log, per-inbox queues, monotonic sequence counter, and dedup set. Tombstones dead teammates (marks dead, preserves in registry for status queries). Writes lifecycle and envelope records to the transcript sink. Also holds the **shape proposal registry** and **recorded topologies** (added in `workflow-shape-composition-m0`).

**Shape proposal state machine** (new in `workflow-shape-composition-m0`):

| Method | Notes |
|--------|-------|
| `register_proposal(shape, adaptation_diff?)` | Returns a `shape_id`; proposal status = `"pending"` |
| `await_proposal(shape_id, timeout)` | `asyncio.Condition` long-poll (mirrors `_lead_message_condition`); `pending` → `approved`/`declined`/`timed_out` |
| `resolve_proposal(shape_id, decision)` | `decision ∈ {"approve","decline"}`; enforces pending-only guard; notifies `_proposal_condition`; **also sends `{type:"shape_resolved", shape_id, status}` to `LEAD_ID`** via the lead-message channel — single choke point inherited by both the chat channel (`resolve_shape`) and the UI channel (`POST /shape-approval`); no `edited_shape` param (M0/M1.5 is approve/decline only) |
| `get_proposal(shape_id)` | Returns `ShapeProposal \| None` |
| `record_topology(topology)` | Stores a `Topology` (edges + slot→teammate map) post-instantiation |
| `get_topologies()` | Returns `tuple[Topology, ...]` |

`BrokerSnapshot` gains `shape_proposals: tuple[ShapeProposal, ...] = ()` and `topologies: tuple[Topology, ...] = ()` (same threading precedent as `startup_diagnostics`).

Key method: `_tombstone_teammate` — called when a teammate dies. Reads the teammate's final snapshot (step 4), populates death-record fields, calls `_close_open_tools` to abandon in-flight tools (step 6), and serializes the result to the transcript.

**M2 additions** (edge routing + circuit breaker, `m2-edge-routing` 2026-06-13):

| Symbol / Method | Notes |
|-----------------|-------|
| `UnauthorizedEdgeError` | Raised by `authorize_send` when no forward edge exists in the active topology for the sender→recipient pair |
| `EdgeStat` | Frozen dataclass: `from_slot`, `to_slot`, `mode` (effective — honoring `_edge_overrides` and trips), `exchanges` (delivered `tee`/`direct` count), `tripped` (True when auto-tripped by budget, not by `promote_edge`) |
| `CIRCUIT_BREAKER_MAX_EXCHANGES` | Module constant (`int = 8`): per-directed-edge exchange budget |
| `BrokerSnapshot.topology_edge_stats` | `tuple[EdgeStat, ...]`; populated by walking `reversed(_topologies)`, one entry per unique `(from_slot, to_slot)` pair (latest topology wins) |
| `_send_routed(env)` | Routes teammate→teammate messages by resolved mode. `direct` → recipient inbox only. `tee` → recipient inbox + CC envelope `{cc_of, from, to, payload}` to lead. `gated` / no-edge-fallback → wrapper `{gated_for, from, payload}` to lead; original id deduped so no replay on re-deliver. |
| `_apply_circuit_breaker(env, routing_mode, ts)` | Called for `tee`/`direct` candidates. Increments `_edge_exchanges[(f,t)]`. On budget exceeded: writes `_edge_overrides[(f,t)]="gated"`, adds to `_edge_tripped`, emits `{type:"circuit_breaker", edge, reason:"budget_exceeded"}` to lead, returns `"gated"`. Idempotent. **No deadlock detector** — 2-node pending-flag check removed by coordinator adjudication: "A waits B waits A" is not well-defined at a message-bus level; the budget is the sole runaway guard. |
| `authorize_send(sender_id, recipient_id)` | No-op for lead; raises `UnauthorizedEdgeError` if no forward edge in active topology |
| `send_scoped(sender_id, recipient, payload, *, id=None)` | Resolves recipient (slot name / teammate-id / `LEAD_ID`), calls `authorize_send`, then `send()`. **Moat choke-point**: the only broker entry path for non-lead teammate sends |
| `promote_edge(from_slot, to_slot)` | Sets `_edge_overrides[(from_slot, to_slot)] = "gated"`. Idempotent. Used by `POST /edge-promote` |

### `claude_crew/teammate.py`

Abstract base class. Defines the inbox-consumption loop, activity tracking (`_begin_turn` / `_end_turn` / `_stamp_activity`), and tool tracking (`_tool_uses` in-flight dict, `_last_tool_completed`). `StubTeammate` is the echo implementation used in tests.

### `claude_crew/sdk_teammate.py`

Production teammate backed by `claude-agent-sdk`. Per-turn loop: pull envelope → translate to prompt → query SDK → drain response → send result envelope. Attaches PreToolUse/PostToolUse hooks for tool tracking (F8) and PreSubagentUse/PostSubagentUse hooks for subagent activity tracking (F7). Includes liveness polling (background task detects SDK death) and a per-turn backstop timeout.

Also owns the **stderr ring buffer subsystem** and **death-site telemetry** — see below.

Also owns the **scoped `send_to` in-process MCP server** (added in `m2-edge-routing` 2026-06-13): each spawned `SdkTeammate` runs a private FastMCP server (`_build_send_to_mcp_server()`) exposing a single `send_to(recipient, message)` tool whose handler calls `broker.send_scoped`. This is the sole channel by which a teammate can address the broker for non-lead sends — see [Edge Routing (M2)](#edge-routing-m2) below.

Also owns the **plan-mode write gate** (added in `plan-gate-and-telemetry-hardening` 2026-06-17): `_PLAN_MODE_DENIED_TOOLS: frozenset = {"Write","Edit","NotebookEdit","MultiEdit"}` (module-level); `self._effective_permission_mode: str | None` (stashed at options-build time from spawn-arg-wins-then-role-pack resolution, before the SDK client context opens, so the hook always sees the same value the SDK received). In `_on_pre_tool_use`, AFTER the memory-write guard and BEFORE the subagent/main tracking branches: when `_effective_permission_mode == "plan"` and `tool_name in _PLAN_MODE_DENIED_TOOLS`, returns `permissionDecision: "deny"`. Read-only tools (`Read`, `Grep`, `Glob`, `Bash`, `WebFetch`, `Task`) are NOT denied — a plan-mode teammate is gated, not neutered. This is a claude-crew-side enforcement that does not depend on the SDK's plan gate (which as of claude-agent-sdk 0.1.68 / CLI 2.1.177 presents an approval UI instead of silently blocking in headless sessions — see Verified SDK Behavioral Invariants).

Also owns the **TNM arrival-order correlation** (updated in `plan-gate-and-telemetry-hardening` 2026-06-17): `_task_notifs_ordered: list[TaskNotificationMessage]` replaces the former `_task_notifs_by_tool_use_id: dict` field. `_record_task_notif` appends TNMs in stream-arrival order (neither `task_id` nor `tool_use_id` matched the PostSubagentUse hook id in SDK 0.1.68 — verified by runtime probe). `_end_turn` correlates the i-th TNM with the i-th closed-scratch entry. The `"no TNM for subagent"` WARNING is preserved for the genuinely-missing case (TNM count < scratch count). Re-verify field correlation when upgrading `claude-agent-sdk`.

### `claude_crew/envelope.py`

Wire format. Fields: `id` (caller-provided UUID for retry safety), `seq` (broker-stamped monotonic), `sender`, `recipient`, `timestamp`, `payload`.

### `claude_crew/factories.py`

Selects teammate implementation. `CLAUDE_CREW_TEAMMATE_MODE=stub` → `StubTeammate` (default in tests). `sdk` (default in production) → `SdkTeammate`. SDK mode merges the default subagent pack with `~/.claude/agents/` and project `.claude/agents/`.

The SDK factory attaches read-accessors to itself at build time (same pattern as `factory.startup_diagnostics`). Added in `workflow-shape-composition-m0`: **`factory.known_roles`** — a zero-arg callable returning `tuple(holder.pack.keys())` read live off the merged pack holder. Used by `server.instantiate_shape` pre-flight to enumerate resolvable roles. The stub factory does not set this attribute by default (tests inject it to exercise the all-or-nothing refusal path).

Added in `m2-edge-routing`: **`neighbors=` kwarg threading** — `spawn_teammate` now passes the declared out-neighbor list for a slot through both `stub_factory` and `sdk_factory` / `default_factory`'s inner closure. `StubTeammate` accepts and ignores it. `SdkTeammate` receives the neighbor list and passes it to `build_teammate_prompt` (a "Neighbors" section is injected into the system prompt). **Invariant**: the neighbor list given to each teammate is derived from the same `shape.edges` as the `_send_routed` authorization check — they cannot drift.

### `claude_crew/transcript.py`

Best-effort JSONL sink. Path resolves via `CLAUDE_CREW_TRANSCRIPT_DIR` → `$XDG_STATE_HOME/claude-crew/transcripts/` → `~/.local/state/claude-crew/transcripts/`. Disabled in tests via `CLAUDE_CREW_TRANSCRIPT_DISABLED=1`.

### `claude_crew/redaction.py`

Tool telemetry redaction (v1 allowlist: Bash, Task, WebFetch). Extracts and redacts secrets from tool args before storage; caps at 256 bytes. Also used by `SdkTeammate._stderr_tail_redacted()` to sanitize ring contents before they leave the `SdkTeammate` instance.

### `claude_crew/diagnostics.py`

Startup-time diagnostic capture. `StartupDiagnostic` frozen dataclass + `StartupDiagCollector` logging handler + `collect_startup_diagnostics()` context manager. Six-category classifier (shadow / unknown_skill / unknown_mcp_server / frontmatter / plugin / other). Surfaced on the dashboard via the Startup Notices panel.

### `claude_crew/subagents/`

Default subagent pack. Three agents (`explorer`, `planner`, `general-purpose`) defined as markdown files with YAML frontmatter (model, tools, effort, maxTurns). No Bash or Task tool — leaf nodes that cannot recurse further.

---

## Telemetry Subsystem: Stderr Capture & Death Diagnostics

Added in `teammate-death-diagnostics` (2026-06-09). Purely additive — no control-flow change.

### Stderr ring buffer (`SdkTeammate`)

```
SdkTeammate
 ├── _stderr_ring: deque[str]       (maxlen=50)
 ├── _stderr_ring_bytes: int        (running byte total)
 ├── _on_stderr_line(line) → None  (SDK callback; never raises)
 └── _stderr_tail_redacted() → str | None
```

**Constants**:
- `_STDERR_RING_MAXLEN = 50` — max lines retained
- `_STDERR_RING_BYTE_CAP = 65_536` — 64 KB total byte budget

**Eviction**: dual-axis. The `deque(maxlen=50)` evicts oldest when line count exceeds 50. The byte cap is enforced separately: when adding a line would exceed `_STDERR_RING_BYTE_CAP`, oldest entries are popped until the cap is satisfied before appending.

**Callback**: `_on_stderr_line` is registered as `opts_kwargs["stderr"] = self._on_stderr_line` in `SdkTeammate._run` (line 1432 at time of authoring). It fires on the event loop once per decoded stderr line from the subprocess. It uses `try/except Exception: return` to guarantee it never raises — a raising callback would terminate the SDK transport's `_handle_stderr` coroutine silently, losing all future stderr for the session.

**Redaction**: `_stderr_tail_redacted()` joins the ring contents and runs `redact_output` before returning. Raw ring content never leaves `SdkTeammate` through any code path. Returns `None` when the ring is empty.

### Producer/consumer snapshot-key contract

`SdkTeammate.status_snapshot()` exposes two new keys consumed by `broker._tombstone_teammate`:

| Key | Type | Semantics |
|-----|------|-----------|
| `stderr_tail` | `str \| None` | Redacted ring tail. `None` when ring is empty. |
| `in_flight_tools` | `list[dict]` | Tools in flight at snapshot time (`current_tools` at that instant). Always a list when the snapshot is readable (never `None` from this path). |

**None-vs-`[]` semantics** (critical for consumers):

| `in_flight_tools_at_death` value | Meaning |
|----------------------------------|---------|
| `None` | Snapshot could not be read (teammate was `None`, or `status_snapshot()` raised `AttributeError`) |
| `[]` | Snapshot readable; no tool was in flight at death |
| `[{...}, ...]` | Tools that were in flight when the snapshot was taken |

The same `None` = unavailable / `[]` = empty distinction applies to `stderr_tail_at_death`:
- `None` = ring was empty **or** snapshot unavailable (the caller cannot distinguish these two sub-cases from the death record alone)
- `str` = redacted tail content

### Death-record fields (`TeammateInfo`)

```python
@dataclass
class TeammateInfo:
    ...
    stderr_tail_at_death: str | None = None
    in_flight_tools_at_death: list[Any] | None = None
```

Populated in `_tombstone_teammate` at **step 4** — before `_close_open_tools` abandons tools at step 6. This ordering is load-bearing: reading the snapshot after `_close_open_tools` would always yield an empty `in_flight_tools`. Both fields are serialized onto the dead-teammate status dict under the same key names and appear in the dashboard payload and JSONL transcript.

### Death-site WARNING

Emitted at the `ProcessError` / `CLIConnectionError` / `BrokenPipeError` catch arm in `SdkTeammate._handle_one_turn` (primary death path only; the graceful-flush arm is out of scope). Log format:

```
WARNING teammate <id> died: exc=<ExcClass> exit_code=<N> last_tool=<name> stderr_tail=<redacted-tail>
```

Uses `%`-style lazy logging args (not f-strings) to match the module's existing call style and avoid formatting cost when the level is suppressed.

### Redaction-before-persist invariant

**Invariant**: the raw stderr ring never leaves `SdkTeammate`. Every code path that reads the ring for external consumption (`_stderr_tail_redacted()`, called by `status_snapshot()`, the death-site WARNING, and transitively by the broker death-record population) passes the joined content through `redact_output` first. The raw ring exists only inside `SdkTeammate` and is garbage-collected with the object after death.

---

## Workflow Shape Composition (M0 + M1.5 + M2 + M3)

**M0** added in `workflow-shape-composition-m0` (2026-06-11) — Makes a crew **shape** a first-class, declarative, legible data structure and gates teammate spawning on human approval. Purely additive — no control-flow change to the existing spawn, routing, or message paths.

**M1.5** added in `m1-5-async-shape-gate` (2026-06-12) — Makes the gate **async/non-blocking** (lead stays free during approval; `wait=True` retains the M0 blocking path), adds a **chat-channel approval path** (`resolve_shape` + `list_pending_shapes`, tool count 14→16), **notifies the lead on resolve** via a single choke point in `broker.resolve_proposal`, promotes the gate to a **resurfaceable** `MCTopBar` badge/tray surface in the dashboard (survives instance-switch), and intentionally softens the human-in-the-loop guarantee from *mechanical* to *trust-enforced* (the bridge to M1 trusted-shape auto-approval).

**M2** added in `m2-edge-routing` (2026-06-13) — Makes the approved graph **execute**: edge modes enforced, scoped `send_to` in-process MCP tool, neighbor injection, budget-only circuit breaker, and on-graph dashboard overlay. See [Edge Routing (M2)](#edge-routing-m2) below for full detail.

**M3** added in `m3-adaptation-algebra` (2026-06-17) — Makes shape adaptation **computable**. Five typed verb commands in `shapes.py` (`AddNode`/`Swap`/`Augment`/`SetGate`/`Drop`), each pure: `apply(shape) -> tuple[Shape, AdaptationDiff]` — never mutates input, returns a new frozen `Shape` (satisfying all `parse_shape` invariants) or raises `ShapeValidationError`; no silent no-op, no partial shape. `AdaptationDiff.render()` produces the human-readable gate string that feeds the existing `adaptation_diff: str` broker channel — **no gate-signature change; `broker.py` is untouched** (`register_proposal` already carried the `adaptation_diff?` param). `AdaptationChain`/`AdaptationStep` carry in-process provenance (not persisted to broker). `adapt_shape` MCP tool in `server.py` resolves the base shape (pending/approved proposal or inline dict), resolves `swap`/`augment` roles via the same `factory.known_roles`/`resolve_role` seam as `instantiate_shape`, applies the verb command, and on success calls `broker.register_proposal(new_shape, adaptation_diff=diff.render())` — reusing the M1.5 gate verbatim. Every adapted shape re-satisfies `parse_shape`. Adaptation is **pre-instantiation only** — reshaping a running crew is the separately-milestoned M3.5.

### Data flow

```
# M1.5 default path (wait=False) — non-blocking
propose_shape(shape_dict, wait=False)
  → parse_shape()          shapes.py       validates; raises ShapeValidationError on malformation
  → register_proposal()    broker.py       status="pending"
  → propose_shape returns  server.py       {ok, shape_id, status:"pending", shape}  ← immediately

  # Dashboard surface — resurfaceable (M1.5)
  /api/state               ui_server.py    shape_proposals[].{crew_id, status, mermaid, name, summary} emitted
  MCTopBar pending-gate-pill  dashboard.html  cross-instance flatMap count badge; click → ShapeGatePanel
  ShapeGatePanel           dashboard.html  mermaid → renderMermaidBlocks → graphical DAG; survives instance-switch

  # Resolution — either channel (both route through resolve_proposal choke point):
  resolve_shape(id, dec)   server.py       NEW (M1.5) chat-channel: validate → broker.resolve_proposal()
  POST /shape-approval     ui_server.py    dashboard path: local resolve OR leader→follower proxy

  → resolve_proposal()     broker.py       pending-only guard → approved/declined; notifies _proposal_condition
                                           + send({type:"shape_resolved", shape_id, status}) to LEAD_ID  ← M1.5
  → get_messages() wakes   server.py       lead receives {type:"shape_resolved", shape_id, status}; no polling

# M0-compat opt-in blocking path
propose_shape(shape_dict, wait=True)
  → (same parse+register as above)
  → await_proposal()       broker.py       asyncio.Condition long-poll; returns resolved status

instantiate_shape(shape_id)
  → get_proposal()         broker.py       refuse if status != "approved"
  → pre-flight             server.py       enumerate factory.known_roles(); any unresolvable → ok:False, zero spawns
  → spawn_teammate() ×N    broker.py       one per ShapeNode; name=slot
  → (transactional)        server.py       on any spawn failure: kill already-spawned, ok:False, proposal stays "approved"
  → record_topology()      broker.py       Topology{edges, slot_to_teammate (immutable)} on BrokerSnapshot
  → broker.mark_instantiated()  broker.py  approved→instantiated under the broker boundary; single-use guard

# M3 — pre-instantiation adaptation (adapt_shape)
adapt_shape(verb, params, base_shape_id?/base_shape?)
  → base resolution         server.py       exactly-one guard (neither/both → stage:"base"); base_shape_id must resolve to pending/approved proposal (instantiated/declined/timed_out rejected); inline base_shape parsed via parse_shape (failure → stage:"parse")
  → verb guard              server.py       verb ∉ {add_node,swap,augment,set_gate,drop} → stage:"verb"
  → role resolution         server.py       getattr(factory,"known_roles",None) + resolve_role for swap/augment only; skipped when known_roles absent (stub mode mirrors instantiate_shape)
  → verb.apply(base)        shapes.py       pure: new frozen Shape + AdaptationDiff; raises ShapeValidationError on illegal mutation → stage:"adapt"; KeyError/TypeError on malformed params → stage:"adapt"
  → register_proposal()     broker.py       adaptation_diff=diff.render(); same gate path as propose_shape; returns shape_id
  → returns                 server.py       {ok:True, shape_id, status:"pending", diff, shape=shape_to_dict(new_shape)}
  # failure paths all return before register_proposal; no proposal registered on any failure
```

### Edge modes: recorded in M0, enforced from M2

Every `ShapeEdge` carries a `mode` ∈ `{"gated", "tee", "direct"}` (omitted → `"gated"`) describing **how messages flow along that edge between two teammates**:

- **`gated`** — the message lands in the **lead/coordinator's inbox first**; the lead approves/forwards. The coordinator is on the wire. *(M0/M1.5 default — every edge was effectively gated before M2.)*
- **`tee`** — the message goes **A→B directly**, but the lead gets a **copy** and can interrupt. Coordinator watches, blocks nothing.
- **`direct`** — the message goes **A→B directly** with no lead turn; the broker still logs / sequences / surfaces it (observable), but the coordinator is not in the loop.

`Topology.edges` records the mode as `(from_slot, to_slot, mode)` triples verbatim. In M0 the mode was recorded only; from **M2 onwards** `broker._send_routed()` resolves and enforces the mode on every teammate→teammate message. See [Edge Routing (M2)](#edge-routing-m2) below.

> **Naming note — two unrelated "gate" concepts.** The **shape-gate** is the *human-approval checkpoint*: `propose_shape` registers a pending proposal that a human must approve — over chat (`resolve_shape`) or the dashboard modal — before the crew can be instantiated (non-blocking since M1.5; `wait=True` retains the M0 blocking path). A **`gated` edge** is a *per-edge routing mode*: messages on it route through the coordinator. Same word, different mechanisms — the shape-gate is a *moment of human approval*; a gated edge is a *property of a connection* between two teammates.

### Multi-instance shape approval

The `POST /shape-approval/{crew_id}/{shape_id}` route follows the same multi-instance rule as all per-instance dashboard endpoints: carry `crew_id`, resolve locally when `crew_id == self._own_crew_id()`, proxy to the follower otherwise (`_proxy_shape_approval` mirrors `_proxy_artifact`). `_PATH_PARAM_RE` guards both path params (400). Unknown `crew_id` → 404.

### Invariants

- **Shape is the gate**: `instantiate_shape` refuses every non-`approved` status before touching the spawn path. Nothing spawns without a human (or stubbed) approval. *(Un-softened in M1.5.)*
- **All-or-nothing pre-flight**: full node loop accumulates `unresolved` before any `spawn_teammate` call. One bad role → zero teammates spawned.
- **Single-use**: `status="instantiated"` after a successful spawn; a second `instantiate_shape` call sees `instantiated` and refuses.
- **XSS-hardened DAG**: mermaid source flows through `mermaid.initialize({securityLevel:'strict'})` + DOMPurify/foreignObject output sanitization already present in `dashboard.html`. No new renderer added.
- **Single choke-point notify** *(M1.5)*: `broker.resolve_proposal` is the only path that resolves a proposal. It always sends `{type:"shape_resolved", shape_id, status}` to `LEAD_ID` before returning — regardless of which channel (chat or UI) triggered it. No resolution path can complete without waking the lead's `get_messages` loop.
- **Non-blocking gate / trust-enforced guarantee** *(M1.5)*: `propose_shape(wait=False)` returns `pending` immediately; the lead is free throughout approval. `resolve_shape` is on the lead MCP surface, so the coordinator *can* resolve a gate (including one it proposed). This is an intentional softening from M0's mechanical barrier (UI-only, model can't click). The trust-enforced path is the bridge to M1's trusted/blessed-shape auto-approval.
- **Resurfaceable gate** *(M1.5)*: pending-gate badge in `MCTopBar` is derived from a cross-instance `flatMap` over all `shape_proposals` filtered to `status === "pending"`; it persists across instance-switch and on modal close; it clears only when a proposal resolves.

---

## Edge Routing (M2)

Added in `m2-edge-routing` (2026-06-13). Makes the `Topology` recorded at `instantiate_shape` time the live **routing rail** for all teammate→teammate messages.

### Routing model

`broker._send_routed(env)` is invoked for every `send()` call where `env.recipient != LEAD_ID` and `env.sender` is a known teammate. It resolves the routing mode via private helpers and dispatches:

| Resolved mode | Behavior |
|---------------|----------|
| `direct` | Stamp + log envelope to recipient's inbox. No lead notification. |
| `tee` | Stamp + log to recipient's inbox; create CC envelope `{cc_of, from, to, payload}` stamped + logged to lead. |
| `gated` | Deduplicate original id; create wrapper `{gated_for, from, payload}` stamped + logged to lead. Original NOT in recipient inbox, NOT in broker log. |
| no-edge fallback | Same as `gated`. Applies when the sender has an active topology but no declared edge to the recipient, OR when no topology exists at all. |

**Fallback is always gated**: a message to a non-neighbor (or sent before any topology is recorded) routes as gated — the coordinator stays on the wire until an edge is explicitly declared and approved.

### Circuit breaker (budget-only)

`broker._apply_circuit_breaker(env, routing_mode, ts)` is called for every `tee`/`direct` candidate before routing:

- Increments `_edge_exchanges[(from_slot, to_slot)]`.
- When the count exceeds `_circuit_breaker_max_exchanges` (default: `CIRCUIT_BREAKER_MAX_EXCHANGES = 8`): writes `_edge_overrides[(f,t)] = "gated"`, adds to `_edge_tripped`, emits `{type:"circuit_breaker", edge:[f,t], reason:"budget_exceeded"}` to lead, returns `"gated"` so the triggering message is rerouted as a gated wrapper.
- Idempotent: if the edge is already overridden, returns the current mode unchanged.

**No deadlock detector.** The spec-proposed 2-node "both-pending" flag (`_edge_pending`) was removed by coordinator adjudication (Jerome + Kael, 2026-06-13):

> "A waits B waits A" is not well-defined at a message-bus level — the broker cannot distinguish a blocked peer from one that simply hasn't responded yet. Reply-clearing makes the both-pending state unreachable in practice (dead code). Any non-clearing implementation would contradict the reciprocal-peer-conversation contract. **The per-edge exchange budget is the sole runaway guard** — it already force-inserts the lead on a runaway loop.

AT#7 was amended to assert: reciprocal direct exchanges below budget do NOT trip the breaker; only budget-exceeded trips.

### Scoped `send_to` — sole teammate→broker entry

Each `SdkTeammate` spawned with a non-empty `neighbors` list runs a private in-process FastMCP server exposing one tool:

```
send_to(recipient: str, message: str) → {ok, seq}
```

The handler calls `broker.send_scoped(sender_id, recipient, {"text": message})`. `send_scoped` resolves the recipient (slot name → teammate-id via active topology, or `LEAD_ID`), calls `authorize_send` (raises `UnauthorizedEdgeError` for non-neighbors), and routes via `send()`.

**This is the moat choke-point**: the only way a teammate can address the broker for non-lead sends. There is no other API surface through which a running teammate can inject messages into the broker's routing engine. The `neighbors` parameter is injected into the teammate's system prompt by `teammate_prompt.build_teammate_prompt` so the model knows which recipients are valid.

### Neighbor injection and authorization coupling

`spawn_teammate` derives the `neighbors=` list for each slot from `shape.edges`: the list of `to_slot` values on edges whose `from_slot` matches the spawned slot. This list is passed through `factories.py` to `SdkTeammate.__init__` and also used to authorize `send_scoped`.

**Invariant**: the neighbor list in the system prompt is derived from the same `shape.edges` record as the `authorize_send` check. They share a single source of truth and cannot drift.

### Dashboard edge observability (M2)

`/api/state` carries `topology_edge_stats: list[EdgeStat]` per crew instance (sourced from `BrokerSnapshot.topology_edge_stats`; each entry includes `{from_slot, to_slot, mode, exchanges, tripped, crew_id}`), plus the additive `slot_to_teammate: {slot: teammate_id}` map (sourced from `BrokerSnapshot.topology_slot_to_teammate`, aggregated from all recorded topologies with last-write-wins on slot collision). `slot_to_teammate` is the authoritative join that lets the unified view attach per-teammate activity (keyed by teammate-id) onto per-slot routing nodes — not an `agent.role === slot` guess (slot labels are author-defined and may differ from pack roles).

Two new per-instance endpoints (fully multi-instance proxied via `crew_id`):

| Endpoint | Purpose |
|----------|---------|
| `GET /edge-log/{crew_id}/{from}/{to}` | Returns `{ok, edge, messages}` — the routed-message log for one directed edge |
| `POST /edge-promote/{crew_id}/{from}/{to}` | Promotes an edge to `gated` via `broker.promote_edge`; returns `{ok, edge, mode:"gated"}` |

Both guarded by `_PATH_PARAM_RE` (`^[A-Za-z0-9_\-]+$`), return 400 on bad param, 502 on proxy failure. Slot→teammate-id resolution walks `reversed(topologies)` (latest topology governs — Assumption #3).

**Unified topology view (supersedes the M2 two-graph layout).** `dashboard.html` renders a single `TopologyGraph` component (which replaced the pre-M2 lead-centric `MiniGraph` roster hub *and* the M2 `TopologyEdgePanel` edge overlay — both removed). One mermaid graph carries both layers on disjoint visual channels:
- **Activity layer** (node): each node is a `foreignObject` card (slot label + 8-char teammate-id) whose border color + 1.6s pulse + corner dot encode the occupant teammate's status, joined via `cli.slot_to_teammate` (→ teammate-id → `agents[].status`). The legacy teammate→lead `animateMotion` spoke pulses are deliberately removed (edge motion would collide with the routing channel).
- **Routing layer** (edge): post-`mermaid.render()` SVG-walk decoration colors each `path.flowchart-link` by mode (direct=green, tee=blue, gated=amber, tripped=red, thicker on tripped), with a 550ms exchange-increment pulse, a selected-edge message-log panel, and a promote-to-gated control.
- **Gated-through-lead bridging:** the broker records gated edges peer→peer; a `displayEdges` memo expands each gated `EdgeStat` into two synthetic segments (`from→lead`, `lead→to`, both amber) carrying the source `EdgeStat` on `_source`, so `lead` visibly bridges gated routes. Click/badge/log on either segment dispatch against the SOURCE peer endpoints.
- **BC-03 keyed edge mapping:** `window.mapEdgeStatsToPaths(svgRoot, edgeStats)` resolves each rendered path to its `EdgeStat` by **endpoint identity** — parsing the mermaid path id (`/^L[-_](.+?)[-_](.+?)[-_]\d+$/`) with an `LS-`/`LE-` class fallback and a positional last-resort that `console.warn`s. This replaces M2's positional `links[i]→edgeStats[i]` mapping, which aliased reciprocal pairs when mermaid v11 reordered paths during layout. A green-suite Playwright deletion-detector (`tests/test_unified_topology_keyed_lookup.py`) fails if the lookup reverts to positional.
- **Roster fallback:** when `topology_edge_stats` is empty, the same component renders a `graph LR` star (`lead → teammate_*`, neutral grey, no badges/click, legend hidden, subtitle "roster — no shape instantiated").
- Multi-instance correct: all `/edge-log` + `/edge-promote` fetches carry `crewId`.

---

## Verified SDK Behavioral Invariants

These are empirically confirmed facts about the `claude-agent-sdk` / Claude CLI boundary. Re-verify when upgrading `claude-agent-sdk`.

**`AgentDefinition(tools=[])` enforces a true no-tools surface.** Verified live 2026-05-02: a subagent declaring `tools=[]` has no tools available at the SDK boundary — operators omitting `tools:` get a no-tool agent, NOT silent inherit-all.

**`AgentDefinition(model=None)` is wire-safe.** The SDK serializes via `{k: v ... if v is not None}`; absent `model:` in a pack = no `--model` flag = SDK default at spawn.

**Token/cost telemetry rolls up at end-of-turn.** `ResultMessage.usage` is populated when the parent's turn returns. Long turns show `0/0/$0.00` throughout; tokens populate cleanly at turn completion.

**Claude CLI emits no stderr during normal turns.** All output, including verbose/debug messages, routes to stdout as a JSON stream (`--output-format stream-json`). Verified empirically (teammate-death-diagnostics, 2026-06-09): `subprocess.Popen` with `stderr=PIPE` on `claude --output-format stream-json --verbose` produces 0 stderr bytes. `SdkTeammate._stderr_ring` therefore only populates during error/crash scenarios. Live tests verifying ring population must inject via `_on_stderr_line` directly; they cannot rely on a healthy turn producing stderr output.

**`permission_mode="plan"` does not block Writes in headless SDK sessions (as of claude-agent-sdk 0.1.68 / CLI 2.1.177).** Verified live 2026-06-17: plan mode presents an approval UI in interactive sessions but is a no-op headless — Writes proceed without approval (`test_plan_mode_blocks_file_write_and_cwd_works`). claude-crew compensates client-side via the `_on_pre_tool_use` deny-hook (`_PLAN_MODE_DENIED_TOOLS` + `_effective_permission_mode == "plan"` check). Gate is reliable regardless of SDK behavior. Re-verify when upgrading `claude-agent-sdk`.

**TNM/hook `tool_use_id` mismatch in SDK 0.1.68.** `TaskNotificationMessage.tool_use_id` and `TaskNotificationMessage.task_id` both differ from the PostSubagentUse hook's `tool_use_id` for the same dispatch (verified by runtime probe 2026-06-17: e.g. `toolu_01ARWn4Z…` vs `toolu_01Bxnup…`). claude-crew uses arrival-order correlation (`_task_notifs_ordered: list`) instead of key-based lookup. Re-verify field correlation when upgrading `claude-agent-sdk`.

---

## Dashboard: Multi-Instance Architecture Note

The Mission Control dashboard (`ui_server.py`) is not single-instance. One instance binds the leader port (7821); others become followers on ephemeral ports and register in `InstanceRegistry`. The leader aggregates every instance: `_build_state` calls `_fetch_remote_state` to pull each follower's `/api/state` and merges their agents + transcripts into one view keyed by `crew_id`.

**Key rule**: any new dashboard endpoint that serves per-instance data must carry `crew_id` and route in the handler — serve locally when `crew_id == self._broker.crew_id`, else proxy to the right follower instance. A single-instance test will pass while the feature is broken for the actual deployment.

---

## Dashboard Shape Graphic (`shape-graphic-redesign`, 2026-06-29)

Unified roomier shape graphic across both Mission Control surfaces: the live in-rail `TopologyGraph` and the `ShapeProposalCard` proposal gate. Pure view-layer change (`dashboard.html`) plus one additive backend field (`ui_server.py`). All routing/XSS invariants preserved.

### Files touched

- `claude_crew/ui/dashboard.html` — CSS, globally-scoped JS substrate, React component changes
- `claude_crew/ui_server.py` — additive `"shape": shape_to_dict(p.shape)` key on `/api/state` shape-proposal entries

### Shared zoom/pan modal substrate

Globally-scoped vanilla JS functions shared by both modal openers:

| Function | Responsibility |
|----------|---------------|
| `fitToHost(hostEl)` | Measures SVG at `scale(1)`, computes largest zoom fitting the host (`w-24`, `h-24`); floor **0.05** (not the old 0.3 — must be low enough for large 8+ node crews); writes `dataset.zoom/tx/ty`; calls `applyTransform`. |
| `applyTransform(hostEl)` | Applies `translate(tx,ty) scale(z)` on `.pan-layer`; updates zoom %-label to `Math.round(z*100)+'%'` (ACTUAL zoom — never hardcoded). |
| `bindPanZoom(hostEl)` | Wheel=zoom (clamp `[0.25, 4]`), mousedown/move/up=pan, `no-anim` class during gesture. `panZoomBoundRef` guard prevents double-bind in React strict mode. |
| `renderInto(hostEl, src, edgeStats)` | Renders mermaid source → wraps SVG in `.pan-layer` → DOMPurify sanitizes → decorates edges via `mapEdgeStatsToPaths` → double-rAF auto-fit (`requestAnimationFrame(() => requestAnimationFrame(() => fitToHost(host)))` — a single rAF reads the host before its flex height settles). |

Two thin openers share this substrate:

| Opener | Mermaid source | Edge decoration |
|--------|---------------|----------------|
| `openTopologyModal()` | Live `mermaidSrc` from `window._topoModalData`; `topology_edge_stats` from the same snapshot | Runtime mode (`direct`/`tee`/`gated`/`tripped`); edge click dispatches `/edge-log` |
| `openProposalModal(proposal)` | `shapeToMermaidUnified(proposal.shape, {proposed:true})` | Declared mode only (no `tripped` — a runtime circuit-breaker concept absent pre-instantiation); no new per-instance fetch |

**Invariant — `.modal-body` must use `.zoom-surface`, never `.topology-host`.** `.topology-host` carries the in-rail height cap (`max-height: 340px`); using it inside the modal body caused the 340px-stuck-in-754px-modal layout bug (body clipped, footer unpinned). `.zoom-surface` uses `flex: 1` to fill the modal body correctly.

**Invariant — double-rAF auto-fit.** A single `requestAnimationFrame` fires before the modal's flex layout completes; the second rAF guarantees the measurement is post-layout. Pattern: `renderInto` fires `requestAnimationFrame(() => requestAnimationFrame(() => fitToHost(host)))`.

### `shapeToMermaidUnified(shape, {proposed})` client helper

Produces a `graph TD` mermaid source string using the same `.nodecard` foreignObject labels as the live `TopologyGraph`. Gated edges are expanded through `lead` (sender→lead→receiver), matching the live `displayEdges` gated-bridge expansion. `proposed:true` adds `.nodecard.proposed` on each node card.

**`.nodecard.proposed` CSS variant**: `border-style: dashed; border-color: var(--accent-line); background: var(--bg-1)`. Dot opacity lowered to 0.6 to distinguish from live nodes. The `class` attribute survives mermaid's foreignObject sanitization (DOMPurify `ADD_ATTR` list already included `class`).

**XSS invariant**: `shapeToMermaidUnified` feeds `renderInto`, which uses `mermaid.initialize({securityLevel:'strict'})` + the existing DOMPurify sanitize config unchanged. No new renderer introduced, no sanitize config relaxed. AT-15 and `tests/dashboard/test_dashboard_artifact_xss.py` are non-regression guards.

### `/api/state` additive `shape` key on shape-proposal entries

`ui_server.py` serializes `"shape": shape_to_dict(p.shape)` in the `shape_proposals` list comprehension inside `_build_local_instance` (line 454), alongside the retained `"mermaid"` key. The existing `mermaid` key is kept for back-compat — `list_pending_shapes` (`server.py`) reads it and is unaffected. `shape_to_dict` already existed in `shapes.py` as the JSON-ready inverse of `parse_shape`; the payload change is a one-line import + call.

**Multi-instance LEADER invariant**: `openProposalModal` reads `proposal.shape` from the already-aggregated `/api/state` response; it introduces NO new per-instance endpoint. The multi-instance rule ("any new lazy-fetch endpoint must carry `crew_id` and proxy") is not triggered because no new endpoint is added.

### In-rail topology host changes

- **`.topo-head`** control row above the topology host: title span, subtitle span, `.ctrls` with `−`/`fit`/z-label/`+`/expand buttons.
- **Roomier host**: inline `height` style set to `clamp(220, 240 + 18·max(0, agents−3), 340)px`. A 3-agent crew → 240px (unchanged from before); 6-agent crew → 294px; 8+ agents → 340px cap.
- **`window._topoModalData`** updated after each render with `{mermaidSrc, edgeStats, subtitle, crewId, branch}` so `openTopologyModal` always reads fresh data.
- **SVG wrapper change**: SVG is now wrapped in a `.pan-layer` div (not appended directly), enabling CSS transform-based pan/zoom. The edge-decoration `useEffect` still operates on the same `svgEl` regardless of the wrapper — no behavior change.

### Responsive `.dash-grid` class

Replaces the hardcoded `gridTemplateColumns: "320px minmax(0, 1fr)"` JSX inline style in `MissionControlLayout` with a `className="dash-grid"` + CSS class:

```css
.dash-grid {
  flex: 1; display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  min-height: 0;
}
@media (max-width: 1024px) {
  .dash-grid { grid-template-columns: clamp(220px, 22vw, 260px) minmax(0, 1fr); }
  .dash-grid .nodecard { min-width: 72px; }
  .dash-grid .roster-row { /* tightened padding */ }
}
```

Two-pane layout preserved at all supported widths — no drawer, no single-column collapse. The per-agent column grid (`gridTemplateColumns: repeat(${agents.length}, minmax(220px, 1fr))` inside the agent columns panel) is a distinct, unrelated CSS property and is untouched.

**AT-18 structural guard**: the deletion-detector greps for the FULL inline JSX form `gridTemplateColumns: "320px minmax(0, 1fr)"` (prefix + quotes) — deliberately distinct from the CSS property `grid-template-columns: 320px minmax(0, 1fr)` (the legitimate CSS default track). This prevents false-fails while ensuring any reversion to the inline JSX style is detected.

### Preserved invariants

The following were explicitly non-regressed by the shape-graphic-redesign:

- **`window.mapEdgeStatsToPaths` BC-03 keyed lookup** — untouched; `tests/test_unified_topology_keyed_lookup.py` AT-5/AT-6 stay green.
- **`displayEdges` gated-bridge `_source` back-refs** — `segment._source || segment` pattern preserved.
- **`/edge-log/` fetch + `promote-to-gated` control** — both literal strings covered by AT-8 structural guard in `test_topology_zoom_modal.py`.
- **Edge-decoration `useEffect`** (status border + 1.6s pulse + per-mode stroke) — preserved; the SVG wrapper change (`.pan-layer`) does not affect the decoration, which operates on the same `svgEl`.

---

## Test Conventions

- `conftest.py` auto-sets `CLAUDE_CREW_TEAMMATE_MODE=stub` and `CLAUDE_CREW_TRANSCRIPT_DISABLED=1`.
- Live SDK tests (`test_live_sdk.py`, `test_live_subagents.py`, `test_live_stderr.py`, `test_user_loader_live.py`) are skipped unless `CLAUDE_CREW_LIVE_TESTS=1`.
- `asyncio.get_running_loop()`, never `asyncio.get_event_loop()` inside coroutines.
- Bound unbounded async-iterator drains with `asyncio.wait_for(..., timeout=T)`.
- HOME-monkeypatch tests must copy `~/.claude/.credentials.json` and `~/.claude.json` into the tmp HOME.
- LLM-relayed sentinels: ≤12 hex characters (preferred) to avoid truncation/paraphrasing across the LLM relay boundary.
- Full `uv run pytest` (not `-k` subset) when changing widely-consumed behavior.
- **Tests that spawn a `claude_crew.cli` subprocess must allocate a free TCP port** using the `_get_free_port()` pattern (bind socket to port 0, read assigned ephemeral port, close; pass result as `CLAUDE_CREW_UI_PORT=<port>` in the subprocess environment). Do NOT rely on the default port 7821 — a live claude-crew MCP session holds it, preventing the subprocess from binding `UIServer` and completing registration. Canonical helper: `_get_free_port()` in `tests/test_shutdown_signals.py`.
