# Feature #19: Tool-Use Events in Dashboard Stream

**Status**: Planning (Phase 1)
**Created**: 2026-04-30
**Capability**: #4 (live observability across all crews)
**Size**: M

---

## Phase 1: Research & Requirements

### Problem Statement

Today, when a teammate runs a tool (Bash, Read, Grep, Edit, WebFetch, etc.), the call IS captured — but only in two places:

1. **Per-teammate status snapshot** — `current_tools[]` (in-flight only) and `last_tool_completed` (only the single most-recent completion). No history.
2. **JSONL transcript on disk** — paired `tool_start` / `tool_end` records written by `broker._sink.write_tool_event(...)`, complete with redacted args, outcome, duration, error_summary. Permanent and rich.

The Mission Control dashboard sees neither. `UIServer._build_local_instance(snapshot)` reads only `snapshot.log` (envelopes) and synthesizes a per-message stream of `{t, from, to, kind: "msg", body}` records. `BrokerSnapshot.tool_events: tuple[Any, ...] = ()` was reserved by #18 (D-10) specifically for #19 to populate, but is empty today.

The frontend (`dashboard.html` `MiniMessage` component, lines 406-446) **already has full render branches for `kind: "tool"`** — purple monospace pill with ▸ icon — and `kind: "thinking"` (italic). The data pipeline is the only missing piece.

The result: an operator watching the dashboard sees a teammate go silent for 90 seconds, then a reply lands. They have no idea what the teammate was doing during those 90s without `tail -f`-ing the JSONL transcript. This is the "what is this teammate actually doing right now" gap that #6 (idle telemetry), #8 (tool execution telemetry), and #13 (multi-instance dashboard) collectively get most of the way to closing — and #19 finishes.

**Why now:** #18 cleared the structural prerequisite. Without #19, every teammate task is an opaque box on the dashboard between message envelopes. With it, the dashboard becomes a true live transcript of teammate behavior.

### Capability Mapping

Capability #4 ("live observability across all crews"): the dashboard is the materialization of this capability. Today it's partial — message-level only. #19 makes it complete at the tool level.

### Success Criteria

- [ ] **SC-1.** A completed tool call (PreToolUse → PostToolUse pair) on any live teammate appears in the dashboard's transcript stream as a `kind: "tool"` entry within one polling cycle (≤2s under normal load).

- [ ] **SC-2.** Tool entries are interleaved with envelope messages in the same per-teammate stream column, sorted by timestamp. The operator sees a single time-ordered narrative per teammate (envelope → tool → tool → envelope), not parallel columns.

- [ ] **SC-3.** Each tool entry surfaces the same redaction-safe fields the JSONL records carry: `tool_name`, `outcome` (one of `ok`/`failed`/`interrupted`/`abandoned`/`killed`), `duration_seconds`, `args_summary` (only for the v1 allowlist: Bash/Task/WebFetch), and `error_summary` (only on failure outcomes). `redaction_version` carried. **Records with `outcome='orphan_post'` (a #8 D11 schema-honesty diagnostic when PostToolUse fires without a prior PreToolUse) are excluded from the per-teammate event list — they remain in JSONL for forensics but carry no operator-visible signal in the dashboard.**

- [ ] **SC-4.** Tool events from a tombstoned teammate remain visible in the dashboard for the broker's lifetime — same retention semantics as envelopes today. (A teammate that died after running a failing build should still show the failed tool entry.) **Tool calls that were *in-flight when tombstone runs* also appear in the stream with `outcome='abandoned'` (death) or `outcome='killed'` (kill), matching their JSONL transcript records. The `_close_open_tools` path appends to the per-teammate in-memory list at the same point it emits the JSONL records — no JSONL-only tool-end events.**

- [ ] **SC-5.** Per-teammate retention is bounded. At most N most-recent completed tool events per teammate are retained in memory (default N = 200; matches the snapshot's `log_limit=200` envelope cap). No unbounded growth.

- [ ] **SC-6.** Tool events from remote claude-crew instances appear in the unified dashboard the same way envelopes do. The `/api/state` HTTP endpoint response is extended to include each instance's tool events in a form the requesting UIServer can merge into the per-agent transcript stream. **On remote-fetch failure or timeout (existing #13 path), the unified dashboard shows the LAST successfully fetched tool events for that instance — same staleness semantics as envelope transcripts today.** Phase 2 must explicitly extend the `/api/state` response schema; the extension is additive (existing consumers keep working).

- [ ] **SC-7.** In-flight tool calls (PreToolUse fired, PostToolUse not yet seen) are NOT rendered in the stream. They are visible separately via `current_tools` in the agent header (existing #8 surface). The stream shows only completed calls so an operator scrolling history sees facts, not transient state. *(Co-architect raised that this leaves the original problem — silence during a 90s Bash — only partly addressed; the agent-header in-flight surface must remain visually prominent. Phase 2 includes a UX-validation note.)*

- [ ] **SC-8.** Tool events appear in the in-memory per-teammate list **regardless of JSONL transcript success.** The in-memory append and the JSONL `write_tool_event` call happen in the same hook callback execution path, but the in-memory append is NOT gated on JSONL success. JSONL writes are best-effort (disk failures are caught and logged); the in-memory list is the authoritative in-session state for the dashboard. A disk-full or transcript-disabled condition must not blind the dashboard.

- [ ] **SC-9.** No new MCP tools, no new envelope kinds, no protocol changes. Pure read-side enrichment of the existing `BrokerSnapshot` API plus an additive extension to `/api/state`.

- [ ] **SC-10.** Snapshot construction time stays under 10ms with a typical workload (5 live teammates × 200 retained events each = 1000 events flattened + sorted). The benchmark measures `Broker.snapshot()` call time in isolation (NOT including `_build_local_instance` merge or HTTP serialization). Measured by a microbenchmark in tests. *(Co-architect flagged that SC-1's 2s end-to-end budget gives only ~500ms slack after polling and fanout; Phase 2 will break the budget down by stage.)*

- [ ] **SC-11.** Each completed `ToolEvent` maps to a stream record with shape `{t: <event timestamp>, from: <teammate_id>, to: null, kind: "tool", body: <assembled string>}`, consistent with the envelope record shape the dashboard already consumes. The `to` field is `null` for tool entries (they have no recipient). The `from` field carries `teammate_id` (matching envelope convention), not role name. The `body` format is specified in Phase 2 but must include at minimum `tool_name` and `outcome` so the operator can read the entry without expanding it.

### Questions

- [x] **Q1: Where does the in-memory event list live — on Teammate or on Broker?**
  - **Answer:** On Teammate. Mirrors the existing #8 pattern (`_tool_uses` dict, `_last_tool_completed`, `_recently_closed_tool_use_ids` deque all live on the teammate). `Broker.snapshot()` flattens per-teammate event lists into the single flat `tool_events` tuple. Locality of write (the hook fires inside the SdkTeammate) and locality of cleanup (teammate death cleans up its own list) both favor teammate-side state.

- [x] **Q2: Should Task (subagent) tool calls be filtered out of the tool stream to avoid duplication with #7's subagent_spawn / subagent_result envelopes?**
  - **Answer (revised after co-architect pushback):** Filter Task at **render time in UIServer**, not at the teammate-side append site. Keeping Task in the in-memory list preserves data for future views (tool timeline, subagent debugger, replay) and decouples #19 from #7's boundary drift. The dashboard merge-step in `_build_local_instance` is the right filter point — cheap O(1) per event, fully reversible if a future view wants the raw stream. Operational Task failures stay covered by #7's existing `subagent_result(tnm_missing=True)` envelope.

- [x] **Q3: What's the bounded retention size?**
  - **Answer:** 200 per teammate (matches `log_limit=200` for envelopes — symmetric retention is the easy mental model). Configurable via `CLAUDE_CREW_TOOL_EVENTS_PER_TEAMMATE` env var with that default.

- [x] **Q4: Are in-flight tool events surfaced in the stream too?**
  - **Answer:** No (see SC-7). In-flight is transient state already exposed via `current_tools` in the agent header. The stream shows completed calls only.

- [x] **Q5: Clock skew between teammates / instances?**
  - **Answer:** Non-issue locally (single host, monotonic-derived wallclocks). For #13 remote fanout: each remote contributes its own events tagged with its own wallclock. We sort the merged stream by timestamp; minor skew (sub-second) is visually identical to local concurrency.

### Constraints & Dependencies

- **Requires (already-shipped):**
  - #8 — tool_start/tool_end JSONL records and the PreToolUse/PostToolUse hook framework.
  - #18 — BrokerSnapshot dataclass and the reserved `tool_events` field with documented flat-tuple contract.
  - #13 — UIServer remote-fanout architecture (`_fetch_remote_state`, `httpx.AsyncClient`).

- **Cohabits with (not blocked by, but worth noting):**
  - #16 (message kind typing) — about re-typing **existing envelope** records that contain tool calls or thinking blocks, distinct from #19's introduction of NEW tool-event records into the stream. Schedule independently.

- **Breaking changes:** None. `BrokerSnapshot.tool_events` was reserved as `tuple[Any, ...] = ()` in #18 specifically so #19 could land without a v2 snapshot. Tightening the type to `tuple[ToolEvent, ...]` is forward-compatible.

- **Performance implications:** Per-teammate bounded deque (200 entries × ~250 bytes each ≈ 50KB worst case per teammate). 5 teammates ≈ 250KB. Snapshot flattening + timestamp sort: 1000-element merge sort, sub-millisecond.

- **Security / redaction:** Reuse #8's redactor (`redaction.py` v1) — args_summary already redacted at write time. No new redaction surface.

### Cross-Feature Integration Check

- **#7 (subagent envelopes):** see Q2. Filter Task tool out of #19's stream.
- **#8 (tool execution telemetry):** the producer of every event #19 surfaces. Same redaction, same write path, same teammate cleanup semantics.
- **#13 (multi-instance fanout):** each instance surfaces its own tool events; remote events reach the local dashboard via the existing `/api/state` payload, which #19 extends to include them under the per-instance `transcripts[crew_id]` array.
- **#16 (message kind typing):** orthogonal. #16 retypes envelope records; #19 introduces tool event records. Both target the same frontend render branches.
- **#18 (broker snapshot):** consumes the reserved `tool_events` field exactly as the D-10 contract specified.

---

## Pre-Phase-2 Reviews

Two parallel reviews ran before Phase 2 — sentinel for SC completeness/testability, co-architect (Opus) for architectural pushback.

### Sentinel findings — applied to Phase 1

- **F1** → SC-11 added (stream record shape contract — `{t, from, to, kind, body}` mapping).
- **F2** → SC-3 extended (`orphan_post` filtered).
- **F3** → SC-8 reworded (in-memory append NOT gated on JSONL success).
- **F4** → SC-4 extended (in-flight-at-tombstone covered, `_close_open_tools` appends to in-memory list).
- **F5** → SC-6 strengthened (`/api/state` schema extension explicit, stale-on-failure semantics).

Sentinel deferred to Phase 2: deque rollover marker (D1), SC-10 benchmark scope ambiguity (D2), auto-scroll under high event volume (D3), batching identical successive tool events (D4 — known UX limitation, post-#19).

### Co-architect findings — applied to Phase 1

- **Q2 flipped.** Filter Task at render time in UIServer, not at teammate append site. Preserves data; reversible.
- **Pushback A (data shape vs retention math), B (SC-1 freshness budget), C (SC-7 doesn't fully solve operator-silence)** carried into Phase 2 as design pillars. Annotations added to SC-7 and SC-10.

### Recorded decisions (R-series)

- **R1.** Task tool operational failures are covered by #7's `subagent_result(tnm_missing=True)` envelope. The only unobservable gap is a broker-killing process crash before `_close_open_subagents` runs — pre-existing limitation of #7, out of scope for #19.
- **R2.** #19 closes the tool-observability gap in capability #4 but does not fully complete it. #16 (envelope kind enrichment for thinking blocks) remains open. Post-#19 status: "substantially met — message and tool observability complete; envelope kind enrichment (#16) deferred."
- **R3.** `orphan_post` is a diagnostic artifact for JSONL forensics, not a meaningful operator action. Filtering keeps the dashboard clean. If `orphan_post` becomes a signal worth surfacing (e.g., to diagnose hook ordering bugs in future SDK versions), add a dedicated filter opt-in at that time.
- **R4.** In-flight state is transient. The stream shows completed facts. An operator reading the stream reconstructs "what did this teammate do" from the archive — not from snapshots of intermediate state. `current_tools` in the agent header provides the live in-flight view. These two surfaces are complementary, not redundant.

### Co-architect's "protect this" — Phase 2 guardrail

`BrokerSnapshot.tool_events: tuple[Any, ...] = ()` is the contract reserved by #18 D-10. Phase 2 will be tempted to negotiate this away when dispatch ergonomics get awkward (per pushback A). Don't. The reservation is the contract; if it's wrong, add a sibling field — do not reshape.

---

## Phase 2: Design & Specification

### Architecture Overview

Three new pieces, one tightening, one merge. No new envelopes, no new MCP tools, no protocol change.

1. **`ToolEvent` frozen dataclass** in `claude_crew/teammate.py` — mirrors #8's JSONL `tool_end` record fields exactly so the in-memory shape and on-disk shape stay synchronized.
2. **Per-teammate bounded deque** `_completed_tool_events: collections.deque[ToolEvent]` on the `Teammate` base class. `maxlen` = `CLAUDE_CREW_TOOL_EVENTS_PER_TEAMMATE` (default 200).
3. **`TeammateInfo.tool_events_at_death: tuple[ToolEvent, ...] | None`** — populated in `_tombstone_teammate` from the deque. Mirrors #14's `*_at_death` pattern so snapshot can read from the right source whether the teammate is alive or tombstoned.
4. **`BrokerSnapshot.tool_events` type tightened** from `tuple[Any, ...] = ()` to `tuple[ToolEvent, ...] = ()`. Forward-compatible (no consumer today). Populated by `Broker.snapshot()` flattening per-teammate deques + at-death tuples in a single pass, sorted by `finished_at_wallclock`.
5. **UIServer merge** — `_build_local_instance` converts each `ToolEvent` (filtering `tool_name == "Task"` at this step) to `{t, from, to: None, kind: "tool", body: <formatted>}` and merges into the per-crew transcript stream by timestamp (stable sort). Frontend already renders `kind: "tool"` (existing Phase 1 finding). `/api/state` schema stays unchanged at the top level — tool events ride inside `transcripts[crew_id]`.

Data flows: hook (PreTool/PostTool/_close_open_tools) → deque append → snapshot flatten → UIServer merge into transcript → frontend renders.

### Data / API Contracts

```python
# claude_crew/teammate.py
@dataclass(frozen=True)
class ToolEvent:
    teammate_id: str
    tool_name: str
    tool_use_id: str
    started_at_wallclock: float
    finished_at_wallclock: float
    duration_seconds: float
    outcome: str  # "ok" | "failed" | "interrupted" | "abandoned" | "killed"
    args_summary: str | None  # only for v1 allowlist (Bash/Task/WebFetch); else None
    error_summary: str | None  # only on non-"ok" outcomes; else None
    redaction_version: str    # currently "v1"

# Teammate base class
self._completed_tool_events: collections.deque[ToolEvent] = collections.deque(
    maxlen=int(os.environ.get("CLAUDE_CREW_TOOL_EVENTS_PER_TEAMMATE", "200"))
)

# claude_crew/broker.py — TeammateInfo extension
@dataclass(frozen=True)
class TeammateInfo:
    # ... existing fields ...
    tool_events_at_death: tuple[ToolEvent, ...] | None = None  # populated at tombstone

# claude_crew/broker.py — BrokerSnapshot tightening
@dataclass(frozen=True)
class BrokerSnapshot:
    crew_id: str
    teammates: tuple[TeammateInfo, ...]
    live: tuple[LiveTeammateInfo, ...]
    log: tuple[Envelope, ...]
    tool_events: tuple[ToolEvent, ...] = ()  # was tuple[Any, ...]; #19 populates

# UIServer per-message shape (no change — additional entries with kind="tool")
{
    "t": "2026-05-01T20:14:32.418Z",   # ISO from finished_at_wallclock
    "from": "t-abc123",                # teammate_id (same convention as envelope from)
    "to": None,                        # tool entries have no recipient
    "kind": "tool",                    # frontend already has render branch
    "body": "Bash (ok, 0.45s) — command=ls /tmp",  # see D-9 format
}
```

### Design Decisions

Each decision names where it is enforced. A decision with no carried-into pointer drifts; tests/types make the decision load-bearing.

- **D-1. `ToolEvent` shape mirrors #8's JSONL tool_end record exactly.** *Rationale:* one shape for both in-memory and on-disk eliminates serialization translation and keeps the dashboard and forensic JSONL telling the same story. *Carried into:* `ToolEvent` dataclass field list + `tests/test_teammate.py::test_tool_event_field_parity_with_jsonl`.

- **D-2. Per-teammate storage is `collections.deque(maxlen=N)`.** *Rationale:* O(1) append, O(1) auto-eviction at the head when full. Bounded memory by construction. Reuses the same pattern as `_recently_closed_tool_use_ids` (#8). *Carried into:* `Teammate._completed_tool_events` field declaration + `tests/test_teammate.py::test_completed_tool_events_bounded_to_maxlen`.

- **D-3. Three append sites: `_on_post_common` (success + failure), `_close_open_tools` (death + kill).** No append in PreToolUse (only completed events), no append in `orphan_post` path (per F2/R3). *Rationale:* every dashboard-visible event is a completed-tool boundary; orphan_post is a JSONL-forensic diagnostic that would just confuse operators. *Carried into:* code in `sdk_teammate.py:_on_post_common` + `teammate.py:_close_open_tools` + `tests/test_sdk_teammate.py::test_completed_tool_events_appended_on_*` (4 scenarios: post_ok, post_fail, close_open_death, close_open_kill).

- **D-4. In-memory append is NOT gated on JSONL write success (F3 SC-8).** Deque append happens BEFORE the `broker._sink.write_tool_event` call; transcript exceptions are caught locally as best-effort. Disk-full does not blind the dashboard. *Carried into:* code ordering in append sites + `tests/test_sdk_teammate.py::test_completed_tool_events_appended_when_transcript_disabled`.

- **D-5. `BrokerSnapshot.tool_events` type tightened to `tuple[ToolEvent, ...]`.** *Rationale:* the #18 D-10 reservation specified flat-tuple-with-self-tagged-events; tightening from `Any` is forward-compatible (no consumer reads it today, per the cross-impact check below). *Carried into:* dataclass annotation + `tests/test_broker.py::test_snapshot_tool_events_type_is_tuple_of_toolevent`.

- **D-6. Snapshot flattens per-teammate deques + at-death tuples in one pass, sorted by `finished_at_wallclock` asc, stable.** *Rationale:* stable timestamp sort gives a deterministic chronological merged stream regardless of snapshot iteration order. *Carried into:* `Broker.snapshot()` implementation + `tests/test_broker.py::test_snapshot_tool_events_sorted_by_timestamp_stable`.

- **D-7. Tombstoned teammates contribute via `TeammateInfo.tool_events_at_death: tuple[ToolEvent, ...]` populated in `_tombstone_teammate`.** Mirrors the #14 `*_at_death` pattern (input/output/cost). Snapshot reads from the live teammate's deque if alive, from the TeammateInfo tuple if dead. *Rationale:* same pattern, same death-record semantics, no race between tombstone and snapshot. *Carried into:* `_tombstone_teammate` body + `tests/test_broker.py::test_tombstone_preserves_tool_events_at_death`.

- **D-8. UIServer merge happens in `_build_local_instance`, NOT as a separate `tool_events` field on the instance dict.** Convert each `ToolEvent` to a `{t, from, to:None, kind:"tool", body}` record; filter `tool_name == "Task"` at this step (Q2 revised); concatenate with envelope-derived messages; sort by `t` asc, stable. *Rationale:* a single transcript stream per crew is simpler than parallel arrays the frontend would have to merge; the existing `transcripts[crew_id]` payload absorbs the new entries with zero schema change; remote fanout via `_fetch_remote_state` is pass-through (already proven by Bucket B). *Carried into:* `_build_local_instance` body + `tests/test_ui_server.py::test_build_local_instance_merges_tool_events_*` (4 scenarios: ordering, Task filter, no-tool-events, tombstoned teammate).

- **D-9. Body format:** `f"{tool_name} ({outcome}, {duration_seconds:.2f}s)" + (f" — {args_summary}" if args_summary else "") + (f" [{error_summary}]" if error_summary else "")`. Examples:
  - `Bash (ok, 0.45s) — command=ls /tmp`
  - `Read (ok, 0.01s)`
  - `WebFetch (failed, 12.3s) [http 503]`
  *Rationale:* operator-readable at a glance; the typed fields stay available in `ToolEvent` for any future "tool timeline" view that wants structured data. *Carried into:* `_format_tool_event_body` helper in ui_server + `tests/test_ui_server.py::test_tool_event_body_format`.

- **D-10. `/api/state` schema stays unchanged at the top level.** Tool events ride inside `transcripts[crew_id]` (per D-8). Remote fanout in `_fetch_remote_state` is pass-through. SC-6's "additive extension" is satisfied by the inline merge — no actual schema change required. *Rationale:* simpler than a parallel field; remotes serve already-merged streams; no frontend logic change. *Carried into:* `_fetch_remote_state` unchanged + `tests/test_ui_server.py::test_api_state_includes_tool_events_inline_in_transcript`.

- **D-11. SC-1 freshness budget breakdown.** End-to-end ≤2s decomposes as: hook→deque <1ms; polling cycle 1.5s; `Broker.snapshot()` <5ms (D-6 math: 5 × 200 = 1000-element flatten+sort, sub-ms in practice); `_build_local_instance` merge <10ms; HTTP serialize <10ms; frontend WS receive + React render <50ms; remote fanout +100ms typical (capped by #13's 2s httpx timeout, which fails open). Local-only total worst-case ~1.6s; with remote ~1.7s. Both fit. *Carried into:* `tests/test_broker.py::test_snapshot_construction_under_5ms_at_design_scale` (SC-10 microbenchmark) + `tests/test_e2e_tool_events.py::test_completed_tool_event_visible_within_one_polling_cycle`.

- **D-12. Deque rollover is silent.** When the 201st event evicts the 1st, no marker is rendered. *Rationale:* matches existing envelope `log_limit=200` semantics; consistent operator mental model; if rollover signaling becomes a request, add an `is_truncated: bool` flag on the instance dict in v2. *Carried into:* documented; no code beyond bounded-deque behavior.

- **D-13. Completed-only stream; in-flight visibility relies on the agent-header `current_tool` badge.** Per SC-7. **Phase 1 finding (Bucket A): the badge today is plaintext in a secondary scrolling row, no animation, may require horizontal scroll under wide layouts.** A `BACKLOG.md` entry will be added to investigate badge-prominence boost (pinned, animated, or moved into the agent header proper). Out of scope for #19 — this is a #19-adjacent UX gap, not a #19 SC. *Carried into:* `doc/BACKLOG.md` entry logged at end of Phase 2 + Phase 5 retro carry-forward.

### Edge Cases

- **E-1. Empty teammate (just spawned, no tools fired).** Deque empty; snapshot contributes zero events from this teammate. Pass-through.
- **E-2. Deque full and rolling over.** Most-recent 200 retained; older silently evicted (D-12). Test: append 250, assert snapshot returns the latest 200 in arrival order.
- **E-3. Tombstone-time race.** `_close_open_tools` runs synchronously inside `_tombstone_teammate`; deque appends complete before `tool_events_at_death = tuple(deque)` snapshot. Single-threaded asyncio guarantees order.
- **E-4. Concurrent snapshots within one polling cycle.** Each call to `Broker.snapshot()` builds a fresh frozen tuple from a list copy of each deque; subsequent mutations on the live deque do not affect snapshots already returned.
- **E-5. Parallel tools (multiple PostToolUse fire near-simultaneously).** Each `_on_post_common` appends independently; asyncio single-threading serializes them; deque order matches PostToolUse arrival order; timestamp ordering preserved by D-6 stable sort.
- **E-6. Disk-full during JSONL write.** D-4 guarantees in-memory append happened first; transcript exception caught and logged; deque still has the event; dashboard renders normally. Test asserts snapshot has the event when transcript sink is disabled.
- **E-7. Task tool events in the deque.** Q2 revised — kept in deque, filtered at UIServer merge step (D-8). `BrokerSnapshot.tool_events` includes them for any future consumer; the dashboard transcript hides them.
- **E-8. orphan_post events.** Filtered at append site (D-3). Never enter the deque. JSONL still records them for forensics (existing #8 behavior unchanged).
- **E-9. Remote fanout failure.** `_fetch_remote_state` returns None → `_unreachable_instance` placeholder (existing #13 behavior). No tool events from that crew in the unified view; same staleness semantics as envelopes (per F5 SC-6).
- **E-10. Same-timestamp tool event and envelope.** Stable sort by `t` preserves arrival order (envelope first or tool first depending on which was appended first to the merged list). Documented; no tie-breaker needed.
- **E-11. Frontend receives `kind: "tool"` with `to: None`.** Existing `MiniMessage` only renders `body` for tool kind (Bucket A line 432-440 finding); `to` is unused. No frontend change required.
- **E-12. A teammate is killed mid-tool with no PostToolUse ever fired.** `_close_open_tools(reason="kill")` synthesizes a `ToolEvent(outcome="killed", duration_seconds=now - started_at_wallclock, error_summary="killed by lead")` — appended to deque before tombstone snapshots `tool_events_at_death`.
- **E-13. Snapshot with zero teammates.** `tool_events` is `()`. Pass-through.

**Display answers (template prompt):**
- *No tool events yet:* the transcript stream renders only envelopes (existing behavior). No "no tool events" placeholder needed — the absence is itself the signal.
- *Bounded retention exceeded:* silent truncation (D-12). The operator sees the most-recent 200 per teammate.
- *Zero-duration tool (`duration_seconds == 0.0`):* renders as `Read (ok, 0.00s)` — the formatter does not special-case zero. Documented.

### Validation Contracts at Handoff Boundaries

| Boundary | Preconditions | Failure Behavior | Postconditions | Rollback |
|---|---|---|---|---|
| Hook callback → deque append | `tool_use_id` correlates a Pre with a Post (or `_close_open_tools` synthesizes a synthetic finished_at) | If construction of `ToolEvent` fails (malformed kwargs), log WARNING with the offending field name and skip — do not raise inside the hook callback | Event appended to teammate's deque; `_last_tool_completed` updated (D-3 ordering: deque first, then `_last_tool_completed`, then transcript) | None — append is the operation; rollback would mean popping the just-appended entry, not worth it |
| Deque → BrokerSnapshot flatten | Teammate is alive (read deque) OR tombstoned (read `tool_events_at_death`) | If teammate is in the registry but neither live nor has `tool_events_at_death` populated (transient state mid-tombstone), contribute zero events from this teammate; do not raise | `snapshot.tool_events` is a flat tuple sorted by `finished_at_wallclock` asc, stable | None — read-only operation |
| Snapshot → UIServer merge | Snapshot is a frozen dataclass (immutable) | Per-event formatting failure (e.g., missing `tool_name`) → log WARNING and skip that event; envelope-derived messages still ship | `transcripts[crew_id]` is a flat list of `{t, from, to, kind, body}` records sorted by `t` asc | None |
| UIServer → `/api/state` JSON | merged transcript is a list of dicts | JSON serialization failure → existing `_handle_state` exception handler returns 500 (existing behavior) | Response shape unchanged at top level; per-crew transcript carries new `kind:"tool"` entries inline | None |
| Local UIServer → remote `/api/state` consumer (`_fetch_remote_state`) | Remote serves merged transcript (each instance is post-#19 once shipped) | Remote returns malformed transcript or pre-#19 schema (no tool entries) → still parses fine; pre-#19 remotes are silently tool-event-less in the unified view | Remote tool events appear in their own crew's transcript stream | None — fetch is read-only |

### Specification

**Producer side (per-teammate append):**

```
On PostToolUse / PostToolUseFailure (sdk_teammate.py:_on_post_common):
  1. Compute outcome, duration, error_summary (existing logic)
  2. IF outcome != "orphan_post":
       Build ToolEvent(teammate_id=self.id, ...)
       self._completed_tool_events.append(ToolEvent)   # D-4: BEFORE transcript write
  3. Update self._last_tool_completed (existing logic)
  4. broker._sink.write_tool_event("tool_end", {...})  # best-effort, may swallow

On _close_open_tools(reason ∈ {"death", "kill"}) (teammate.py):
  For each entry remaining in self._tool_uses:
    1. Build ToolEvent(outcome="abandoned" if reason=="death" else "killed",
                      finished_at_wallclock=time.time(),
                      duration_seconds=finished - started,
                      error_summary=f"{reason} during execution")
    2. self._completed_tool_events.append(ToolEvent)   # D-4
    3. broker._sink.write_tool_event("tool_end", {...})  # best-effort

On _tombstone_teammate (broker.py):
  After _close_open_tools runs (existing step 8b), insert a NEW step 8c:
    if teammate is not None:
        self._info[teammate_id] = dataclasses.replace(
            self._info[teammate_id],
            tool_events_at_death=tuple(teammate._completed_tool_events),
        )
  Step 9 (lifecycle event) follows.

  IMPORTANT: TeammateInfo is @dataclass(frozen=True). Direct field assignment
  raises FrozenInstanceError. Use dataclasses.replace() — same pattern #14
  uses for the other *_at_death fields. The two-step write (step 5 tombstone
  + step 8c tool_events) is intentional: _close_open_tools (step 8b) must run
  between them so abandoned/killed events are included in the captured tuple.
  The brief window where a snapshot sees tool_events_at_death=None is E-3 —
  contribute zero events from that teammate; do not block the snapshot.
```

**Initialization (T1 implementation note — sentinel F2):**

`_completed_tool_events` is read by `Broker.snapshot()` for every alive teammate. To avoid `AttributeError` on the existing `StubTeammate` test path (every non-live test), initialize the deque in **both** `SdkTeammate.__init__` and `StubTeammate.__init__`:

```python
self._completed_tool_events: collections.deque[ToolEvent] = collections.deque(
    maxlen=int(os.environ.get("CLAUDE_CREW_TOOL_EVENTS_PER_TEAMMATE", "200"))
)
```

The deque stays empty for `StubTeammate`; it exists so `Broker.snapshot()` can safely read it without branching on teammate type.

**Aggregator side (Broker.snapshot):**

```
def snapshot(self, log_limit: int | None = None) -> BrokerSnapshot:
    # ... existing teammate / live / log build ...
    all_events: list[ToolEvent] = []
    for tid, info in self._info.items():
        if info.alive:
            tm = self._teammates.get(tid)
            if tm is not None:
                all_events.extend(tm._completed_tool_events)
        elif info.tool_events_at_death is not None:
            all_events.extend(info.tool_events_at_death)
    all_events.sort(key=lambda e: e.finished_at_wallclock)  # stable
    return BrokerSnapshot(..., tool_events=tuple(all_events))
```

**Consumer side (UIServer):**

```
def _build_local_instance(snapshot: BrokerSnapshot) -> tuple[dict, list[dict]]:
    # ... existing instance + envelope-derived messages build ...
    for ev in snapshot.tool_events:
        if ev.tool_name == "Task":
            continue  # Q2 revised: filter at render time
        messages.append({
            "t": _ts(ev.finished_at_wallclock),
            "from": ev.teammate_id,
            "to": None,
            "kind": "tool",
            "body": _format_tool_event_body(ev),
        })
    messages.sort(key=lambda m: m["t"])  # stable
    return instance, messages

def _format_tool_event_body(ev: ToolEvent) -> str:
    base = f"{ev.tool_name} ({ev.outcome}, {ev.duration_seconds:.2f}s)"
    if ev.args_summary:
        base += f" — {ev.args_summary}"
    if ev.error_summary:
        base += f" [{ev.error_summary}]"
    return base
```

### Cross-Feature Integration Check

Verified by Bucket A + B reads (Phase 1 explorer + Phase 2 narrow re-explore):

- **`BrokerSnapshot.tool_events` consumers today:** `UIServer._build_local_instance` and `tests/test_broker.py` (existing snapshot tests). Tightening the type from `Any` to `ToolEvent` is safe — no other reader.
- **`Teammate._completed_tool_events` consumers (post-#19):** `Broker.snapshot()` only. New field; no migration needed.
- **`TeammateInfo.tool_events_at_death` consumers (post-#19):** `Broker.snapshot()` only. New field with `None` default; no migration.
- **`/api/state` consumers:** the dashboard frontend (`dashboard.html` MiniMessage), `_fetch_remote_state`. Both consume `transcripts[crew_id]` as a list of `{t, from, to, kind, body}` records. New `kind: "tool"` entries: frontend has render branch (Bucket A); `_fetch_remote_state` is pass-through. No consumer breaks.
- **`MiniMessage` (frontend):** existing branches for `kind: "tool"` and `kind: "thinking"` (Phase 1 Bucket A). Tool branch renders `body` as monospace with `▸` icon and `var(--st-tool)` color; ignores `to` field. No change required.

### Assumptions

- **A-1. Single-writer / single-reader for `_completed_tool_events`.** The teammate's own hook callbacks are the only writer; `Broker.snapshot()` is the only reader. asyncio single-threading serializes both. No lock needed. *Default: accept.*
- **A-2. Body string is operator-readable, not machine-parsable.** A future "tool timeline" view should query the typed `ToolEvent` fields, not parse `body` text. *Default: accept.*
- **A-3. 200 events × 5 teammates × broker-lifetime tombstones is the v1 design scale.** Memory: ~50 KB per teammate's deque + same per tombstone. 100 dead teammates would be ~5 MB. Real-world session sizes well within this. *Default: accept; revisit only if a real session exceeds.*
- **A-4. Stable timestamp sort is enough for human-readable interleaving.** Sub-millisecond same-timestamp ordering arbitrarily resolves to arrival order. Operators don't need stricter than that. *Default: accept.*
- **A-5. The `body` format choice (D-9) is acceptable for v1.** Operators get tool name, outcome, duration, optional args, optional error in one line. Open to a redesign in Phase 5 retro if real-use shows it's wrong. *Default: accept.*

### Open Questions

- [ ] **OQ-1. Indefinite tombstone retention vs. age-out.** Per D-7, `tool_events_at_death` retains for the broker's lifetime. A long crew session with many spawn-die cycles accumulates `(N_dead × 200)` events. Memory math (A-3) suggests v1 is fine; flag for v2 if it bites. **Proposal: indefinite v1; document and revisit. Confirm.**
- [ ] **OQ-2. Deque rollover marker.** D-12 chose silent truncation to match envelope `log_limit=200` semantics. Sentinel D1 raised this as a design question. **Proposal: silent v1, add `is_truncated: bool` on instance JSON if operators ask. Confirm.**
- [ ] **OQ-3. Body format (D-9).** The format is the operator's primary signal in the dashboard. Want to change anything? Examples:
  - `Bash (ok, 0.45s) — command=ls /tmp`
  - `Read (ok, 0.01s)`
  - `WebFetch (failed, 12.3s) [http 503]`
  Alternative phrasings considered: `Bash ▸ ok ▸ 0.45s`, `[Bash] ok 0.45s`, etc. **Default proposal as written; confirm or redirect.**

### Phase 2 → Phase 3 prep notes

Tasks the Phase 3 breakdown will need to cover:
- T1. `ToolEvent` dataclass + `_completed_tool_events` deque on **both** `SdkTeammate` and `StubTeammate` (sentinel F2) + env-var maxlen
- T2. `_on_post_common` and `_close_open_tools` append sites + `_tombstone_teammate` step 8c with `dataclasses.replace` (sentinel F1)
- T3. `BrokerSnapshot.tool_events` tightened type + flatten + stable sort by `finished_at_wallclock`
- T4. UIServer merge + Task filter + body formatter
- T5. E2E test: drive 50 events through one cycle; assert dashboard ordering, Task filter, tombstoned-teammate retention, disk-full safety

**Test additions called out by sentinel review (must appear in Phase 3 ACs):**
- T1 must include `test_tool_events_per_teammate_env_var_sets_maxlen` — set env var to a small value (e.g. `5`), construct teammate, append 6 events, assert deque length is 5 (sentinel D3 — env var coverage gap).
- T2 must include `test_pre_tool_use_does_not_append_to_completed_events` — fire `PreToolUse` without a matching Post, assert deque length unchanged (sentinel D2 — SC-7 has no failing test today).
- T2 must include `test_orphan_post_not_appended_to_completed_events` — fire PostToolUse without matching Pre (orphan_post path), assert deque length unchanged (SC-3 / D-3 explicit verification).
- T4 should sort the merged stream by the **raw float timestamps** (`finished_at_wallclock` for tool events, `env.timestamp` for envelopes) and apply `_ts()` formatting only to the output, NOT sort by the truncated ISO string. Today `_ts()` hard-codes `.000Z` (sub-second truncation); sorting by the formatted string makes E-10 ordering brittle within the same second. One-line fix in `_build_local_instance` (sentinel D1).
- T5 should add a stale-on-fetch-failure scenario — start two instances, kill the remote mid-test, assert the unified dashboard still shows the last successfully fetched tool events for the dead remote (SC-6 stale-on-failure clause).

`doc/BACKLOG.md` entry to add at task start: agent-header `current_tool` badge prominence boost (D-13 carryover — Phase 1 Bucket A finding + co-architect pushback C).

---

## Phase 3: Task Breakdown

Five tasks. T1 → T2 → T3 → T4 → T5 sequential (each task's tests need the prior task's implementation to pass). Each task ships independently testable code in one commit. **Verification commands listed below all fail today — `uv run pytest -k <name>` returns "no tests ran" because the test file or test name does not exist yet. After each task, the named tests must pass.**

Test file convention: each task adds tests to existing files where the surface lives (`test_teammate.py`, `test_sdk_teammate.py`, `test_broker.py`, `test_ui_server.py`) plus a new `tests/test_e2e_tool_events.py` for T5.

---

### Task 1: `ToolEvent` dataclass + per-teammate deque

**Depends on**: None | **Blocks**: T2, T3, T4, T5

Add the `ToolEvent` frozen dataclass and the `_completed_tool_events` deque on both teammate implementations. No append logic yet — just the storage and the env-var-controlled bound.

**Files touched:**
- `claude_crew/teammate.py` — add `ToolEvent` dataclass at module top (alongside `_ToolUseEntry`, `_SubagentUseEntry`)
- `claude_crew/sdk_teammate.py` — initialize `self._completed_tool_events` in `__init__` after the existing `_recently_closed_tool_use_ids` line
- `claude_crew/teammate.py` — initialize same in `StubTeammate.__init__` (sentinel F2)

**Acceptance Criteria**:

```
Scenario: ToolEvent dataclass field parity with #8's tool_end JSONL record
  Given the ToolEvent dataclass is defined
  Then it has exactly these fields with these types:
    teammate_id: str
    tool_name: str
    tool_use_id: str
    started_at_wallclock: float
    finished_at_wallclock: float
    duration_seconds: float
    outcome: str
    args_summary: str | None
    error_summary: str | None
    redaction_version: str
  And it is frozen (FrozenInstanceError on assignment)

Scenario: SdkTeammate initializes _completed_tool_events
  Given a fresh SdkTeammate is constructed
  Then teammate._completed_tool_events is an empty collections.deque
  And teammate._completed_tool_events.maxlen == 200

Scenario: StubTeammate initializes _completed_tool_events
  Given a fresh StubTeammate is constructed
  Then teammate._completed_tool_events is an empty collections.deque
  And teammate._completed_tool_events.maxlen == 200

Scenario: env var controls maxlen
  Given CLAUDE_CREW_TOOL_EVENTS_PER_TEAMMATE=5 in the environment
  When a teammate is constructed
  And 6 ToolEvent instances are appended
  Then teammate._completed_tool_events has length 5
  And the first-appended event has been evicted
```

**Verification**: `uv run pytest tests/test_teammate.py::test_tool_event_field_parity tests/test_teammate.py::test_completed_tool_events_init_sdk tests/test_teammate.py::test_completed_tool_events_init_stub tests/test_teammate.py::test_tool_events_per_teammate_env_var_sets_maxlen -v`

---

### Task 2: Hook append sites + tombstone capture

**Depends on**: T1 | **Blocks**: T3, T4, T5

Wire the three append sites (PostToolUse normal/failure, `_close_open_tools` death/kill) and the `_tombstone_teammate` step 8c capture. This is the core producer logic.

**Files touched:**
- `claude_crew/sdk_teammate.py` — in `_on_post_common`, **before** the existing `broker._sink.write_tool_event("tool_end", ...)` call, append `ToolEvent(...)` to `self._completed_tool_events` (D-4 ordering: deque first, transcript second). Skip if `outcome == "orphan_post"` (D-3 / SC-3).
- `claude_crew/teammate.py` — in `_close_open_tools(reason)`, append `ToolEvent(outcome="abandoned" if reason=="death" else "killed", ...)` for each in-flight entry **before** the transcript write.
- `claude_crew/broker.py` — in `_tombstone_teammate`, after step 8b (`_close_open_tools` runs), insert step 8c: `self._info[teammate_id] = dataclasses.replace(self._info[teammate_id], tool_events_at_death=tuple(teammate._completed_tool_events))`. Add `tool_events_at_death: tuple[ToolEvent, ...] | None = None` to `TeammateInfo` dataclass.

**Acceptance Criteria**:

```
Scenario: PostToolUse with outcome=ok appends to deque
  Given an SdkTeammate with empty _completed_tool_events
  When PreToolUse fires for tool_use_id "X" with tool_name "Bash"
  And PostToolUse fires for tool_use_id "X" successfully
  Then teammate._completed_tool_events has length 1
  And the event has tool_name="Bash" and outcome="ok"

Scenario: PostToolUse with outcome=failed appends to deque
  Given an SdkTeammate with empty _completed_tool_events
  When PreToolUse fires for tool_use_id "Y"
  And PostToolUseFailure fires for tool_use_id "Y" with an error
  Then teammate._completed_tool_events has length 1
  And the event has outcome="failed"
  And the event's error_summary is populated and redacted

Scenario: PreToolUse alone does NOT append (SC-7 / sentinel D2)
  Given an SdkTeammate with empty _completed_tool_events
  When PreToolUse fires but no PostToolUse follows
  Then teammate._completed_tool_events has length 0
  And teammate._tool_uses has the in-flight entry

Scenario: orphan_post does NOT append (SC-3 / sentinel test)
  Given an SdkTeammate with empty _completed_tool_events
  When PostToolUse fires for tool_use_id "Z" with NO prior PreToolUse
  Then a JSONL tool_end record with outcome="orphan_post" is written
  And teammate._completed_tool_events has length 0

Scenario: _close_open_tools(reason="death") appends abandoned events
  Given an SdkTeammate with two in-flight tools and empty _completed_tool_events
  When _close_open_tools(reason="death") runs
  Then teammate._completed_tool_events has length 2
  And every event has outcome="abandoned"

Scenario: _close_open_tools(reason="kill") appends killed events
  Given an SdkTeammate with one in-flight tool and empty _completed_tool_events
  When _close_open_tools(reason="kill") runs
  Then teammate._completed_tool_events has length 1
  And the event has outcome="killed"

Scenario: _tombstone_teammate captures tool_events_at_death (sentinel F1)
  Given an SdkTeammate with 3 completed tool events and 1 in-flight tool
  When _tombstone_teammate is called
  Then info.alive == False
  And info.tool_events_at_death has length 4
    (3 originally completed + 1 abandoned by _close_open_tools)
  And the last event has outcome="abandoned"
  And no FrozenInstanceError was raised

Scenario: in-memory append happens regardless of JSONL write success (SC-8)
  Given the transcript sink is disabled (CLAUDE_CREW_TRANSCRIPT_DISABLED=1)
  When PostToolUse fires for a completed tool
  Then teammate._completed_tool_events has length 1
  And no exception escaped the hook callback
```

**Verification**: `uv run pytest tests/test_sdk_teammate.py -k "test_completed_tool_events_appended or test_pre_tool_use_does_not_append or test_orphan_post_not_appended or test_completed_tool_events_appended_when_transcript_disabled" tests/test_teammate.py::test_close_open_tools_appends_to_completed_events tests/test_broker.py::test_tombstone_preserves_tool_events_at_death -v`

---

### Task 3: BrokerSnapshot tightening + flatten

**Depends on**: T2 | **Blocks**: T4, T5

Tighten `BrokerSnapshot.tool_events` type to `tuple[ToolEvent, ...]` and populate it in `Broker.snapshot()` by flattening live + dead per-teammate events with stable timestamp sort.

**Files touched:**
- `claude_crew/broker.py` — change `tool_events: tuple[Any, ...] = ()` annotation to `tuple[ToolEvent, ...] = ()`. In `Broker.snapshot()`, add the flatten loop (live teammates: read `tm._completed_tool_events`; tombstoned: read `info.tool_events_at_death`); sort by `finished_at_wallclock` (stable); pass to `BrokerSnapshot(...)`.

**Acceptance Criteria**:

```
Scenario: snapshot.tool_events default is empty tuple (no regression)
  Given a fresh broker with one StubTeammate (no events)
  When broker.snapshot() is called
  Then snapshot.tool_events == ()
  And the existing `tests/test_broker.py::test_snapshot_tool_events_default_empty` still passes

Scenario: snapshot flattens live teammate events
  Given two SdkTeammates each with 3 completed tool events
  When broker.snapshot() is called
  Then snapshot.tool_events has length 6
  And every entry is a ToolEvent instance
  And each event's teammate_id matches the originating teammate

Scenario: snapshot includes tombstoned teammate's at-death events
  Given one teammate with 2 events that has been tombstoned
  And one alive teammate with 3 events
  When broker.snapshot() is called
  Then snapshot.tool_events has length 5

Scenario: snapshot sort is stable by finished_at_wallclock asc
  Given a broker with events at timestamps [1.0, 3.0, 2.0, 2.0, 1.5] across 3 teammates
  When broker.snapshot() is called
  Then snapshot.tool_events timestamps in order are [1.0, 1.5, 2.0, 2.0, 3.0]
  And the two events at 2.0 preserve their teammate-of-origin order

Scenario: SC-10 microbenchmark — snapshot under 5ms at design scale
  Given 5 SdkTeammates each with 200 completed tool events (deque full)
  When broker.snapshot() is called 10 times in a row
  Then the median call time is < 5 ms
  And the 95th percentile is < 10 ms
```

**Verification**: `uv run pytest tests/test_broker.py -k "test_snapshot_tool_events" -v`

---

### Task 4: UIServer merge + Task filter + body formatter

**Depends on**: T3 | **Blocks**: T5

Extend `UIServer._build_local_instance` to merge tool events into the per-crew transcript stream. Filter `tool_name == "Task"` at this step (Q2 revised). Sort by raw float timestamps (sentinel D1) before applying `_ts()` formatting.

**Files touched:**
- `claude_crew/ui_server.py` — add `_format_tool_event_body(ev: ToolEvent) -> str` helper (D-9 format). In `_build_local_instance`, after the existing envelope-derived messages list is built, iterate `snapshot.tool_events`, skip Task entries, append `{t, from, to:None, kind:"tool", body}` records. Sort the merged list by raw float timestamp (collect (float_t, record) tuples, sort, then map to records with `_ts()`-formatted `t`). Return the merged list.

**Acceptance Criteria**:

```
Scenario: tool events appear as kind:"tool" entries in messages
  Given a snapshot with 2 envelopes and 3 tool events (none Task)
  When _build_local_instance(snapshot) is called
  Then the returned messages list has length 5
  And exactly 2 entries have kind="msg"
  And exactly 3 entries have kind="tool"
  And every tool entry has to=None and from set to a teammate_id

Scenario: Task tool events are filtered out (Q2 / D-8)
  Given a snapshot with 3 Bash events and 2 Task events
  When _build_local_instance(snapshot) is called
  Then the returned messages list has 3 tool entries (Bash only)
  And no entry's body starts with "Task "

Scenario: merged stream sorted by raw timestamp, not formatted string (sentinel D1)
  Given a snapshot with a tool event at t=1.5 and an envelope at t=1.2 and a tool at t=1.7
  When _build_local_instance(snapshot) is called
  Then messages are in order: envelope(1.2), tool(1.5), tool(1.7)
  And same-second sub-second precision is preserved in the SORT
  (verifier uses raw float timestamps from input, not the truncated ISO _ts strings)

Scenario: body format matches D-9
  Given a ToolEvent(tool_name="Bash", outcome="ok", duration_seconds=0.45, args_summary="command=ls /tmp", error_summary=None)
  When _format_tool_event_body(ev) is called
  Then the result is exactly "Bash (ok, 0.45s) — command=ls /tmp"

  Given a ToolEvent(tool_name="WebFetch", outcome="failed", duration_seconds=12.3, args_summary=None, error_summary="http 503")
  When _format_tool_event_body(ev) is called
  Then the result is exactly "WebFetch (failed, 12.3s) [http 503]"

  Given a ToolEvent(tool_name="Read", outcome="ok", duration_seconds=0.01, args_summary=None, error_summary=None)
  When _format_tool_event_body(ev) is called
  Then the result is exactly "Read (ok, 0.01s)"

Scenario: empty snapshot.tool_events leaves messages unchanged
  Given a snapshot with envelopes only (tool_events == ())
  When _build_local_instance(snapshot) is called
  Then the returned messages list is identical to the existing envelope-only behavior
  (regression guard for existing test_ui_server tests)
```

**Verification**: `uv run pytest tests/test_ui_server.py -k "test_build_local_instance_merges_tool_events or test_tool_event_body_format or test_task_tool_filtered or test_merged_stream_sorted_by_raw_timestamp" -v`

---

### Task 5: End-to-end integration tests

**Depends on**: T1, T2, T3, T4 | **Blocks**: Documentation / Phase 5

Cohesive E2E tests that exercise the full pipeline: hook fires → deque appends → snapshot flattens → UIServer merges → JSON serializes → consumer reads. New file `tests/test_e2e_tool_events.py`.

**Happy Path Scenarios**:

```
Scenario: completed tool event visible within one polling cycle (SC-1)
  Given a broker with one SdkTeammate (or stub-driven equivalent that fires hooks)
  When PostToolUse fires for a tool_use_id that had a prior PreToolUse
  And one polling cycle (≤2s) elapses
  When the dashboard's /api/state response is fetched
  Then the response includes a kind:"tool" entry under transcripts[crew_id]
  And the entry's body matches the D-9 format
  And the entry's `from` is the teammate_id

Scenario: multi-teammate interleaved stream (SC-2)
  Given two teammates A and B each completing 5 tool events with interleaved timestamps
  When /api/state is fetched
  Then transcripts[crew_id] is sorted by timestamp ascending
  And both teammates' events appear in the same merged list
  And the order matches the actual completion order
```

**Sad Path Scenarios**:

```
Scenario: tool event from a tombstoned teammate stays visible (SC-4)
  Given a teammate that completed 3 tool events
  And was then tombstoned
  When /api/state is fetched after tombstone
  Then transcripts[crew_id] still contains the 3 tool entries
  And they appear with the originating teammate_id in `from`

Scenario: in-flight tool at tombstone-time appears as abandoned (SC-4 / D-3)
  Given a teammate with 1 in-flight tool (PreToolUse fired, no Post)
  When the teammate is tombstoned (e.g., kill_teammate or SDK death)
  And /api/state is fetched
  Then the in-flight tool appears in the stream with outcome="abandoned" or "killed"
  And the body reflects that outcome

Scenario: disk-full does NOT blind the dashboard (SC-8 / D-4)
  Given the JSONL transcript sink is disabled
  When PostToolUse fires for a completed tool
  And /api/state is fetched
  Then the kind:"tool" entry appears in the response
  And no exception escaped to the hook layer

Scenario: deque rollover silently retains the most-recent N events (SC-5 / D-12)
  Given CLAUDE_CREW_TOOL_EVENTS_PER_TEAMMATE=10 in the environment
  And a teammate completes 25 tool events
  When /api/state is fetched
  Then transcripts[crew_id] contains exactly 10 tool entries
  And those 10 are the most recently completed (the first 15 are silently dropped)

Scenario: stale-on-fetch-failure for remote instance (SC-6)
  Given two claude-crew instances running locally
  And the remote instance has 3 tool events visible
  When the remote instance becomes unreachable mid-test
  And /api/state is fetched on the local instance
  Then the unified dashboard shows the LAST successfully fetched tool events for the dead remote
  And the unreachable instance is marked status="unreachable"
  And local instance's tool events are unaffected

Scenario: Task tool events do NOT pollute the stream (Q2 / D-8)
  Given a teammate that fires 5 tool events: 3 Bash + 2 Task
  When /api/state is fetched
  Then transcripts[crew_id] contains exactly 3 kind:"tool" entries (Bash only)
  But snapshot.tool_events contains all 5 (Task preserved upstream of UIServer)
```

**Verification**: `uv run pytest tests/test_e2e_tool_events.py -v` — all happy + sad scenarios pass.

**Live-probe checklist:** N/A. This feature has no live SDK probe — all behavior is exercised via stub-mode + direct hook simulation. Live SDK confirmation will happen in Phase 5 manual verification (spawn a real teammate, run a Bash, watch the dashboard).

---

**Phase 3 → Phase 4 prep:**

- All 5 verification commands fail today (`pytest -k <name>` → "no tests ran"). Each task's commit moves the named tests from "missing" to "passing."
- Sentinel review at T3-T4 boundary (3 tasks complete, before T4 polish).
- Final sentinel at T5 + manual live-spawn check before Phase 5.
- BACKLOG entry added at T1 start: agent-header `current_tool` badge prominence (D-13 / Phase 1 Bucket A).

**SC traceability** (every Phase 1 SC traces to at least one Phase 3 task):

| SC | Task(s) |
|---|---|
| SC-1 (≤2s visibility) | T5 happy-path #1 |
| SC-2 (interleaved sort) | T4 + T5 happy-path #2 |
| SC-3 (fields + orphan_post filter) | T1 + T2 |
| SC-4 (tombstoned retention + in-flight-at-death) | T2 + T5 sad-path #1, #2 |
| SC-5 (bounded N + env var) | T1 + T5 sad-path #4 |
| SC-6 (remote stale-on-failure) | T5 sad-path #5 |
| SC-7 (completed-only) | T2 (Pre-non-append test) |
| SC-8 (append not gated on JSONL) | T2 + T5 sad-path #3 |
| SC-9 (no new tools/envelopes) | architectural — verified by absence of new files |
| SC-10 (snapshot <5ms benchmark) | T3 |
| SC-11 (stream record shape) | T4 |

---

## Phase 4: Implementation

*To be filled in Phase 4.*

---

## Phase 5: Completion

### Verification

- [x] Feature works against Phase 1 SCs — sentinel walked all 11 SCs to named tests, all ✅
- [x] No regressions — 585 passed (started branch at 550), no skipped failures
- [x] FEATURE.md spec matches implementation — D-1 through D-13 all enforced
- [x] PRODUCT-VISION.md updated — see journal entry below
- [x] BACKLOG.md updated — D-13 (current_tool badge prominence) entry logged
- [x] Live spawn check — general-purpose teammate fired Read tool, `last_tool_completed` populated, deque path verified by code-adjacency to that update

### Retrospective

**What went well**

- **Pre-load co-architect's three-pushback warmup BEFORE Phase 2.** Co-architect named flat-tuple-shape-vs-retention-math, SC-1 freshness budget, and SC-7-doesn't-solve-operator-silence at Phase 1 review — all three became Phase 2 design pillars (D-6, D-11, BACKLOG D-13). The "name three things you'll push back on" prompt at spawn time keeps paying off.
- **Sentinel review at T2-T3 boundary, not T3-T4.** Co-architect's recommendation. T2 was the highest-risk task (3 hook callsites + tombstone state mutation); reviewing it before T3 froze the dataclass shape caught five gaps cheap (turn_end coverage, interrupted coverage, parallel-tools coverage, missing assertion + 1 cosmetic). Reviewing T3-T4 as a unit at final added the remaining cleanup. Two checkpoints, not three, was the right cadence.
- **The "spec pseudocode bug" pattern at Phase 2 sentinel review.** Sentinel F1 caught that my Phase 2 pseudocode mutated a frozen dataclass directly (`info.tool_events_at_death = ...` would have FrozenInstanceError'd at runtime). Sentinel F2 caught that StubTeammate / _NoopTeammate needed the deque init too or 550+ tests would AttributeError. Both were spec-time fixes, zero implementation cost. Phase 2 sentinel pays off when it reads the pseudocode like an implementer would.
- **D-4 ordering enforcement (in-memory append BEFORE transcript write).** This was the single load-bearing decision for SC-8 (disk-full doesn't blind dashboard). Encoding it as a 2-line code ordering with a named test made it impossible to drift. The `test_completed_tool_events_appended_when_transcript_disabled` unit test plus `test_disk_full_does_not_blind_dashboard` E2E catches any future refactor that swaps the order.
- **No `/api/state` schema change.** Tool events ride inline in `transcripts[crew_id]` (D-8). This makes #13's existing remote-fanout pass-through carry tool events for free — SC-6 satisfied with zero new code in `_fetch_remote_state`. The simplest extension wins.
- **Sentinel Defer-1 fix landed in same task as the gap was found.** "Add a turn_end test" surfaced at the T2-T3 checkpoint. Two-line addition before T3 — done. Deferring to T5 would have been technically correct but added context-switch cost. Land tiny coverage gaps where they're noticed if the cost is two lines.

**What was friction**

- **D-9 body format spec example was wrong.** I wrote `WebFetch (failed, 12.3s) [http 503]` in the spec but the format string `{:.2f}` produces `12.30s`. Test caught it; took one minute to fix the test. The spec should have said `12.30s` from the start. Lesson: when writing format-string examples in a spec, run them through `.format()` mentally before pasting. Or: write the test first, read the failure, paste the actual output into the spec.
- **`_NoopTeammate` test fixture initialization gap was caught at T2 test runtime, not at design.** Sentinel F2 flagged `StubTeammate` needed the deque init, but the project also has `_NoopTeammate` in `test_broker.py` as a separate minimal teammate. Both needed the field. T2 test failure surfaced it cleanly (one-line fix), but a Phase 2 grep for "class.*Teammate" or "_tool_uses.*=" in tests/ would have caught both at spec time. Add: when adding a new field to the Teammate ABC, grep all teammate subclasses (production + test fixtures) at Phase 2.
- **One commit was a "feature feature commit + sentinel-defer-fix combo" (T3 included the turn_end test from T2-T3 checkpoint).** Mostly fine — the defer-fix was small and same-area. But the commit message had to explain both. Cleaner: separate sentinel-defer fixes from task commits when they touch different test classes. Not a hard rule; readability call.

**Improvements**

1. **Add to TEMPLATE.md Phase 2 gate:** "When adding a new field to a base class with multiple subclasses (including test fixtures), grep all subclasses and list each one in the implementation notes." Catches the F2-style gap at spec time.
2. **Add to TEMPLATE.md Phase 2 gate:** "When the spec includes string-formatting examples, run the format string against the example values before locking the spec." Catches the D-9-style gap at spec time, before any test is written.
3. **For dashboard/UX features, add to Phase 1 sentinel prompt:** "Does the data-pipeline change actually deliver the user-experience win the problem statement promises, or does it deliver the data and rely on a deferred UX change?" — would have surfaced D-13 as "this isn't done; a follow-up is required" rather than "we'll log to BACKLOG." Not always actionable but worth asking.

**Workflow updates made**

- [ ] TEMPLATE.md updated — improvements 1 and 2 above are general enough to lift into the template, but I'm leaving them as retro notes for now; the next 1-2 features will tell me whether they're load-bearing or one-time.
- [x] BACKLOG.md updated — D-13 entry logged for the current_tool badge prominence follow-up
- [x] PRODUCT-VISION.md updated — #19 marked done + journal entry + co-architect's MERGE-WITH-NOTE flag carried forward to elevate the D-13 follow-up from BACKLOG to the next dashboard-UX feature row
