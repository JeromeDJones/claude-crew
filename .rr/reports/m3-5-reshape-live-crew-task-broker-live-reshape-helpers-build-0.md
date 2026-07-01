# Build Report: m3-5-reshape-live-crew (cycle 0)

**Verdict:** PASS
**Cycle:** 0
**Generated:** 2026-06-30T00:00:00Z

## Tests Run

- **Declared command:** `uv run pytest tests/test_reshape_broker_helpers.py`
- **Actual command:** `uv run pytest tests/test_reshape_broker_helpers.py` (then `uv run pytest tests/test_broker.py` for regression gate)
- **Divergence reason:** None — ran declared command plus regression suite as spec required.
- **Exit code:** 0
- **Passed:** 19 / **Failed:** 0 / **Total:** 19 (declared); regression: 105/0/105

## Failing Tests

_None._

## Uncovered / Partially Covered Tests

_None._

All sub-assertions of AT 19 covered:
- `latest_topology()` → None on empty broker (4 tests)
- `latest_topology()` → last Topology after `record_topology` (single + multiple)
- `set_edge_override()` for each mode: `gated`, `tee`, `direct`; overwrite; equivalence with `promote_edge`
- `remove_edge_overrides()` happy path (present key, multiple keys), sad path (absent key, mixed present+absent, empty iterable, double-remove, preserves unrelated, accepts generator)

## Files Changed

```
M	claude_crew/broker.py
?	tests/test_reshape_broker_helpers.py
```

(`git diff --name-status HEAD` shows tracked changes only; `tests/test_reshape_broker_helpers.py` is untracked/new.)

## Scope-Creep Entries (this cycle)

_None._

## Blocker Reason

N/A
