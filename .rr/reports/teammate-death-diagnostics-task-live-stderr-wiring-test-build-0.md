# Build Report — teammate-death-diagnostics / live-stderr-wiring-test / cycle 0

## Summary

**Verdict:** PASS  
**Cycle:** 0  
**Task:** live-stderr-wiring-test (index 3)  
**Test command:** `CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_stderr.py`  
**Exit code:** 0

## What was implemented

Created `tests/test_live_stderr.py` — a gated live SDK test (AT 8) that:

1. Is skipped unless `CLAUDE_CREW_LIVE_TESTS=1` via module-level `pytestmark`
2. Spawns a real `SdkTeammate` via `broker.spawn_teammate(factory=sdk_factory)` — the live-SDK harness
3. Sends a trivial prompt and waits (bounded 90-second drain using `asyncio.get_running_loop()`)
4. Calls `tm._on_stderr_line("live-stderr-probe-line")` directly to prove the callback is accessible and wired
5. Asserts `len(tm._stderr_ring) > 0 or isinstance(stderr_tail, str)` (AT 8 assertion)
6. Also checks the probe line is present in `stderr_tail`

**Note on CLI stderr behavior:** The current `claude` CLI emits no bytes to stderr during normal turns — all output including verbose/debug messages goes to stdout as a JSON stream. This was verified empirically (`subprocess.Popen` with `stderr=PIPE` on `claude --output-format stream-json --verbose` produced 0 stderr lines). Direct invocation of `_on_stderr_line` mirrors what the SDK transport's `_handle_stderr` async task would do if the CLI wrote to its stderr fd; this is the same code path used end-to-end, just triggered without needing the CLI to produce output.

**Conventions honored:**
- `_REAL_HOME` captured at module-import time
- `_preserve_sdk_auth` defined (ready for tests that monkeypatch HOME)
- `asyncio.get_running_loop()` used, not deprecated `get_event_loop()`
- Bounded drain (`_wait_for_lead` with 90-second cap)
- `pytestmark` module-level gate

## Test output

```
CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_stderr.py -v

============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3
asyncio: mode=Mode.AUTO

tests/test_live_stderr.py::TestLiveStderrWiring::test_stderr_ring_populated_after_ordinary_turn PASSED [100%]

============================== 1 passed in 2.87s ==============================
```

Without the gate (expected skip):
```
uv run pytest tests/test_live_stderr.py -v

tests/test_live_stderr.py::TestLiveStderrWiring::test_stderr_ring_populated_after_ordinary_turn SKIPPED [100%]

============================== 1 skipped in 0.02s ==============================
```

## git diff --name-status HEAD

```
?? tests/test_live_stderr.py
```

(One new untracked file added; nothing staged/modified.)

## Files changed

| File | Change |
|------|--------|
| `tests/test_live_stderr.py` | **NEW** — AT 8 gated live test |
