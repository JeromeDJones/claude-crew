# Slice Review: graceful-termination-memory-flush task=teammate-sdk-flush

**Cycle:** 0 (no prior report)
**Verdict:** PASS

## Summary

Task implements the real SdkTeammate flush: `GRACEFUL_FLUSH_SECONDS`, `_GRACEFUL_FLUSH_SENTINEL`, `FLUSH_PROMPT`, `_role_memory`/`_terminating`/`_flush_complete`/`_client` state, `has_memory_surface()`, `begin_graceful_termination()`, `_run_flush_turn()`, the `_run`-loop sentinel branch, and the `_handle_one_turn` terminating-guard. Plus the gated live test. All four owned ATs (1,2,3,4) covered. Slice suite green (10 sdk + 6 base = 16 pass, 1 live skipped), full suite 1277 pass / 33 skip / 1 xfail (no regression), scope clean.

## Non-regression note

`tests/test_graceful_kill_broker.py` (one of the supplied sibling commands) **does not exist in this worktree** — it belongs to the parallel `broker-graceful-kill` slice whose changes have not merged here. Per the parallel-dispatch rule I substituted it with the **full suite** (`uv run pytest --ignore=tests/test_shutdown_signals.py`) as the authoritative non-regression gate, which proves the tree is green from this worktree's contents alone. The present sibling command (`test_graceful_termination_base.py`) ran green. The one excluded file (`test_shutdown_signals.py`) is a pre-existing environment failure documented in the build report (verified on stashed HEAD, change-independent).

## Check 1 — Slice adherence (AT#1,2,3,4)

- **AT#1 (idle, happy)** — `test_idle_flush_fires_flush_prompt`: sentinel breaks `inbox.get()` (`_run` loop branch at sdk_teammate.py:1372-1376 → `_run_flush_turn`), fake client receives **exactly one** query == `FLUSH_PROMPT`, `_flush_complete` set, returns < 5s. `test_idle_flush_idempotent` confirms the `_flush_complete.is_set()` guard (line 1709) prevents a second query. ✅
- **AT#2 (busy, happy)** — `test_busy_flush_fires_interrupt_then_flush_query`: teammate confirmed busy (`_current_turn_started_at_wallclock is not None`), `client.interrupt()` called **exactly once** (line 1719), in-flight drain breaks, `_handle_one_turn` returns via the `if self._terminating: return` guard (line ~1519), flush turn runs **inline on the same `_run` task / same held client** (last query == `FLUSH_PROMPT`), `_flush_complete` set. `test_busy_flush_sets_flush_complete_even_on_empty_drain` exercises the empty-drain path. ✅
- **AT#3 (surface detection)** — `TestHasMemorySurface` (6 tests): project/user/local + Write → True; no scope → False; scope but Write removed → False; scope but `tools=None` → False. Matches `has_memory_surface()` (scope AND `"Write" in tools`, lines 1688-1692). ✅
- **AT#4 (gated live)** — `test_live_graceful_flush.py` class-level `@pytest.mark.skipif(not LIVE_TESTS)`; confirmed **skipped** in my run without `CLAUDE_CREW_LIVE_TESTS=1`. Asserts real teammate writes a non-empty `MEMORY.md`. ✅

`_flush_complete.set()` confirmed in a `finally` block (sdk_teammate.py:1773-1776) — `begin_graceful_termination`'s `wait_for` waiter can never hang past its own budget. ✅

## Concurrency seam (corroborates T2 INFO-01)

T2's slice-review flagged the SdkTeammate internal bound vs the broker's outer `asyncio.wait_for`. Confirmed **safe, no double-cancel on the real client**: the flush turn (`client.query` + drain) runs inside the **`_run` task** via the sentinel, structurally isolated from `begin_graceful_termination`. If the broker's outer `wait_for(flush_timeout)` cancels `begin_graceful_termination`, it only cancels that method's `wait_for(_flush_complete.wait())` — the live client query/drain in `_run_flush_turn` is in a different task and is untouched; `_flush_complete` still sets in its `finally`. Independent timeouts. No surprise.

## Check 2 — Non-regression

- Slice + present-sibling + gated live: **16 passed, 1 skipped, exit 0.**
- Full suite (substituted gate): **1277 passed, 33 skipped, 1 xfailed** in ~91s — identical shape to the build report. No green→red.

## Check 3 — Code-quality smoke (`sdk_teammate.py`, 2 new test files)

- `_run` reference-hold (`self._client = client`) wrapped in `try/finally: self._client = None` — no dangling reference after the context exits. Clean.
- Flush errors logged then swallowed (best-effort teardown contract), not silently dropped.
- New tests import at module top (mostly); two `from claude_crew.envelope import ...` are inline inside test functions — see LOW-01.
- Scope check `slice-touches-check.sh` → EXIT=0.

## Findings

### Critical
_None identified._

### High
_None identified._

### Medium
_None identified._

### Low
- [LOW-01] `slice.quality.style` — `tests/test_graceful_flush_sdk.py:308,378`: `from claude_crew.envelope import Envelope, new_message_id` is imported inline inside two test methods. CLAUDE.md test conventions call inline imports a code smell; the file already has a top-level import block. Non-blocking. `fix-style`.

### Info
- [INFO-01] `slice.review-process.cross-slice-observation` — `sdk_teammate.py:1722`: `except (asyncio.TimeoutError, Exception)` is redundant — `TimeoutError` is a subclass of `Exception`. Harmless; reads as intent-signalling.
- [INFO-02] `slice.review-process.cross-slice-observation` — concurrency-seam analysis above confirms T2's INFO-01 resolves cleanly. Forwarded to the feature-reviewer for integration corroboration once `broker-graceful-kill` merges with this slice.

## Findings Disposition

| finding-id | severity | tag | disposition | rationale |
|------------|----------|-----|-------------|-----------|
| LOW-01 | Low | slice.quality.style | deferred-with-rationale | Inline imports are a documented smell but non-blocking; batch-fix in coordinator cleanup before signoff |
| INFO-01 | Info | slice.review-process.cross-slice-observation | waived | Redundant-but-harmless except clause; batch-fix in coordinator cleanup |
| INFO-02 | Info | slice.review-process.cross-slice-observation | waived | Seam confirmed safe; forwarded to feature-reviewer for post-merge corroboration |
