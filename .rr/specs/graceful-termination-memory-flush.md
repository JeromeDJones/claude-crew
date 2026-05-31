<!-- vars: SLUG=graceful-termination-memory-flush -->

# Spec: graceful-termination-memory-flush

## Problem

When the coordinator (or a shutdown) terminates a *healthy* claude-crew teammate today, the
teammate is hard-killed immediately: `broker._tombstone_teammate` ends the turn, snapshots
telemetry, bounces in-flight/queued envelopes, closes tools, emits a lifecycle event, and
detaches `shutdown()`. It never gives the teammate a chance to persist what it learned. The
multi-scope-agent-memory feature is entirely spawn-side and self-directed — the teammate gets
the `Write` tool plus a memory addendum in its system prompt and writes memory *if it chooses*,
mid-session. Kill it before it self-distills and that conversation's lessons are lost forever.
The outcome we want: when a healthy teammate that has a memory surface is terminated, it gets
one final, bounded turn to distill anything worth saving into its project/user/local-scoped
memory via the `Write` tool — *before* the broker tombstones it — replacing today's immediate
hard kill with a graceful termination. Teammates with no memory surface, forced hard kills, and
unexpected deaths are unaffected.

## Architecture Overview

The flush turn must run on the **same `ClaudeSDKClient` session** the teammate has been using
(`sdk_teammate.py:1329`, held in `_run`'s `async with ClaudeSDKClient(...)` block) — a fresh
session has no conversation to distill from. Therefore the flush executes **inside the
teammate's own `_run` task** (the single owner of `client`), never in the broker's task. The
broker only *signals* the teammate and *waits* (bounded) for completion, then runs the existing
tombstone unchanged.

Control flow:

```
broker.kill_teammate(teammate_id, reason, graceful=True, flush_timeout=90.0)
  ├─ if not graceful OR teammate not alive OR not teammate.has_memory_surface():
  │     → skip straight to _tombstone_teammate  (today's behavior)
  └─ else (graceful + healthy + has memory surface):
        ├─ mark teammate terminating  (broker bounces NEW sends as teammate_dead)
        ├─ await teammate.begin_graceful_termination(timeout=flush_timeout)
        │     (runs the flush turn INSIDE _run on the held client; bounded)
        └─ THEN _tombstone_teammate(...)  unchanged  (D2 ordering preserved —
              flush happens BEFORE tombstone step 1)
```

`begin_graceful_termination` unifies idle and busy under one termination signal:

- **IDLE** (`_run` blocked on `inbox.get()`): inject a flush sentinel into the inbox to break
  the wait; the loop recognizes the sentinel and runs the flush turn inline, then signals
  `_flush_complete`.
- **BUSY** (inside `_handle_one_turn`): `client.interrupt()` breaks the in-flight drain (the
  in-flight turn's result is already discarded on kill today, so we do not wait for it); the
  turn handler observes the `_terminating` flag and runs the flush turn inline (same task, same
  client), then signals `_flush_complete`.

The flush turn reuses the proven `_handle_one_turn` machinery: `client.query(flush_prompt, …)`
then a bounded `_collect_response_text` drain, with the existing `interrupt() → bounded-grace →
re-drain` ladder available on backstop. `client.interrupt()` is not a new primitive here
(`sdk_teammate.py:1376-1404`).

### Call-site survey

| Call-site | Path | Shape | Notes |
|-----------|------|-------|-------|
| explicit kill | `claude_crew/broker.py:597` `kill_teammate` | async, single teammate id | new `graceful`/`flush_timeout` params; flush is opt-out via `graceful=False` |
| shutdown | `claude_crew/broker.py:607` `shutdown_all` | async, iterates all teammates | must flush in **parallel** under ONE shared deadline, not N×timeout sequential |
| death detection | `claude_crew/broker.py:589` `_handle_teammate_death` | async, subprocess already gone | NEVER flushes — routes straight to `_tombstone_teammate(…, "died")` unchanged |
| MCP tool | `claude_crew/server.py:456` `kill_teammate` | FastMCP tool | gains `graceful` (and `flush_timeout`) arg threaded to `broker.kill_teammate` |

Resolution: the flush is a **single new pre-tombstone step** added to the explicit-kill /
shutdown paths only. `shutdown_all` calls a parallel variant (gather over `begin_graceful_termination`
under one `asyncio.wait_for`) rather than looping `kill_teammate`. The death path is left
structurally untouched. The teammate-side hook (`begin_graceful_termination`,
`has_memory_surface`) is a **single polymorphic method on the `Teammate` ABC** — `StubTeammate`
gets a no-op flush, `SdkTeammate` gets the real client-driven flush.

## Data / API Contracts

```python
# claude_crew/teammate.py — Teammate ABC additions
class Teammate(ABC):
    async def begin_graceful_termination(self, *, timeout: float) -> None:
        """Run one final memory-distillation turn, bounded by `timeout` seconds,
        then return. Best-effort: SDK errors / timeouts are swallowed (logged) so
        the caller can proceed to tombstone. Idempotent.
        Base default: no-op (immediately returns)."""

    def has_memory_surface(self) -> bool:
        """True iff this teammate can persist memory: has a memory scope AND the
        Write tool. Base default: False."""

# claude_crew/teammate.py — StubTeammate
#   has_memory_surface(): configurable via __init__ flag (default False);
#   begin_graceful_termination(): no-op that records self._flush_invoked = True
#   and returns immediately (lets broker orchestration tests assert the call
#   without an SDK).

# claude_crew/sdk_teammate.py — SdkTeammate
GRACEFUL_FLUSH_SECONDS: float = 90.0   # matches established hang-detection budget

#   self._terminating: bool                 # set when flush requested; gates loop
#   self._flush_complete: asyncio.Event     # signaled when flush turn returns
#   self._role_memory: Scope | None         # captured at __init__ for has_memory_surface
#   _GRACEFUL_FLUSH_SENTINEL: object        # idle-path inbox sentinel (distinct from _SHUTDOWN_SENTINEL)

#   has_memory_surface() -> bool:
#       self._role_memory in ("user","project","local") and "Write" in <effective tools>
#   begin_graceful_termination(timeout): set _terminating; if a turn is in flight
#       call client.interrupt() (bounded), else inbox.put(_GRACEFUL_FLUSH_SENTINEL);
#       await wait_for(_flush_complete.wait(), timeout); swallow TimeoutError.
#   _run_flush_turn(client): client.query(FLUSH_PROMPT, session_id) +
#       bounded _collect_response_text; finally _flush_complete.set().

FLUSH_PROMPT = (
    "You are about to be terminated. Per your memory instructions, persist "
    "anything worth saving now using the Write tool, then stop. Do nothing else."
)

# claude_crew/broker.py
async def kill_teammate(
    self, teammate_id: str, reason: str = "explicit",
    *, graceful: bool = True, flush_timeout: float = 90.0,
) -> None: ...
#   self._terminating: set[str]   # ids in the flush window; send() bounces them
async def shutdown_all(self, *, graceful: bool = True, flush_timeout: float = 90.0) -> None: ...

# claude_crew/server.py — MCP tool
async def kill_teammate(
    teammate_id: str, graceful: bool = True, flush_timeout: float = 90.0,
) -> dict[str, Any]: ...
```

## Design Decisions

- **Default-on, auto-skip when no memory surface** — *Rationale:* memory-less teammates have
  nothing to distill; flushing them wastes a turn and time. — *Carried into:*
  `Teammate.has_memory_surface()`; broker `kill_teammate` skip-branch; AT#7.
- **`graceful=False` forces an immediate hard kill** — *Rationale:* preserves today's behavior
  for wedged/urgent teardown and gives callers an explicit opt-out. — *Carried into:*
  `kill_teammate(graceful=...)` param; AT#8.
- **90s flush budget (`GRACEFUL_FLUSH_SECONDS`)** — *Rationale:* matches the established
  hang-detection budget in this codebase; on timeout fall through to the hard tombstone
  (partial Write output persists incrementally). — *Carried into:* `GRACEFUL_FLUSH_SECONDS`
  constant; `begin_graceful_termination(timeout=...)`; AT#9.
- **Interrupt the in-flight turn and flush immediately** — *Rationale:* the in-flight turn's
  result is already discarded on kill today (bounced as `teammate_dead`), so waiting for it adds
  latency for nothing. — *Carried into:* busy-path `client.interrupt()` in
  `begin_graceful_termination`; AT#2.
- **Flush runs inside the teammate's own `_run` task on the held client** — *Rationale:* a fresh
  `ClaudeSDKClient` session has no conversation to distill; only `_run` owns the live client. —
  *Carried into:* idle-sentinel + busy-interrupt branches; `_run_flush_turn`; AT#1, AT#2.
- **`shutdown_all` flushes in parallel under ONE shared deadline** — *Rationale:* N×90s
  sequential would make shutdown unbearably slow with a large crew. — *Carried into:*
  `shutdown_all` gather-under-`wait_for`; AT#11.
- **Death never flushes** — *Rationale:* the subprocess is already gone; there is no client to
  query. — *Carried into:* `_handle_teammate_death` routes straight to `_tombstone_teammate`;
  AT#12.
- **Intermediate terminating state bounces new sends** — *Rationale:* the flush turn must not be
  interleaved with real inbound traffic. — *Carried into:* `Broker._terminating` set checked in
  `send`; AT#10.
- **Reuse the injected memory guidance; no new distillation engine** — *Rationale:* the spawn-side
  memory addendum already tells the teammate what/how to save; the flush prompt only nudges it to
  act now. — *Carried into:* `FLUSH_PROMPT` text (leans on existing guidance); Out of Scope.
- **Tombstone runs unchanged AFTER the flush** — *Rationale:* preserves D2 tombstone-before-pop
  ordering and idempotency invariants. — *Carried into:* `kill_teammate` calls
  `_tombstone_teammate` after `begin_graceful_termination`; AT#6, AT#13.

## Edge Cases

- **No memory surface** (no `Write` tool / no memory scope) → skip flush, immediate tombstone
  (zero added cost). (AT#7)
- **`graceful=False`** → skip flush, immediate hard kill. (AT#8)
- **Unexpected DEATH** (`_handle_teammate_death`) → NEVER flush; subprocess already gone. (AT#12)
- **Flush turn hangs** → `flush_timeout` (90s) fires → proceed to hard tombstone; telemetry
  tombstone intact; partial Write output persists. (AT#9)
- **Flush turn raises an SDK error** → log, set `_flush_complete`, proceed to tombstone (never
  block teardown). (AT#9 covers the swallow-and-proceed contract via the timeout/error path.)
- **`client.interrupt()` unsupported / raises** → graceful-degrade: the wedge path
  (`_death_suspected`) applies as today; `begin_graceful_termination` still returns within
  `flush_timeout` and the caller proceeds to tombstone. (AT#9)
- **New send arrives during the flush window** → bounce with `teammate_dead` semantics. (AT#10)
- **Double kill** → idempotent: second call sees the teammate already tombstoned and raises
  `TeammateAlreadyDeadError` as today (no second flush). (AT#13)
- **Idle teammate** (blocked on `inbox.get()`) → flush sentinel injected to break the wait; flush
  turn runs; loop exits cleanly. (AT#1)
- **Busy teammate** (mid-`_handle_one_turn`) → interrupt breaks the drain; flush runs inline in
  the same task. (AT#2)
- **`StubTeammate` (non-SDK)** → no-op flush that records invocation so broker orchestration is
  testable without the SDK. (AT#5)

**If this feature affects displayed data, answer these:**
- This feature does not change displayed data. The teammate is tombstoned exactly as today after
  the flush; the dashboard sees the same lifecycle/telemetry tombstone. No new UI surface.

**If this feature retires, expires, or caps data, answer these:**
- This feature does not retire/expire/cap data. It adds writes (memory files) before teardown;
  the only consumer of those memory files is a future teammate spawned for the same role+project,
  which already reads them via the existing spawn-side `build_memory_section` injection
  (unchanged).

## Validation Contracts at Handoff Boundaries

| Boundary | Preconditions | Failure Behavior | Postconditions | Rollback |
|---|---|---|---|---|
| broker → teammate (`begin_graceful_termination`) | teammate alive, healthy, `has_memory_surface()` True, `graceful=True` | flush hang/SDK error swallowed; returns by `flush_timeout` | flush turn ran (or timed out) on the held client; `_flush_complete` set | none needed — tombstone proceeds regardless |
| teammate → memory file (flush turn `Write`) | injected memory guidance present in system prompt; `Write` granted | model may write nothing; partial write persists incrementally | memory file written/appended if the model judged content worth saving | file persists; no rollback (incremental Write) |
| broker `_terminating` window → `send` | id in `_terminating` set | n/a | inbound sends bounce as `teammate_dead` | id removed from `_terminating` on tombstone |

## Acceptance Tests

Test-construction note: tests that drive `SdkTeammate` branch logic without the real SDK use a
**fake client** stand-in — a lightweight object exposing async `query`, `interrupt`, and an async
`receive_response`/iterator that `_collect_response_text` can drain — injected in place of the
real `ClaudeSDKClient`. State this fake-client construction inline in any AT that needs it.

1. **(SdkTeammate, idle, happy)** Given an `SdkTeammate` with a memory scope + `Write`, whose
   `_run` loop is blocked on `inbox.get()`, and a fake client recording `query` calls, when
   `begin_graceful_termination(timeout=5)` is awaited, then a flush sentinel breaks the inbox
   wait, the fake client receives exactly one `query` carrying the flush prompt, `_flush_complete`
   is set, and the call returns before the timeout.
2. **(SdkTeammate, busy, happy)** Given an `SdkTeammate` mid-`_handle_one_turn` (fake client's
   `receive_response` is blocking), when `begin_graceful_termination(timeout=5)` is awaited, then
   `client.interrupt()` is called once to break the in-flight drain, the flush turn then runs
   inline on the same task/client (one flush `query`), `_flush_complete` is set, and the call
   returns before the timeout.
3. **(SdkTeammate, surface detection)** Given an `SdkTeammate` constructed with role memory scope
   `"project"` and `Write` in its effective tools, `has_memory_surface()` returns True; given a
   teammate with no memory scope, or a scope but no `Write`, it returns False.
4. **(SdkTeammate, gated live, happy)** Gated by `CLAUDE_CREW_LIVE_TESTS=1`: given a real
   `SdkTeammate` spawned with a project memory scope into a tmp project root, when it is flushed
   via `begin_graceful_termination`, then a memory file is created/appended under the role's
   project memory directory. Skipped unless the env var is set.
5. **(StubTeammate, no-op flush)** Given a `StubTeammate`, when `begin_graceful_termination(timeout=5)`
   is awaited, then it returns immediately, records `_flush_invoked == True`, and never raises;
   `has_memory_surface()` reflects its construction flag (default False).
6. **(Broker, graceful kill, happy)** Given a live teammate with `has_memory_surface()` True, when
   `broker.kill_teammate(id, graceful=True)` is awaited, then `begin_graceful_termination` is
   invoked exactly once BEFORE the teammate is tombstoned, and afterward the teammate is
   tombstoned with telemetry snapshot intact (`alive=False`, `died_at_wallclock` set, `exit_code`
   preserved).
7. **(Broker, auto-skip, no memory surface)** Given a live teammate with `has_memory_surface()`
   False, when `broker.kill_teammate(id, graceful=True)` is awaited, then `begin_graceful_termination`
   is NOT invoked and the teammate is tombstoned immediately.
8. **(Broker, graceful=False)** Given a live teammate with `has_memory_surface()` True, when
   `broker.kill_teammate(id, graceful=False)` is awaited, then no flush occurs and the teammate is
   hard-killed immediately.
9. **(Broker, flush timeout / sad)** Given a teammate whose `begin_graceful_termination` hangs
   past the budget (or raises), when `broker.kill_teammate(id, graceful=True, flush_timeout=0.2)`
   is awaited, then the call returns within a small bound after the timeout, the teammate is
   tombstoned (telemetry intact), and no exception escapes.
10. **(Broker, terminating-window bounce)** Given a teammate in its flush window (between flush
    start and tombstone), when another teammate `send`s it an envelope, then the sender receives a
    `teammate_dead` bounce and the flush turn is not interleaved with that traffic.
11. **(Broker, shutdown_all parallel)** Given N (≥3) live teammates each with `has_memory_surface()`
    True and each taking ~T seconds to flush, when `broker.shutdown_all(graceful=True,
    flush_timeout=B)` is awaited, then all N flushes run concurrently and total wall time is
    bounded by roughly one shared deadline B (not N×B), and all N teammates are tombstoned.
12. **(Broker, death never flushes)** Given a live teammate with `has_memory_surface()` True, when
    `_handle_teammate_death(id, exit_code)` runs (unexpected death), then `begin_graceful_termination`
    is NEVER invoked and the teammate is tombstoned with `lifecycle_event_name == "died"`.
13. **(Broker, double kill idempotent)** Given a teammate already tombstoned by a graceful kill,
    when `kill_teammate(id)` is awaited again, then it raises `TeammateAlreadyDeadError` and no
    second flush occurs.
14. **(Server MCP tool)** Given the FastMCP `kill_teammate` tool, when called with
    `graceful=False` (and a `flush_timeout`), then those arguments are threaded through to
    `broker.kill_teammate`; the default (no args) preserves `graceful=True`. Verified with a
    stub/mocked broker that records the call kwargs.
15. **(Doc-sync)** `doc/sdk-teammate-wiring.md` no longer claims memory is "Auto-distilled at
    `kill_teammate` exit"; a grep over `doc/sdk-teammate-wiring.md` for the stale phrase
    `Auto-distilled at` returns no matches, and the corrected line accurately describes the
    graceful-flush behavior.

## Test Command

Prerequisites: `uv sync` installs all dependencies (pytest is the only test-framework import;
every test file imports only `claude_crew.*`, the stdlib `asyncio`, and `pytest` — all in
`pyproject.toml`). Tests run in stub mode by default (`conftest.py` sets
`CLAUDE_CREW_TEAMMATE_MODE=stub`); the live test (AT#4) is gated behind `CLAUDE_CREW_LIVE_TESTS=1`
and skipped here. Per CLAUDE.md ("validate the whole suite when changing widely-consumed
behavior") this mutates the shared termination path, so the gate runs the FULL suite, not a
keyword-filtered subset.

```bash
uv sync && uv run pytest
```

## Out of Scope

- Memory distillation on unexpected DEATH (the subprocess is already gone — there is no client to
  flush).
- Any new summarization/distillation engine — reuse the existing injected-guidance + `Write`-tool
  self-distill mechanism.
- Changes to spawn-side memory injection (`multi-scope-agent-memory`: `build_memory_section`,
  `ensure_write_tool`, `memory_dir`) — those stay exactly as is.
- Peer-to-peer or autonomous termination — the coordinator/shutdown still owns the kill decision.
- Changing the displayed dashboard tombstone shape or adding a new UI surface.
- A configurable flush prompt or per-role prompt customization — one fixed `FLUSH_PROMPT`.

## Assumptions

- **Effective-tools source for `has_memory_surface()`** — *Default:* read the role's effective
  tool list the same way `_run` builds it (`role_def.tools` after `ensure_write_tool` patching,
  which the memory path already applies at `__init__`), so a memory-scoped teammate always reports
  `Write` present. — *Rationale:* the memory init path already appends `Write` for any memory
  scope, so scope presence is the load-bearing signal; checking `Write` too is belt-and-suspenders.
- **`flush_timeout` default value** — *Default:* `90.0` seconds, surfaced as `GRACEFUL_FLUSH_SECONDS`
  and as the default kwarg on `kill_teammate`/`shutdown_all`/the MCP tool. — *Rationale:* matches
  the codebase's established hang-detection budget (locked decision 2).
- **Idle vs busy detection** — *Default:* use `_current_turn_started_at_wallclock is not None`
  (already maintained by `_begin_turn`/`_end_turn`) as the busy signal; otherwise treat as idle. —
  *Rationale:* reuses existing turn-state bookkeeping rather than adding a parallel flag.
- **Flush sentinel object** — *Default:* a module-level `_GRACEFUL_FLUSH_SENTINEL = object()`
  distinct from `_SHUTDOWN_SENTINEL`, recognized in the `_run` loop. — *Rationale:* keeps the
  idle break-out path separate from shutdown so the loop runs the flush turn rather than exiting.
- **`shutdown_all` keeps its no-arg call sites working** — *Default:* `graceful=True` /
  `flush_timeout=90.0` defaults so existing callers (server teardown) get graceful behavior with
  no signature break. — *Rationale:* additive, backward-compatible.
- **`_flush_complete` set in a `finally`** — *Default:* the flush turn always sets the event in a
  `finally`, even on error/timeout, so `begin_graceful_termination`'s waiter never hangs beyond
  its own `wait_for`. — *Rationale:* never block teardown (edge cases).

## Open Questions

- (none)

## Validation

After feature-review PASS, the coordinator runs the full stub suite (proves the broker
orchestration, skip/timeout branches, and parallel shutdown without the SDK) and, as the
user-visible proof, the gated live test that a real teammate writes a memory file when flushed.

Automatable gate (stub-mode, full suite — exercises AT#1-3, 5-15):

```bash
uv sync && uv run pytest
```

User-visible proof (gated live, AT#4 — requires SDK auth; run manually): set
`CLAUDE_CREW_LIVE_TESTS=1` and run `uv run pytest tests/test_live_graceful_flush.py`. PASS =
a real SDK teammate spawned with a project memory scope writes/appends a memory file under its
role's project memory directory when flushed via `begin_graceful_termination`, and the file is
non-empty.

## Task Breakout

```yaml
tasks:
  - name: teammate-base-graceful-hook
    description: |
      Add `begin_graceful_termination(*, timeout)` (no-op base default that returns
      immediately) and `has_memory_surface()` (base default False) to the Teammate ABC.
      Implement StubTeammate's overrides: no-op flush that records `self._flush_invoked = True`
      and returns immediately; `has_memory_surface()` driven by an __init__ flag (default
      False). This is the seam the broker calls; SdkTeammate fills in the real flush in
      teammate-sdk-flush.
    dependsOn: []
    acceptanceTests: [5]
    taskTouches: ["claude_crew/teammate.py", "tests/test_graceful_termination_base.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_graceful_termination_base.py

  - name: teammate-sdk-flush
    description: |
      Implement the real flush on SdkTeammate: GRACEFUL_FLUSH_SECONDS=90, _terminating flag,
      _flush_complete Event, _GRACEFUL_FLUSH_SENTINEL, _role_memory captured at __init__,
      has_memory_surface() (memory scope + Write), begin_graceful_termination (idle: inject
      sentinel; busy: client.interrupt(); await _flush_complete bounded by timeout, swallow
      TimeoutError/SDK errors), _run_flush_turn (client.query(FLUSH_PROMPT)+bounded drain,
      _flush_complete.set() in finally), and the _run-loop sentinel handling. Add the gated
      (CLAUDE_CREW_LIVE_TESTS=1) live test proving a real teammate writes a memory file on flush.
      Use a fake client stand-in for the non-live branch tests.
    dependsOn: [teammate-base-graceful-hook]
    acceptanceTests: [1, 2, 3, 4]
    taskTouches: ["claude_crew/sdk_teammate.py", "tests/test_graceful_flush_sdk.py", "tests/test_live_graceful_flush.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_graceful_flush_sdk.py

  - name: broker-graceful-kill
    description: |
      Add `graceful`/`flush_timeout` params to broker.kill_teammate; insert the pre-tombstone
      flush step (skip when not graceful, teammate not alive, or no memory surface). Add the
      `_terminating` id set and bounce new sends arriving during the flush window. Leave
      _handle_teammate_death routing straight to _tombstone_teammate (no flush). Preserve D2
      ordering, double-kill idempotency, and timeout fall-through to hard tombstone.
    dependsOn: [teammate-base-graceful-hook]
    acceptanceTests: [6, 7, 8, 9, 10, 12, 13]
    taskTouches: ["claude_crew/broker.py", "tests/test_graceful_kill_broker.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_graceful_kill_broker.py

  - name: broker-shutdown-all-parallel
    description: |
      Add `graceful`/`flush_timeout` params to broker.shutdown_all and flush all memory-surface
      teammates in PARALLEL under ONE shared deadline (gather over begin_graceful_termination
      wrapped in a single asyncio.wait_for), then tombstone all. Not N×timeout sequential.
      Serialized after broker-graceful-kill because both edit broker.py.
    dependsOn: [broker-graceful-kill]
    acceptanceTests: [11]
    taskTouches: ["claude_crew/broker.py", "tests/test_graceful_shutdown_all.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_graceful_shutdown_all.py

  - name: server-graceful-arg
    description: |
      Thread `graceful` (default True) and `flush_timeout` (default 90.0) args through the
      FastMCP kill_teammate tool to broker.kill_teammate; update the tool docstring to mention
      the graceful memory flush. Default (no args) preserves graceful=True.
    dependsOn: [broker-graceful-kill]
    acceptanceTests: [14]
    taskTouches: ["claude_crew/server.py", "tests/test_server_graceful.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_server_graceful.py

  - name: doc-sync-wiring
    description: |
      Fix doc/sdk-teammate-wiring.md line ~109: remove the false "Auto-distilled at
      kill_teammate exit" claim and replace it with an accurate description of the graceful
      termination memory flush (final bounded turn on healthy explicit kill / shutdown,
      skipped on death and for memory-less teammates).
    dependsOn: []
    acceptanceTests: [15]
    taskTouches: ["doc/sdk-teammate-wiring.md"]
    implementationKind: documentation
    testCommand: |
      ! grep -q "Auto-distilled at" doc/sdk-teammate-wiring.md
```

## Design Notes

- Bound any open-ended async-iterator drains in tests with `asyncio.wait_for` (repo convention,
  CLAUDE.md) — both for the fake-client `receive_response` drain and the real flush drain.
- The flush turn deliberately leans on the spawn-side memory addendum already in the system
  prompt; `FLUSH_PROMPT` must NOT re-explain save rules (locked design).
- `_handle_teammate_death` is structurally untouched — the cleanest way to honor "death never
  flushes" is to keep the death path calling `_tombstone_teammate` directly and add the flush
  only to the explicit-kill / shutdown paths.
- Tombstone telemetry (D2 ordering, frozen `TeammateInfo`, tool-event capture) is unchanged; the
  flush is purely a pre-step gated before tombstone step 1.
