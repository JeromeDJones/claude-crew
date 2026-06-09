# Slice Review: teammate-death-diagnostics task=live-stderr-wiring-test

**Verdict: REQUEST-CHANGES**
**Cycle:** 0

## Summary

This task adds `tests/test_live_stderr.py`, a gated (`CLAUDE_CREW_LIVE_TESTS=1`) live SDK test for AT-8. The supporting infrastructure is well-built — module-level skip gate, bounded 90s drain via `asyncio.get_running_loop()`, `_REAL_HOME`/`_preserve_sdk_auth` auth-preservation helper, real teammate spawned through `sdk_factory`. Non-regression is clean. **But the test does not satisfy AT-8's sole purpose**: it proves SDK liveness + the ring→snapshot chain in isolation, not that the stderr callback is *registered with the SDK* end-to-end. That gap is dispositive for the one acceptance test this task owns.

## Slice Adherence (AT 8) — the coordinator's concern, adjudicated

AT-8's stated purpose: a live turn proves the `stderr` callback was registered and invoked end-to-end. The literal assertion the spec author wrote (`ring non-empty OR stderr_tail is str`) was designed under the assumption the CLI emits stderr naturally during a turn. The implementor correctly discovered it does not — a genuine constraint — and substituted a **direct call** `tm._on_stderr_line(PROBE)`, then asserts the ring is populated.

**Decisive analysis (the coordinator's key question):** delete the registration line `claude_crew/sdk_teammate.py:1432` (`opts_kwargs["stderr"] = self._on_stderr_line`) and re-run this test. It still passes — because the test populates the ring itself by calling `_on_stderr_line` directly, never relying on the SDK transport to invoke it. Therefore the test proves only (a) a turn completes and (b) the callback mutates the ring in isolation. It does **not** prove (c) the SDK is configured to call that callback — which is precisely what AT-8 exists to guard.

The test's own docstring claims accessibility of `_on_stderr_line` is "registration proof." This is logically false: `_on_stderr_line` is a bound method always accessible on any `SdkTeammate` instance regardless of whether it was ever passed to the SDK. Accessibility proves the method exists, not that it was registered.

**The constraint does not excuse the gap.** Natural-invocation assertion is infeasible (CLI writes nothing to stderr), so the test should not be required to observe a natural callback fire. But a registration assertion is entirely feasible and does not depend on natural stderr: construct `opts_kwargs` the way `_run()` does (or introspect the live client's transport options) and assert `opts_kwargs["stderr"] is tm._on_stderr_line`. That single assertion would make the test regression-sensitive to line 1432 and satisfy AT-8's intent. Its absence is the defect.

## Scope (taskTouches — Invariant 1)

`slice-touches-check.sh` → **EXIT=0**. The single new file `tests/test_live_stderr.py` matches the declared `taskTouches` glob. No out-of-scope edits.

## Non-regression

- `CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_stderr.py` → **1 passed, exit 0** (real SDK subprocess).
- Ungated → **1 skipped, exit 0** (gate works).
- `uv run pytest tests/test_sdk_teammate.py tests/test_broker.py` → **225 passed, exit 0**. No regression.

All non-regression checks pass; the verdict is driven solely by the adherence gap.

## Code-quality smoke

Clean on conventional axes: no secrets, no swallowed exceptions, imports at module top, bounded drain, `get_running_loop()`, correct gating, self-contained auth helper. The one substantive issue is the misleading docstring claim that accessibility proves registration, which should be corrected alongside the test fix.

### Critical
_None identified._

### High
- [HIGH-01] `slice.adherence.false-pass` — `tests/test_live_stderr.py`: AT-8 is a "stderr callback registered & invoked end-to-end" test, but the test populates `_stderr_ring` via a direct `tm._on_stderr_line(PROBE)` call and would pass even if the SDK registration line `claude_crew/sdk_teammate.py:1432` were deleted. It verifies liveness + ring/snapshot chain, not the wiring AT-8 owns. Add a registration assertion that does not depend on natural CLI stderr (e.g. assert `opts_kwargs["stderr"] is tm._on_stderr_line` from the same construction `_run()` uses, or introspect the live client's transport options). The direct-call invocation may remain as a ring/snapshot smoke, but it cannot stand in for the registration proof. `address-acceptance-test`.

### Medium
- [MED-01] `slice.quality.style` — `tests/test_live_stderr.py`: the docstring asserts that `_on_stderr_line` being accessible means the registration "was executed." This is logically false (the bound method is always accessible regardless of SDK registration) and obscures HIGH-01. Correct the rationale when fixing the test. `fix-style`.

### Low
_None identified._

### Info
_None identified._

## Findings Disposition

| finding-id | severity | tag | disposition | rationale |
|------------|----------|-----|-------------|-----------|
| HIGH-01 | High | slice.adherence.false-pass | routed-back | Test under-verifies its sole AT (blind to registration line 1432); must add a registration assertion. Drives REQUEST-CHANGES. |
| MED-01 | Medium | slice.quality.style | routed-back | Misleading docstring claim (accessibility ≠ registration) to be corrected alongside the HIGH-01 fix. |
