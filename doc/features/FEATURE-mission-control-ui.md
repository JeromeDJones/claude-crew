# Feature: Mission Control UI

**Status**: Complete (Phase 5)
**Created**: 2026-04-30
**Branch**: master (shipped directly — no feature branch; SDD process was skipped)

---

## Phase 1: Research & Requirements

*Reconstructed retroactively from the implementation and design handoff.*

### Problem Statement

Operators running claude-crew have no real-time visibility into what their crew is doing. The only existing observability surface is the JSONL transcript file, which requires `tail -f` and parsing raw JSON. There's no at-a-glance view of which agents are active, what state they're in, or what messages are flowing between them. Success Criterion #4 from PRODUCT-VISION.md ("full crew conversation observable in real time") has been "Not started" since the product launched.

The design came from a Claude Design handoff bundle: the user had iterated on three layout explorations (topology graph + inspector, channels, mission control) and preferred the Mission Control direction — a top status bar, a strip of clickable CLI-instance cards, a mini topology graph, and parallel per-agent stream columns.

### Success Criteria

- [x] Starting `claude-crew` also starts an HTTP server on `CLAUDE_CREW_UI_PORT` (default 7821) serving the dashboard at `/`
- [x] Dashboard shows the active crew: all alive teammates, their roles, statuses (idle / thinking / tool-use), and uptime
- [x] Dashboard shows live message transcript, auto-updating every 1.5 seconds via WebSocket
- [x] Agent status dots animate (pulse) for thinking and tool-use states
- [x] Mini topology SVG shows agents orbiting the lead node, with animated pulses for active agents
- [x] Clicking instance cards focuses that crew in the workspace (single-crew v1 shows one card)
- [x] Dashboard uses a light theme (near-white background, dark text)
- [x] No new runtime dependencies added to `pyproject.toml`
- [x] All existing tests continue to pass

### Questions

- [x] **What data is available from the broker?** — `broker._info` (TeammateInfo), `broker._teammates` (live teammate objects with `status_snapshot()`), `broker._log` (append-only Envelope list). All synchronous reads, safe to call from a separate asyncio context.
- [x] **How to run HTTP alongside MCP stdio?** — FastMCP exposes `run_stdio_async()` as a coroutine; anyio task group runs both concurrently in the same event loop.
- [x] **Do we need new deps?** — No. `starlette` and `uvicorn` are already transitive deps from `mcp[cli]`.
- [x] **Model ID format?** — SDK model IDs look like `claude-sonnet-4-6`, `claude-opus-4-7`, `claude-haiku-4-5-20251001`. Normalized to `sonnet`/`opus`/`haiku` short labels.

### Constraints & Dependencies

- Requires: `broker._info`, `broker._log`, `broker._teammates`, `broker.crew_id` (all existing private attrs)
- Requires: `starlette`, `uvicorn` (transitive, no explicit dep needed)
- Breaking changes: No — `server.py` `main()` behavior changes only when `CLAUDE_CREW_UI_PORT != "0"`; existing stdio MCP contract unchanged
- Port: `CLAUDE_CREW_UI_PORT` env var, default 7821, set to `"0"` to disable

**Gate**: ⚠️ *Not run at the time. Reconstructed retroactively.*

---

## Phase 2: Design & Specification

### Architecture Overview

```
claude-crew process
├── MCP stdio server (FastMCP)          ← existing, unchanged
│   └── run_stdio_async()               ← anyio task in shared event loop
└── HTTP/WS UI server (Starlette)       ← new
    ├── GET /                           → serve dashboard.html
    ├── GET /api/state                  → JSON snapshot of broker state
    └── WS  /ws                         → push state every 1.5s
```

Both tasks run in the same anyio event loop started by `main()`. The broker is created in `main()` and passed to both `make_server()` and `UIServer`.

### Data / API Contracts

**`GET /api/state` and WebSocket `{"type": "state", "data": ...}` payload:**

```typescript
interface CrewState {
  instances: Instance[];
  transcripts: Record<string, Message[]>;
}

interface Instance {
  id: string;        // broker.crew_id
  label: string;     // "crew-<crew_id>"
  cwd: string;       // "~" (hardcoded v1)
  branch: string;    // "main" (hardcoded v1)
  uptime: number;    // seconds since first teammate spawned
  status: "active" | "idle";
  cost: number;      // 0.0 (not tracked in v1)
  tokens: { in: number; out: number };  // 0/0 (not tracked in v1)
  agents: Agent[];
}

interface Agent {
  id: string;        // info.id
  role: string;      // info.role
  model: "opus" | "sonnet" | "haiku";  // normalized from _model attr
  status: "idle" | "thinking" | "tool-use";
  uptime: number;    // seconds since info.spawned_at
  lastMsg: string;   // ISO timestamp of last_activity_at_wallclock, or now
  cost: number;      // 0.0
  tokens: { in: number; out: number };
  tools: string[];   // current in-flight tool names
  current_tool: string | null;
}

interface Message {
  t: string;         // ISO timestamp
  from: string;      // env.sender
  to: string;        // env.recipient
  kind: "msg";       // always "msg" in v1
  body: string;      // str(payload) capped at 500 chars
}
```

**Agent status derivation:**
```
current_tool_count > 0            → "tool-use"
current_turn_started_at != null   → "thinking"
otherwise                         → "idle"
```

**Model normalization:**
```
"opus" in model_id.lower()   → "opus"
"haiku" in model_id.lower()  → "haiku"
otherwise                    → "sonnet"
```

### Design Decisions

- **Same anyio event loop for both servers** — *Rationale:* broker state is asyncio-native; sharing the loop avoids thread-safety issues with broker mutations. — *Carried into:* `main()` anyio task group.
- **WebSocket polling (not push) at 1.5s** — *Rationale:* broker has no change-notification mechanism for UI consumers; polling is simplest correct v1. — *Carried into:* `UIServer._handle_ws()` sleep interval.
- **Read private broker internals directly** — *Rationale:* broker has no public read-only API beyond per-record methods. Direct read is fast; N+1 `get_teammate_status()` calls would be equivalent cost with more overhead. *Risk: fragile to broker refactors.* — *Carried into:* `UIServer._build_state()`.
- **Light theme** — *Rationale:* user feedback during implementation. oklch palette with bg-0=0.98 (near-white), fg-0=0.14 (near-black). — *Carried into:* `dashboard.html` CSS custom properties.
- **Port env var, disable with 0** — *Rationale:* subagent sessions or sandboxed environments may not want a side HTTP port. — *Carried into:* `main()` `CLAUDE_CREW_UI_PORT` check.

### Edge Cases

- **No teammates spawned yet** — agents list empty; instance status "idle"; UI shows "No teammates spawned yet."
- **Teammate dies mid-session** — `info.alive == False` → excluded from agents list on next push
- **Very large transcript** — capped at last 200 envelopes in `_build_state()`
- **Long message body** — capped at 500 chars
- **Unknown model ID** — falls through to "sonnet" default
- **WebSocket disconnect** — client auto-reconnects after 3s; connection dot shows amber during gap
- **Port already in use** — uvicorn logs error to stderr; MCP stdio task unaffected

### Assumptions

- **`broker._model` on SdkTeammate is stable** — accessed via `getattr(teammate, "_model", None)`. Guarded; won't crash on refactor but will silently return None → "sonnet".
- **`status_snapshot()` is safe off-loop** — reads only local state written from the same event loop; no cross-loop locks needed.
- **Single crew per process** — one Broker per `main()`. Multi-crew aggregation is a future feature.

### Open Questions

- [ ] **Should git branch be read from the real checkout?** — Hardcoded "main" in v1. Deferred.
- [ ] **Real token/cost tracking?** — All zeros in v1. Deferred to a usage-telemetry feature.

**Gate**: ⚠️ *Not run at the time. Reconstructed retroactively.*

---

## Phase 3: Task Breakdown

*Reconstructed retroactively. These are the BDD scenarios that should have driven the implementation — and still need to be written as tests.*

### Task 1: UIServer `/api/state` endpoint

**Acceptance Criteria**:
```
Scenario: State endpoint returns valid crew shape with alive agents
  Given a broker with two alive teammates (roles: "builder", "reviewer")
  When GET /api/state
  Then response is 200 JSON
  And instances[0].id == broker.crew_id
  And instances[0].agents has length 2
  And each agent has fields: id, role, model, status, uptime, lastMsg, cost, tokens, tools

Scenario: Dead teammates are excluded
  Given a broker where teammate "t-abc" is tombstoned (alive=False)
  When GET /api/state
  Then instances[0].agents does not contain "t-abc"

Scenario: Empty crew returns idle instance
  Given a fresh broker with no teammates
  When GET /api/state
  Then instances[0].agents == [] and instances[0].status == "idle"
```

**Verification**: `uv run pytest tests/test_ui_server.py -k "state"` ← **tests not yet written**

---

### Task 2: Agent status derivation

**Acceptance Criteria**:
```
Scenario: In-flight tools → "tool-use"
  Given status_snapshot returns current_tool_count=2
  Then agent.status == "tool-use"

Scenario: Active turn, no tools → "thinking"
  Given current_tool_count=0, current_turn_started_at != None
  Then agent.status == "thinking"

Scenario: Between turns → "idle"
  Given current_tool_count=0, current_turn_started_at=None
  Then agent.status == "idle"
```

**Verification**: `uv run pytest tests/test_ui_server.py -k "status"` ← **tests not yet written**

---

### Task 3: Dashboard HTML serves correctly

**Acceptance Criteria**:
```
Scenario: GET / returns valid HTML
  Given UIServer running on a test port
  When GET /
  Then 200 with Content-Type text/html
  And body contains "claude-crew" and "Mission Control"
  And body is parseable as valid HTML

Scenario: No broken local imports
  When dashboard.html is parsed
  Then all script/link src attributes reference CDN URLs, not local paths
```

**Verification**: `uv run pytest tests/test_ui_server.py -k "dashboard"` ← **tests not yet written**

---

### Task 4 (E2E): server.py integration

**Acceptance Criteria**:
```
Scenario: UI server disabled when CLAUDE_CREW_UI_PORT=0
  Given CLAUDE_CREW_UI_PORT=0
  When main() logic is exercised
  Then no HTTP server is started (UIServer.serve not called)

Scenario: Broker shared between MCP and UI server
  Given UIServer instantiated with the broker from make_server()
  When a teammate is spawned via the broker
  Then GET /api/state reflects that teammate
```

**Verification**: `uv run pytest tests/test_ui_server.py -k "main"` ← **tests not yet written**

---

**Gate**: ⚠️ *Not run at the time. No BDD scenarios were written before implementation.*

---

## Phase 4: Implementation

Implemented in a single shot by a builder subagent directed from the main session. Shipped directly to master with no feature branch. No per-task commits or sentinel review.

**Files created:**
- `claude_crew/ui_server.py` — `UIServer` class with `_build_state()`, Starlette routes, uvicorn runner
- `claude_crew/ui/dashboard.html` — self-contained React Mission Control dashboard (~600 lines)

**Files modified:**
- `claude_crew/server.py` — `main()` updated; `Broker` import added; anyio task group for co-run

**Gate**: ⚠️ *Not run. No sentinel review during implementation.*

---

## Phase 5: Completion

### Verification

- [x] Dashboard loads at `http://127.0.0.1:7821` when `claude-crew` starts
- [x] Alive agents appear with correct role and animated status dots
- [x] WebSocket auto-reconnects after 3s gap
- [x] Mini topology SVG renders with pulse animations for active agents
- [x] Light theme applied (oklch near-white bg, dark text)
- [x] 387 existing tests pass (9 skipped live tests unchanged)
- [ ] **`ui_server.py` has zero test coverage** — logged to backlog
- [ ] Feature branch was not used — shipped directly to master
- [x] PRODUCT-VISION.md updated
- [x] Backlog items logged

### Retrospective

**What went well:**

1. **Design handoff quality was exceptional.** The Claude Design prototype included a pixel-level README spec, TypeScript data shapes, implementation notes, and working React component code. Adaptation to production was faster because the handoff specified behavior, not just pixels.

2. **Zero dependency cost.** Starlette and uvicorn were already transitive deps from `mcp[cli]`. The feature added zero lines to `pyproject.toml`.

3. **anyio task group was the right integration point.** Sharing the event loop with the MCP server avoided all the thread-safety complexity that a background-thread approach would have introduced. `run_stdio_async()` was the clean hook.

4. **Light theme feedback landed cleanly.** The oklch token system made the palette shift a one-pass change with no component-level breakage.

**What was friction:**

1. **`ui_server.py` shipped with zero tests.** The entire module — `_build_state()`, `_derive_status()`, `_normalize_model()`, the WebSocket handler — has no test coverage. Any broker refactor that renames `_info`, `_log`, or `_teammates` silently breaks the UI with no failing test.

2. **SDD process was skipped.** The feature went from design handoff to implementation with no phase gates. The retro is being written after the fact. Gaps that a Phase 2 would have caught: the private broker API dependency, the missing git branch read, the zero token/cost data, the single-crew limitation.

3. **Private broker internals are a fragile coupling.** `_build_state()` reads `broker._info`, `broker._log`, `broker._teammates` directly. A `broker.snapshot()` method would be the correct seam and would make `ui_server.py` testable without reaching into private state.

4. **No feature branch.** Three files changed; the workflow says "branch when touching more than one file." Missing the branch means no clean diff, no PR, no merge-after-testing flow.

**Improvements:**

1. **Any new module > 50 lines must ship with a `tests/test_<module>.py`.** Add this as an explicit checklist item to Phase 4's per-task loop in SKILL.md. Even a 3-scenario smoke test is enough to catch the most common regressions.

2. **Phase 2 must ask: "Am I reading private attrs of another module?"** If yes, propose a public interface first. Add to Phase 2 checklist: "Does this feature access `_`-prefixed attributes of another module? Log as a coupling risk and propose a public seam."

3. **Feature branches are non-negotiable even for UI-only work.** One file changed → maybe skip. Two or more → branch, always. The workflow already says this; enforce it at the "shall I start implementing?" moment.

### Workflow updates made

- [x] `doc/features/FEATURE-mission-control-ui.md` — this file
- [x] `doc/PRODUCT-VISION.md` — capability #4 status updated, feature pipeline updated
- [x] `doc/BACKLOG.md` — 6 new entries from this feature
- [ ] `tests/test_ui_server.py` — deferred to backlog item #1
- [ ] `broker.snapshot()` public API — deferred to backlog item #2
