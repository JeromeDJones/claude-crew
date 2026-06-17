# Build Report: adapt-shape-tool — Cycle 0

## Summary

Implemented the `adapt_shape` MCP tool in `claude_crew/server.py` and authored the test suite `tests/test_shape_adapt_tool.py`. All 38 tests in the slice test command pass; no regressions in adjacent test files.

## Task

- **Slug**: m3-adaptation-algebra
- **Task**: adapt-shape-tool (index 1)
- **Cycle**: 0
- **Test command**: `uv run pytest tests/test_shape_adapt_tool.py tests/test_shape_gate.py`

## Result

**PASS** — exit code 0, 38/38 tests passed.

```
============================= 38 passed in 1.66s ==============================
```

## Files Changed

```
M       claude_crew/server.py
??      tests/test_shape_adapt_tool.py
```

### claude_crew/server.py

1. Updated the `claude_crew.shapes` import line to add: `AddNode`, `Augment`, `Drop`, `SetGate`, `ShapeAdaptation`, `ShapeEdge`, `ShapeNode`, `Swap`, `shape_to_dict`.
2. Added the `adapt_shape` MCP tool inside `make_server()` (immediately before the "Stash the broker" comment). The tool implements:
   - **Stage `base`**: exactly-one guard (neither/both → error); `base_shape_id` look-up + status check (only `pending`/`approved` accepted; `instantiated`/`declined`/`timed_out` → error); inline `base_shape` parse via `parse_shape`.
   - **Stage `parse`**: inline `base_shape` that fails `parse_shape` → `{ok:False, stage:"parse"}`.
   - **Stage `verb`**: five-verb frozenset guard (`add_node`/`swap`/`augment`/`set_gate`/`drop`).
   - **Stage `adapt` — role resolution**: for `swap`/`augment` mirrors `instantiate_shape`'s `getattr(factory,"known_roles",None)` / `getattr(factory,"resolve_role",None)` seam exactly; skipped when `known_roles` absent; unresolvable role → `{ok:False, stage:"adapt", unresolved_roles:[...]}`.
   - **Stage `adapt` — verb apply**: constructs the verb command from `params` (with `_node_from_dict` helper for `ShapeNode` coercing lists→tuples); calls `command.apply(resolved_base)`; `ShapeValidationError` (or `KeyError`/`TypeError` for malformed params) → `{ok:False, stage:"adapt"}`.
   - **Success**: `broker.register_proposal(new_shape, adaptation_diff=diff.render())` → `{ok:True, shape_id, status:"pending", diff, shape: shape_to_dict(new_shape)}`.

### tests/test_shape_adapt_tool.py (new)

Authored test file covering ATs 23–33 in seven test classes:
- `TestGateIntegration` (AT23): inline base + set_gate → proposal registered, `adaptation_diff` populated
- `TestIterativeReGateLoop` (AT24): swap → approve → set_gate with `base_shape_id` → distinct new proposal
- `TestRoleResolution` (AT25–28): resolvable swap, unresolvable swap, unresolvable augment, no-known_roles skip
- `TestBaseGuards` (AT29–31): unknown id, instantiated/declined/timed_out status, neither/both args
- `TestVerbGuard` (AT32): unknown verb
- `TestAdaptGuard` (AT33): structural ShapeValidationError → stage:adapt

## Acceptance Tests Coverage

| AT | Description | Status |
|----|-------------|--------|
| 23 | Gate integration — inline base, set_gate success | ✅ PASS |
| 24 | Iterative re-gate loop (swap → approve → set_gate with id) | ✅ PASS |
| 25 | Role resolution: resolvable swap | ✅ PASS |
| 26 | Role resolution: unresolvable swap → stage:adapt | ✅ PASS |
| 27 | Role resolution: unresolvable augment → stage:adapt | ✅ PASS |
| 28 | No known_roles → resolution skipped | ✅ PASS |
| 29 | Unknown base_shape_id → stage:base | ✅ PASS |
| 30 | Instantiated/declined/timed_out base → stage:base | ✅ PASS |
| 31 | Neither/both base args → stage:base | ✅ PASS |
| 32 | Unknown verb → stage:verb | ✅ PASS |
| 33 | ShapeValidationError in verb → stage:adapt | ✅ PASS |

## Regression Check

Additional suites run: `test_shapes.py`, `test_shape_adaptation.py`, `test_shape_broker.py`, `test_shape_render.py`, `test_shape_dashboard.py`, `test_server.py` — **193 passed, 0 failures**.

## Scope Creep

None. Only `claude_crew/server.py` (modified) and `tests/test_shape_adapt_tool.py` (new) were touched. `broker.py` and `claude_crew/shapes.py` are unchanged.

## Design Notes

- The `_node_from_dict` helper is a local nested function inside `adapt_shape` (not extracted to module level) — consistent with the spec's explicit note that role-resolution sites remain duplicated, not extracted.
- `extra_tools`/`extra_skills` coercion from list→tuple at verb construction time ensures the round-trip invariant `parse_shape(shape_to_dict(new_shape)) == new_shape` holds when params arrive from JSON.
- `ShapeAdaptation` is imported for the `command: ShapeAdaptation` type annotation (clarity only; not strictly required).
