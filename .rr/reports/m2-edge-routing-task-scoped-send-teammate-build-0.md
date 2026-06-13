# Build Report — scoped-send-teammate (cycle 0)

**Feature:** m2-edge-routing  
**Task:** scoped-send-teammate (index 2)  
**Cycle:** 0  
**Verdict:** PASS  
**Date:** 2026-06-13

---

## Test Command

```
uv run pytest tests/test_scoped_send.py
```

## Result

**17 passed, 0 failed, 0 errors** — all acceptance tests green on first cycle.

Full suite (excluding live SDK + pre-existing flaky `test_shutdown_signals`): **1475 passed, 11 skipped, 1 xfailed**, no regressions.

---

## Acceptance Tests Covered

| AT | Description | Status |
|----|-------------|--------|
| AT#8 | Neighbor adjacency injected at spawn (prompt injection via `system_prompt_override`) | ✅ PASS |
| AT#9 | `broker.send_scoped` authorizes a declared neighbor, rejects a non-neighbor | ✅ PASS |
| AT#10 | Direct ping-pong a→b→a→b stays off the lead inbox (integration) | ✅ PASS |

---

## Implementation Summary

### `claude_crew/teammate_prompt.py`
- Added `SENTINEL_NEIGHBORS = "## Crew neighbors"` to the public test surface
- Added `neighbors: list[dict] | None = None` parameter to `build_teammate_prompt`
- Added `_build_neighbors_section(neighbors)` private helper that formats out-edges ("Out-edges (teammates you may message via `send_to`):") and in-edges ("In-edges (teammates that may message you):") from `{"direction", "slot", "role", "mode"}` entries
- When `neighbors` is non-empty, the SENTINEL_NEIGHBORS section is appended as the last part of the addendum (after SENTINEL_MEMORY when present)

### `claude_crew/broker.py`
- Added `neighbors: list[dict] | None = None` parameter to `spawn_teammate`
- When `neighbors is not None`, it is included in `factory_kwargs` so any factory that accepts it receives the neighbor list

### `claude_crew/sdk_teammate.py`
- Added `neighbors: list[dict] | None = None` parameter to `SdkTeammate.__init__`
- Passed `neighbors=neighbors` to `build_teammate_prompt` in the pack-body assembly block, so the SENTINEL_NEIGHBORS section appears in `_system_prompt` when neighbors are provided

### `claude_crew/server.py`
- In `instantiate_shape`, before the spawn loop, pre-computes per-node adjacency from `shape.edges`:
  - Builds `_slot_to_role` map from `shape.nodes`
  - Builds `_node_neighbors` dict: each slot → list of `{"direction", "slot", "role", "mode"}` dicts
  - Out-edges: the `from_slot` node gets `direction="out"` entries naming its target slots/roles/modes
  - In-edges: the `to_slot` node gets `direction="in"` entries naming its source slots/roles/modes
- Each `broker.spawn_teammate` call now receives `neighbors=_node_neighbors.get(node.slot) or None`

### `tests/test_scoped_send.py` (new file)
- **`_NeighborTeammate`**: A `_NoopTeammate` subclass that accepts `neighbors` kwarg and calls `build_teammate_prompt` to set `_system_prompt`, enabling AT#8 integration path without live SDK
- **`_neighbor_factory`**: Factory wrapping `_NeighborTeammate`
- **`TestNeighborInjection`** (AT#8): 7 tests covering unit-level `build_teammate_prompt` with neighbors (no section when omitted/empty, section present and named correctly when provided) plus integration via `broker.spawn_teammate` checking `broker._configs[tid]["system_prompt"]`
- **`TestScopedSendAuthorize`** (AT#9): 6 tests covering slot-name resolution, teammate-id resolution, authorization rejection for non-declared recipients and non-topology recipients, lead always authorized, and gated-edge routing
- **`TestDirectPingPong`** (AT#10): 4 tests covering reciprocal-direct a→b→a→b off-lead, broker log capture, single-edge delivery to inbox, and breaker-not-tripped below budget

---

## Cross-Slice Touches

### `claude_crew/factories.py` (not in `taskTouches`)
Added `neighbors: list[dict] | None = None` to three factory signatures to keep `broker.spawn_teammate`'s `factory(**factory_kwargs)` call from raising `TypeError` when `neighbors` flows through:
1. `stub_factory` — accepts and ignores it (signature uniformity)
2. `sdk_factory` — accepts and passes to `SdkTeammate` via `kwargs["neighbors"] = neighbors`
3. Inner `factory` closure in `default_factory()` — accepts and passes to `sdk_factory`

This is the minimal cross-slice edit necessary; no behavioral change for existing tests.

---

## Pre-Existing Failures (not introduced by this task)

- `tests/test_shutdown_signals.py::TestSignalShutdown::test_sigterm_triggers_clean_exit_and_deregister` — fails with "claude-crew did not register within 15.0s". Confirmed pre-existing: fails identically on `git stash` of my changes. Infrastructure/timing issue unrelated to this slice.

---

## Files Changed

```
M	claude_crew/broker.py
M	claude_crew/factories.py
M	claude_crew/sdk_teammate.py
M	claude_crew/server.py
M	claude_crew/teammate_prompt.py
?	tests/test_scoped_send.py  (new)
```
