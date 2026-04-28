# Feature: Subagent-Activity Envelopes

**Status**: In Progress (Phase 2)
**Created**: 2026-04-27
**Feature**: #7 in PRODUCT-VISION.md pipeline

---

## Phase 1: Research & Requirements

### Problem Statement

When a teammate spawns a subagent (e.g., a sentinel spawning parallel Haiku readers), the lead has no way to observe it in real time. The activity crossed the bus during Feature #5's real-task validation run — we only confirmed it happened by asking each sentinel teammate after the fact. The bus is blind to the most expensive, parallel work happening inside a crew.

This closes the v1 documented limit from Feature #4: "subagent activity does not cross the broker." Capability #2 (recursive subagent spawning) is already being exercised in production — we just can't see it.

**Scope boundary (co-architect):** Subagents are SDK-internal Task invocations — not broker-registered peers. The lead can *observe* them via status and JSONL but cannot address, message, or intervene with them. The lead cannot `send_to` a subagent. Addressability is out of scope for v1.

### Prior Art

- **Feature #8 (tool-execution telemetry):** Established the hook-based subagent detection pattern. `PreToolUse` with `agent_id` present = subagent spawn. `PostToolUse` with `agent_id` present = subagent completion. Currently: activity stamped (`_stamp_activity()`), tool-use tracking skipped entirely (D3 invariant at `sdk_teammate.py:238`). The "skip" is the gap this feature fills.
- **`TaskNotificationMessage`:** The Agent SDK stream event signaling subagent completion/failure. `_collect_response_text` captures `last_failed_task_notif` (single-slot, most-recent-wins) for error synthesis. Carries `task_id` and `summary`. **Unknown whether `task_id` correlates to parent `tool_use_id` — requires spike before Phase 2.**
- **Feature #6 / #8 status pattern:** `status_snapshot()` + `get_teammate_status` MCP tool. Feature #8 added `current_tools` / `last_tool_completed` as a new tracking lane. This feature adds a parallel `current_subagents` / `last_subagent_completed` lane — separate namespace, not shared with `_tool_uses`.

### Success Criteria

- [ ] **SC-1 (spawn detection):** When `PreToolUse` fires with `agent_id` present on an `SdkTeammate`, the event is routed to a subagent-spawn tracking path — distinct from the regular tool-use path. **D3 extended invariant (co-architect):** `current_tools`, `last_tool_completed`, `_tool_uses`, and `_recently_closed_tool_use_ids` are NOT read or written by the subagent path. Subagent state lives in a separate namespace.

- [ ] **SC-2 (spawn transcript record):** A `subagent_spawn` JSONL record is written to the transcript sink at spawn time with fields: `v: 1`, `kind: "subagent_spawn"`, `ts`, `crew_id`, `teammate_id`, `agent_id`, `tool_use_id`, `spawned_at_wallclock`.

- [ ] **SC-3 (result transcript record):** A `subagent_result` JSONL record is written when the corresponding `PostToolUse` fires with fields: `v: 1`, `kind: "subagent_result"`, `ts`, `crew_id`, `teammate_id`, `agent_id`, `tool_use_id`, `outcome` (`"ok"` | `"failed"` | `"abandoned"` | `"killed"`), `duration_seconds`, `summary` (from correlation mechanism determined by spike — may be `null` if correlation is ambiguous or unavailable), `finished_at_wallclock`. **Summary is best-effort; must not synthesize correlation that doesn't exist in the SDK.**

- [ ] **SC-4 (in-flight status visibility — additive):** `get_teammate_status(teammate_id)` gains `current_subagents: list[dict]` — in-flight subagent calls each with `{agent_id, tool_use_id, spawned_at_wallclock}`. Empty list when none in flight. All existing keys in the status payload are preserved; no existing keys removed or repurposed.

- [ ] **SC-5 (last subagent completed — additive):** `get_teammate_status(teammate_id)` gains `last_subagent_completed: dict | null` — most recently closed subagent with `{agent_id, tool_use_id, outcome, duration_seconds, summary, finished_at_wallclock}`. Null if no subagent has completed yet for this teammate instance.

- [ ] **SC-6 (lead observability — pull model):** Lead observes subagent lifecycle via `get_teammate_status` (SC-4/5) and JSONL transcript (SC-2/3). No new bus envelopes pushed to the lead's inbox. Rationale: subagent activity is high-frequency relative to teammate turns; push would pollute the lead inbox and degrade its primary semantic (peer-to-peer addressed communication). JSONL is the forensics path; status poll is the real-time view.

- [ ] **SC-7 (TaskNotificationMessage linkage):** The `subagent_result` record's `summary` field is populated via the correlation mechanism determined by the pre-Phase-2 spike. If no SDK-native correlation exists, summary is populated only for single-subagent turns (safe) and is `null` otherwise (honest). The existing `_handle_one_turn` failure synthesis path (empty text + `last_failed_task_notif` → `invalid_response` envelope) is preserved and not broken.

- [ ] **SC-8 (abandoned cleanup — cheap v1):** If a teammate dies or is killed while subagents are in flight, a single `subagent_abandoned_batch` JSONL record is emitted with the in-flight subagent list, and `current_subagents` is cleared in the tombstone. Per-subagent `subagent_result(abandoned)` synthesis is deferred to BACKLOG pending a downstream consumer need. Tombstone gains `in_flight_subagents_at_death: int` (additive field).

- [ ] **SC-9 (tombstone state — additive):** The broker tombstone for a dead/killed teammate gains `in_flight_subagents_at_death: int` (count of subagents in flight at death). No existing tombstone fields removed or renamed.

- [ ] **SC-10 (no regression):** All 295 existing tests pass. Feature #8 tool telemetry (tool_start/tool_end, current_tools, last_tool_completed) is unaffected for non-subagent tool calls.

- [ ] **SC-11 (parallel fan-out — co-architect):** `current_subagents` is a list keyed by `tool_use_id`. N concurrent subagents from the same teammate track independently. No entry clobbers another. Verified by a test driving two simultaneous subagent PreToolUse fires followed by their respective PostToolUse fires.

- [ ] **SC-12 (F8 invariant preservation — co-architect):** Subagent events do not mutate `_tool_uses`, `current_tools`, `last_tool_completed`, or `_recently_closed_tool_use_ids`. Verified by an integration test that fires a subagent PreToolUse + PostToolUse and asserts those four fields are unchanged.

- [ ] **SC-13 (hook exception isolation — co-architect):** An exception raised inside the subagent tracking path (spawn recording, result recording) does not crash the teammate. The main loop continues; the subagent goes untracked for that event. Verified by a test that injects a fault into the subagent tracking path and confirms the teammate processes subsequent messages normally.

- [ ] **SC-14 (ordering guarantee — co-architect):** For a given `tool_use_id`, the `subagent_spawn` JSONL record is always written before the corresponding `subagent_result` record. The abandoned-cleanup path must preserve this: a `subagent_abandoned_batch` record cannot be emitted before its corresponding `subagent_spawn` records.

### Questions

- [x] **Q1 (lead observability mechanism) — RESOLVED:** Pull model. No bus envelopes pushed to lead inbox. See SC-6 rationale.

- [x] **Q2 (TaskNotificationMessage correlation — RESOLVED by spike + co-architect):** `TaskNotificationMessage` carries `tool_use_id: str | None` as a first-class optional field (`claude_agent_sdk/types.py:1048`). The same `tool_use_id` flows through the full lifecycle: `PreToolUseHookInput.tool_use_id` → `TaskStartedMessage.tool_use_id` → `TaskProgressMessage.tool_use_id` → `TaskNotificationMessage.tool_use_id`. Direct correlation — no LIFO, no proximity heuristic. TNM fires for all three statuses: `"completed"`, `"failed"`, `"stopped"` — so SC-3 `summary` is available for all outcomes, not just failures. **Phase 2 implications (co-architect):** (1) `_collect_response_text`'s single-slot `last_failed_task_notif` → `task_notifs_by_tool_use_id: dict[str, TNM]`, accumulates all TNM statuses. (2) PostToolUse hook reads from this dict by `tool_use_id`. (3) `TaskStartedMessage` and `TaskProgressMessage` are explicitly NOT consumed in v1 — no consumer, would re-open push semantics. Route both to BACKLOG.

- [x] **Q3 (parallel fan-out) — RESOLVED:** Handled by SC-11 design — `current_subagents` list keyed by `tool_use_id`, N concurrent entries track independently.

### Constraints & Dependencies

- Requires: `claude_crew/sdk_teammate.py`, `claude_crew/broker.py`, `claude_crew/server.py`
- Breaking changes: additive only — new fields on `status_snapshot()` return dict, new JSONL kinds, new tombstone field. Existing fields untouched.
- No new package dependencies.
- D3 extended invariant: subagent calls must NOT touch `_tool_uses`, `current_tools`, `last_tool_completed`, or `_recently_closed_tool_use_ids`. These fields are main-agent-only.
- SC-8 cheap path: per-subagent `subagent_result(abandoned/killed)` symmetry deferred to BACKLOG. If added later, ensure SC-14 ordering holds.

**Gate**: Questions answered, success criteria measurable, constraints documented, user confirmed.

---

## Phase 2: Design & Specification

### Architecture Overview

Feature #7 adds a parallel subagent-tracking lane to `SdkTeammate` that mirrors the tool-tracking lane added by Feature #8, but lives in a completely separate namespace. Three surfaces are affected:

1. **`sdk_teammate.py`** — hook callbacks extended at the D3 branch; new instance state; `_collect_response_text` extended to accumulate TNMs by `tool_use_id` via callback; `subagent_result` JSONL emitted from `_end_turn` (not PostToolUse hook) to guarantee all TNMs are in; `status_snapshot()` overridden in `SdkTeammate` to add subagent fields.
2. **`broker.py`** — `TeammateInfo` gains `in_flight_subagents_at_death` and `last_subagent_completed_at_death`; `_tombstone_teammate` calls new `_close_open_subagents()`; `get_teammate_status` surfaces subagent fields.
3. **`transcript.py`** — `write_tool_event` Literal extended to include `subagent_spawn`, `subagent_result`, `subagent_abandoned_batch`.

No new files. No new MCP tools (SC-6: pull model via existing `get_teammate_status`). No new package dependencies.

### Data / API Contracts

**New dataclass — `_SubagentUseEntry` (sdk_teammate.py):**
```python
@dataclass(frozen=True)
class _SubagentUseEntry:
    agent_id: str        # from inp["agent_id"] at PreToolUse
    tool_use_id: str     # SDK's tool_use_id — dict key (denormalized)
    spawned_at_wallclock: float  # time.time() at PreToolUse hook fire
```

**New SdkTeammate instance fields (init):**
```python
self._subagent_uses: dict[str, _SubagentUseEntry] = {}            # in-flight, keyed by tool_use_id
self._closed_subagent_scratch: dict[str, _ClosedSubagentEntry] = {}  # closed this turn, awaiting TNM
self._recently_closed_subagent_use_ids: deque[str] = deque(maxlen=64)  # dedup guard
self._last_subagent_completed: dict[str, Any] | None = None       # most recent closed (for status)
self._task_notifs_by_tool_use_id: dict[str, TaskNotificationMessage] = {}  # TNMs from stream
```

New dataclass `_ClosedSubagentEntry` (sdk_teammate.py, alongside `_SubagentUseEntry`):
```python
@dataclass(frozen=True)
class _ClosedSubagentEntry:
    agent_id: str
    tool_use_id: str
    spawned_at_wallclock: float
    finished_at_wallclock: float
    hook_outcome: str  # "ok" | "failed" — from PostToolUse vs PostToolUseFailure
```

**TurnDrainResult change:**
```python
# Before (F8):
@dataclass
class TurnDrainResult:
    text: str
    last_failed_task_notif: TaskNotificationMessage | None

# After (F7):
@dataclass
class TurnDrainResult:
    text: str
    failed_task_notifs: list[TaskNotificationMessage]  # all failed/stopped TNMs this turn
```

**`_collect_response_text` signature change:**
```python
async def _collect_response_text(
    client: Any,
    stamp_activity: Callable[[], None] | None = None,
    record_task_notif: Callable[[str, TaskNotificationMessage], None] | None = None,
) -> TurnDrainResult:
```
The `record_task_notif` callback is called for ALL TNM statuses (completed/failed/stopped) with `(tool_use_id, tnm)`. If `tnm.tool_use_id` is None, callback is skipped (can't correlate). Caller (`SdkTeammate._handle_one_turn`) passes `self._record_task_notif` which stores into `self._task_notifs_by_tool_use_id`.

**`subagent_spawn` JSONL record:**
```json
{"v": 1, "kind": "subagent_spawn", "ts": 1777338000.0, "crew_id": "abc12345",
 "teammate_id": "t-...", "agent_id": "sub-...", "tool_use_id": "toolu_...",
 "spawned_at_wallclock": 1777338000.0}
```

**`subagent_result` JSONL record:**
```json
{"v": 1, "kind": "subagent_result", "ts": 1777338045.0, "crew_id": "abc12345",
 "teammate_id": "t-...", "agent_id": "sub-...", "tool_use_id": "toolu_...",
 "outcome": "ok",
 "duration_seconds": 45.2,
 "summary": "Found 3 files matching the pattern.",
 "finished_at_wallclock": 1777338045.0,
 "tnm_missing": false}
```
Outcome values: `"ok"` | `"failed"`. (Abandoned/killed go to `subagent_abandoned_batch`.)
`tnm_missing: bool` — `true` when no TNM was correlated by `tool_use_id` at emit time. Should effectively never be true with the `_end_turn` emit pattern (stream fully drained), but kept as a production diagnostic. A WARNING is logged when `tnm_missing=true`.

**`subagent_abandoned_batch` JSONL record:**
```json
{"v": 1, "kind": "subagent_abandoned_batch", "ts": 1777338060.0, "crew_id": "abc12345",
 "teammate_id": "t-...", "reason": "death",
 "subagents": [
   {"agent_id": "sub-1", "tool_use_id": "toolu_1", "spawned_at_wallclock": 1777338010.0},
   {"agent_id": "sub-2", "tool_use_id": "toolu_2", "spawned_at_wallclock": 1777338015.0}
 ]}
```

**`get_teammate_status` additions (alive path):**
```python
{
    # ... all existing F6/F8 fields unchanged ...
    "current_subagents": [
        {"agent_id": "sub-...", "tool_use_id": "toolu_...", "spawned_at_wallclock": float}
    ],  # empty list when none in flight
    "last_subagent_completed": {
        "agent_id": "sub-...", "tool_use_id": "toolu_...",
        "outcome": "ok", "duration_seconds": float,
        "summary": str | None, "finished_at_wallclock": float
    } | None,
    "in_flight_subagents_at_death": None,  # always None for alive
}
```

**`get_teammate_status` additions (tombstone path):**
```python
{
    # ... all existing F6/F8 tombstone fields unchanged ...
    "current_subagents": [],     # always empty post-death
    "last_subagent_completed": info.last_subagent_completed_at_death,  # preserved, mirrors F8
    "in_flight_subagents_at_death": 2,  # count at time of death
}
```

**`TeammateInfo` additions (both additive; `None` for alive teammates):**
```python
in_flight_subagents_at_death: int | None = None
last_subagent_completed_at_death: dict[str, Any] | None = None  # mirrors last_tool_completed_at_death
```

**`transcript.py` Literal extension:**
```python
def write_tool_event(
    self,
    event: Literal["tool_start", "tool_end",
                   "subagent_spawn", "subagent_result", "subagent_abandoned_batch"],
    fields: dict[str, Any],
) -> None:
```

### Design Decisions

- **D1 (subagent state in SdkTeammate, not base Teammate).** StubTeammate doesn't spawn subagents; polluting the base class with optional subagent attributes invites the same pattern StubTeammate would need to stub. Instead, `_SubagentUseEntry`, `_subagent_uses`, `_last_subagent_completed`, and `_task_notifs_by_tool_use_id` are `SdkTeammate`-only. `status_snapshot()` is overridden in `SdkTeammate` (calls `super().status_snapshot()` then merges subagent fields). `get_teammate_status` uses `snap.get("current_subagents", [])` for safe access — consistent with F6/F8 pattern. *Carried into:* `sdk_teammate.py:_SubagentUseEntry`, `SdkTeammate.__init__`, `SdkTeammate.status_snapshot` override, `broker.py:get_teammate_status` `.get()` calls.

- **D2 (subagent_result emitted from _end_turn, not PostToolUse hook; TNMs stored on instance via callback).** SDK ordering spike confirmed non-deterministic: hooks are spawned as detached tasks (`query.py:232`), TNM is streamed immediately (`query.py:299`) — no guaranteed ordering between PostToolUse hook fire and TNM arrival in `receive_response()`. Emitting `subagent_result` from PostToolUse risks `summary: null` whenever hook fires before TNM. Fix: PostToolUse D3 branch moves the entry from `_subagent_uses` into `_closed_subagent_scratch` (records `finished_at_wallclock` and `hook_outcome`), then returns. `_end_turn` emits `subagent_result` JSONL for each entry in `_closed_subagent_scratch`, looking up TNMs from `_task_notifs_by_tool_use_id` — by `_end_turn`, `_collect_response_text` has fully drained, so all TNMs are guaranteed present. `_collect_response_text` receives `record_task_notif: Callable[[str, TNM], None]` callback (None-safe) for all TNM statuses; if `tnm.tool_use_id` is None, skip. Orphan TNMs (arrived with no matching Pre) land in the dict, are never popped, cleared at `_end_turn` — benign. `TurnDrainResult.last_failed_task_notif: TNM | None` → `failed_task_notifs: list[TNM]` (accumulates all failed/stopped) for `_handle_one_turn` synthesis. *Carried into:* `sdk_teammate.py:_collect_response_text` signature; `sdk_teammate.py:TurnDrainResult.failed_task_notifs`; `sdk_teammate.py:SdkTeammate._record_task_notif` method; `sdk_teammate.py:SdkTeammate._closed_subagent_scratch` dict; `sdk_teammate.py:SdkTeammate._end_turn` (emits JSONL, clears both dicts); `sdk_teammate.py:SdkTeammate._task_notifs_by_tool_use_id` (init in `__init__`, clear in `_end_turn`).

- **D3 (PreToolUse is the sole canonical spawn signal; TaskStartedMessage/TaskProgressMessage not consumed in v1).** PreToolUse fires at intent time, before subagent dispatches, and has synchronous access to teammate state. Two writers to `_subagent_uses` would split state-mutation surface and invite races. TaskStartedMessage would add timing-delta data (requested_at vs started_at) — route to BACKLOG, not v1. TaskProgressMessage is a firehose with no current reader — route to BACKLOG. *Carried into:* D3 comment update in `_on_pre_tool_use`, BACKLOG entries.

- **D4 (spawn tracking in the D3 branch of _on_pre_tool_use — emit-first, then store).** The existing early-return at `sdk_teammate.py:239` is replaced with: (1) emit `subagent_spawn` JSONL **first**; (2) if emit succeeds, store `_SubagentUseEntry` in `self._subagent_uses[tool_use_id]`; (3) `return {}`. **Emit-first ordering is required for SC-14:** if JSONL write fails and the exception is swallowed (SC-13), the entry is never added to `_subagent_uses` — PostToolUse sees no pre-entry, logs orphan-post warning, and no `subagent_result` is emitted. This preserves the SC-14 invariant (no result without a preceding spawn). Reversing the order (store-then-emit) would leave an orphaned dict entry that produces a `subagent_result` with no `subagent_spawn` — SC-14 violation (sentinel F2). Null `tool_use_id` guard: log WARNING + return early. Duplicate `tool_use_id` guard: log WARNING + last-write-wins. Exception isolation: outer try/except returns `{}` + logs WARNING (SC-13). *Carried into:* `sdk_teammate.py:_on_pre_tool_use:D3 branch`, `tests/test_sdk_teammate.py::test_subagent_stamps_activity_but_no_state` updated.

- **D5 (D3 branch of _on_post_common: move to _closed_subagent_scratch, not emit).** The existing early-return at `sdk_teammate.py:318` is replaced with: (1) dedup check — if `tool_use_id in self._recently_closed_subagent_use_ids`: log INFO + return `{}`; (2) pop from `self._subagent_uses.pop(tool_use_id, None)` — if None, log WARNING ("subagent post-without-pre") + return `{}`; (3) add `tool_use_id` to `_recently_closed_subagent_use_ids`; (4) store `_ClosedSubagentEntry(agent_id, tool_use_id, spawned_at_wallclock, finished_at_wallclock=time.time(), hook_outcome="ok"|"failed")` in `_closed_subagent_scratch[tool_use_id]`; (5) return `{}`. No JSONL emit here — deferred to `_end_turn` (D2). Exception isolation same as D4. `_end_turn` emit: for each entry in `_closed_subagent_scratch`, pop TNM from `_task_notifs_by_tool_use_id`; outcome = TNM status mapped to ok/failed if TNM present, else `hook_outcome`; `tnm_missing = (tnm is None)`; log WARNING if `tnm_missing`; emit `subagent_result` JSONL; update `_last_subagent_completed`. *Carried into:* `sdk_teammate.py:_on_post_common:D3 branch`; `sdk_teammate.py:SdkTeammate._end_turn` emit loop; `sdk_teammate.py:SdkTeammate._recently_closed_subagent_use_ids`.

- **D6 (_handle_one_turn synthesis update).** `result.last_failed_task_notif` → `result.failed_task_notifs[-1]` when non-empty (last failed in the list, preserving existing semantics). Import and isinstance check unchanged; only the field name and list access change. *Carried into:* `sdk_teammate.py:_handle_one_turn:590-601`.

- **D7 (abandoned cleanup: _close_open_subagents drains both in-flight dicts).** New method called from `_tombstone_teammate` after `_close_open_tools` (step 8c). Must drain **both** `_subagent_uses` (Pre fired, Post not yet) and `_closed_subagent_scratch` (Post fired, `_end_turn` not yet). The window where `_closed_subagent_scratch` has entries but `_subagent_uses` is empty is real: PostToolUse hook fires (entry moves to scratch), SDK continues streaming, teammate dies before `_end_turn` runs — without draining scratch, those entries vanish silently (sentinel F1). No-op condition: both dicts empty. Else: collect all entries from both dicts; emit one `subagent_abandoned_batch` JSONL with combined list; clear both. `reason` arg: `"death"` or `"kill"`. SC-14 holds: all `subagent_spawn` records were written at PreToolUse time, before death. *Carried into:* `SdkTeammate._close_open_subagents`, `broker.py:_tombstone_teammate` step 8c.

- **D8 (in_flight_subagents_at_death counts both dicts).** In `_tombstone_teammate` step 4 (alongside `last_tool_completed` snapshot), capture `in_flight_count = len(getattr(teammate, "_subagent_uses", {})) + len(getattr(teammate, "_closed_subagent_scratch", {}))`. Counts entries in both dicts — reflects true count of subagents not yet finalized at death (sentinel F1). Runs before `_close_open_subagents` clears both dicts. Stored in `TeammateInfo.in_flight_subagents_at_death`. *Carried into:* `broker.py:_tombstone_teammate:step4`, `broker.py:TeammateInfo`, `broker.py:get_teammate_status`.

- **D9 (last_subagent_completed preserved in tombstone — symmetry with F8).** F8 preserves `last_tool_completed` in the tombstone via `TeammateInfo.last_tool_completed_at_death`. F7 must be symmetric: `TeammateInfo.last_subagent_completed_at_death: dict | None = None`. Captured in `_tombstone_teammate` step 4 alongside `last_tool_completed_at_death` — call `teammate.status_snapshot()`, read `snap.get("last_subagent_completed")`. Dead path in `get_teammate_status` returns `info.last_subagent_completed_at_death`. *Carried into:* `broker.py:TeammateInfo.last_subagent_completed_at_death`; `broker.py:_tombstone_teammate` step 4 snapshot; `broker.py:get_teammate_status` dead path return dict.

- **D10 (SdkTeammate.status_snapshot override — current_subagents includes both in-flight dicts).** `SdkTeammate` overrides `status_snapshot()` as: `snap = super().status_snapshot(); snap["current_subagents"] = [...]; snap["last_subagent_completed"] = ...; snap["in_flight_subagents_at_death"] = None; return snap`. `current_subagents` is built from **both** `_subagent_uses.values()` AND `_closed_subagent_scratch.values()` — entries from both are projected to `{agent_id, tool_use_id, spawned_at_wallclock}`. This closes the limbo-state gap (sentinel D1 promoted to FIX-NOW): between PostToolUse firing (entry moves to scratch) and `_end_turn` running (result committed), the lead would otherwise see `current_subagents: []` and `last_subagent_completed: null` — a subagent that started but never existed. Including scratch entries makes the status contract honest for the full subagent lifecycle. The `None` for `in_flight_subagents_at_death` is always `None` on alive path — present for shape consistency with dead path. *Carried into:* `sdk_teammate.py:SdkTeammate.status_snapshot`; T1 BDD scenario updated.

### Edge Cases

- **Null `tool_use_id` at PreToolUse for subagent call:** log WARNING, return `{}`, no entry created. Same guard as main-agent path.
- **PostToolUse fires without matching PreToolUse (subagent orphan-post):** entry is None after pop → log WARNING, no `subagent_result` emitted, return `{}`. Mirror of main-agent orphan-post (which emits `tool_end(orphan_post)`). No JSONL record for subagent orphan — simpler, no false pairing.
- **TNM arrives after PostToolUse fires:** `self._task_notifs_by_tool_use_id.pop(tool_use_id, None)` returns None → `summary: null` in `subagent_result`. Honest, not fabricated.
- **TNM arrives but `tool_use_id` is None (SDK omits it):** `record_task_notif` callback receives `tool_use_id=None` → skip storing (can't correlate). `summary` remains null for that subagent. TNM still counted in `failed_task_notifs` if status is failed/stopped (synthesis path unaffected).
- **Parallel fan-out, two subagents fail:** Both TNMs stored in dict by `tool_use_id`. Each PostToolUse hook call pops its own. `failed_task_notifs` list has both. `_handle_one_turn` synthesis uses `failed_task_notifs[-1]` for the error envelope (last-failed, arbitrary but deterministic).
- **Subagent in flight at teammate death:** `_subagent_uses` non-empty → `subagent_abandoned_batch` emitted. Count captured in `in_flight_subagents_at_death`. SC-14 ordering: `subagent_spawn` records were written at PreToolUse hook time (before death), so `subagent_abandoned_batch` always follows them in the JSONL.
- **Exception in spawn tracking (write_tool_event fails):** outer try/except catches → WARNING logged, return `{}`. Teammate continues normally (SC-13). Entry may be in `_subagent_uses` even if JSONL write failed — PostToolUse will still close it cleanly.
- **Exception in result tracking:** same isolation. `_last_subagent_completed` may not update. Entry may remain in `_subagent_uses` — cleaned up at `_end_turn` if still present.
- **`_end_turn` clears `_task_notifs_by_tool_use_id`:** any TNMs that arrived after their PostToolUse (and thus weren't popped) are silently dropped. No state leak between turns.
- **StubTeammate calls `get_teammate_status`:** `snap.get("current_subagents", [])` returns `[]`; `snap.get("last_subagent_completed")` returns `None`. No crash.
- **Cross-turn subagent:** Task tool is synchronous from the parent model's perspective — it blocks until the subagent returns. PostToolUse fires before `_end_turn` is called. `_closed_subagent_scratch` should always be empty at turn start. `_end_turn` should WARN if `_subagent_uses` is still non-empty (subagent Pre fired but no Post — shouldn't happen for synchronous Task, but defensive).
- **PostToolUse fired, teammate dies before _end_turn (sentinel F1):** `_subagent_uses` is empty but `_closed_subagent_scratch` has entries. `_close_open_subagents` must drain both dicts and include scratch entries in the `subagent_abandoned_batch`. Without this, those entries vanish silently — no JSONL record, undercounted `in_flight_subagents_at_death`. D7 and D8 both updated to reflect this.
- **Separate caps and dedup namespaces:** `_subagent_uses` gets its own overflow cap (log WARNING if `len(_subagent_uses) >= MAX_CONCURRENT_TOOLS`, same constant reused). `_recently_closed_subagent_use_ids` is a separate `deque(maxlen=64)` from `_recently_closed_tool_use_ids` — different namespaces, no cross-contamination.
- **Orphan TNM (TNM arrives with no matching PreToolUse):** Lands in `_task_notifs_by_tool_use_id`, never popped by PostToolUse (no matching entry), cleared at `_end_turn`. Benign — one-line comment in D2 acknowledges this.
- **`subagent_abandoned_batch` consumer contract:** Spawn↔result records are NOT balanced under abandonment. `count(subagent_spawn) ≠ count(subagent_result)` is expected when a teammate dies mid-turn. Future analytics consumers must handle this. Documented in the JSONL schema comment.
- **TeammateInfo backward compatibility:** `in_flight_subagents_at_death` and `last_subagent_completed_at_death` use `= None` defaults — frozen dataclass `dataclasses.replace()` pattern unchanged. Old tombstone records (from F6/F8) lack these fields; callers use `.get()` already (broker internal access by field name, not dict). No migration needed.
- **Status key collision:** Confirmed no collision: `current_subagents`, `last_subagent_completed`, `in_flight_subagents_at_death` are new keys not present in any existing `get_teammate_status` consumer.

### Assumptions

- **A1 — `tool_use_id` is present in SDK subagent TNMs in practice.** The field is `str | None` in the type definition. We assume it's populated for Task invocations. If None: summary is null and correlation fails silently. Acceptable for v1 — the JSONL record is still emitted, just without summary.
- **A2 — PostToolUse fires for subagent completions (not just PostToolUseFailure for failures).** F8 established this; unchanged.
- **A3 — Subagent `agent_id` is stable across PreToolUse and PostToolUse for the same invocation.** If not, the D3 branch detection breaks — but F8 relied on the same assumption.
- **A4 — `status_snapshot()` is called only from the asyncio event loop thread.** No locking on `_subagent_uses`. Same single-writer assumption as `_tool_uses`.

### Open Questions

All questions resolved. No blockers to Phase 3.

---

## Phase 3: Task Breakdown

### Pre-T2 contract-change grep (sentinel F4 — completed pre-Phase-3)

`TurnDrainResult.last_failed_task_notif` rename → `failed_task_notifs: list[TNM]`. All consumers confirmed:

| File | Line | Usage |
|---|---|---|
| `claude_crew/sdk_teammate.py` | 103 | Docstring field description |
| `claude_crew/sdk_teammate.py` | 110 | Dataclass field declaration |
| `claude_crew/sdk_teammate.py` | 126 | `_collect_response_text` return doc |
| `claude_crew/sdk_teammate.py` | 160 | Return statement in `_collect_response_text` |
| `claude_crew/sdk_teammate.py` | 620 | Condition in `_handle_one_turn` synthesis |
| `claude_crew/sdk_teammate.py` | 621 | Field read in `_handle_one_turn` synthesis |
| `tests/test_sdk_teammate.py` | 290 | Comment reference only |

T2 builder updates all 7 locations. No hidden consumers in other files.

### Dependency graph
```
T1 (new state/dataclasses) ──┬──> T3 (hooks + _end_turn emit)
T2 (stream + TurnDrainResult) ─┘
                                     T3 ──> T4 (broker/transcript)
                                     T4 ──> T5 (E2E tests)
T1 ──> T4 (TeammateInfo fields, tombstone snapshot)
```
T1 and T2 are independent and can run in parallel.

---

### Task 1: New subagent state + dataclasses in sdk_teammate.py
**Depends on**: None | **Blocks**: T3, T4

Pure data structure work — no behavior changes yet. Establishes the subagent namespace and makes the codebase compile cleanly with new types present.

**Files**: `claude_crew/sdk_teammate.py`

**Implementation**:
- Add `_SubagentUseEntry` frozen dataclass alongside `_ToolUseEntry`: fields `agent_id: str`, `tool_use_id: str`, `spawned_at_wallclock: float`.
- Add `_ClosedSubagentEntry` frozen dataclass: fields `agent_id: str`, `tool_use_id: str`, `spawned_at_wallclock: float`, `finished_at_wallclock: float`, `hook_outcome: str`.
- Add to `SdkTeammate.__init__` (after existing F8 tool-tracking fields):
  ```python
  self._subagent_uses: dict[str, _SubagentUseEntry] = {}
  self._closed_subagent_scratch: dict[str, _ClosedSubagentEntry] = {}
  self._recently_closed_subagent_use_ids: collections.deque[str] = collections.deque(maxlen=64)
  self._last_subagent_completed: dict[str, Any] | None = None
  self._task_notifs_by_tool_use_id: dict[str, TaskNotificationMessage] = {}
  ```
- Add `_record_task_notif(self, tool_use_id: str, tnm: TaskNotificationMessage) -> None` method: stores into `self._task_notifs_by_tool_use_id[tool_use_id]`.
- Override `status_snapshot(self) -> dict[str, Any]` in `SdkTeammate`: call `snap = super().status_snapshot()`, build `current_subagents` list from `self._subagent_uses.values()` sorted by `spawned_at_wallclock`, merge fields (`current_subagents`, `last_subagent_completed`, `in_flight_subagents_at_death: None`), return.

**Acceptance Criteria (BDD)**:

```
Scenario: SdkTeammate initializes subagent namespace fields
  Given a freshly created SdkTeammate (not yet started)
  Then _subagent_uses is an empty dict
  And _closed_subagent_scratch is an empty dict
  And _recently_closed_subagent_use_ids is an empty deque with maxlen=64
  And _last_subagent_completed is None
  And _task_notifs_by_tool_use_id is an empty dict

Scenario: status_snapshot includes subagent fields
  Given a started SdkTeammate with no subagents in flight
  When status_snapshot() is called
  Then the result contains current_subagents == []
  And last_subagent_completed is None
  And in_flight_subagents_at_death is None
  And all existing F6/F8 fields are present and unchanged

Scenario: status_snapshot reflects in-flight subagents
  Given a started SdkTeammate
  And _subagent_uses contains two _SubagentUseEntry records
  When status_snapshot() is called
  Then current_subagents has two entries
  And each entry contains {agent_id, tool_use_id, spawned_at_wallclock}
  And _tool_uses and current_tools are unaffected

Scenario: status_snapshot includes scratch entries — no limbo gap (sentinel D1 fix)
  Given _subagent_uses is empty
  And _closed_subagent_scratch has one entry (PostToolUse fired, _end_turn not yet)
  When status_snapshot() is called
  Then current_subagents has one entry with {agent_id, tool_use_id, spawned_at_wallclock}
  And last_subagent_completed is still null (result not yet committed)

Scenario: _record_task_notif stores by tool_use_id
  Given a started SdkTeammate
  When _record_task_notif("tu-1", <TNM with tool_use_id="tu-1">) is called
  Then _task_notifs_by_tool_use_id["tu-1"] is that TNM
```

**Verification**: `cd /home/jerome/dev/claude-crew && uv run pytest tests/test_sdk_teammate.py tests/test_teammate.py -v -k "subagent"` — new tests pass; existing tests untouched.

---

### Task 2: _collect_response_text + TurnDrainResult + synthesis update
**Depends on**: None | **Blocks**: T3

Changes the stream draining function to accumulate all TNMs via callback and updates the synthesis logic in `_handle_one_turn`.

**Files**: `claude_crew/sdk_teammate.py`

**Implementation**:
- Change `TurnDrainResult`: `last_failed_task_notif: TaskNotificationMessage | None` → `failed_task_notifs: list[TaskNotificationMessage]`.
- Update `_collect_response_text` signature: add `record_task_notif: Callable[[str, TaskNotificationMessage], None] | None = None`. In the `isinstance(msg, TaskNotificationMessage)` block: call `record_task_notif(msg.tool_use_id, msg)` if callback is not None AND `msg.tool_use_id is not None`; accumulate to `failed_task_notifs` list if `msg.status in ("failed", "stopped")` (keep existing WARNING log). Change `return TurnDrainResult(text=..., last_failed_task_notif=last_failed)` → `TurnDrainResult(text=..., failed_task_notifs=failed_list)`.
- Update `_handle_one_turn` call site: pass `record_task_notif=self._record_task_notif`.
- Update `_handle_one_turn` synthesis (line ~595): `if result.last_failed_task_notif is not None` → `if result.failed_task_notifs`.  Access `result.failed_task_notifs[-1]` for the summary.

**Acceptance Criteria (BDD)**:

```
Scenario: _collect_response_text accumulates failed TNMs into list
  Given a fake client that yields two TaskNotificationMessages (failed, stopped) and one text block
  When _collect_response_text is called with no callback
  Then result.failed_task_notifs has two entries
  And result.text is the text block content

Scenario: record_task_notif callback fires for all TNM statuses
  Given a fake client that yields TNMs with statuses: completed, failed, stopped
  And a callback that records (tool_use_id, tnm) pairs
  When _collect_response_text is called with that callback
  Then callback was called three times (completed, failed, stopped)
  And failed_task_notifs has two entries (failed, stopped only)

Scenario: record_task_notif skips TNMs with null tool_use_id
  Given a TNM with tool_use_id=None
  When _collect_response_text fires the callback
  Then callback is NOT called for that TNM

Scenario: _handle_one_turn synthesis uses failed_task_notifs
  Given a turn that produces empty text and one failed TNM
  When _handle_one_turn completes
  Then the lead receives an invalid_response envelope with the TNM's summary
```

**Verification**: `uv run pytest tests/test_sdk_teammate.py -v -k "collect_response or turn_drain or synthesis"` — updated and new tests pass.

---

### Task 3: Hook D3 branch extensions + _end_turn emit + _close_open_subagents
**Depends on**: T1, T2 | **Blocks**: T4, T5

Core behavioral change. Extends the D3 branches to track subagent spawns/results, adds the `_end_turn` JSONL emit loop, and adds `_close_open_subagents` for death cleanup.

**Files**: `claude_crew/sdk_teammate.py`, `claude_crew/transcript.py`

**Implementation**:
- `transcript.py`: extend `write_tool_event` Literal to include `"subagent_spawn"`, `"subagent_result"`, `"subagent_abandoned_batch"`.
- `_on_pre_tool_use` D3 branch (sdk_teammate.py:239): replace `return {}` with: null `tool_use_id` guard (WARNING + return); duplicate guard (WARNING + last-write-wins); cap guard (WARNING); create `_SubagentUseEntry`; store in `self._subagent_uses[tool_use_id]`; emit `subagent_spawn` via `broker._sink.write_tool_event("subagent_spawn", {...})`.
- `_on_post_common` D3 branch (sdk_teammate.py:318): replace `return {}` with: dedup check (`_recently_closed_subagent_use_ids`); pop from `_subagent_uses` (orphan-post WARNING if None); add to `_recently_closed_subagent_use_ids`; store `_ClosedSubagentEntry` in `_closed_subagent_scratch`.
- `_end_turn`: after existing `_last_tool_completed` / `_tool_uses` clearing, add emit loop: for each entry in `_closed_subagent_scratch`, pop TNM from `_task_notifs_by_tool_use_id`; compute outcome (TNM status mapped to ok/failed, or `hook_outcome` if TNM missing); set `tnm_missing`; log WARNING if `tnm_missing`; emit `subagent_result` JSONL; update `self._last_subagent_completed`. Clear both `_closed_subagent_scratch` and `_task_notifs_by_tool_use_id` in the `finally` block (always cleared even on error paths — co-architect confirmation).
- Add `_close_open_subagents(self, reason: Literal["death", "kill"]) -> None`: if `_subagent_uses` empty, no-op; else emit `subagent_abandoned_batch` JSONL with snapshot of in-flight list and `reason`; clear `_subagent_uses`.
- Update existing D3 tests (`test_subagent_stamps_activity_but_no_state`, `test_subagent_bash_does_not_emit_transcript_line`) to assert new behavior.

**Acceptance Criteria (BDD)**:

```
Scenario: PreToolUse subagent fires — spawn record written and state updated (SC-1, SC-2)
  Given a started SdkTeammate with broker and transcript sink
  When _on_pre_tool_use fires with agent_id="sub-1", tool_use_id="tu-1"
  Then _subagent_uses["tu-1"] is a _SubagentUseEntry with agent_id="sub-1"
  And transcript contains a subagent_spawn record with those fields
  And _tool_uses is empty (SC-12: F8 namespace untouched)
  And current_tools in status_snapshot() is []

Scenario: PostToolUse subagent fires — moves to scratch, no JSONL yet
  Given a started SdkTeammate with "tu-1" in _subagent_uses
  When _on_post_tool_use fires with agent_id="sub-1", tool_use_id="tu-1"
  Then _subagent_uses is empty
  And _closed_subagent_scratch["tu-1"] exists with hook_outcome="ok"
  And no subagent_result JSONL record has been written yet

Scenario: _end_turn emits subagent_result with TNM summary (SC-3)
  Given _closed_subagent_scratch has one entry for "tu-1"
  And _task_notifs_by_tool_use_id["tu-1"] is a completed TNM with summary="Done."
  When _end_turn() is called
  Then transcript contains a subagent_result record with outcome="ok", summary="Done.", tnm_missing=false
  And _last_subagent_completed reflects that result
  And both _closed_subagent_scratch and _task_notifs_by_tool_use_id are cleared

Scenario: _end_turn emits with tnm_missing=true on error path (co-architect)
  Given _closed_subagent_scratch has one entry (PostToolUse fired)
  And _task_notifs_by_tool_use_id is empty (stream was interrupted)
  When _end_turn() is called
  Then transcript contains subagent_result with tnm_missing=true and outcome from hook_outcome
  And a WARNING is logged

Scenario: F8 invariants preserved — subagent events don't touch tool namespace (SC-12)
  Given a started SdkTeammate
  When subagent PreToolUse + PostToolUse fire
  Then _tool_uses is unchanged
  And current_tools in status_snapshot() is []
  And last_tool_completed is unchanged
  And _recently_closed_tool_use_ids is unchanged

Scenario: parallel fan-out — two subagents tracked independently (SC-11)
  Given a started SdkTeammate
  When PreToolUse fires for "tu-1" (agent_id="sub-1") and "tu-2" (agent_id="sub-2")
  Then _subagent_uses has two entries, keyed separately
  And status_snapshot().current_subagents has two entries

Scenario: hook exception isolation — teammate continues (SC-13)
  Given _on_pre_tool_use D3 branch raises an exception mid-tracking
  Then the hook returns {} without crashing
  And the teammate processes the next message normally

Scenario: _close_open_subagents emits abandoned batch on death (SC-8)
  Given _subagent_uses has two in-flight entries
  When _close_open_subagents(reason="death") is called
  Then transcript contains a subagent_abandoned_batch record with both entries and reason="death"
  And _subagent_uses is cleared

Scenario: _close_open_subagents drains _closed_subagent_scratch too (sentinel F1)
  Given _subagent_uses is empty
  And _closed_subagent_scratch has one entry (PostToolUse fired before death)
  When _close_open_subagents(reason="death") is called
  Then transcript contains subagent_abandoned_batch with that entry
  And _closed_subagent_scratch is cleared

Scenario: _end_turn emit exception — dicts cleared, teammate continues (sentinel F3, SC-13)
  Given _closed_subagent_scratch has one entry
  And the transcript sink raises on write_tool_event
  When _end_turn() is called
  Then a WARNING is logged
  And _closed_subagent_scratch is empty (cleared in finally block)
  And _task_notifs_by_tool_use_id is empty
  And the teammate processes its next turn normally
```

**Verification**: `uv run pytest tests/test_sdk_teammate.py tests/test_e2e_tool_telemetry.py -v` — updated D3 tests pass; new scenarios pass; all 295 pre-existing tests pass.

---

### Task 4: Broker, TeammateInfo, get_teammate_status
**Depends on**: T1, T3 | **Blocks**: T5

Wires `_close_open_subagents` into the tombstone path and surfaces subagent fields in `get_teammate_status`.

**Files**: `claude_crew/broker.py`

**Implementation**:
- `TeammateInfo`: add `in_flight_subagents_at_death: int | None = None` and `last_subagent_completed_at_death: dict[str, Any] | None = None` (additive defaults).
- `_tombstone_teammate` step 4 snapshot: alongside `last_tool_completed_at_death = snap.get("last_tool_completed")`, also capture `last_subagent_completed_at_death = snap.get("last_subagent_completed")` and `in_flight_subagents_at_death = len(getattr(teammate, "_subagent_uses", {}))`.
- `_tombstone_teammate` step 5 (`dataclasses.replace`): include new fields.
- `_tombstone_teammate` step 8c: after `teammate._close_open_tools(reason=close_reason)`, add `teammate._close_open_subagents(reason=close_reason)`.
- `get_teammate_status` alive path: add `"current_subagents": snap.get("current_subagents", [])`, `"last_subagent_completed": snap.get("last_subagent_completed")`, `"in_flight_subagents_at_death": None`.
- `get_teammate_status` dead path: add `"current_subagents": []`, `"last_subagent_completed": info.last_subagent_completed_at_death`, `"in_flight_subagents_at_death": info.in_flight_subagents_at_death`.

**Acceptance Criteria (BDD)**:

```
Scenario: get_teammate_status alive path includes subagent fields (SC-4, SC-5)
  Given an alive SdkTeammate with no subagents in flight
  When get_teammate_status is called
  Then result contains current_subagents == []
  And last_subagent_completed is None
  And in_flight_subagents_at_death is None
  And all existing F6/F8 fields are present

Scenario: get_teammate_status reflects in-flight subagents (SC-4)
  Given an alive SdkTeammate with one subagent in _subagent_uses
  When get_teammate_status is called
  Then current_subagents has one entry with {agent_id, tool_use_id, spawned_at_wallclock}

Scenario: tombstone captures subagent count at death (SC-9)
  Given an alive SdkTeammate with two subagents in _subagent_uses
  When the teammate is killed
  Then get_teammate_status on the dead teammate returns in_flight_subagents_at_death == 2

Scenario: tombstone preserves last_subagent_completed (D9 flip — symmetry with F8)
  Given a teammate that completed one subagent then was killed
  When get_teammate_status is called post-death
  Then last_subagent_completed matches the last completed subagent record

Scenario: _close_open_subagents called on death (SC-8)
  Given a teammate with one in-flight subagent when killed
  When kill_teammate fires
  Then transcript contains subagent_abandoned_batch with that subagent
  And get_teammate_status dead path returns in_flight_subagents_at_death == 1
```

**Verification**: `uv run pytest tests/test_broker.py -v` — new and updated tests pass; all existing broker tests pass.

---

### Task 5: E2E integration tests
**Depends on**: T1–T4 | **Blocks**: None

Full-lifecycle tests through the real broker, real transcript sink, and real hook callbacks via `ProgrammableSDKClient`.

**Files**: `tests/test_e2e_subagent_telemetry.py` (new file)

**Happy Path Scenarios**:

```
Scenario: Single subagent lifecycle — spawn to result (SC-1 through SC-7)
  Given a started SdkTeammate with a scripted response that includes one Task tool call
  When PreToolUse fires (agent_id present), TNM arrives, PostToolUse fires, _end_turn runs
  Then transcript contains subagent_spawn followed by subagent_result for same tool_use_id
  And subagent_result.outcome == "ok", summary from TNM, tnm_missing == false
  And get_teammate_status during execution shows current_subagents with one entry
  And get_teammate_status after turn shows current_subagents == [], last_subagent_completed set

Scenario: Parallel fan-out — two concurrent subagents (SC-11)
  Given PreToolUse fires for tool_use_id "tu-1" and "tu-2" (two subagents)
  And TNMs arrive for both (via record_task_notif)
  And PostToolUse fires for both
  When _end_turn runs
  Then transcript contains two subagent_result records with correct tool_use_ids
  And last_subagent_completed is one of the two (last closed)
```

**Sad Path Scenarios**:

```
Scenario: Subagent PreToolUse fires, teammate killed before PostToolUse (SC-8, SC-9, SC-14)
  Given a started SdkTeammate with one in-flight subagent
  When kill_teammate is called
  Then transcript contains subagent_abandoned_batch (after subagent_spawn, never subagent_result)
  And get_teammate_status dead path: in_flight_subagents_at_death == 1

Scenario: PostToolUse fired, teammate killed before _end_turn (sentinel F1)
  Given PreToolUse and PostToolUse have both fired for a subagent (entry in _closed_subagent_scratch)
  When kill_teammate is called before _end_turn runs
  Then transcript contains subagent_abandoned_batch containing that entry
  And in_flight_subagents_at_death == 1 (counts scratch entries)

Scenario: Error-path emit — stream interrupted after PostToolUse fired (co-architect)
  Given PostToolUse fires for a subagent (entry moves to _closed_subagent_scratch)
  And _collect_response_text is then interrupted (backstop or exception)
  When _end_turn runs in the finally block
  Then transcript contains subagent_result with tnm_missing=true
  And _closed_subagent_scratch is cleared

Scenario: F8 regression — non-subagent tool calls unaffected (SC-10, SC-12)
  Given a turn with one normal Bash call and one subagent Task call
  When the turn completes
  Then transcript contains tool_start/tool_end for Bash and subagent_spawn/subagent_result for Task
  And current_tools reflects only Bash (not Task)
  And last_tool_completed reflects Bash (not Task)
```

**Verification**: `uv run pytest tests/test_e2e_subagent_telemetry.py tests/test_e2e_tool_telemetry.py -v` — all E2E scenarios pass; existing tool telemetry E2E unchanged.

---

**Gate**:
- ✅ 5 tasks, each independently testable
- ✅ Dedicated E2E test task (T5) with happy and sad path coverage
- ✅ All Phase 1 SCs trace to at least one BDD scenario
- ✅ Verification commands fail without the feature
- ✅ User approved

---

## Phase 4: Implementation

*Execution driven by SKILL.md. Update status in header as tasks complete.*

---

## Phase 5: Completion

### Verification
- [ ] Feature works against Phase 1 success criteria
- [ ] No regressions — full test suite passes
- [ ] Spec updated to match implementation
- [ ] PRODUCT-VISION.md updated

### Retrospective

*To be filled after completion.*
