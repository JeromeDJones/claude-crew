# Feature: MCP Server Skeleton + Tool Surface

**Status**: Planning
**Created**: 2026-04-25
**Maps to**: PRODUCT-VISION.md Feature Pipeline #1, Capability #1, Success Criterion #1

---

## Phase 1: Research & Requirements

### Problem Statement

claude-crew's foundation is an MCP server that the lead Claude Code session uses to spawn, address, and supervise a crew of teammates. Before any real Agent-SDK teammates exist (Feature #2), we need the bus protocol and the lead's tool surface in place — running, registered, callable, and validated end-to-end with stub teammates.

This feature is the substrate every other MVP feature builds on. Without it:
- Feature #2 has nothing to attach `ClaudeSDKClient` instances to
- Feature #3a/#3b has no surface to expose subagent configs through
- Feature #4 has no message stream to transcribe
- Feature #5 has nothing to validate against

The deliberate sequence is: prove the bus shape with stubs first, then plug in real agents. If the tool surface or message envelopes need to change, we'd rather find that out before we've built the SDK runtime layer on top.

### Success Criteria

- [ ] An MCP server runs over stdio and registers with Claude Code via `claude mcp add`
- [ ] The lead can call `spawn_teammate(role, name)` and receive a teammate id; the teammate is a stub that echoes any messages it receives
- [ ] The lead can call `send_to(teammate_id, payload)` and the message is delivered to the stub teammate
- [ ] The lead can call `get_messages(since=...)` and retrieve all messages the lead is the recipient of, in order, with no duplicates
- [ ] The lead can call `broadcast(payload)` and all teammates receive it
- [ ] The lead can call `list_crew()` and see all currently spawned teammates with their roles
- [ ] The lead can call `kill_teammate(teammate_id)` and the teammate is removed; subsequent sends to that id return a clear error
- [ ] Each message has a structured envelope: `id` (uuid), `seq` (monotonic int per crew, broker-assigned on enqueue), `sender`, `recipient`, `timestamp`, `payload`. Duplicates by `id` are silently deduplicated by the broker.
- [ ] All seven tools have implementation-level tests (the broker's behavior in isolation) and integration-level tests (the tools called through the MCP server)

### Resolved Decisions

- [x] **Process model for stub teammates: in-process asyncio task.** Feature #2's `ClaudeSDKClient` teammates also run in-process, so the seam Feature #2 plugs into is shaped right from day one. Subprocess isolation is a v2+ concern only if real-world crashes prove it's needed.
- [x] **Message ordering: per-recipient FIFO.** Implemented via one `asyncio.Queue` per teammate (and one for the lead). Matches user mental model; free at this scale.
- [x] **`get_messages` cursor: monotonic integer `seq` per crew.** Broker assigns `seq` on enqueue; lead passes `since=<last_seen_seq>` and gets everything `> since`. The envelope's UUID `id` field stays for content-level dedup (e.g., retried sends); `seq` is purely a broker-assigned ordering/cursor primitive. Two distinct concerns, two distinct fields.

### Constraints & Dependencies

- **Python 3.12+**, `mcp[cli]` SDK (already installed in `~/dev/claude-crew/spikes/mcp-pubsub-spike` — same SDK applies here)
- **stdio transport only** for v1 (matches Claude Code's `claude mcp add -- <cmd>` registration model)
- **Local-machine only.** No network transport, no auth, no remote brokers. Trust boundary is the local user.
- **No persistence across MCP server restarts** in this feature. The crew lives for the lifetime of the lead's session. Cross-session persistence is a v2 question.
- **No real Agent SDK runtime yet** — this feature uses stub teammates only. The seam where teammates plug in must be clean enough that Feature #2 can swap stubs for `ClaudeSDKClient` instances without changing the tool surface.
- **Breaking changes**: N/A (greenfield)
- **Performance implications**: The bus is in-process and message volume is low (human-driven lead, a few messages per turn). No throughput concerns for v1.

**Gate**: Questions answered, success criteria measurable, constraints documented, user confirmed.

---

## Phase 2: Design & Specification

### Architecture Overview

One Python process. One `asyncio` event loop. Three logical layers, in order from outside in:

```
┌─────────────────────────────────────────────────────────┐
│  MCP Server (stdio)        — registered with Claude     │
│   ├ tool: spawn_teammate                                │
│   ├ tool: send_to                                       │
│   ├ tool: broadcast                                     │
│   ├ tool: get_messages                                  │
│   ├ tool: list_crew                                     │
│   └ tool: kill_teammate                                 │
│                                                         │
│  Broker                    — single source of truth     │
│   ├ teammate registry      (id → Teammate)              │
│   ├ message log            (per crew, append-only)      │
│   ├ inbox queues           (one per participant)        │
│   ├ seq counter            (monotonic, per crew)        │
│   └ id dedup set                                        │
│                                                         │
│  Teammates                 — Teammate ABC               │
│   └ StubTeammate           (v1; echoes to sender)       │
│        SdkTeammate         (Feature #2 plugs in here)   │
└─────────────────────────────────────────────────────────┘
```

The MCP server is thin — every tool delegates to the broker. The broker is where contracts and state live. Teammates are pluggable via a small ABC. There is exactly one broker per process; one process per crew (multi-crew runs as multiple processes, see Capability #3).

### Message Envelope

```python
@dataclass(frozen=True)
class Envelope:
    id: str          # UUIDv4 — content-level dedup; sender provides on retry
    seq: int         # monotonic per crew, broker-assigned on enqueue (>= 1)
    sender: str      # "lead" or teammate_id
    recipient: str   # "lead" or teammate_id
    timestamp: float # unix epoch seconds (broker-assigned)
    payload: Any     # JSON-serializable; opaque to the broker
```

- `id` is **sender-supplied for tool calls**, broker-supplied for stub responses. The broker dedups on `id`: if a message with that id was already enqueued, it's silently dropped.
- `seq` is broker-assigned, monotonic, contiguous per crew. Cursor reads use this. **`seq` is never reused, even after `kill_teammate`**.
- `timestamp` is informational, never used for ordering.
- `payload` is whatever the lead wants to put in. JSON-serializable. Broker does not interpret it.

### Broker Contract

Internal Python API the MCP tool handlers call. This is the seam — the MCP server can stay constant while the teammate implementation evolves.

```python
class Broker:
    async def spawn_teammate(self, role: str, name: str | None,
                              factory: TeammateFactory) -> str: ...
        # Allocates teammate_id, creates inbox queue, instantiates Teammate
        # via factory, registers it, returns teammate_id.

    async def send(self, env: Envelope) -> None: ...
        # Validates recipient exists, dedups by env.id, assigns seq,
        # appends to log, enqueues to recipient's inbox.

    async def broadcast(self, sender: str, payload: Any,
                         id: str) -> list[str]: ...
        # Fans out one Envelope per current teammate (recipient = teammate_id).
        # Returns list of resulting message ids. Does NOT loop back to sender.

    def get_messages(self, recipient: str,
                      since_seq: int = 0,
                      limit: int | None = None) -> list[Envelope]: ...
        # Returns envelopes from the log where
        # recipient == <recipient> AND seq > since_seq, ordered by seq.
        # Non-blocking. Pure read — does not consume from the inbox queue.

    def list_crew(self) -> list[TeammateInfo]: ...
        # Snapshot of registered teammates: id, name, role, spawned_at, alive.

    async def kill_teammate(self, teammate_id: str) -> None: ...
        # Marks teammate dead (subsequent send_to → KeyError),
        # awaits teammate.shutdown(), drops from registry.
        # Already-logged messages remain readable via get_messages.
```

**Concurrency model.** Single event loop. State mutations (`registry`, `seq`, `id_seen`, `log`) happen only in coroutines, never threads. No locks needed — `asyncio` cooperative scheduling is sufficient. Inbox is `asyncio.Queue` per participant.

**Lead's inbox.** The lead is a participant with `id == "lead"`. Its inbox queue is created at broker startup. `get_messages(recipient="lead", ...)` is what the lead's MCP tool calls map to.

**Where does it live in code.** The broker holds two parallel structures:
- `log: list[Envelope]` — the full crew transcript, append-only, indexed by `seq - 1`. This is what `get_messages` reads from.
- `inboxes: dict[str, asyncio.Queue[Envelope]]` — what teammates `await` on. Stub/SDK teammates pull from their own inbox.

The log is the durable view; inboxes are the live delivery channel. Both are in-memory only in v1 (no persistence — that's a v2 question).

### Teammate ABC — The Feature #2 Seam

```python
class Teammate(ABC):
    id: str
    name: str
    role: str

    @abstractmethod
    async def start(self, broker: Broker, inbox: asyncio.Queue[Envelope]) -> None:
        """Begin running. Reads from inbox, sends responses via broker.send().
        Must run as a background asyncio.Task; returns the task handle."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Stop processing, drain in-flight work, release resources."""

class StubTeammate(Teammate):
    # On each inbound message: send back Envelope with payload={"echo": original_payload}
    # to the original sender. That's it.
```

Feature #2 implements `SdkTeammate(Teammate)` wrapping a `ClaudeSDKClient`. **Same ABC, same broker, same MCP server.** The only thing that changes is `factory=` passed to `spawn_teammate`.

### Tool Surface (MCP-facing)

| Tool | Args | Returns | Errors |
|---|---|---|---|
| `spawn_teammate` | `role: str, name: str \| None = None` | `{teammate_id, name, role}` | — |
| `send_to` | `teammate_id: str, payload: Any, id: str \| None = None` | `{message_id, seq}` | `unknown_teammate`, `teammate_dead` |
| `broadcast` | `payload: Any, id: str \| None = None` | `{message_ids: [...], seq_range: [lo, hi]}` | — (empty crew → empty result, success) |
| `get_messages` | `since_seq: int = 0, limit: int \| None = 100` | `{messages: [Envelope, ...], next_seq: int}` | — |
| `list_crew` | — | `{teammates: [{id, name, role, spawned_at, alive}, ...]}` | — |
| `kill_teammate` | `teammate_id: str` | `{ok: true}` | `unknown_teammate` |

**Error model.** Tool returns `{error: "<code>", message: "<human readable>"}` on the structured error cases above. MCP-level exceptions (malformed args) bubble to the SDK and surface to the model as a tool error.

**Optional `id` on send_to / broadcast.** If omitted, broker generates a UUID. Provided ids enable retry-safe sends — if the lead times out and retries with the same id, the broker dedups.

### Stub Teammate Behavior

`StubTeammate` runs a loop:
```python
async def start(self, broker, inbox):
    self._task = asyncio.create_task(self._run(broker, inbox))

async def _run(self, broker, inbox):
    while True:
        env = await inbox.get()
        if env is _SHUTDOWN_SENTINEL: return
        response = Envelope(
            id=str(uuid4()), seq=0,  # broker overwrites seq
            sender=self.id, recipient=env.sender,
            timestamp=time.time(),
            payload={"echo": env.payload, "from": self.role},
        )
        await broker.send(response)
```

That's the whole stub. Round-trip provable: lead → `send_to` → broker → stub inbox → stub responds → broker → lead's inbox → lead's `get_messages` returns the echo.

### Module Layout

```
claude_crew/
├── __init__.py
├── envelope.py        # Envelope dataclass, validation
├── broker.py          # Broker, registry, log, inbox queues
├── teammate.py        # Teammate ABC, StubTeammate
├── server.py          # MCP server: tool definitions, broker wiring
└── cli.py             # console_scripts entry: `claude-crew`

tests/
├── test_envelope.py        # implementation-level
├── test_broker.py          # implementation-level (broker in isolation)
├── test_stub_teammate.py   # implementation-level
└── test_server.py          # integration-level (tools called via MCP)
```

Console script `claude-crew` launches `server.py:main()`. Registration:
`claude mcp add claude-crew -- claude-crew`

### Test Strategy (per `validate-before-change.md`)

| Layer | Coverage | Tool |
|---|---|---|
| Implementation | Broker: send/broadcast/get_messages/list_crew/kill_teammate, dedup, seq monotonicity, FIFO per recipient. Stub: echoes correctly. Envelope: serialization, validation. | `pytest`, `pytest-asyncio` |
| Integration | All six MCP tools driven through the actual MCP server (in-process MCP client harness from the SDK), happy + sad paths each. | `pytest` + `mcp` SDK in-memory client |

**Sad paths covered:** unknown teammate id, killed teammate, duplicate `id` (verifies silent dedup), `since_seq` past the end (returns `[]`).

### Open Items I Want Eyes On

These are choices I made on your behalf — flag any you want to revisit before Phase 3.

1. **Lead does not receive its own broadcasts.** Sender is excluded from fan-out. Alternative: include sender, lead reads its own broadcast back. I picked exclude because the lead already knows what it broadcast and double-delivery feels noisy.
2. **`kill_teammate` is synchronous (awaits shutdown).** Alternative: fire-and-forget. Sync is slower but means "kill returned" → "the teammate is gone." Simpler mental model; matters more once Feature #2 has SDK clients to tear down cleanly.
3. **Roles are free strings, no validation.** A typo in `role="planr"` won't be caught. We'll get role validation when default subagent packs (Feature #3a) define a role registry. Until then, it's a string.
4. **`get_messages` is non-blocking.** Long-poll `wait_for_messages` is explicitly deferred per the vision doc. Lead polls. If polling latency hurts in real use, we promote `wait_for_messages` from deferred to MVP.
5. **Stub echo includes `"from": self.role`.** Trivial but it makes integration tests more diagnostic when something goes wrong. Pure debugging affordance.
6. **No teammate authentication.** Anyone with a teammate id can send as that teammate. Trust boundary is the local user; no MCP-level identity beyond what Claude Code provides. Documented as a constraint.

### What This Design Does Not Do

Listed so future-me (or a reviewer) doesn't think they're omissions:

- **No persistence.** Crew dies with the process.
- **No backpressure.** Inbox queues are unbounded. If a teammate stops draining, the queue grows. Acceptable at v1's volumes; revisit if it bites.
- **No per-tool rate limiting.**
- **No cross-crew anything.** Each crew is its own process.
- **No live UI.** JSONL transcript is Feature #4; this feature lays the log structure that #4 reads from.

---

## Phase 3: Task Breakdown

*To be filled during SDD Phase 3.*

---

## Phase 4: Implementation

*To be filled during SDD Phase 4.*

---

## Phase 5: Completion

### Implementation summary

Module layout matches the design exactly:

- `claude_crew/envelope.py` — `Envelope` frozen dataclass + `new_message_id()`
- `claude_crew/teammate.py` — `Teammate` ABC + `StubTeammate` (echoes payload back to sender with role tag)
- `claude_crew/broker.py` — `Broker` with monotonic `seq`, id-based dedup, per-recipient FIFO via `asyncio.Queue`, append-only log
- `claude_crew/server.py` — FastMCP server with all 6 tools delegating to broker
- `claude_crew/cli.py` — `claude-crew` console entry

### Test results

`uv run pytest`: **49 passed** in <1s.

| Layer | File | Count |
|---|---|---|
| Implementation | `tests/test_envelope.py` | 8 |
| Implementation | `tests/test_broker.py` | 23 |
| Implementation | `tests/test_stub_teammate.py` | 4 |
| Integration (in-memory MCP harness) | `tests/test_server.py` | 14 |

Sad paths covered: unknown teammate id, killed teammate, duplicate `id`, `since_seq` past end.

### Manual smoke test (run in Claude Code)

1. **Register the server:**
   ```bash
   cd ~/dev/claude-crew
   claude mcp add claude-crew -- uv --directory ~/dev/claude-crew run claude-crew
   ```
2. **In a Claude Code session, run:** "Spawn a stub teammate with role 'parrot', send it the message {hello: 'world'}, then read your messages."
3. **Expected:** lead's `get_messages` returns one envelope where `payload == {"echo": {"hello": "world"}, "from": "parrot"}`.

### Open follow-ups (not blockers for shipping)

- Backpressure: inbox queues are unbounded. Document but do not fix unless real-use volumes hurt.
- Persistence: deferred to v2.
- Long-poll `wait_for_messages`: deferred per vision doc.
