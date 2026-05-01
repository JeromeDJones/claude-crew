# Feature: Token/Cost Telemetry (#14)

**Status**: In Progress (Phase 1)
**Created**: 2026-04-30

---

## Phase 1: Research & Requirements

### Problem Statement

The Mission Control dashboard has cost and token columns wired up visually but every value reads `$0.000` and `0 / 0`. The data pipeline is missing: `SdkTeammate` never extracts usage metadata from the response stream, `status_snapshot()` never returns token or cost fields, and `UIServer._build_local_instance()` hardcodes both to zero.

This is the last visible SC #4 gap — live observability across all crews. Cost is the primary operational metric operators use to decide when to kill a runaway teammate, whether a team configuration is economically viable, and how much a specific real-task run cost. Without real numbers in the dashboard, that decision-making is blind.

The SDK already provides usage data. `AssistantMessage.usage` carries per-turn token counts; `ResultMessage.total_cost_usd` carries the session aggregate cost. The feature is a data-pipeline completion, not new infrastructure.

### Success Criteria

- [ ] **SC-1**: `status_snapshot()` on a live `SdkTeammate` returns three new fields with initial state `(total_input_tokens=0, total_output_tokens=0, total_cost_usd=0.0)` immediately after spawn. Snapshot reads are atomic across the three fields — a reader never sees tokens from turn N and cost from turn N-1. The three fields are sourced from `ResultMessage` at end-of-turn (per OQ-3 resolution): values are **overwritten**, not accumulated, since ResultMessage carries session-cumulative totals. Cache tokens (`cache_read_input_tokens`, `cache_creation_input_tokens`) are included in `total_input_tokens` if present in `ResultMessage.usage` (billed context is billed context). Exactly one ResultMessage is consumed per completed turn (guaranteed by SDK iterator contract — `receive_response()` terminates at ResultMessage).
- [ ] **SC-2**: The snapshot reflects all turns fully completed before the call. The polling interval that drives dashboard updates is configured in `UIServer._POLL_INTERVAL`; the SC does not pin a wall-clock latency bound (that is a UIServer configuration concern). Cost appears in the dashboard as of the next push cycle after a turn completes.
- [ ] **SC-3**: The dashboard instance summary row shows aggregate token counts and cost across **all teammates — alive and tombstoned**. The aggregate answers "what has this session cost me," not "what are the current live agents running." Aggregate reads are consistent snapshots; no torn reads between alive and dead sources. Tombstoned totals are included while the tombstone is present in the broker.
- [ ] **SC-4**: When a teammate is tombstoned, the **last ResultMessage values observed** before tombstoning are preserved in `TeammateInfo` (new fields: `total_input_tokens_at_death`, `total_output_tokens_at_death`, `total_cost_usd_at_death`). `get_teammate_status()` on a dead teammate returns these values. Mid-turn death naturally preserves the previous turn's cumulative totals — the next overwrite simply never happens (consistent with SC-1's overwrite semantics; partial in-flight turn data is not recovered, which is accepted behavior).
- [ ] **SC-5**: Accumulation is session-scoped — it resets to zero on teammate respawn. Lifetime aggregation across multiple spawns of the same role is explicitly out of scope for this feature.
- [ ] **SC-6**: If the SDK returns null, absent, or malformed usage data for a turn (wrong types, missing keys, unexpected structure), the teammate's accumulated totals are unchanged — no exception, no zeroing, no partial update. Failures are logged at WARNING level. Tokens and cost remain at their last valid values.
- [ ] **SC-7**: `StubTeammate` `status_snapshot()` returns all three new fields present with zero values (`total_input_tokens: 0`, `total_output_tokens: 0`, `total_cost_usd: 0.0`) — fields must be present, not just absent or defaulted.
- [ ] **SC-8**: Cost source: `ResultMessage.total_cost_usd` from the per-turn drain (OQ-3 resolved). The teammate stores the latest value as `total_cost_usd` (overwrite semantics). No model-rate table is maintained. If `total_cost_usd` is `None` on a given ResultMessage, the stored value is unchanged (SC-6 graceful degradation applies).
- [ ] **SC-9**: Type and precision contract: `total_input_tokens` and `total_output_tokens` are `int`. `total_cost_usd` is `float` with no rounding at accumulation (rounding only at display). JSON serialization does not introduce scientific notation for small values.
- [ ] **SC-10**: The new fields are additive — no existing `status_snapshot()` key is renamed or removed. All existing consumers of `status_snapshot()` (broker `get_teammate_status()`, `UIServer._build_local_instance()`, test assertions) continue to work without changes.

### Questions

- [x] **OQ-1**: What SDK message type carries per-turn usage? **Resolved**: `AssistantMessage.usage: dict[str, Any] | None` — emitted in the per-turn drain loop. Standard Anthropic keys: `input_tokens`, `output_tokens`. May also include `cache_read_input_tokens`, `cache_creation_input_tokens` for prompt-cached turns.
- [x] **OQ-2**: Does `ResultMessage.total_cost_usd` appear per-turn or only at session end? **Resolved by OQ-3 spike**: per-turn drain. `client.receive_response()` terminates at ResultMessage (one per turn), and the value carried is the session-cumulative running total. So we get an updated cumulative total at the end of every turn.
- [x] **OQ-3**: Should cost be SDK-provided or locally derived? **Resolved**: SDK-provided. Spike of SDK source (claude_agent_sdk/client.py:566-605, types.py:1069-1087, _internal/message_parser.py:222-244) confirms: `ResultMessage` is emitted exactly once per `receive_response()` drain (the iterator terminates at it), and `total_cost_usd` / `num_turns` / `usage` carry **session-cumulative** values (the CLI maintains session state across `query()` calls). Strategy: overwrite the teammate's stored cost/tokens with the latest ResultMessage values at end-of-turn — no per-turn delta math, no rate table, no double-counting risk. This also resolves OQ-2 (cadence: per-turn-drain, value: cumulative).

### Constraints & Dependencies

- **Requires**: `claude_crew/sdk_teammate.py` (F6 telemetry substrate, F7 subagent hooks, F8 tool hooks — all shipped)
- **Requires**: `claude_crew/teammate.py` base class (status_snapshot, _begin_turn/_end_turn lifecycle)
- **Requires**: `claude_crew/broker.py` (get_teammate_status tombstone path — must forward new fields)
- **Requires**: `claude_crew/ui_server.py` (hardcoded zeros replaced with snap.get() reads)
- **Breaking changes**: No — additive fields only. Existing `status_snapshot()` consumers get new keys in the dict; no removed or renamed keys.
- **Stub behavior**: StubTeammate must return zeros for new fields — already the natural behavior (no SDK calls, no accumulation).
- **SDK-version coupling**: Relies on `AssistantMessage.usage` existing in the installed claude_agent_sdk version. If absent, feature degrades to zeros gracefully (SC-6).
- **Cost accuracy**: If cost is locally derived, the model-rate table must be maintained when prices change. If SDK-provided, no maintenance needed. Resolution depends on OQ-2/OQ-3.
- **Performance**: `status_snapshot()` is called periodically per alive teammate by the UIServer WebSocket loop (current default `_POLL_INTERVAL`, not contractual). New fields are simple integer/float reads — negligible cost.
- **No new dependencies**: stdlib only for accumulation; existing SDK types (`AssistantMessage`, `ResultMessage`) already imported or available.

**Gate**: Questions answered, success criteria measurable, constraints documented, user confirmed.

---

## Phase 2: Design & Specification

### Architecture Overview

The feature is a five-touchpoint data pipeline completion. No new modules, no new processes. Each touchpoint is mechanical and named; the data flow is one-way.

```
SDK CLI ──ResultMessage──▶ _collect_response_text  (sdk_teammate.py)
                              │
                              ▼  TurnDrainResult (extended with token/cost values)
                          _handle_one_turn  (sdk_teammate.py)
                              │  overwrite three instance fields
                              ▼
                  SdkTeammate._total_input_tokens
                  SdkTeammate._total_output_tokens
                  SdkTeammate._total_cost_usd
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
   status_snapshot()    _tombstone_teammate    (death path)
   (live read)               .get() x3 → TeammateInfo._at_death fields
                                  │
              ┌───────────────────┴────────────────────┐
              ▼                                         ▼
       UIServer._build_local_instance              broker.get_teammate_status
       (alive: snap.get; tombstones: at_death)     (alive: snap.get; dead: at_death)
              │
              ▼
       agent dict {cost, tokens}    +    instance summary {cost, tokens}
       (alive only)                      (alive snap + tombstone _at_death)
```

The single source of truth for cost/tokens is `ResultMessage`, captured per-turn-drain. Every downstream consumer reads from one of three places: (a) the live `SdkTeammate` instance fields via `status_snapshot()`, (b) the `TeammateInfo._at_death` fields via the broker tombstone path, or (c) the UIServer instance-summary aggregate that combines (a) and (b).

### Data / API Contracts

#### `claude_crew/sdk_teammate.py`

```python
# imports — add ResultMessage to existing claude_agent_sdk.types import
from claude_agent_sdk.types import (
    AssistantMessage,
    HookMatcher,
    RateLimitEvent,
    ResultMessage,           # NEW
    TaskNotificationMessage,
    TextBlock,
)

# TurnDrainResult — extend
@dataclass(frozen=True)
class TurnDrainResult:
    text: str
    failed_task_notifs: list[TaskNotificationMessage]
    # NEW — overwrite values from terminal ResultMessage; None if no
    # ResultMessage was observed during this drain (interrupted, etc.)
    cumulative_input_tokens: int | None = None
    cumulative_output_tokens: int | None = None
    cumulative_cost_usd: float | None = None

# SdkTeammate — new instance fields (initialized in __init__, alongside _tool_uses)
self._total_input_tokens: int = 0
self._total_output_tokens: int = 0
self._total_cost_usd: float = 0.0

# SdkTeammate.status_snapshot() — extend the dict returned (after super().status_snapshot())
snap["total_input_tokens"] = self._total_input_tokens
snap["total_output_tokens"] = self._total_output_tokens
snap["total_cost_usd"] = self._total_cost_usd
```

#### `claude_crew/teammate.py`

```python
# Teammate.status_snapshot() base implementation — add three keys with zero values
# so StubTeammate inherits the additive contract for SC-7 with no override.
snap["total_input_tokens"] = 0
snap["total_output_tokens"] = 0
snap["total_cost_usd"] = 0.0
```

#### `claude_crew/broker.py`

```python
@dataclass(frozen=True)
class TeammateInfo:
    # ... existing 12 fields ...
    # NEW death-record fields (None for alive teammates)
    total_input_tokens_at_death: int | None = None
    total_output_tokens_at_death: int | None = None
    total_cost_usd_at_death: float | None = None
```

`_tombstone_teammate` — extract three new keys from snap inside the existing try/except, append to the `dataclasses.replace(...)` call. Defaults: `0`, `0`, `0.0` (not `None`) when snap is absent — see Decision D-7.

`get_teammate_status` — both alive and dead branches return three new keys at the top level of the response dict. Alive branch reads from the live snap; dead branch reads from `TeammateInfo._at_death` fields.

#### `claude_crew/ui_server.py`

```python
# Per-agent dict (alive only) — replace hardcoded zeros at lines 136-137
"cost": float(snap.get("total_cost_usd", 0.0)),
"tokens": {
    "in": int(snap.get("total_input_tokens", 0)),
    "out": int(snap.get("total_output_tokens", 0)),
},

# Instance summary — replace hardcoded zeros at lines 172-173 with an aggregate
# that includes tombstoned teammates (SC-3: "what has this session cost me").
# Aggregate is computed in _build_local_instance, NOT in the frontend.
total_cost = 0.0
total_in = 0
total_out = 0
for info in broker._info.values():
    if info.alive:
        snap = info.teammate.status_snapshot()
        total_cost += float(snap.get("total_cost_usd", 0.0))
        total_in += int(snap.get("total_input_tokens", 0))
        total_out += int(snap.get("total_output_tokens", 0))
    else:
        total_cost += float(info.total_cost_usd_at_death or 0.0)
        total_in += int(info.total_input_tokens_at_death or 0)
        total_out += int(info.total_output_tokens_at_death or 0)
# instance dict
"cost": total_cost,
"tokens": {"in": total_in, "out": total_out},
```

The agents array remains alive-only (matches existing UI contract). Tombstones contribute only to the instance-summary aggregate. Snapshot is taken once per teammate per `_build_local_instance` call to avoid double-reads (SC-3 atomicity at the instance level).

#### `tests/fakes/sdk.py`

```python
def text_response_with_usage(
    text: str,
    *,
    cumulative_input_tokens: int,
    cumulative_output_tokens: int,
    cumulative_cost_usd: float,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> list[Any]:
    """text_response variant that scripts ResultMessage with cumulative usage/cost.

    Tokens passed are the cumulative session totals as ResultMessage would
    carry them. Cache tokens are added INTO ResultMessage.usage, not separately
    summed by the caller — the SdkTeammate's extraction logic decides whether
    to include them in total_input_tokens (per SC-1: yes, billed context is
    billed context).
    """
    usage = {
        "input_tokens": cumulative_input_tokens - cache_read_input_tokens - cache_creation_input_tokens,
        "output_tokens": cumulative_output_tokens,
    }
    if cache_read_input_tokens:
        usage["cache_read_input_tokens"] = cache_read_input_tokens
    if cache_creation_input_tokens:
        usage["cache_creation_input_tokens"] = cache_creation_input_tokens
    return [
        AssistantMessage(content=[TextBlock(text=text)], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=0, duration_api_ms=0,
            is_error=False, num_turns=1, session_id="fake",
            total_cost_usd=cumulative_cost_usd, usage=usage,
        ),
    ]
```

### Design Decisions

- **D-1: ResultMessage is the single source for cost AND tokens.** — *Rationale:* OQ-3 spike confirmed ResultMessage carries cumulative `total_cost_usd` and `usage` per-turn-drain. Using one source eliminates cross-source skew (e.g., AssistantMessage usage diverging from ResultMessage usage). Also dodges OQ-1's multi-AssistantMessage overcounting risk entirely. — *Carried into:* `_collect_response_text` only branches on `ResultMessage` for token/cost extraction; never on `AssistantMessage.usage`. Test: `test_token_cost_overwrite_from_result_message_only`.

- **D-2: Overwrite, not accumulate.** — *Rationale:* ResultMessage values are session-cumulative. Accumulating would double-count exponentially across turns. — *Carried into:* `_handle_one_turn` uses `=` not `+=` when applying TurnDrainResult values. Test: `test_three_turns_show_final_cumulative_not_sum`.

- **D-3: Cache tokens included in `total_input_tokens`.** — *Rationale:* SC-1 — billed context is billed context; users care about "what did I pay for," not "what did the cache miss." Excluding cache tokens makes the input count look artificially low for cached sessions. — *Carried into:* `_collect_response_text` extraction sums `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` from `ResultMessage.usage`. Test: `test_cache_tokens_summed_into_total_input`.

- **D-4: Single-threaded mutation, no lock; atomicity via co-assignment within a single ResultMessage.** — *Rationale:* `SdkTeammate._run()` is a single asyncio task — all mutations happen there. The three fields are assigned together in one synchronous block in `_handle_one_turn` (no `await` between assignments). Reader (`status_snapshot`) cannot interleave mid-assignment because it's also synchronous Python. **Scope of atomicity:** within a single ResultMessage, the three values either all update or all don't — the reader can't see a torn state from the assignment block itself. **Caveat:** D-8 explicitly overrides this when source fields are individually malformed: a ResultMessage with valid `total_cost_usd` but malformed `usage` will update cost while leaving tokens unchanged, which is per-field independence (the design choice) rather than a torn read (an accident). The "never see tokens from turn N and cost from turn N-1" guarantee holds for healthy ResultMessages; for malformed ones, the per-field rule applies. — *Carried into:* code comment at the assignment site documenting both the invariant and the D-8 override. Test: code review (atomicity is structural) + `test_malformed_result_message_leaves_totals_unchanged` (per-field independence).

- **D-5: Base `Teammate.status_snapshot()` returns zero values for the three new keys.** — *Rationale:* SC-7 requires StubTeammate to return fields-present-with-zero. Putting it in the base means StubTeammate gets it for free, and SdkTeammate's override naturally shadows the base zero with the real value. — *Carried into:* `Teammate.status_snapshot` in `claude_crew/teammate.py` adds three keys; StubTeammate has zero override-cost. Test: `test_stub_status_snapshot_has_token_cost_zero_fields`.

- **D-6: Instance-summary aggregate computed in UIServer, not frontend.** — *Rationale:* SC-3 requires tombstones included in the session aggregate. Frontend MCTopBar (dashboard.html:219-229) sums across instances and has no access to broker tombstone data. Computing in `_build_local_instance` keeps tombstone access local to the broker scope. — *Carried into:* `_build_local_instance` returns a populated `cost`/`tokens` on the instance dict; frontend continues to sum across instances unchanged. Test: `test_instance_summary_includes_tombstoned_teammate_cost`.

- **D-7: Tombstone at-death fields default to numeric zero (not None) when snap is absent.** — *Rationale:* The aggregate sum in D-6 reads `info.total_cost_usd_at_death or 0.0`; numeric zero defaults make the aggregate-summation code symmetric with the alive branch. `None` in TeammateInfo is reserved for "field doesn't apply" (e.g., `died_at_wallclock` for alive teammates) — but a dead teammate that never produced a turn legitimately has zero cost, not absent cost. — *Carried into:* `_tombstone_teammate` extraction uses `snap.get("total_cost_usd", 0.0)` etc. TeammateInfo type stays `int | None` / `float | None` for forward-compat with future "didn't capture" sentinels. Test: `test_tombstone_with_no_turns_has_zero_at_death_values`.

- **D-8: Malformed usage/cost values are silently ignored; teammate fields unchanged.** — *Rationale:* SC-6. If `ResultMessage.total_cost_usd` is None, or `ResultMessage.usage` is None / not-a-dict / has wrong types, the existing values stay. WARNING log fires once per malformed message. **Granularity clarification (T2 implementation):** `total_cost_usd` and `usage` are independent fields on `ResultMessage`. Malformed `usage` invalidates only token extraction; cost continues to update from `total_cost_usd` if that field is itself valid. Vice versa for malformed cost. The "leave unchanged" rule applies *per-field*, not as an all-or-nothing transaction. — *Carried into:* `_collect_response_text` wraps extraction in try/except (TypeError, ValueError), returns three independent Optional values in TurnDrainResult, and `_handle_one_turn` only assigns each value if not None. Test: `test_malformed_result_message_leaves_totals_unchanged` (asserts tokens stay at turn-1 values when usage="not-a-dict" but cost DID update because total_cost_usd was valid).

- **D-9: Existing tests using `text_response()` (no usage scripted) remain valid.** — *Rationale:* `text_response()` builds a ResultMessage with `total_cost_usd=None` and `usage=None`. Per D-8, the teammate fields stay at zero. Existing assertions about other snap keys are unaffected. — *Carried into:* no test changes required for any pre-existing test. Verified by full test suite passing on branch before any new tests are added. Test (SC-10 explicit non-regression): `test_no_existing_snapshot_keys_renamed_or_removed` asserts the union of pre-feature keys is a subset of the post-feature snap dict.

#### Additional test anchors (Sentinel-required)

- **SC-2** → `test_snapshot_excludes_in_flight_turn` — start a turn (no ResultMessage yet); assert snapshot's three fields are unchanged from pre-turn values.
- **SC-4 (typical case)** → `test_tombstone_preserves_last_cumulative_after_n_turns` — drive 3 turns each with progressive cumulative cost ($0.10 → $0.30 → $0.60); kill teammate; assert `_at_death` fields equal $0.60 / final tokens.
- **SC-9** → `test_token_cost_types_and_no_scinotation` — run a turn with cost=$0.0001; serialize snap to JSON; assert types are `int`/`int`/`float` and the cost string contains no `e-` (no scientific notation).

- **D-10: Tombstones excluded from agents array; included only in instance summary.** — *Rationale:* The `agents: [...]` array in the dashboard payload is a UI contract for "currently running agents." Adding dead agents would change row rendering and break existing tests (`test_dead_teammate_excluded`). The session-cost question is answered at the instance-summary level instead. — *Carried into:* `_build_local_instance` keeps `if not info.alive: continue` for the agents loop; aggregate is computed via a separate iteration that includes dead teammates.

- **D-11: Session-scoped accumulation; respawn = brand-new instance with zero fields.** — *Rationale:* SC-5. `__init__` initializes the three fields to zero. A respawn after kill creates a new `SdkTeammate` instance via the factory; old instance's tombstone is preserved separately in `TeammateInfo`. Lifetime aggregation across spawns is explicitly out-of-scope. — *Carried into:* `SdkTeammate.__init__` field initializers; aggregate logic in `_build_local_instance` sums BOTH the new alive instance's fields AND the prior tombstone's `_at_death` fields (D-12). Test: `test_respawn_with_tombstone_present_aggregates_both`.

- **D-12: Tombstone aggregate survives co-existing live respawn under the same role/name.** — *Rationale:* Edge case 15. Broker's `_info` is keyed by teammate `id` (UUID-like, generated per-spawn — see `broker.py` spawn path), NOT by name. A respawn under the same name is a distinct entry; the tombstone entry remains in `_info` alongside it. `_build_local_instance`'s aggregate iterates ALL `_info.values()`, so both contributions are summed. — *Carried into:* the iteration in the aggregate already does this naturally; verified by Edge Case 15 reasoning. Test: `test_respawn_with_tombstone_present_aggregates_both`.

### Edge Cases

**Per Phase 1 / SC coverage:**

1. **Just-spawned teammate (zero turns)** — `total_input_tokens=0`, `total_output_tokens=0`, `total_cost_usd=0.0` (initial state, SC-1). Dashboard shows `$0.000` and `0 / 0`, indistinguishable from "ran for free". This is acceptable — zero is the natural starting state and matches how cost will look for a sub-cent first turn anyway.

2. **ResultMessage missing (SDK iterator interrupted, RateLimitedError)** — `TurnDrainResult` carries `None` for the three values. `_handle_one_turn` skips assignment. Teammate fields keep their previous values (last successful turn's cumulative). Subsequent successful turn overwrites with the new (still cumulative) value — no gap, no duplication.

3. **ResultMessage with `total_cost_usd=None`** — `usage` may still be valid. Tokens are updated; cost is left unchanged. Logged at WARNING.

4. **ResultMessage with `usage=None`** — Cost may still be valid. Cost is updated; tokens are left unchanged. Logged at WARNING.

5. **Malformed usage values (string, missing keys)** — TypeError/ValueError caught in extraction; returned as None; assignment skipped. Logged at WARNING. SC-6.

6. **Multiple ResultMessages in one drain (defensive)** — Per SDK contract this can't happen (`receive_response()` terminates at the first ResultMessage), but if it did the last one wins (overwrite semantics — D-2).

7. **Concurrent turn completion (theoretical)** — Cannot happen: `_handle_one_turn` is called serially by `_run()`. SC-1 atomic-snapshot satisfied by single-threaded mutation (D-4).

8. **Mid-turn teammate kill** — Tombstone captures `status_snapshot()` BEFORE the in-flight turn completes. Last successful turn's cumulative is preserved as `_at_death` values. In-flight tokens are not recovered. SC-4. Acceptable.

9. **Teammate with no turns ever (immediate kill)** — `_at_death` values are `0`, `0`, `0.0`. Aggregate sum unaffected. D-7.

10. **Very small per-turn cost (sub-cent)** — `total_cost_usd` is `float`, no rounding at storage (SC-9). Display formatting in dashboard rounds to `$0.000` (3 decimals). JSON serialization of small floats uses standard Python repr (`0.0001`), not scientific notation, for values >= 1e-4 — confirmed by Python's default `json.dumps` behavior.

11. **Instance summary with no teammates** — Aggregate loop iterates over zero-element `broker._info`, returns `cost=0.0`, `tokens={in:0, out:0}`. Matches pre-feature behavior.

12. **All teammates dead** — Aggregate loop finds zero alive, sums tombstone `_at_death` values. Instance summary continues to show last-known cost; agents array is empty. SC-3 — answers "what has this session cost" even after all teammates die.

13. **Cache token edge: `cache_read_input_tokens` present without `input_tokens`** — extraction sums `(input_tokens or 0) + (cache_read_input_tokens or 0) + (cache_creation_input_tokens or 0)`. Each `.get` defaults to 0. SC-1 + D-3.

14. **Dashboard zero vs. missing**: tokens/cost are always present (D-5 ensures the keys exist with zero values from spawn). The dashboard never sees missing fields and never needs to distinguish "zero" from "absent" — the contract is "always-present, zero-on-spawn".

15. **Respawn while prior tombstone is still in `broker._info`.** Role X tombstones with cost $1.20. User respawns role X — broker assigns a new teammate id (UUIDs differ across spawns), so the tombstone entry persists in `_info` alongside the new alive entry. The aggregate (D-6) iterates ALL `_info.values()` and includes the tombstone's `_at_death` cost ($1.20) PLUS the new alive teammate's running cost ($0.05 after first turn) → instance shows $1.25. SC-3 satisfied: "what has this session cost me" survives the respawn. Verified by D-12.

### Specification

**Implementation order (informational; bound to Phase 3 task split):**

1. Extend `Teammate.status_snapshot()` to add three zero-valued keys; verify StubTeammate inherits cleanly.
2. Add three instance fields to `SdkTeammate.__init__`; override `status_snapshot()` to surface them.
3. Import `ResultMessage` in `sdk_teammate.py`; extend `TurnDrainResult` and `_collect_response_text` to extract values from terminal ResultMessage.
4. Wire `_handle_one_turn` to apply overwrite from `TurnDrainResult`.
5. Extend `TeammateInfo` with three `_at_death` fields; extend `_tombstone_teammate` extraction; extend `get_teammate_status` to forward.
6. Replace UIServer hardcoded zeros: per-agent reads from snap; instance summary computed via aggregate that includes tombstones.
7. Add `text_response_with_usage()` test helper.
8. E2E test: multi-teammate, multi-turn, cache tokens, malformed input, mid-turn death, tombstone aggregate.

### Assumptions

*Default-accept: silence = agreed.*

- **A-1: `ResultMessage.usage` keys follow Anthropic-standard names** (`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`). Spike of `claude_agent_sdk/_internal/message_parser.py:233` shows `usage=data.get("usage")` is passed through opaquely; the CLI proxies the Anthropic API's `Usage` object literally. Sentinel flagged as load-bearing-unverified — recommend a single live-SDK probe test (gated behind `CLAUDE_CREW_LIVE_TESTS=1`) that asserts these keys appear in a real ResultMessage. Added as a Phase 3 task ("live key-name probe").

- **A-2: `total_cost_usd` is in USD with no implicit unit conversion.** SDK field name is literal. Dashboard displays as USD with `$` prefix.

- **A-3: Test fixtures driving SdkTeammate via FakeSDKClient already include a ResultMessage** (confirmed: `tests/fakes/sdk.py:35-50`). New tests just need richer ResultMessage construction (new helper). No retrofit needed for existing tests.

- **A-4: Dashboard frontend MCTopBar can sum non-zero per-instance cost/tokens without code changes.** Existing JS already does `acc.cost += i.cost; acc.in += i.tokens.in; acc.out += i.tokens.out` (dashboard.html:219-229). Replacing zero with non-zero only changes the displayed number.

- **A-5: A teammate's `_total_*` fields are read by `status_snapshot()` synchronously and never `await`-ed.** Reader cannot observe a partial assignment because the assigner block has no awaits between the three statements.

- **A-6: Single `broker._info.values()` iteration in `_build_local_instance` is consistent enough.** Two iterations (one for agents, one for aggregate) could see one teammate die between iterations. Mitigation: use one iteration, branch alive/dead, populate both agents list and aggregate counters in the same loop. This is the implementation pattern.

- **A-7: ResultMessage emission is reliable enough to trust for cost telemetry.** If the SDK silently drops a ResultMessage on rare error paths, that turn's cost increment is lost — the next successful turn's cumulative will leapfrog past it. This is acceptable; cost telemetry is operational, not financial.

- **A-8 (SPIKED): Subagent (Task tool) cost rolls into the parent session's `total_cost_usd`.** Verified via `claude_agent_sdk/types.py:1047`: `TaskNotificationMessage.session_id` matches the parent session_id — subagents execute within the same SDK session, so the CLI's session-scoped cost accounting captures their consumption automatically. `TaskUsage.total_tokens` (types.py:986-991) is informational, not authoritative; ResultMessage remains the single source of truth. No additional plumbing required for F7 cost coverage.

### Open Questions

*Must-answer before Phase 3.*

- **OQ-4: Should the WARNING log on malformed usage data carry sampled context?** Default proposal: log the offending dict's KEYS (not values, to avoid leaking content) once per teammate per malformed event, at WARNING. Phase 3-deferrable per Sentinel review.

- [x] **OQ-5 RESOLVED:** Session_id IS stable across all `client.query()` calls from a given teammate. `sdk_teammate.py:794-797` computes `session_id = f"{crew_id}-{teammate.id}"` per turn — both inputs are immutable for the teammate's lifetime. The Anthropic CLI maintains cumulative session state per session_id, so `ResultMessage.total_cost_usd` IS cumulative across all turns of one teammate's session. D-1 and D-2 are validated.

**Gate**:
- ✅ Design clear and justifiable
- ✅ Spec comprehensive — no ambiguity, no TODOs
- ✅ ALL edge cases listed
- ✅ Error handling specified
- ✅ Cross-feature integration check complete
- ✅ Implementable by someone with no additional context

---

## Phase 3: Task Breakdown

Five tasks, sized for incremental verification. Each has a verification command that fails without the feature, BDD scenarios traceable to Phase 1 SCs, and named tests anchoring the Phase 2 design decisions.

**Branch**: `feature/token-cost-telemetry` (created at start of Phase 4).

### Task T1 — Base snapshot contract (additive, no SDK changes)

**Goal**: extend `Teammate.status_snapshot()` base implementation with three new keys at zero values; assert StubTeammate inherits cleanly and no existing keys are removed.

**Anchors**: D-5, SC-7, SC-10. Foundation for all downstream tasks.

**BDD**:
```
Scenario: StubTeammate snapshot exposes token/cost fields with zero values
  Given a freshly-spawned StubTeammate
  When I call status_snapshot()
  Then snap["total_input_tokens"] == 0 and is type int
  And snap["total_output_tokens"] == 0 and is type int
  And snap["total_cost_usd"] == 0.0 and is type float

Scenario: existing snapshot consumers see no removed/renamed keys
  Given the pre-feature set of status_snapshot keys (recorded as a fixture)
  When I call status_snapshot() on the post-feature StubTeammate
  Then every pre-feature key is still present
  And no pre-feature key has changed type
```

**Tests**: `test_stub_status_snapshot_has_token_cost_zero_fields`, `test_no_existing_snapshot_keys_renamed_or_removed`.

**Verification**: `uv run pytest tests/test_stub_teammate.py tests/test_teammate.py -v`. Fails if base class doesn't add the three keys.

**Dependencies**: none.

---

### Task T2 — SdkTeammate token/cost capture from ResultMessage

**Goal**: capture `ResultMessage.total_cost_usd` and `ResultMessage.usage` per-turn-drain; overwrite three new instance fields on `SdkTeammate`; surface them via overridden `status_snapshot()`. Add a `text_response_with_usage()` test helper.

**Anchors**: D-1, D-2, D-3, D-4, D-8, D-9, SC-1, SC-2, SC-6, SC-8, SC-9.

**BDD**:
```
Scenario: SdkTeammate stores cumulative cost from ResultMessage (overwrite, not accumulate)
  Given an SdkTeammate driven by FakeSDKClient
  And turn 1 emits ResultMessage with total_cost_usd=0.10 (cumulative)
  And turn 2 emits ResultMessage with total_cost_usd=0.30 (cumulative)
  And turn 3 emits ResultMessage with total_cost_usd=0.60 (cumulative)
  When all three turns complete
  Then snap["total_cost_usd"] == 0.60 (not 1.00 — overwrite, not sum)

Scenario: cache tokens are included in total_input_tokens
  Given a turn emitting ResultMessage.usage =
    {input_tokens: 100, output_tokens: 50, cache_read_input_tokens: 1000}
  When the turn completes
  Then snap["total_input_tokens"] == 1100

Scenario: malformed usage data leaves totals unchanged
  Given snap["total_cost_usd"] == 0.50 from a previous valid turn
  When the next turn emits ResultMessage with usage = "not-a-dict"
  Then snap["total_cost_usd"] is still 0.50
  And a WARNING is logged with the offending dict's keys (or marker for non-dict)

Scenario: snapshot during in-flight turn shows pre-turn values
  Given a turn has started (query sent, ResultMessage not yet received)
  When status_snapshot() is called
  Then the three fields equal their post-previous-turn values (no in-flight contribution)

Scenario: cost types and JSON serialization
  Given snap with total_cost_usd = 0.0001
  When snap is serialized via the same path as the dashboard payload
  Then total_input_tokens / total_output_tokens are JSON integers
  And total_cost_usd is a JSON number with no scientific-notation 'e' character
```

**Tests**: `test_token_cost_overwrite_from_result_message_only`, `test_three_turns_show_final_cumulative_not_sum`, `test_cache_tokens_summed_into_total_input`, `test_malformed_result_message_leaves_totals_unchanged`, `test_snapshot_excludes_in_flight_turn`, `test_token_cost_types_and_no_scinotation`.

Implementation note (from co-architect): the no-sci-notation test will likely require a `format()` call at serialization (Python's default `repr(float)` switches to scientific around 1e-5, not 1e-4 as initially noted in Edge Case 10). Drive the implementation off the test result, not the comment.

**Verification**: `uv run pytest tests/test_sdk_teammate.py -v -k "token or cost"`. Fails if SdkTeammate fields don't capture from ResultMessage, or if extraction crashes on malformed input.

**Dependencies**: T1.

---

### Task T3 — Tombstone preservation in TeammateInfo + broker forwarding

**Goal**: extend `TeammateInfo` with three `_at_death` fields (defaulting to numeric zero per D-7); extend `_tombstone_teammate` extraction; extend `get_teammate_status` to forward in both alive and dead branches.

**Anchors**: D-7, D-11, SC-4, SC-5.

**BDD**:
```
Scenario: tombstone preserves last cumulative cost after multiple turns
  Given an SdkTeammate that completed 3 turns with final total_cost_usd=$0.60
  When the teammate is killed
  Then TeammateInfo.total_cost_usd_at_death == 0.60
  And get_teammate_status(id) returns total_cost_usd: 0.60

Scenario: tombstone with no completed turns has zero at-death values
  Given an SdkTeammate killed before any turn produced a ResultMessage
  When the teammate is killed
  Then TeammateInfo.total_*_at_death are 0 / 0 / 0.0 (not None)
  And get_teammate_status(id) returns zeros for the three fields

Scenario: alive teammate's status returns live snap fields
  Given a live SdkTeammate with total_cost_usd=$0.25
  When get_teammate_status(id) is called
  Then the response includes total_cost_usd: 0.25 read from status_snapshot()
```

**Tests**: `test_tombstone_preserves_last_cumulative_after_n_turns`, `test_tombstone_with_no_turns_has_zero_at_death_values`, `test_get_teammate_status_alive_forwards_token_cost`.

**Verification**: `uv run pytest tests/test_broker.py -v -k "token or cost or tombstone"`. Fails if `_at_death` fields are missing or `get_teammate_status` doesn't forward them.

**Dependencies**: T2.

---

### Task T4 — UIServer dashboard wiring + instance aggregate

**Goal**: replace hardcoded zeros in `_build_local_instance` (per-agent fields and instance summary). Instance summary aggregate iterates both alive teammates (via live snap) AND tombstoned teammates (via `_at_death` fields), in a single pass over `broker._info.values()`.

**Anchors**: D-6, D-10, D-12, A-6, SC-3, SC-5 (respawn aggregate), edge case 15.

**BDD**:
```
Scenario: per-agent dashboard fields read from live snap
  Given an alive SdkTeammate with total_cost_usd=$0.15 and tokens 200/100
  When _build_local_instance() runs
  Then the agent dict has cost: 0.15 and tokens: {in: 200, out: 100}
  (no longer hardcoded 0.0 / {0,0})

Scenario: instance summary includes tombstoned teammate cost
  Given two SdkTeammates: A (alive, $0.30) and B (tombstoned, $1.20 at_death)
  When _build_local_instance() runs
  Then the instance dict has cost: 1.50
  And the agents array has only A (tombstone excluded from agents per D-10)

Scenario: respawn with tombstone present aggregates both
  Given role X tombstoned with $1.20 at_death
  And role X respawned and accumulated $0.05 in its first turn
  When _build_local_instance() runs
  Then the instance summary cost == 1.25
  And the agents array contains the new alive role X (one entry)

Scenario: empty crew shows zero aggregate
  Given broker._info is empty
  When _build_local_instance() runs
  Then instance cost == 0.0 and tokens == {in: 0, out: 0}
```

**Tests**: `test_per_agent_cost_reads_from_snap`, `test_instance_summary_includes_tombstoned_teammate_cost`, `test_respawn_with_tombstone_present_aggregates_both`, `test_empty_crew_aggregate_is_zero` (likely already exists; extend assertions).

**Verification**: `uv run pytest tests/test_ui_server.py -v -k "cost or token or aggregate or summary"`. Fails if hardcoded zeros remain or tombstones don't contribute.

**Dependencies**: T3.

---

### Task T5 — E2E integration + live-SDK key-name probe

**Goal**: cohesive end-to-end tests that exercise the full pipeline through the public surface (broker spawn → multi-turn drives → status query → tombstone → dashboard). Plus one live-SDK probe (gated by `CLAUDE_CREW_LIVE_TESTS=1`) that asserts Anthropic-standard usage keys appear in a real ResultMessage (verifies A-1).

**Anchors**: full pipeline; A-1; SC-1 through SC-10 jointly verified through real flow.

**BDD (E2E happy path)**:
```
Scenario: full token/cost pipeline end-to-end
  Given a broker with two SdkTeammates (A and B), each driven by FakeSDKClient
  When teammate A runs 2 turns (cumulative cost $0.40)
  And teammate B runs 3 turns (cumulative cost $0.75)
  Then dashboard payload's instance cost == 1.15
  And both agents appear in agents[] with their correct individual costs
  When teammate A is killed
  Then dashboard payload's instance cost is still 1.15 (tombstone preserves $0.40)
  And agents[] now contains only teammate B
```

**BDD (E2E sad paths)**:
```
Scenario: multi-turn with one malformed ResultMessage in the middle
  Given teammate runs 3 turns, where turn 2 emits malformed usage
  When all three turns complete
  Then total_cost_usd reflects turn 3's cumulative (not turn 2's)
  And turn 2's malformed payload was logged at WARNING

Scenario: teammate killed mid-turn preserves last successful cumulative
  Given teammate completes turn 1 with cumulative cost $0.10
  And turn 2 is started but not completed (no ResultMessage yet)
  When the teammate is killed
  Then TeammateInfo.total_cost_usd_at_death == 0.10
```

**BDD (live-SDK key-name probe)**:
```
Scenario [live-only]: real SDK ResultMessage carries Anthropic-standard usage keys
  Given CLAUDE_CREW_LIVE_TESTS=1
  When a live SdkTeammate runs one minimal turn against the real CLI
  Then the captured ResultMessage.usage dict contains "input_tokens" and "output_tokens" keys
  And the test does not crash regardless of cache_* keys' presence
```

**Tests**: `test_e2e_token_cost_pipeline`, `test_e2e_malformed_midstream_does_not_corrupt_totals`, `test_e2e_kill_mid_turn_preserves_last_cumulative`, `test_live_sdk_result_message_uses_standard_usage_keys` (skipped unless live env var set).

**Verification**: `uv run pytest tests/test_e2e_token_cost.py -v` (and `CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_e2e_token_cost.py::test_live_sdk_result_message_uses_standard_usage_keys` for the gated probe).

**Dependencies**: T1, T2, T3, T4.

---

### Task summary table

| # | Task | SCs | Tests | Depends on |
|---|---|---|---|---|
| T1 | Base snapshot contract | SC-7, SC-10 | 2 | — |
| T2 | SdkTeammate ResultMessage capture | SC-1, SC-2, SC-6, SC-8, SC-9 | 6 | T1 |
| T3 | Tombstone + broker forwarding | SC-4, SC-5 | 3 | T2 |
| T4 | UIServer dashboard + aggregate | SC-3, SC-5 (respawn) | 4 | T3 |
| T5 | E2E + live-SDK probe | All (joint) + A-1 | 4 (3 stub + 1 live) | T1-T4 |

**Gate**:
- ✅ 5 tasks, each independently testable
- ✅ Dedicated E2E test task with happy + multiple sad paths
- ✅ Dependencies clear and minimal (linear chain)
- ✅ Verification commands fail without the feature
- ✅ Every Phase 1 SC is traced to at least one BDD scenario
- ⏳ User approval to proceed to Phase 4

---

## Phase 4: Implementation

*Execution driven by SKILL.md. Update status in header as tasks complete.*

**Gate**: All tasks complete, quality gates passing, Sentinel review done.

---

## Phase 5: Completion

### Verification
- [ ] Feature works against Phase 1 success criteria
- [ ] No regressions — full test suite passes
- [ ] Spec updated to match implementation
- [ ] Docs updated if user-facing behavior changed

### Retrospective

**What went well**:

**What was friction**:

**Improvements**:
1. [Specific, actionable change to workflow]

**Workflow updates made**:
- [ ] TEMPLATE.md or SKILL.md updated
- [ ] Project knowledge base updated (`.claude/rules/`)
- [ ] MEMORY.md updated (if cross-project insight)

**Gate**: Feature verified, retrospective captured, workflow improved.
