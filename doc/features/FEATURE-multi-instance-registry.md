# Feature: Multi-Instance Registry (Unified Dashboard)

**Status**: Implementation complete — awaiting Jerome's manual testing before merge to main
**Created**: 2026-04-30

---

## Phase 1: Research & Requirements

### Problem Statement

Each claude-crew instance only exposes its own broker's state through the Mission Control dashboard. When a developer runs two crews concurrently — e.g., Crew A planning a feature in one repo, Crew B reviewing a PR in another — they must open N separate browser tabs and remember which port maps to which crew. The dashboard is blind to every instance except the one it's directly served from.

The product vision's north star explicitly describes "two crews side-by-side on one workstation, both streaming into a live UI the developer can glance at without context-switching." Feature #12 (Mission Control UI) shipped the single-crew half; this feature ships the cross-crew aggregation that completes the vision.

This feature addresses SC #3 (multi-crew concurrency on one host without interference) and advances SC #4 (live observability across all crews) from "partially met" to "met."

### Success Criteria

- [ ] **SC-1**: When two or more claude-crew instances are running concurrently on one host, any instance's Mission Control dashboard shows state from all running instances — not just the local broker.
- [ ] **SC-2**: Each instance shown in the dashboard displays its `crew_id`, alive agent list, per-agent role/status/uptime, and that instance's message transcript (last 200 envelopes, same as single-instance today). Transcript data for remote instances is fetched from their `/api/state` endpoint and merged into the aggregated payload.
- [ ] **SC-3**: When an instance shuts down cleanly (atexit / SIGTERM handler runs), it disappears from all dashboards within 15 seconds (the next WebSocket push cycle after deregistration removes it from the aggregated state).
- [ ] **SC-4**: When a new instance starts after the dashboard is already open, it appears in the dashboard without requiring a page refresh (the next WebSocket push cycle picks it up from the registry).
- [ ] **SC-5**: When an instance has crashed (abnormal exit, no clean deregistration), the dashboard marks it as "unreachable" rather than silently omitting it. Stale entries are detected by checking PID liveness (`os.kill(pid, 0)`) and cleaned up automatically; the entry is removed from subsequent WebSocket pushes.
- [ ] **SC-6**: A stale or corrupt registry does not prevent a new instance from starting or its dashboard from loading. Specifically: (a) if the registry directory does not exist yet, it is created on first startup; (b) if a registry file is corrupt JSON, it is deleted and the instance proceeds with a local-only view; (c) if an HTTP fanout call to a remote instance's `/api/state` fails, that instance is shown as unreachable — other instances and the local instance are unaffected.
- [ ] **SC-7**: The registry uses per-instance files (`<crew_id>.json`), one file per process. Each process owns exactly one file and writes it with an atomic rename (`tmp` + `os.replace`). No inter-process locking is required; two instances registering simultaneously cannot corrupt each other's files.
- [ ] **SC-8**: The WebSocket `state` payload includes an `is_local: true` flag on the instance whose UIServer served the WebSocket connection. The browser dashboard uses this flag to visually distinguish the local instance from remote ones.
- [ ] **SC-9**: Remote instance HTTP fanout calls are bounded — each `/api/state` request has a timeout of ≤ 2 seconds. A slow or hung remote instance does not delay the local WebSocket push cycle beyond this bound.

### Questions

- [x] **OQ-1**: Single registry file vs. per-instance files. **Resolved: per-instance files** (`~/.local/state/claude-crew/instances/<crew_id>.json`). Each process owns exactly one file; concurrent writes cannot collide. Captured in SC-7.
- [x] **OQ-2**: Stale-entry detection. **Resolved: PID liveness check** — store `pid` + `started_at`; read with `os.kill(pid, 0)` (POSIX). Dead process → delete file. Cleanup happens on every registry read (i.e., every UIServer fanout cycle). Accept the small PID-reuse window as a known limitation (see Constraints below).
- [x] **OQ-3**: Server-side vs browser-side aggregation. **Resolved: server-side**. UIServer polls each remote instance's `/api/state` during `_build_state()`, merges results, pushes via existing WebSocket. Browser is unchanged. Captured in SC-9 (bounded timeout per fanout call).
- [x] **OQ-4**: Payload shape extension vs. new shape. **Resolved: extend existing shape.** Aggregation adds more entries to the `instances` array and more keys to the `transcripts` dict. The browser and all existing WebSocket tests require zero changes. Confirmed by Jerome.

### Constraints & Dependencies

- **Requires**: `claude_crew/ui_server.py` (Feature #12 — shipped), port auto-selection in `server.py` (shipped)
- **Local-machine only**: Registry is a filesystem artifact under `$XDG_STATE_HOME/claude-crew/instances/`. No network transport across machines.
- **No new PyPI dependencies**: stdlib (`pathlib`, `json`, `os`, `signal`, `atexit`) handles the registry. `httpx` is not currently imported in `ui_server.py` — if server-side fanout needs an async HTTP client, use `httpx.AsyncClient` (already in the dependency tree transitively via Starlette/uvicorn). Confirm before Phase 2 design.
- **Breaking changes**: None — additive only. Single-instance dashboards continue to work when the registry has only one entry.
- **Cross-platform target**: Linux and macOS. Windows not targeted (`os.kill(pid, 0)` is POSIX).
- **UIServer disabled case** (`CLAUDE_CREW_UI_PORT=0`): Instance must not register — no HTTP endpoint to aggregate from. Registry entry only created when `ui_port > 0`.
- **PID reuse window (accepted risk)**: Between a crash and the next PID-liveness check (≤ WebSocket push cycle, 1.5s), an unrelated process could theoretically reuse the dead PID. We do not cross-check `started_at` against procfs (would require platform-specific code). Accept this window as a known limitation; it self-resolves within seconds.
- **Filtering / search explicitly out of scope**: The product vision mentions "filterable by role and crew" in Core Capability #4. That is deferred. This feature ships the aggregation primitive; filtering is a follow-up.

**Gate**: Questions answered, success criteria measurable, constraints documented, user confirmed.

---

## Phase 2: Design & Specification

### Architecture Overview

Three components change or are introduced:

**`claude_crew/instance_registry.py` (new)** — Owns all registry I/O. Resolves the registry directory (XDG pattern, `CLAUDE_CREW_INSTANCE_REGISTRY_DIR` override). Writes per-instance files atomically. Reads all live entries (with PID liveness check). Cleans dead entries. No HTTP, no broker awareness — pure filesystem.

**`claude_crew/ui_server.py` (modified)** — `UIServer` gains a reference to `InstanceRegistry` and the local `port`. `_build_state()` becomes `async def` and does HTTP fanout to remote instances using `httpx.AsyncClient`. `serve()` registers on startup and deregisters on shutdown (try/finally). The payload shape grows `is_local: bool` on each instance entry; aggregated instances and transcripts are merged into the existing `{"instances": [...], "transcripts": {...}}` shape.

**`claude_crew/server.py` (modified)** — `main()` registers a SIGTERM handler so unclean exits (Ctrl-C, kill) still trigger deregistration. UIServer already receives `port` and `broker` — no new params needed.

**Data flow per WebSocket push:**
1. `_build_state()` reads registry directory → collects all `<crew_id>.json` entries
2. For each entry: check `os.kill(pid, 0)` liveness; delete file if dead
3. Separate remote entries (crew_id ≠ local) from local
4. `asyncio.gather(*[_fetch_remote(entry) for entry in remote_entries], return_exceptions=True)` — 2s timeout each
5. Build local instance dict (same as today, adds `is_local: True`)
6. For each remote: on success merge `instances` + `transcripts`; on failure/timeout → append `unreachable` entry to `instances`, no transcript key
7. Return merged payload

### Data / API Contracts

**Registry file schema** (`<crew_id>.json`):
```json
{
  "crew_id": "a1b2c3d4",
  "port": 7823,
  "pid": 98765,
  "started_at": 1746000000.0
}
```

**WebSocket payload (extended, same top-level shape):**
```json
{
  "type": "state",
  "data": {
    "instances": [
      {
        "id": "a1b2c3d4",
        "is_local": true,
        "label": "crew-a1b2c3d4",
        "status": "active",
        "agents": [...],
        "uptime": 120,
        "cost": 0.0,
        "tokens": {"in": 0, "out": 0},
        "cwd": "~",
        "branch": "main"
      },
      {
        "id": "e5f6g7h8",
        "is_local": false,
        "label": "crew-e5f6g7h8",
        "status": "active",
        "agents": [...],
        "uptime": 45,
        "cost": 0.0,
        "tokens": {"in": 0, "out": 0},
        "cwd": "~",
        "branch": "main"
      },
      {
        "id": "i9j0k1l2",
        "is_local": false,
        "label": "crew-i9j0k1l2",
        "status": "unreachable",
        "agents": [],
        "uptime": 0,
        "cost": 0.0,
        "tokens": {"in": 0, "out": 0},
        "cwd": "~",
        "branch": "main"
      }
    ],
    "transcripts": {
      "a1b2c3d4": [...],
      "e5f6g7h8": [...]
    }
  }
}
```

**`/api/state` HTTP endpoint**: unchanged — returns `_build_state()` result (now async, but response shape identical).

**`InstanceRegistry` public API:**
```python
class InstanceRegistry:
    def __init__(self, crew_id: str, port: int) -> None: ...
    def register(self) -> None: ...          # atomic write of <crew_id>.json
    def deregister(self) -> None: ...        # delete <crew_id>.json
    def read_all(self) -> list[dict]: ...    # live entries only (dead = deleted)

    @staticmethod
    def resolve_dir() -> Path: ...           # XDG-aware path resolution
```

### Design Decisions

- **D1: Per-instance files, one per crew_id** — Each process owns exactly one file and writes it without coordination. Two instances registering simultaneously write to different paths → no collision possible. Satisfies SC-7 by construction.
  *Carried into:* `InstanceRegistry.register()` path is `<dir>/<crew_id>.json`; `test_registry_concurrent_writes` verifies no corruption.

- **D2: Atomic write via tmpfile + `os.replace`** — Write to `<crew_id>.json.tmp`, then `os.replace(tmp, final)`. `os.replace` is atomic on POSIX. A crash mid-write leaves a `.tmp` file that is ignored on read (only `*.json` files are read, not `*.json.tmp`).
  *Carried into:* `InstanceRegistry.register()` implementation; `test_registry_write_is_atomic` verifies no partial reads.

- **D3: `_build_state()` becomes `async def`** — Server-side HTTP fanout requires `httpx.AsyncClient` for non-blocking IO. Making `_build_state` async is the correct fix; using sync `httpx.Client` would block the asyncio event loop during fanout calls. Both callers (`_handle_ws` and `_handle_state`) are already `async def`.
  *Carried into:* method signature; tests that call `ui._build_state()` must be updated to `await ui._build_state()`; `test_build_state_is_awaitable` verifies signature.

- **D4: Server-side aggregation** — UIServer fetches remote `/api/state` endpoints. Browser receives a single merged payload via the existing WebSocket protocol. No browser-side polling, no CORS headers needed. Keeps the frontend dumb — a design constraint from Feature #12.
  *Carried into:* `_build_state()` async fanout; no new frontend JS; existing WS message format preserved.

- **D5: Single long-lived `httpx.AsyncClient` with `asyncio.gather(..., return_exceptions=True)`** — All remote fetches run concurrently; no sequential blocking. `return_exceptions=True` means one slow or failed remote doesn't abort the others. A single `httpx.AsyncClient` is created in `UIServer.__init__` (not per push cycle — per-call discards connection pooling) and closed in `serve()`'s finally block alongside `registry.deregister()`. `httpx` must be added as an explicit dep in `pyproject.toml` — it is present in `uv.lock` (0.28.1) but neither Starlette nor uvicorn directly depends on it; relying on transitive presence is fragile.
  *Carried into:* `UIServer.__init__` (`self._http_client = httpx.AsyncClient(timeout=2.0)`); `serve()` finally block (`await self._http_client.aclose()`); `_fetch_remote_state()` uses `self._http_client`; `pyproject.toml` gains `httpx>=0.27`.

- **D6: 2-second per-remote timeout** — Satisfies SC-9. The WebSocket push loop runs every 1.5s; a 2s timeout means a slow remote can delay ONE push cycle at most, then the push resumes. The local instance is always served regardless of remote state.
  *Carried into:* `httpx.AsyncClient(timeout=2.0)` in `_fetch_remote_state()`.

- **D7: Registration in `serve()`, deregistration via try/finally + asyncio SIGTERM handler** — `serve()` calls `registry.register()` before `await server.serve()` and `registry.deregister()` (+ `http_client.aclose()`) in a finally block (covers normal exit and asyncio CancelledError from the anyio task group). SIGTERM handler must be installed **inside `async def _run()`** (in `server.py`) using `asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, registry.deregister)`. Installing it in `main()` before `anyio.run()` targets a loop that anyio replaces — the handler silently never fires. `registry.deregister()` is a synchronous filesystem call and is safe to call directly from a signal handler callback (no coroutine scheduling needed). No atexit — atexit handlers don't fire reliably on SIGTERM.
  *Carried into:* `UIServer.serve()` try/finally; `_run()` in `server.py` installs handler via `asyncio.get_running_loop().add_signal_handler()`; `test_deregistration_on_serve_cancel` cancels the serve task and asserts registry file absent; `test_sigterm_handler_deregisters` sends SIGTERM to a live subprocess and verifies registry file deleted.

- **D8: `is_local: bool` field in instance payload** — Determined by comparing `entry["crew_id"] == self._broker.crew_id`. The browser uses this flag to visually distinguish the local instance (bold label, different accent color). No new API surface — it's a field in the existing instance dict.
  *Carried into:* `_build_state()` local instance dict; `test_build_state_local_instance_is_marked`.

- **D9: PID liveness via `os.kill(pid, 0)`** — POSIX-only (Linux + macOS). Returns without error if process exists, raises `ProcessLookupError` if not. `PermissionError` means the PID exists but belongs to another user — treat as alive (conservative). Accept the small PID-reuse window as documented in Phase 1 constraints.
  *Carried into:* `InstanceRegistry.read_all()` liveness check; `test_dead_pid_entry_removed`.

- **D10: Registry path mirrors `transcript.py` XDG pattern** — Directory is `~/.local/state/claude-crew/instances/` (default), `$XDG_STATE_HOME/claude-crew/instances/` (if set), or `$CLAUDE_CREW_INSTANCE_REGISTRY_DIR` (explicit override for tests). Created with `mkdir(parents=True, exist_ok=True)` on first register.
  *Carried into:* `InstanceRegistry.resolve_dir()`; test fixtures set `CLAUDE_CREW_INSTANCE_REGISTRY_DIR` to `tmp_path`.

### Edge Cases

- **Registry directory absent on first start**: `register()` calls `mkdir(parents=True, exist_ok=True)` before writing. No error, directory is created.
- **Corrupt registry file** (invalid JSON): `read_all()` wraps each file read in try/except, logs a warning, deletes the corrupt file, continues. The instance running with the corrupt file is skipped (not shown as unreachable — it's unreadable).
- **Remote `/api/state` returns non-200 or malformed JSON**: `_fetch_remote_state()` returns `None`; the aggregation loop inserts an unreachable entry for that crew_id.
- **Remote `/api/state` returns `instances: []`** (instance started but no crew yet — startup race): `instances[0]` would raise `IndexError`. `_fetch_remote_state()` catches both `KeyError` and `IndexError`; treats as unreachable (the instance will appear correctly on the next push cycle once it has registered itself).
- **Remote `/api/state` times out** (2s exceeded): `httpx.TimeoutException` caught by `return_exceptions=True`; aggregation loop inserts unreachable entry.
- **Remote instance is live (PID check passes) but HTTP fails**: PID check on registry read passes (so file is not deleted), but HTTP fanout fails → shown as unreachable. On next cycle, same behavior until the process dies and PID check cleans it up.
- **Local instance is the only registered instance**: `read_all()` returns one entry (self). Fanout returns nothing. `_build_state()` returns exactly what it does today — no regression.
- **`CLAUDE_CREW_UI_PORT=0`**: UIServer is disabled, `serve()` is never called, `register()` is never called. No registry file written for this instance.
- **Same crew_id appears twice** (shouldn't happen — crew_id is generated fresh per Broker): `read_all()` deduplicates by crew_id (last file wins, but in practice this can't occur since filenames are crew_id-keyed).
- **Registry entry for local instance**: `_build_state()` skips remote fanout for `crew_id == self._broker.crew_id` (the local entry is built from the in-process broker directly, as today).
- **httpx not importable** (hypothetical — it's in the dep tree): `_fetch_remote_state()` should catch `ImportError` and mark all remotes as unreachable with a one-time log. Paranoid but cheap.
- **Zombie "unreachable" entry (PID reused)**: If a crashed instance's PID is reused by an unrelated process before the registry file self-cleans, `os.kill(pid, 0)` returns success (the new process is alive), the registry file is never deleted, and the dashboard shows a permanent "unreachable" entry. This resolves only when the new unrelated process also exits and the next liveness check finds the PID gone. Documented as accepted: PID reuse requires millisecond-level coincidence and the visible artifact is benign (stale "unreachable" badge, not data corruption). Users can manually delete `~/.local/state/claude-crew/instances/<crew_id>.json` to clear it immediately.

### Validation Contracts at Handoff Boundaries

| Boundary | Preconditions | Failure Behavior | Postconditions | Rollback |
|---|---|---|---|---|
| `serve()` → `register()` | Registry dir accessible | Log error, proceed without registration (local-only view) | `<crew_id>.json` present in registry dir | N/A (register is best-effort) |
| `_build_state()` → `read_all()` | Registry dir may or may not exist | Missing dir → return empty list | Returns list of dicts with `crew_id`, `port`, `pid`, `started_at` | N/A |
| `_build_state()` → `_fetch_remote_state()` | Remote instance may be unreachable | Timeout/error → returns `None`; aggregation inserts unreachable entry | On success: returns valid `/api/state` dict | N/A |
| `serve()` finally → `deregister()` | Instance file may already be absent (concurrent cleanup) or have wrong permissions | `FileNotFoundError` and `PermissionError` silently ignored (log warning on `PermissionError`) | `<crew_id>.json` absent from registry dir | N/A |

### Specification

**`claude_crew/instance_registry.py`** — new module:
```
class InstanceRegistry:
    crew_id: str
    port: int

    resolve_dir() -> Path          # XDG / env-var resolution; no I/O
    register() -> None             # mkdir + atomic json write
    deregister() -> None           # delete own file; silently ignore FileNotFoundError
    read_all() -> list[dict]       # read *.json, PID-check each, delete dead, return live
```

**`claude_crew/ui_server.py`** — modified:
- `__init__` gains `registry: InstanceRegistry | None = None` (None for tests that don't need aggregation)
- `_build_state()` → `async def _build_state()`:
  - reads `self._registry.read_all()` if registry set, else local-only
  - skips self in remote list
  - `asyncio.gather` fan-out with 2s timeout
  - merges results into extended payload
- `_handle_ws()`: `state = await self._build_state()`
- `_handle_state()`: `state = await self._build_state()`
- `serve()`: register before `await server.serve()`; deregister + `http_client.aclose()` in finally
- New private helper: `async def _fetch_remote_state(self, entry: dict) -> dict | None`

**Test migration note — `_build_state()` async conversion:**
`test_ui_server.py` has two categories of call sites:
- **Already-async tests** (`TestBuildStateWithTeammates`, `TestBuildStateTranscript`): marked `async def`, already use `await` on broker operations — these need `state = await ui._build_state()` (mechanical change).
- **Sync tests** (`TestBuildStateEmptyCrew`, lines ~73–113): marked with plain `def`, call `ui._build_state()` without `await`. These will silently return coroutine objects — assertions will pass vacuously (comparing `{}` keys to a coroutine), producing false-green tests. These 7 tests must be converted to `async def` + `await` explicitly. **Do not leave any sync `def test_*` that calls `_build_state()` directly.**
- **TestClient-based tests** (`TestHttpEndpoints`, `TestWebSocket`, `test_e2e_ui.py`): go through ASGI routing, never call `_build_state()` directly — no changes needed to test code, behavior is unchanged.

**`claude_crew/server.py`** — modified:
- `main()` creates `InstanceRegistry(crew_id=broker.crew_id, port=ui_port)`, passes to `UIServer`
- Installs SIGTERM handler that calls `registry.deregister()` then re-raises to allow graceful shutdown

**`tests/test_instance_registry.py`** — new test file (unit tests for registry module)

**`tests/test_ui_server.py`** — modified: all `ui._build_state()` calls become `await ui._build_state()`; existing tests pass `registry=None` (no-aggregation path, behavior identical to today)

**`tests/test_e2e_ui.py`** — modified: same async fix for `_build_state()` calls; add aggregation scenarios

### Assumptions

- **httpx as explicit dep** — httpx 0.28.1 is present in `uv.lock` but is not a direct dep of Starlette or uvicorn. It must be added to `pyproject.toml` explicitly. This is a task 1 prerequisite. *Default: add it in task 1; do not trust transitive presence.*

- **Registry dir write permission** — `~/.local/state/` is writable by the user. If not, `register()` logs and proceeds (local-only). *Default: assume writable; degrade gracefully if not.*

- **UIServer and InstanceRegistry share the same event loop** — `_build_state()` uses `asyncio.gather`. The UIServer runs inside an anyio event loop (already true). No threading concerns. *Default: single-loop assumption holds.*

- **`/api/state` response shape is stable across versions** — Remote instances may be running a slightly different version. We read `instances[0]` from the remote payload and merge it. If the shape is unexpected, `_fetch_remote_state()` catches the `KeyError` and returns `None` (unreachable). *Default: shape is stable within the same minor version; tolerate errors defensively.*

- **No cross-user crews** — All instances run as the same OS user, sharing the registry dir. Multi-user scenarios are out of scope. *Default: single user, same home directory.*

### Open Questions

*(None — all OQs resolved in Phase 1)*

---

## Phase 3: Task Breakdown

### Task 1: InstanceRegistry module
**Depends on**: None | **Blocks**: Tasks 2, 3

New `claude_crew/instance_registry.py` with XDG-aware path resolution, atomic per-instance file writes, PID liveness check, and dead-entry cleanup. New `tests/test_instance_registry.py`.

**Acceptance Criteria**:
```
Scenario: register writes a valid JSON file atomically
  Given a registry dir set via CLAUDE_CREW_INSTANCE_REGISTRY_DIR=tmp_path
  When InstanceRegistry(crew_id="abc123", port=7821).register() is called
  Then tmp_path/abc123.json exists with keys crew_id, port, pid, started_at

Scenario: deregister removes the file
  Given a registered instance
  When deregister() is called
  Then abc123.json no longer exists

Scenario: deregister is idempotent on FileNotFoundError and PermissionError
  Given the registry file was already deleted
  When deregister() is called
  Then no exception is raised

Scenario: read_all excludes entries whose PID is not alive
  Given a registry file with pid=99999999 (guaranteed dead)
  When read_all() is called
  Then the entry is not returned
  And the file is deleted from disk

Scenario: read_all skips and deletes corrupt JSON files
  Given a registry file containing invalid JSON
  When read_all() is called
  Then the corrupt file is deleted
  And no exception propagates

Scenario: read_all creates the registry directory on first use
  Given the registry directory does not exist
  When register() is called
  Then the directory is created and the file is written

Scenario: two instances with different crew_ids register without collision
  Given crew_id="aaa" and crew_id="bbb" both call register() concurrently
  When read_all() is called
  Then both entries are returned, neither corrupted
```

**Verification**: `uv run pytest tests/test_instance_registry.py -v` — all scenarios pass; `uv run pytest tests/ -v` — no regressions in existing suite.

---

### Task 2: UIServer async migration + aggregation
**Depends on**: Task 1 | **Blocks**: Tasks 3, 4, 5

Convert `_build_state()` to `async def`. Add `httpx.AsyncClient` (long-lived). Add `_fetch_remote_state()`. Add `registry: InstanceRegistry | None` param to `__init__`. Convert all 7 sync `_build_state()` call sites in `TestBuildStateEmptyCrew` to `async def` + `await`. Add `is_local` field. Add `httpx>=0.27` to `pyproject.toml`.

**Acceptance Criteria**:
```
Scenario: _build_state() with registry=None returns single local instance (no regression)
  Given UIServer(broker, port=0, registry=None)
  When await ui._build_state() is called
  Then result["instances"] has exactly one entry
  And result["instances"][0]["is_local"] is True
  And result shape matches existing tests (id, label, agents, transcripts)

Scenario: _build_state() merges a live remote instance
  Given UIServer with registry that reads one remote entry (different crew_id, mock /api/state)
  When await ui._build_state() is called
  Then result["instances"] has two entries
  And the local entry has is_local=True
  And the remote entry has is_local=False with data from the mocked /api/state

Scenario: slow remote (>2s) marked unreachable without delaying local
  Given a remote instance whose /api/state takes 5s to respond
  When await ui._build_state() is called
  Then the call completes in ≤3s
  And the slow remote appears with status="unreachable" and agents=[]

Scenario: remote /api/state returns instances=[] (startup race)
  Given a remote instance whose /api/state returns {"instances": [], "transcripts": {}}
  When await ui._build_state() is called
  Then the remote appears with status="unreachable" (no IndexError raised)

Scenario: all existing TestBuildStateEmptyCrew tests pass with await
  Given the 7 sync tests converted to async def
  When uv run pytest tests/test_ui_server.py is run
  Then all tests pass (no coroutine object comparisons)
```

**Verification**: `uv run pytest tests/test_ui_server.py -v` — all tests pass including converted async tests.

---

### Task 3: server.py wiring + SIGTERM handler
**Depends on**: Tasks 1, 2 | **Blocks**: Task 5

Wire `InstanceRegistry` into `main()`. Pass registry to `UIServer`. Install SIGTERM handler inside `async def _run()` using `asyncio.get_running_loop().add_signal_handler()`.

**Acceptance Criteria**:
```
Scenario: UIServer receives a registry when UI port > 0
  Given main() executes with CLAUDE_CREW_UI_PORT=auto
  When the UIServer is constructed
  Then UIServer._registry is an InstanceRegistry with the broker's crew_id and the bound port

Scenario: no registry when UIServer is disabled
  Given main() executes with CLAUDE_CREW_UI_PORT=0
  When main() runs
  Then no registry file is written to the instance registry dir

Scenario: SIGTERM during serve causes deregistration
  Given a live UIServer with a registry file on disk
  When SIGTERM is sent to the process
  Then the registry file is removed before the process exits

Scenario: serve() task cancellation causes deregistration
  Given UIServer.serve() running as an asyncio task
  When the task is cancelled (anyio task group shutdown)
  Then registry.deregister() is called
  And the registry file is absent from disk
```

**Verification**: `uv run pytest tests/test_server.py tests/test_e2e_ui.py -v` — no regressions; deregistration-on-cancel scenario passes.

---

### Task 4: Dashboard `is_local` visual distinction
**Depends on**: Task 2 | **Blocks**: Task 5

Update `claude_crew/ui/dashboard.html` to visually distinguish the local instance from remote instances using the `is_local` field present in each entry of the `instances` array. The local instance should be clearly labelled or styled differently (e.g., bold label, accent border, "local" badge). Remote instances display their `crew_id` label. Unreachable instances display a distinct visual state (grey, strikethrough, or warning icon).

**Acceptance Criteria**:
```
Scenario: WebSocket payload includes is_local flag
  Given a connected WebSocket client
  When the first state push arrives
  Then msg["data"]["instances"][0]["is_local"] is True (for the local instance)

Scenario: no regressions in existing dashboard tests
  When uv run pytest tests/test_e2e_ui.py -v
  Then all existing tests pass

Scenario: local instance visually distinguished (manual check)
  Given two instances running with the dashboard open
  When you look at the dashboard
  Then the local crew is labelled or accented differently from the remote crew
  And an unreachable instance shows a visual "unreachable" state
```

**Verification**: `uv run pytest tests/test_e2e_ui.py -v` — no regressions. Manual: open dashboard with two UIServer instances in the same test process using different crew_ids; confirm visual distinction.

---

### Task 5: E2E integration tests
**Depends on**: Tasks 1, 2, 3, 4 | **Blocks**: None

New `tests/test_e2e_multi_instance.py`. Tests exercise the full aggregation pipeline: registry write → registry read → HTTP fanout → merged WebSocket payload. No live SDK calls needed — StubTeammate and mock HTTP responses throughout.

**Happy Path Scenarios**:
```
Scenario: two instances both appear in _build_state output
  Given UIServer A (crew_id="aaa") and UIServer B (crew_id="bbb") both registered
  And UIServer A's registry reads both entries
  And a mock /api/state for crew B returns valid state
  When await uiserver_a._build_state() is called
  Then result["instances"] has two entries with ids "aaa" and "bbb"
  And result["transcripts"] has keys "aaa" and "bbb"
  And the "aaa" entry has is_local=True
  And the "bbb" entry has is_local=False

Scenario: serve() deregisters on clean task cancellation (SC-3)
  Given UIServer.serve() running as an asyncio task
  When the task is cancelled
  Then the registry file for that instance is absent from disk
  And subsequent read_all() does not include that crew_id
```

**Sad Path Scenarios**:
```
Scenario: dead registry entry not shown (SC-5)
  Given a registry file with a PID that does not exist
  When await ui._build_state() is called
  Then the dead entry is not in result["instances"]
  And the registry file has been deleted from disk

Scenario: unreachable remote shown as unreachable (SC-6c)
  Given a registry entry whose /api/state endpoint returns a connection error
  When await ui._build_state() is called
  Then result["instances"] contains the entry with status="unreachable" and agents=[]
  And the local instance is unaffected

Scenario: corrupt registry file skipped without crashing (SC-6b)
  Given a registry file containing "not json"
  When await ui._build_state() is called
  Then no exception propagates
  And the corrupt file is deleted
  And the local instance appears normally in result["instances"]
```

**Verification**: `uv run pytest tests/test_e2e_multi_instance.py -v` — all scenarios pass; `uv run pytest tests/ -v` — full suite clean.

---

**Gate**:
- ✅ 5 tasks, each independently testable
- ✅ Dedicated E2E test task with happy and sad path coverage
- ✅ Verification commands fail without the feature
- ✅ Each Phase 2 edge case traces to at least one BDD scenario
- ✅ User approved

---

## Phase 4: Implementation

**Branch**: `feature/multi-instance-registry`  
**Commit**: `153dd16`  
**Approach**: Kael direct (all 5 tasks in main session)  
**Completed**: 2026-04-30

All 5 tasks complete. 486 tests pass, 9 skipped (live-SDK only).

### Task completion summary

- **T1 (InstanceRegistry)**: `claude_crew/instance_registry.py` — 19 unit tests. XDG-aware path resolution, atomic writes, PID liveness, stale-entry GC.
- **T2 (UIServer async + aggregation)**: `_build_state()` made async, `_build_local_instance()` extracted, `_fetch_remote_state()` added with `is_local=True` search, long-lived `httpx.AsyncClient`, `_unreachable_instance()` helper. 40 tests (7 converted to async).
- **T3 (server.py wiring + SIGTERM)**: `InstanceRegistry` created and passed to `UIServer`; SIGTERM handler installed via `asyncio.get_running_loop().add_signal_handler()` inside `async def _run()` (not before `anyio.run()` — critical placement).
- **T4 (Dashboard is_local visual)**: `local` badge chip for local instance, red border + `unreachable` label for unreachable instances, `StreamColumns` unreachable guard.
- **T5 (E2E tests)**: `tests/test_e2e_multi_instance.py` — 20 tests covering multi-instance aggregation, deregistration, dead PID exclusion, unreachable remotes, corrupt registry, startup race, 2s timeout bound, and no-registry regression.

### Sentinel findings during implementation

Post-T5 Sentinel caught 3 issues, all fixed before commit:
1. `_fetch_remote_state()` was taking `instances[0]` unconditionally — changed to search for `is_local=True` first, fall back to `[0]`.
2. Startup-race test assertion was conditional; made unconditional `assert status == "unreachable"`.
3. No SC-9 timeout coverage existed; added `TestRemoteTimeout` with a real 10s-sleep server verifying completion in < 5s.

---

## Phase 5: Completion

### Verification
- [x] Feature works against Phase 1 success criteria
- [x] No regressions — full test suite passes (492 passed, 9 skipped)
- [x] Spec updated to match implementation
- [x] Jerome's manual testing complete
- [x] Merged to main

### Post-Implementation Fixes (same session, pre-merge)

After initial implementation, live multi-instance testing surfaced several bugs and prompted UX improvements. All resolved before merge.

**Orphan process fix** — `_mcp_then_cancel()` wasn't cancelling the anyio task group when MCP stdin closed. The uvicorn coroutine kept the process alive indefinitely after Claude Code exited. Fix: `_mcp_then_cancel()` calls `tg.cancel_scope.cancel()` in its finally block.

**Circular HTTP fanout fix** — Instance A's `_build_state()` would call B's `/api/state`, which would call A's `/api/state`, creating a deadlock. Fix: `/api/state?local=1` skips the registry fanout on remote calls. `_handle_state()` reads the query param and passes `local_only=True` to `_build_state()`.

**SDK teammate UI suppression** — SDK teammates inherit the host's MCP config and were spawning their own UIServers, polluting the instance registry with non-crew entries. Fix: `SdkTeammate` injects `CLAUDE_CREW_UI_PORT=0` into the subprocess environment via `ClaudeAgentOptions(env={...})`.

**Leader election** — First instance now atomically claims port 7821 (the stable, bookmarkable URL). Subsequent instances get OS-assigned ephemeral ports and poll every 20 seconds for 7821 to free up. When it does, the follower promotes itself — creates a new UIServer on 7821 and logs the promotion. Only the leader logs the dashboard URL to stderr; followers stay silent.

**Race-free port binding** — The original `_pick_ui_port()` probed a port (bind → get port number → release socket), then returned the port number. Two concurrent processes could both see port 7821 as free during the probe and both fail the subsequent uvicorn bind, landing on ephemeral ports. Fix: `_bind_ui_socket(preferred)` holds the socket open and passes it to uvicorn via `fd=sock.fileno()`. Uvicorn inherits the already-bound fd instead of binding again — no re-bind race. `SO_REUSEADDR` is set so a port in TIME_WAIT (left by a previous leader's connections) doesn't block promotion. `listen()` is called before returning the socket — this is the serialization point: with `SO_REUSEADDR`, two processes can both `bind()` the same port, but only one can `listen()`. 4 new tests in `TestBindUiSocket` cover hold-while-open, TIME_WAIT fallback, and concurrent-caller isolation.

**No HTML caching** — UIServer was caching the dashboard HTML in memory on first read, requiring a process restart to pick up CSS changes after a hard refresh. Removed the cache — `_get_html()` reads the file on every request. Cost is negligible (one small file read per page load).

**Dashboard UX polish** — bidirectional message display in agent columns (lead→agent and agent→lead); agent column headers show name bold + role dimmed when they differ; message body extraction from `payload["text"]` instead of `json.dumps` blob; body cap raised 500→2000 chars.

**Theme** — Blue palette inspired by the Clearwater Analytics design system (Poppins/Inter, navy-to-teal palette, bright blue accent). Higher-contrast lightness range (bg-0: 0.91 → bg-4: 0.54). Text anchored at near-black fg-0.

### Retrospective

**What went well**:

- The Sentinel chain caught real bugs. The SIGTERM event-loop placement issue (D7) was a silent correctness bug that would have gone undetected in testing — anyio replaces the event loop, so the handler installed before `anyio.run()` would never fire. Phase 2 Sentinel caught this from the spec before any code was written.
- The `is_local=True` search in `_fetch_remote_state()` was also Sentinel-driven. Blindly taking `instances[0]` was fragile; the fix makes aggregation order-independent.
- Kael direct worked cleanly for this feature — all 5 tasks were well-scoped enough to run sequentially without subagent handoffs.
- `asyncio.gather(..., return_exceptions=True)` + `httpx.AsyncClient(timeout=2.0)` gave exactly the bounded-fanout semantics SC-9 required, with no extra machinery.
- `_bind_ui_socket`'s `bind+listen` pattern is the correct atomic primitive for leader election. The `listen()` insight (two sockets with `SO_REUSEADDR` can both `bind()`, but only one can `listen()`) was confirmed empirically before being baked into the implementation.

**What was friction**:

- The `_build_state()` async migration required touching every existing test in `TestBuildStateEmptyCrew` individually (7 tests, each converted from `def` to `async def`). The `replace_all` edit tool helped, but the conversion still required care to avoid silent coroutine-object comparisons (the tests would pass vacuously if not properly awaited).
- The probe-then-bind race wasn't visible in tests — it only manifested with two live concurrent processes. A test that actually spawns two concurrent callers (like `TestBindUiSocket::test_two_concurrent_callers_get_different_ports`) is the only way to catch it. Write socket-binding tests that simulate concurrent use, not just sequential.
- HTML caching was a recurring friction point during dashboard iteration — every CSS change required a process restart to verify. Removing the cache eliminated this entirely at negligible cost.

**Improvements**:

- When migrating a sync test class to async, do a single targeted conversion pass before writing any new tests in that class. Don't interleave conversion and addition.
- Pre-Phase-3: for any feature touching async lifecycle methods, enumerate all test files that call those methods and note which need async migration. Prevents mid-implementation surprises.
- Any new socket-binding helper must include a concurrent-caller test that holds one socket open while a second tries to bind the same port. The sequential probe is not sufficient.

**Workflow updates made**:
- [ ] TEMPLATE.md or SKILL.md updated
- [ ] Project knowledge base updated (`.claude/rules/`)
- [ ] MEMORY.md updated (if cross-project insight)
