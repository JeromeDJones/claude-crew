# Build Report — teammate-death-diagnostics / live-stderr-wiring-test / cycle 1

## Summary

**Verdict:** PASS  
**Cycle:** 1  
**Task:** live-stderr-wiring-test (index 3)  
**Test command:** `CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_stderr.py`  
**Exit code:** 0

## Rework addressed

### HIGH-01 (slice.adherence.false-pass)

The cycle-0 test populated `_stderr_ring` via a direct `tm._on_stderr_line(PROBE)` call and would have passed even if `sdk_teammate.py:1432` were deleted. Added a **registration assertion** that introspects the live SDK client's `options` object:

```python
from claude_crew.sdk_teammate import SdkTeammate
registered = client.options.stderr
assert registered is not None, ...          # fails when line 1432 absent
assert registered.__self__ is tm, ...       # correct SdkTeammate instance
assert registered.__func__ is SdkTeammate._on_stderr_line, ...  # correct method
```

`ClaudeSDKClient` stores `self.options = options` directly (`__init__`, line 49 in the SDK), so `client.options.stderr` is exactly the value assigned at construction. Deleting `opts_kwargs["stderr"] = self._on_stderr_line` leaves `options.stderr = None` (the dataclass default), failing the `registered is not None` assert.

**Why `is` was not used on the bound method itself:** Python creates a new bound method object on each attribute access (`tm._on_stderr_line` ≠ `tm._on_stderr_line` by identity). The `__self__ is tm` and `__func__ is SdkTeammate._on_stderr_line` checks are identity-stable and definitively prove the same method was registered.

### MED-01 (style)

Corrected the docstring that falsely claimed accessibility of `_on_stderr_line` was "registration proof." The updated module docstring and class docstring now correctly distinguish the two assertion steps:
1. Registration proof via `client.options.stderr` introspection (sensitive to line 1432).  
2. Ring-to-snapshot smoke via direct `_on_stderr_line` call (separate chain check).

## Delete-line-1432 → RED verification

Line 1432 (`opts_kwargs["stderr"] = self._on_stderr_line`) was temporarily commented out and the test was run:

```
CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_stderr.py -v

E       AssertionError: client.options.stderr is None — opts_kwargs["stderr"] = self._on_stderr_line at
        sdk_teammate.py:1432 was not executed (or was deleted).
E       assert None is not None

FAILED tests/test_live_stderr.py::TestLiveStderrWiring::test_stderr_callback_registered_and_ring_wired
============================== 1 failed in 3.41s ==============================
```

Line 1432 restored; test confirmed GREEN:

```
CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_stderr.py -v

tests/test_live_stderr.py::TestLiveStderrWiring::test_stderr_callback_registered_and_ring_wired PASSED [100%]

============================== 1 passed in 3.89s ==============================
```

## Sibling suite results

```
uv run pytest tests/test_sdk_teammate.py tests/test_broker.py

============================= 225 passed in 4.50s ==============================
```

## git status --porcelain

```
?? tests/test_live_stderr.py
```

(One new untracked file; no modifications to existing files; nothing staged.)

## Files changed

| File | Change |
|------|--------|
| `tests/test_live_stderr.py` | **NEW** — AT 8 gated live test (cycle-1 version with registration assertion) |
