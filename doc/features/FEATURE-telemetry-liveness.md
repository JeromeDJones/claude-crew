# Feature: Telemetry-Based Teammate Liveness

**Status**: In Progress (Phase 1)
**Created**: 2026-04-26
**Vision row**: PRODUCT-VISION.md → Post-MVP Substrate → #6
**Substrate origin**: `doc/research/feature-5-substrate-findings.md` (S1 + S2)
**Tactical predecessor**: commit `b9bc611` (raised `TURN_TIMEOUT_SECONDS` 120s → 600s)

---

## Phase 1: Research & Requirements

### Problem Statement

Today, `claude_crew/sdk_teammate.py` enforces a hard wall-clock timeout (`TURN_TIMEOUT_SECONDS = 600.0`) on the SDK's `receive_response()` drain. When the wall fires:

1. The teammate emits an `invalid_response` error envelope to the sender.
2. **The underlying SDK subprocess is NOT cancelled** — `client.interrupt()` is never called. The subprocess keeps generating.
3. The next turn for that teammate may receive the *previous* turn's late-arriving response, delivered as the answer to the *new* prompt (S2 stale-response).

The 600s tactical raise (`b9bc611`) reduced false fires significantly but didn't eliminate them — two more fires occurred during MMM-35 Phase 4 (long endpoint test generation; final sentinel comprehensive review). And as long as the wall exists, S2 stays latent: any teammate doing genuinely long work (>10min) re-exposes the timeout-fire-but-keep-generating race.

The right fix is **structural**: stop guessing how long the model "should" take, and instead observe whether it is actually doing anything. If the SDK is yielding events, the teammate is alive and working — no error, no envelope, no panic. If it stops yielding for an operator-decided window, the lead can ask `get_teammate_status` and decide what to do (wait, ping, kill).

The hard timeout moves out of the substrate (where it's a one-size-fits-all constant buried in module scope) and into lead policy (where the operator can size it to the task). The substrate's job becomes telling the lead the truth: *when did this teammate last produce activity?*

This also addresses a Feature #5 tripwire-#4 confound: today, "broker fired invalid_response" doesn't mean the teammate is stuck — it means the broker got impatient. After this feature, the broker no longer guesses; tripwire #4 ("teammate produced no transcript line for >X min AND no SDK timeout fired") becomes a clean signal again because *no SDK timeout fires at all* under normal operation.

### Why now

- MMM-35 (Feature #5 real-task run) was paced by S1 fires and the operator overhead they created (status-pings, "IGNORE PRIOR" workarounds). Quantified in the retro: 7 substrate timeouts across the run, all false positives, all recovered manually.
- The next real-task run (likely MMM-4b) will hit the same pattern unless the structural fix lands first.
- This is the prerequisite the #5 retro identified for the *next* claude-crew real-task validation. Build order is intentional: substrate fix → real-task run → confirm fix held.

### Success Criteria

Each criterion is testable. SC-7 explicitly captures the "S2 dies by construction" claim so we can prove it, not just believe it. Sentinel review (Phase 1, sentinel-p1) folded into the SC list — six [FIX-NOW] items below carry "(sentinel)" tags.

- [ ] **SC-1 (no per-turn wall):** A turn that takes longer than the previous `TURN_TIMEOUT_SECONDS` value (600s) completes successfully and delivers a normal result envelope, with no `invalid_response` error envelope sent. Verified by an integration test that drives a fake SDK client through a controlled, slow-but-active stream.
- [ ] **SC-2 (`get_teammate_status` MCP tool):** The MCP server exposes `get_teammate_status(teammate_id)` returning a structured payload `{teammate_id, role, alive, last_activity_at_wallclock, current_turn_started_at_wallclock, idle_seconds}`. Returns a structured error for unknown ids (matches existing `unknown_teammate` shape). Returns `alive: false` plus the death record fields (see SC-5) for tombstoned teammates. **(sentinel: dual-clock — wallclock for human-readable display, internal monotonic for `idle_seconds` math.)**
- [ ] **SC-3 (activity stamping per stream event):** **Every** message yielded by `client.receive_response()` — regardless of type (`AssistantMessage`, `RateLimitEvent`, `TaskNotificationMessage`, `SystemMessage`, `ResultMessage`, anything new) — updates the teammate's `last_activity_at` (both monotonic and wallclock). Verified by asserting that during a multi-event drain, the timestamp advances on each yielded event including non-text events. **(sentinel: explicit "all event types count," not just `AssistantMessage`.)**
- [ ] **SC-4 (turn lifecycle stamps):** `current_turn_started_at` is set when the teammate dequeues an inbound envelope and begins driving the SDK; cleared (set to null) when the turn ends — successful result, error envelope, **or** when the death-detection path emits `lifecycle: died` for that teammate. **(sentinel: name the responsibility — clear-on-death is owned by the death-detection path, since the worker can't clear its own state if it died.)**
- [ ] **SC-5 (death detection):** When the underlying SDK subprocess dies (process exits / receives SIGKILL / parent closes / `ProcessError` raised by transport), the broker emits a `lifecycle: died` transcript line within a bounded interval (target ≤30s for the *idle* case; immediate for the *in-turn* case). The death record carries `{teammate_id, exit_code (if available), idle_seconds_at_death, last_activity_at_wallclock}`. Subsequent `get_teammate_status` calls return `alive: false`. **(sentinel: observability of WHY, not just THAT — exit_code + last activity stamp on the death record.)** **(spike-resolved: SDK exposes `_transport._process.pid` and `_transport._process.returncode`. Death surfaces automatically on read/write via `ProcessError` / `CLIConnectionError`. Idle-case detection requires a periodic poll of `returncode` since no read is in flight when the teammate is between turns.)**
- [ ] **SC-5b (death-detection cleanup contract):** **(sentinel: new SC.)** When `lifecycle: died` is emitted for teammate T:
  1. Any in-flight turn for T sends an error envelope to the original requester with code `teammate_dead` and a brief diagnostic (exit_code if known).
  2. T's inbox queue is drained and discarded — no envelopes are silently lost without operator-visible signal (each pending envelope's sender, if any, gets a `teammate_dead` bounce).
  3. T transitions to **tombstoned** in the registry (per Q1→b): subsequent `send_to(T, ...)` returns `teammate_dead`, not `unknown_teammate`. Tombstone persists for the broker lifetime.
  4. T's transcript writer is **not** closed (the broker's transcript is crew-wide, per Feature #4 design; only the broker's `shutdown` closes it).
- [ ] **SC-6 (no operator surprise on shutdown):** Normal `kill_teammate` and broker `shutdown_all` paths continue to emit `lifecycle: kill` / `lifecycle: shutdown`, NOT `lifecycle: died`. Death detection distinguishes "process exited unexpectedly" from "we asked it to stop." **(sentinel-clarified: this also holds when the SDK context-manager exits via a raised exception in normal-shutdown ordering — the death-detection path must not race the kill path.)**
- [ ] **SC-7 (S2 cannot occur):** Verified by a deterministic test using a controlled fake SDK client (not a timing repro). Setup: turn N's drain yields no events for an arbitrarily long simulated duration without firing any timeout (proves no wall exists). Then turn N completes (yields its final event), and turn N+1 is sent. Assert: turn N+1's response envelope contains turn N+1's content, never turn N's content. **(sentinel: the property is enforced at the code level — there is no code path that bleeds turn N's drain output into turn N+1's response handler.)**
- [ ] **SC-8 (operator policy lives in lead):** No `TURN_TIMEOUT_SECONDS`-style wall remains in `sdk_teammate.py`. The 1hr backstop (per Q2→b, see SC-11) is the only timer in the file, configurable via env var, defaulting high enough that no real reply hits it.
- [ ] **SC-9 (transcript carries new lifecycle event):** The `kind: lifecycle` discriminator in the JSONL transcript schema gets one new event type: `died`. **No schema version bump** (per Q4-resolved: additive). All existing transcript consumers (`tail -f`, future replay tooling) keep working unchanged.
- [ ] **SC-10 (no regression in existing teammate behavior):** Existing tests in `tests/test_sdk_teammate.py`, `tests/test_broker.py`, `tests/test_server.py`, `tests/test_transcript.py` continue to pass without modification beyond what the feature requires (e.g., updated mocks for the activity-stamping path).
- [ ] **SC-11 (backstop semantics — sentinel: new SC):** When the 1hr backstop fires for an in-flight turn:
  1. The teammate calls `await client.interrupt()` (public SDK API, spike-confirmed) **before** sending any error envelope. This is the load-bearing step — without it, S2 reappears at the 1hr boundary.
  2. The teammate drains any remaining buffered events (with a short bounded grace, ≤5s) so the interrupt acknowledgment is observed.
  3. The teammate sends an `invalid_response` error envelope to the requester with code `backstop_timeout` and a message naming the configured backstop value.
  4. The teammate continues to the next turn (does NOT die — the backstop is a turn-level guard, not a teammate-level guard).
- [ ] **SC-12 (PID probe degrade-open — sentinel: new SC):** If the periodic liveness poll itself raises (`OSError`, `ProcessLookupError`, `PermissionError`, `AttributeError` from SDK private-attribute drift), the failure is logged at WARNING with the teammate id and exception, and the teammate is treated as **alive** for that polling cycle. The probe must NOT mass-tombstone the crew on a single transient probe failure. Verified by injecting `OSError` into the probe and asserting `alive: true` afterwards.

### Questions

Q1, Q2, Q4, Q5 are **resolved** below (sentinel concurred with leans, with one tightening). Q6 is **new** (sentinel-flagged). Q3 stays accepted-out-of-scope.

- [x] **Q1 (dead-teammate `send_to` semantics) — RESOLVED: (b) tombstone with `teammate_dead` error code.** Sentinel concurred. Implication: SC-5b makes the tombstone contract concrete; broker reuses `TeammateAlreadyDeadError` (already reserved in `broker.py:27`).
- [x] **Q2 (1hr backstop yes/no) — RESOLVED: (b) with default 3600s, env override `CLAUDE_CREW_TURN_BACKSTOP_SECONDS`.** Sentinel concurred *conditional on Q6/SC-11* — without `client.interrupt()` on backstop fire, (a) would be safer because (b) re-introduces S1/S2 at the 1hr boundary. SC-11 pins the interrupt requirement.
- [x] **Q3 (`lifecycle: stalled` for wedged-but-alive subprocess) — ACCEPTED OUT OF SCOPE.** Lead's lean stands: substrate exposes `idle_seconds` via `get_teammate_status`; lead policy decides what to do. Sentinel concurred — adding a stall alarm would re-bake policy into the substrate.
- [x] **Q4 (transcript schema version bump for `died`) — RESOLVED: no bump, additive change.** Sentinel concurred; SC-9 wording cleaned up to drop the conditional.
- [x] **Q5 (`get_teammate_status` for stub teammates) — RESOLVED: yes, stamp in base `Teammate` class.** Stubs always report `alive: true`; SC-12's PID probe is a no-op for non-SDK teammates.
- [x] **Q6 (NEW, sentinel-flagged: does the backstop call `client.interrupt()` before erroring?) — RESOLVED: yes, see SC-11.** Spike confirmed `client.interrupt()` is public SDK API designed for this. Without it, the backstop is just a slower S1/S2.

### Constraints & Dependencies

- **Requires:** existing `SdkTeammate`, `Broker`, `TranscriptSink`, `FastMCP` server. No new external dependencies.
- **SDK subprocess access (spike-resolved):**
  - PID + returncode: `client._transport._process.pid` and `client._transport._process.returncode`. Private attribute path (anyio Process), but stable across SDK versions in scope. SDK version pinning in `pyproject.toml` is the upgrade-safety lever.
  - Public liveness method: none. Polling `returncode` (with a short interval) is the path.
  - In-turn death surfaces automatically: transport raises `ProcessError` on EOF in `read_messages` or `CLIConnectionError` on write to dead subprocess. Existing `_handle_one_turn` catch-all already routes these to `_send_error_envelope` — Phase 2 enriches the route to also emit `lifecycle: died` and tombstone.
  - Idle-case death (no in-flight turn): requires a periodic background poll of `returncode`. Poll interval default 5s — well under SC-5's 30s ceiling, with margin.
  - Public interrupt: `await client.interrupt()` exists and is the right backstop primitive (SC-11).
- **Breaking changes:** None for lead-side MCP tool surface (purely additive: new tool, new lifecycle event type, new error code). Internal: removes `TURN_TIMEOUT_SECONDS` constant from `sdk_teammate.py` — `grep -r "TURN_TIMEOUT_SECONDS"` shows the constant is only referenced inside `sdk_teammate.py` itself (Phase 2 to verify final).
- **Performance implications:** Per-event activity stamping is one `time.monotonic()` + one `time.time()` call per yielded SDK message. Per-teammate periodic liveness poll is one private-attribute read every 5s. Both negligible.
- **Concurrency:** Single asyncio event loop. The liveness poll task and the per-turn drain run as separate tasks on the same loop — Phase 2 design must specify which task owns the death-detection write to the registry / transcript (single-writer rule).
- **Cross-feature:** Touches Feature #4 (transcript schema — adds `died` event type). Touches Feature #1 (MCP tool surface — adds `get_teammate_status`, adds `teammate_dead` error code).
- **Env var convention:** `CLAUDE_CREW_TURN_BACKSTOP_SECONDS` (default 3600). Joins existing `CLAUDE_CREW_TRANSCRIPT_DIR` / `CLAUDE_CREW_TRANSCRIPT_DISABLED` (Feature #4).

### Pre-existing rule check

claude-crew has no `.claude/rules/` directory yet (verified). No project-level architecture rules to honor or violate. The implicit rules from MMM-35 / Feature #5 retro that apply here:
- Tests at implementation layer + one layer above, happy + sad path at both (`~/.claude/rules/validate-before-change.md`)
- Live SDK tests are valuable but expensive — gate behind a marker, default off
- No backwards-compat shims unless real consumers exist (`SOUL.md` / coding-standards)

---

**Gate**:
- ✅ Sentinel review of acceptance criteria — done (sentinel-p1, Opus 4.7 medium). 8 [FIX-NOW] items folded in: SC-2/SC-3 dual clock, SC-3 all-events-count, SC-4 death-path attribution, SC-5 observability fields, new SC-5b (cleanup contract), new SC-11 (backstop+interrupt), new SC-12 (probe degrade-open), Q6 raised + resolved.
- ✅ SDK subprocess access spike — done (Haiku Explore). Private `_transport._process.pid`/`returncode` viable; public `client.interrupt()` confirmed; idle-case death needs 5s polling.
- ✅ Open Questions Q1–Q6 resolved.
- ⏳ Jerome confirms

---

## Phase 2: Design & Specification

### Architecture Overview

Three concurrent asyncio tasks per `SdkTeammate`, all on one event loop:

```
┌──────────────────────────────────────────────────────┐
│ SdkTeammate (one instance per spawned teammate)      │
│                                                      │
│  ┌────────────────┐   ┌──────────────────┐          │
│  │  worker task   │   │   liveness poll  │          │
│  │  (existing)    │   │   task (NEW)     │          │
│  │                │   │                  │          │
│  │  inbox.get()   │   │  every 5s:       │          │
│  │  ↓             │   │  read returncode │          │
│  │  query() +     │   │  if dead → call  │          │
│  │  drain stream  │   │  broker death-   │          │
│  │  ↑ stamps      │   │  handler         │          │
│  │  activity per  │   │                  │          │
│  │  event         │   │  catches ALL     │          │
│  │                │   │  exceptions →    │          │
│  │  on SDK death: │   │  log + treat     │          │
│  │  set flag,     │   │  alive (SC-12)   │          │
│  │  exit cleanly  │   │                  │          │
│  └────────────────┘   └──────────────────┘          │
│         │                       │                    │
│         └───────────┬───────────┘                    │
│                     ▼                                │
│              broker._handle_teammate_death()         │
│                  (single writer)                     │
└──────────────────────────────────────────────────────┘
```

**Single-writer rule** (co-architect pushback #1, resolved): only the **liveness poll task** writes the `lifecycle: died` event and tombstones the teammate. The worker task, if it observes SDK death mid-turn (via `ProcessError` / `CLIConnectionError`), does NOT tombstone — it sets `_death_suspected` and exits cleanly. The poll task's next tick (≤5s) confirms via `returncode` and runs the death-handler exactly once. Result: no race, no duplicate `lifecycle: died`, no torn tombstone state. Trade-off: up to 5s latency on in-turn death (well under SC-5's 30s ceiling).

**Activity stamping** lives in the base `Teammate` class (per Q5). `_stamp_activity()` is called by `SdkTeammate` on every yielded SDK event and by `StubTeammate` on every inbox dequeue. The MCP tool reads it for both teammate types uniformly.

**Backstop sequence** (co-architect pushback #2, resolved by D4 below): interrupt-first with bounded grace on the interrupt itself, then bounded post-interrupt drain, then error envelope. Worst case (interrupt hangs too) is bounded by `INTERRUPT_GRACE_SECONDS` (30s) — the teammate then exits the failed turn without tombstoning, and the poll task picks up any genuine death on its next tick.

### Data / API Contracts

**MCP tool: `get_teammate_status`** (additive to server.py)

```python
@mcp.tool()
async def get_teammate_status(teammate_id: str) -> dict[str, Any]:
    """Return live or post-mortem status for a teammate.

    Args:
        teammate_id: id from spawn_teammate.

    Returns the same payload shape whether the teammate is alive or
    tombstoned, with death-record fields populated only when alive=False.
    """
```

Success payload (alive teammate):
```python
{
    "teammate_id": "t-...",
    "name": "co-architect-f6",
    "role": "co-architect",
    "alive": True,
    "spawned_at": 1777256000.0,             # wallclock (mirrors list_crew)
    "last_activity_at_wallclock": 1777256123.4,
    "current_turn_started_at_wallclock": 1777256120.0,  # null if idle
    "idle_seconds": 3.4,                     # monotonic-derived; freezes at death
    "died_at_wallclock": None,
    "exit_code": None,
    "last_activity_at_wallclock_at_death": None,
}
```

Tombstoned teammate: same shape, `alive=False`, death-record fields populated, `current_turn_started_at_wallclock=None`, `idle_seconds` frozen at the value at time of death.

Unknown id: `{"error": "unknown_teammate", "message": "no teammate with id 't-...'"}` — matches existing `server.py:25` pattern.

**Updated `TeammateInfo` dataclass** (`broker.py:37`):

```python
@dataclass(frozen=True)
class TeammateInfo:
    id: str
    name: str
    role: str
    spawned_at: float
    alive: bool
    # NEW (death-record fields, all None for alive teammates)
    died_at_wallclock: float | None = None
    exit_code: int | None = None
    last_activity_at_wallclock_at_death: float | None = None
```

Replace via `dataclasses.replace` on death (frozen-safe).

**Updated base `Teammate` class** (`teammate.py`):

```python
class Teammate(ABC):
    id: str
    name: str
    role: str

    # NEW: activity telemetry, stamped by subclasses
    _last_activity_monotonic: float       # init: monotonic at construction
    _last_activity_wallclock: float       # init: time.time() at construction
    _current_turn_started_at_wallclock: float | None  # init: None

    def _stamp_activity(self) -> None:
        self._last_activity_monotonic = time.monotonic()
        self._last_activity_wallclock = time.time()

    def _begin_turn(self) -> None:
        self._current_turn_started_at_wallclock = time.time()
        self._stamp_activity()

    def _end_turn(self) -> None:
        self._current_turn_started_at_wallclock = None

    def status_snapshot(self) -> dict[str, Any]:
        """Read-only snapshot for get_teammate_status. Computed from monotonic+wallclock."""
        return {
            "last_activity_at_wallclock": self._last_activity_wallclock,
            "current_turn_started_at_wallclock": self._current_turn_started_at_wallclock,
            "idle_seconds": time.monotonic() - self._last_activity_monotonic,
        }
```

**New `Broker.get_teammate_status(teammate_id)` method** — combines `TeammateInfo` (lifecycle fields) + `teammate.status_snapshot()` (activity fields). Server calls this.

**New `Broker._handle_teammate_death(teammate_id, exit_code)` method** — the single-writer death path:

```python
async def _handle_teammate_death(
    self, teammate_id: str, exit_code: int | None
) -> None:
    """Single-writer death handler. Idempotent — no-op if already tombstoned."""
    info = self._info.get(teammate_id)
    if info is None or not info.alive:
        return  # already tombstoned or unknown
    teammate = self._teammates.pop(teammate_id, None)  # remove from active set

    # Snapshot teammate state at death for observability
    snap = teammate.status_snapshot() if teammate is not None else {}
    last_activity = snap.get("last_activity_at_wallclock")
    idle_at_death = snap.get("idle_seconds", 0.0)

    # Tombstone in registry (replace frozen dataclass)
    self._info[teammate_id] = dataclasses.replace(
        info,
        alive=False,
        died_at_wallclock=time.time(),
        exit_code=exit_code,
        last_activity_at_wallclock_at_death=last_activity,
    )

    # Drain inbox: bounce each pending envelope back to its sender
    inbox = self._inboxes.pop(teammate_id, None)
    while inbox is not None and not inbox.empty():
        try:
            pending = inbox.get_nowait()
        except asyncio.QueueEmpty:
            break
        if isinstance(pending, Envelope):
            await self._send_dead_bounce(pending, teammate_id, exit_code)

    # Emit lifecycle: died (single point of emission)
    self._sink.write_lifecycle("died", {
        "teammate_id": teammate_id,
        "exit_code": exit_code,
        "idle_seconds_at_death": idle_at_death,
        "last_activity_at_wallclock": last_activity,
    })

    # Cancel the teammate's tasks
    if teammate is not None:
        await teammate.shutdown()  # idempotent; cancels worker + poll task
```

**New error code: `teammate_dead`** — returned by `send_to` and `broadcast` when the recipient is tombstoned. Broker's `send` method checks `info.alive` before enqueueing; raises `TeammateAlreadyDeadError` (already reserved at `broker.py:27`); server catches and returns `{"error": "teammate_dead", "message": "teammate t-... died at <ts>; exit_code=<n>"}`.

**Transcript: new `lifecycle: died` event type** (Feature #4 schema, additive, no `v` bump):

```json
{
  "v": 1,
  "kind": "lifecycle",
  "ts": 1777256123.5,
  "crew_id": "abc12345",
  "event": "died",
  "teammate_id": "t-...",
  "exit_code": 137,
  "idle_seconds_at_death": 4.2,
  "last_activity_at_wallclock": 1777256119.3
}
```

### Design Decisions

> **Phase 2 review folded in (2026-04-26):** D2, D3, D4, D11 amended to address co-architect pushback (kill-path tombstoning, self-cancellation, escalation-on-hung-interrupt) and sentinel pushback (in-flight envelope bounce, clear-on-death wiring, `idle_seconds` freeze, interrupt-raising path).

- **D1 — Activity stamping in base class** — *Rationale:* Q5; one MCP-tool contract for stub + SDK. *Carried into:* `Teammate._stamp_activity()` (asserted by `tests/test_stub_teammate.py` activity test + `tests/test_sdk_teammate.py` per-event test). **Stamping order (sentinel):** `_collect_response_text` invokes the stamp callback at loop top, **before** any `continue` branch. RateLimitEvent and TaskNotificationMessage paths must stamp before continuing.
- **D2 — Single-writer death detection (poll task wins) + in-flight handoff** — *Rationale:* eliminates duplicate-`died` race; SC-5b's clause 1 requires the in-flight requester gets bounced even when the worker can't write the bounce itself. *Carried into:*
  - `Broker._handle_teammate_death` is idempotent via `info.alive` check.
  - `SdkTeammate._handle_one_turn`'s SDK-death exception path (`ProcessError` / `CLIConnectionError`) does three things in order: (a) `self._death_in_flight_envelope = env` to hand the requester back to the death handler; (b) `self._death_suspected = True`; (c) returns. Sends NO envelope itself.
  - `_handle_teammate_death` execution order: (1) call `teammate._end_turn()` to clear `current_turn_started_at`; (2) capture the in-flight envelope from `teammate._death_in_flight_envelope` (None if death was while idle); (3) write tombstone to `_info` via `dataclasses.replace`; (4) remove from `_teammates` (so concurrent `send_to` sees `alive=False` first → `teammate_dead`, not `unknown_teammate` — co-architect free correctness win); (5) bounce the in-flight envelope with `teammate_dead` to its sender; (6) drain inbox, bounce each pending envelope; (7) emit `lifecycle: died`; (8) **detach** worker+poll-task shutdown via `loop.create_task(teammate.shutdown())` rather than `await` — handler must NOT await its own poll task's cancellation. **Start-ordering invariant (co-architect):** `SdkTeammate.start()` spawns the poll task BEFORE the worker is allowed to enter its inbox loop, so the death-suspected handoff is always observable.
- **D3 — Tombstone via frozen-dataclass replace, not pop; freeze idle_seconds at death** — *Rationale:* preserves death-record fields for `get_teammate_status` queries after death; `idle_seconds` must freeze (sentinel) so post-mortem queries don't return monotonically-growing values from a dead teammate. *Carried into:*
  - `TeammateInfo` gains 4 nullable fields: `died_at_wallclock`, `exit_code`, `last_activity_at_wallclock_at_death`, **`idle_seconds_at_death`**.
  - `_info` dict entries persist with `alive=False`; `_teammates` dict entries are removed (active-set semantic).
  - `Broker.get_teammate_status` assembler short-circuits when `info.alive == False`: returns the frozen `idle_seconds_at_death` from `info` and forces `current_turn_started_at_wallclock=None` from the assembler regardless of teammate's stored field. (Defense-in-depth on top of D2's `_end_turn()` call.)
- **D4 — Backstop sequence: interrupt → bounded grace → drain → error; escalate on interrupt-hang/raise** — *Rationale:* SC-11 / co-architect pushback #2 / sentinel; covers both `interrupt()` hanging *and* `interrupt()` raising synchronously (e.g., subprocess died between backstop fire and interrupt call). *Carried into:* `_handle_one_turn` `asyncio.TimeoutError` branch:
  ```python
  interrupt_succeeded = False
  try:
      await asyncio.wait_for(client.interrupt(), timeout=INTERRUPT_GRACE_SECONDS)
      interrupt_succeeded = True
  except asyncio.TimeoutError:
      logger.warning("interrupt hung past %ss for teammate=%s", INTERRUPT_GRACE_SECONDS, self.id)
  except Exception as e:  # interrupt raised — subprocess may already be dead
      logger.warning("interrupt raised for teammate=%s: %s", self.id, e)
  if not interrupt_succeeded:
      # Co-architect escalation: hung-or-raising interrupt is a wedge signal.
      # Skip drain (SDK state is unknown), set death-suspected, send error envelope.
      # Poll task tombstones on next tick.
      self._death_suspected = True
  else:
      try:
          await asyncio.wait_for(_drain_remaining(client), timeout=POST_INTERRUPT_DRAIN_SECONDS)
      except asyncio.TimeoutError:
          pass
  await self._send_error_envelope(to=env.sender, code="backstop_timeout", message=...)
  return  # next turn starts only if not death-suspected; if it does, runs against clean stream
  ```
- **D5 — Probe degrade-open with broad `Exception` catch** — *Rationale:* SC-12 / co-architect pushback #3. *Carried into:* `_liveness_poll_loop` wraps the `_transport._process.returncode` access in `try/except Exception:` with WARNING-level log; on probe error, treat as alive and sleep to next tick.
- **D6 — `teammate_dead` as a peer error code, not folded into `unknown_teammate`** — *Rationale:* Jerome gate directive ("APIs cohesive and well suited to enterprise use"); preserves cause-of-death in the error surface. *Carried into:* broker raises `TeammateAlreadyDeadError` (already reserved); server.py `send_to` and `broadcast` route to `_err("teammate_dead", "teammate {id} died at {wallclock}; exit_code={n}")`.
- **D7 — `lifecycle: died` is additive, no schema `v` bump** — *Rationale:* Q4-resolved. *Carried into:* `tests/test_transcript.py` adds a fixture for the `died` line.
- **D8 — Liveness poll interval = 5s, configurable via `CLAUDE_CREW_LIVENESS_POLL_SECONDS`** — *Rationale:* well under SC-5's 30s ceiling. *Carried into:* module constant; env override at `SdkTeammate.__init__`.
- **D9 — Stub teammate has no liveness poll task** — *Rationale:* no subprocess to probe. *Carried into:* `StubTeammate.start()` spawns only the worker task.
- **D10 — `TURN_TIMEOUT_SECONDS` removed entirely; `SHUTDOWN_TIMEOUT_SECONDS` stays** — *Rationale:* SC-8. *Carried into:* `sdk_teammate.py:56` constant deleted.
- **D11 — `kill_teammate` tombstones uniformly, not pops** — *Rationale:* co-architect FIX-NOW; Q9-resolved (yes, stubs tombstone too). Cohesion: explicit kill and process-death share the post-mortem-queryable surface; operator can ask `get_teammate_status` after kill and get `alive=False` with `died_at_wallclock` set, `exit_code=None`. *Carried into:* `Broker.kill_teammate(id, reason="explicit")` reroutes through a shared tombstone code path that emits `lifecycle: kill` (not `died`) but otherwise produces the same `_info` shape (alive=False, died_at_wallclock=time.time(), exit_code=None, last_activity_at_wallclock_at_death=snapshot, idle_seconds_at_death=snapshot). The `lifecycle: died` event type is reserved for **unexpected** subprocess death (SC-6 invariant). Inbox drain + in-flight bounce semantics from D2 apply identically.
- **D12 — `broadcast` returns `skipped_dead` field** — *Rationale:* Q8-resolved (yes); enterprise-cohesion directive; silent skips are harder to debug than explicit ones. *Carried into:* `Broker.broadcast` filters tombstoned recipients before fan-out, returns both `delivered_to` (count of live recipients sent) and `skipped_dead` (list of tombstoned teammate ids). Server.py `broadcast` tool surfaces this in the response.

### Edge Cases

- **Poll task observes death during broker `shutdown_all`** — broker sets a `_stopping` flag; death handler checks it and routes to `lifecycle: shutdown` ordering instead of `lifecycle: died`. SC-6.
- **Two consecutive backstops in adjacent turns** — each turn's interrupt sequence is independent; teammate stays alive across both (SC-11 explicit: backstop is turn-level, not teammate-level).
- **Worker task crashes from a non-SDK bug (`AttributeError`, etc.)** — worker exits, sends error envelope to LEAD via existing `_run` outer-catch path. Subprocess is still alive; poll task sees `returncode is None`; teammate stays alive but stops processing inbox. **Known gap (deferred):** detecting "worker dead but subprocess alive" — out of scope for #6, captured as backlog item.
- **`client.interrupt()` itself hangs longer than `INTERRUPT_GRACE_SECONDS`** — log WARNING, abandon the interrupt await, proceed to drain (also bounded), then send error envelope. Worker continues to next turn. If subprocess is genuinely wedged, poll task tombstones on next tick.
- **Probe error every tick (e.g., SDK upgrade broke the attribute path)** — every tick logs WARNING, treats alive, never tombstones. Operator sees stderr noise; `get_teammate_status` shows alive but stale `idle_seconds` if stamping also broke. Backstop still fires at 1hr backstop boundary as last-resort safety. Acceptable degradation (fail open, observable, recoverable by SDK pin or fix).
- **`get_teammate_status` called for a teammate during the poll task's death-handler execution** — async single-loop guarantees no concurrent mutation; status reads `info` and `teammate.status_snapshot()` atomically from one task's perspective. Either pre-death or post-tombstone state is returned, never torn.
- **`send_to` race with `_handle_teammate_death`** — the death handler removes from `_teammates` first, so a concurrent send sees `UnknownTeammateError` (alive teammate has been pulled). After tombstone is fully written, `_info[id].alive == False` triggers `TeammateAlreadyDeadError`. Window of `unknown_teammate`-instead-of-`teammate_dead` is microseconds wide. Acceptable; both errors signal "do not retry."
- **Inbox drain during death-handler bounces an envelope whose sender is itself a dead teammate** — bounce delivery to the dead sender raises `UnknownTeammateError` / `TeammateAlreadyDeadError`; bounce is silently dropped (envelope loss is logged at INFO). Operator visibility via transcript's `lifecycle: died` for both teammates.
- **Stub teammate's `get_teammate_status` after `kill_teammate`** — stub's tombstone fields are all None / `exit_code=None` (no subprocess to source from). `alive=False`, `died_at_wallclock` is set (kill time), `exit_code=None`. Documented as "stub death = explicit kill only."
- **`idle_seconds` is silent during long tool execution** — *(co-architect: substrate honesty)* When a teammate runs a long-blocking tool (Bash build, large WebFetch, MCP tool call), `receive_response()` yields no events from the model's tool_use boundary until the tool returns. `idle_seconds` climbs across the entire tool window even though the subprocess is healthy and working. The two complementary signals — `current_turn_started_at != None` AND `returncode is None` — let the lead distinguish "doing work but quiet" from "wedged." This is a documented limit of #6's signal surface; tool-level telemetry via SDK hooks lands in **Feature #8**. The `get_teammate_status` docstring and `idle_seconds` field documentation must explicitly say so to prevent lead-side policy from encoding wrong assumptions.
- **Hung-or-raising `client.interrupt()` triggers death-suspected escalation** — *(co-architect:)* per D4, when interrupt fails (hangs past 30s OR raises synchronously), the teammate sets `_death_suspected=True` rather than continuing to next turn. The current turn's error envelope still goes to the requester (`backstop_timeout`); next turn does not start until the poll task either tombstones or confirms the subprocess is alive. This closes the residual S2 risk at the 1hr boundary that pure "abandon-and-continue" would have left.
- **`_handle_teammate_death` calling `await teammate.shutdown()` in-line would self-cancel** — *(co-architect:)* `teammate.shutdown()` cancels both the worker task AND the poll task that's currently executing the death handler. D2 detaches the shutdown via `loop.create_task(teammate.shutdown())` so the handler's writes (tombstone, bounces, transcript) all complete before the poll task gets cancelled by the detached shutdown task. Detached task is fire-and-forget (errors logged, not awaited).

### Validation Contracts at Handoff Boundaries

| Boundary | Preconditions | Failure Behavior | Postconditions | Rollback |
|---|---|---|---|---|
| Worker → poll task (in-turn SDK death) | Worker catches `ProcessError`/`CLIConnectionError` mid-turn | Worker sets `_death_suspected=True`, sends NO envelope, returns from turn | Poll task's next tick observes `returncode is not None`, calls `_handle_teammate_death` | None — `_handle_teammate_death` is idempotent (alive-check) |
| Poll task → broker death handler | `returncode is not None` (or `_death_suspected` flag set) | Probe error → log + skip tick; if persistent, backstop catches at 1hr | Single `lifecycle: died` event, full tombstone, inbox drained, in-flight requester bounced | None — handler is idempotent |
| Broker death handler → inbox drain | `_inboxes[id]` is owned by handler (popped before drain) | Bounce-send raises (dead sender) → log INFO, drop bounce | Every dead envelope's sender notified OR loss logged; inbox empty when handler returns | None |
| Backstop fire → interrupt → drain → error envelope | Backstop timer fired in `_handle_one_turn` | `interrupt()` hangs → log + skip; drain hangs → log + skip; error envelope always sent | Subprocess cleaned via interrupt OR detected dead by poll task; requester gets `backstop_timeout` envelope; next turn sees clean SDK stream | None |
| MCP `get_teammate_status` → broker → teammate | Teammate is registered (alive or tombstoned) | Unknown id → `unknown_teammate` error | Atomic snapshot of lifecycle + activity state | N/A (read-only) |

### Specification

The implementation lands as five focused changes, in this order:

1. **`teammate.py`**: Add `_last_activity_monotonic`, `_last_activity_wallclock`, `_current_turn_started_at_wallclock` to base `Teammate`; add `_stamp_activity()`, `_begin_turn()`, `_end_turn()`, `status_snapshot()`. Update `StubTeammate._run()` to call `_begin_turn()` on dequeue and `_end_turn()` after sending its echo.
2. **`broker.py`**: Extend `TeammateInfo` with the **four** nullable death-record fields (`died_at_wallclock`, `exit_code`, `last_activity_at_wallclock_at_death`, `idle_seconds_at_death`). Add `_handle_teammate_death(teammate_id, exit_code)` (single-writer, idempotent, executes the 8-step sequence in D2). Add `get_teammate_status(teammate_id)` (read-only, combines info + snapshot, short-circuits on `alive=False`). Update `send` to check `info.alive` and raise `TeammateAlreadyDeadError`. **Update `kill_teammate` to route through the same tombstone code path (D11) emitting `lifecycle: kill` instead of `lifecycle: died`.** Update `broadcast` to filter tombstoned recipients and return `skipped_dead` (D12).
3. **`sdk_teammate.py`**: Delete `TURN_TIMEOUT_SECONDS` constant. Replace `asyncio.wait_for(..., timeout=TURN_TIMEOUT_SECONDS)` in `_handle_one_turn` with the D4 backstop sequence (interrupt-first with hung-OR-raising handling, bounded grace, drain only on interrupt success, escalate to `_death_suspected` otherwise, always send error envelope). Add `_liveness_poll_loop` async method spawned by `start()` **before the worker enters its inbox loop** (D2 start-ordering invariant) as a sibling task. Pass `_stamp_activity` callback into `_collect_response_text`; **callback fires at loop top, before any `continue` branch** (D1 stamping order). Add `_death_suspected` flag and `_death_in_flight_envelope` field. SDK-death exception path in `_handle_one_turn` sets both, returns without sending an envelope. Add `CLAUDE_CREW_LIVENESS_POLL_SECONDS` and `CLAUDE_CREW_TURN_BACKSTOP_SECONDS` env reads.
4. **`server.py`**: Add `get_teammate_status` MCP tool delegating to `broker.get_teammate_status` — docstring **explicitly notes** `idle_seconds` reflects SDK stream activity only and that long-running tool execution appears as idle (Feature #8 will close this gap). Update `send_to` and `broadcast` exception handling to catch `TeammateAlreadyDeadError` → `_err("teammate_dead", "...")`. Update `broadcast` response shape to include `skipped_dead`.
5. **`tests/fakes/programmable_sdk_client.py` (NEW)**: Extend `FakeSDKClient` with: configurable per-event delays (`event_timings: list[float]`), `interrupt_calls` tracker + async `interrupt()` method (also configurable to hang OR raise — for D4 testing), configurable `_transport._process.returncode` (settable mid-test), configurable raise-on-read/write to simulate `ProcessError`/`CLIConnectionError`. Used by SC-1, SC-3, SC-7, SC-11, SC-12 tests.

**Phase 3 prerequisite (co-architect FIX-NOW):** Before Phase 3 task breakdown, run `grep -nE "unknown_teammate.*kill_teammate|kill_teammate.*unknown_teammate" tests/` and audit any hits — D11 changes the post-kill error code from `unknown_teammate` to `teammate_dead`. Tests asserting the old behavior become explicit Phase 3 tasks, not Phase 4 surprises.

**Phase 4 prerequisite (sentinel A2 hardening):** Before Phase 4 closes, run a 30-second live SDK probe to confirm A2 (`client.interrupt()` is safe to call concurrently with an in-flight `receive_response()` drain). Cheap insurance against the silent S2-recurrence failure mode if A2 is wrong.

### Assumptions

*Default-accept; call out any that are wrong before Phase 3.*

- **A1 — `client._transport._process.returncode` access does not raise on a healthy SDK** — *Default:* read returns `None` synchronously without I/O. *Rationale:* anyio's Process abstraction surfaces `returncode` as a property snapshot, not an awaitable; spike confirmed pattern. If wrong, the probe wraps in `try/except` and degrades open per SC-12.
- **A2 — `client.interrupt()` is safe to call concurrently with an in-flight `receive_response()` drain** — *Default:* yes, that is precisely the public API's purpose. *Rationale:* spike confirmed it routes through control protocol over stdin separately from the read stream.
- **A3 — `INTERRUPT_GRACE_SECONDS = 30.0` is enough headroom for a normal interrupt acknowledgment** — *Default:* yes; SDK's own internal `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` defaults to 60s, so we'd fail-open well before the SDK gives up. *Rationale:* a hung interrupt at this point indicates a wedged subprocess that the poll task will catch independently.
- **A4 — `POST_INTERRUPT_DRAIN_SECONDS = 5.0` is enough to flush in-flight events** — *Default:* yes; events that haven't arrived in 5s after an interrupt acknowledgment are extremely unlikely to ever arrive. *Rationale:* drain is best-effort; any leftovers will surface as the first event of the next turn (which is fine — the next turn's `query()` resets `session_id="default"` semantics).
- **A5 — Adding three nullable fields to `TeammateInfo` does not break existing dataclass consumers** — *Default:* yes; dataclass field additions with defaults are additive in Python. *Rationale:* `frozen=True` permits this; all consumers read by name, not by position.
- **A6 — Test code does not import `TURN_TIMEOUT_SECONDS` directly** — *Default:* yes; grep returned only the constant declaration in `sdk_teammate.py:56`. *Rationale:* if a stray import surfaces during Phase 4, it's a test fix, not a design change.
- **A7 — `_handle_teammate_death` calling `await teammate.shutdown()` on an SDK teammate whose subprocess is already dead is safe** — *Default:* yes; the shutdown sentinel goes into the inbox queue; worker task is already exited (or about to be); `asyncio.wait_for` falls through. *Rationale:* current `shutdown` impl already handles `CancelledError` and timeout. Phase 4 should add a regression test for this path.
- **A8 — `idle_seconds` computed from `time.monotonic()` is meaningful across the poll task's lifetime** — *Default:* yes; monotonic clock is per-process and monotonically increasing. *Rationale:* monotonic delta is unaffected by wall-clock adjustments (NTP sync, DST, etc.), which is exactly what `idle_seconds` math wants.
- **A9 — Tombstones in `_info` are bounded by broker lifetime; no eviction needed at MVP scale** — *Default:* yes; broker process restarts on every claude-crew session; tombstone count = teammates spawned in this session ≤ ~10s. *Rationale:* (co-architect:) `_info` grows monotonically with this design; no LRU/TTL eviction. At MVP scale (one developer machine, one crew per session), this is trivially bounded. If a future deployment runs a long-lived broker with thousands of spawn/kill cycles, tombstone GC becomes a real concern — but not at the scale Feature #6 ships at. Documented for the future-self who scales us up.

### Open Questions

Q7, Q8, Q9 went out for review; co-architect and sentinel both concurred with the leans. **All three resolved at the lean's recommendation.** Recorded here for traceability.

- [x] **Q7 — RESOLVED: no push-to-lead death envelope.** Substrate observes-and-records; push-to-lead patterns belong in Feature #7 (subagent-activity envelopes) where the broader push surface gets designed. LEAD discovers death via `teammate_dead` error on next `send_to`, defensive `get_teammate_status` call, or transcript tailing.
- [x] **Q8 — RESOLVED: `broadcast` returns `skipped_dead` field.** See D12. Cohesion directive: silent skips are harder to debug than explicit ones.
- [x] **Q9 — RESOLVED: stubs tombstone uniformly via D11.** `kill_teammate` on a stub produces `alive=False`, `died_at_wallclock=time.time()`, `exit_code=None`. Same `get_teammate_status` shape across stub and SDK teammates.

**Gate**:
- ✅ Co-architect (co-architect-f6) review of Phase 2 design — done. 4 [FIX-NOW] folded in (kill-path tombstoning D11, test blast-radius prerequisite, idle_seconds honesty Edge Case, self-cancellation D2 detach). 3 [ACCEPTABLE] preferences folded in (start-ordering invariant D2, escalate-on-hung-interrupt D4, tombstone-before-pop ordering D2). 2 [GOOD] confirmations on D5 + D2 single-writer pattern. Verdict: ready for sentinel + gate after fixes.
- ✅ Sentinel (sentinel-p1) review of Phase 2 spec coverage — done. SC traceability matrix 13/13 PASS or PASS-with-verification after fixes. 5 [FIX-NOW] folded in (SC-5b in-flight bounce D2, SC-4 clear-on-death wiring D2, idle_seconds freeze D3, interrupt-raising path D4, SC-3 stamping order D1). Q7/Q8/Q9 leans concurred.
- ✅ Q7, Q8, Q9 answered (all leans, both reviewers concurred)
- ⏳ Jerome confirms

---

## Phase 3: Task Breakdown

Five tasks, ordered by dependency. T1 → T2 in parallel; T3 depends on T1; T4 depends on T1/T2/T3; T5 depends on all.

**Test blast radius (D11 prerequisite, completed):**
- `tests/test_server.py:200-204` — send-to-killed expects `unknown_teammate` → update to `teammate_dead` (T3 scope).
- `tests/test_server_sdk_mode.py:103` — same pattern → update to `teammate_dead` (T3 scope).
- `tests/test_broker.py:test_send_to_killed_teammate_raises` — already accepts both `UnknownTeammateError | TeammateAlreadyDeadError`; narrow to `TeammateAlreadyDeadError` for hygiene (T3 scope).
- All `unknown_teammate` tests for never-existed ids stay unchanged (correct behavior preserved).

---

### Task 1: Base teammate activity stamping
**Depends on**: None | **Blocks**: T3, T4

Adds activity telemetry to the base `Teammate` ABC (D1) and wires `StubTeammate` to use it. This is the smallest, most foundational change — independently testable without any broker or SDK plumbing.

**Files**: `claude_crew/teammate.py`

**Implementation**:
- Add `_last_activity_monotonic: float`, `_last_activity_wallclock: float`, `_current_turn_started_at_wallclock: float | None` to base `Teammate`. Initialize in `__init__` to construction time / None.
- Add `_stamp_activity()`, `_begin_turn()`, `_end_turn()`, `status_snapshot() -> dict` methods.
- Update `StubTeammate._run()` to call `_begin_turn()` after `inbox.get()` returns and before any handling, then `_end_turn()` after sending the echo response.

**Acceptance Criteria** (BDD):

```
Scenario: Stub teammate stamps activity on every dequeue (SC-3 analog for stubs)
  Given a started StubTeammate with empty inbox
  And initial last_activity_at_monotonic recorded
  When the lead sends 3 envelopes via the broker
  Then status_snapshot's last_activity_at_monotonic advances at least 3 times
  And status_snapshot's idle_seconds is < 1.0 immediately after the third send

Scenario: Stub teammate clears current_turn_started_at between turns (SC-4)
  Given a started StubTeammate
  When the lead sends one envelope and the teammate has finished echoing
  Then status_snapshot()["current_turn_started_at_wallclock"] is None
  And status_snapshot()["last_activity_at_wallclock"] is set

Scenario: Stub teammate sets current_turn_started_at while in turn (SC-4)
  Given a StubTeammate constructed with a slow-echo flag (synthetic delay)
  When the lead sends one envelope
  And the test polls status_snapshot during the echo delay
  Then current_turn_started_at_wallclock is set to a recent time
```

**Verification**: `cd /home/jerome/dev/claude-crew && uv run pytest tests/test_stub_teammate.py -v` — three new tests pass; existing tests untouched.

---

### Task 2: Programmable SDK fake
**Depends on**: None | **Blocks**: T4, T5

A new test fake that lets us drive the SDK at the event level: configurable per-event delays, `interrupt()` tracking with hang/raise modes, settable `_transport._process.returncode`, configurable raise-on-read/write. Used by SC-1, SC-3, SC-7, SC-11, SC-12 verification in T4 and T5.

**Files**: `tests/fakes/programmable_sdk_client.py` (NEW)

**Implementation**:
- `ProgrammableSDKClient` extends or wraps `tests/fakes/sdk.py`'s `FakeSDKClient`.
- `__init__` accepts: `event_timings: list[float]` (delay before each yielded event), `interrupt_behavior: Literal["normal", "hang", "raise"]`, `transport_returncode: int | None` (settable), `read_raises: type[Exception] | None`, `write_raises: type[Exception] | None`.
- Override `receive_response()`: per yielded event, `await asyncio.sleep(event_timings[i])` then yield.
- Add `interrupt_calls: list[float]` (records monotonic time of each call); `async def interrupt()`: appends call time, then either returns / hangs (await Event never set) / raises depending on `interrupt_behavior`.
- Mock `self._transport = SimpleNamespace(_process=SimpleNamespace(returncode=transport_returncode))` so SC-12 tests can flip the field mid-test.
- `query()` and `interrupt()` honor `read_raises` / `write_raises` if configured.

**Acceptance Criteria** (BDD):

```
Scenario: Programmable fake yields events at configured delays
  Given a ProgrammableSDKClient with event_timings=[0.0, 0.5, 0.0] and 3 events
  When the test drives receive_response() with monotonic timestamps
  Then the second event arrives ≥0.5s after the first
  And the third event arrives ≤0.1s after the second

Scenario: Programmable fake's interrupt records calls in normal mode
  Given a ProgrammableSDKClient with interrupt_behavior="normal"
  When the test calls await client.interrupt() twice
  Then client.interrupt_calls has length 2
  And both entries are recent monotonic times

Scenario: Programmable fake's interrupt hangs in hang mode
  When client = ProgrammableSDKClient(interrupt_behavior="hang")
  And the test wraps client.interrupt() in asyncio.wait_for(timeout=0.5)
  Then asyncio.TimeoutError is raised
  And client.interrupt_calls still records the hung call

Scenario: Programmable fake's interrupt raises in raise mode
  When client = ProgrammableSDKClient(interrupt_behavior="raise")
  Then await client.interrupt() raises ConnectionError (or configured exception)

Scenario: Settable transport returncode is observable
  Given a ProgrammableSDKClient with transport_returncode=None
  When the test sets client._transport._process.returncode = 137
  Then a subsequent read of returncode returns 137
```

**Verification**: `uv run pytest tests/test_programmable_sdk_client.py -v` — five new tests pass. (New test file accompanies the fake.)

---

### Task 3: Broker tombstone, cohesive APIs, MCP tool wiring
**Depends on**: T1 | **Blocks**: T4, T5

Implements D2/D3/D6/D11/D12 in `broker.py`, plus the `get_teammate_status` MCP tool and error-code routing in `server.py` (D6/D11/D12 surface). This is the data-layer + lead-facing API change. Pure Python; doesn't need the SDK fake.

**Files**: `claude_crew/broker.py`, `claude_crew/server.py`, `tests/test_broker.py` (extend), `tests/test_server.py` (extend + 1 contract update), `tests/test_server_sdk_mode.py` (1 contract update)

**Implementation**:
- Extend `TeammateInfo` with 4 nullable fields: `died_at_wallclock`, `exit_code`, `last_activity_at_wallclock_at_death`, `idle_seconds_at_death`.
- Add `Broker._handle_teammate_death(teammate_id, exit_code)` — the 8-step idempotent sequence from D2 (end_turn → capture in-flight → tombstone via dataclasses.replace → pop active → bounce in-flight → drain inbox + bounce → emit `lifecycle: died` → detached shutdown via `loop.create_task`).
- Add `Broker.get_teammate_status(teammate_id) -> dict` — combines `TeammateInfo` + `teammate.status_snapshot()`; short-circuits on `alive=False` to return frozen `idle_seconds_at_death` and force `current_turn_started_at_wallclock=None`.
- Update `Broker.send` to check `info.alive` before enqueueing; raise `TeammateAlreadyDeadError` if dead.
- **Reroute `Broker.kill_teammate`** to call the same tombstone code path (D11), emitting `lifecycle: kill` (not `died`); same `TeammateInfo` shape, `exit_code=None`.
- Update `Broker.broadcast` (D12) — filter out `not info.alive`, return `skipped_dead: list[str]` alongside existing `message_ids` and `delivered_to`.
- Add `get_teammate_status` MCP tool in `server.py`. Docstring **explicitly notes** `idle_seconds` reflects SDK stream activity only and that long-running tool execution appears as idle (Feature #8 will close).
- Update `server.py` `send_to` and `broadcast` exception handling: catch `TeammateAlreadyDeadError` → `_err("teammate_dead", "...")`; surface `skipped_dead` in `broadcast` response.
- Update `tests/test_server.py:200-204` and `tests/test_server_sdk_mode.py:103` — change `unknown_teammate` to `teammate_dead` for post-kill assertions.
- Narrow `tests/test_broker.py:test_send_to_killed_teammate_raises` to `pytest.raises(TeammateAlreadyDeadError)` only.

**Acceptance Criteria** (BDD):

```
Scenario: kill_teammate tombstones (does not evict) — D11
  Given a started StubTeammate teammate t-X
  When the lead calls kill_teammate(t-X)
  Then list_crew() includes t-X with alive=False
  And get_teammate_status(t-X) returns alive=False with died_at_wallclock set
  And exit_code is None
  And the transcript has a lifecycle: kill line (NOT lifecycle: died)

Scenario: send_to a killed teammate returns teammate_dead — D6
  Given a killed teammate t-X
  When the lead calls send_to(t-X, "hello")
  Then the response is {"error": "teammate_dead", "message": "..."}
  And the message includes the died_at timestamp

Scenario: get_teammate_status on unknown id (cohesion preserved)
  When the lead calls get_teammate_status("ghost")
  Then the response is {"error": "unknown_teammate", "message": "..."}

Scenario: broadcast filters dead recipients and reports skipped_dead — D12
  Given two alive teammates t-A and t-B and one tombstoned teammate t-C
  When the lead calls broadcast({"hello": "all"})
  Then delivered_to is 2
  And skipped_dead == ["t-C"]
  And neither t-C's inbox nor any successor receives the envelope

Scenario: _handle_teammate_death is idempotent — D2
  Given an alive SDK teammate t-X (using stub-broker shim)
  When _handle_teammate_death(t-X, exit_code=137) is called twice
  Then exactly one lifecycle: died line appears in the transcript
  And t-X's TeammateInfo shows died_at_wallclock from the FIRST call

Scenario: _handle_teammate_death drains inbox and bounces pending envelopes — SC-5b
  Given a teammate t-X with 3 envelopes queued in its inbox
  When _handle_teammate_death(t-X, exit_code=137) runs
  Then each of the 3 senders receives a teammate_dead error envelope
  And the lifecycle: died line is the LAST broker write for t-X

Scenario: _handle_teammate_death bounces the in-flight envelope — SC-5b clause 1
  Given a teammate t-X mid-turn on envelope E (envelope dequeued, worker driving SDK)
  When the worker observes SDK death and sets _death_in_flight_envelope = E
  And the poll task triggers _handle_teammate_death
  Then E's sender receives a teammate_dead error envelope
  And the bounce arrives BEFORE the inbox-drain bounces

Scenario: get_teammate_status freezes idle_seconds at death — D3
  Given a teammate t-X with last_activity_at = T0 that gets tombstoned at T1
  When the lead calls get_teammate_status(t-X) at T1 + 10s
  Then idle_seconds equals (T1 - T0), NOT (T1 + 10s - T0)
  And current_turn_started_at_wallclock is None

Scenario: Concurrent send_to during _handle_teammate_death sees teammate_dead — co-architect tombstone-before-pop
  Given a teammate t-X being tombstoned (mid-handler)
  When a concurrent send_to(t-X, ...) lands
  Then the response is teammate_dead, NOT unknown_teammate

Scenario: kill_teammate on a stub produces the same shape as SDK death — Q9 cohesion
  When the lead kills a StubTeammate t-X
  Then get_teammate_status(t-X) returns alive=False, died_at_wallclock set, exit_code=None,
       last_activity_at_wallclock_at_death set, idle_seconds_at_death set
  And the same response shape as a tombstoned SDK teammate

Scenario: Pre-existing kill-then-send tests use the new contract
  When the lead kills a teammate then sends to it
  Then test_server.py and test_server_sdk_mode.py expect teammate_dead, not unknown_teammate
```

**Verification**: `uv run pytest tests/test_broker.py tests/test_server.py tests/test_server_sdk_mode.py -v` — all existing tests pass (with the contract updates) and ~10 new scenarios pass.

---

### Task 4: SdkTeammate liveness, backstop, activity callback
**Depends on**: T1, T2, T3 | **Blocks**: T5

The substrate change. Deletes `TURN_TIMEOUT_SECONDS`, replaces the wall with the D4 backstop sequence (interrupt-first with hung/raising escalation), adds the liveness poll task (D5/D8), wires per-event activity stamping (D1).

**Files**: `claude_crew/sdk_teammate.py`, `tests/test_sdk_teammate.py` (extend)

**Implementation**:
- **Delete `TURN_TIMEOUT_SECONDS` constant** (line 56).
- Add module constants: `INTERRUPT_GRACE_SECONDS = 30.0`, `POST_INTERRUPT_DRAIN_SECONDS = 5.0`, `POLL_INTERVAL_SECONDS_DEFAULT = 5.0`. Read `CLAUDE_CREW_TURN_BACKSTOP_SECONDS` (default 3600.0) and `CLAUDE_CREW_LIVENESS_POLL_SECONDS` (default 5.0) at `__init__`.
- Add fields: `_death_suspected: bool = False`, `_death_in_flight_envelope: Envelope | None = None`, `_poll_task: asyncio.Task | None = None`.
- Update `_collect_response_text` signature to accept a `stamp_activity: Callable[[], None]` callback. Invoke at loop top, **before** any `continue` branch (D1 stamping order).
- Update `_handle_one_turn`:
  - Call `self._begin_turn()` at top.
  - Replace `asyncio.wait_for(_collect_response_text(client), timeout=TURN_TIMEOUT_SECONDS)` with `asyncio.wait_for(_collect_response_text(client, self._stamp_activity), timeout=self._backstop_seconds)`.
  - On `asyncio.TimeoutError`: execute D4 sequence (interrupt with grace, escalate on hang/raise via `_death_suspected=True`, send `backstop_timeout` error envelope).
  - On `ProcessError`/`CLIConnectionError`: set `self._death_in_flight_envelope = env`, set `self._death_suspected = True`, return WITHOUT sending an envelope.
  - Call `self._end_turn()` before returning successfully.
- Add `async def _liveness_poll_loop(self)`: every `POLL_INTERVAL_SECONDS`, broadly catch reading `_transport._process.returncode`; if `returncode is not None` OR `self._death_suspected`, call `self._broker._handle_teammate_death(self.id, exit_code=returncode)` and exit the loop.
- Update `start()` to spawn `_poll_task` BEFORE the worker enters its inbox loop (D2 start-ordering invariant). Use an `asyncio.Event` if needed to gate worker entry on poll-task ready.
- Update `shutdown()` to cancel `_poll_task` alongside the worker task.

**Acceptance Criteria** (BDD):

```
Scenario: A 12-minute turn completes successfully with no wall — SC-1
  Given an SdkTeammate driven by ProgrammableSDKClient with event_timings totaling 720s simulated
  When the lead sends one envelope and waits for the response
  Then the response is the normal result envelope
  And no invalid_response error envelope was sent
  And no backstop_timeout error envelope was sent
  And TURN_TIMEOUT_SECONDS does not exist as a module attribute

Scenario: Activity stamps advance on every yielded event — SC-3
  Given an SdkTeammate driven by ProgrammableSDKClient yielding 5 events
       (mix of AssistantMessage, RateLimitEvent, TaskNotificationMessage)
  When the lead sends one envelope
  Then status_snapshot's last_activity_at_monotonic advances 5+ times during the drain
  And RateLimitEvent and TaskNotificationMessage events stamp activity
       (NOT skipped by the continue branches)

Scenario: S2 cannot occur — turn N+1 receives turn N+1's response — SC-7
  Given an SdkTeammate using a single ProgrammableSDKClient instance
  When turn N is sent (slow drain configured to take ~30s simulated)
  And turn N completes normally with content "alpha"
  And turn N+1 is sent with prompt "next"
  Then turn N+1's response payload contains turn N+1's content "beta"
  And NEVER turn N's content "alpha"

Scenario: Backstop fires, interrupt succeeds, error envelope sent — SC-11
  Given an SdkTeammate with backstop=2.0s and ProgrammableSDKClient that hangs forever
  When the lead sends one envelope
  Then within 3 seconds:
    client.interrupt_calls has length 1
    the lead receives an error envelope with code "backstop_timeout"
  And the teammate is still alive (not tombstoned)

Scenario: Backstop fires, interrupt hangs, escalates to death-suspected — co-architect D4
  Given an SdkTeammate with backstop=2.0s and ProgrammableSDKClient(interrupt_behavior="hang")
       and INTERRUPT_GRACE_SECONDS=1.0 (test override)
  When the lead sends one envelope
  Then within 5 seconds:
    error envelope with code "backstop_timeout" is sent
    self._death_suspected becomes True
    on next poll task tick, _handle_teammate_death is invoked
    teammate is tombstoned

Scenario: Backstop fires, interrupt raises, escalates to death-suspected — sentinel D4
  Given the same setup but interrupt_behavior="raise"
  When the lead sends one envelope
  Then error envelope is sent
  And self._death_suspected becomes True
  And tombstoning follows on next poll tick

Scenario: Subprocess dies between turns — poll task tombstones within 30s — SC-5
  Given an SdkTeammate at idle (no in-flight turn)
  When the test sets client._transport._process.returncode = 137
  Then within (poll_interval + 1)s, _handle_teammate_death is called
  And lifecycle: died is in the transcript with exit_code=137
  And get_teammate_status returns alive=False

Scenario: SDK death mid-turn handed to handler via _death_in_flight_envelope — SC-5b
  Given an SdkTeammate mid-turn with envelope E in flight
  When ProgrammableSDKClient is configured to raise ProcessError on next read
  Then the worker sets _death_in_flight_envelope=E and _death_suspected=True
  And does NOT send any envelope itself
  And the poll task triggers _handle_teammate_death which bounces E with teammate_dead

Scenario: Probe error degrades open — SC-12
  Given an SdkTeammate whose returncode-read injects OSError
  When 3 poll cycles elapse
  Then 3 WARNING log lines are emitted
  And teammate is still alive (alive=True)
  And no lifecycle: died line is in the transcript
```

**Verification**: `uv run pytest tests/test_sdk_teammate.py -v` — all existing tests pass (with mocks updated for stamping callback), ~9 new scenarios pass.

---

### Task 5: End-to-end integration sweep + live SDK A2 probe
**Depends on**: T1, T2, T3, T4 | **Blocks**: Phase 5

Cohesive scenarios that exercise the full pipeline through the MCP tool surface (server.py → broker.py → SdkTeammate → fake SDK), plus the Phase 4 prerequisite live probe of A2 (interrupt safe to call concurrently with receive_response drain).

**Files**: `tests/test_telemetry_e2e.py` (NEW), `tests/test_live_sdk.py` (extend)

**Happy Path Scenarios**:

```
Scenario: Full crew lifecycle with telemetry — happy path
  Given a fresh broker (E2E mode with ProgrammableSDKClient factory)
  When the lead spawns a teammate, sends 3 envelopes, polls get_teammate_status after each
  Then status reflects: alive=True throughout, last_activity_at advancing,
       current_turn_started_at_wallclock set during turns and None between
  And the lead kills the teammate
  And get_teammate_status returns alive=False with full death record
  And the transcript contains: started, spawn, 3 envelope pairs, kill (NOT died)

Scenario: Multi-teammate broadcast skips tombstoned recipients
  Given 3 spawned teammates: t-A alive, t-B alive, t-C killed
  When the lead broadcasts {"hello": "all"}
  Then response is {message_ids: [...], delivered_to: 2, skipped_dead: ["t-C"]}
  And t-A and t-B both process the broadcast
  And t-C is silent
```

**Sad Path Scenarios**:

```
Scenario: Subprocess dies; subsequent send_to returns teammate_dead with cause
  Given an alive SDK teammate t-X
  When the test simulates subprocess death (set returncode=137, await poll cycle)
  Then within 30s the broker emits lifecycle: died with exit_code=137
  And get_teammate_status returns alive=False, exit_code=137
  And subsequent send_to(t-X, ...) returns {"error": "teammate_dead", "message": "...exit_code=137..."}
  And no orphaned envelopes remain in t-X's inbox

Scenario: Backstop fires during real work; teammate continues to next turn
  Given an SdkTeammate with backstop=3.0s, healthy fake (interrupt normal)
  When the test sends a slow envelope that hangs the drain past backstop
  Then the lead receives backstop_timeout error envelope
  And client.interrupt_calls is non-empty
  And a SECOND envelope sent immediately afterward processes normally
  And status shows alive=True with current_turn_started_at advanced

Scenario: Probe failure does not mass-tombstone — degrade-open verified end-to-end
  Given 3 alive teammates, all with returncode-read configured to raise OSError
  When 6 poll cycles elapse
  Then all 3 teammates remain alive=True
  And get_teammate_status for all 3 returns alive=True
  And only WARNING log lines are emitted, no lifecycle: died

Scenario: idle_seconds during long tool execution is honest (documented limit)
  Given an SdkTeammate using ProgrammableSDKClient with one event followed by a 30s silent gap
       (simulating tool execution between assistant messages)
  When the test polls get_teammate_status during the silent gap
  Then idle_seconds climbs continuously
  And current_turn_started_at_wallclock remains set throughout
  And alive remains True (returncode is None — subprocess healthy)
  # This scenario PROVES the documented gap that Feature #8 will close
```

**Live SDK Probe — A2 hardening (Phase 4 prerequisite)**:

```
Scenario: client.interrupt() is safe to call concurrently with active receive_response drain
  Given a real ClaudeSDKClient (gated by CLAUDE_CREW_LIVE_TESTS=1)
  When a long-running query() is in flight and concurrently await client.interrupt()
  Then no exception is raised by either await
  And the receive_response stream terminates cleanly within 30s
  # This is A2 — if wrong, S2 reappears silently at the 1hr boundary
```

**Verification**:
- Default: `uv run pytest tests/test_telemetry_e2e.py -v` — all 5 e2e scenarios pass.
- Live A2 probe: `CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_sdk.py::test_interrupt_during_drain -v` — passes against real SDK. Run once at Phase 4 close; cost ≤ $0.05 per run.

---

**Gate**:
- ✅ 5 tasks, dependencies wired (T1+T2 parallel; T3 depends on T1; T4 depends on T1+T2+T3; T5 depends on all)
- ✅ Dedicated E2E test task (T5) with happy + sad path coverage including the documented `idle_seconds`-during-tool-execution honesty scenario
- ✅ Verification commands fail without the feature (each task adds new tests; T1's new scenarios assert fields that don't exist today; T3's new scenarios assert behaviors current code lacks; etc.)
- ✅ Each Phase 2 SC traces to ≥1 BDD scenario:
  - SC-1: T4 "12-minute turn completes"
  - SC-2: T3 "kill_teammate tombstones", T3 "get_teammate_status on unknown id"
  - SC-3: T1 "stub stamps on every dequeue", T4 "activity stamps advance on every yielded event"
  - SC-4: T1 "clears between turns" + "sets while in turn"; T3 "freezes idle_seconds at death"
  - SC-5: T4 "subprocess dies between turns"; T3 "_handle_teammate_death idempotent"
  - SC-5b: T3 "drains inbox and bounces pending"; T3 "bounces in-flight envelope"; T4 "SDK death mid-turn handed via _death_in_flight_envelope"
  - SC-6: T3 "kill_teammate tombstones (NOT lifecycle: died)"
  - SC-7: T4 "S2 cannot occur"
  - SC-8: T4 "TURN_TIMEOUT_SECONDS does not exist"
  - SC-9: T3 transcript line shape verified in the kill/death scenarios
  - SC-10: existing test runs in T3/T4 verification commands
  - SC-11: T4 "backstop fires, interrupt succeeds"; T4 "interrupt hangs"; T4 "interrupt raises"
  - SC-12: T4 "probe error degrades open"; T5 "probe failure does not mass-tombstone"
- ✅ Cross-feature interaction scenarios: T3 covers Feature #4 transcript (lifecycle: died/kill schema); T3 covers Feature #1 MCP tool surface (get_teammate_status, broadcast skipped_dead, teammate_dead error code)
- ✅ Phase 4 prerequisite (A2 live probe) is itself a verification scenario in T5
- ⏳ Jerome approves

---

## Phase 4: Implementation

*To be filled after Phase 3 gate.*

---

## Phase 5: Completion

*To be filled after Phase 4 completion.*
