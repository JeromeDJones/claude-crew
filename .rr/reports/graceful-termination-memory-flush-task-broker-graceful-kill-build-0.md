# Build Report: graceful-termination-memory-flush / broker-graceful-kill (cycle 0)

**Verdict:** PASS
**Cycle:** 0
**Generated:** 2026-05-31

## Tests Run

- **Declared command:** `uv run pytest tests/test_graceful_kill_broker.py`
- **Actual command:** `uv run pytest tests/test_graceful_kill_broker.py`
- **Divergence reason:** None
- **Exit code:** 0
- **Passed:** 9 / **Failed:** 0 / **Total:** 9

## Failing Tests

_None._

## Uncovered / Partially Covered Tests

_None._ All AT#6-10, 12, 13 assertions are covered:
- AT#6: two tests — flush invoked before tombstone; default-graceful path
- AT#7: auto-skip when has_memory_surface() is False
- AT#8: graceful=False hard-kills immediately
- AT#9: hanging flush times out; tombstone still runs; no exception escapes
- AT#10: _HoldingTeammate (controllable event) verifies non-zero terminating window bounce (MEDIUM-01 addressed inline)
- AT#12: _handle_teammate_death never invokes flush
- AT#13: double kill raises TeammateAlreadyDeadError; no second flush

## Files Changed

```
M	claude_crew/broker.py
?? tests/test_graceful_kill_broker.py
```

## Scope-Creep Entries (this cycle)

_None._

## Blocker Reason

_None — PASS verdict._
