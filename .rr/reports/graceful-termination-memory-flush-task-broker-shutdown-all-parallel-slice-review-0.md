# Slice Review: graceful-termination-memory-flush task=broker-shutdown-all-parallel

**Cycle:** 0 (no prior report)
**Verdict:** PASS

## Summary

Task rewrites `Broker.shutdown_all` to flush all memory-surface teammates in **parallel** under ONE shared deadline (single `asyncio.wait_for` over a single `asyncio.gather`), reusing the `begin_graceful_termination` seam, then tombstones all. AT#11 covered with a controllable sleeping double that discriminates parallel from sequential. Slice suite green (6 tests), siblings green (9 + 6), scope clean.

## Check 1 — Slice adherence (AT#11)

`shutdown_all(*, graceful=True, flush_timeout=90.0)` (broker.py:631):
- **Parallel under one deadline** — collects `memory_ids` (alive + `has_memory_surface()`), marks them all `_terminating` up front, then `asyncio.wait_for(asyncio.gather(*(begin_graceful_termination(...) for ...), return_exceptions=True), timeout=flush_timeout)`. This is **one** `wait_for` over **one** `gather` — a single shared deadline, not N×timeout. ✅
- **Reuses the seam** — calls `begin_graceful_termination` directly per teammate; no duplicated per-teammate flush logic. ✅
- **All N tombstoned** — after the flush block, every teammate (memory and memory-less alike) is tombstoned via `_tombstone_teammate(tid, None, "kill", reason="shutdown")` in the trailing loop. ✅
- `return_exceptions=True` so one teammate's flush error doesn't cancel siblings; `finally` discards all `_terminating` ids regardless of outcome. ✅

**Timing test (`test_parallel_flushes_wall_time`)** uses the prescribed controllable double `_SlowFlushTeammate` whose `begin_graceful_termination` sleeps T=0.15s. N=3 → parallel ≈0.15s, sequential would be ≈0.45s; asserts `wall_time < 2×T = 0.30s` — proves parallel, **rules out the sequential regression** (0.30 < 0.45). Asserts all 3 tombstoned. Companion tests: backward-compat no-args, `graceful=False` skips flush, memory-less skip, mixed crew, and timeout-still-tombstones (hang 999s, budget 0.2s → all tombstoned, no raise). ✅

**Flakiness margin (brief's specific concern):** the `< 2×T` threshold gives 2× headroom; the discriminating boundary (0.30s threshold vs 0.45s sequential) has 0.15s separation. Empirically stable: 5 consecutive runs each completed in 0.17s wall. Margin adequate (see LOW-01).

## Check 2 — Non-regression

- `uv run pytest tests/test_graceful_shutdown_all.py tests/test_graceful_kill_broker.py tests/test_graceful_termination_base.py` → **21 passed, exit 0.** Both sibling suites present in this worktree (`broker-graceful-kill` merged) and green. The build report's 2 full-suite failures are in `test_shutdown_signals.py`, verified pre-existing (env process-startup timing) via `git stash` re-run, unrelated.

## Check 3 — Code-quality smoke (changed file: `broker.py`; new test file)

- Behavior change worth noting (Info): `shutdown_all` no longer routes through `kill_teammate`; it does its own parallel flush then calls `_tombstone_teammate` directly. **Intentional and correct** — routing through `kill_teammate` would serialize the flushes (the anti-pattern AT#11 forbids) and double-flush. `_tombstone_teammate` is idempotent. No regression (sibling broker tests green).
- TimeoutError and generic Exception both caught and logged — best-effort teardown contract honored. No secrets, no dead code.
- Scope check `slice-touches-check.sh` → EXIT=0.

## Findings

### Critical
_None identified._

### High
_None identified._

### Medium
_None identified._

### Low
- [LOW-01] `slice.quality.style` — `tests/test_graceful_shutdown_all.py:106,228`: parallel-timing assertions rely on wall-clock timing with 2× headroom. Stable in 5 local re-runs (0.17s each) and the threshold separates parallel from sequential, so not a present defect — but timing assertions can flake under heavy CI. Advisory: a future hardening could assert against a larger N (sharper ratio). `fix-style`.

### Info
- [INFO-01] `slice.review-process.cross-slice-observation` — `shutdown_all` deliberately bypasses `kill_teammate` and tombstones directly after the parallel flush. Flagged for the feature-reviewer to confirm composition with the `broker-graceful-kill` slice's `kill_teammate` flush path (no path double-flushes; the two entry points are mutually exclusive by design).

## Findings Disposition

| finding-id | severity | tag | disposition | rationale |
|------------|----------|-----|-------------|-----------|
| LOW-01 | Low | slice.quality.style | deferred-with-rationale | Timing margin adequate (5/5 stable); larger-N hardening tracked as debt for retro |
| INFO-01 | Info | slice.review-process.cross-slice-observation | waived | Direct-tombstone is intentional per AT#11; forwarded to feature-reviewer for composition check |
