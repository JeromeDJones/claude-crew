# Slice Review: graceful-termination-memory-flush task=broker-graceful-kill

**Cycle:** 0 (no prior report)
**Verdict:** PASS

## Summary

Task adds `graceful`/`flush_timeout` params to `Broker.kill_teammate`, a `_terminating` id set with a pre-tombstone flush step, and a send-bounce during the flush window. Death path left untouched. All seven owned acceptance tests (AT#6,7,8,9,10,12,13) are covered by real assertions; `tests/test_graceful_kill_broker.py` (9 tests) and the sibling base suite (6 tests) both green in re-run; scope check clean.

## Check 1 — Slice adherence (AT#6,7,8,9,10,12,13)

- **AT#6** (graceful kill happy) — `test_graceful_kill_invokes_flush_before_tombstone` + `test_graceful_kill_default_is_graceful`. Flush invoked before tombstone; telemetry intact (`alive=False`, `died_at_wallclock` set, `exit_code is None`). D2 ordering preserved: `begin_graceful_termination` runs at broker.py:619, `_tombstone_teammate` at :632. ✅
- **AT#7** (auto-skip, no memory surface) — gated by `graceful and teammate.has_memory_surface()` (broker.py:616); test asserts `_flush_invoked is False`, still tombstoned. ✅
- **AT#8** (graceful=False) — skip branch; flush not invoked, hard kill. ✅
- **AT#9** (flush timeout → hard tombstone, no exception escapes) — `_HangingTeammate.begin_graceful_termination` sleeps 999s ignoring its own timeout; broker's outer `asyncio.wait_for(..., timeout=flush_timeout)` (broker.py:619-622) enforces the bound, `except Exception` swallows, `finally` discards, tombstone proceeds. Test asserts `elapsed < 2.0` with `flush_timeout=0.2`, tombstoned, no raise. `TimeoutError ⊆ Exception` → caught. ✅
- **AT#10** (terminating-window bounce) — uses a **controllable** `_HoldingTeammate` whose flush awaits `flush_released` (held open via the kill_task + `flush_started` handshake, `flush_timeout=30.0`), NOT a zero-window stub. The send during the open window raises `TeammateAlreadyDeadError`. This is exactly the construction the task brief flagged as required. ✅
- **AT#12** (death never flushes) — `_handle_teammate_death` (broker.py:594-600) routes straight to `_tombstone_teammate(..., "died")`, structurally unchanged. Test asserts `_flush_invoked is False`, `exit_code == 1`. ✅
- **AT#13** (double-kill idempotent) — second call hits the `not in self._teammates` / `in self._info` guard (broker.py:606-609) and raises `TeammateAlreadyDeadError` before the flush branch; test resets `_flush_invoked` and asserts no second flush. ✅

Bonus `test_terminating_set_empty_after_kill` confirms no `_terminating` leak.

## Check 2 — Non-regression

- `uv run pytest tests/test_graceful_kill_broker.py tests/test_graceful_termination_base.py` → **15 passed, exit 0.** Slice command (9) green; sibling base suite (6) green. No regression.

## Check 3 — Code-quality smoke (changed files: `broker.py`, new test file)

- `send()` bounce (broker.py:666-669) correctly placed after the D6 tombstone-dead check and before the unknown-recipient check — ordering coherent, lead unaffected.
- `_terminating.discard` in a `finally` — no leak on timeout/error path.
- `except Exception` at broker.py:623 is broad but **appropriate** for best-effort teardown, and it logs (`logger.warning`) — not a silent swallow.
- No secrets, no dead code, no broken contracts. Scope check `slice-touches-check.sh` → EXIT=0.

## Findings

### Critical
_None identified._

### High
_None identified._

### Medium
_None identified._

### Low
_None identified._

### Info
- [INFO-01] `slice.review-process.cross-slice-observation` — broker.py:619-622: the flush is double-bounded — `timeout=flush_timeout` passed *into* `begin_graceful_termination` (the teammate self-bounds per spec contract) **and** an outer `asyncio.wait_for(..., timeout=flush_timeout)`. The spec control-flow sketch (lines 37-40) shows a bare `await`. The outer guard is defense-in-depth (it's what makes AT#9's non-self-bounding `_HangingTeammate` terminate) and is harmless/more robust, but the feature-reviewer may want to confirm it composes correctly with `SdkTeammate`'s own internal bound from the `teammate-sdk-flush` slice (no double-cancel surprises on the real client). Not a defect in this slice.

## Findings Disposition

| finding-id | severity | tag | disposition | rationale |
|------------|----------|-----|-------------|-----------|
| INFO-01 | Info | slice.review-process.cross-slice-observation | waived | Defense-in-depth, harmless; forwarded to feature-reviewer for SdkTeammate composition check |
