# Build Report: graceful-termination-memory-flush (cycle 0)

**Verdict:** PASS
**Cycle:** 0
**Generated:** 2026-05-31T00:00:00Z

## Tests Run

- **Declared command:** `uv run pytest tests/test_graceful_flush_sdk.py`
- **Actual command:** `uv run pytest tests/test_graceful_flush_sdk.py`
- **Divergence reason:** None
- **Exit code:** 0
- **Passed:** 10 / **Failed:** 0 / **Total:** 10

## Failing Tests

_None._

## Uncovered / Partially Covered Tests

- AT-4: Live test (`test_live_graceful_flush.py`) skipped by design — requires `CLAUDE_CREW_LIVE_TESTS=1` and real SDK auth. No non-live coverage gap: the file exists and the test is gated correctly.

## Files Changed

```
M	claude_crew/sdk_teammate.py
?? tests/test_graceful_flush_sdk.py
?? tests/test_live_graceful_flush.py
```

## Scope-Creep Entries (this cycle)

_None._

## Blocker Reason

N/A — PASS verdict.

## Full Suite Notes

Full suite (`uv run pytest --ignore=tests/test_shutdown_signals.py`): 1277 passed, 33 skipped, 1 xfailed, 35 warnings.

The single failure (`test_sigterm_triggers_clean_exit_and_deregister` in `tests/test_shutdown_signals.py`) is a pre-existing environment failure (the `claude-crew` process fails to register within 15s on this machine). Verified pre-existing by running the same test on a stashed HEAD — same failure, independent of this PR's changes.

## Implementation Summary

### `claude_crew/sdk_teammate.py` — SdkTeammate flush implementation

**Constants added:**
- `GRACEFUL_FLUSH_SECONDS = 90.0` — matches codebase hang-detection budget
- `_GRACEFUL_FLUSH_SENTINEL = object()` — distinct from `_SHUTDOWN_SENTINEL`
- `FLUSH_PROMPT` — concise nudge reusing spawn-side memory addendum guidance

**`__init__` additions:**
- `self._role_memory` — captured after `role_memory` is computed (and after `ensure_write_tool` may patch tools; `memory` scope is unaffected by that patch)
- `self._terminating: bool = False` — gates the retry loop in `_handle_one_turn`
- `self._flush_complete: asyncio.Event` — signaled in `_run_flush_turn`'s `finally`
- `self._client: Any | None = None` — held reference to live `ClaudeSDKClient` for interrupt

**`has_memory_surface()`** — checks `_role_memory in ("user", "project", "local")` AND `"Write"` in `self._agents[role].tools` (belt-and-suspenders per spec)

**`begin_graceful_termination(timeout)`** — idempotent via `_flush_complete.is_set()` guard; sets `_terminating`, interrupts any in-flight turn (busy path), injects `_GRACEFUL_FLUSH_SENTINEL` into inbox (both paths), awaits `_flush_complete` bounded by `timeout`, swallows `TimeoutError`/exceptions

**`_run_flush_turn(client)`** — calls `client.query(FLUSH_PROMPT, session_id=...)`, drains via `asyncio.wait_for(_collect_response_text(...), GRACEFUL_FLUSH_SECONDS)`, sets `_flush_complete` in `finally`

**`_run()` loop** — added `self._client = client` with `try/finally` clear; sentinel handling: `if msg is _GRACEFUL_FLUSH_SENTINEL: await self._run_flush_turn(client); return`

**`_handle_one_turn()`** — added `if self._terminating: return` in the `if not text:` branch after the `failed_task_notifs` check, preventing spurious retries when an in-flight drain was interrupted by `begin_graceful_termination`

### `tests/test_graceful_flush_sdk.py` — AT#1, AT#2, AT#3

- `TestHasMemorySurface` (6 tests) — covers project/user/local scopes → True; no scope → False; scope with Write removed → False; scope with tools=None → False
- `TestIdleFlushHappy` (2 tests) — idle path: sentinel breaks `inbox.get()`; `FLUSH_PROMPT` arrives at fake client; idempotency
- `TestBusyFlushHappy` (2 tests) — busy path: `interrupt()` called once; flush query fires; `_flush_complete` set; edge case: empty flush drain still sets event

### `tests/test_live_graceful_flush.py` — AT#4 (gated)

Gated by `CLAUDE_CREW_LIVE_TESTS=1`. Spawns a real `SdkTeammate` with `memory="project"` into a `tmp_path`, flushes via `begin_graceful_termination`, asserts `MEMORY.md` exists and is non-empty. Skipped in stub CI runs.
