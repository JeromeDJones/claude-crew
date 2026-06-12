# Build Report — workflow-shape-composition-m0 hardening — cycle 0

**Slug:** workflow-shape-composition-m0  
**Task:** 5 hardening fixes  
**Cycle:** 0  
**Date:** 2026-06-12  
**Verdict:** PASS

---

## Test command

```
uv run pytest
```

## Result

```
2 failed, 1438 passed, 34 skipped, 1 xfailed, 46 warnings in 193.16s
```

Only the 2 known-flaky `test_shutdown_signals` tests failed (pre-existing baseline; documented in the task as acceptable). All 45 shape-specific tests pass, and zero regressions across the rest of the suite.

---

## Changed files

```
M   claude_crew/broker.py
M   claude_crew/factories.py
M   claude_crew/server.py
M   tests/test_shape_broker.py
M   tests/test_shape_gate.py
```

(Other tracked modifications — `CLAUDE.md`, `doc/ARCHITECTURE.md`, `doc/BACKLOG.md`, `doc/PRODUCT-VISION.md` — are pre-existing uncommitted changes from prior slices; this task did not touch them.)

---

## Per-fix notes

### Fix 1 — `resolve_proposal` source-state guard (broker.py)

Added a status-check guard **after** the unknown-shape KeyError check and **after** the decision validation:

```python
if proposal.status != "pending":
    raise ValueError(
        f"cannot resolve proposal {shape_id!r} in state {proposal.status!r}; "
        "only 'pending' is resolvable"
    )
```

This prevents silently flipping `approved → declined`, `timed_out → approved`, etc. The guard order (invalid-decision → unknown-id → wrong-status) ensures callers see the most useful error first.

**Tests added (test_shape_broker.py):**
- `test_resolve_approved_proposal_raises` — approve then approve again raises ValueError
- `test_resolve_declined_proposal_raises` — decline then decline again raises ValueError
- `test_resolve_timed_out_proposal_raises` — await timeout, then resolve raises ValueError
- `test_resolve_state_error_message_includes_state` — error text includes the current status string

---

### Fix 2 — shared resolve_role accessor (factories.py + server.py)

**Judgment call:** The inline fallback (exact-or-suffix check) in `instantiate_shape`'s pre-flight is kept as a path for stub-mode tests that inject `known_roles` without `resolve_role`. This preserves the existing AT14 tests exactly as written (they inject `known_roles` on `stub_factory` but not `resolve_role`, so they go through the fallback path). New tests in `TestSharedResolveRoleAccessor` exercise the new `resolve_role` path explicitly.

**Double-log side effect:** `_resolve_role` logs INFO/WARN when it promotes or detects ambiguity. Calling it in pre-flight and again at spawn time means two INFO/WARN lines for the same promotion. This is acceptable (the task explicitly says so) and actually useful — it confirms the pre-flight ran. No quiet-path was added to avoid over-engineering.

**Changes:**
- `factories.py`: added `factory.resolve_role = _resolve_role` after `factory.known_roles = ...`
- `server.py`: pre-flight detects `resolve_role_fn = getattr(factory, "resolve_role", None)`; if present, uses it (`resolve_role_fn(role) not in known` → unresolved); falls back to the inline exact/suffix check otherwise.

**Tests added (test_shape_gate.py):**
- `test_shared_resolver_unique_suffix_promotion_accepted` — both `known_roles` and `resolve_role` injected; bare roles uniquely promote → `ok: True`
- `test_shared_resolver_ambiguous_role_refused` — ambiguous roles → `ok: False`, `unresolved_roles` present, zero spawn
- `_make_resolve_role()` helper mirrors `_resolve_role` semantics for standalone test use

---

### Fix 3 — `Topology.slot_to_teammate` truly immutable (broker.py)

Added:
- `from collections.abc import Mapping` and `from types import MappingProxyType` at module top
- Updated type annotation from `"dict[str, str]"` to `"Mapping[str, str]"`
- Added `__post_init__` that wraps via `MappingProxyType(dict(self.slot_to_teammate))`; the `dict()` copy ensures the proxy owns an independent snapshot so the caller's original dict cannot be mutated either.

`MappingProxyType` is still dict-comparable (`== {"a": "b"}` works), iterable, indexable, and `json.dumps`-serializable via `dict()`. The `slot_to_teammate` value in the `instantiate_shape` return JSON is now `dict(slot_to_teammate)` (explicit copy) to be safe.

**Tests added (test_shape_broker.py):**
- `test_topology_slot_to_teammate_is_immutable` — write raises `TypeError`
- `test_topology_slot_to_teammate_reads_work` — reads and `.keys()` work correctly
- `test_topology_snapshot_round_trips_slot_map` — content survives `record_topology → snapshot()` unchanged; mutation still refused on snapshot copy
- `test_topology_caller_dict_mutation_does_not_affect_proxy` — post-construction mutation of caller's dict has no effect

---

### Fix 4 — transactional spawn (no partial crew) (server.py)

**Judgment call on `failed` state:** The spec says "leave `approved` so a fixed retry can proceed; if you judge a `failed` state cleaner, discuss." Decision: **leave `approved`**. Reasoning: introducing a `failed` terminal state would require a new `ShapeProposal.status` value, new validation in `resolve_proposal` and `mark_instantiated`, and potentially new UI handling — all without a clear user-visible benefit. Retryability from `approved` is the more useful default for operators.

**Rollback implementation:**
```python
try:
    for node in shape.nodes:
        tid = await broker.spawn_teammate(...)
        crew.append(...)
        slot_to_teammate[node.slot] = tid
except Exception as exc:
    rolled_back = []
    for entry in crew:
        try:
            await broker.kill_teammate(entry["teammate_id"], reason="spawn-rollback", graceful=False)
            rolled_back.append(entry["slot"])
        except Exception:
            pass  # best-effort
    return {"ok": False, "error": f"spawn failed mid-instantiation: {exc}", "partial_crew_rolled_back": rolled_back}
```

`graceful=False` skips the memory-flush window so rollback is immediate. Inner exception is swallowed (best-effort) so a double-failure doesn't mask the original error.

**Tests added (test_shape_gate.py):**
- `TestTransactionalSpawn::test_spawn_failure_rolls_back_partial_crew` — patches `broker.spawn_teammate` to fail on call #2; approves a 3-node shape; calls `instantiate_shape`; asserts `ok: False`, zero alive teammates, proposal still `approved`

---

### Fix 5 — `mark_instantiated` transition into broker (broker.py + server.py)

Added `Broker.mark_instantiated(shape_id)` — mirrors `get_proposal` style: KeyError on unknown, ValueError if not `approved`, then sets `status = "instantiated"`. In `server.py`, replaced `proposal.status = "instantiated"` with `broker.mark_instantiated(shape_id)`.

**Tests added (test_shape_broker.py):**
- `test_mark_instantiated_on_pending_raises` — pending → ValueError
- `test_mark_instantiated_on_declined_raises` — declined → ValueError
- `test_mark_instantiated_on_unknown_raises` — unknown → KeyError
- `test_mark_instantiated_on_approved_succeeds` — approved → `"instantiated"` (happy path)
- `test_mark_instantiated_on_already_instantiated_raises` — double-instantiate → ValueError

Existing AT8 (`test_instantiate_marks_proposal_instantiated`) confirms the full end-to-end happy path still works.

---

## New test count

| File | Before | After | Added |
|------|--------|-------|-------|
| `tests/test_shape_broker.py` | 18 | 31 | 13 |
| `tests/test_shape_gate.py` | 11 | 14 | 3 |
| **Total** | **29** | **45** | **+16** |
