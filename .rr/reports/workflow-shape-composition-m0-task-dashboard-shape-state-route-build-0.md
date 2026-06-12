# Build Report — dashboard-shape-state-route (cycle 0)

**Feature:** workflow-shape-composition-m0  
**Task:** dashboard-shape-state-route (index 3)  
**Cycle:** 0  
**Verdict:** PASS  
**Date:** 2026-06-11

---

## Slice Test Result

```
uv run pytest tests/test_shape_dashboard.py
```

**24 passed** in 1.74s — all tests green on first run.

---

## Full Suite Result

```
uv run pytest --ignore=tests/test_shutdown_signals.py
```

**1408 passed, 34 skipped, 1 xfailed** in 139.44s — no regressions.

(The 2 pre-existing `test_shutdown_signals` failures are baseline infra flakes, excluded per task instructions.)

---

## What Was Implemented

### `claude_crew/ui_server.py` (modified)

1. **Added import**: `from claude_crew.shapes import shape_to_mermaid`

2. **`_build_local_instance` — added `shape_proposals` field** to the instance dict:
   - Each entry carries `{shape_id, crew_id, status, adaptation_diff, mermaid}`.
   - `crew_id` is load-bearing for the multi-instance proxy rule (dashboard leader must route `/shape-approval` POSTs to the owning follower when `crew_id != self._own_crew_id()`).
   - `mermaid` is pre-rendered via `shape_to_mermaid(p.shape)` (a `graph TD` source string with one node per slot and one edge per declared edge labeled by mode) so the dashboard can feed it directly into the existing `mermaid.render()` pipeline.

3. **Added `_handle_shape_approval`** (`POST /shape-approval/{crew_id}/{shape_id}`):
   - Validates path params against `_PATH_PARAM_RE` (400 on bad chars).
   - Parses `{"decision": "approve"|"decline"}` from the JSON body (400 on malformed JSON or invalid decision).
   - When `crew_id == self._own_crew_id()`: resolves locally via `self._broker.resolve_proposal()` and returns `{ok, shape_id, status}`.
   - Otherwise: proxies to the owning follower via `_proxy_shape_approval`.
   - Unknown local shape_id → 404; unexpected errors → 500.

4. **Added `_proxy_shape_approval`** (mirrors `_proxy_artifact`):
   - Looks up `crew_id` in `InstanceRegistry`; 404 if absent.
   - Validates port (int, 1–65535); 404 on invalid.
   - POSTs to `http://127.0.0.1:{port}/shape-approval/{crew_id}/{shape_id}` via `self._http_client`.
   - Proxy failure → 502.
   - The follower receives `crew_id == its own broker's crew`, so it serves locally (no re-proxy loop).

5. **Registered route** in `_make_app()`:
   ```python
   Route("/shape-approval/{crew_id}/{shape_id}", self._handle_shape_approval, methods=["POST"])
   ```

### `tests/test_shape_dashboard.py` (new, 24 tests)

Three test classes covering AT#11 and AT#12:

- **`TestStateShapeProposals`** (9 tests): `GET /api/state` includes `shape_proposals` with all required fields (`shape_id`, `crew_id`, `status`, `adaptation_diff`, `mermaid`); mermaid source matches `shape_to_mermaid()` output; multiple proposals all appear; approved proposals persist with updated status.

- **`TestShapeApprovalSingleInstance`** (10 tests): POST approve/decline returns `{ok, shape_id, status}`; broker proposal mutates; `await_proposal` unblocks after POST; 400 for bad path params/invalid decision/malformed JSON; 404 for unknown shape_id.

- **`TestShapeApprovalMultiInstance`** (5 tests): Leader proxies approve/decline to a real running follower; follower's broker reflects the resolution; unknown `crew_id` → 404; `registry=None` → 404; own `crew_id` served locally without proxy.

---

## Acceptance Tests Coverage

| AT | Description | Status |
|----|-------------|--------|
| AT#11 | Dashboard single-instance approval + DAG state | ✅ PASS |
| AT#12 | Dashboard multi-instance proxy | ✅ PASS |

---

## Git Diff

```
git diff --name-status HEAD
M       claude_crew/ui_server.py

git status --short
 M claude_crew/ui_server.py
?? tests/test_shape_dashboard.py
```

---

## Deferred / Out of Scope

- AT#13 (dashboard graphical mermaid render in browser — Playwright test) is owned by a different task slice.
- Frontend Approve/Decline UI wiring (dashboard.html) is out of scope for this slice.
- `adaptation_diff` display in the gate panel is handled by the frontend slice.
