# Build Report — reshape-crew-scaffold (cycle 0)

**Slug:** m3-5-reshape-live-crew  
**Task:** reshape-crew-scaffold (index 2)  
**Cycle:** 0  
**Verdict:** PASS  
**Date:** 2026-06-30

---

## Test Command Results

```
uv run pytest tests/test_reshape_crew_gate.py -v
```

**Result:** 23 passed, 0 failed, 0 errors — 1.58s

Regression check:
```
uv run pytest tests/test_shape_adapt_tool.py tests/test_server.py -v
```

**Result:** 48 passed, 0 failed, 0 errors — 1.86s

---

## What Was Implemented

### `claude_crew/server.py` — `reshape_crew` MCP tool (250 lines added)

New `@mcp.tool()` function `reshape_crew(verb, params, base_shape_id, gate_timeout=600.0)` added after `adapt_shape`, before the broker stash. Implements all five stages from the spec in order:

1. **Base resolution** — `broker.get_proposal(base_shape_id)` → reject if `None` (stage `"base"`) or `status != "instantiated"` (stage `"base"`). Distinct from `adapt_shape` which accepts `pending/approved`.

2. **Verb guard** — reject unknown verbs with `stage:"verb"`. Recognises exactly `{add_node, swap, augment, set_gate, drop}`.

3. **Role resolution (swap/augment only)** — mirrors `adapt_shape` seam verbatim: `getattr(factory, "known_roles", None)` + `factory.resolve_role` (with fallback `*:role` unique-suffix promotion). Returns `stage:"adapt"` + `unresolved_roles` list on failure. Skipped when `known_roles` is absent (stub mode).

4. **Verb apply (pure)** — constructs verb command from `params` exactly as `adapt_shape` does (`_node_from_dict`, `ShapeEdge(**e)`, etc.), calls `command.apply(base_shape)` → `(new_shape, diff)`. Catches `ShapeValidationError` / `KeyError` / `TypeError` → `stage:"adapt"`.

5. **M1.5 gate (verbatim)** — `broker.register_proposal(new_shape, adaptation_diff=diff.render())` then `await broker.await_proposal(sid, gate_timeout)`. Non-approved results (declined/timed_out) → `{ok:False, stage:"gate", status, shape_id}` with zero live mutation.

6. **Live mutation stub** — `_actions` dict with empty lists for `spawned/killed/informed/edge_overrides_set/edge_overrides_removed`. Contains an extensive block comment documenting the per-verb contract (set_gate → `set_edge_override`, add_node/augment → spawn+record+inform, drop → record+kill+cleanup+inform, swap → spawn+record THEN kill). Marked clearly for the `reshape-crew-verbs` task to fill.

7. **Lineage + return** — `broker.mark_instantiated(sid)` transitions the new proposal to `"instantiated"` so it can serve as `base_shape_id` for the next `reshape_crew` call. Returns full success envelope including `topology` from `broker.latest_topology()`.

### `tests/test_reshape_crew_gate.py` — 23 tests (new file)

Covers all scaffold-owned ATs:

| AT | Class | Tests | What is asserted |
|----|-------|-------|-----------------|
| 4  | `TestReshapeCrewToolRegistered` | 1 | Named literal `"reshape_crew"` present in `s.list_tools()` |
| 10 | `TestGateDeclineOrTimeout` | 4 | decline → `stage:"gate"`, `status:"declined"`; no topology/override/spawn; timeout path same |
| 11 | `TestGateApproveLineage` | 3 | approve → `ok:True`, `status:"instantiated"`; lineage: second call with new_sid reaches `stage:"gate"` not `stage:"base"`; diff carried in gate proposal |
| 12 | `TestUnknownBase` | 2 | Unknown id → `stage:"base"`; no proposal registered |
| 13 | `TestNonInstantiatedBase` | 4 | Each non-instantiated status (pending/approved/declined/timed_out) → `stage:"base"` |
| 14 | `TestUnknownVerb` | 2 | Unknown verb → `stage:"verb"`; no proposal registered |
| 15 | `TestUnresolvableRole` | 4 | swap+augment with unknown role when `known_roles` set → `stage:"adapt"`, `unresolved_roles`; no proposal registered; absent `known_roles` skips resolution (reaches gate) |
| 16 | `TestIllegalMutation` | 3 | Duplicate slot → `stage:"adapt"`; no proposal registered; no topology/override/spawn |

Key test technique for blocking gate (AT 10/11): `asyncio.create_task(s.call_tool("reshape_crew", ...))` runs the MCP call concurrently, `_poll_for_new_proposal(broker, existing_id=base_sid)` polls for the gate proposal, then `broker.resolve_proposal(...)` unblocks the awaiting `await_proposal` inside `reshape_crew`.

---

## Acceptance Tests Covered

- **AT 4** ✅ — `reshape_crew` is registered as an MCP tool (deletion-detector)
- **AT 10** ✅ — Gate decline → `stage:"gate"`, `status:"declined"`, crew untouched; timeout variant too
- **AT 11** ✅ — Gate approve → proposal `"instantiated"`, lineage chain proven, diff carried
- **AT 12** ✅ — Unknown base_shape_id → `stage:"base"`, no proposal registered
- **AT 13** ✅ — Each non-instantiated status (pending/approved/declined/timed_out) → `stage:"base"`
- **AT 14** ✅ — Unknown verb → `stage:"verb"`, no proposal registered
- **AT 15** ✅ — Unresolvable role (swap + augment) → `stage:"adapt"` + `unresolved_roles`; absent `known_roles` skips (AT 28-equivalent)
- **AT 16** ✅ — `ShapeValidationError` from `verb.apply` → `stage:"adapt"`, no mutation

## Out of Scope (left for reshape-crew-verbs task)

- Per-verb live effects: `broker.set_edge_override`, `broker.spawn_teammate`, `broker.record_topology`, `broker.kill_teammate`, `broker.remove_edge_overrides`, `broker.send` inform
- AT 5 (set_gate live), AT 6 (add_node live), AT 7 (augment live), AT 8 (drop live), AT 9 (swap ordering)
- `tests/test_reshape_crew_verbs.py`

---

## `git diff --name-status HEAD`

```
M	claude_crew/server.py
? tests/test_reshape_crew_gate.py    (untracked — new file)
```

---

## No Regressions

- `tests/test_shape_adapt_tool.py`: 14/14 passed (adapt_shape unchanged)
- `tests/test_server.py`: 34/34 passed (all existing tools unchanged)
