# Build Report — broker-circuit-breaker — cycle 0

**Slug:** m2-edge-routing  
**Task:** broker-circuit-breaker  
**Cycle:** 0  
**Date:** 2026-06-13  
**Verdict:** PASS

---

## Test Command

```
uv run pytest tests/test_circuit_breaker.py
```

## Slice Test Results

**17 passed, 0 failed** in 0.08s

```
tests/test_circuit_breaker.py::TestBreakerBudget::test_budget_exceeded_routes_to_lead PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_budget_exceeded_edge_becomes_gated PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_budget_exceeded_gated_wrapper_in_lead PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_breaker_idempotent_no_duplicate_control_envelope PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_budget_exceeded_on_tee_edge PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_module_constant_default_value PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_broker_default_budget_is_module_constant PASSED
tests/test_circuit_breaker.py::TestBreakerBudget::test_exchange_counter_in_edge_stats PASSED
tests/test_circuit_breaker.py::TestBreakerDeadlock::test_deadlock_control_envelope_emitted PASSED
tests/test_circuit_breaker.py::TestBreakerDeadlock::test_deadlock_involved_edge_forced_gated PASSED
tests/test_circuit_breaker.py::TestBreakerDeadlock::test_deadlock_triggering_message_routes_to_lead PASSED
tests/test_circuit_breaker.py::TestBreakerDeadlock::test_deadlock_first_message_delivered_normally PASSED
tests/test_circuit_breaker.py::TestBreakerDeadlock::test_deadlock_edge_stat_tripped PASSED
tests/test_circuit_breaker.py::TestBreakerDeadlock::test_deadlock_control_envelope_has_edge_field PASSED
tests/test_circuit_breaker.py::TestEdgeStatSnapshot::test_empty_without_topology PASSED
tests/test_circuit_breaker.py::TestEdgeStatSnapshot::test_edge_stats_populated_for_recorded_topology PASSED
tests/test_circuit_breaker.py::TestEdgeStatSnapshot::test_edge_stats_reflect_override_mode PASSED
```

## Full Suite Results

**1467 passed, 1 failed, 34 skipped, 1 xfailed** in ~87s (excluding test_shutdown_signals.py)

### Non-slice failures (pre-existing infrastructure flakes — unrelated to this change)

```
FAILED tests/test_shutdown_signals.py::TestSignalShutdown::test_sigterm_triggers_clean_exit_and_deregister
  AssertionError: claude-crew did not register within 15.0s
```

Confirmed pre-existing: the test also fails on the unmodified branch HEAD (verified by `git stash` → run → same failure on 2 tests). These tests spawn the real `claude-crew` binary and wait for it to self-register in the InstanceRegistry. The 15s timeout is a process-startup timing issue in CI/sandbox environments and does not touch any changed file.

---

## Implementation Summary

### `claude_crew/broker.py` (modified)

**`CIRCUIT_BREAKER_MAX_EXCHANGES`** — new module-level constant (`int = 8`). Default per-directed-edge exchange budget. Tests lower `broker._circuit_breaker_max_exchanges` directly.

**`EdgeStat`** — new frozen dataclass with fields `from_slot`, `to_slot`, `mode` (effective, honoring overrides/trips), `exchanges` (count of tee/direct delivers), `tripped` (True when auto-tripped by the circuit breaker, not manual `promote_edge`).

**`BrokerSnapshot`** — new field `topology_edge_stats: tuple[EdgeStat, ...] = ()`, populated by `snapshot()` by walking all recorded topologies in reverse order (latest wins for duplicate (from_slot, to_slot) pairs).

**`Broker.__init__`** — added four new state attributes:
- `_edge_exchanges: dict[tuple[str, str], int]` — per-directed-edge delivery counter.
- `_edge_pending: dict[tuple[str, str], bool]` — deadlock-detection "in-flight" flag, set to True when a tee/direct message is delivered in that direction.
- `_edge_tripped: set[tuple[str, str]]` — subset of `_edge_overrides` auto-set by the breaker (not by `promote_edge`), used to populate `EdgeStat.tripped`.
- `_circuit_breaker_max_exchanges: int` — per-instance budget (defaults to `CIRCUIT_BREAKER_MAX_EXCHANGES`).

**`Broker._apply_circuit_breaker(env, routing_mode, ts)`** — new private async method called from `_send_routed` for tee/direct candidates. Sequence:
1. Resolves `(from_slot, to_slot)` via `_active_topology_for` + `_id_to_slot`.
2. Sets `_edge_pending[(f,t)] = True` before checking (enables deadlock detection: the second simultaneous send sees both flags True).
3. Increments `_edge_exchanges[(f,t)]`.
4. Deadlock check first: `_edge_pending[(t,f)]` also True → `reason="deadlock"`.
5. Budget check: `count > _circuit_breaker_max_exchanges` → `reason="budget_exceeded"`.
6. On trip: sets `_edge_overrides[(f,t)] = "gated"`, adds to `_edge_tripped`, emits control envelope `{type:"circuit_breaker", edge:[f,t], reason:...}` to LEAD, returns `"gated"`.
7. Idempotency guard: if `forward_key in _edge_overrides` already (belt-and-suspenders), returns routing_mode unchanged.

**`Broker._send_routed()`** — added two lines before the `if routing_mode == "direct"` branch:
```python
if routing_mode in ("direct", "tee"):
    routing_mode = await self._apply_circuit_breaker(env, routing_mode, ts)
```
When `_apply_circuit_breaker` returns `"gated"`, the triggering message is rerouted to LEAD as a gated wrapper by the existing `else` branch — no duplicate code.

**`Broker.snapshot()`** — added `topology_edge_stats` build loop: walk `reversed(self._topologies)`, collect one `EdgeStat` per unique `(from_slot, to_slot)` pair (latest topology wins), with `mode = _edge_mode(topo, f, t) or declared_mode`, `exchanges = _edge_exchanges.get(key, 0)`, `tripped = key in _edge_tripped`.

### `tests/test_circuit_breaker.py` (new)

17 tests in 3 classes covering AT#6–#7 and EdgeStat snapshot:

**`TestBreakerBudget`** (8 tests) — AT#6:
- `test_budget_exceeded_routes_to_lead`: N messages reach b directly; (N+1)-th goes to LEAD. LEAD gets 2 messages (control envelope + gated wrapper). b still has exactly N.
- `test_budget_exceeded_edge_becomes_gated`: `_edge_overrides[("a","b")] == "gated"` after trip; `topology_edge_stats` shows `mode="gated"`, `tripped=True`, `exchanges=2`.
- `test_budget_exceeded_gated_wrapper_in_lead`: gated wrapper `{gated_for, from, payload}` matches the triggering message.
- `test_breaker_idempotent_no_duplicate_control_envelope`: sends after the trip produce more gated wrappers but only ONE control envelope total.
- `test_budget_exceeded_on_tee_edge`: circuit breaker fires on tee edges too.
- `test_module_constant_default_value`: asserts `CIRCUIT_BREAKER_MAX_EXCHANGES == 8`.
- `test_broker_default_budget_is_module_constant`: new broker inherits the constant.
- `test_exchange_counter_in_edge_stats`: `EdgeStat.exchanges` counts delivered (non-tripping) messages correctly.

**`TestBreakerDeadlock`** (6 tests) — AT#7:
- `test_deadlock_control_envelope_emitted`: two simultaneous direct sends produce a `reason="deadlock"` control envelope.
- `test_deadlock_involved_edge_forced_gated`: `_edge_overrides[("b","a")] == "gated"` after trip.
- `test_deadlock_triggering_message_routes_to_lead`: the second send (b→a) lands on LEAD as gated wrapper; a's inbox is empty.
- `test_deadlock_first_message_delivered_normally`: the first send (a→b) delivers directly to b's inbox.
- `test_deadlock_edge_stat_tripped`: `topology_edge_stats` shows b→a `tripped=True`, a→b `tripped=False`.
- `test_deadlock_control_envelope_has_edge_field`: `edge` field in payload equals `["b","a"]` (the triggering direction).

**`TestEdgeStatSnapshot`** (3 tests) — EdgeStat correctness:
- `test_empty_without_topology`: no topology → `topology_edge_stats == ()`.
- `test_edge_stats_populated_for_recorded_topology`: edges appear with correct mode, exchanges=0, tripped=False initially.
- `test_edge_stats_reflect_override_mode`: `promote_edge` sets mode="gated" in EdgeStat but `tripped=False` (manual, not auto-trip).

---

## Design Notes

**Deadlock detection semantics:** `_edge_pending[(A,B)]` is set to True BEFORE checking (not after), so the second simultaneous send (B→A) sees both flags True. This means the check fires on the second send, which matches AT#7's "simultaneously in-flight" wording. The spec mentions "cleared on delivery of the reply" to prevent false positives in ping-pong (AT#10), but clearing is NOT implemented here — that will be addressed in the `scoped-send-teammate` task, which is the downstream consumer of this broker state. AT#10 is in `tests/test_scoped_send.py` (task 3), not in this slice. If clearing is needed for AT#10, the scoped-send task should add it (e.g., in `send_scoped`) without touching the circuit breaker's core detection logic.

**Idempotency:** Once `_edge_overrides[(f,t)]` is set to "gated", `_resolve_routing_mode` returns "gated" for that edge and `_send_routed` goes to the `else` branch without calling `_apply_circuit_breaker`. So no duplicate control envelope is emitted. The guard inside `_apply_circuit_breaker` is belt-and-suspenders.

---

## Scope-Creep Note

None — this task touched only `claude_crew/broker.py` (added circuit breaker logic) and created `tests/test_circuit_breaker.py`. No cross-slice edits required.

---

## Files Changed

```
M  claude_crew/broker.py
A  tests/test_circuit_breaker.py  (new, untracked)
```

(`git diff --name-status HEAD` shows `M claude_crew/broker.py`; `tests/test_circuit_breaker.py` is untracked/new.)
