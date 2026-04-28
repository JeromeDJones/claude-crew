# Feature: get_messages Long-Poll

**Status**: Phase 2 (design locked, ready for builder)
**Created**: 2026-04-27
**Feature**: #9 in PRODUCT-VISION.md pipeline
**Size**: S — single-param addition, pure asyncio, no new protocol

---

## Phase 1: Research & Requirements

### Problem Statement

During team builds the lead polls `get_messages` every 2–3 s. With five builders running, the vast majority of those calls return `{"messages": [], "next_seq": <unchanged>}` — empty responses that burn lead context, add 2–3 s of latency between a teammate finishing a task and the lead dispatching the next one, and force the lead to maintain a polling cadence rather than reacting to events.

We want the lead to **wait for work**, not spin for it. Add a `wait_seconds` parameter to `get_messages`: when it's > 0 and no messages are queued at call time, the server blocks until one arrives or the timeout elapses. Default `0` preserves today's exact behavior bit-for-bit.

This is the last expected addition to the post-MVP substrate (Features #6 + #7 + #8 + #9) before the substrate is locked for the MMM-4b real-task validation run.

### Prior Art

- **`broker.send` (`broker.py:322-353`)** appends every stamped envelope to `self._log` *and* puts it on `self._inboxes[recipient]`. The lead inbox queue (`_inboxes[LEAD_ID]`) is created in `__init__` (line 68) but **never drained** — `broker.get_messages` reads from `_log`, not the queue. The lead-side queue is dead weight that grows unbounded across a long crew lifetime. **Cleanup is in scope for this feature** (Q3 below): drop the LEAD entry from `_inboxes` init and gate the put in `send` so LEAD-bound sends notify the Condition only, never enqueue. Reference scan (2026-04-27) confirmed: only one test reads `_inboxes`, and it reads a teammate id, not LEAD.
- **`broker.get_messages` (`broker.py:393-402`)** is sync. It filters `_log` by `recipient` and `seq > since_seq`. We do not need to make it async; the long-poll wait happens in the *server tool handler*, around the existing call.
- **`server.get_messages` tool (`server.py:125-141`)** is already an async FastMCP handler. Adding `await` inside it is free.
- **Feature #7 / #8 substrate** introduced no new asyncio primitives in the broker. The broker is single-event-loop, single-writer; no thread synchronization needed. asyncio.Condition is the right primitive for "signal that broker state has changed."

### Architectural Decision: Condition, not Queue, not Event

The briefing asks the load-bearing question: *the lead inbox queue already gets every envelope put onto it — why not wait on that queue directly?* Three options were on the table.

**Option A — `asyncio.Queue.get()` on the lead inbox.** Tempting because the plumbing already exists. **Rejected.** Three reasons:

1. **Consumption side-effect.** `Queue.get()` *removes* the item. The same envelope is also in `_log`, so the next `get_messages` would still surface it via the existing read — but the queue would mutate as a side effect of long-poll. The lead-inbox queue currently grows unbounded; using it as a wait primitive would also drain it, which is a behavior change disguised as a feature addition. Two concerns in one PR.
2. **No multi-waiter semantics.** `Queue.get()` wakes exactly one waiter per `put`. Today there's only one lead, so it's fine — but observability tools, debugging UIs, or a future "secondary lead" pattern would silently break. Picking a primitive that breaks under fan-out is the wrong default.
3. **Couples wait-correctness to leak-status.** If the queue happens to have stale items (and today, every item ever sent to the lead is stale), `get()` returns immediately even though no *new* message arrived. The handler re-reads `_log` and returns empty — semantically OK ("we may return early") but architecturally bad: the wait correctness depends on whether someone has cleaned up the queue lately.

**Option B — `asyncio.Event`.** Sticky: once set, stays set until cleared. Works for the single-waiter case if you `clear()` before reading and re-check after. **Rejected.** The clear/recheck dance is a TOCTOU footgun for any future multi-waiter use, and Event was clearly designed for "one-shot signal," not "state changed N times."

**Option C — `asyncio.Condition`. Adopted.** The right primitive for "wait until broker state has changed":

- **No consumption side-effect.** Notifying does not interact with `_log` or `_inboxes`. The lead-inbox queue leak is left exactly as it is today (not made better or worse by this feature).
- **Multi-waiter correct by construction.** `notify_all()` wakes every current waiter; each re-reads `_log` independently with its own `since_seq` cursor.
- **Cancellation-safe.** `Condition.wait()` re-acquires the lock during `CancelledError` unwind, no leaked state if the MCP transport closes the request.
- **Cost is trivial.** One `async with`/`notify_all` per send to LEAD. On a single-threaded event loop, lock acquisition is a few state toggles — sub-microsecond.

The Condition lives in `broker.Broker` (it's broker state). The server tool handler calls a thin broker helper (`wait_for_lead_message`) so the lock acquisition stays encapsulated where it belongs.

### Success Criteria

- [ ] **SC-1 (additive parameter).** `get_messages` MCP tool gains `wait_seconds: float = 0.0`. With the default, behavior is **bit-for-bit identical** to today: synchronous read of `_log`, immediate return. Existing callers and existing tests untouched.

- [ ] **SC-2 (immediate-return when messages already present).** When `wait_seconds > 0` *and* there is at least one envelope in `_log` with `recipient == LEAD_ID and seq > since_seq` at call time, the handler returns immediately without touching the Condition. Verified by an integration test that pre-populates a message, calls `get_messages(wait_seconds=5)`, and asserts wall-clock duration < 100 ms.

- [ ] **SC-3 (block until message arrives).** When `wait_seconds > 0` and no messages match the cursor, the handler blocks. When a `send` to LEAD lands during the wait, the handler wakes, re-reads `_log`, and returns the new message(s). Verified by scheduling a delayed `send` via `asyncio.create_task` while a long-poll is in flight; assert messages are returned and total duration is roughly the send delay (with generous slack for CI).

- [ ] **SC-4 (timeout returns empty cleanly).** When `wait_seconds > 0` and no message arrives within the window, the handler returns `{"messages": [], "next_seq": <since_seq unchanged>}`. No exception, no error envelope, identical shape to "no messages waiting." Verified by a test with a short `wait_seconds` (e.g., 0.2 s) and no producers.

- [ ] **SC-5 (notify-on-send only for LEAD recipient).** `Broker.send` notifies the lead-message Condition only when `stamped.recipient == LEAD_ID`. Sends between teammates do not acquire the Condition lock. Verified by a unit test on `Broker.send` that asserts no lock contention on teammate-to-teammate messages.

- [ ] **SC-6 (server-layer cap).** `wait_seconds` is silently capped at `MAX_WAIT_SECONDS = 600.0` (10 minutes) at the server tool layer. Negative values are treated as `0` (no wait). The broker helper itself has no cap — caps belong to the transport-facing layer. Verified by a test passing `wait_seconds=9999`, asserting the cap is applied (e.g., by mocking the helper to assert it was called with `600.0`, or by passing a value just over the cap and asserting the call returns within ~605 s). **Rationale (final 2026-04-27, Jerome):** an Opus teammate turn can run minutes; we want enough headroom that mid-turn timeouts genuinely don't happen. 10 minutes covers any realistic single-turn duration with margin. The lead can always cancel, and FastMCP/stdio has no transport timeout, so a held request just keeps the channel quiet.

- [ ] **SC-11 (LEAD inbox queue removal — co-architect, in scope this feature).** `Broker.__init__` no longer seeds `_inboxes` with a LEAD entry: `self._inboxes: dict[str, asyncio.Queue] = {}`. `Broker.send` is restructured so the LEAD-bound path notifies the Condition and skips `_inboxes` entirely; the teammate-bound path keeps the existing put. Verified by a test asserting `LEAD_ID not in broker._inboxes` after multiple lead-bound sends, and that no `KeyError` is raised on those sends. Reference scan confirmed only one test (`test_broker.py:409`) touches `_inboxes`, and it reads a teammate id — unaffected.

- [ ] **SC-7 (cancellation-safe).** If the MCP request is cancelled mid-wait (e.g., transport closes), the underlying asyncio task receives `CancelledError`, `Condition.wait()` re-acquires its lock during unwind, and no broker state is corrupted. Verified by a test that creates a long-poll task, cancels it, and confirms (a) the task raises `CancelledError`, (b) a subsequent `send` to LEAD does not deadlock, (c) a fresh `get_messages` call works.

- [ ] **SC-8 (no regression on existing get_messages tests).** All existing `test_broker.py` and `test_server.py` get-messages tests pass unchanged.

- [ ] **SC-9 (non-blocking when wait_seconds=0).** When `wait_seconds == 0`, the handler does **not** acquire the Condition lock and does not touch any new code path beyond the parameter check. Verified by code inspection during sentinel review (no test needed; this is a structural assertion).

- [ ] **SC-10 (shutdown wakes pending waiters — co-architect).** `Broker.shutdown_all` calls `notify_all` on the lead Condition before tearing down the sink. Pending long-polls wake, re-read `_log` (likely empty), and return cleanly rather than hanging until their `wait_seconds` cap. Verified by spawning a long-poll, calling `shutdown_all`, asserting the long-poll returns within ~100 ms (well under any reasonable `wait_seconds`).

### Questions

- [x] **Q1 (server-layer cap value) — RESOLVED (final):** `MAX_WAIT_SECONDS = 600.0` (10 minutes). Jerome 2026-04-27 final call after walking 60 → 120 → 600 as the eliminate-mid-turn-timeouts argument sharpened. Headroom over realistic worst-case turn length so mid-turn timeouts genuinely don't happen for any teammate (Opus, Sonnet, or hung). Lead can cancel; FastMCP/stdio has no transport timeout; held request just keeps the channel quiet.

- [x] **Q2 (return shape — `timed_out` flag) — RESOLVED:** No flag. Lead knows what it asked for; empty `messages` list is semantically sufficient. Return shape unchanged from today.

- [x] **Q3 (lead inbox queue leak) — RESOLVED (revised, in scope):** Jerome 2026-04-27 override: fix in this feature. The "two concerns" framing applied to using the queue *as the wait primitive* (which would have coupled wait correctness to leak status) — not to deleting dead code that pairs naturally with adding the Condition. We're already touching `broker.py`; reference scan confirmed nothing reads `_inboxes[LEAD_ID]`; deletion alongside the Condition addition is a cleanup, not a competing concern. Captured as SC-11. BACKLOG entry routed to "fixed in #9."

- [x] **Q4 (broker.get_messages stays sync) — RESOLVED:** Confirmed. No signature change to `Broker.get_messages`. The wait happens in the server tool handler around the existing sync call.

- [x] **Q5 (loop-on-spurious-wake) — RESOLVED:** No loop. Every `notify_all()` corresponds to a real `send` that appended to `_log` with strictly-monotonic `seq`, so any current waiter's cursor will be satisfied. The shutdown notify is the only intentional notify-without-message, and we want it to return fast. Looping would reintroduce polling.

- [x] **Q6 (asyncio.wait_for vs asyncio.timeout — added at Phase 1 gate) — RESOLVED:** Use `asyncio.timeout()` context manager. `pyproject.toml` requires `>=3.12`, so it's available. Sidesteps the `wait_for`-wraps-coroutine-in-Task semantics question entirely. See helper code in §Phase 2 / Data Contracts.

### Constraints & Dependencies

- Touches: `claude_crew/broker.py` (add Condition + helper, notify in send + shutdown_all, drop LEAD entry from `_inboxes` init, gate the put in send), `claude_crew/server.py` (extend `get_messages` tool signature with `wait_seconds`, call helper), `tests/test_broker.py`, `tests/test_server.py`. No new files.
- Breaking changes: none externally. Internally, `LEAD_ID not in broker._inboxes` is now invariant — verified to break no caller during reference scan (only `test_broker.py:409` touches `_inboxes`, and it reads a teammate id).
- No new package dependencies. `asyncio.Condition` and `asyncio.timeout()` are stdlib (3.11+, project requires 3.12).
- D-rules invariants from Features #6/#7/#8 (telemetry, tombstone, subagent tracking) are not touched by this feature.
- Out of scope: generalizing to teammate-side long-poll (current `get_messages` tool is hardcoded to LEAD; teammates use their inbox queues directly via SdkTeammate's run loop and don't poll).

**Gate**: Questions answered, success criteria measurable, constraints documented, user confirmed.

---

## Phase 2: Design & Specification (preliminary — finalize after Phase 1 gate)

### Architecture Overview

Three surfaces:

1. **`broker.py`** — gain `_lead_message_condition: asyncio.Condition` in `__init__`; **drop the `LEAD_ID` entry from `_inboxes` init**; restructure `send` so LEAD-bound envelopes notify the Condition (no `_inboxes` interaction) and teammate-bound envelopes keep the existing put; in `shutdown_all`, notify before sink close; new helper `wait_for_lead_message(timeout)`.
2. **`server.py`** — `get_messages` tool gains `wait_seconds: float = 0.0`; on `wait_seconds > 0` with no immediate matches, calls broker helper, then re-reads. Module-level `MAX_WAIT_SECONDS = 600.0`.
3. **`tests/`** — new `test_broker.py` cases for the helper + notify + LEAD inbox removal; new `test_server.py` cases for the tool param including SC-2/3/4/6/7/10/11.

No new files. No new MCP tools. No new dependencies.

### Data / API Contracts

**Broker `__init__` change** (line 68):
```python
# Before:
self._inboxes: dict[str, asyncio.Queue] = {LEAD_ID: asyncio.Queue()}

# After:
self._inboxes: dict[str, asyncio.Queue] = {}
self._lead_message_condition: asyncio.Condition = asyncio.Condition()
```

**Broker `send` restructure** (replaces line 352 `await self._inboxes[stamped.recipient].put(stamped)`):
```python
self._seen_ids.add(stamped.id)
self._log.append(stamped)
self._sink.write_envelope(stamped.to_dict())
if stamped.recipient == LEAD_ID:
    # LEAD has no inbox queue; readers consume from _log via get_messages.
    # Notify the long-poll Condition so any waiting get_messages call wakes.
    async with self._lead_message_condition:
        self._lead_message_condition.notify_all()
else:
    await self._inboxes[stamped.recipient].put(stamped)
return stamped
```

The notify lives **after** `_log.append` and `sink.write_envelope` so any waiter that wakes and re-reads `_log` is guaranteed to see the new envelope. Putting it before the append would be a TOCTOU bug.

**Broker `shutdown_all` addition** (before `self._sink.close()` at line 318):
```python
async with self._lead_message_condition:
    self._lead_message_condition.notify_all()
```

**New broker helper:**
```python
async def wait_for_lead_message(self, timeout: float) -> None:
    """Block until any send to LEAD fires the Condition, or timeout.

    No-op if timeout <= 0. Returns silently on timeout. Caller is
    responsible for re-reading get_messages() after this returns;
    this helper does not touch _log.
    """
    if timeout <= 0:
        return
    async with self._lead_message_condition:
        try:
            async with asyncio.timeout(timeout):
                await self._lead_message_condition.wait()
        except TimeoutError:
            pass
```

**Why `asyncio.timeout()` rather than `asyncio.wait_for()`** (co-architect, Phase 1 gate Q from Jerome):
`pyproject.toml` requires Python `>=3.12`, so `asyncio.timeout()` (added 3.11) is available. In 3.12, `asyncio.wait_for()` wraps its coroutine argument in a new Task and cancels that Task on timeout — fine in practice for `Condition.wait()` because the underlying `asyncio.Lock` is not task-owned since 3.10, but "fine in practice" is the wrong epistemic posture for broker primitives. `asyncio.timeout()` is a context manager that propagates cancellation through the *current* task without an extra wrapper Task, sidestepping the question entirely. It's also the idiomatic 3.11+ form.

The `except TimeoutError` (builtin, since 3.11 `asyncio.TimeoutError` is an alias) is correct for both — but with `asyncio.timeout()` there's no Task-wrapping semantics to reason about.

**Server tool signature change:**
```python
@mcp.tool()
async def get_messages(
    since_seq: int = 0,
    limit: int = 100,
    wait_seconds: float = 0.0,
) -> dict[str, Any]:
    """Return messages addressed to the lead with seq > since_seq.

    Args:
        since_seq: Cursor; pass the largest seq you've already seen.
        limit: Maximum messages to return (default 100).
        wait_seconds: If > 0 and no messages are waiting, block up to this
            many seconds for one to arrive. Default 0 returns immediately
            (existing behavior). Capped at 600 s (10 min) server-side;
            negative values treated as 0.
    """
```

**Server tool body (sketch):**
```python
msgs = broker.get_messages(recipient=LEAD_ID, since_seq=since_seq, limit=limit)
if not msgs and wait_seconds > 0:
    capped = min(max(wait_seconds, 0.0), MAX_WAIT_SECONDS)
    await broker.wait_for_lead_message(capped)
    msgs = broker.get_messages(recipient=LEAD_ID, since_seq=since_seq, limit=limit)
next_seq = msgs[-1].seq if msgs else since_seq
return {"messages": [m.to_dict() for m in msgs], "next_seq": next_seq}
```

`MAX_WAIT_SECONDS = 600.0` defined as a module-level constant in `server.py`.

**Return shape:** unchanged. `{"messages": [...], "next_seq": int}`.

### Edge Cases & Failure Modes

| # | Case | Behavior |
|---|------|----------|
| 1 | `wait_seconds = 0` (default) | No new code path. Existing behavior bit-for-bit. |
| 2 | `wait_seconds = -5` | Treated as 0 by server cap. Helper also no-ops on `<= 0`. |
| 3 | `wait_seconds = 9999` | Capped to 600 s (10 min) by server. |
| 4 | Messages already in `_log` matching cursor | Return immediately, do not touch Condition. |
| 5 | `send` lands during wait | `notify_all` wakes waiter; waiter re-reads `_log`, returns the new message. |
| 6 | No `send` lands within `wait_seconds` | Helper returns silently on TimeoutError; outer handler returns `{messages: [], next_seq: <unchanged>}`. |
| 7 | MCP request cancelled mid-wait | `CancelledError` propagates; `Condition.wait()` re-acquires lock during unwind. No leaked state. |
| 8 | `Broker.shutdown_all` while wait pending | Pre-close `notify_all` wakes waiter; re-read finds no match; returns empty cleanly. SC-10. |
| 9 | Two concurrent long-poll callers (hypothetical — single-lead today) | `notify_all` wakes both; each independently re-reads `_log` with its own `since_seq`. No starvation. |
| 10 | `send` to non-LEAD recipient during a lead long-poll | No notify fires; lead waiter stays blocked. Correct: the message isn't for the lead. |
| 11 | Duplicate envelope (`env.id in _seen_ids`) | `send` returns `None` early (line 329) *before* the new notify code. No spurious wake. |
| 12 | Send raises `TeammateAlreadyDeadError` for non-LEAD recipient | Notify code never reached (exception thrown earlier). Correct. |
| 13 | `bounce_dead` envelope addressed to LEAD | Bounce envelope is sent to `env.sender`, which for lead-originated messages is LEAD itself. Notify fires correctly — lead sees the bounce. |
| 14 | LEAD-bound send post-cleanup (no `_inboxes[LEAD_ID]`) | The LEAD branch of `send` skips `_inboxes` entirely — no `KeyError`. SC-11. |
| 15 | Teammate-to-LEAD send (e.g., teammate replying) | Same LEAD branch — notifies Condition, does not enqueue. Lead reads via `_log`. |

### BDD Scenarios for Phase 3

**Scenario 1 — Happy path: messages already present return immediately.**
```
Given the lead has spawned a teammate
And the teammate has sent one message to the lead (seq=1, in _log)
When the lead calls get_messages(since_seq=0, wait_seconds=5.0)
Then the response contains the one message
And the response is returned within 100 ms (well under wait_seconds)
And next_seq == 1
```

**Scenario 2 — Block-and-wake: long-poll returns when message arrives mid-wait.**
```
Given the lead has spawned a teammate
And the lead's _log contains no messages addressed to LEAD
When the lead calls get_messages(since_seq=0, wait_seconds=5.0)
And after 200 ms the teammate sends a message to the lead
Then the response contains the one new message
And the call duration is approximately 200 ms (with CI slack ±300 ms)
And next_seq == 1
```

**Scenario 3 — Timeout returns empty cleanly.**
```
Given the lead has spawned a teammate
And no message will be sent during the test
When the lead calls get_messages(since_seq=0, wait_seconds=0.2)
Then the response contains an empty messages list
And next_seq == 0 (unchanged from since_seq)
And the call duration is approximately 200 ms
And no error is raised
```

**Scenario 4 — Backwards compatibility: wait_seconds=0 preserves existing behavior.**
```
Given the lead's _log contains no messages
When the lead calls get_messages(since_seq=0)  # no wait_seconds specified
Then the response contains an empty messages list
And the call returns within 50 ms
And the broker's lead Condition was never acquired
```

**Scenario 5 — Server-layer cap silently truncates to 600 s.**
```
Given the lead's _log contains no messages
And no message will be sent during the test
When the lead calls get_messages(since_seq=0, wait_seconds=9999)
Then the broker.wait_for_lead_message helper is invoked with timeout=600.0
   (assert via mock — do NOT actually wait 10 minutes in the test suite)
And the response contains an empty messages list
```

Note: the cap test must mock `broker.wait_for_lead_message` and assert the
timeout argument rather than letting the real wait elapse — running 10-minute
tests in CI is unacceptable. A second test passing `wait_seconds=0.1` exercises
the real wait path.

**Scenario 6 — Cancellation safety.**
```
Given the lead has started a long-poll get_messages(wait_seconds=10.0) as a task
When the task is cancelled after 100 ms
Then the task raises CancelledError
And a subsequent send to LEAD does not deadlock
And a subsequent get_messages(wait_seconds=0) returns the new message
```

**Scenario 7 — Shutdown wakes pending waiter.**
```
Given the lead has started a long-poll get_messages(wait_seconds=30.0) as a task
When broker.shutdown_all is called after 100 ms
Then the long-poll returns within ~200 ms (well under 30 s)
And the response contains an empty messages list
```

**Scenario 8 — LEAD inbox queue is removed (no enqueue on lead-bound send).**
```
Given a fresh broker
Then broker._inboxes does not contain LEAD_ID
When the lead has spawned a teammate
And the teammate sends three messages to the lead via broker.send
Then no KeyError is raised
And broker._inboxes still does not contain LEAD_ID
And broker.get_messages(LEAD_ID, since_seq=0) returns all three messages from _log
```

### Risks & Open Questions for Jerome

1. **Q1 — MAX_WAIT_SECONDS value.** RESOLVED: 600 s / 10 min (Jerome 2026-04-27 final).
2. **Q2 — `timed_out: bool` in response.** RESOLVED: no. Empty messages list is sufficient.
3. **Q3 — Lead inbox queue leak.** RESOLVED: in scope this feature (SC-11). Jerome 2026-04-27 override.
4. **Q6 — `asyncio.wait_for` vs `asyncio.timeout()`.** RESOLVED: use `asyncio.timeout()` — see helper code.
5. **Test infrastructure for time-based assertions.** Wall-clock `time.monotonic()` deltas with generous slack (±300 ms) are the simplest path. Consider whether we want a pytest fixture for "schedule a delayed send and assert wake timing." Probably overkill for one feature; inline `asyncio.create_task` with a small sleep is fine.
6. **MCP transport behavior on a 600 s (10 min) held request.** FastMCP over stdio has no transport-level timeout, so a held request just keeps the JSON-RPC channel quiet. Confirmed by code inspection — but at 10 minutes we should validate behavior in the manual smoke run before marking Phase 4 done. Specifically: confirm the MCP client doesn't have its own per-request timeout that fires before the server cap. Add to the manual validation checklist.

### Phase Gate

Phase 1 gate: Q1, Q2, Q3 resolved with Jerome before Phase 2 finalizes.
Phase 2 gate: data contracts approved, BDD scenarios approved.
Phase 3 gate: BDD tests written + red, then green.
Phase 4 gate: sentinel pass + manual validation that an actual lead long-poll works against an actual teammate.
