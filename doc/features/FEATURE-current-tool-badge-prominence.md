# Feature: Agent-header `current_tool` Badge Prominence Boost (#22)

**Status**: In Progress (Phase 1)
**Created**: 2026-05-01
**Branch**: `feature/current-tool-badge-prominence`
**Vision row**: #22 (S-size, capability #4 — "in-flight badge prominence" deferred from #19)
**Lineage**: #19 D-13 → BACKLOG entry 2026-05-01 → promoted to pipeline row #22 at #19 merge.

---

## Phase 1: Research & Requirements

### Problem Statement

When a teammate runs a long-lived tool — the canonical case is a 90-second `Bash` — the Mission Control dashboard goes silent at the operator's eye level. #19 deliberately scoped the tool-event *stream* to completed-only entries (SC-7) on the principle that the stream is for archival fact, not transient state. In-flight visibility was assigned to the agent-header `current_tool` badge — a job the badge today does not do well:

- **Plaintext, not visually distinct.** Rendered as a 10px monospaced span (`dashboard.html:502–506`) inside the secondary status row, sharing space with status text and time-since-last-message. No animation on the tool name itself; only the status dot pulses.
- **Buried among siblings.** The agent header already carries avatar, name, role, agent ID, status dot, model badge, token count, and cost across two rows. The tool badge is the ninth element competing for attention in ~192px of usable column width.
- **No elapsed-time signal.** Operators cannot see "this Bash has been running 23s and counting" from the badge — the data exists (`current_tools[].started_at_wallclock`) but is not surfaced. The longer the silence, the less the operator knows.
- **No column-level scanning affordance.** With N≥4 agents on screen, the operator scans for "which column is alive vs. stuck." A 10px chip among eight other chips loses that race regardless of how prominent it is *within* a column.

Result: the original "operator stares at silence during a 90s Bash" problem statement that motivated #19's stream work is partially still open. #19 closed the archival half (completed events flow into the stream); #22 closes the in-flight half (operator sees activity *while* it's happening, scannable across columns, with growing elapsed time).

**Why now:** co-architect's MERGE-WITH-NOTE flag at #19 explicitly called for scheduling this before other dashboard polish — context is fresh, the data path (`current_tools` list with timestamps) is already in place, and this directly improves operator confidence during the next real-task validation (MMM-4b).

### Success Criteria

**Operator-silence closure (the why):**

- [ ] **SC-1. Column-level scannability.** With ≥4 agent columns visible and one teammate executing a tool for >5 seconds, an operator can identify *which* column has an active long-running tool without reading any text — purely from a column-level visual affordance (border, header background, accent bar, or equivalent). The affordance is distinct from idle, thinking-without-tool, and completed states. *(Closes the column-scanning gap; chip-level changes alone do not satisfy this.)*

- [ ] **SC-2. Elapsed seconds visible and growing.** For an in-flight tool, the badge displays `tool_name · Ns` where N is integer seconds since `started_at_wallclock`. The displayed N advances at least once per second of wall-clock time on the client, even between server polls (1.5s WebSocket cadence).

- [ ] **SC-3. Pulse threshold at 5s.** Below 5s elapsed, the tool-name text itself does NOT pulse or shimmer (the existing status-dot pulse is sufficient). At ≥5s elapsed, an additional visual signal becomes active on the badge (e.g., the elapsed counter or the badge background animates). The threshold prevents fast tools (Read, Edit, sub-second Bash) from creating constant motion noise.

- [ ] **SC-4. Settle frame on completion.** When PostToolUse fires and `current_tools` empties, the badge does not jump-cut to gone. The final duration is rendered for ≥500ms (`Bash · 23s ✓` or equivalent) before the badge clears. Operators see the resolution instead of a flicker. **Sad-path scope:** the settle frame is REQUIRED only for the normal Pre→Post completion path. On `kill_teammate` or SDK death (`_close_open_tools(reason="kill"|"death")`), the teammate is tombstoned and the agent column is removed from `_build_local_instance` (`ui_server.py:214`), so badge state is moot — no settle frame attempted, no flicker risk because the column itself is gone. The spec MUST NOT require a settle frame on kill/death.

**Truthfulness under parallelism (Pillar 1 resolved):**

- [ ] **SC-5. Parallel-tool truth.** When `current_tools` contains N>1 entries, the badge surfaces the **oldest in-flight** tool (the one most likely to be the operator's actionable signal — it's the one that's been running longest), plus a count indicator (e.g., `Bash · 23s +2` when 2 additional tools are also in-flight). N≤1: no count suffix. **Source field — locked:** the source of truth is `current_tools[0]` from the snapshot (the list is sorted ascending by `started_at_wallclock` per `teammate.py:246`, so index 0 is oldest). The legacy `current_tool` scalar (last-started, `teammate.py:248`) is NOT used by the badge. To preserve SC-9 backward compatibility, `current_tool` keeps its existing semantic; #22 adds a new additive payload field named **`oldest_in_flight`** (an object with `{tool_name, tool_use_id, started_at_wallclock}` mirroring `current_tools[0]`, or `None`). The badge MUST NOT silently render only the last-started tool, because that produces a confidently-wrong signal under parallel-tool dispatch (e.g., a 2s Read displacing the 23s Bash).

**Clock correctness (Pillar 2 resolved):**

- [ ] **SC-6. Clock-skew-safe elapsed.** Elapsed seconds are computed from a single producer's clock: `now_wallclock - started_at_wallclock`. **Field — locked:** `now_wallclock` is added to each per-instance dict in the `/api/state` response (NOT per-agent), at the same level as `instance.id`/`instance.uptime`. Stamped exactly once per `_build_state` call via a single `time.time()` at the top of `_build_local_instance`, then propagated into the instance dict. Per-instance is sufficient because `_build_state` runs synchronously and one `time.time()` covers every agent on that instance; per-agent stamping would just add bytes and create the illusion of finer resolution we don't have. Each instance's `oldest_in_flight.started_at_wallclock` is then paired with that *same instance's* `now_wallclock` — never crossing instances, so multi-instance clock skew is structurally impossible. The client ticks the displayed counter locally between server polls (UI smoothness) using `Date.now()/1000 - server_now_at_arrival + (now_wallclock - started_at_wallclock)`, snapping to server-stamped truth on each refresh.

**Layout discipline (Pillar 3 partially resolved; Phase 2 picks the column affordance):**

- [ ] **SC-7. Pinned, never horizontally scrolled.** The badge lives within the existing `flexShrink: 0` pinned region of `AgentStreamColumn` (the header rows above the scrollable message list). At the minimum column width (220px from `gridTemplateColumns: minmax(220px, 1fr)`), the badge text either fits or truncates with ellipsis — it never pushes the column into horizontal scroll, never overflows visibly, and never gets pushed below the scroll fold.

- [ ] **SC-8. Layout stability across tool start/end.** Tool-start and tool-end MUST NOT cause the agent header to jump in height or shift other elements. The space the badge will occupy when present is reserved or absent in a way that the overall column geometry is stable. (Sad path: rapid Pre/Post pairs from sub-second tools should not produce a strobing layout.)

**Compatibility (no regression to shipped surfaces):**

- [ ] **SC-9. /api/state remains additive-only.** Any new field added to the per-agent payload (the candidate is `now_wallclock` or `current_tool_started_at`, decided in Phase 2) is purely additive. Existing fields (`current_tool`, `current_tool_count`, `current_tools[]`, `status`, `tools[]`, etc.) keep their semantics. A pre-#22 dashboard hitting a #22 server still renders correctly (degrades gracefully — no elapsed counter, but no broken UI).

- [ ] **SC-10. #19 stream rendering unchanged.** Tool events in the per-agent transcript stream remain completed-only, ordered by raw float timestamp, with the existing body format. Badge and stream remain complementary surfaces; #22 does not touch the stream-rendering branches in `MiniMessage` for `kind: "tool"`.

**Test discipline:**

- [ ] **SC-11. JSON-boundary tests prove the data path.** A `test_ui_server.py` test asserts the agent payload contains the fields needed to render badge state (oldest-in-flight tool, elapsed reference timestamp, count). An `test_e2e_tool_events.py`-style integration test exercises the broker → snapshot → /api/state flow with synthetic in-flight state and asserts the wire payload. No DOM/screenshot tests — they are out of scope per CLAUDE.md test conventions.

### Three-Pushback Resolutions (from co-architect Phase 1 warmup)

1. **Pillar 1 — Single-slot vs. parallel-tool reality.** Resolved at Phase 1 (SC-5). The badge surfaces *oldest in-flight* (operator-actionable: the one stuck), with `+N` count suffix when N>1. Last-started semantics (current `current_tool` scalar) is rejected for the prominent surface — it produces a confidently-wrong signal under parallel dispatch.

2. **Pillar 2 — Elapsed seconds clock source and tick cadence.** Resolved at Phase 1 (SC-6). Server stamps `now_wallclock` per snapshot build; client computes initial elapsed from server truth, ticks locally for smoothness, snaps to truth on next poll. Producer-clock pairing across `now_wallclock` and `started_at_wallclock` eliminates multi-instance clock skew. Settle frame (SC-4) handles the in-flight→completed transition explicitly.

3. **Pillar 3 — Layout problem vs. hierarchy problem.** *Partially* resolved at Phase 1. The badge IS both: column-level affordance for cross-column scanning (SC-1) AND chip-level prominence for in-column investigation (SC-2/3). The exact column-level treatment (border tint, header bg shade, accent bar, etc.) is a Phase 2 design call. SC-1 sets the bar — "scannable across ≥4 columns without reading text" — without dictating the implementation.

### Open Questions (Phase 2 calls — co-architect-arbitrated)

- [ ] **OQ-1: Column-level affordance choice.** Border tint vs. header background shade vs. top accent bar vs. AgentAvatar halo — which gives the strongest scan signal at SC-1 thresholds without competing with status-dot color or breaking the existing visual hierarchy?

- [ ] **OQ-2: Where the chip lives.** Stay in the existing status row (line 494–508) with reformatted styling, OR promote into the primary header (line 467–491) alongside name/avatar, OR carve a third row dedicated to in-flight tool? Each has tradeoffs (visual weight, layout stability across tool start/end per SC-8, vertical real estate).

- [ ] **OQ-3 [RESOLVED at Phase 1]: `now_wallclock` field placement.** Resolved per-instance (see SC-6). Closed.

### Sentinel-Surfaced Phase 2 Inputs (carried forward)

These are flagged in the Phase 1 sentinel review as design considerations for Phase 2 (not Phase 1 SC gaps):

- **S-1. `tools[]` array vs. badge redundancy.** `ui_server.py:207` emits a `"tools"` array (all in-flight tool names). With #22's `oldest_in_flight` + count, this becomes redundant for the badge UX. Phase 2 decides: keep `tools[]` for backward compat (and document it as the "full set" while badge shows oldest+count), or deprecate. Either way, document the relationship.
- **S-2. SC-2 client-tick is untestable at the JSON boundary.** The "advances at least once per second" liveness lives entirely in client JS. SC-11 scopes tests to JSON shape. Phase 2 must explicitly mark SC-2's tick guarantee as visual-verification-only (manual inspection during implementation), not automated, and document that decision so future readers don't assume there's a test.
- **S-3. SC-8 layout-stability sad path.** The strobing-layout risk lives in the client render loop, not the JSON payload. The JSON-boundary test for SC-8 asserts type-stability of the snapshot fields (`now_wallclock` always present and `float`; `oldest_in_flight` toggles between object and `None` cleanly). The DOM-level stability claim is visual-verification-only.

### Constraints & Dependencies

- **Requires:** existing `current_tools` list shape (#8/F8); `_POLL_INTERVAL` cadence (1.5s); CSS Grid layout (`minmax(220px, 1fr)`); existing `pulse` keyframe.
- **Breaking changes:** No. SC-9 forbids any non-additive `/api/state` change.
- **Performance implications:** Negligible (one float per snapshot, 1Hz client tick via `setInterval` or `requestAnimationFrame`).
- **Out of scope:** stream-rendering changes (#19 territory); new producer-side telemetry; browser/DOM-rendering tests (no Playwright); audio or desktop-notification escalation.

**Gate**: Sentinel review of SCs next.

---

## Phase 2: Design & Specification

### Architecture Overview

#22 is a *display-layer* feature: the producer (teammate hooks → broker snapshot) already emits the data needed (`current_tools[]` with `started_at_wallclock`, `last_tool_completed`). The work is on the consumer side — UIServer adds two additive payload fields (`now_wallclock` per-instance and `oldest_in_flight` per-agent), the dashboard renders three new visual elements (column-top accent bar, dedicated tool-chip row, pulse-on-stuck animation), and the client tick smooths elapsed display between server polls. No producer-side change. No protocol change beyond additive fields.

The design splits the operator-silence signal across two reinforcing layers:
- **Cross-column scan layer** (SC-1) — top accent bar at column edge, color-tinted per `--st-tool`, animated when ≥5s elapsed. Operator scans column-tops and finds the active one.
- **In-column investigation layer** (SC-2/3/4) — dedicated chip row showing `tool_name · 23s [+N]`. Tells operator *what* and *how long*, with stuck-pulse and settle-frame transitions.

These layers are visually independent (top edge vs. mid-header) and reinforce the same fact, so operator confidence scales whether they glance or fixate.

### Data / API Contracts

**Producer-side (no change):** existing `current_tools` list on each `LiveTeammateInfo.status` snapshot, sorted ascending by `started_at_wallclock` per `claude_crew/teammate.py:246`.

**UIServer `/api/state` per-instance (additive — D-4):**
```json
{
  "id": "abcd1234",
  "is_local": true,
  "label": "crew-abcd1234",
  "cwd": "/home/jerome/dev/claude-crew",
  "branch": "feature/current-tool-badge-prominence",
  "uptime": 456,
  "status": "active",
  "cost": 0.123,
  "tokens": {"in": 5000, "out": 2000},
  "now_wallclock": 1730482845.123,    // NEW: server-stamped, single time.time() per _build_local_instance call
  "agents": [/* per-agent dicts below */]
}
```

**UIServer `/api/state` per-agent (additive — D-3, D-7):**
```json
{
  "id": "t-12345678",
  "role": "builder",
  "name": "builder",
  "model": "sonnet",
  "status": "tool-use",
  "uptime": 123,
  "lastMsg": "2026-05-01T14:32:15.000Z",
  "cost": 0.042,
  "tokens": {"in": 1500, "out": 800},
  "tools": ["Bash", "Read"],            // FROZEN as-of-#22 (D-8); no new consumers; full set of names
  "current_tool": "Read",               // LEGACY (D-8); last-started semantic retained for SC-9
  "oldest_in_flight": {                 // NEW (D-3); None when no in-flight tool
    "tool_name": "Bash",
    "tool_use_id": "toolu_01abc...",
    "started_at_wallclock": 1730482822.456
  },
  "in_flight_count": 2,                 // NEW (D-3); convenience scalar; mirrors len(current_tools)
  "last_tool_completed": {              // NEW EXPOSURE (D-7); already on snap, surfaced for settle frame
    "tool_name": "WebFetch",
    "outcome": "ok",
    "finished_at_wallclock": 1730482820.0,
    "duration_seconds": 1.2,
    "error_summary": null
  }
}
```

`oldest_in_flight` is `current_tools[0]` shape-mirrored (no `args_summary` — operator-readable badge does not need redacted args; the chip is `tool_name · elapsed [+N]`, not `tool_name(args)`). When `current_tools` is empty, `oldest_in_flight` is `None` and `in_flight_count` is `0`.

`last_tool_completed` was previously available only via `get_teammate_status` MCP (broker.py:573 area); #22 surfaces it on the per-agent UI payload so the dashboard's settle-frame logic doesn't need a second round-trip.

### Design Decisions

- **D-1. Top-of-column accent bar for cross-column scan signal.** *Rationale:* the eye lands on column-tops when scanning N≥4 columns left-to-right; placing the SC-1 affordance there is the cheapest scannable signal that doesn't compete with status-dot color (spatially separated) and doesn't contaminate the avatar/model-badge color system (rejected: header background shade conflicts with model-badge purple at hue 290 = `--st-tool` hue). *Carried into:* `dashboard.html` new CSS class `.agent-column-accent` (3px, full column width) — applied conditionally in `AgentStreamColumn`. Test: visual decision documented in this section + `test_ui_server.py` asserts agent payload contains the data the bar reads (`oldest_in_flight` set ⇒ bar visible).

- **D-2. Dedicated tool-chip row, always reserved (~22px).** *Rationale:* SC-8 layout stability requires the slot to exist whether or not a tool is in-flight; the existing status row at `dashboard.html:494-508` is already crammed at 220px min column width and would either truncate or wrap on rapid Pre/Post pairs (rejected: in-place reformat). Promoting into the primary header (rejected) means evicting an identity element (agent ID) from the diagnostic surface. A new third row keeps identity rows untouched and reserves 22px once. *Carried into:* `dashboard.html` `AgentStreamColumn` adds a third `flexShrink: 0` div between status row (line 494) and scrollable messages (line 510); always-rendered with conditional content (chip OR transparent placeholder of equal height).

- **D-3. Per-agent `oldest_in_flight` field, additive.** *Rationale:* SC-5 mandates oldest-in-flight semantic for the badge; the existing `current_tool` scalar is last-started (`teammate.py:248`) and SC-9 requires its semantic preserved. Cleanest path is a new field, not a redefinition. *Carried into:* `ui_server.py:_build_local_instance` reads `snap.get("current_tools", [])` and computes `oldest_in_flight` via **explicit key allowlist** — NOT a `del` or `pop` on the source dict (that would mutate the snapshot). Concretely: `oldest_in_flight = {"tool_name": current_tools[0]["tool_name"], "tool_use_id": current_tools[0]["tool_use_id"], "started_at_wallclock": current_tools[0]["started_at_wallclock"]} if current_tools else None`. The `args_summary` key MUST be absent from the wire payload (operator-readable badge does not need redacted args; explicit allowlist prevents accidental exposure on a future redactor regression). Test: `test_oldest_in_flight_omits_args_summary` asserts the key `"args_summary"` is NOT in the dict (assertNotIn, not assertEqual to None).

- **D-4. Per-instance `now_wallclock` field; single `time.time()` per `_build_local_instance` call.** *Rationale:* SC-6 clock-skew safety requires producer-clock pairing. `_build_local_instance` already calls `now = time.time()` at line 172 — D-4 promotes that local variable into the returned instance dict. Per-instance (not per-agent) is sufficient because `_build_state` runs synchronously and one `time.time()` covers every agent on that instance; per-agent stamping wastes bytes and creates the illusion of finer resolution we don't have. *Carried into:* `ui_server.py:_build_local_instance` — assign `now_wallclock = now` on the instance dict. Test: `test_ui_server.py::test_now_wallclock_present_on_instance`, `test_now_wallclock_pairs_with_started_at_wallclock_for_consistent_elapsed`.

- **D-5. Client tick uses `performance.now()` (monotonic), not `Date.now()`. Tick driver is `setInterval(1000)`, not `requestAnimationFrame`.** *Rationale:* PB3 — `Date.now()` is wall-clock and subject to NTP corrections, browser-tab throttling, and laptop suspend/resume; on a 90s tool, an NTP step makes the displayed counter jump backward. `performance.now()` is monotonic and immune. **Tick choice:** `setInterval(1000)` keeps counting in backgrounded tabs (throttled to ~1s minimum, which is fine — that's our display granularity), whereas `requestAnimationFrame` pauses entirely under `visibility=hidden` and would freeze the displayed elapsed for an inactive operator's tab. Server-stamped values stay the source of truth; the client tick smooths between polls only. *Carried into:* `dashboard.html` JS — on each WebSocket message, capture `perf_at_arrival = performance.now()` and `server_now_at_arrival = msg.now_wallclock`; display elapsed via `Math.floor((server_now_at_arrival - oldest_in_flight.started_at_wallclock) + (performance.now() - perf_at_arrival)/1000)`. A single module-level `setInterval(forceRerender, 1000)` triggers re-render at 1Hz. Code comment cites D-5 by name to deter future refactors that swap in `Date.now()` or `requestAnimationFrame`.

- **D-6. Pulse threshold at displayed-elapsed ≥5s; reuse existing `pulse` keyframe.** *Rationale:* SC-3 — fast tools (Read, Edit) complete in <1s; pulsing the badge on every Pre creates motion noise. Threshold prevents that. Reusing the existing 1.6s `pulse` keyframe (`dashboard.html:75-78`) instead of inventing a new shimmer keeps the visual vocabulary minimal and synchronizes the badge animation with the status-dot animation already in `--st-tool` color — column reads as "one organism breathing." *Carried into:* `dashboard.html` className conditional `className={elapsed >= 5 ? "tool-chip stuck" : "tool-chip"}` + CSS rule `.tool-chip.stuck { animation: pulse 1.6s ease-in-out infinite; }`.

- **D-7. Settle frame on completion via `last_tool_completed` exposure. React-cleanup-safe.** *Rationale:* SC-4 — when `oldest_in_flight` transitions non-null → null, the badge MUST render the final duration for ≥500ms before clearing. Easiest path: payload exposes `last_tool_completed` (already on the snap dict per `teammate.py:250`, just not surfaced on the UI agent payload today); client tracks `oldest_in_flight !== null → null` transitions and renders `last_tool_completed.tool_name · last_tool_completed.duration_seconds.toFixed(1)s ✓` for 500ms. SC-4 sad-path scope (kill/death) is moot — tombstoned agents are removed from `agents[]` per `_build_local_instance:214`, so the column itself is gone; no settle frame attempted. **React cleanup invariant:** the 500ms timer MUST be cleared in a `useEffect` cleanup (return a `() => clearTimeout(timer)` from the effect). If the agent column unmounts (tombstone, or instance removed) during the 500ms window, an uncleared timer fires on an unmounted component — leak in production, swallowed-error in dev. *Carried into:* `ui_server.py:_build_local_instance` adds `last_tool_completed = snap.get("last_tool_completed")` to the agent dict (currently absent — verified at line 208 area). Client `useEffect` hook tracks transitions and registers a cleanup. Test: `test_last_tool_completed_present_on_agent_payload`. Cleanup invariant is a code-review check (no automated test under JSON-shape-only discipline).

- **D-8. API surface freeze: `tools[]` and `current_tool` scalar are legacy after #22.** *Rationale:* S-1 — keeping all four representations (`current_tools[]`, `tools[]`, `current_tool`, `oldest_in_flight`) is API foot-gun country. SC-9 forbids breaking changes, so we cannot remove the legacy fields in #22. We can document the freeze: post-#22, `tools[]` is the full-set name list (no new consumers), `current_tool` is last-started-name (legacy semantic for SC-9), `current_tools[]` is the structured list (canonical), and `oldest_in_flight` is the badge field. *Carried into:* one-line code comment in `ui_server.py` near the agent dict construction; one-line journal entry in `PRODUCT-VISION.md` at #22 close.

- **D-9. Tombstone-race invariant for `oldest_in_flight`.** *Rationale:* PB2 — `_build_local_instance` reads `snap["current_tools"]` and `info.alive` from the SAME `BrokerSnapshot` (D-11/SC-10 from #18), so tombstone race against `_close_open_tools` is structurally impossible. The invariant: `oldest_in_flight` is non-None ⇒ `info.alive` was True at snapshot time ⇒ `current_tools` was non-empty at snapshot time. The broker enforces `_close_open_tools` runs before `info.alive=False` becomes observable (`broker.py:288` runs at step 8b of `_tombstone_teammate`, before tombstone-after-pop visibility resolves). *Carried into:* `test_ui_server.py::test_oldest_in_flight_none_for_tombstoned_teammate` + a comment in `_build_local_instance` near the `oldest_in_flight` derivation.

- **D-10. Reserved row height regardless of `oldest_in_flight`.** *Rationale:* SC-8 — strobing layout on rapid Pre/Post pairs is unacceptable. Reserve the slot at fixed height even when empty. *Carried into:* `dashboard.html` always renders the row with `style={{height: 22, flexShrink: 0}}`; content is the chip OR an empty `<span>` placeholder of equal height.

- **D-11. Sub-second elapsed renders as `0s` (not blank).** *Rationale:* SC-2 mandates integer-second display advancing once per second. At t<1s elapsed, `Math.floor` yields `0`. The chip renders `Bash · 0s`; advances to `1s`, `2s`, etc. Avoids a blank-counter "phantom" frame between Pre and the first 1Hz tick. *Carried into:* dashboard JS `Math.floor` + a comment.

### Edge Cases

- **Empty `current_tools`** → `oldest_in_flight = None`, `in_flight_count = 0`; chip row renders transparent placeholder; accent bar transparent.
- **N=1 in-flight** → chip shows `Bash · Ns`, no count suffix.
- **N≥2 in-flight** → chip shows `Bash · Ns +(N-1)` where Bash is `current_tools[0].tool_name`.
- **Tools start at exact same `started_at_wallclock`** (sub-microsecond): Python list sort is stable; whichever was appended first to `current_tools` wins index 0. Acceptable; spec does not require any tie-breaking semantics.
- **Long tool name** (Pre-internal hook tool with 40+ char name): CSS `text-overflow: ellipsis` on the tool-name span; elapsed counter and count suffix remain visible (right-aligned within chip).
- **Tool name with non-ASCII / special chars:** rendered as-is; no escaping needed beyond standard React string handling.
- **Snapshot mid-transition (Pre fired, Post pending):** `oldest_in_flight` reflects current state; client tick continues from `now_wallclock` anchor.
- **WebSocket disconnects mid-tool:** UI freezes counter at last-known display value (no extrapolation past last server snapshot once disconnect is detected); on reconnect, next snapshot re-anchors `perf_at_arrival` and counter snaps to truth.
- **Browser tab throttled (visibility=hidden):** `performance.now()` continues advancing; on visibility return, next animation frame resyncs. No negative jumps because the clock is monotonic.
- **NTP step / laptop suspend/resume:** D-5 ensures wall-clock discontinuities don't propagate. The next snapshot from server resets the anchor cleanly.
- **Settle frame interrupted by new tool:** if a tool starts within 500ms of the previous one completing, `oldest_in_flight` becomes non-None again and the settle frame state is dropped — the new tool's badge replaces it. Acceptable; consistent with "current state wins."
- **Sub-500ms tools (Read/Edit):** Pre→Post happens between two server snapshots → may never appear in a snapshot. Settle frame fires from `last_tool_completed` only when an `oldest_in_flight → None` transition is observed in the snapshot stream. If neither end of the transition is observed, no settle frame; the tool flicks past invisibly. Acceptable per SC-3 reasoning.
- **Tombstoned teammate:** removed from `agents[]` per existing `_build_local_instance:214` filter. Row never rendered. Settle frame not attempted (SC-4 sad-path scope).
- **Multi-instance dashboard with skewed clocks:** `now_wallclock` is per-instance; pairing always within instance. Cross-instance comparison (e.g., "which instance has the oldest tool overall") is NOT supported by #22 and not required by any SC.
- **Pre-#22 dashboard against #22 server:** `oldest_in_flight` and `now_wallclock` ignored by old client; existing `current_tool` and `tools[]` continue to render as before. SC-9 satisfied.
- **#22 dashboard against pre-#22 server:** `oldest_in_flight` and `now_wallclock` are absent. Client must defensively check (`if (instance.now_wallclock && agent.oldest_in_flight)`). On absence, accent bar is transparent and chip row is empty. Graceful degradation; matches the "no in-flight" state visually.
- **Unreachable remote instance:** `_unreachable_instance(crew_id)` (`ui_server.py:105-117`) emits an instance dict WITHOUT `now_wallclock` and with `agents=[]`. The defensive check above handles it (no `now_wallclock` ⇒ no chip rendering anywhere on that instance). The unreachable accent treatment (red border, opacity 0.5 — existing #14 behavior) is unchanged; #22 does NOT add an accent bar to unreachable instances.
- **Disk-full / transcript-disabled mode:** UI surfaces are not affected by transcript persistence; #22 reads from in-memory broker snapshot. No interaction.

### Specification

The implementation surface is:

1. **`claude_crew/ui_server.py:_build_local_instance`** — add three additive payload fields:
   - Instance dict: `now_wallclock = now` (line 172's existing `time.time()`).
   - Agent dict: `oldest_in_flight = current_tools[0] without args_summary if current_tools else None`.
   - Agent dict: `in_flight_count = len(current_tools)`.
   - Agent dict: `last_tool_completed = snap.get("last_tool_completed")` (verbatim from snap; already shaped correctly by teammate.py).

2. **`claude_crew/ui/dashboard.html`** — add three rendering elements:
   - `<style>`: `.agent-column-accent { height: 3px; ... }`, `.agent-column-accent.active { background: var(--st-tool); opacity: 0.5; }`, `.agent-column-accent.stuck { background: var(--st-tool); opacity: 1.0; animation: pulse 1.6s ease-in-out infinite; }`, `.tool-chip { ... }`, `.tool-chip.stuck { animation: pulse 1.6s ease-in-out infinite; }`.
   - `AgentStreamColumn`: top accent bar before line 467 area; new dedicated row between line 508 and line 510 with always-reserved height.
   - JS: per-message anchor (`perf_at_arrival`, `server_now_at_arrival`); 1Hz `setInterval` to force re-render of elapsed values; settle-frame state hook tracking `oldest_in_flight` non-null → null transitions.

3. **Tests** added to `tests/test_ui_server.py`:
   - `test_now_wallclock_present_on_instance`
   - `test_oldest_in_flight_is_index_zero_of_current_tools`
   - `test_oldest_in_flight_is_none_when_idle`
   - `test_oldest_in_flight_omits_args_summary`
   - `test_oldest_in_flight_for_parallel_tools_picks_oldest`
   - `test_in_flight_count_matches_current_tools_length`
   - `test_last_tool_completed_present_on_agent_payload`
   - `test_oldest_in_flight_none_for_tombstoned_teammate` (D-9) — uses the `make_server()` + `asyncio.run()` pattern (per `test_broker.py`), spawning a stub teammate, planting in-flight `_tool_uses` directly, then calling `kill_teammate` and asserting the agent is absent from `_build_local_instance` output. Test runs against a real broker because tombstone is a broker-level state machine; bare `_build_local_instance` with synthetic snapshot data won't exercise the alive→tombstoned transition.
   - `test_now_wallclock_pairs_with_started_at_wallclock_for_consistent_elapsed` (D-4) — asserts `0 <= (instance.now_wallclock - agent.oldest_in_flight.started_at_wallclock) < 0.1` when the test plants a synthetic in-flight tool with `started_at_wallclock = time.time()` immediately before calling `_build_local_instance`. The 100ms tolerance captures wall-clock drift between the test's `time.time()` and the function's `time.time()`. Verifies both fields come from `time.time()` on the same producer (not a mismatched clock source like `time.monotonic()`).

4. **E2E test** in new `tests/test_e2e_badge_pipeline.py` exercising broker → snapshot → /api/state with synthetic in-flight state, asserting the wire payload includes all #22 fields.

### Assumptions

- **A-1. Operator visual-verification bar.** SC-1, SC-2 client-tick, SC-3 pulse threshold, SC-4 settle frame, SC-7 layout-fit, SC-8 layout-stability — all visual claims verified by manual inspection during Phase 4, not by automated test (per S-2/S-3 sentinel notes). Documented decision; future-readers should not assume there's a Playwright suite.
- **A-2. `last_tool_completed` shape stability.** The settle-frame logic depends on `last_tool_completed.duration_seconds` being a float and `tool_name` being a string. These are guaranteed by `claude_crew/teammate.py` Post hook population; no new contract.
- **A-3. CSS Grid `minmax(220px, 1fr)` is sticky.** No plan to make the dashboard responsive at <220px column width. SC-7's "fits or ellipsizes at 220px" is the lower bound.

### Open Questions

*All resolved.* OQ-1 → D-1 (top accent bar). OQ-2 → D-2 (new row). OQ-3 was resolved at Phase 1 (per-instance `now_wallclock`).

**Gate**: Phase 2 sentinel pseudocode-reader review next. After review, refine if needed, then proceed to Phase 3.

---

## Phase 3: Task Breakdown

Five tasks, sequential dependency chain. Each ends in a green test suite on the feature branch with a per-task commit.

### Task T1: Server payload — `now_wallclock` + `oldest_in_flight` + `in_flight_count` + `last_tool_completed`
**Depends on**: None | **Blocks**: T2, T5

Add four additive payload fields to `_build_local_instance` in `claude_crew/ui_server.py`. Implementation surface from Phase 2 §Specification item 1. No client-side changes in this task.

**Acceptance Criteria** (BDD):
```
Scenario: now_wallclock stamped on every instance dict
  Given _build_local_instance is called with any BrokerSnapshot
  When the function returns
  Then the instance dict contains key "now_wallclock"
  And its value is a float (seconds since epoch)
  And it equals the function's local `now = time.time()` value (single source — D-4)

Scenario: oldest_in_flight reflects current_tools[0] without args_summary
  Given a teammate snapshot with current_tools=[{tool_name:"Bash", tool_use_id:"x", started_at_wallclock:100.0, args_summary:"command=ls"}]
  When _build_local_instance is called
  Then the agent dict's "oldest_in_flight" equals {"tool_name":"Bash", "tool_use_id":"x", "started_at_wallclock":100.0}
  And the key "args_summary" is NOT in oldest_in_flight (assertNotIn)

Scenario: oldest_in_flight is None when no tools in flight
  Given a teammate snapshot with current_tools=[]
  When _build_local_instance is called
  Then the agent dict's "oldest_in_flight" is None
  And "in_flight_count" is 0

Scenario: oldest_in_flight picks oldest under parallel dispatch
  Given current_tools=[{tool_name:"Bash", started_at_wallclock:100.0, ...}, {tool_name:"Read", started_at_wallclock:105.0, ...}]
  When _build_local_instance is called
  Then oldest_in_flight.tool_name == "Bash"
  And in_flight_count == 2

Scenario: last_tool_completed surfaced verbatim from snap
  Given a teammate snapshot with snap["last_tool_completed"] = {tool_name:"WebFetch", outcome:"ok", duration_seconds:1.2, finished_at_wallclock:99.0, error_summary:None}
  When _build_local_instance is called
  Then agent["last_tool_completed"] equals that dict

Scenario: now_wallclock pairs with started_at_wallclock from same clock
  Given a planted tool with started_at_wallclock = time.time() immediately before the call
  When _build_local_instance is called
  Then 0 <= (instance.now_wallclock - agent.oldest_in_flight.started_at_wallclock) < 0.1

Scenario: pre-existing fields preserved (SC-9)
  When _build_local_instance is called against any snapshot
  Then "current_tool" still equals snap.get("current_tool")  (last-started semantic)
  And "tools" still equals [t.tool_name for t in current_tools]
```

**Verification**: `uv run pytest tests/test_ui_server.py -k "now_wallclock or oldest_in_flight or in_flight_count or last_tool_completed or pairs_with"` — 7 new tests pass on T1; fail before T1 lands (no `now_wallclock` field in payload).

---

### Task T2: Server tombstone-race test (D-9)
**Depends on**: T1 | **Blocks**: T3

Single-test task — uses the broker `kill_teammate` path against a real broker (not synthetic snapshot data), per Phase 2 test note for D-9. Confirms structural invariant: `oldest_in_flight` is never set for an agent that is not in `agents[]`.

**Acceptance Criteria**:
```
Scenario: Tombstoned teammate produces no agent entry, regardless of in-flight tools at kill time
  Given a broker with a stub teammate that has _tool_uses populated (planted via direct attribute write)
  And the teammate is alive
  When kill_teammate is called via the broker
  And _build_local_instance is called against the post-kill snapshot
  Then the agent is absent from instance["agents"]
  And no "ghost" oldest_in_flight surfaces anywhere in the response
```

**Verification**: `uv run pytest tests/test_ui_server.py::test_oldest_in_flight_none_for_tombstoned_teammate` passes.

---

### Task T3: Dashboard rendering — accent bar + chip row + settle-frame state
**Depends on**: T2 | **Blocks**: T4

Edit `claude_crew/ui/dashboard.html` per Phase 2 §Specification item 2. Three rendering elements:
1. Top-of-column accent bar (D-1) — 3px high, `--st-tool` tinted, opacity-staged across `<5s` (`active`) and `≥5s` (`stuck` + `pulse` animation).
2. Dedicated tool-chip row (D-2, D-10) — always reserved 22px height between status row (line 508) and message scroll (line 510). Chip content: `tool_name · {elapsed}s [+{N}]` when `oldest_in_flight` set; transparent placeholder otherwise.
3. Settle-frame state hook (D-7) — `useEffect` tracking `oldest_in_flight` non-null → null transitions, renders `last_tool_completed.tool_name · {duration}s ✓` for 500ms, with cleanup `() => clearTimeout(t)` to prevent leaks on unmount.

Client tick: single module-level `setInterval(forceRerender, 1000)` (D-5). Anchors `perf_at_arrival` and `server_now_at_arrival` on each WebSocket message. Display elapsed via D-5 formula. Defensive check `if (instance.now_wallclock && agent.oldest_in_flight)` for backward compat (Phase 2 edge case).

**Acceptance Criteria** (visual-verification per A-1, plus one JSON-shape sanity test):
```
Scenario: Dashboard HTML loads cleanly
  Given the UIServer is running with a feature-branch dashboard
  When GET / is requested
  Then response is 200
  And the body contains "agent-column-accent" (new CSS class)
  And the body contains "tool-chip" (new CSS class)
  And the body contains "performance.now()" (D-5 enforcement)
  And the body does NOT contain "Date.now()" inside the elapsed-display code path

Scenario [VISUAL]: Operator scans 4 columns, identifies the active-tool one
  Given 4 stub teammates spawned, one with a planted in-flight tool of duration ≥6s
  When the dashboard is open in a browser
  Then the operator visually identifies the active column from its accent bar
  And the chip row shows "Bash · 6s" (or higher) advancing
  And the chip pulses (≥5s threshold met)

Scenario [VISUAL]: Settle frame on completion
  Given a teammate executing a 3s tool
  When the tool completes (PostToolUse fires; current_tools empties)
  Then for ~500ms the chip shows "Bash · 3.0s ✓"
  And then clears to the empty placeholder
```

**Verification**: `uv run pytest tests/test_ui_server.py::test_dashboard_html_contains_badge_classes` (the assert-string-presence test) + manual visual verification per A-1.

---

### Task T4: Comment + freeze documentation (D-8)
**Depends on**: T3 | **Blocks**: T5

One-line code comment near the agent-dict construction in `_build_local_instance` documenting the API surface freeze: `tools[]` is the full-set name list (frozen as-of-#22, no new consumers); `current_tool` is last-started-name (legacy, retained for SC-9); `current_tools[]` is the structured list (canonical); `oldest_in_flight` is the badge field. PRODUCT-VISION journal entry deferred to Phase 5.

**Acceptance Criteria**:
```
Scenario: Comment present at agent dict
  When grep "frozen as-of-#22" claude_crew/ui_server.py runs
  Then output is non-empty (matches the inline comment)
```

**Verification**: `grep -q "frozen as-of-#22" claude_crew/ui_server.py`

---

### Task T5: End-to-end integration tests
**Depends on**: T1, T2, T3, T4 | **Blocks**: Phase 5

New file `tests/test_e2e_badge_pipeline.py`. Cohesive tests that exercise the full feature pipeline through the public `/api/state` HTTP endpoint — not component-level. These are the proof the assembled feature works.

**Happy Path Scenarios**:
```
Scenario: Single in-flight tool — full pipeline visibility
  Given a UIServer running against a real broker with one stub teammate
  When the teammate's _tool_uses is populated with a "Bash" entry started 7s ago
  And GET /api/state is requested
  Then response contains exactly one instance with now_wallclock as a float
  And that instance has one agent with oldest_in_flight.tool_name == "Bash"
  And oldest_in_flight.started_at_wallclock matches the planted value
  And in_flight_count == 1
  And (now_wallclock - started_at_wallclock) is between 6.5 and 8.5

Scenario: Parallel tools — oldest wins
  Given a teammate with three planted in-flight tools at start times (t-30, t-10, t-2)
  When /api/state is fetched
  Then oldest_in_flight.tool_name corresponds to the t-30 tool
  And in_flight_count == 3
  And tools[] contains all three names (legacy field preserved per D-8 freeze note)
```

**Sad Path Scenarios**:
```
Scenario: Idle teammate — no in-flight surfaces
  Given a teammate with empty current_tools and last_tool_completed=None
  When /api/state is fetched
  Then agent.oldest_in_flight is None
  And agent.in_flight_count == 0
  And agent.last_tool_completed is None
  And no spurious now_wallclock fields appear on the agent dict (per-instance only)

Scenario: Killed teammate mid-tool — no ghost badge
  Given a teammate with planted in-flight tool
  When kill_teammate is called via the broker
  And /api/state is fetched after the tombstone resolves
  Then the agent is absent from instance.agents
  And no oldest_in_flight surfaces anywhere in the response

Scenario: Unreachable remote instance — no now_wallclock leakage
  Given a multi-instance setup where one remote is unreachable
  When /api/state is fetched
  Then the unreachable instance dict has agents=[] and no now_wallclock key
  And the local instance has now_wallclock present
  And the client-side defensive check works (no badge rendering on unreachable)

Scenario: last_tool_completed exposure — settle-frame data path
  Given a teammate with last_tool_completed set on its status_snapshot
  When /api/state is fetched
  Then agent.last_tool_completed mirrors snap.last_tool_completed exactly
  And in particular tool_name, outcome, duration_seconds, finished_at_wallclock, error_summary all present
```

**Verification**: `uv run pytest tests/test_e2e_badge_pipeline.py` — all scenarios pass on T5; fail before T1-T3 land (missing payload fields).

---

**Phase 3 gate**:
- ✅ 5 tasks, each independently testable
- ✅ Dedicated E2E task (T5) with happy + sad path coverage including parallel, idle, kill, unreachable, settle-frame data
- ✅ Each Phase 2 SC traces to at least one BDD scenario:
  - SC-1, SC-2, SC-3, SC-4 → T3 visual scenarios + T5 happy/settle-frame data
  - SC-5 → T1 parallel scenario, T5 parallel-tools scenario
  - SC-6 → T1 pairs_with scenario, T5 happy-path elapsed-range assertion
  - SC-7, SC-8 → T3 visual verification (per A-1)
  - SC-9 → T1 pre-existing-fields scenario, T5 idle scenario (no spurious fields)
  - SC-10 → no #19 stream tests added; existing tests in `test_e2e_tool_events.py` continue to pass (regression-by-omission)
  - SC-11 → T1, T2, T5 all JSON-boundary tests
- ✅ Verification commands fail without the feature (T1 tests reference fields that don't exist pre-T1)
- ✅ Co-architect-arbitrated approach; user not gating

---

## Phase 4: Implementation

*To be filled.*

---

## Phase 5: Completion

*To be filled.*
