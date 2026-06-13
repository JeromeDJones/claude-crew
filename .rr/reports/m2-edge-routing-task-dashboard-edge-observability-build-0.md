# Build Report — dashboard-edge-observability — cycle 0

## Verdict
**PASS**

## Slice
- **Task:** `dashboard-edge-observability` (index 3)
- **Acceptance tests owned:** AT#11, AT#12, AT#13
- **Spec:** `specs/m2-edge-routing.md`
- **Cycle:** 0

## Test Command
```
uv run pytest tests/test_edge_dashboard.py
```

## Result
**17 passed, 0 failed, 0 errors** in 1.38s

## Implementation Summary

### `claude_crew/ui_server.py` — modified

1. **`topology_edge_stats` on `/api/state` instance payload** (AT#11):
   Added `topology_edge_stats` key to the `_build_local_instance` dict, serializing each `EdgeStat` from `snapshot.topology_edge_stats` as `{from_slot, to_slot, mode, exchanges, tripped, crew_id}`. `crew_id` is load-bearing for the multi-instance proxy (AT#13).

2. **`GET /edge-log/{crew_id}/{from_slot}/{to_slot}`** (AT#12, AT#13):
   - `_handle_edge_log`: validates path params with `_PATH_PARAM_RE`, routes locally when `crew_id == _own_crew_id()`, else proxies via `_proxy_edge_log`.
   - `_local_edge_log_response`: resolves slot names → teammate IDs via the most-recent topology in `snapshot.topologies`, then filters `snapshot.log` (with `log_limit=0` for full log) for direct-edge messages, gated wrappers (`payload.gated_for == to_id`), and tee cc copies (`cc_of` in payload).
   - `_proxy_edge_log`: mirrors `_proxy_artifact` — looks up owner in `InstanceRegistry`, sends a real `httpx.AsyncClient.get()` to `http://127.0.0.1:{port}/edge-log/{crew_id}/{from_slot}/{to_slot}`, returns proxied response.

3. **`POST /edge-promote/{crew_id}/{from_slot}/{to_slot}`** (AT#12, AT#13):
   - `_handle_edge_promote`: validates params, routes locally (`broker.promote_edge(from_slot, to_slot)`) or proxies via `_proxy_edge_promote`.
   - `_proxy_edge_promote`: mirrors `_proxy_shape_approval` — POSTs to the owning follower's port.

4. **Routes added to `_make_app`**:
   - `Route("/edge-log/{crew_id}/{from_slot}/{to_slot}", self._handle_edge_log)`
   - `Route("/edge-promote/{crew_id}/{from_slot}/{to_slot}", self._handle_edge_promote, methods=["POST"])`

### `tests/test_edge_dashboard.py` — new (191 lines)

- **`TestEdgeStatsOnApiState`** (AT#11, 4 tests): verifies `topology_edge_stats` appears on `/api/state` instance payload with correct shape, crew_id, mode, exchanges, and tripped fields.
- **`TestEdgeLogSingleInstance`** (AT#12, 5 tests): covers happy path (messages returned for direct edge), empty-messages path, correct response shape, 404 for no-registry proxy, 400/404 for traversal-unsafe params.
- **`TestEdgePromoteSingleInstance`** (AT#12, 4 tests): covers promote returns `{ok, edge, mode:"gated"}`, promote gates subsequent sends (route to LEAD), 404 for no-registry proxy, 400/404 for bad params.
- **`TestMultiInstanceEdgeAggregation`** (AT#13, 1 test): leader `_build_state()` includes follower's `topology_edge_stats` keyed by follower's `crew_id`.
- **`TestMultiInstanceEdgeLogProxy`** (AT#13, 3 tests): leader ASGI transport proxies `/edge-log/{follower_crew_id}/a/b` to follower's real HTTP port; leader serves own edge log locally; unregistered crew returns 404.

## Files Changed
```
M  claude_crew/ui_server.py
?? tests/test_edge_dashboard.py
```

## Scope Creep
None. Only `claude_crew/ui_server.py` and `tests/test_edge_dashboard.py` were touched. `claude_crew/ui/dashboard.html` was not modified — the spec Out-of-Scope section explicitly excludes the SVG decoration layer from the green suite. No files owned by sibling tasks (`broker.py`, `sdk_teammate.py`, `teammate_prompt.py`, `server.py`) were touched.

## Full Suite Non-Regression Check
Full suite (excluding live SDK and Playwright tests): **1498 passed, 2 failed, 21 skipped, 1 xfailed**.

The 2 failures are in `tests/test_shutdown_signals.py::TestSignalShutdown` (SIGTERM + SIGINT tests that spawn a real `claude-crew` process). These failures were confirmed pre-existing by `git stash` + re-run before my changes — both fail identically on the base branch with `"claude-crew did not register within 15.0s"` (auth/environment issue in this sandbox, not related to this slice).

## Deferred / Known Gaps
- `dashboard.html` SVG decoration layer (edge coloring, pulse animation, click hit-testing, promote affordance): explicitly Out of Scope per spec — verified manually / Playwright probe, not in green suite.
- Edge-log filtering for tee cc copies and gated wrappers: implemented but the test suite exercises only the direct-edge path (consistent with the spec's AT#12 which uses a direct edge). The gated/tee filter paths are covered by code inspection.
