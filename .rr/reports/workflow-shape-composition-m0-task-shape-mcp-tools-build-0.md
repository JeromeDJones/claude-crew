# Build Report — shape-mcp-tools — cycle 0

## Verdict: PASS

**Slug:** workflow-shape-composition-m0  
**Task:** shape-mcp-tools (index 2)  
**Cycle:** 0  
**Date:** 2026-06-11

---

## Test command

```
uv run pytest tests/test_shape_gate.py
```

## Slice test results

```
11 passed in 1.13s
```

All 11 tests pass. Covers ATs 8, 9, 10, 14:

| Test | AT | Status |
|------|----|--------|
| TestProposeApproveInstantiate::test_propose_approve_instantiate_spawns_two_teammates | AT8 | PASS |
| TestProposeApproveInstantiate::test_propose_shape_parse_error_returns_ok_false | AT8 | PASS |
| TestProposeApproveInstantiate::test_instantiate_marks_proposal_instantiated | AT8 | PASS |
| TestInstantiateBeforeApproval::test_pending_proposal_refused | AT9 | PASS |
| TestInstantiateBeforeApproval::test_unknown_shape_id_refused | AT9 | PASS |
| TestDeclinedProposal::test_decline_aborts_spawn | AT10 | PASS |
| TestDeclinedProposal::test_timed_out_proposal_refused | AT10 | PASS |
| TestPreflightRoleResolution::test_unresolvable_role_refuses_all_zero_spawn | AT14 | PASS |
| TestPreflightRoleResolution::test_resolvable_via_unique_suffix_promotion_accepted | AT14 | PASS |
| TestPreflightRoleResolution::test_no_known_roles_skips_preflight | AT14 | PASS |
| TestPreflightRoleResolution::test_ambiguous_suffix_matches_refused | AT14 | PASS |

## Full suite regression check

```
uv run pytest --ignore=tests/test_dashboard_render.py -q
2 failed, 1380 passed, 34 skipped, 1 xfailed, 28 warnings in 147.28s
```

**Pre-existing failures (unrelated to this slice):**
- `tests/test_shutdown_signals.py::TestSignalShutdown::test_sigterm_triggers_clean_exit_and_deregister`
- `tests/test_shutdown_signals.py::TestSignalShutdown::test_sigint_triggers_clean_exit_and_deregister`

Both failures are process-spawn/registration infrastructure tests that time out waiting for the real `claude-crew` server to bind a port. Identical to the baseline in the prior tasks' build reports — unaffected by this slice's changes.

Baseline was 1369 passed. New total is 1380 (+11 from this slice's new tests).

## Changes made

### `claude_crew/factories.py` (modified)

Added `factory.known_roles` accessor after `factory.startup_diagnostics`, mirroring the same idiom exactly:

```python
# Live enumeration of known roles for instantiate_shape pre-flight.
# Reads holder.pack LIVE so post-refresh state is always current.
factory.known_roles = lambda: tuple(holder.pack.keys())
```

This accessor is added only on the sdk-mode factory (inside the `if mode == "sdk":` branch). The stub factory does **not** get this attribute by default, preserving today's behavior where stub tests spawn any role unconditionally. Tests that need to exercise the pre-flight inject `stub_factory.known_roles` directly (AT14).

### `claude_crew/server.py` (modified)

**New imports:**
```python
from claude_crew.broker import (..., Topology, ...)
from claude_crew.shapes import ShapeValidationError, parse_shape
```

**New `propose_shape` MCP tool** (async closure over `broker` and `factory`):
- Calls `parse_shape(shape)` → on `ShapeValidationError` returns `{ok:False, stage:"parse", error:…}`
- Calls `broker.register_proposal(parsed, adaptation_diff=adaptation_diff)` → shape_id
- Awaits `broker.await_proposal(shape_id, timeout=timeout_seconds)` (blocks)
- Returns `{ok:True, shape_id, status, shape:{name,description,nodes:[{slot,role}…]}}`

**New `instantiate_shape` MCP tool** (async closure over `broker` and `factory`):
- Looks up proposal via `broker.get_proposal(shape_id)` → unknown → `{ok:False}`
- Refuses if `proposal.status != "approved"` → `{ok:False, error:…}`
- Pre-flight (when `factory.known_roles` exists): checks every node's role via exact match or unique `*:role` suffix promotion; any unresolvable → `{ok:False, unresolved_roles:[…]}`, zero spawn
- Spawns each node via `broker.spawn_teammate(role=node.role, name=node.slot, factory=factory, model=…, extra_tools=list(…) or None, extra_skills=list(…) or None, cwd=…)` with tuple→list conversion per L1
- Creates `Topology(shape_name=…, edges=tuple((from, to, mode)…), slot_to_teammate={…})`
- Calls `broker.record_topology(topology)` and sets `proposal.status = "instantiated"` (single-use)
- Returns `{ok:True, shape_id, crew:[{slot,teammate_id,role}…], topology:{shape_name,edges,slot_to_teammate}}`

Tool count: 12 → 14 (as anticipated in the spec's design notes).

### `tests/test_shape_gate.py` (new)

11 tests across 4 classes. All use the `make_server(broker=broker)` + `create_connected_server_and_client_session` pattern with a directly-injected broker so tests can drive `resolve_proposal` in-process to unblock the waiting `propose_shape` tool. AT14 tests inject `stub_factory.known_roles` directly and use the `clean_stub_known_roles` autouse fixture to clean up after each test.

## git diff --name-status HEAD

```
M	claude_crew/factories.py
M	claude_crew/server.py
?? tests/test_shape_gate.py
```
