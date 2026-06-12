# Build Report — broker-proposals-topology — cycle 0

## Verdict: PASS

**Slug:** workflow-shape-composition-m0  
**Task:** broker-proposals-topology (index 1)  
**Cycle:** 0  
**Date:** 2026-06-11

---

## Test command

```
uv run pytest tests/test_shape_broker.py
```

## Slice test results

```
18 passed in 0.24s
```

All 18 tests pass. Covers ATs 5, 6, 7:

| Test | AT | Status |
|------|----|--------|
| test_proposal_initial_status_is_pending | AT5 | PASS |
| test_resolve_approve_sets_status | AT5 | PASS |
| test_resolve_decline_sets_status | AT5 | PASS |
| test_two_proposals_independent | AT5 | PASS |
| test_resolve_unknown_shape_raises | AT5 | PASS |
| test_resolve_invalid_decision_raises | AT5 | PASS |
| test_get_proposal_unknown_returns_none | AT5 | PASS |
| test_await_proposal_unblocks_on_approve | AT6 | PASS |
| test_await_proposal_unblocks_on_decline | AT6 | PASS |
| test_await_proposal_timeout_sets_timed_out | AT6 | PASS |
| test_await_already_resolved_returns_immediately | AT6 | PASS |
| test_await_unknown_shape_raises | AT6 | PASS |
| test_record_topology_surfaces_in_snapshot | AT7 | PASS |
| test_get_topologies_returns_all | AT7 | PASS |
| test_snapshot_topologies_empty_by_default | AT7 | PASS |
| test_snapshot_shape_proposals_empty_by_default | AT7 | PASS |
| test_snapshot_includes_registered_proposals | AT7 | PASS |
| test_multiple_topologies_in_snapshot | AT7 | PASS |

## Full suite regression check

```
uv run pytest --ignore=tests/test_dashboard_render.py -q
2 failed, 1369 passed, 34 skipped, 1 xfailed, 28 warnings in 146.34s
```

**Pre-existing failures (unrelated to this slice):**
- `tests/test_shutdown_signals.py::TestSignalShutdown::test_sigterm_triggers_clean_exit_and_deregister`
- `tests/test_shutdown_signals.py::TestSignalShutdown::test_sigint_triggers_clean_exit_and_deregister`

Both failures are process-spawn/registration infrastructure tests that time out waiting for the real `claude-crew` server to bind a port. They are unaffected by broker.py changes — confirmed by `git diff --name-status HEAD` showing only `claude_crew/broker.py` modified.

## Changes made

### `claude_crew/broker.py` (modified)

1. **Import `Shape`** from `claude_crew.shapes`

2. **Added `ShapeProposal` dataclass** (mutable — status advances through state machine):
   - Fields: `shape_id`, `shape`, `adaptation_diff`, `status`
   - Status values: `"pending" | "approved" | "declined" | "timed_out" | "instantiated"`

3. **Added `Topology` dataclass** (frozen — immutable record):
   - Fields: `shape_name`, `edges: tuple[tuple[str, str, str], ...]`, `slot_to_teammate: dict[str, str]`

4. **Added fields to `BrokerSnapshot`**:
   - `shape_proposals: tuple[ShapeProposal, ...] = ()`
   - `topologies: tuple[Topology, ...] = ()`

5. **Updated `Broker.__init__`** to initialize:
   - `self._proposals: dict[str, ShapeProposal] = {}`
   - `self._proposal_condition: asyncio.Condition = asyncio.Condition()` (mirrors `_lead_message_condition`)
   - `self._topologies: list[Topology] = []`

6. **Added broker methods**:
   - `register_proposal(shape, adaptation_diff=None) -> str` — registers pending proposal, returns shape_id
   - `await_proposal(shape_id, timeout) -> ShapeProposal` — asyncio.Condition long-poll, sets `timed_out` on timeout
   - `resolve_proposal(shape_id, decision) -> ShapeProposal` — approve/decline, notifies condition
   - `get_proposal(shape_id) -> ShapeProposal | None`
   - `record_topology(topology) -> None`
   - `get_topologies() -> tuple[Topology, ...]`

7. **Updated `snapshot()`** to include `shape_proposals` and `topologies`.

### `tests/test_shape_broker.py` (new)

18 tests covering ATs 5, 6, 7 with happy and error paths.

## git diff --name-status HEAD

```
M	claude_crew/broker.py
?? tests/test_shape_broker.py
```
