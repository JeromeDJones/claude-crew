# Build Report: broker-shutdown-all-parallel (cycle 0)

**Slug:** graceful-termination-memory-flush  
**Task:** broker-shutdown-all-parallel (index 3)  
**Cycle:** 0  
**Verdict:** PASS

---

## Summary

Implemented parallel flush in `broker.shutdown_all` under one shared deadline, and wrote the AT#11 test suite validating the parallel-vs-sequential distinction.

---

## Test Results

### Slice test command: `uv run pytest tests/test_graceful_shutdown_all.py`

```
collected 6 items

tests/test_graceful_shutdown_all.py::TestShutdownAllParallel::test_parallel_flushes_wall_time PASSED
tests/test_graceful_shutdown_all.py::TestShutdownAllParallel::test_shutdown_all_no_args_backward_compat PASSED
tests/test_graceful_shutdown_all.py::TestShutdownAllParallel::test_shutdown_all_graceful_false_skips_flush PASSED
tests/test_graceful_shutdown_all.py::TestShutdownAllParallel::test_shutdown_all_skips_memory_less_teammates PASSED
tests/test_graceful_shutdown_all.py::TestShutdownAllParallel::test_shutdown_all_mixed_crew PASSED
tests/test_graceful_shutdown_all.py::TestShutdownAllParallel::test_shutdown_all_parallel_timeout_still_tombstones PASSED

6 passed in 0.49s
```

### Full suite: `uv run pytest`

```
2 failed, 1282 passed, 32 skipped, 1 xfailed, 35 warnings in 123.32s (0:02:03)
```

Failures: `tests/test_shutdown_signals.py::TestSignalShutdown::test_sigterm_triggers_clean_exit_and_deregister` and `test_sigint_triggers_clean_exit_and_deregister`.

**Pre-existing:** Verified by `git stash` + re-run — both fail without my changes (environment-level process startup timing issue; `claude-crew did not register within 15.0s`). Unrelated to broker.shutdown_all changes.

---

## Implementation

### `claude_crew/broker.py` — `shutdown_all` rewrite

**Before:** `async def shutdown_all(self) -> None:` — iterated teammates sequentially calling `kill_teammate(tid, reason="shutdown")`, meaning N×90s theoretical maximum for large crews.

**After:** `async def shutdown_all(self, *, graceful: bool = True, flush_timeout: float = 90.0) -> None:`

Key design:
1. Collect all alive teammates with `has_memory_surface() == True`
2. Mark all as `_terminating` simultaneously (bounce sends during flush window)
3. ONE `asyncio.wait_for` wrapping ONE `asyncio.gather` over all `begin_graceful_termination` calls — single shared deadline, truly parallel
4. `return_exceptions=True` in gather so one teammate's error doesn't cancel the others
5. `finally` block discards all from `_terminating` regardless of outcome
6. Then tombstone all via `_tombstone_teammate` (already idempotent)

Backward compatible: `*` forces keyword-only, so `await b.shutdown_all()` still works.

### `tests/test_graceful_shutdown_all.py` — 6 tests covering AT#11

Per the plan-review MEDIUM-01 note: uses `_SlowFlushTeammate` double whose `begin_graceful_termination` sleeps T seconds. N=3, T=0.15s:
- Parallel: wall time ≈ 0.15s
- Sequential would be: ≈ 0.45s
- Assert: wall time < 2×T = 0.30s (proves parallel, rules out sequential)

Additional tests: backward-compat no-args call, `graceful=False` skip, memory-less skip, mixed crew, timeout-still-tombstones.

---

## Files Changed

```
M  claude_crew/broker.py
?? tests/test_graceful_shutdown_all.py
```

---

## Scope Creep

None — changes confined to `claude_crew/broker.py` (the task's declared file) and the new test file.

---

## Acceptance Tests Covered

- **AT#11** (Broker, shutdown_all parallel): `test_parallel_flushes_wall_time` — N=3 teammates × T=0.15s, wall time < 2×T=0.30s. All N tombstoned. ✅

---

## Exit Code

Slice tests: 0 (all pass)  
Full suite: non-zero (2 pre-existing failures in test_shutdown_signals.py, unrelated to this task)
